"""Live homing indications, without inferring undocumented sequence phases."""
from qt_compat import QtCore, QtWidgets

class HomingIndicators(QtWidgets.QGroupBox):
    def __init__(self):
        super().__init__("Live homing signals")
        layout=QtWidgets.QGridLayout(self)
        self.items={}
        for i,(key,title) in enumerate((("back","Backward limit"),("home","Home switch"),("forward","Forward limit"),
                                       ("active","Home executing"),("homed","Homed"),("error","Error"))):
            w=QtWidgets.QLabel(title)
            w.setAlignment(QtCore.Qt.AlignCenter)
            layout.addWidget(w,i//3,i%3)
            self.items[key]=(title,w)
        self.state=QtWidgets.QLabel("Sequence state: —")
        layout.addWidget(self.state,2,0,1,3)
        self.update_samples({})

    def update_samples(self,s):
        def bit(key,invert=False):
            v=s.get(key)
            return (v==0 if invert else v==1) if v in (0,1) else None
        cmd,busy=s.get("-MtnCmd"),s.get("-Busy")
        active=(cmd==10 and busy==1) if cmd is not None and busy in (0,1) else None
        error=s.get("-ErrId")
        values={"back":bit("-LimBwd",True),"home":bit("-HomeSwitch"),"forward":bit("-LimFwd",True),
                "active":active,"homed":bit("-Homed"),"error":error!=0 if error is not None else None}
        for key,(title,w) in self.items.items():
            v=values[key]
            color=("#c43c3c" if key=="error" else "#b7791f" if key in ("back","forward","home") else "#26804b") if v else ""
            w.setText(title+(" · ON" if v is True else " · OFF" if v is False else " · —"))
            w.setStyleSheet("padding: 5px; border: 1px solid #89939e;"+(f"background: {color}; color: white;" if color else ""))
        seq=s.get("-SeqState")
        self.state.setText("Sequence state: "+("—" if seq is None else str(seq)))
