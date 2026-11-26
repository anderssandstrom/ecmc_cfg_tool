"""Native CiA 402 status display; actions remain owned by hardware panel."""
from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_hardware_widgets import SignalValue, WordValue
from ecmc_ds402 import decode_state, word_number, bit_label, CONTROL_BITS, edit_controlword

class DS402Status(SignalValue):
    def __init__(self):
        super().__init__("Unavailable", lambda: False)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumHeight(22)

    def set_status(self, value):
        state = decode_state(value)
        color = {"Operation enabled": "#26804b", "Fault": "#c43c3c",
                 "Fault reaction active": "#c43c3c", "Quick stop active": "#b7791f"}.get(state, "#777")
        self.setText(state)
        self.setStyleSheet("font-weight: bold; color: " + color + "; border: 1px solid " + color + "; border-radius: 4px; padding: 1px;")


class DS402Word(WordValue):
    def __init__(self, callback, status=False, bit_callback=None):
        super().__init__(callback, bit_callback=bit_callback, allowed_bits=CONTROL_BITS if not status else ())
        self.bit_hit_y = (13, 28)
        self.status_word = status
        self.setMinimumHeight(32)
        self.setMinimumWidth(190)
        self.setMaximumHeight(34)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(self.palette().color(QtGui.QPalette.Text))
        if self._editable:
            p.drawRect(self.rect().adjusted(0, 0, -1, -1))
        font = p.font()
        font.setPointSize(max(7, font.pointSize()-3))
        p.setFont(font)
        cell = self.width_px()/16
        for i in range(16):
            bit = 15-i
            x = i*cell
            p.setPen(self.palette().color(QtGui.QPalette.Text))
            p.drawText(QtCore.QRectF(x, 1, cell, 12), QtCore.Qt.AlignCenter, str(bit))
            color = "#87919b" if self.word is None else "#287bc0" if self.word & (1 << bit) else "#c2c8cd"
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(color))
            p.drawRoundedRect(QtCore.QRectF(x+1, 15, max(2,cell-2), 11), 2, 2)
        p.end()

    def event(self, event):
        if event.type() == QtCore.QEvent.ToolTip and self.width_px() > 0:
            bit = 15 - min(15, max(0, int(event.pos().x() * 16 / self.width_px())))
            QtWidgets.QToolTip.showText(event.globalPos(), f"Bit {bit}: {bit_label(bit, self.status_word)}", self)
            return True
        return super().event(event)

class DS402StateMap(SignalValue):
    """Read-only state overview. No state transition is issued by clicking."""
    def __init__(self):
        super().__init__("", lambda: False)
        self.state = "Unavailable"
        self.setMinimumHeight(84)
        self.setMaximumHeight(84)
        self.setMinimumWidth(210)

    def set_status(self, value):
        self.state = decode_state(value)
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        states = [("Not ready to switch on", "Not ready"),
                  ("Switch on disabled", "Switch on disabled"),
                  ("Ready to switch on", "Ready to switch on"),
                  ("Switched on", "Switched on"),
                  ("Operation enabled", "Operation enabled"),
                  ("Quick stop active", "Quick stop active"),
                  ("Fault reaction active", "Fault reaction"),
                  ("Fault", "Fault")]
        font = p.font()
        font.setPointSize(max(8, font.pointSize()-2))
        p.setFont(font)
        width = (self.width()-12)/2
        for i,(state, label) in enumerate(states):
            rect = QtCore.QRectF(3+(i%2)*(width+6), 2+(i//2)*20, width, 18)
            active = state == self.state
            color = "#c43c3c" if state.startswith("Fault") else "#b7791f" if state == "Quick stop active" else "#26804b" if state == "Operation enabled" else "#287bc0"
            p.setPen(QtGui.QPen(QtGui.QColor(color if active else "#89929d"), 2 if active else 1))
            p.setBrush(QtGui.QColor(color) if active else QtCore.Qt.NoBrush)
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(QtGui.QColor("white") if active else self.palette().color(QtGui.QPalette.Text))
            p.drawText(rect, QtCore.Qt.AlignCenter, label)
        p.end()

def controlword_dialog(parent, original):
    original = word_number(original)
    if original is None:
        return None
    dialog = QtWidgets.QDialog(parent)
    dialog.setWindowTitle("Edit CiA 402 controlword")
    layout = QtWidgets.QVBoxLayout(dialog)
    layout.addWidget(QtWidgets.QLabel("Changing these bits may enable motion. Unlisted bits are preserved."))
    checks = {}
    for bit, label in CONTROL_BITS.items():
        check = QtWidgets.QCheckBox(f"Bit {bit}: {label}")
        check.setChecked(bool(original & (1 << bit)))
        checks[bit] = check
        layout.addWidget(check)
    preview = QtWidgets.QLabel()
    layout.addWidget(preview)
    def selected():
        return edit_controlword(original, {bit: check.isChecked() for bit,check in checks.items()})
    def refresh(*_):
        preview.setText(f"0x{original:04X} → 0x{selected():04X}")
    for check in checks.values():
        check.toggled.connect(refresh)
    refresh()
    buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Apply")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    accepted = dialog.exec() if hasattr(dialog, "exec") else dialog.exec_()
    return selected() if accepted else None
