"""Native read-only EtherCAT process signal browser."""
import re
from qt_compat import QtCore, QtWidgets
from ecmc_epics import EpicsClient
from ecmc_hardware_catalog import hardware_pvs


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
        self.rows = {}
        layout = QtWidgets.QVBoxLayout(self)
        self.selector = QtWidgets.QComboBox()
        for slave in slaves:
            self.selector.addItem(
                f"m{slave.get('master', master)} s{int(slave['id']):03d}  {slave.get('name', '')}",
                slave)
        self.prefix, self.master = prefix, master
        layout.addWidget(self.selector)
        self.status = QtWidgets.QLabel("Read-only hardware diagnostics")
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
        plot = QtWidgets.QPushButton("Plot selected PVs")
        plot.setEnabled(on_plot is not None and not demo)
        plot.clicked.connect(self.plot_selected)
        buttons.addWidget(plot)
        layout.addLayout(buttons)
        self.selector.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        if self._closed or self._task is not None:
            return
        slave = self.selector.currentData()
        if not slave:
            return
        candidates = slave.get("panels") or [slave.get("panel"), slave.get("name")]
        pairs = hardware_pvs(self.prefix, slave.get("master", self.master), slave["id"], candidates)
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

    def build_cards(self, pairs):
        root = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(root)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(5)
        groups = {}
        self.card_values = {}
        for label, pv in pairs:
            m = re.match(r"(Drv|Enc|Tp|AI|AO|BI|BO)(\d+)", label)
            group = ("Channel " + str(int(m[2]))) if m else "Device"
            groups.setdefault(group, []).append((label, pv))
        for index, (title, rows) in enumerate(groups.items()):
            card = QtWidgets.QGroupBox(title)
            form = QtWidgets.QGridLayout(card)
            form.setContentsMargins(5, 5, 5, 5)
            form.setVerticalSpacing(2)
            for row, (name, pv) in enumerate(rows):
                key = QtWidgets.QLabel(name)
                value = QtWidgets.QLabel("—")
                for widget in (key, value):
                    widget.setToolTip(pv)
                    widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                    widget.customContextMenuRequested.connect(
                        lambda pos, w=widget, pv=pv: self.pv_menu(w.mapToGlobal(pos), pv))
                form.addWidget(key, row, 0)
                form.addWidget(value, row, 1)
                self.card_values[pv] = (name, value)
            form.setColumnStretch(1, 1)
            grid.addWidget(card, index // 2, index % 2, QtCore.Qt.AlignTop)
        grid.setRowStretch((len(groups) + 1) // 2, 1)
        self.cards.setWidget(root)

    def update_card(self, pv, value, error):
        if pv not in self.card_values:
            return
        name, label = self.card_values[pv]
        label.setToolTip(error or pv)
        if value is None:
            label.setText("—")
            label.setStyleSheet("color: #888;")
            return
        text = str(value)
        truth = {"1": True, "0": False, "true": True, "false": False,
                 "yes": True, "no": False}.get(text.strip().lower())
        alarm = name.endswith("Alrm")
        positive = name in ("Online", "Operational") or name.endswith(("Rdy", "RdyStat", "RdyToEna"))
        digital = bool(re.fullmatch(r"(BI|BO)\d+(-RB)?", name))
        if truth is not None and (alarm or positive or digital):
            color = ("#c43c3c" if truth == alarm else "#26804b") if (alarm or positive) else ("#26804b" if truth else "#888")
            label.setText("● " + text)
            label.setStyleSheet("font-weight: bold; color: " + color + ";")
        else:
            label.setText(text)
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
        copy = menu.addAction("Copy PV name")
        copy.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(pv))
        if hasattr(menu, "exec"):
            menu.exec(global_pos)
        else:
            menu.exec_(global_pos)

    def plot_selected(self):
        pvs = [self.table.item(i.row(), 3).text()
               for i in self.table.selectionModel().selectedRows()
               if self.table.item(i.row(), 2).text() == "Read OK"]
        if pvs and self.on_plot:
            self.on_plot(pvs)

    def closeEvent(self, event):
        self._closed = True
        super().closeEvent(event)
