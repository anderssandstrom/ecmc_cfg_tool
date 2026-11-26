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
        self._editable = False

    def set_editable(self, enabled):
        self._editable = enabled
        self.setFrameShape(QtWidgets.QFrame.Box if enabled else QtWidgets.QFrame.NoFrame)
        self.setFrameShadow(QtWidgets.QFrame.Plain)
        self.update()

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
    def __init__(self, callback, width=16, bit_callback=None, allowed_bits=None):
        super().__init__("—", callback)
        self.bit_width = width
        self.bit_callback = bit_callback
        self.allowed_bits = set(range(width)) if allowed_bits is None else set(allowed_bits)
        self.bit_hit_y = (30, 46)
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
        if self._editable:
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        p.drawText(QtCore.QRectF(0, 0, self.width_px()-3, 18), QtCore.Qt.AlignRight, text)
        font = p.font()
        font.setPointSize(max(7, font.pointSize()-3))
        p.setFont(font)
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

    def mouseReleaseEvent(self, event):
        if (event.button() == QtCore.Qt.LeftButton and self.bit_callback is not None
                and self.bit_hit_y[0] <= event.pos().y() <= self.bit_hit_y[1]):
            if self._editable and self.word is not None and self.width_px() > 0:
                bit = self.bit_width-1-min(self.bit_width-1, max(0, int(event.pos().x()*self.bit_width/self.width_px())))
                if bit in self.allowed_bits:
                    self.bit_callback(self.word, bit)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def width_px(self):
        return self.rect().width()

def boolean_value(value):
    return {"1": 1, "0": 0, "1.0": 1, "0.0": 0, "true": 1, "false": 0,
            "yes": 1, "no": 0}.get(str(value).strip().lower())

class BooleanValue(SignalValue):
    """Monitor-driven switch: never changes its displayed state optimistically."""
    def __init__(self, callback):
        super().__init__("", lambda: False)
        self.value = None
        self.toggle = callback
        self.setMinimumSize(68, 24)

    def set_value(self, value):
        self.value = boolean_value(value)
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        width = min(62, max(1, self.width()-4))
        x = (self.width()-width)/2
        rect = QtCore.QRectF(x, 3, width, 18)
        color = "#26804b" if self.value == 1 else "#737d87" if self.value == 0 else "#aab0b5"
        p.setPen(QtGui.QPen(QtGui.QColor("#287bc0" if self._editable else color), 1.5))
        p.setBrush(QtGui.QColor(color))
        p.drawRoundedRect(rect, 9, 9)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor("white"))
        if self.value is not None:
            p.drawEllipse(QtCore.QRectF(x+width-17 if self.value else x+3, 6, 12, 12))
        p.setPen(QtGui.QColor("white"))
        p.drawText(QtCore.QRectF(x+3 if self.value else x+17, 3, max(1,width-20), 18),
                   QtCore.Qt.AlignCenter, "ON" if self.value == 1 else "OFF" if self.value == 0 else "—")
        p.end()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._editable and self.value is not None:
            self.toggle(self.value, 1-self.value)
            event.accept()
            return
        super().mouseReleaseEvent(event)

class AnalogValue(SignalValue):
    """Raw numerical value with a small autoscaled history; no assumed engineering range."""
    def __init__(self, callback):
        super().__init__("—", callback)
        self.history = []
        self.value = None
        self.setMinimumSize(130, 38)

    def set_value(self, value):
        import math
        try:
            n = float(value)
            if not math.isfinite(n):
                raise ValueError()
            self.value = n
            self.history.append(n)
            self.history = self.history[-60:]
        except (TypeError, ValueError, OverflowError):
            self.value = None
            self.history.clear()
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(self.palette().color(QtGui.QPalette.Text))
        p.drawText(QtCore.QRectF(2, 0, self.width()-4, 18), QtCore.Qt.AlignRight,
                   "—" if self.value is None else f"{self.value:.9g}")
        if self._editable:
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if len(self.history) > 1:
            lo, hi = min(self.history), max(self.history)
            span = hi-lo
            points = [QtCore.QPointF(3+i*(self.width()-6)/max(1,len(self.history)-1),
                        29 if span == 0 else 35-(v-lo)*15/span) for i,v in enumerate(self.history)]
            p.setPen(QtGui.QPen(QtGui.QColor("#287bc0"), 1.3))
            p.drawPolyline(QtGui.QPolygonF(points))
        p.end()
