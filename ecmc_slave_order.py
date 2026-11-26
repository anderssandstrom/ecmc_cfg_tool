"""Physical-position order view; does not assume actual cable connectivity."""
from qt_compat import QtCore,QtWidgets
class SlaveOrderPanel(QtWidgets.QWidget):
    def __init__(self,slaves,health,on_open):
        super().__init__()
        self.slaves=sorted(slaves,key=lambda x:(int(x.get("master",0)),int(x["id"])))
        self.health=health
        self.buttons=[]
        layout=QtWidgets.QVBoxLayout(self)
        label=QtWidgets.QLabel("EtherCAT slave order · position order, not verified physical port topology")
        label.setWordWrap(True)
        layout.addWidget(label)
        scroll=QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        content=QtWidgets.QWidget()
        grid=QtWidgets.QGridLayout(content)
        for i,obj in enumerate(self.slaves):
            button=QtWidgets.QPushButton()
            button.setToolTip(str(obj.get("pv_base","")))
            button.clicked.connect(lambda checked=False,obj=obj:on_open(obj))
            grid.addWidget(button,i//3,i%3)
            self.buttons.append(button)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
    def _tick(self):
        for obj,button in zip(self.slaves,self.buttons):
            state=self.health(obj) or "Unknown"
            button.setText(f"s{int(obj['id']):03d} {obj.get('name','')}\n{state}")
            color="#26804b" if state=="OP" else "#b91c1c" if state in ("Offline","Not OP") else "#64748b"
            button.setStyleSheet(f"border: 1px solid {color}; padding: 6px;")
    def showEvent(self,event):
        super().showEvent(event)
        self._tick()
        self.timer.start()
    def hideEvent(self,event):
        self.timer.stop()
        super().hideEvent(event)
