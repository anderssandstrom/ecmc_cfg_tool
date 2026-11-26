"""Native read-only EtherCAT master diagnostics, based on ecmcEcExpert.ui."""
from qt_compat import QtCore, QtWidgets
import time

MASTER_SIGNALS = (
    ("Master OK", "Stat-OK"),
    ("Domain WC complete", "Dom-WC-Complete"),
    ("Domain WC incomplete", "Dom-WC-Incomplete"),
    ("Domain WC zero", "Dom-WC-Zero"),
    ("Domain failures (total)", "Dom-FailCntrTot"),
    ("Slave count", "SlvCntr"),
    ("PDO entry count", "EntryCntr"),
    ("Memory map count", "MemmapCntr"),
    ("Redundancy active", "Dom-RedunActive"),
)

class MasterPanel(QtWidgets.QWidget):
    def __init__(self, prefix, master, slaves, timeout=1.0, demo=False, on_plot=None, on_slave=None):
        super().__init__()
        self.demo, self.on_plot, self.on_slave = demo, on_plot, on_slave
        self._closed = False
        self._pvs = {}
        self._last = {}
        self._events = []
        self._signals = {}
        self._slave_rows = {}
        self._slave_data = {}
        self._master_base = f"{prefix.rstrip(':')}:m{int(master)}"
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(f"EtherCAT master {master}")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        self.status = QtWidgets.QLabel("Paused")
        layout.addWidget(self.status)
        form = QtWidgets.QGridLayout()
        for row,(label, suffix) in enumerate(MASTER_SIGNALS):
            pv = self._master_base + "-" + suffix
            name = QtWidgets.QLabel(label)
            value = QtWidgets.QLabel("—")
            value.setToolTip(pv)
            trend = QtWidgets.QPushButton("Trend")
            trend.setEnabled(on_plot is not None)
            trend.clicked.connect(lambda checked=False, pv=pv: self._trend(pv))
            form.addWidget(name,row,0)
            form.addWidget(value,row,1)
            form.addWidget(trend,row,2)
            self._signals[pv] = value
        layout.addLayout(form)
        self.slaves = QtWidgets.QTableWidget(0,4)
        self.slaves.setHorizontalHeaderLabels(["Slave","Hardware","Online","Operational"])
        self.slaves.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.slaves.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.slaves.horizontalHeader().setSectionResizeMode(1,QtWidgets.QHeaderView.Stretch)
        for slave in slaves:
            if int(slave.get("master",master)) != int(master):
                continue
            row = self.slaves.rowCount()
            self.slaves.insertRow(row)
            base = f"{self._master_base}s{int(slave['id']):03d}"
            self._slave_data[row] = slave
            for col,value in enumerate((str(slave['id']),slave.get("name",""),"—","—")):
                self.slaves.setItem(row,col,QtWidgets.QTableWidgetItem(value))
            for col,suffix in ((2,"Online"),(3,"Operational")):
                self._slave_rows[base+"-"+suffix] = (row,col)
        self.slaves.cellDoubleClicked.connect(self._open_slave)
        layout.addWidget(self.slaves,1)
        layout.addWidget(QtWidgets.QLabel("Observed changes (while visible)"))
        self.events = QtWidgets.QPlainTextEdit()
        self.events.setReadOnly(True)
        self.events.setMaximumBlockCount(250)
        self.events.setMaximumHeight(110)
        layout.addWidget(self.events)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)

    def _open_slave(self,row,col):
        if self.on_slave and row in self._slave_data:
            self.on_slave(self._slave_data[row])

    def _demo_value(self,pv):
        if pv in self._slave_rows:
            return 1
        suffix = pv[len(self._master_base)+1:]
        return {"Stat-OK":1,"Dom-WC-Complete":1,"Dom-WC-Incomplete":0,"Dom-WC-Zero":0,
                "Dom-FailCntrTot":0,"SlvCntr":len(self._slave_data),
                "EntryCntr":None,"MemmapCntr":None,"Dom-RedunActive":0}.get(suffix)

    def _trend(self,pv):
        if self.on_plot:
            self.on_plot([pv],demo_value=self._demo_value if self.demo else None)

    def _start(self):
        if self._closed or not self.isVisible() or self.timer.isActive():
            return
        self._last.clear()
        if not self.demo:
            try:
                import epics
                for name in list(self._signals)+list(self._slave_rows):
                    try:
                        self._pvs[name] = epics.PV(name,auto_monitor=True)
                    except Exception:
                        pass
            except ImportError:
                self.status.setText("Channel Access unavailable")
                return
        self.timer.start()
        self._tick()

    def _stop(self):
        self.timer.stop()
        for pv in self._pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self._pvs.clear()
        self._last.clear()
        self.status.setText("Paused — hidden tab")

    def _read(self,name):
        if self.demo:
            return self._demo_value(name)
        pv = self._pvs.get(name)
        try:
            if pv is not None and pv.connected and pv.severity != 3:
                return pv.get(use_monitor=True,timeout=0.001)
        except Exception:
            pass
        return None

    def _tick(self):
        if not self.isVisible():
            self._stop()
            return
        online = operational = available = 0
        for name in list(self._signals)+list(self._slave_rows):
            value = self._read(name)
            available += value is not None
            if name in self._last and self._last[name] != value:
                self.events.appendPlainText(f"{time.strftime('%H:%M:%S')}  {name}: {self._last[name]} → {value}")
            self._last[name] = value
            if name in self._signals:
                self._signals[name].setText("—" if value is None else str(value))
            else:
                row,col = self._slave_rows[name]
                self.slaves.item(row,col).setText("Unknown" if value is None else "Yes" if value == 1 else "No")
                if col == 2:
                    online += value == 1
                else:
                    operational += value == 1
        total = len(self._slave_data)
        self.status.setText(f"{'DEMO — ' if self.demo else ''}{online}/{total} online | {operational}/{total} operational | {available}/{len(self._signals)+len(self._slave_rows)} PVs available")

    def showEvent(self,event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0,self._start)

    def hideEvent(self,event):
        self._stop()
        super().hideEvent(event)

    def closeEvent(self,event):
        self._closed = True
        self._stop()
        super().closeEvent(event)
