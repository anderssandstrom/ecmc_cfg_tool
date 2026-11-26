"""Modeless, read-only CA trend using the controller tuning plot widget."""
import time
import math
from collections import deque
from threading import Lock
from ecmc_acquisition import bounded_series
from qt_compat import QtCore, QtWidgets
from ecmc_tuning_qt import Trend
from ecmc_native_motion import numeric_readback


class HardwareTrendWindow(QtWidgets.QDialog):
    def __init__(self, pvs, parent=None, demo_value=None):
        super().__init__(parent)
        self.setWindowTitle("PV trend — " + (pvs[0] if len(pvs) == 1 else "Hardware"))
        self._demo_value = demo_value
        if demo_value is not None:
            self.setWindowTitle(self.windowTitle() + ' [DEMO]')
        self.setModal(False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.resize(760, 340)
        self._subscriptions = []
        self._closed = False
        self._latest = {}
        self._lock = Lock()
        self._pending = deque(maxlen=200000)
        self._dropped = 0
        self._counts = {}
        self._rate_at = time.monotonic()
        self._rate = {}
        self._pvs = list(dict.fromkeys(pvs))
        layout = QtWidgets.QVBoxLayout(self)
        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(QtWidgets.QLabel("History [s]"))
        history = QtWidgets.QSpinBox()
        history.setRange(1, 120)
        history.setValue(15)
        controls.addWidget(history)
        freeze = QtWidgets.QCheckBox("Freeze")
        controls.addWidget(freeze)
        clear = QtWidgets.QPushButton("Clear")
        controls.addWidget(clear)
        controls.addStretch(1)
        layout.addLayout(controls)
        colors = ("#287bc0", "#d06d22", "#a64070", "#26804b")
        self.trend = Trend([(pv, pv.rsplit(":", 1)[-1], colors[i % len(colors)])
                            for i, pv in enumerate(self._pvs)])
        self.trend.samples = bounded_series(self._pvs, 200000)
        self.trend.include_zero = False
        self.trend.axis_precision = 12
        scale_row = QtWidgets.QHBoxLayout()
        scale_row.addWidget(QtWidgets.QLabel("Y-axis"))
        self.scale_mode = QtWidgets.QComboBox()
        self.scale_mode.addItems(["Auto", "Include zero", "Manual"])
        scale_row.addWidget(self.scale_mode)
        self.y_min = QtWidgets.QLineEdit("0")
        self.y_max = QtWidgets.QLineEdit("1")
        for label, edit in (("Min", self.y_min), ("Max", self.y_max)):
            scale_row.addWidget(QtWidgets.QLabel(label))
            edit.setMaximumWidth(130)
            edit.setEnabled(False)
            scale_row.addWidget(edit)
        self.apply_scale = QtWidgets.QPushButton("Apply")
        self.apply_scale.setEnabled(False)
        scale_row.addWidget(self.apply_scale)
        scale_row.addStretch(1)
        layout.addLayout(scale_row)
        self.scale_mode.currentIndexChanged.connect(self._scale_changed)
        self.apply_scale.clicked.connect(self._apply_manual)
        self.y_min.returnPressed.connect(self._apply_manual)
        self.y_max.returnPressed.connect(self._apply_manual)
        layout.addWidget(self.trend, 1)
        self.status = QtWidgets.QLabel("Connecting…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        history.valueChanged.connect(self._history)
        freeze.toggled.connect(self.trend.freeze)
        clear.clicked.connect(self._clear)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        if self._demo_value is not None:
            self.timer.start()
            return
        try:
            import epics
            for name in self._pvs:
                def callback(pvname=None, value=None, severity=None, _key=name, **kw):
                    self._enqueue(_key, numeric_readback(value, severity))
                def connection(pvname=None, conn=None, _key=name, **kw):
                    if not conn:
                        self._enqueue(_key, None)
                pv = epics.PV(name, auto_monitor=True, callback=callback,
                              connection_callback=connection)
                self._subscriptions.append(pv)
        except Exception as ex:
            self.status.setText("CA monitor unavailable: " + str(ex))
            return
        self.timer.start()

    def _scale_changed(self, index):
        manual = index == 2
        self.y_min.setEnabled(manual)
        self.y_max.setEnabled(manual)
        self.apply_scale.setEnabled(manual)
        if manual:
            now, source = self.trend.frozen if self.trend.frozen is not None else (time.monotonic(), self.trend.samples)
            values = [v for q in source.values() for t, v in q
                      if now - self.trend.history <= t <= now and v is not None]
            if values:
                lo, hi = min(values), max(values)
                pad = (hi - lo) * .05 or max(abs(hi) * 1e-6, 1e-9)
                self.y_min.setText(format(lo - pad, ".15g"))
                self.y_max.setText(format(hi + pad, ".15g"))
            self._apply_manual()
        else:
            self.trend.fixed_range = None
            self.trend.include_zero = index == 1
            self.trend.update()

    def _apply_manual(self):
        if self.scale_mode.currentIndex() != 2:
            return
        try:
            lo, hi = float(self.y_min.text()), float(self.y_max.text())
            if not math.isfinite(lo) or not math.isfinite(hi) or not math.isfinite(hi-lo) or lo >= hi:
                raise ValueError()
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Invalid Y-axis range", "Enter finite values with Min < Max.")
            return
        self.trend.fixed_range = (lo, hi)
        self.trend.update()

    def _history(self, value):
        self.trend.history = value
        self.trend.update()

    def _enqueue(self, key, value):
        with self._lock:
            if self._closed:
                return
            if len(self._pending) == self._pending.maxlen:
                self._dropped += 1
            self._pending.append((key, time.monotonic(), value))

    def _clear(self):
        with self._lock:
            self._pending.clear()
            self._dropped = 0
        self.trend.clear()

    def _tick(self):
        if self._demo_value is not None:
            for pv in self._pvs:
                self._enqueue(pv, numeric_readback(self._demo_value(pv)))
        with self._lock:
            events = self._pending
            self._pending = deque(maxlen=200000)
        for key, stamp, value in events:
            self._latest[key] = value
            self._counts[key] = self._counts.get(key, 0) + 1
            self.trend.append(key, stamp, value)
        now = time.monotonic()
        elapsed = now - self._rate_at
        if elapsed >= 1.0:
            self._rate = {key: self._counts.get(key, 0) / elapsed for key in self._pvs}
            self._counts.clear()
            self._rate_at = now
        self.trend.update()
        self.status.setText(" | ".join(
            f"{key}: {self._latest[key]:.9g} ({self._rate.get(key, 0):.0f} events/s)"
            if self._latest.get(key) is not None else f"{key}: unavailable"
            for key in self._pvs) + (f" | Dropped events: {self._dropped}" if self._dropped else ""))

    def closeEvent(self, event):
        with self._lock:
            self._closed = True
            self._pending.clear()
        self.timer.stop()
        for pv in self._subscriptions:
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass
        self._subscriptions.clear()
        super().closeEvent(event)
