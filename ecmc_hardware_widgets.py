"""Reusable native graphical summaries for standard ECMC hardware functions."""
from qt_compat import QtCore, QtGui, QtWidgets


class HardwareSymbol(QtWidgets.QWidget):
    """Schematic only: no inferred shaft angle, speed or encoder scaling."""
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setFixedSize(58, 48)
        self.setToolTip("Drive schematic" if kind == "Drv" else "Encoder schematic")

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(self.palette().color(QtGui.QPalette.Text), 1.5)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        if self.kind == "Drv":
            p.drawRoundedRect(QtCore.QRectF(3, 7, 22, 33), 3, 3)
            p.drawText(QtCore.QRectF(3, 7, 22, 33), QtCore.Qt.AlignCenter, "D")
            p.drawLine(25, 24, 34, 24)
            p.drawEllipse(QtCore.QRectF(34, 13, 21, 21))
            p.drawText(QtCore.QRectF(34, 13, 21, 21), QtCore.Qt.AlignCenter, "M")
        else:
            p.drawEllipse(QtCore.QRectF(3, 7, 34, 34))
            p.drawEllipse(QtCore.QRectF(16, 20, 8, 8))
            for x1, y1, x2, y2 in [(20,9,20,15),(20,33,20,39),(5,24,11,24),(29,24,35,24)]:
                p.drawLine(x1,y1,x2,y2)
            p.drawRect(QtCore.QRectF(39, 16, 7, 16))
            p.drawLine(46, 24, 54, 24)
        p.end()


class SignalValue(QtWidgets.QLabel):
    """A value with optional left-click edit; right-click remains independent."""
    def __init__(self, text, callback, parent=None):
        super().__init__(text, parent)
        self._callback = callback

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._callback():
            event.accept()
            return
        super().mouseReleaseEvent(event)


def signal_role(kind, suffix):
    """Primary measurements, compact status indicators, then remaining signals."""
    if kind == "Enc" and suffix in ("PosAct", "PosAct02"):
        return "primary"
    if kind == "Drv" and suffix in ("Spd-RB", "PosCmd-RB", "CurAct", "TrqAct", "DS402State"):
        return "primary"
    if suffix.endswith("Alrm") or suffix in ("Rdy", "RdyStat", "RdyToEna", "ExtLtchOK", "CLtchOK"):
        return "status"
    return "detail"
