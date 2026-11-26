"""Native read-only IOC dashboard and aggregate object navigation."""
from qt_compat import QtCore, QtWidgets
from ecmc_diagnostic_graphics import DiagnosticGraphic

class IocDashboard(QtWidgets.QWidget):
    def __init__(self,snapshot,open_group,global_motion,demo=False):
        super().__init__()
        self.snapshot=snapshot
        self.demo=demo
        self.pvs={}
        self.labels={}
        self.names={}
        self.prefix=snapshot["prefix"]
        layout=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel(self.prefix+" · IOC overview"+(" · DEMO" if demo else ""))
        layout.addWidget(title)
        counts=QtWidgets.QGridLayout()
        for i,(key,title) in enumerate((("axes","Motion axes"),("hardware","EtherCAT slaves"),
                ("plcs","PLCs"),("state_machines","State machines"),("axis_groups","Axis groups"),
                ("data_storages","Data storage"),("plugins","Plugins"),("cpp_logic","CppLogic"))):
            button=QtWidgets.QPushButton(f"{title}: {len(snapshot.get(key,[]))}")
            button.clicked.connect(lambda checked=False,key=key:open_group(key))
            counts.addWidget(button,i//3,i%3)
        layout.addLayout(counts)
        grid=QtWidgets.QGridLayout()
        fields=(("Hostname","MCU-Cfg-Hostname"),("Mode","MCU-Cfg-Mode"),("Application mode","MCU-AppMode"),
                ("EPICS version",f"m{snapshot.get('master','0')}-Epics-Ver"),
                ("ECMCCFG version",f"m{snapshot.get('master','0')}-Ecmccfg-Ver"),
                ("Error ID","MCU-ErrId"),("Error message","MCU-ErrMsg"),("RT priority OK","MCU-ThdRTPrioOK"),
                ("Memory locked","MCU-ThdMemLocked"),("Frequency (Hz)","MCU-ThdFrqAvg"),
                ("Execution max (ns)","MCU-ThdExeMax"),("Send max (ns)","MCU-ThdSndMax"))
        for i,(caption,suffix) in enumerate(fields):
            name=self.prefix+":"+suffix
            label=QtWidgets.QLabel("—")
            label.setWordWrap(True)
            label.setToolTip(name)
            grid.addWidget(QtWidgets.QLabel(caption),i,0)
            grid.addWidget(label,i,1)
            self.labels[name]=label
            self.names[name]=suffix
        grid.setColumnStretch(1,1)
        layout.addLayout(grid)
        self.graphic=DiagnosticGraphic("thread")
        layout.addWidget(self.graphic)
        row=QtWidgets.QHBoxLayout()
        for kill,title in ((False,"STOP ALL"),(True,"KILL ALL")):
            button=QtWidgets.QPushButton(title)
            button.setStyleSheet("font-weight: bold; color: #c43c3c;")
            button.clicked.connect(lambda checked=False,kill=kill:global_motion(kill))
            row.addWidget(button)
        layout.addLayout(row)
        layout.addStretch()
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
    def showEvent(self,event):
        super().showEvent(event)
        if not self.demo:
            try:
                import epics
                for name in self.labels:
                    self.pvs[name]=epics.PV(name,auto_monitor=True)
            except Exception: pass
        self.timer.start()
        self._tick()
    def _tick(self):
        values={}
        for name,label in self.labels.items():
            suffix=self.names[name]
            value=None
            if self.demo:
                value={"MCU-Cfg-Hostname":"demo-host","MCU-Cfg-Mode":"demo","MCU-AppMode":1,
                       "MCU-ErrId":0,"MCU-ErrMsg":"","MCU-ThdRTPrioOK":1,"MCU-ThdMemLocked":1,
                       "MCU-ThdFrqAvg":1000,"MCU-ThdExeMax":48000,"MCU-ThdSndMax":11000}.get(suffix,"demo")
            else:
                try:
                    pv=self.pvs.get(name)
                    if pv is not None and pv.connected and pv.severity!=3:
                        value=pv.get(use_monitor=True,timeout=.001,as_string=suffix=="MCU-ErrMsg")
                except Exception: pass
            label.setText("—" if value is None else str(value))
            values[suffix.removeprefix("MCU-")]=value
        self.graphic.set_values(values)
    def hideEvent(self,event):
        self.timer.stop()
        for pv in self.pvs.values():
            try: pv.disconnect()
            except Exception: pass
        self.pvs.clear()
        super().hideEvent(event)

class ObjectOverview(QtWidgets.QWidget):
    def __init__(self,title,objects,open_object,health):
        super().__init__()
        self.objects=list(objects)
        self.health=health
        layout=QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(title))
        self.table=QtWidgets.QTableWidget(len(objects),4)
        self.table.setHorizontalHeaderLabels(("ID","Name","Health","Open"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(1,QtWidgets.QHeaderView.Stretch)
        for i,obj in enumerate(objects):
            for col,value in enumerate((obj.get("id",""),obj.get("name",""),"—")):
                self.table.setItem(i,col,QtWidgets.QTableWidgetItem(str(value)))
            button=QtWidgets.QPushButton("Open")
            button.clicked.connect(lambda checked=False,obj=obj:open_object(obj))
            self.table.setCellWidget(i,3,button)
        layout.addWidget(self.table)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
    def _tick(self):
        for i,obj in enumerate(self.objects):
            self.table.item(i,2).setText(self.health(obj) or "—")
    def showEvent(self,event):
        super().showEvent(event)
        self.timer.start()
        self._tick()
    def hideEvent(self,event):
        self.timer.stop()
        super().hideEvent(event)
