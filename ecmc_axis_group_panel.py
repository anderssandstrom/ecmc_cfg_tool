"""Read-only native axis group overview, with explicit member navigation."""
import re
from qt_compat import QtCore, QtWidgets

def group_members(raw,axes):
    tokens=[t for t in re.split(r"[,;\s]+",str(raw).strip()) if t]
    lookup={str(a.get("id")):a for a in axes}
    result=[]
    for token in dict.fromkeys(tokens):
        match=lookup.get(token)
        if match is None:
            matches=[a for a in axes if token in (a.get("name"),a.get("motor"))]
            match=matches[0] if len(matches)==1 else None
        result.append((token,match))
    return result

class AxisGroupPanel(QtWidgets.QWidget):
    def __init__(self,group,axes,on_axis,demo=False):
        super().__init__()
        self.demo=demo
        self.pvs={}
        self.rows=[]
        layout=QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(group.get("name","Axis group")))
        grid=QtWidgets.QGridLayout()
        for col,title in enumerate(("Axis","Actual","Target","Enabled","Moving","Error","")):
            grid.addWidget(QtWidgets.QLabel(title),0,col)
        for row,(token,axis) in enumerate(group_members(group.get("axes",""),axes),1):
            grid.addWidget(QtWidgets.QLabel(axis.get("name",token) if axis else token),row,0)
            if not axis or not axis.get("motor"):
                grid.addWidget(QtWidgets.QLabel("Unresolved member"),row,1,1,5)
                continue
            labels=[]
            for col in range(1,6):
                label=QtWidgets.QLabel("—")
                grid.addWidget(label,row,col)
                labels.append(label)
            button=QtWidgets.QPushButton("Open")
            button.clicked.connect(lambda checked=False,axis=axis:on_axis(axis))
            grid.addWidget(button,row,6)
            self.rows.append((axis["motor"],labels))
        layout.addLayout(grid)
        layout.addStretch()
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)

    def showEvent(self,event):
        super().showEvent(event)
        if not self.demo:
            try:
                import epics
                for base,_ in self.rows:
                    for suffix in ("-PosAct","-TgtPosCmd","-EnaAct","-Moving","-ErrId"):
                        name=base+suffix
                        self.pvs[name]=epics.PV(name,auto_monitor=True)
            except Exception:
                pass
        self.timer.start()
        self._tick()

    def _tick(self):
        for base,labels in self.rows:
            for i,(suffix,label) in enumerate(zip(("-PosAct","-TgtPosCmd","-EnaAct","-Moving","-ErrId"),labels)):
                value=(10,10,1,0,0)[i] if self.demo else None
                if not self.demo:
                    try:
                        pv=self.pvs.get(base+suffix)
                        if pv is not None and pv.connected and pv.severity!=3:
                            value=pv.get(use_monitor=True,timeout=.001)
                    except Exception:
                        pass
                if value is None:
                    text,color="—","#89939e"
                elif i<2:
                    text,color=str(value),""
                elif i==4:
                    text,color=f"0x{int(value):X}","#c43c3c" if value else "#26804b"
                else:
                    text,color=("● On","#26804b") if value else ("○ Off","#89939e")
                label.setText(text)
                label.setStyleSheet(f"color: {color};" if color else "")

    def hideEvent(self,event):
        self.timer.stop()
        for pv in self.pvs.values():
            try: pv.disconnect()
            except Exception: pass
        self.pvs.clear()
        super().hideEvent(event)
