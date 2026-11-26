"""Visibility-scoped native PLC, state-machine and RT thread panels."""
import math
from qt_compat import QtCore, QtWidgets
from ecmc_native_diagnostic_spec import diagnostic_rows
from ecmc_hardware_widgets import BooleanValue, BooleanStatus, SignalValue
from ecmc_hardware_write import WriteTask, EnumTask

class NativeDiagnosticPanel(QtWidgets.QWidget):
    def __init__(self,prefix,kind,object_id=None,demo=False,on_plot=None,timeout=1.0):
        super().__init__()
        self.demo,self.on_plot,self.timeout=demo,on_plot,timeout
        self.kind=kind
        self.rows=diagnostic_rows(prefix,kind,object_id)
        self._pvs={}
        self._values={}
        self._overrides={}
        self._widgets={}
        self._tasks=set()
        self._generation=0
        self._closed=False
        layout=QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6,6,6,6)
        layout.setSpacing(4)
        self.heading=QtWidgets.QLabel(f"{prefix} · {kind.replace('_',' ').title()}"+("" if object_id is None else f" {object_id}"))
        layout.addWidget(self.heading)
        top=QtWidgets.QHBoxLayout()
        self.status=QtWidgets.QLabel("Paused")
        top.addWidget(self.status)
        top.addStretch()
        self.arm=QtWidgets.QCheckBox("Enable writes")
        self.arm.setVisible(kind!="thread")
        self.arm.toggled.connect(self._controls)
        top.addWidget(self.arm)
        if on_plot and kind=="thread":
            trend=QtWidgets.QPushButton("Trend timing")
            trend.clicked.connect(lambda: on_plot([pv for _,pv,mode in self.rows if mode=="ns"],
                                                  demo_value=self._demo_value if demo else None))
            top.addWidget(trend)
        layout.addLayout(top)
        grid=QtWidgets.QGridLayout()
        grid.setSpacing(4)
        for i,(label,pv,mode) in enumerate(self.rows):
            name=QtWidgets.QLabel(label)
            if mode=="switch":
                w=BooleanValue(lambda old,new,pv=pv: self._write(pv,new,"caToggleButton",old))
            elif mode in ("bool","health","alarm"):
                w=BooleanStatus("#c43c3c" if mode=="alarm" else "#26804b",
                                "Fault" if mode=="alarm" else "Yes",
                                "OK" if mode=="alarm" else "No")
            else:
                w=SignalValue("—",lambda pv=pv: self._choose_enum(pv))
            w.setToolTip(pv)
            w.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            w.customContextMenuRequested.connect(lambda pos,pv=pv,w=w:self._menu(pv,w,pos))
            grid.addWidget(name,i//2,(i%2)*2)
            grid.addWidget(w,i//2,(i%2)*2+1)
            self._widgets[pv]=(w,mode)
        grid.setColumnStretch(1,1)
        grid.setColumnStretch(3,1)
        layout.addLayout(grid)
        layout.addStretch()
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)

    def _controls(self,*_):
        armed=self.arm.isChecked() and self.isVisible() and not self._tasks and not self._closed
        for pv,(w,mode) in self._widgets.items():
            if mode in ("switch","enum"):
                w.set_editable(armed and self._values.get(pv) is not None)

    def _demo_value(self,pv):
        if pv in self._overrides:
            return self._overrides[pv]
        suffix=pv.rsplit("-",1)[-1]
        return {"Desc":"Main PLC","Nam":"Master/slave","SlvGrpNam":"Slaves","MstGrpNam":"Masters",
                "StatTxt":"IDLE","StatPrvStateTxt":"IDLE","StatTrRsnTxt":"NONE",
                "ScanTime":.001,"EnaCmd":1,"ThdRTPrioOK":1,"ThdMemLocked":1,
                "ThdFrqAvg":1000,"ThdPrdMin":994000,"ThdPrdMax":1006000,
                "ThdLatMin":-3000,"ThdLatMax":6000,"ThdExeMin":25000,"ThdExeMax":48000,
                "ThdSndMin":5000,"ThdSndMax":11000}.get(suffix,0)

    def _start(self):
        if self._closed or not self.isVisible() or self.timer.isActive():
            return
        if not self.demo:
            try:
                import epics
                for _,pv,_ in self.rows:
                    self._pvs[pv]=epics.PV(pv,auto_monitor=True)
            except Exception:
                self.status.setText("Channel Access unavailable")
                self._stop()
                return
        self.timer.start()
        self._tick()

    def _stop(self):
        self.timer.stop()
        for pv in self._pvs.values():
            try: pv.disconnect()
            except Exception: pass
        self._pvs.clear()
        self._values.clear()
        for w,mode in self._widgets.values():
            if isinstance(w,(BooleanValue,BooleanStatus)): w.set_value(None)
            else: w.setText("—")
        self.status.setText("Paused")

    def _tick(self):
        if not self.isVisible():
            self._stop()
            return
        count=0
        for name,(w,mode) in self._widgets.items():
            value=None
            if self.demo:
                value=self._demo_value(name)
            else:
                pv=self._pvs.get(name)
                try:
                    if pv is not None and pv.connected and pv.severity!=3:
                        value=pv.get(use_monitor=True,timeout=.001,as_string=mode=="text")
                except Exception: pass
            self._values[name]=value
            count+=value is not None
            if isinstance(w,(BooleanValue,BooleanStatus)):
                w.set_value(value)
            else:
                text="—"
                if value is not None:
                    try:
                        if mode=="ns": text=f"{float(value)/1000:.3f} µs"
                        elif mode=="seconds": text=f"{float(value)*1000:.3f} ms"
                        elif mode=="hz": text=f"{float(value):.2f} Hz"
                        elif mode in ("hex","error"): text=f"0x{int(value):X}"
                        elif mode=="enum":
                            labels=("IDLE","SLAVE","MASTER","RESET")
                            text=labels[int(value)] if float(value)==int(value) and 0<=int(value)<len(labels) else str(value)
                        else: text=str(value)
                    except (ValueError,TypeError,OverflowError): text=str(value)
                w.setText(text)
                if mode=="error":
                    w.setStyleSheet("color: #c43c3c;" if value not in (None,0) else "")
        self.status.setText(f"{'DEMO · ' if self.demo else ''}{count}/{len(self.rows)} connected")
        self._controls()

    def _write(self,pv,value,mode,expected=None):
        if self._closed or not self.isVisible() or not self.arm.isChecked() or self._tasks:
            return
        if pv not in self._widgets or self._widgets[pv][1] not in ("switch","enum"):
            return
        generation=self._generation
        if not self.demo and QtWidgets.QMessageBox.question(self,"Confirm command",f"Write {value} to {pv}? This can affect motion.",
                QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:
            return
        if generation!=self._generation or not self.isVisible() or not self.arm.isChecked() or self._tasks:
            return
        if self.demo:
            self._overrides[pv]=value
            self._tick()
            return
        task=WriteTask(pv,value,self.timeout,mode,expected=expected)
        self._tasks.add(task)
        def done(error):
            self._tasks.discard(task)
            if not self._closed:
                self._controls()
                if error: QtWidgets.QMessageBox.warning(self,"Write not confirmed",error)
        task.signals.finished.connect(done)
        self._controls()
        QtCore.QThreadPool.globalInstance().start(task)

    def _choose_enum(self,pv):
        if self._widgets[pv][1]!="enum" or not self.isVisible() or not self.arm.isChecked() or self._tasks or self._values.get(pv) is None:
            return False
        generation=self._generation
        def select(choices,error):
            if generation!=self._generation or not self.isVisible() or not self.arm.isChecked() or self._closed:
                return
            if error or not choices:
                QtWidgets.QMessageBox.warning(self,"Enum unavailable",error or "No choices")
                return
            choice,ok=QtWidgets.QInputDialog.getItem(self,"Select state",pv,choices,0,False)
            if ok and generation==self._generation:
                self._write(pv,choices.index(choice),"caMenu")
        if self.demo:
            select(["IDLE","SLAVE","MASTER","RESET"],"")
        else:
            task=EnumTask(pv,self.timeout)
            self._tasks.add(task)
            def done(choices,error):
                self._tasks.discard(task)
                self._controls()
                select(choices,error)
            task.signals.finished.connect(done)
            self._controls()
            QtCore.QThreadPool.globalInstance().start(task)
        return True

    def _menu(self,pv,w,pos):
        menu=QtWidgets.QMenu(self)
        copy=menu.addAction("Copy PV name")
        copy.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(pv))
        if self.on_plot and self._widgets[pv][1]!="text":
            trend=menu.addAction("Trend")
            trend.triggered.connect(lambda: self.on_plot([pv],demo_value=self._demo_value if self.demo else None))
        if hasattr(menu,"exec"): menu.exec(w.mapToGlobal(pos))
        else: menu.exec_(w.mapToGlobal(pos))

    def showEvent(self,event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0,self._start)

    def hideEvent(self,event):
        self._generation+=1
        self.arm.setChecked(False)
        self._stop()
        super().hideEvent(event)

    def closeEvent(self,event):
        self._closed=True
        self._stop()
        super().closeEvent(event)
