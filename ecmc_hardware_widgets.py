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

class WordValue(SignalValue):
    """Raw word visualization; no assumed bit semantics or writable bit toggles."""
    def __init__(self, callback, width=16):
        super().__init__("—", callback)
        self.bit_width = width
        self.word = None
        self.setMinimumHeight(48)
        self.setMinimumWidth(240)

    def set_word(self, value):
        try:
            raw = str(value).strip()
            number = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
            if not 0 <= number < (1 << self.bit_width):
                raise ValueError("Outside displayed word range")
            self.word = number
        except (ValueError, TypeError, OverflowError):
            self.word = None
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(self.palette().color(QtGui.QPalette.Text))
        text = "—" if self.word is None else f"0x{self.word:04X}"
        p.drawText(QtCore.QRectF(0, 0, self.width_px(), 18), QtCore.Qt.AlignRight, text)
        cell = self.width_px() / self.bit_width
        for i in range(self.bit_width):
            bit = self.bit_width - 1 - i
            x = i * cell
            p.setPen(self.palette().color(QtGui.QPalette.Text))
            p.drawText(QtCore.QRectF(x, 17, cell, 13), QtCore.Qt.AlignCenter, str(bit))
            color = "#87919b" if self.word is None else "#287bc0" if self.word & (1 << bit) else "#c2c8cd"
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(color))
            p.drawRoundedRect(QtCore.QRectF(x + 2, 32, max(2, cell - 4), 12), 2, 2)
        p.end()

    def width_px(self):
        return self.rect().width()
