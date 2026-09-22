#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import io
from datetime import datetime
from pathlib import Path

from qt_compat import QtCore, QtGui, QtWidgets

from ecmc_sdo import (
    SdoEntry,
    DEFAULT_ETHERCAT_BINARY,
    build_ssh_command,
    decode_command_output,
    display_upload_value,
    download_arguments,
    ecmc_add_sdo_line,
    parse_sdos,
    sdos_arguments,
    upload_arguments,
)


SDO_UNKNOWN_BG = "#f3f6fb"
SDO_BUSY_BG = "#e0f2fe"
SDO_READ_BG = "#dff5dd"
SDO_WRITTEN_BG = "#fff7d6"
SDO_ERROR_BG = "#fee2e2"


class CommandSignals(QtCore.QObject):
    finished = QtCore.pyqtSignal(object, int, str, str) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(object, int, str, str)
    debug = QtCore.pyqtSignal(str) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(str)


class AskpassSignals(QtCore.QObject):
    requested = QtCore.pyqtSignal(str, object) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(str, object)
    debug = QtCore.pyqtSignal(str) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(str)


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
            self.signals.debug.emit("Preparing SSH environment")
            environment["SSH_ASKPASS"] = str(self.askpass)
            environment["SSH_ASKPASS_REQUIRE"] = "force"
            environment["ECMC_SDO_ASKPASS"] = "1"
            environment["ECMC_SDO_ASKPASS_SOCKET"] = self.askpass_socket
            environment["ECMC_SDO_PYTHON"] = sys.executable
            environment.setdefault("DISPLAY", ":0")
            self.signals.debug.emit("Starting SSH process")
            self._process = subprocess.Popen(
                self.command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                start_new_session=True,
            )
            self.signals.debug.emit(f"SSH process started, pid={self._process.pid}")
            if self._cancelled.is_set():
                self.cancel()
            self.signals.debug.emit(f"Waiting for SSH command, timeout={self.timeout:g} s")
            stdout, stderr = self._process.communicate(timeout=self.timeout)
            code = 130 if self._cancelled.is_set() else self._process.returncode
            self.signals.debug.emit(f"SSH command finished with status {code}")
            self.signals.finished.emit(
                self.token, code, decode_command_output(stdout, preserve_bytes=True), decode_command_output(stderr)
            )
        except subprocess.TimeoutExpired as ex:
            self.signals.debug.emit(f"SSH command timed out after {self.timeout:g} s; terminating process group")
            if self._process is not None:
                self.cancel()
                try:
                    self._process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    self.signals.debug.emit("SSH process did not stop after SIGTERM; sending SIGKILL")
                    os.killpg(self._process.pid, signal.SIGKILL)
                    self._process.communicate()
            stdout = decode_command_output(ex.stdout, preserve_bytes=True)
            stderr = decode_command_output(ex.stderr)
            self.signals.finished.emit(self.token, 124, stdout, stderr or f"Command timed out after {self.timeout:g} s")
        except Exception as ex:
            self.signals.debug.emit(f"SSH command worker failed: {ex}")
            self.signals.finished.emit(self.token, 1, "", str(ex))


