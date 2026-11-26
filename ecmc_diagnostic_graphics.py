"""Live status graphics. No inferred sequence transitions or combined WCET."""
import math
from qt_compat import QtCore, QtWidgets

PHASES=("IDLE","SLV_ACT","PREP_MST","WAIT_EXT","MST_MOV","WAIT_AT","WAIT_DIS","TIMEOUT")
def utilization(duration_ns, frequency_hz):
    try:
        d,f=float(duration_ns),float(frequency_hz)
        if math.isfinite(d) and math.isfinite(f) and d>=0 and f>0:
            return d*f/1e7
    except (TypeError,ValueError,OverflowError): pass
    return None

class DiagnosticGraphic(QtWidgets.QGroupBox):
    def __init__(self,kind):
        super().__init__("Current phase" if kind=="state_machine" else "Timing relative to average cycle")
        self.kind=kind
        grid=QtWidgets.QGridLayout(self)
        self.items={}
        if kind=="state_machine":
            for i,name in enumerate(PHASES):
                label=QtWidgets.QLabel(name)
                label.setAlignment(QtCore.Qt.AlignCenter)
                grid.addWidget(label,i//4,i%4)
                self.items[name]=label
            self.note=QtWidgets.QLabel("Phase indicators only; transitions depend on configuration.")
            grid.addWidget(self.note,2,0,1,4)
        else:
            for i,name in enumerate(("Execution maximum","Send maximum")):
                grid.addWidget(QtWidgets.QLabel(name),i,0)
                bar=QtWidgets.QProgressBar()
                bar.setRange(0,1000)
                grid.addWidget(bar,i,1)
                self.items[name]=bar
            self.note=QtWidgets.QLabel("Separate observed maxima; not additive or a WCET guarantee.")
            self.note.setWordWrap(True)
            grid.addWidget(self.note,2,0,1,2)

    def set_values(self,values):
        if self.kind=="state_machine":
            phase=values.get("StatTxt")
            for name,w in self.items.items():
                active=phase==name
                w.setStyleSheet("padding: 5px; border: 1px solid #89939e;"+("background: #c43c3c; color: white;" if active and name=="TIMEOUT" else "background: #287bc0; color: white;" if active else ""))
            self.note.setText("Previous: "+str(values.get("StatPrvStateTxt") or "—")+"  ·  Reason: "+str(values.get("StatTrRsnTxt") or "—"))
        else:
            for name,key in (("Execution maximum","ThdExeMax"),("Send maximum","ThdSndMax")):
                value=utilization(values.get(key),values.get("ThdFrqAvg"))
                bar=self.items[name]
                bar.setValue(0 if value is None else min(1000,round(value*10)))
                bar.setFormat("Unavailable" if value is None else f"{value:.1f}%")
