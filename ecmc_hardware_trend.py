"""Modeless, read-only CA trend using the controller tuning plot widget."""
import time
import math
from collections import deque
from threading import Lock
from ecmc_acquisition import bounded_series, export_event_csv
from qt_compat import QtCore, QtGui, QtWidgets
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
        self._subscriptions = {}
        self._tokens = {}
        self._providers = {}
        self._colors = {}
        self._visible = {}
        self._axes = {}
        self._color_index = 0
        self._independent = {}
        self._initial_pvs = list(dict.fromkeys(pvs))
        self._closed = False
        self._latest = {}
        self._lock = Lock()
        self._pending = deque(maxlen=200000)
        self._dropped = 0
        self._counts = {}
        self._rate_at = time.monotonic()
        self._rate = {}
        self._pvs = []
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
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
        export = QtWidgets.QPushButton("Export CSV")
        export.clicked.connect(self._export)
        controls.addWidget(export)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.trend = Trend([])
        self.trend.samples = {}
        self.trend.include_zero = False
        self.trend.axis_precision = 12
        self.trend.show_legend = False
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
        self.independent_check = QtWidgets.QCheckBox("Independent axes")
        self.independent_check.toggled.connect(self._rebuild_plots)
        controls.insertWidget(controls.count()-1, self.independent_check)
        self.right_controls = QtWidgets.QWidget()
        right_row = QtWidgets.QHBoxLayout(self.right_controls)
        right_row.setContentsMargins(0, 0, 0, 0)
        right_row.addWidget(QtWidgets.QLabel("Right Y-axis"))
        self.right_mode = QtWidgets.QComboBox()
        self.right_mode.addItems(["Auto", "Include zero", "Manual"])
        right_row.addWidget(self.right_mode)
        self.right_min = QtWidgets.QLineEdit("0")
        self.right_max = QtWidgets.QLineEdit("1")
        for label, edit in (("Min", self.right_min), ("Max", self.right_max)):
            right_row.addWidget(QtWidgets.QLabel(label))
            edit.setMaximumWidth(130)
            edit.setEnabled(False)
            right_row.addWidget(edit)
        self.right_apply = QtWidgets.QPushButton("Apply")
        self.right_apply.setEnabled(False)
        right_row.addWidget(self.right_apply)
        right_row.addStretch(1)
        self.right_mode.currentIndexChanged.connect(self._right_scale)
        self.right_apply.clicked.connect(self._apply_right)
        self.right_min.returnPressed.connect(self._apply_right)
        self.right_max.returnPressed.connect(self._apply_right)
        self.right_controls.hide()
        layout.addWidget(self.right_controls)
        self.plot_area = QtWidgets.QScrollArea()
        self.plot_area.setWidgetResizable(True)
        self.plot_root = QtWidgets.QWidget()
        self.plot_layout = QtWidgets.QVBoxLayout(self.plot_root)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_layout.setSpacing(4)
        self.trend.setMinimumHeight(230)
        self.plot_area.setMinimumHeight(250)
        self.plot_area.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.plot_layout.addWidget(self.trend, 1)
        self.plot_area.setWidget(self.plot_root)
        layout.addWidget(self.plot_area, 1)
        self.curves = QtWidgets.QTableWidget(0, 5)
        self.curves.setHorizontalHeaderLabels(["Show / PV", "Color", "Value", "Events/s", "Y-axis"])
        self.curves.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.curves.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.curves.horizontalHeader().setStretchLastSection(False)
        self.curves.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.curves.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.curves.verticalHeader().setDefaultSectionSize(max(25, self.fontMetrics().height()+9))
        self.curves.itemChanged.connect(self._visibility_changed)
        self.curves.cellDoubleClicked.connect(self._color_clicked)
        layout.addWidget(self.curves)
        manage = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add PV")
        add.clicked.connect(self._add_dialog)
        remove = QtWidgets.QPushButton("Remove selected")
        remove.clicked.connect(self._remove_selected)
        manage.addWidget(add)
        manage.addWidget(remove)
        manage.addStretch(1)
        layout.addLayout(manage)
        self.status = QtWidgets.QLabel("Connecting…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        history.valueChanged.connect(self._history)
        freeze.toggled.connect(self._freeze)
        clear.clicked.connect(self._clear)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        self.resize(900, 660)
        for name in self._initial_pvs:
            self.add_pv(name, demo_value)
        self.timer.start()

    def add_pv(self, name, demo_value=None):
        name = str(name).strip()
        if not name or name in self._pvs or self._closed:
            return False
        if self._demo_value is not None and demo_value is None:
            demo_value = self._demo_value
        if self._demo_value is None and demo_value is not None:
            return False
        token = object()
        with self._lock:
            self._tokens[name] = token
        self._pvs.append(name)
        self.trend.samples[name] = deque(maxlen=200000)
        if self.trend.frozen is not None:
            self.trend.frozen[1][name] = []
        palette = ("#287bc0", "#d06d22", "#a64070", "#26804b", "#7258bb", "#95732b")
        self._colors[name] = palette[self._color_index % len(palette)]
        self._color_index += 1
        self._visible[name] = True
        self._axes[name] = 'Left'
        if demo_value is not None:
            self._providers[name] = demo_value
        else:
            try:
                from ecmc_pv_subscriptions import shared_subscriptions
                def callback(pvname=None, value=None, severity=None, **kw):
                    self._enqueue(name, numeric_readback(value, severity), token)
                def connection(pvname=None, conn=None, **kw):
                    if not conn:
                        self._enqueue(name, None, token)
                self._subscriptions[name] = shared_subscriptions.acquire(name, callback=callback,
                                                     connection_callback=connection)
            except Exception as ex:
                self.status.setText(f"{name}: CA monitor unavailable: {ex}")
        self._refresh_table()
        self._rebuild_plots()
        return True

    def _add_dialog(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Add PV", "Full PV name:")
        if ok and name.strip():
            self.add_pv(name.strip())

    def _remove_selected(self):
        names = [self._pvs[i.row()] for i in self.curves.selectionModel().selectedRows()]
        for name in names:
            with self._lock:
                self._tokens.pop(name, None)
            pv = self._subscriptions.pop(name, None)
            if pv is not None:
                try:
                    pv.clear_callbacks()
                    pv.disconnect()
                except Exception:
                    pass
            self._pvs.remove(name)
            self.trend.samples.pop(name, None)
            if self.trend.frozen is not None:
                self.trend.frozen[1].pop(name, None)
            for mapping in (self._providers, self._colors, self._visible, self._axes, self._latest, self._counts, self._rate):
                mapping.pop(name, None)
        self._refresh_table()
        self._rebuild_plots()

    def _refresh_table(self):
        self.curves.blockSignals(True)
        self.curves.setRowCount(len(self._pvs))
        for i, name in enumerate(self._pvs):
            item = QtWidgets.QTableWidgetItem(name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if self._visible[name] else QtCore.Qt.Unchecked)
            self.curves.setItem(i, 0, item)
            color = QtWidgets.QTableWidgetItem(self._colors[name])
            color.setForeground(QtGui.QColor(self._colors[name]))
            self.curves.setItem(i, 1, color)
            self.curves.setItem(i, 2, QtWidgets.QTableWidgetItem("—"))
            self.curves.setItem(i, 3, QtWidgets.QTableWidgetItem("—"))
            axis = QtWidgets.QComboBox()
            axis.addItems(["Left", "Right"])
            axis.setCurrentText(self._axes[name])
            axis.currentTextChanged.connect(lambda side, key=name: self._assign_axis(key, side))
            axis.setEnabled(not self.independent_check.isChecked())
            self.curves.setCellWidget(i, 4, axis)
        self.curves.blockSignals(False)
        self.curves.setFixedHeight(self.curves.horizontalHeader().height() + sum(self.curves.rowHeight(i) for i in range(min(5, self.curves.rowCount()))) + self.curves.frameWidth()*2 + 4)

    def _visibility_changed(self, item):
        if item.column() == 0 and item.row() < len(self._pvs):
            self._visible[self._pvs[item.row()]] = item.checkState() == QtCore.Qt.Checked
            self._rebuild_plots()

    def _color_clicked(self, row, column):
        if column != 1 or row >= len(self._pvs):
            return
        name = self._pvs[row]
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._colors[name]), self)
        if color.isValid():
            self._colors[name] = color.name()
            self._refresh_table()
            self._rebuild_plots()

    def _assign_axis(self, key, side):
        self._axes[key] = side
        self._rebuild_plots()

    def _right_scale(self, index):
        manual = index == 2
        for widget in (self.right_min, self.right_max, self.right_apply):
            widget.setEnabled(manual)
        self.trend.right_include_zero = index == 1
        if manual:
            now, source = self.trend.frozen if self.trend.frozen is not None else (time.monotonic(), self.trend.samples)
            values = [v for key, q in source.items() if self._visible.get(key) and self._axes.get(key) == "Right"
                      for t, v in q if now-self.trend.history <= t <= now and v is not None]
            if values:
                lo, hi = min(values), max(values)
                pad = (hi-lo)*.05 or max(abs(hi)*1e-6, 1e-9)
                self.right_min.setText(format(lo-pad, ".15g"))
                self.right_max.setText(format(hi+pad, ".15g"))
            self._apply_right()
        else:
            self.trend.right_fixed_range = None
            self.trend.update()

    def _apply_right(self):
        if self.right_mode.currentIndex() != 2:
            return
        try:
            lo, hi = float(self.right_min.text()), float(self.right_max.text())
            if not math.isfinite(lo) or not math.isfinite(hi) or not math.isfinite(hi-lo) or lo >= hi:
                raise ValueError()
        except ValueError:
            QtWidgets.QMessageBox.warning(self, "Invalid right Y-axis range", "Enter finite values with Min < Max.")
            return
        self.trend.right_fixed_range = (lo, hi)
        self.trend.update()

    def _rebuild_plots(self, *_):
        for plot in self._independent.values():
            self.plot_layout.removeWidget(plot)
            plot.deleteLater()
        self._independent.clear()
        visible = [(name, name.rsplit(":", 1)[-1], self._colors[name])
                   for name in self._pvs if self._visible[name]]
        self.trend.series = visible
        separate = self.independent_check.isChecked()
        dual = not separate and any(self._axes.get(key) == 'Right' for key, _, _ in visible)
        self.trend.dual_axis = dual
        self.trend.right_keys = {key for key, side in self._axes.items() if side == "Right"}
        self.right_controls.setVisible(dual)
        for i in range(self.curves.rowCount()):
            axis = self.curves.cellWidget(i, 4)
            if axis is not None:
                axis.setEnabled(not separate)
        self.trend.setVisible(not separate)
        self.scale_mode.setEnabled(not separate)
        manual = not separate and self.scale_mode.currentIndex() == 2
        for widget in (self.y_min, self.y_max, self.apply_scale):
            widget.setEnabled(manual)
        if separate:
            for entry in visible:
                name = entry[0]
                plot = Trend([entry])
                plot.include_zero = False
                plot.axis_precision = 12
                plot.history = self.trend.history
                plot.samples = {name: self.trend.samples[name]}
                self._independent[name] = plot
                self.plot_layout.addWidget(plot)
        self._sync_plots()

    def _sync_plots(self):
        self.trend.update()
        for name, plot in self._independent.items():
            plot.history = self.trend.history
            plot.frozen = None if self.trend.frozen is None else (
                self.trend.frozen[0], {name: self.trend.frozen[1].get(name, [])})
            plot.update()

    def _freeze(self, checked):
        self.trend.freeze(checked)
        self._sync_plots()

    def _export(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export acquired samples",
                                                       "hardware_trend.csv", "CSV (*.csv)")
        if path:
            try:
                export_event_csv(path, self.trend.samples)
            except Exception as ex:
                QtWidgets.QMessageBox.warning(self, "Export failed", str(ex))

    def _scale_changed(self, index):
        manual = index == 2
        self.y_min.setEnabled(manual)
        self.y_max.setEnabled(manual)
        self.apply_scale.setEnabled(manual)
        if manual:
            now, source = self.trend.frozen if self.trend.frozen is not None else (time.monotonic(), self.trend.samples)
            values = [v for key, q in source.items() if self._visible.get(key) and (not self.trend.dual_axis or self._axes.get(key) == 'Left') for t, v in q
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
        self._sync_plots()

    def _enqueue(self, key, value, token):
        with self._lock:
            if self._closed or self._tokens.get(key) is not token:
                return
            if len(self._pending) == self._pending.maxlen:
                self._dropped += 1
            self._pending.append((key, token, time.monotonic(), value))

    def _clear(self):
        with self._lock:
            self._pending.clear()
            self._dropped = 0
        self.trend.clear()
        self._sync_plots()

    def _tick(self):
        for pv, provider in self._providers.items():
            self._enqueue(pv, numeric_readback(provider(pv)), self._tokens[pv])
        with self._lock:
            events = self._pending
            self._pending = deque(maxlen=200000)
        for key, token, stamp, value in events:
            if self._tokens.get(key) is not token:
                continue
            self._latest[key] = value
            self._counts[key] = self._counts.get(key, 0) + 1
            self.trend.append(key, stamp, value)
        now = time.monotonic()
        elapsed = now - self._rate_at
        if elapsed >= 1.0:
            self._rate = {key: self._counts.get(key, 0) / elapsed for key in self._pvs}
            self._counts.clear()
            self._rate_at = now
        self._sync_plots()
        self.curves.blockSignals(True)
        for i, key in enumerate(self._pvs):
            value = self._latest.get(key)
            self.curves.item(i, 2).setText("—" if value is None else f"{value:.12g}")
            self.curves.item(i, 3).setText(f"{self._rate.get(key, 0):.0f}")
        self.curves.blockSignals(False)
        self.status.setText(f"{len(self._pvs)} PV(s)" +
            (f" | Dropped events: {self._dropped}" if self._dropped else ""))

    def closeEvent(self, event):
        with self._lock:
            self._closed = True
            self._pending.clear()
        self.timer.stop()
        for pv in self._subscriptions.values():
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass
        self._subscriptions.clear()
        super().closeEvent(event)
