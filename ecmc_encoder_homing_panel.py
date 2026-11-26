"""Read-only encoder homing configuration; only selected encoder is subscribed."""
from qt_compat import QtCore, QtWidgets
from ecmc_axis_diagnostics import format_axis_value
from ecmc_hardware_widgets import BooleanStatus

FIELDS = (("Configured procedure","HmeProc"),("Home position","HmePos"),
          ("Velocity to cam","HmeVelToCam"),("Velocity from cam","HmeVelFrmCam"),
          ("Acceleration","HmeAcc"),("Deceleration","HmeDec"),
          ("Post move enabled","HmePstMveEna"),("Post move position","HmePstMvePos"),
          ("Reference at startup","HmeRefAtStrtToEncId"),("Reference at home","HmeRefAtHme"),
          ("Homed","Homed"))

class EncoderHomingPanel(QtWidgets.QWidget):
    def __init__(self,base,demo=False,on_plot=None):
        super().__init__()
        self.base,self.demo,self.on_plot=base,demo,on_plot
        self.pvs={}
        self.values={}
        self.count=None
        self.active=None
        self.labels={}
        layout=QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        row=QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Encoder"))
        self.selector=QtWidgets.QComboBox()
        row.addWidget(self.selector)
        self.primary=QtWidgets.QLabel("Primary: —")
        row.addWidget(self.primary)
        row.addStretch()
        layout.addLayout(row)
        grid=QtWidgets.QGridLayout()
        for i,(caption,field) in enumerate(FIELDS):
            grid.addWidget(QtWidgets.QLabel(caption),i//2,2*(i%2))
            w=BooleanStatus("#26804b","Homed","Not homed") if field=="Homed" else QtWidgets.QLabel("—")
            grid.addWidget(w,i//2,2*(i%2)+1)
            self.labels[field]=w
        grid.setColumnStretch(1,1)
        grid.setColumnStretch(3,1)
        layout.addLayout(grid)
        self.selector.currentIndexChanged.connect(self._select)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        self.note=QtWidgets.QLabel("Encoder configuration snapshot (read-only); selection does not change the primary encoder.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def set_axis_state(self,count,primary):
        try:
            n=int(count)
            if float(count)!=n or not 1<=n<=8: n=0
        except (ValueError,TypeError,OverflowError): n=0
        self.primary.setText(f"Primary: {format_axis_value(primary)}")
        if n==self.count: return
        previous=self.selector.currentData()
        self.count=n
        self.selector.blockSignals(True)
        self.selector.clear()
        for i in range(1,n+1):
            self.selector.addItem(f"{i}",i)
        if n:
            chosen=previous if previous in range(1,n+1) else 1
            self.selector.setCurrentIndex(chosen-1)
        self.selector.blockSignals(False)
        self._select()

    def _disconnect(self):
        for pv in self.pvs.values():
            try: pv.disconnect()
            except Exception: pass
        self.pvs.clear()
        for w in self.labels.values():
            if isinstance(w,BooleanStatus): w.set_value(None)
            else: w.setText("—")

    def _select(self,*_):
        self._disconnect()
        self.active=self.selector.currentData()
        if self.active is None or not self.isVisible(): return
        for _,field in FIELDS:
            name=f"{self.base}-Enc0{self.active}-{field}"
            self.labels[field].setToolTip(name)
            if not self.demo:
                try:
                    import epics
                    self.pvs[field]=epics.PV(name,auto_monitor=True)
                except Exception: pass
        self._tick()

    def _tick(self):
        if not self.isVisible() or self.active is None: return
        for field,w in self.labels.items():
            value=None
            if self.demo:
                value={"HmeProc":"IndexN via backward LS" if self.active==1 else "Not Homable",
                       "HmeVelToCam":1,"HmeVelFrmCam":.2,"HmeAcc":2,"HmeDec":2,
                       "Homed":1,"HmeRefAtStrtToEncId":-1,"HmeRefAtHme":-1}.get(field,0)
            else:
                try:
                    pv=self.pvs.get(field)
                    if pv is not None and pv.connected and pv.severity!=3:
                        value=pv.get(use_monitor=True,timeout=.001,as_string=field=="HmeProc")
                except Exception: pass
            if isinstance(w,BooleanStatus): w.set_value(value)
            else: w.setText(format_axis_value(value))

    def showEvent(self,event):
        super().showEvent(event)
        self._select()
        self.timer.start()

    def hideEvent(self,event):
        self.timer.stop()
        self._disconnect()
        super().hideEvent(event)
