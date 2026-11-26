"""Native CiA 402 status display; actions remain owned by hardware panel."""
from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_hardware_widgets import SignalValue, WordValue
from ecmc_ds402 import decode_state, word_number, bit_label, CONTROL_BITS, edit_controlword

class DS402Status(SignalValue):
    def __init__(self):
        super().__init__("Unavailable", lambda: False)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setMinimumHeight(28)

    def set_status(self, value):
        state = decode_state(value)
        color = {"Operation enabled": "#26804b", "Fault": "#c43c3c",
                 "Fault reaction active": "#c43c3c", "Quick stop active": "#b7791f"}.get(state, "#777")
        self.setText(state)
        self.setStyleSheet("font-weight: bold; color: " + color + "; border: 1px solid " + color + "; border-radius: 4px; padding: 3px;")


class DS402Word(WordValue):
    def __init__(self, callback, status=False):
        super().__init__(callback)
        self.status_word = status

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
        self.setMinimumHeight(150)
        self.setMinimumWidth(240)

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
        width = (self.width()-12)/2
        for i,(state, label) in enumerate(states):
            rect = QtCore.QRectF(3+(i%2)*(width+6), 3+(i//2)*36, width, 30)
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
