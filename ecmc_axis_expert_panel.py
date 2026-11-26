"""Visibility-scoped, read-only axis expert diagnostics."""
import math
import time
from qt_compat import QtCore, QtWidgets
from ecmc_axis_diagnostics import axis_signals

class AxisExpertPanel(QtWidgets.QWidget):
    def __init__(self, base, axis_id, demo=False, on_plot=None, actions=(), interface="native"):
        super().__init__()
        self.base,self.demo,self.on_plot = base,demo,on_plot
        self._closed = False
        self._pvs = {}
        self._labels = {}
        self._started = time.monotonic()
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel(f"Axis {axis_id} — {base}")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.interface = QtWidgets.QComboBox()
        self.interface.addItem("ECMC native interface","native")
        self.interface.addItem("EPICS motor record","motor")
        self.interface.setCurrentIndex(1 if interface == "motor" else 0)
        layout.addWidget(self.interface)
        self.status = QtWidgets.QLabel("Paused")
        layout.addWidget(self.status)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll,1)
        buttons = QtWidgets.QHBoxLayout()
        for caption,action in actions:
            b = QtWidgets.QPushButton(caption)
            b.clicked.connect(lambda checked=False, action=action: action())
            buttons.addWidget(b)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._tick)
        self.interface.currentIndexChanged.connect(self._rebuild)
        self._rebuild()

    def _rebuild(self,*_):
        self._stop()
        self._labels = {}
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        for group,rows in axis_signals(self.base,self.interface.currentData()):
            box = QtWidgets.QGroupBox(group)
            grid = QtWidgets.QGridLayout(box)
            grid.setSpacing(3)
            for row,(label,pv) in enumerate(rows):
                name = QtWidgets.QLabel(label)
                value = QtWidgets.QLabel("—")
                value.setWordWrap(True)
                value.setToolTip(pv)
                grid.addWidget(name,row,0)
                grid.addWidget(value,row,1)
                value.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                value.customContextMenuRequested.connect(lambda pos,v=value,pv=pv: self._menu(v,pos,pv))
                self._labels[pv] = value
            grid.setColumnStretch(1,1)
            layout.addWidget(box)
        layout.addStretch()
        self.scroll.setWidget(root)
        self._start()

    def _menu(self,widget,pos,pv):
        menu = QtWidgets.QMenu(self)
        if self.on_plot:
            action = menu.addAction("Trend PV")
            action.triggered.connect(lambda: self.on_plot([pv],demo_value=self._demo_value if self.demo else None))
        action = menu.addAction("Copy PV name")
        action.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(pv))
        if hasattr(menu,"exec"):
            menu.exec(widget.mapToGlobal(pos))
        else:
            menu.exec_(widget.mapToGlobal(pos))

    def _demo_value(self,pv):
        suffix = pv[len(self.base):]
        t = time.monotonic()-self._started
        actual = 10+0.01*math.sin(t)
        values = {"-PosAct":actual,"-PosSet":10,"-TgtPosCmd":10,"-PosErr":10-actual,
                  "-VelAct":0,"-TgtVelCmd":1,"-AccCmd":2,"-DecCmd":2,
                  "-TgtPosCmd.EGU":"mm","-EnaCmd-RB":1,"-EnaAct":1,"-Homed":1,
                  "-AtTarget":1,"-InRT":1,"-EncCnt":1,"-SetEncIdCmd-RB":1,
                  "-MsgTxt":"","-IlockTxt":"","-Ctrl-Kp":1,"-Ctrl-Ki":0,"-Ctrl-Kd":0,
                  ".RBV":actual+5,".VAL":15,".VELO":1,".EGU":"mm",".DMOV":1,
                  ".CNEN":1,".MSTA":0x22,".HLM":50,".LLM":-50,".TWV":0.1}
        return values.get(suffix,0)

    def _start(self):
        if self._closed or not self.isVisible() or self.timer.isActive():
            return
        if not self.demo:
            try:
                import epics
                for pv in self._labels:
                    try:
                        self._pvs[pv] = epics.PV(pv,auto_monitor=True)
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
        for label in self._labels.values():
            label.setText("—")
        self.status.setText("Paused")

    def _tick(self):
        if not self.isVisible():
            self._stop()
            return
        count = 0
        for name,label in self._labels.items():
            value = None
            if self.demo:
                value = self._demo_value(name)
            else:
                pv = self._pvs.get(name)
                try:
                    if pv is not None and pv.connected and pv.severity != 3:
                        value = pv.get(use_monitor=True,timeout=0.001)
                except Exception:
                    pass
            count += value is not None
            text = "—" if value is None else f"{value:.12g}" if isinstance(value,float) else str(value)
            if label.text() != text:
                label.setText(text)
        self.status.setText(f"{'DEMO — ' if self.demo else ''}{count}/{len(self._labels)} signals available")

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
