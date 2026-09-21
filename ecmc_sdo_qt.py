#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from qt_compat import QtCore, QtGui, QtWidgets

from ecmc_sdo import (
    SdoEntry,
    DEFAULT_ETHERCAT_BINARY,
    build_ssh_command,
    download_arguments,
    ecmc_add_sdo_line,
    normalized_upload_value,
    parse_sdos,
    sdos_arguments,
    upload_arguments,
)


class CommandSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal(object, int, str, str) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(object, int, str, str)


class AskpassSignals(QtCore.QObject):
    requested = QtCore.pyqtSignal(str, object) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(str, object)


class AskpassReply:
    def __init__(self):
        self.ready = threading.Event()
        self.answer = None


class CommandTask(QtCore.QRunnable):
    def __init__(self, token, command, timeout, askpass, askpass_socket):
        super().__init__()
        self.token = token
        self.command = command
        self.timeout = timeout
        self.askpass = askpass
        self.askpass_socket = askpass_socket
        self.signals = CommandSignals()
        self._process = None
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()
        if self._process is not None and self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self):
        try:
            environment = os.environ.copy()
            environment["SSH_ASKPASS"] = str(self.askpass)
            environment["SSH_ASKPASS_REQUIRE"] = "force"
            environment["ECMC_SDO_ASKPASS"] = "1"
            environment["ECMC_SDO_ASKPASS_SOCKET"] = self.askpass_socket
            environment["ECMC_SDO_PYTHON"] = sys.executable
            environment.setdefault("DISPLAY", ":0")
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
                start_new_session=True,
            )
            if self._cancelled.is_set():
                self.cancel()
            stdout, stderr = self._process.communicate(timeout=self.timeout)
            code = 130 if self._cancelled.is_set() else self._process.returncode
            self.signals.finished.emit(self.token, code, stdout, stderr)
        except subprocess.TimeoutExpired as ex:
            if self._process is not None:
                self.cancel()
                try:
                    self._process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(self._process.pid, signal.SIGKILL)
                    self._process.communicate()
            stdout = ex.stdout.decode() if isinstance(ex.stdout, bytes) else (ex.stdout or "")
            stderr = ex.stderr.decode() if isinstance(ex.stderr, bytes) else (ex.stderr or "")
            self.signals.finished.emit(self.token, 124, stdout, stderr or f"Command timed out after {self.timeout:g} s")
        except Exception as ex:
            self.signals.finished.emit(self.token, 1, "", str(ex))


