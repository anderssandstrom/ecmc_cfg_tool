"""Native CiA 402 status display; actions remain owned by hardware panel."""
from qt_compat import QtCore, QtWidgets
from ecmc_hardware_widgets import SignalValue, WordValue
from ecmc_ds402 import decode_state, word_number

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
    def set_word(self, value):
        super().set_word(value)
