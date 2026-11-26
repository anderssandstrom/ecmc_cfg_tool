"""Native read-only EtherCAT process signal browser."""
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
        layout.addWidget(self.table)
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
            available += value is not None
        self.status.setText(f"{available}/{len(results)} PVs read successfully (snapshot)")
        self.selector.setEnabled(True)
        self.refresh_button.setEnabled(True)

    def plot_selected(self):
        pvs = [self.table.item(i.row(), 3).text()
               for i in self.table.selectionModel().selectedRows()
               if self.table.item(i.row(), 2).text() == "Read OK"]
        if pvs and self.on_plot:
            self.on_plot(pvs)

    def closeEvent(self, event):
        self._closed = True
        super().closeEvent(event)