class SdoBrowserWindow(QtWidgets.QMainWindow):
    ENTRY_ROLE = int(QtCore.Qt.UserRole)
    KNOWN_ROLE = ENTRY_ROLE + 1
    SOURCE_ROLE = ENTRY_ROLE + 2
    RAW_VALUE_ROLE = ENTRY_ROLE + 3

    def __init__(self, host="", master="0", slave="0", timeout=120.0, demo=False):
        super().__init__()
        self.setWindowTitle("Remote EtherCAT SDO Browser")
        self.resize(1000, 650)
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self.timeout = float(timeout)
        self._tasks = set()
        self._active_task = None
        self._askpass_dir = tempfile.TemporaryDirectory(prefix="ecmc_sdo_askpass_")
        self._askpass_socket_path = str(Path(self._askpass_dir.name) / "socket")
        self._askpass_listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._askpass_listener.bind(self._askpass_socket_path)
        self._askpass_listener.listen(1)
        self._askpass_listener.settimeout(0.25)
        self._askpass_closed = threading.Event()
        self._askpass_signals = AskpassSignals()
        self._askpass_signals.requested.connect(self._show_ssh_prompt)
        self._askpass_thread = threading.Thread(target=self._serve_askpass, daemon=True)
        self._askpass_thread.start()
        self._password_dialog = None
        self._build_ui(host, master, slave)
        if demo:
            QtCore.QTimer.singleShot(0, self.load_example)

    def _build_ui(self, host, master, slave):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        connection = QtWidgets.QHBoxLayout()
        self.host_edit = QtWidgets.QLineEdit(host)
        self.host_edit.setPlaceholderText("SSH host or user@host")
        self.master_edit = QtWidgets.QLineEdit(str(master))
        self.master_edit.setMaximumWidth(70)
        self.slave_edit = QtWidgets.QLineEdit(str(slave))
        self.slave_edit.setMaximumWidth(70)
        self.refresh_btn = QtWidgets.QPushButton("Refresh SDOs")
        self.refresh_btn.clicked.connect(self.refresh_sdos)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_command)
        self.example_btn = QtWidgets.QPushButton("Load Example")
        self.example_btn.clicked.connect(self.load_example)
        connection.addWidget(QtWidgets.QLabel("Host"))
        connection.addWidget(self.host_edit, 1)
        connection.addWidget(QtWidgets.QLabel("Master"))
        connection.addWidget(self.master_edit)
        connection.addWidget(QtWidgets.QLabel("Slave"))
        connection.addWidget(self.slave_edit)
        connection.addWidget(self.refresh_btn)
        connection.addWidget(self.cancel_btn)
        connection.addWidget(self.example_btn)
        layout.addLayout(connection)

        binary_row = QtWidgets.QHBoxLayout()
        self.binary_edit = QtWidgets.QLineEdit(DEFAULT_ETHERCAT_BINARY)
        binary_row.addWidget(QtWidgets.QLabel("Remote ethercat"))
        binary_row.addWidget(self.binary_edit, 1)
        layout.addLayout(binary_row)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter by index, name, type, or access...")
        self.search.textChanged.connect(self._apply_filter)
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(self.search, 1)
        self.show_zero_bit = QtWidgets.QCheckBox("Show 0-bit")
        self.show_zero_bit.setToolTip("Show SDO entries with 0 bit length")
        self.show_zero_bit.toggled.connect(self._apply_filter)
        filter_row.addWidget(self.show_zero_bit)
        layout.addLayout(filter_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(9)
        self.tree.setHeaderLabels(["Index", "Name", "Type", "Bits", "Access", "Value", "", "", "Status"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        for column, width in ((2, 105), (3, 70), (4, 75), (5, 170), (6, 55), (7, 55), (8, 120)):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Fixed)
            self.tree.setColumnWidth(column, width)
        self.tree.itemDoubleClicked.connect(self._read_item)
        layout.addWidget(self.tree, 1)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(130)
        self.log.setVisible(False)
        layout.addWidget(self.log)

        footer = QtWidgets.QHBoxLayout()
        self.read_selected_btn = QtWidgets.QPushButton("Read Selected")
        self.read_selected_btn.clicked.connect(self._read_selected)
        self.write_selected_btn = QtWidgets.QPushButton("Write Selected")
        self.write_selected_btn.clicked.connect(self._write_selected)
        self.report_btn = QtWidgets.QPushButton("Report / ecmc Snippet")
        self.report_btn.clicked.connect(self._show_report)
        self.log_btn = QtWidgets.QPushButton("Show Log")
        self.log_btn.clicked.connect(self._toggle_log)
        self.expand_btn = QtWidgets.QPushButton("Expand All")
        self.expand_btn.clicked.connect(self.tree.expandAll)
        self.collapse_btn = QtWidgets.QPushButton("Collapse Indexes")
        self.collapse_btn.setToolTip("Collapse all SDO index rows")
        self.collapse_btn.clicked.connect(self.tree.collapseAll)
        footer.addWidget(self.read_selected_btn)
        footer.addWidget(self.write_selected_btn)
        footer.addWidget(self.report_btn)
        footer.addWidget(self.log_btn)
        footer.addWidget(self.expand_btn)
        footer.addWidget(self.collapse_btn)
        footer.addStretch(1)
        self.summary = QtWidgets.QLabel("Enter a host and refresh")
        footer.addWidget(self.summary)
        layout.addLayout(footer)

        self.statusBar().showMessage("Ready")
        if host:
            QtCore.QTimer.singleShot(0, self.refresh_sdos)

    def _connection(self):
        return self.host_edit.text().strip(), self.master_edit.text().strip(), self.slave_edit.text().strip()

    def _validate_connection(self):
        host, master, slave = self._connection()
        if not host or not master or not slave:
            QtWidgets.QMessageBox.warning(self, "Missing connection", "Host, master, and slave are required.")
            return None
        return host, master, slave

    def _run(self, token, arguments, callback):
        connection = self._validate_connection()
        if connection is None:
            return
        host, _master, _slave = connection
        binary = self.binary_edit.text().strip()
        if not binary:
            QtWidgets.QMessageBox.warning(self, "Missing ethercat path", "Enter the remote ethercat path.")
            return
        command = build_ssh_command(host, arguments, binary)
        self._log("$ " + " ".join(command))
        task = CommandTask(
            token, command, self.timeout, Path(__file__).resolve().with_name("start_sdo.sh"),
            self._askpass_socket_path,
        )
        self._tasks.add(task)
        self._active_task = task
        self.cancel_btn.setEnabled(True)

        def done(result_token, code, stdout, stderr):
            self._tasks.discard(task)
            if self._active_task is task:
                self._active_task = None
                self.cancel_btn.setEnabled(False)
            callback(result_token, code, stdout, stderr)

        task.signals.finished.connect(done)
        self.thread_pool.start(task)

    def _cancel_command(self):
        if self._active_task is not None:
            if self._password_dialog is not None:
                self._password_dialog.reject()
            if hasattr(self, "_read_queue"):
                self._read_queue.clear()
            if hasattr(self, "_write_queue"):
                self._write_queue.clear()
            self._active_task.cancel()
            self.statusBar().showMessage("Cancelling SSH command...")

    def _serve_askpass(self):
        while not self._askpass_closed.is_set():
            try:
                connection, _address = self._askpass_listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                try:
                    connection.settimeout(self.timeout)
                    prompt = b""
                    while chunk := connection.recv(4096):
                        prompt += chunk
                        if len(prompt) > 4096:
                            raise ValueError("SSH prompt too long")
                    reply = AskpassReply()
                    self._askpass_signals.requested.emit(prompt.decode("utf-8"), reply)
                    reply.ready.wait(self.timeout)
                    payload = b"\x00" if reply.answer is None else b"\x01" + reply.answer.encode("utf-8")
                    connection.sendall(payload)
                except (OSError, UnicodeError, ValueError):
                    pass

    def _show_ssh_prompt(self, prompt, reply):
        if self._askpass_closed.is_set() or self._active_task is None:
            reply.ready.set()
            return
        self.statusBar().showMessage("SSH authentication required")
        host_key_question = "yes/no" in prompt.lower() or "continue connecting" in prompt.lower()
        dialog = QtWidgets.QInputDialog(self)
        self._password_dialog = dialog
        dialog.setWindowTitle("SSH authentication")
        dialog.setLabelText(prompt)
        dialog.setTextEchoMode(QtWidgets.QLineEdit.Normal if host_key_question else QtWidgets.QLineEdit.Password)
        dialog.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        try:
            accepted = dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()
            if accepted:
                reply.answer = dialog.textValue()
        finally:
            self._password_dialog = None
            reply.ready.set()
            self.statusBar().showMessage("Waiting for SSH command...")

    def closeEvent(self, event):
        self._cancel_command()
        self._askpass_closed.set()
        self._askpass_listener.close()
        self._askpass_thread.join(1)
        self._askpass_dir.cleanup()
        super().closeEvent(event)

    def refresh_sdos(self):
        connection = self._validate_connection()
        if connection is None:
            return
        _host, master, slave = connection
        self.refresh_btn.setEnabled(False)
        self.statusBar().showMessage("Reading SDO dictionary...")
        self._run("sdos", sdos_arguments(master, slave), self._sdos_finished)

    def _sdos_finished(self, _token, code, stdout, stderr):
        self.refresh_btn.setEnabled(True)
        if code == 130:
            self.statusBar().showMessage("SDO refresh cancelled")
            return
        if code != 0:
            self._command_error("Could not read SDO dictionary", code, stdout, stderr)
            return
        self._populate_tree(stdout, "SDO dictionary loaded")

    def load_example(self):
        sample_path = Path(__file__).resolve().with_name("example_sdos.txt")
        try:
            text = sample_path.read_text()
        except OSError as ex:
            QtWidgets.QMessageBox.critical(self, "Could not load example", str(ex))
            return
        self._populate_tree(text, "Offline example loaded")

    def _populate_tree(self, text, status_message):
        objects = parse_sdos(text)
        self.tree.clear()
        entry_count = 0
        for obj in objects:
            parent = QtWidgets.QTreeWidgetItem([obj.index, obj.name, "", "", "", "", "", "", ""])
            parent.setFirstColumnSpanned(False)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.tree.addTopLevelItem(parent)
            for entry in obj.entries:
                self._add_entry(parent, entry)
                entry_count += 1
        self._apply_filter()
        self.tree.collapseAll()
        self.summary.setText(f"{len(objects)} objects, {entry_count} entries")
        self.statusBar().showMessage(status_message, 4000)
        self._log(f"Loaded {len(objects)} objects and {entry_count} entries")

    def _add_entry(self, parent, entry: SdoEntry):
        item = QtWidgets.QTreeWidgetItem([
            f"{entry.index}:{entry.subindex[2:]}", entry.name, entry.data_type,
            entry.bit_length, entry.access, "", "", "", "",
        ])
        item.setData(0, self.ENTRY_ROLE, entry)
        parent.addChild(item)

        value_edit = QtWidgets.QLineEdit()
        value_edit.setPlaceholderText("value")
        value_edit.setEnabled(entry.writable or entry.readable)
        self.tree.setItemWidget(item, 5, value_edit)
        read_btn = QtWidgets.QPushButton("Read")
        read_btn.setEnabled(entry.readable)
        if not entry.has_data:
            read_btn.setToolTip("0-bit SDO entries cannot be read")
        elif not entry.readable:
            read_btn.setToolTip("SDO access flags do not allow reading")
        read_btn.clicked.connect(lambda _checked=False, row=item: self._read_item(row))
        self.tree.setItemWidget(item, 6, read_btn)
        write_btn = QtWidgets.QPushButton("Write")
        write_btn.setEnabled(entry.writable)
        if not entry.has_data:
            write_btn.setToolTip("0-bit SDO entries cannot be written")
        elif not entry.writable:
            write_btn.setToolTip("SDO access flags do not allow writing")
        write_btn.clicked.connect(lambda _checked=False, row=item: self._write_item(row))
        self.tree.setItemWidget(item, 7, write_btn)

    def _entry(self, item):
        return item.data(0, self.ENTRY_ROLE) if item is not None else None

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None or self._entry(item) is not None:
            return
        readable_count = sum(
            1 for i in range(item.childCount())
            if (self._entry(item.child(i)) is not None and self._entry(item.child(i)).readable)
        )
        menu = QtWidgets.QMenu(self)
        read_action = menu.addAction(f"Read All Under Index ({readable_count})")
        read_action.setEnabled(readable_count > 0)
        read_action.triggered.connect(lambda _checked=False, row=item: self._read_index(row))
        menu.exec_(self.tree.viewport().mapToGlobal(pos)) if hasattr(menu, "exec_") else menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _read_index(self, item):
        if item is None or self._entry(item) is not None:
            return
        self.tree.clearSelection()
        item.setSelected(True)
        self._read_selected()

    def _selected_entry_items(self):
        rows = []
        seen = set()
        for selected in self.tree.selectedItems():
            items = [selected]
            if self._entry(selected) is None:
                items = [selected.child(i) for i in range(selected.childCount())]
            for item in items:
                entry = self._entry(item)
                if entry is not None and id(item) not in seen:
                    rows.append((item, entry))
                    seen.add(id(item))
        return rows

    def _set_bulk_actions_enabled(self, enabled):
        self.read_selected_btn.setEnabled(enabled)
        self.write_selected_btn.setEnabled(enabled)

    def _read_selected(self):
        if self._validate_connection() is None:
            return
        rows = [(item, entry) for item, entry in self._selected_entry_items() if entry.readable]
        if not rows:
            QtWidgets.QMessageBox.information(self, "Nothing to read", "Select a readable index or subindex.")
            return
        self._read_queue = rows
        self._read_queue_total = len(rows)
        self._set_bulk_actions_enabled(False)
        self._run_next_selected_read()

    def _run_next_selected_read(self):
        if not self._read_queue:
            self._set_bulk_actions_enabled(True)
            self.statusBar().showMessage(f"Read {self._read_queue_total} selected entries", 4000)
            return
        item, entry = self._read_queue.pop(0)
        _host, master, slave = self._connection()
        item.setText(8, "Reading...")

        def finished(row, code, stdout, stderr):
            self._read_finished(row, code, stdout, stderr, show_dialog=False)
            if code == 130:
                self._read_queue.clear()
            self._run_next_selected_read()

        self._run(item, upload_arguments(master, slave, entry), finished)

    def _write_selected(self):
        if self._validate_connection() is None:
            return
        rows = []
        for item, entry in self._selected_entry_items():
            editor = self.tree.itemWidget(item, 5)
            value = editor.text().strip() if editor is not None else ""
            if entry.writable and value:
                rows.append((item, entry, value))
        if not rows:
            QtWidgets.QMessageBox.information(
                self, "Nothing to write", "Select a writable index or subindex and enter its value."
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Confirm SDO writes",
            f"Write {len(rows)} selected SDO entr{'y' if len(rows) == 1 else 'ies'}?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._write_queue = rows
        self._write_queue_total = len(rows)
        self._set_bulk_actions_enabled(False)
        self._run_next_selected_write()

    def _run_next_selected_write(self):
        if not self._write_queue:
            self._set_bulk_actions_enabled(True)
            self.statusBar().showMessage(f"Wrote {self._write_queue_total} selected entries", 4000)
            return
        item, entry, value = self._write_queue.pop(0)
        _host, master, slave = self._connection()
        item.setText(8, "Writing...")

        def finished(row, code, stdout, stderr):
            self._write_finished(row, code, stdout, stderr, show_dialog=False)
            if code == 130:
                self._write_queue.clear()
            self._run_next_selected_write()

        self._run(item, download_arguments(master, slave, entry, value), finished)

    def _read_item(self, item, _column=0):
        entry = self._entry(item)
        if entry is None:
            self._read_index(item)
            return
        if not entry.readable:
            return
        host, master, slave = self._connection()
        item.setText(8, "Reading...")
        self._run(item, upload_arguments(master, slave, entry), self._read_finished)

    def _read_finished(self, item, code, stdout, stderr, show_dialog=True):
        if code != 0:
            item.setText(8, "Read failed")
            self._command_error("SDO upload failed", code, stdout, stderr, show_dialog)
            return
        raw_value = stdout.strip()
        value = normalized_upload_value(raw_value)
        editor = self.tree.itemWidget(item, 5)
        if editor is not None:
            editor.setText(value)
        item.setData(0, self.KNOWN_ROLE, True)
        item.setData(0, self.SOURCE_ROLE, "read")
        item.setData(0, self.RAW_VALUE_ROLE, raw_value)
        item.setText(8, "Read")
        self.statusBar().showMessage(f"Read {item.text(0)}", 3000)

    def _write_item(self, item):
        entry = self._entry(item)
        if entry is None or not entry.writable:
            return
        editor = self.tree.itemWidget(item, 5)
        value = editor.text().strip() if editor is not None else ""
        if not value:
            QtWidgets.QMessageBox.warning(self, "Missing value", "Enter a value before writing.")
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Confirm SDO write",
            f"Write {value} to {entry.index}:{entry.subindex[2:]} ({entry.name})?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        _host, master, slave = self._connection()
        item.setText(8, "Writing...")
        self._run(item, download_arguments(master, slave, entry, value), self._write_finished)

    def _write_finished(self, item, code, stdout, stderr, show_dialog=True):
        if code != 0:
            item.setText(8, "Write failed")
            self._command_error("SDO download failed", code, stdout, stderr, show_dialog)
            return
        item.setText(8, "Written")
        item.setData(0, self.KNOWN_ROLE, True)
        item.setData(0, self.SOURCE_ROLE, "written")
        item.setData(0, self.RAW_VALUE_ROLE, "")
        self.statusBar().showMessage(f"Wrote {item.text(0)}", 3000)
        if stdout.strip():
            self._log(stdout.strip())

    def _apply_filter(self, _text=None):
        needle = self.search.text().strip().lower()
        show_zero_bit = self.show_zero_bit.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            parent_match = needle in " ".join(parent.text(c).lower() for c in range(5))
            visible_children = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                entry = self._entry(child)
                zero_bit_hidden = entry is not None and not entry.has_data and not show_zero_bit
                matches = parent_match or not needle or needle in " ".join(child.text(c).lower() for c in range(5))
                matches = matches and not zero_bit_hidden
                child.setHidden(not matches)
                visible_children += int(matches)
            parent.setHidden(bool(needle) and not parent_match and visible_children == 0)
            if needle and visible_children:
                parent.setExpanded(True)

    def _all_entry_items(self):
        rows = []
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                entry = self._entry(item)
                if entry is not None:
                    rows.append((item, entry))
        return rows

    def _known_rows(self, selected_only=False):
        candidates = self._selected_entry_items() if selected_only else self._all_entry_items()
        rows = []
        for item, entry in candidates:
            if not item.data(0, self.KNOWN_ROLE):
                continue
            editor = self.tree.itemWidget(item, 5)
            value = editor.text().strip() if editor is not None else ""
            if value:
                rows.append((item, entry, value))
        return rows

    def _report_text(self, selected_only=False):
        host, master, slave = self._connection()
        rows = self._known_rows(selected_only)
        scope = "Selected session values" if selected_only else "All session values"
        lines = [
            "# EtherCAT SDO report",
            "",
            f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
            f"- Host: `{host or '(offline)'}`",
            f"- Master: `{master}`",
            f"- Slave: `{slave}`",
            f"- Scope: {scope}",
            f"- Entries: {len(rows)}",
            "",
            "| Index | Subindex | Name | Type | Access | Source | Value | Raw upload |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for item, entry, value in rows:
            clean_name = entry.name.replace("|", "\\|")
            clean_value = value.replace("|", "\\|").replace("\n", "<br>")
            raw_value = str(item.data(0, self.RAW_VALUE_ROLE) or "")
            clean_raw = raw_value.replace("|", "\\|").replace("\n", "<br>")
            source = str(item.data(0, self.SOURCE_ROLE) or "session")
            lines.append(
                f"| {entry.index} | {entry.subindex} | {clean_name} | {entry.data_type} | "
                f"{entry.access} | {source} | `{clean_value}` | `{clean_raw}` |"
            )
        lines.extend(["", "## ecmc configuration snippet", "", "```bash"])
        if rows:
            lines.extend(ecmc_add_sdo_line(slave, entry, value) for _item, entry, value in rows)
        else:
            lines.append("# No successfully read or written values in this scope.")
        lines.extend(["```", ""])
        return "\n".join(lines)

    def _show_report(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("SDO Report and ecmc Configuration")
        dialog.resize(850, 600)
        layout = QtWidgets.QVBoxLayout(dialog)
        scope = QtWidgets.QComboBox()
        scope.addItem("All read or written values", False)
        scope.addItem("Selected read or written values", True)
        layout.addWidget(scope)
        preview = QtWidgets.QPlainTextEdit()
        preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        layout.addWidget(preview, 1)
        buttons = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton("Copy")
        save_btn = QtWidgets.QPushButton("Save Markdown...")
        close_btn = QtWidgets.QPushButton("Close")
        buttons.addWidget(copy_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        def refresh_preview():
            preview.setPlainText(self._report_text(bool(scope.currentData())))

        def save_report():
            path, _chosen_filter = QtWidgets.QFileDialog.getSaveFileName(
                dialog, "Save SDO report", "ethercat_sdo_report.md", "Markdown (*.md);;Text (*.txt)"
            )
            if path:
                try:
                    Path(path).write_text(preview.toPlainText())
                except OSError as ex:
                    QtWidgets.QMessageBox.critical(dialog, "Could not save report", str(ex))

        scope.currentIndexChanged.connect(lambda _index: refresh_preview())
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(preview.toPlainText()))
        save_btn.clicked.connect(save_report)
        close_btn.clicked.connect(dialog.accept)
        refresh_preview()
        dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()

    def _command_error(self, title, code, stdout, stderr, show_dialog=True):
        if code == 130:
            self.statusBar().showMessage("SSH command cancelled")
            return
        detail = (stderr or stdout or "No output").strip()
        self._log(f"ERROR ({code}): {detail}")
        self.statusBar().showMessage(title)
        if show_dialog:
            QtWidgets.QMessageBox.critical(self, title, f"Command exited with status {code}.\n\n{detail}")

    def _log(self, text):
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{stamp}] {text}")

    def _toggle_log(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_btn.setText("Hide Log" if visible else "Show Log")


def main():
    parser = argparse.ArgumentParser(description="Browse EtherCAT SDOs on a remote host over SSH")
    parser.add_argument("host", nargs="?", default="", help="SSH host or user@host")
    parser.add_argument("--master", "-m", default="0", help="EtherCAT master ID")
    parser.add_argument("--slave", "-p", default="0", help="EtherCAT slave position")
    parser.add_argument("--timeout", type=float, default=120.0, help="SSH command timeout in seconds")
    parser.add_argument("--demo", action="store_true", help="load the bundled SDO example without SSH")
    args = parser.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    window = SdoBrowserWindow(args.host, args.master, args.slave, args.timeout, args.demo)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