class SdoBrowserWindow(QtWidgets.QMainWindow):
    ENTRY_ROLE = int(QtCore.Qt.UserRole)
    KNOWN_ROLE = ENTRY_ROLE + 1
    SOURCE_ROLE = ENTRY_ROLE + 2
    RAW_VALUE_ROLE = ENTRY_ROLE + 3
    COL_INDEX = 0
    COL_NAME = 1
    COL_VALUE = 2
    COL_READ = 3
    COL_WRITE = 4
    COL_STATUS = 5
    COL_TYPE = 6
    COL_BITS = 7
    COL_ACCESS = 8

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
        self._askpass_signals.debug.connect(self._debug_log)
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
        self.show_subindex = QtWidgets.QCheckBox("Show SubIndex")
        self.show_subindex.setToolTip('Show SDO entries whose name contains "SubIndex"')
        self.show_subindex.toggled.connect(self._apply_filter)
        filter_row.addWidget(self.show_subindex)
        layout.addLayout(filter_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(9)
        self.tree.setHeaderLabels(["Index", "Name", "Value", "R", "W", "Status", "Type", "Bits", "Access"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        header = self.tree.header()
        header.setStretchLastSection(False)
        for column in range(self.tree.columnCount()):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.Interactive)
        for column, width in (
            (0, 120),
            (1, 200),
            (2, 85),
            (3, 34),
            (4, 34),
            (5, 48),
            (6, 115),
            (7, 75),
            (8, 85),
        ):
            self.tree.setColumnWidth(column, width)
        self.tree.itemDoubleClicked.connect(self._read_item)
        self.tree.itemSelectionChanged.connect(self._update_details)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(self.tree)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(2000)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

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
        self.report_btn = QtWidgets.QPushButton("Report")
        self.report_btn.clicked.connect(self._show_report)
        self.snippet_btn = QtWidgets.QPushButton("ecmc Snippet")
        self.snippet_btn.clicked.connect(self._show_snippet)
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
        footer.addWidget(self.snippet_btn)
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
        self._log(f"SSH target={host}, remote ethercat={binary}, args={' '.join(arguments)}")
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
            self._debug_log(
                f"SSH callback entered: status={code}, stdout={len(stdout)} chars, stderr={len(stderr)} chars"
            )
            callback(result_token, code, stdout, stderr)
            self._debug_log("SSH callback returned")

        task.signals.debug.connect(self._debug_log)
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
                    prompt_text = prompt.decode("utf-8")
                    self._askpass_signals.debug.emit("SSH askpass requested: " + prompt_text.replace("\n", " "))
                    self._askpass_signals.requested.emit(prompt_text, reply)
                    reply.ready.wait(self.timeout)
                    self._askpass_signals.debug.emit(
                        "SSH askpass reply received" if reply.answer is not None else "SSH askpass cancelled or timed out"
                    )
                    payload = b"\x00" if reply.answer is None else b"\x01" + reply.answer.encode("utf-8")
                    connection.sendall(payload)
                except (OSError, UnicodeError, ValueError) as ex:
                    self._askpass_signals.debug.emit(f"SSH askpass bridge failed: {ex}")

    def _show_ssh_prompt(self, prompt, reply):
        if self._askpass_closed.is_set() or self._active_task is None:
            self._debug_log("Ignoring SSH prompt because no active task is waiting")
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
        self._debug_log(f"Parsing SDO dictionary: {len(text)} chars")
        objects = parse_sdos(text)
        entry_total = sum(len(obj.entries) for obj in objects)
        self._debug_log(f"Parsed SDO dictionary: {len(objects)} objects, {entry_total} entries")
        self.tree.clear()
        entry_count = 0
        for obj_index, obj in enumerate(objects, start=1):
            parent = QtWidgets.QTreeWidgetItem([obj.index, obj.name, "", "", "", "", "", "", ""])
            parent.setFirstColumnSpanned(False)
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            self.tree.addTopLevelItem(parent)
            for entry in obj.entries:
                self._add_entry(parent, entry)
                entry_count += 1
                if entry_count % 100 == 0:
                    self._debug_log(f"Built {entry_count}/{entry_total} SDO rows")
                    QtWidgets.QApplication.processEvents()
            if obj_index % 20 == 0:
                QtWidgets.QApplication.processEvents()
        self._debug_log("Applying SDO filters")
        self._apply_filter()
        self._debug_log("Collapsing SDO tree")
        self.tree.collapseAll()
        self.summary.setText(f"{len(objects)} objects, {entry_count} entries")
        self.statusBar().showMessage(status_message, 4000)
        self._log(f"Loaded {len(objects)} objects and {entry_count} entries")
        self._debug_log("SDO tree population finished")

    def _add_entry(self, parent, entry: SdoEntry):
        item = QtWidgets.QTreeWidgetItem([
            f"{entry.index}:{entry.subindex[2:]}", entry.name, "", "", "", "",
            entry.data_type, entry.bit_length, entry.access,
        ])
        item.setData(0, self.ENTRY_ROLE, entry)
        parent.addChild(item)

        value_edit = QtWidgets.QLineEdit()
        value_edit.setPlaceholderText("value")
        value_edit.setEnabled(entry.writable or entry.readable)
        self.tree.setItemWidget(item, self.COL_VALUE, value_edit)
        read_btn = QtWidgets.QPushButton("R")
        read_btn.setToolTip("Read SDO")
        read_btn.setFixedWidth(28)
        read_btn.setEnabled(entry.readable)
        if not entry.has_data:
            read_btn.setToolTip("0-bit SDO entries cannot be read")
        elif not entry.readable:
            read_btn.setToolTip("SDO access flags do not allow reading")
        read_btn.clicked.connect(lambda _checked=False, row=item: self._read_item(row))
        self.tree.setItemWidget(item, self.COL_READ, read_btn)
        write_btn = QtWidgets.QPushButton("W")
        write_btn.setToolTip("Write SDO")
        write_btn.setFixedWidth(28)
        write_btn.setEnabled(entry.writable)
        if not entry.has_data:
            write_btn.setToolTip("0-bit SDO entries cannot be written")
        elif not entry.writable:
            write_btn.setToolTip("SDO access flags do not allow writing")
        write_btn.clicked.connect(lambda _checked=False, row=item: self._write_item(row))
        self.tree.setItemWidget(item, self.COL_WRITE, write_btn)
        self._set_row_state(item, "unknown")

    def _set_row_state(self, item, state):
        colors = {
            "unknown": SDO_UNKNOWN_BG,
            "busy": SDO_BUSY_BG,
            "read": SDO_READ_BG,
            "written": SDO_WRITTEN_BG,
            "error": SDO_ERROR_BG,
        }
        color = colors.get(state, SDO_UNKNOWN_BG)
        editor = self.tree.itemWidget(item, self.COL_VALUE)
        if editor is not None:
            pal = editor.palette()
            pal.setColor(QtGui.QPalette.Base, QtGui.QColor(color))
            editor.setPalette(pal)
        brush = QtGui.QBrush(QtGui.QColor(color))
        item.setBackground(self.COL_STATUS, brush)
        item.setBackground(self.COL_VALUE, brush)

    def _entry(self, item):
        return item.data(0, self.ENTRY_ROLE) if item is not None else None

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None or self._entry(item) is not None:
            return
        readable_count = sum(
            1 for i in range(item.childCount())
            if (
                not item.child(i).isHidden()
                and self._entry(item.child(i)) is not None
                and self._entry(item.child(i)).readable
            )
        )
        menu = QtWidgets.QMenu(self)
        read_action = menu.addAction(f"Read All Under Index ({readable_count})")
        read_action.setEnabled(readable_count > 0)
        read_action.triggered.connect(lambda _checked=False, row=item: self._read_index(row))
        menu.exec_(self.tree.viewport().mapToGlobal(pos)) if hasattr(menu, "exec_") else menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _update_details(self):
        selected = self.tree.selectedItems()
        if not selected:
            self.details.setPlainText("")
            return
        item = selected[0]
        entry = self._entry(item)
        if entry is None:
            self.details.setPlainText(
                f"Index: {item.text(0)}\n"
                f"Name: {item.text(1)}\n"
                f"Subindexes: {item.childCount()}\n"
                f"Visible readable: {sum(1 for i in range(item.childCount()) if not item.child(i).isHidden() and self._entry(item.child(i)) and self._entry(item.child(i)).readable)}"
            )
            return
        editor = self.tree.itemWidget(item, self.COL_VALUE)
        value = editor.text().strip() if editor is not None else ""
        raw = str(item.data(0, self.RAW_VALUE_ROLE) or "")
        source = str(item.data(0, self.SOURCE_ROLE) or "")
        _host, master, slave = self._connection()
        details = [
            f"Index: {entry.index}",
            f"Subindex: {entry.subindex}",
            f"Name: {entry.name}",
            f"Type: {entry.data_type}",
            f"Effective type: {entry.effective_data_type or '(none)'}",
            f"Bits: {entry.bit_length}",
            f"Access: {entry.access}",
            f"Readable: {'yes' if entry.readable else 'no'}",
            f"Writable: {'yes' if entry.writable else 'no'}",
            f"Status: {item.text(self.COL_STATUS)}",
            f"Source: {source or '(none)'}",
            f"Value: {value}",
            f"Raw upload: {raw}",
            "Upload command: " + " ".join(upload_arguments(master, slave, entry)),
        ]
        if entry.writable:
            details.append("Download command: " + " ".join(download_arguments(master, slave, entry, value or "<value>")))
        self.details.setPlainText("\n".join(details))

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
                items = [selected.child(i) for i in range(selected.childCount()) if not selected.child(i).isHidden()]
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
        item.setText(self.COL_STATUS, "Reading...")
        self._set_row_state(item, "busy")
        self._update_details()

        def finished(row, code, stdout, stderr):
            self._read_finished(row, code, stdout, stderr, show_dialog=False)
            if code == 130:
                self._read_queue.clear()
            self._run_next_selected_read()

        self._read_entry(item, entry, finished)

    def _write_selected(self):
        if self._validate_connection() is None:
            return
        rows = []
        for item, entry in self._selected_entry_items():
            editor = self.tree.itemWidget(item, self.COL_VALUE)
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
            (
                f"Write {len(rows)} selected SDO entr{'y' if len(rows) == 1 else 'ies'}?\n\n"
                + "\n".join(
                    f"{entry.index}:{entry.subindex[2:]} {entry.name} = {value}"
                    for _item, entry, value in rows[:12]
                )
                + ("\n..." if len(rows) > 12 else "")
                + "\n\nThis writes to the EtherCAT slave."
            ),
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
        item.setText(self.COL_STATUS, "Writing...")
        self._set_row_state(item, "busy")

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
        item.setText(self.COL_STATUS, "Reading...")
        self._set_row_state(item, "busy")
        self._read_entry(item, entry, self._read_finished)

    def _read_entry(self, item, entry, callback):
        _host, master, slave = self._connection()

        def typed_finished(row, code, stdout, stderr):
            if code not in (0, 130) and entry.is_text_like:
                row.setText(self.COL_STATUS, "Retrying without type")
                self._set_row_state(row, "busy")
                self._log(f"Typed upload failed for {row.text(0)}; retrying without --type")
                self._update_details()
                self._run(row, upload_arguments(master, slave, entry, include_type=False), callback)
                return
            callback(row, code, stdout, stderr)

        self._run(item, upload_arguments(master, slave, entry), typed_finished)

    def _read_finished(self, item, code, stdout, stderr, show_dialog=True):
        if code != 0:
            item.setText(self.COL_STATUS, "Read failed")
            self._set_row_state(item, "error")
            self._update_details()
            self._command_error("SDO upload failed", code, stdout, stderr, show_dialog)
            return
        raw_value = stdout.strip()
        entry = self._entry(item)
        value = display_upload_value(entry, raw_value) if entry is not None else raw_value
        editor = self.tree.itemWidget(item, self.COL_VALUE)
        if editor is not None:
            editor.setText(value)
        item.setData(0, self.KNOWN_ROLE, True)
        item.setData(0, self.SOURCE_ROLE, "read")
        item.setData(0, self.RAW_VALUE_ROLE, raw_value)
        item.setText(self.COL_STATUS, "Read")
        self._set_row_state(item, "read")
        self._update_details()
        self.statusBar().showMessage(f"Read {item.text(0)}", 3000)

    def _write_item(self, item):
        entry = self._entry(item)
        if entry is None or not entry.writable:
            return
        editor = self.tree.itemWidget(item, self.COL_VALUE)
        value = editor.text().strip() if editor is not None else ""
        if not value:
            QtWidgets.QMessageBox.warning(self, "Missing value", "Enter a value before writing.")
            return
        old_value = str(item.data(0, self.RAW_VALUE_ROLE) or "")
        answer = QtWidgets.QMessageBox.question(
            self,
            "Confirm SDO write",
            (
                f"Write SDO {entry.index}:{entry.subindex[2:]} ({entry.name})?\n\n"
                f"Type: {entry.data_type}\n"
                f"Access: {entry.access}\n"
                f"Current/session value: {old_value or '(not read)'}\n"
                f"New value: {value}\n\n"
                "This writes to the EtherCAT slave."
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        _host, master, slave = self._connection()
        item.setText(self.COL_STATUS, "Writing...")
        self._set_row_state(item, "busy")
        self._run(item, download_arguments(master, slave, entry, value), self._write_finished)

    def _write_finished(self, item, code, stdout, stderr, show_dialog=True):
        if code != 0:
            item.setText(self.COL_STATUS, "Write failed")
            self._set_row_state(item, "error")
            self._update_details()
            self._command_error("SDO download failed", code, stdout, stderr, show_dialog)
            return
        item.setText(self.COL_STATUS, "Written")
        item.setData(0, self.KNOWN_ROLE, True)
        item.setData(0, self.SOURCE_ROLE, "written")
        item.setData(0, self.RAW_VALUE_ROLE, "")
        self._set_row_state(item, "written")
        self.statusBar().showMessage(f"Wrote {item.text(0)}", 3000)
        if stdout.strip():
            self._log(stdout.strip())
        self._update_details()

    def _apply_filter(self, _text=None):
        needle = self.search.text().strip().lower()
        show_zero_bit = self.show_zero_bit.isChecked()
        show_subindex = self.show_subindex.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            parent_match = needle in " ".join(parent.text(c).lower() for c in range(self.tree.columnCount()))
            visible_children = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                entry = self._entry(child)
                zero_bit_hidden = entry is not None and not entry.has_data and not show_zero_bit
                subindex_hidden = entry is not None and "subindex" in entry.name.lower() and not show_subindex
                matches = parent_match or not needle or needle in " ".join(
                    child.text(c).lower() for c in range(self.tree.columnCount())
                )
                matches = matches and not zero_bit_hidden and not subindex_hidden
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
            editor = self.tree.itemWidget(item, self.COL_VALUE)
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
        return "\n".join(lines)

    def _snippet_text(self, selected_only=False):
        _host, _master, slave = self._connection()
        rows = self._known_rows(selected_only)
        lines = []
        if rows:
            lines.extend(ecmc_add_sdo_line(slave, entry, value) for _item, entry, value in rows)
        else:
            lines.append("# No successfully read or written values in this scope.")
        return "\n".join(lines) + "\n"

    def _report_rows(self, selected_only=False):
        rows = []
        for item, entry, value in self._known_rows(selected_only):
            rows.append({
                "index": entry.index,
                "subindex": entry.subindex,
                "name": entry.name,
                "type": entry.data_type,
                "bits": entry.bit_length,
                "access": entry.access,
                "source": str(item.data(0, self.SOURCE_ROLE) or "session"),
                "value": value,
                "raw_upload": str(item.data(0, self.RAW_VALUE_ROLE) or ""),
            })
        return rows

    def _report_csv(self, selected_only=False):
        output = io.StringIO()
        fieldnames = ["index", "subindex", "name", "type", "bits", "access", "source", "value", "raw_upload"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(self._report_rows(selected_only))
        return output.getvalue()

    def _report_json(self, selected_only=False):
        host, master, slave = self._connection()
        payload = {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "host": host,
            "master": master,
            "slave": slave,
            "entries": self._report_rows(selected_only),
        }
        return json.dumps(payload, indent=2)

    def _show_report(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("SDO Report")
        dialog.resize(850, 600)
        layout = QtWidgets.QVBoxLayout(dialog)
        scope = QtWidgets.QComboBox()
        scope.addItem("All read or written values", False)
        scope.addItem("Selected read or written values", True)
        layout.addWidget(scope)
        fmt = QtWidgets.QComboBox()
        fmt.addItem("Markdown report", "md")
        fmt.addItem("CSV", "csv")
        fmt.addItem("JSON", "json")
        layout.addWidget(fmt)
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
            selected_only = bool(scope.currentData())
            kind = fmt.currentData()
            if kind == "csv":
                preview.setPlainText(self._report_csv(selected_only))
            elif kind == "json":
                preview.setPlainText(self._report_json(selected_only))
            else:
                preview.setPlainText(self._report_text(selected_only))

        def save_report():
            kind = fmt.currentData()
            filename = {
                "csv": "ethercat_sdo_report.csv",
                "json": "ethercat_sdo_report.json",
            }.get(kind, "ethercat_sdo_report.md")
            filters = "CSV (*.csv);;JSON (*.json);;Markdown (*.md);;Text (*.txt)"
            path, _chosen_filter = QtWidgets.QFileDialog.getSaveFileName(
                dialog, "Save SDO report", filename, filters
            )
            if path:
                try:
                    Path(path).write_text(preview.toPlainText())
                except OSError as ex:
                    QtWidgets.QMessageBox.critical(dialog, "Could not save report", str(ex))

        scope.currentIndexChanged.connect(lambda _index: refresh_preview())
        fmt.currentIndexChanged.connect(lambda _index: refresh_preview())
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(preview.toPlainText()))
        save_btn.clicked.connect(save_report)
        close_btn.clicked.connect(dialog.accept)
        refresh_preview()
        dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()

    def _show_snippet(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("ecmc SDO Snippet")
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
        save_btn = QtWidgets.QPushButton("Save Snippet...")
        close_btn = QtWidgets.QPushButton("Close")
        buttons.addWidget(copy_btn)
        buttons.addWidget(save_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        def refresh_preview():
            preview.setPlainText(self._snippet_text(bool(scope.currentData())))

        def save_snippet():
            path, _chosen_filter = QtWidgets.QFileDialog.getSaveFileName(
                dialog,
                "Save ecmc SDO snippet",
                "ethercat_sdo_ecmc_snippet.cmd",
                "Command files (*.cmd);;Markdown (*.md);;Text (*.txt)",
            )
            if path:
                try:
                    Path(path).write_text(preview.toPlainText())
                except OSError as ex:
                    QtWidgets.QMessageBox.critical(dialog, "Could not save snippet", str(ex))

        scope.currentIndexChanged.connect(lambda _index: refresh_preview())
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(preview.toPlainText()))
        save_btn.clicked.connect(save_snippet)
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

    def _debug_log(self, text):
        print(f"[SDO debug] {text}", file=sys.stderr, flush=True)
        if not self.log.isVisible():
            self.log.setVisible(True)
            self.log_btn.setText("Hide Log")
        self._log("debug: " + str(text))

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
