"""Read-only simultaneous native encoder diagnostics."""
import math
from qt_compat import QtCore, QtWidgets
from ecmc_axis_diagnostics import format_axis_value

FIELDS=("PosAct","VelAct","Homed","Absolute","Ready","ErrId")
def encoder_delta(position,primary):
    try:
        a,b=float(position),float(primary)
        return a-b if math.isfinite(a) and math.isfinite(b) else None
    except (TypeError,ValueError,OverflowError):
        return None

class EncoderOverview(QtWidgets.QWidget):
    def __init__(self,base,demo=False,on_plot=None):
        super().__init__()
        self.base,self.demo,self.on_plot=base,demo,on_plot
        self.count=0
        self.primary=None
        self.pvs={}
        layout=QtWidgets.QVBoxLayout(self)
        self.table=QtWidgets.QTableWidget(0,9)
        self.table.setHorizontalHeaderLabels(("Encoder","Position","Δ primary","Velocity","Homed","Absolute","Ready","Error","Trend"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.table)
        note=QtWidgets.QLabel("Δ is a direct numerical difference; compare only encoders with matching units and coordinate references. Primary is read-only.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)

    def set_axis_state(self,count,primary):
        try:
            n=int(count)
            if float(count)!=n or not 1<=n<=8: n=0
        except (TypeError,ValueError,OverflowError): n=0
        try:
            i=int(primary)
            self.primary=i if float(primary)==i and 1<=i<=n else None
        except (TypeError,ValueError,OverflowError): self.primary=None
        if n==self.count: return
        self.count=n
        self._disconnect()
        self.table.setRowCount(n)
        for row in range(n):
            for col in range(8):
                self.table.setItem(row,col,QtWidgets.QTableWidgetItem("—"))
            button=QtWidgets.QPushButton("Trend")
            button.setEnabled(self.on_plot is not None)
            button.clicked.connect(lambda checked=False,i=row+1:self._trend(i))
            self.table.setCellWidget(row,8,button)
        if self.isVisible(): self._connect()
        self._tick()

    def _name(self,i,field):
        return f"{self.base}-Enc0{i}-{field}"

    def _trend(self,i):
        if self.on_plot:
            names=[self._name(i,f) for f in ("PosAct","VelAct")]
            self.on_plot(names,demo_value=self._demo_value if self.demo else None)

    def _demo_value(self,name):
        for i in range(1,self.count+1):
            for field in FIELDS:
                if name==self._name(i,field):
                    return {"PosAct":10+(i-1)*.001,"VelAct":0,"Homed":1,"Absolute":int(i>1),"Ready":1,"ErrId":0}[field]
        return None

    def _connect(self):
        if self.demo or self.pvs: return
        try:
            import epics
            for i in range(1,self.count+1):
                for field in FIELDS:
                    name=self._name(i,field)
                    self.pvs[name]=epics.PV(name,auto_monitor=True)
        except Exception: pass

    def _disconnect(self):
        for pv in self.pvs.values():
            try: pv.disconnect()
            except Exception: pass
        self.pvs.clear()

    def _read(self,i,field):
        name=self._name(i,field)
        if self.demo: return self._demo_value(name)
        try:
            pv=self.pvs.get(name)
            if pv is not None and pv.connected and pv.severity!=3:
                return pv.get(use_monitor=True,timeout=.001)
        except Exception: pass
        return None

    def _tick(self):
        if not self.isVisible(): return
        samples={i:{f:self._read(i,f) for f in FIELDS} for i in range(1,self.count+1)}
        reference=samples.get(self.primary,{}).get("PosAct")
        for i,v in samples.items():
            values=[str(i)+(" ★" if i==self.primary else ""),v["PosAct"],
                    encoder_delta(v["PosAct"],reference),v["VelAct"]]
            values += ["Yes" if v[f]==1 else "No" if v[f]==0 else "—" for f in ("Homed","Absolute","Ready")]
            error=v["ErrId"]
            try: values.append(f"0x{int(error):X}" if error is not None else "—")
            except (ValueError,TypeError,OverflowError): values.append("—")
            for col,value in enumerate(values):
                item=self.table.item(i-1,col)
                item.setText(format_axis_value(value))
                font=item.font()
                font.setBold(i==self.primary)
                item.setFont(font)

    def showEvent(self,event):
        super().showEvent(event)
        self._connect()
        self.timer.start()
        self._tick()

    def hideEvent(self,event):
        self.timer.stop()
        self._disconnect()
        super().hideEvent(event)
