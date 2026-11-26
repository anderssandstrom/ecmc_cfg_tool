"""Read-only compact axis overview, independent of command execution."""
import math
from qt_compat import QtCore, QtGui, QtWidgets

def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError,OverflowError):
        return None

class AxisPositionBar(QtWidgets.QWidget):
    def __init__(self,native):
        super().__init__()
        self.native = native
        self.values = {}
        self.setMinimumHeight(65)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding,QtWidgets.QSizePolicy.Fixed)
        self.setToolTip("Read-only position overview; markers cannot command motion.")

    def set_values(self,values):
        self.values = values
        self.update()

    def paintEvent(self,event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        v = self.values
        native = self.native
        keys = ("-PosAct","-TgtPosCmd","-CfgDLLM","-CfgDHLM") if native else (".RBV",".VAL",".LLM",".HLM")
        actual,target,low,high = (number(v.get(k)) for k in keys)
        le = number(v.get("-SftLimBwdEna-RB")) if native else 1
        he = number(v.get("-SftLimFwdEna-RB")) if native else 1
        fg = self.palette().color(QtGui.QPalette.Text)
        p.setPen(fg)
        if low is None or high is None or high <= low:
            p.drawText(self.rect(),QtCore.Qt.AlignCenter,"Position range unavailable")
            p.end()
            return
        left,right = 12.0,max(13.0,self.width()-12.0)
        y = 29.0
        def x(value):
            return left+(right-left)*max(0.0,min(1.0,(value-low)/(high-low)))
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("#a0a9b2"))
        p.drawRoundedRect(QtCore.QRectF(left,y-4,right-left,8),4,4)
        for xpos,enabled in ((left,le),(right,he)):
            p.setPen(QtGui.QPen(QtGui.QColor("#b7791f" if enabled==1 else "#89939e"),2))
            p.drawLine(QtCore.QPointF(xpos,y-9),QtCore.QPointF(xpos,y+9))
        if actual is not None:
            pos=x(actual)
            p.setPen(QtGui.QPen(QtGui.QColor("#c43c3c" if actual<low or actual>high else "#287bc0"),3))
            p.drawLine(QtCore.QPointF(pos,y-13),QtCore.QPointF(pos,y+6))
        if target is not None:
            pos=x(target)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor("#26804b"))
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(pos,y+5),QtCore.QPointF(pos-5,y+14),QtCore.QPointF(pos+5,y+14)]))
        p.setPen(fg)
        p.drawText(QtCore.QRectF(left,44,(right-left)/2,18),QtCore.Qt.AlignLeft,f"{low:.6g}")
        p.drawText(QtCore.QRectF((left+right)/2,44,(right-left)/2,18),QtCore.Qt.AlignRight,f"{high:.6g}")
        legend = "Blue: actual   Green: target"
        if native and (le != 1 or he != 1):
            legend += "   • Limit disabled/unknown"
        p.drawText(QtCore.QRectF(0,0,self.width(),17),QtCore.Qt.AlignCenter,legend)
        p.end()

class AxisStatusIcon(QtWidgets.QLabel):
    """Semantic read-only badge with explicit unavailable state."""
    def __init__(self,caption,glyph,key,active_bad=False):
        super().__init__()
        self.caption,self.glyph,self.key,self.active_bad = caption,glyph,key,active_bad
        self.setToolTip(caption)
        self.set_values({})

    def set_values(self,values):
        raw=number(values.get(self.key))
        active=None if raw is None else bool(raw)
        color="#89939e" if active is None else "#c43c3c" if active and self.active_bad else "#26804b" if active else "#89939e"
        text=f"{self.glyph} {self.caption}"
        if active is None:
            text+=" ?"
        self.setText(text)
        self.setStyleSheet(f"color: {color};")
        self.setToolTip(f"{self.caption}: {'unavailable' if active is None else 'active' if active else 'inactive'}")
