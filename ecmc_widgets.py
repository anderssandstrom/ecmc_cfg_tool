"""Common Qt input widgets."""
from qt_compat import QtCore, QtWidgets
from ecmc_formatting import compact_float_text

class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def textFromValue(self, value):
        return compact_float_text(value)


class SpinBoxWithButtons(QtWidgets.QWidget):
    def __init__(self, float_mode=False):
        super().__init__()
        self.float_mode = bool(float_mode)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if self.float_mode:
            self.spin = CompactDoubleSpinBox()
            self.spin.setDecimals(4)
            self.spin.setSingleStep(0.1)
        else:
            self.spin = QtWidgets.QSpinBox()
            self.spin.setSingleStep(1)

        self.spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin.setFixedSize(100, 24)
        self.spin.setKeyboardTracking(False)
        self.spin.setFocusPolicy(QtCore.Qt.StrongFocus)

        btn_col = QtWidgets.QWidget()
        btn_col_l = QtWidgets.QVBoxLayout(btn_col)
        btn_col_l.setContentsMargins(2, 0, 0, 0)
        btn_col_l.setSpacing(0)
        self.up_btn = QtWidgets.QPushButton('^')
        self.down_btn = QtWidgets.QPushButton('v')
        self.up_btn.setFixedSize(20, 12)
        self.down_btn.setFixedSize(20, 12)
        self.up_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self.down_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self.up_btn.setCursor(QtCore.Qt.ArrowCursor)
        self.down_btn.setCursor(QtCore.Qt.ArrowCursor)
        arrow_btn_style = (
            'QPushButton {'
            ' background: #6a6a6a;'
            ' color: #ffffff;'
            ' border: 1px solid #4c4c4c;'
            ' border-radius: 2px;'
            ' padding: 0px;'
            ' font-size: 10px;'
            ' font-weight: 700;'
            '}'
            'QPushButton:pressed { background: #4f4f4f; }'
        )
        self.up_btn.setStyleSheet(arrow_btn_style)
        self.down_btn.setStyleSheet(arrow_btn_style)
        self.up_btn.clicked.connect(self.spin.stepUp)
        self.down_btn.clicked.connect(self.spin.stepDown)
        btn_col_l.addWidget(self.up_btn)
        btn_col_l.addWidget(self.down_btn)

        layout.addWidget(self.spin)
        layout.addWidget(btn_col)
        self.setFixedSize(120, 24)

    def setRange(self, low, high):
        self.spin.setRange(low, high)

    def setSingleStep(self, step):
        self.spin.setSingleStep(step)

    def setDecimals(self, dec):
        if isinstance(self.spin, QtWidgets.QDoubleSpinBox):
            self.spin.setDecimals(dec)

    def setHexMode(self):
        if isinstance(self.spin, QtWidgets.QSpinBox):
            self.spin.setDisplayIntegerBase(16)
            self.spin.setPrefix('0x')

    def value(self):
        return self.spin.value()

    def setValue(self, value):
        self.spin.setValue(value)

    def on_value_changed(self, cb):
        self.spin.valueChanged.connect(cb)



def reset_combo(combo):
    previous = combo.blockSignals(True)
    try:
        combo.setCurrentIndex(0)
    finally:
        combo.blockSignals(previous)
