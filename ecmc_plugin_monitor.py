"""Explicit read-only plugin-specific PV monitoring, scoped to the selected IOC."""
import re
from qt_compat import QtCore, QtWidgets

def plugin_pv(prefix,name):
    prefix=prefix.rstrip(":")
    name=name.strip()
    if ":" not in name: name=prefix+":"+name
    if not name.startswith(prefix+":") or not re.fullmatch(r"[A-Za-z0-9_:.-]{1,180}",name):
        raise ValueError("Enter a scalar PV name under the selected IOC prefix")
    return name

class PluginMonitor(QtWidgets.QGroupBox):
    def __init__(self,prefix,demo=False,on_plot=None):
        super().__init__("Plugin-specific signals")
        self.prefix,self.demo,self.on_plot=prefix,demo,on_plot
        self.pvs={}
        self.names=[]
        layout=QtWidgets.QVBoxLayout(self)
        row=QtWidgets.QHBoxLayout()
        self.entry=QtWidgets.QLineEdit()
        self.entry.setPlaceholderText("Relative or full IOC PV name")
        row.addWidget(self.entry,1)
        add=QtWidgets.QPushButton("Add PV")
        add.clicked.connect(self._add)
        row.addWidget(add)
        remove=QtWidgets.QPushButton("Remove selected")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        layout.addLayout(row)
        self.table=QtWidgets.QTableWidget(0,2)
        self.table.setHorizontalHeaderLabels(("PV","Value"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
        trend=QtWidgets.QPushButton("Trend selected")
        trend.setEnabled(on_plot is not None and not demo)
        trend.clicked.connect(self._trend)
        layout.addWidget(trend)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
    def _add(self):
        try: name=plugin_pv(self.prefix,self.entry.text())
        except ValueError as ex:
            QtWidgets.QMessageBox.warning(self,"Invalid PV",str(ex))
            return
        if name in self.names or len(self.names)>=32: return
        self.names.append(name)
        self.table.insertRow(len(self.names)-1)
        self.table.setItem(len(self.names)-1,0,QtWidgets.QTableWidgetItem(name))
        self.table.setItem(len(self.names)-1,1,QtWidgets.QTableWidgetItem("—"))
        self.entry.clear()
        self._connect()
    def _remove(self):
        row=self.table.currentRow()
        if row<0: return
        name=self.names.pop(row)
        pv=self.pvs.pop(name,None)
        if pv is not None:
            try: pv.disconnect()
            except Exception: pass
        self.table.removeRow(row)
    def _connect(self):
        if self.demo or not self.isVisible(): return
        try:
            import epics
            for name in self.names:
                if name not in self.pvs: self.pvs[name]=epics.PV(name,auto_monitor=True)
        except Exception: pass
    def _tick(self):
        for i,name in enumerate(self.names):
            value="—"
            try:
                pv=self.pvs.get(name)
                if pv is not None and pv.connected and pv.severity!=3:
                    value=str(pv.get(use_monitor=True,timeout=.001)) if pv.count==1 else "Array (use DAQ)"
            except Exception: pass
            self.table.item(i,1).setText(value[:256])
    def _trend(self):
        row=self.table.currentRow()
        if row>=0 and self.on_plot and not self.demo:
            pv=self.pvs.get(self.names[row])
            if pv is not None and pv.connected and pv.count==1:
                self.on_plot([self.names[row]])
    def showEvent(self,event):
        super().showEvent(event)
        self._connect()
        self.timer.start()
    def hideEvent(self,event):
        self.timer.stop()
        for pv in self.pvs.values():
            try: pv.disconnect()
            except Exception: pass
        self.pvs.clear()
        super().hideEvent(event)
