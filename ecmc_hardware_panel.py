"""Native read-only EtherCAT process signal browser."""
import re
from qt_compat import QtCore, QtWidgets
from ecmc_epics import EpicsClient
from ecmc_hardware_catalog import hardware_pvs, hardware_writable_pvs
from ecmc_hardware_write import WriteTask


class ReadSignals(QtCore.QObject):
    finished = QtCore.Signal(object) if hasattr(QtCore, "Signal") else QtCore.pyqtSignal(object)


class ReadTask(QtCore.QRunnable):
    def __init__(self, pvs, timeout):
        super().__init__()
        self.pvs, self.timeout = pvs, timeout
        self.signals = ReadSignals()

    def run(self):
        result = []
        try:
            client = EpicsClient(timeout=self.timeout)
        except Exception as ex:
            self.signals.finished.emit([(pv, None, str(ex)) for pv in self.pvs])
            return
        for pv in self.pvs:
            try:
                result.append((pv, client.get(pv), ""))
            except Exception as ex:
                result.append((pv, None, str(ex)))
        self.signals.finished.emit(result)


class HardwarePanel(QtWidgets.QWidget):
    def __init__(self, prefix, master, slaves, timeout=1.0, demo=False, on_plot=None):
        super().__init__()
        self.timeout, self.demo, self.on_plot = timeout, demo, on_plot
        self._task = None
        self._closed = False
        self._write_task = None
        self.writable = {}
        self.rows = {}
        layout = QtWidgets.QVBoxLayout(self)
        self.selector = QtWidgets.QComboBox()
        for slave in slaves:
            self.selector.addItem(
                f"m{slave.get('master', master)} s{int(slave['id']):03d}  {slave.get('name', '')}",
                slave)
        self.prefix, self.master = prefix, master
        layout.addWidget(self.selector)
        self.status = QtWidgets.QLabel("Hardware diagnostics")
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Signal", "Value", "Status", "PV"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.views = QtWidgets.QTabWidget()
        self.cards = QtWidgets.QScrollArea()
        self.cards.setWidgetResizable(True)
        self.views.addTab(self.cards, "Hardware")
        self.views.addTab(self.table, "All PVs")
        layout.addWidget(self.views, 1)
        self.card_values = {}
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_menu)
        buttons = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        self.write_enable = QtWidgets.QCheckBox("Enable writes")
        self.write_enable.setEnabled(not demo)
        buttons.addWidget(self.write_enable)
        buttons.addStretch(1)
        buttons.addWidget(QtWidgets.QLabel("Right-click a signal to trend or copy PV"))
        layout.addLayout(buttons)
        self.selector.currentIndexChanged.connect(lambda _: self.write_enable.setChecked(False))
        self.selector.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        if self._closed or self._task is not None or self._write_task is not None:
            return
        slave = self.selector.currentData()
        if not slave:
            return
        candidates = slave.get("panels") or [slave.get("panel"), slave.get("name")]
        pairs = hardware_pvs(self.prefix, slave.get("master", self.master), slave["id"], candidates)
        self.writable = hardware_writable_pvs(self.prefix, slave.get("master", self.master), slave["id"], candidates)
        self.table.setRowCount(len(pairs))
        self.rows = {}
        for row, (label, pv) in enumerate(pairs):
            self.rows[pv] = row
            for col, value in enumerate((label, "—", "Demo: not read" if self.demo else "Pending", pv)):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.build_cards(pairs)
        if self.demo:
            self.status.setText("Demo mode — no network requests")
            return
        self.status.setText("Reading PVs…")
        self.selector.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self._task = ReadTask(list(self.rows), self.timeout)
        self._task.signals.finished.connect(self._finished)
        QtCore.QThreadPool.globalInstance().start(self._task)

    def _finished(self, results):
        self._task = None
        if self._closed:
            return
        available = 0
        for pv, value, error in results:
            row = self.rows[pv]
            self.table.item(row, 1).setText(str(value) if value is not None else "—")
            item = self.table.item(row, 2)
            item.setText("Read OK" if value is not None else "Unavailable")
            item.setToolTip(error)
            self.update_card(pv, value, error)
            available += value is not None
        self.status.setText(f"{available}/{len(results)} PVs read successfully (snapshot)")
        self.selector.setEnabled(True)
        self.refresh_button.setEnabled(True)

    def _bind(self, widget, pv):
        widget.setToolTip(pv)
        widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, w=widget, pv=pv: self.pv_menu(w.mapToGlobal(pos), pv))

    def _group(self, title, rows):
        card = QtWidgets.QGroupBox(title)
        form = QtWidgets.QGridLayout(card)
        form.setContentsMargins(5, 4, 5, 4)
        form.setVerticalSpacing(2)
        for row, (name, pv, short) in enumerate(rows):
            key = QtWidgets.QLabel(short)
            value = QtWidgets.QLabel("—")
            value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            for widget in (key, value):
                self._bind(widget, pv)
            form.addWidget(key, row, 0)
            form.addWidget(value, row, 1)
            self.card_values[pv] = (name, value)
        form.setColumnStretch(1, 1)
        return card

    def build_cards(self, pairs):
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.card_values = {}
        self.device_values = {}
        header = QtWidgets.QHBoxLayout()
        channels, other = {}, []
        for name, pv in pairs:
            if name == "PnlTyp":
                continue
            if name in ("HWType", "Online", "Operational") or name.startswith("Alstate-"):
                label = QtWidgets.QLabel("—" if name == "HWType" else "○ " + name.replace("Alstate-", ""))
                self._bind(label, pv)
                header.addWidget(label)
                self.device_values[pv] = (name, label)
                continue
            m = re.fullmatch(r"(Drv|Enc|Tp|AI|AO|BI|BO)(\d+)(?:-?(.*))", name)
            if m:
                kind, number, suffix = m.groups()
                channels.setdefault(int(number), {}).setdefault(kind + number, []).append((name, pv, suffix or "Value"))
            else:
                other.append((name, pv, name))
        header.addStretch(1)
        layout.addLayout(header)
        grid = QtWidgets.QGridLayout()
        for i, (ch, groups) in enumerate(sorted(channels.items())):
            col = QtWidgets.QWidget()
            stack = QtWidgets.QVBoxLayout(col)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(3)
            title = QtWidgets.QLabel(f"Channel {ch:02d}")
            title.setStyleSheet("font-weight: bold;")
            stack.addWidget(title)
            for group, rows in sorted(groups.items(), key=lambda item: ({"Drv":0,"Enc":1,"Tp":2}.get(re.sub(r"\d+$","",item[0]),3),item[0])):
                stack.addWidget(self._group(group, rows))
            stack.addStretch(1)
            grid.addWidget(col, i // 2, i % 2, QtCore.Qt.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        if other:
            layout.addWidget(self._group("Other signals", other))
        layout.addStretch(1)
        self.cards.setWidget(root)

    def update_card(self, pv, value, error):
        truth = {"1": True, "0": False, "1.0": True, "0.0": False,
                 "true": True, "false": False, "yes": True, "no": False}.get(str(value).strip().lower())
        if pv in self.device_values:
            name, label = self.device_values[pv]
            label.setToolTip(pv + ("\n" + error if error else ""))
            if name == "HWType":
                label.setText(str(value) if value is not None else "Unknown")
                label.setStyleSheet("font-weight: bold;")
                return
            caption = name.replace("Alstate-", "")
            if name.startswith("Alstate-"):
                color = ("#26804b" if name == "Alstate-Op" else "#c43c3c") if truth else "#888"
            else:
                color = "#26804b" if truth is True else "#c43c3c" if truth is False else "#888"
            label.setText(("● " if truth is not None else "○ ") + caption)
            label.setStyleSheet("font-weight: bold; color: " + color + ";")
            return
        if pv not in self.card_values:
            return
        name, label = self.card_values[pv]
        label.setToolTip(pv + ("\n" + error if error else ""))
        if value is None:
            label.setText("—")
            label.setStyleSheet("color: #888;")
            return
        alarm = name.endswith("Alrm")
        positive = name.endswith(("Rdy", "RdyStat", "RdyToEna"))
        digital = bool(re.fullmatch(r"(BI|BO)\d+(-RB)?", name))
        if truth is not None and (alarm or positive or digital):
            color = ("#c43c3c" if truth == alarm else "#26804b") if (alarm or positive) else ("#26804b" if truth else "#888")
            label.setText("●")
            label.setToolTip(f"{pv}: {value}")
            label.setStyleSheet("font-weight: bold; color: " + color + ";")
        else:
            label.setText(str(value))
            label.setStyleSheet("font-weight: bold;")

    def table_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is not None:
            self.pv_menu(self.table.viewport().mapToGlobal(pos),
                         self.table.item(item.row(), 3).text())

    def pv_menu(self, global_pos, pv):
        menu = QtWidgets.QMenu(self)
        trend = menu.addAction("Trend PV")
        row = self.rows.get(pv)
        trend.setEnabled(not self.demo and self.on_plot is not None and row is not None
                         and self.table.item(row, 2).text() == "Read OK")
        trend.triggered.connect(lambda: self.on_plot([pv]))
        if pv in self.writable:
            write = menu.addAction("Write PV…")
            write.setEnabled(self._can_write(pv))
            write.triggered.connect(lambda: self._write_pv(pv))
        copy = menu.addAction("Copy PV name")
        copy.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(pv))
        if hasattr(menu, "exec"):
            menu.exec(global_pos)
        else:
            menu.exec_(global_pos)

    def _can_write(self, pv):
        row = self.rows.get(pv)
        return (not self.demo and not self._closed and self.write_enable.isChecked()
                and self._task is None and self._write_task is None
                and pv in self.writable and row is not None
                and self.table.item(row, 2).text() == "Read OK")

    def _write_pv(self, pv):
        if not self._can_write(pv):
            return
        value, ok = QtWidgets.QInputDialog.getText(
            self, "Write hardware PV", pv + "\nEnter new numeric value:")
        if not ok:
            return
        try:
            from ecmc_hardware_write import parse_write_value
            value = parse_write_value(value, self.writable[pv])
        except ValueError as ex:
            QtWidgets.QMessageBox.warning(self, "Invalid value", str(ex))
            return
        if QtWidgets.QMessageBox.question(
                self, "Confirm hardware write",
                f"Write {value} to {pv}?\nThis may directly affect connected hardware.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if not self._can_write(pv):
            return
        self.selector.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.write_enable.setEnabled(False)
        self.status.setText("Writing " + pv)
        self._write_task = WriteTask(pv, value, self.timeout, self.writable[pv])
        self._write_task.signals.finished.connect(self._write_finished)
        QtCore.QThreadPool.globalInstance().start(self._write_task)

    def _write_finished(self, error):
        self._write_task = None
        if self._closed:
            return
        self.selector.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.write_enable.setEnabled(True)
        if error:
            QtWidgets.QMessageBox.warning(self, "Write not confirmed", error)
        self.refresh()

    def plot_selected(self):
        pvs = [self.table.item(i.row(), 3).text()
               for i in self.table.selectionModel().selectedRows()
               if self.table.item(i.row(), 2).text() == "Read OK"]
        if pvs and self.on_plot:
            self.on_plot(pvs)

    def closeEvent(self, event):
        self._closed = True
        super().closeEvent(event)
