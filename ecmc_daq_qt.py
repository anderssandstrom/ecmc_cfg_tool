#!/usr/bin/env python3
import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

from qt_compat import QtCore, QtGui, QtWidgets

from ecmc_stream_qt import CompactDoubleSpinBox, EpicsClient, _join_prefix_pv, compact_float_text


Signal = getattr(QtCore, "pyqtSignal", None)
if Signal is None:
    Signal = getattr(QtCore, "Signal")

PLOT_COLORS = [
    "#2563eb",
    "#dc2626",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#0891b2",
    "#be123c",
    "#4f46e5",
]
APP_LAUNCH_PLACEHOLDER = "Open app..."
APP_LAUNCH_DAQ = "New DAQ App"
APP_LAUNCH_STREAM = "Stream App"
APP_LAUNCH_AXIS = "Axis Cfg App"
APP_LAUNCH_CONTROLLER = "Cntrl Cfg App"
APP_LAUNCH_MOTION = "Motion App"
APP_LAUNCH_ISO230 = "ISO230 App"
APP_LAUNCH_CAQTDM_MAIN = "caqtdm Main"


def _coerce_float(value):
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _median(values):
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def _largest_power_of_two_leq(value):
    n = int(value or 0)
    if n < 1:
        return 0
    return 1 << (n.bit_length() - 1)


def _sanitize_samples(samples):
    clean = []
    for ts, value in sorted(samples or [], key=lambda item: float(item[0])):
        try:
            tsf = float(ts)
            vf = float(value)
        except Exception:
            continue
        if not math.isfinite(tsf) or not math.isfinite(vf):
            continue
        if clean and tsf <= clean[-1][0]:
            if abs(tsf - clean[-1][0]) < 1e-12:
                clean[-1] = (tsf, vf)
            continue
        clean.append((tsf, vf))
    return clean


def _resample_uniform(samples):
    clean = _sanitize_samples(samples)
    if len(clean) < 3:
        return {
            "clean": clean,
            "uniform": [],
            "sample_rate_hz": None,
            "median_dt_s": None,
            "span_s": 0.0,
        }

    diffs = [clean[idx][0] - clean[idx - 1][0] for idx in range(1, len(clean))]
    diffs = [dt for dt in diffs if dt > 0.0 and math.isfinite(dt)]
    median_dt = _median(diffs)
    if median_dt is None or median_dt <= 0.0:
        return {
            "clean": clean,
            "uniform": [],
            "sample_rate_hz": None,
            "median_dt_s": None,
            "span_s": max(0.0, clean[-1][0] - clean[0][0]),
        }

    start = clean[0][0]
    stop = clean[-1][0]
    span = max(0.0, stop - start)
    count = max(2, int(math.floor(span / median_dt)) + 1)
    uniform_times = [start + (idx * median_dt) for idx in range(count)]
    if uniform_times[-1] < stop:
        uniform_times.append(stop)

    uniform_values = []
    src_index = 0
    for target_ts in uniform_times:
        while src_index + 1 < len(clean) and clean[src_index + 1][0] < target_ts:
            src_index += 1
        if src_index + 1 >= len(clean):
            uniform_values.append(clean[-1][1])
            continue
        left_ts, left_val = clean[src_index]
        right_ts, right_val = clean[src_index + 1]
        if target_ts <= left_ts or abs(right_ts - left_ts) < 1e-12:
            uniform_values.append(left_val)
            continue
        ratio = (target_ts - left_ts) / (right_ts - left_ts)
        uniform_values.append(left_val + ((right_val - left_val) * ratio))

    uniform = list(zip(uniform_times, uniform_values))
    return {
        "clean": clean,
        "uniform": uniform,
        "sample_rate_hz": 1.0 / median_dt if median_dt > 0.0 else None,
        "median_dt_s": median_dt,
        "span_s": span,
    }


def _fft_complex(values):
    n = len(values)
    data = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            data[i], data[j] = data[j], data[i]

    length = 2
    while length <= n:
        angle = -2.0 * math.pi / float(length)
        step = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for start in range(0, n, length):
            factor = 1.0 + 0.0j
            for offset in range(half):
                even = data[start + offset]
                odd = factor * data[start + offset + half]
                data[start + offset] = even + odd
                data[start + offset + half] = even - odd
                factor *= step
        length <<= 1
    return data


def _slice_samples_by_relative_time(samples, origin_ts, start_s=None, end_s=None):
    if not samples:
        return []
    start_value = None if start_s is None else float(start_s)
    end_value = None if end_s is None else float(end_s)
    out = []
    for ts, value in samples:
        rel = float(ts) - float(origin_ts)
        if start_value is not None and rel < start_value:
            continue
        if end_value is not None and rel > end_value:
            continue
        out.append((float(ts), float(value)))
    return out


def _remove_linear_trend(points):
    if len(points) < 2:
        return list(points)
    xs = [float(x) for x, _y in points]
    ys = [float(y) for _x, y in points]
    mean_x = sum(xs) / float(len(xs))
    mean_y = sum(ys) / float(len(ys))
    var_x = sum((x - mean_x) * (x - mean_x) for x in xs)
    if var_x <= 0.0:
        return [(x, y - mean_y) for x, y in points]
    cov_xy = sum((xs[idx] - mean_x) * (ys[idx] - mean_y) for idx in range(len(xs)))
    slope = cov_xy / var_x
    intercept = mean_y - (slope * mean_x)
    return [(x, y - ((slope * x) + intercept)) for x, y in points]


def _difference_uniform(points, sample_rate_hz):
    if len(points) < 2 or sample_rate_hz is None or sample_rate_hz <= 0.0:
        return []
    out = []
    prev_x, prev_y = points[0]
    for x_val, y_val in points[1:]:
        out.append((float(x_val), (float(y_val) - float(prev_y)) * float(sample_rate_hz)))
        prev_x, prev_y = x_val, y_val
    return out


def _analyze_signal(samples, *, origin_ts=None, analysis_start_s=None, analysis_end_s=None, remove_mean=True, detrend=False, use_delta=False):
    origin = float(origin_ts) if origin_ts is not None else (float(samples[0][0]) if samples else 0.0)
    sliced = _slice_samples_by_relative_time(samples, origin, analysis_start_s, analysis_end_s)
    resampled = _resample_uniform(sliced)
    clean = resampled["clean"]
    uniform = resampled["uniform"]
    sample_rate_hz = resampled["sample_rate_hz"]
    if len(clean) < 3:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": uniform,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }

    fft_size = _largest_power_of_two_leq(len(uniform))
    if fft_size < 8 or sample_rate_hz is None:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": uniform,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }

    uniform = uniform[-fft_size:]
    processed = [(float(ts) - origin, float(v)) for ts, v in uniform]
    if use_delta:
        processed = _difference_uniform(processed, sample_rate_hz)
    if detrend:
        processed = _remove_linear_trend(processed)
    if remove_mean and processed:
        mean_value = sum(float(v) for _ts, v in processed) / float(len(processed))
        processed = [(x_val, float(y_val) - mean_value) for x_val, y_val in processed]

    fft_size = _largest_power_of_two_leq(len(processed))
    if fft_size < 8 or sample_rate_hz is None:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": processed,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }
    processed = processed[-fft_size:]
    if fft_size <= 1:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": processed,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }

    window = [0.5 - (0.5 * math.cos((2.0 * math.pi * idx) / float(fft_size - 1))) for idx in range(fft_size)]
    coherent_gain = sum(window) / float(fft_size)
    fft_input = [complex(float(processed[idx][1]) * window[idx], 0.0) for idx in range(fft_size)]
    spectrum_complex = _fft_complex(fft_input)

    spectrum = []
    half = fft_size // 2
    peak_freq_hz = None
    peak_amplitude = None
    for idx in range(half + 1):
        freq = (float(idx) * float(sample_rate_hz)) / float(fft_size)
        magnitude = abs(spectrum_complex[idx]) / float(fft_size)
        if coherent_gain > 0.0:
            magnitude /= coherent_gain
        if idx != 0 and idx != half:
            magnitude *= 2.0
        spectrum.append((freq, magnitude))
        if idx == 0:
            continue
        if peak_amplitude is None or magnitude > peak_amplitude:
            peak_amplitude = magnitude
            peak_freq_hz = freq

    return {
        "selected": clean,
        "clean": clean,
        "uniform": uniform,
        "processed": processed,
        "sample_rate_hz": sample_rate_hz,
        "fft_size": fft_size,
        "span_s": resampled["span_s"],
        "spectrum": spectrum,
        "peak_freq_hz": peak_freq_hz,
        "peak_amplitude": peak_amplitude,
        "median_dt_s": resampled["median_dt_s"],
    }


class SeriesPlotWidget(QtWidgets.QWidget):
    range_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series = {}
        self._title = ""
        self._x_label = ""
        self._y_label = ""
        self._empty_text = "No data"
        self._x_floor = None
        self._y_floor = None
        self._selection_enabled = False
        self._selection_range = None
        self._active_cursor = None
        self._pan_active = False
        self._pan_last_pos = None
        self._plot_rect = QtCore.QRectF()
        self._plot_x_min = 0.0
        self._plot_x_max = 1.0
        self._plot_y_min = 0.0
        self._plot_y_max = 1.0
        self._full_x_min = 0.0
        self._full_x_max = 1.0
        self._full_y_min = 0.0
        self._full_y_max = 1.0
        self._view_x_range = None
        self._view_y_range = None
        self.setMinimumSize(700, 420)

    def set_axes(self, title="", x_label="", y_label="", empty_text="No data"):
        self._title = str(title or "")
        self._x_label = str(x_label or "")
        self._y_label = str(y_label or "")
        self._empty_text = str(empty_text or "No data")
        self.update()

    def set_x_floor(self, value):
        self._x_floor = None if value is None else float(value)
        self.update()

    def set_y_floor(self, value):
        self._y_floor = None if value is None else float(value)
        self.update()

    def set_series(self, series):
        self._series = dict(series or {})
        self.update()

    def set_selection_enabled(self, enabled):
        self._selection_enabled = bool(enabled)
        if not self._selection_enabled:
            self._active_cursor = None
        self.update()

    def set_selection_range(self, start_s, end_s):
        if start_s is None or end_s is None:
            self._selection_range = None
        else:
            start = float(start_s)
            end = float(end_s)
            self._selection_range = (min(start, end), max(start, end))
        self.update()

    def selection_range(self):
        return self._selection_range

    def reset_zoom(self):
        self._view_x_range = None
        self._view_y_range = None
        self._pan_active = False
        self._pan_last_pos = None
        self.unsetCursor()
        self.update()

    def view_state(self):
        return {
            "x_range": list(self._view_x_range) if self._view_x_range is not None else None,
            "y_range": list(self._view_y_range) if self._view_y_range is not None else None,
        }

    def set_view_state(self, state):
        payload = dict(state or {})
        x_range = payload.get("x_range")
        y_range = payload.get("y_range")
        self._view_x_range = None if x_range is None else (float(x_range[0]), float(x_range[1]))
        self._view_y_range = None if y_range is None else (float(y_range[0]), float(y_range[1]))
        self.update()

    def _map_plot_x(self, value):
        return self._plot_rect.left() + ((float(value) - self._plot_x_min) / (self._plot_x_max - self._plot_x_min)) * self._plot_rect.width()

    def _map_value_x(self, pos_x):
        if self._plot_rect.width() <= 0.0:
            return self._plot_x_min
        ratio = (float(pos_x) - self._plot_rect.left()) / self._plot_rect.width()
        ratio = max(0.0, min(1.0, ratio))
        return self._plot_x_min + (ratio * (self._plot_x_max - self._plot_x_min))

    def _map_value_y(self, pos_y):
        if self._plot_rect.height() <= 0.0:
            return self._plot_y_min
        ratio = (self._plot_rect.bottom() - float(pos_y)) / self._plot_rect.height()
        ratio = max(0.0, min(1.0, ratio))
        return self._plot_y_min + (ratio * (self._plot_y_max - self._plot_y_min))

    def _normalize_view_range(self, view_range, full_min, full_max):
        if view_range is None:
            return None
        if full_max <= full_min:
            return None
        start, end = float(view_range[0]), float(view_range[1])
        if end < start:
            start, end = end, start
        full_span = full_max - full_min
        min_span = max(full_span * 1e-6, 1e-9)
        span = max(min_span, end - start)
        if span >= full_span * 0.999999:
            return None
        if start < full_min:
            end += full_min - start
            start = full_min
        if end > full_max:
            start -= end - full_max
            end = full_max
        start = max(full_min, start)
        end = min(full_max, end)
        if end - start < min_span:
            return None
        return (start, end)

    def _zoom_axis(self, axis, anchor_value, factor):
        if axis == "x":
            current_min, current_max = self._plot_x_min, self._plot_x_max
            full_min, full_max = self._full_x_min, self._full_x_max
        else:
            current_min, current_max = self._plot_y_min, self._plot_y_max
            full_min, full_max = self._full_y_min, self._full_y_max
        current_span = current_max - current_min
        full_span = full_max - full_min
        if current_span <= 0.0 or full_span <= 0.0:
            return
        new_span = current_span * float(factor)
        if new_span >= full_span * 0.999999:
            new_range = None
        else:
            rel = (float(anchor_value) - current_min) / current_span
            rel = max(0.0, min(1.0, rel))
            start = float(anchor_value) - (rel * new_span)
            end = start + new_span
            new_range = self._normalize_view_range((start, end), full_min, full_max)
        if axis == "x":
            self._view_x_range = new_range
        else:
            self._view_y_range = new_range
        self.update()

    def _pan_by_pixels(self, delta_x, delta_y):
        if self._plot_rect.width() <= 0.0 or self._plot_rect.height() <= 0.0:
            return
        if self._view_x_range is not None:
            span = self._view_x_range[1] - self._view_x_range[0]
            shift = -(float(delta_x) / self._plot_rect.width()) * span
            self._view_x_range = self._normalize_view_range(
                (self._view_x_range[0] + shift, self._view_x_range[1] + shift),
                self._full_x_min,
                self._full_x_max,
            )
        if self._view_y_range is not None:
            span = self._view_y_range[1] - self._view_y_range[0]
            shift = (float(delta_y) / self._plot_rect.height()) * span
            self._view_y_range = self._normalize_view_range(
                (self._view_y_range[0] + shift, self._view_y_range[1] + shift),
                self._full_y_min,
                self._full_y_max,
            )
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor("#fbfbfc"))

        margin_l = 68
        margin_r = 24
        margin_t = 42
        margin_b = 52
        plot = rect.adjusted(margin_l, margin_t, -margin_r, -margin_b)
        if plot.width() <= 10 or plot.height() <= 10:
            return

        painter.setPen(QtGui.QPen(QtGui.QColor("#d1d5db"), 1))
        painter.drawRect(plot)

        visible = []
        for name, points in self._series.items():
            clean = []
            for x_val, y_val in points or []:
                try:
                    xf = float(x_val)
                    yf = float(y_val)
                except Exception:
                    continue
                if not math.isfinite(xf) or not math.isfinite(yf):
                    continue
                clean.append((xf, yf))
            if clean:
                visible.append((str(name), clean))

        painter.setPen(QtGui.QColor("#111827"))
        painter.setFont(QtGui.QFont(painter.font().family(), 11, QtGui.QFont.Bold))
        painter.drawText(plot.left(), 24, self._title)

        if not visible:
            painter.setPen(QtGui.QColor("#6b7280"))
            painter.setFont(QtGui.QFont(painter.font().family(), 10))
            painter.drawText(plot, QtCore.Qt.AlignCenter, self._empty_text)
            return

        all_x = [x_val for _name, points in visible for x_val, _y_val in points]
        all_y = [y_val for _name, points in visible for _x_val, y_val in points]
        x_min = min(all_x)
        x_max = max(all_x)
        y_min = min(all_y)
        y_max = max(all_y)
        if self._x_floor is not None:
            x_min = min(x_min, self._x_floor)
        if self._y_floor is not None:
            y_min = min(y_min, self._y_floor)
        if x_max <= x_min:
            x_max = x_min + 1.0
        if y_max <= y_min:
            pad = max(abs(y_min) * 0.1, 1.0)
            y_min -= pad
            y_max += pad
        else:
            pad = max((y_max - y_min) * 0.08, 1e-12)
            y_min -= pad
            y_max += pad
            if self._y_floor is not None:
                y_min = min(y_min, self._y_floor)

        self._full_x_min = float(x_min)
        self._full_x_max = float(x_max)
        self._full_y_min = float(y_min)
        self._full_y_max = float(y_max)
        self._view_x_range = self._normalize_view_range(self._view_x_range, self._full_x_min, self._full_x_max)
        self._view_y_range = self._normalize_view_range(self._view_y_range, self._full_y_min, self._full_y_max)
        if self._view_x_range is not None:
            x_min, x_max = self._view_x_range
        if self._view_y_range is not None:
            y_min, y_max = self._view_y_range

        def map_x(value):
            return plot.left() + ((float(value) - x_min) / (x_max - x_min)) * plot.width()

        def map_y(value):
            return plot.bottom() - ((float(value) - y_min) / (y_max - y_min)) * plot.height()

        self._plot_rect = QtCore.QRectF(plot)
        self._plot_x_min = float(x_min)
        self._plot_x_max = float(x_max)
        self._plot_y_min = float(y_min)
        self._plot_y_max = float(y_max)

        grid_pen = QtGui.QPen(QtGui.QColor("#e5e7eb"), 1)
        for idx in range(1, 5):
            x_pos = plot.left() + (plot.width() * idx) / 5.0
            y_pos = plot.top() + (plot.height() * idx) / 5.0
            painter.setPen(grid_pen)
            painter.drawLine(int(x_pos), int(plot.top()), int(x_pos), int(plot.bottom()))
            painter.drawLine(int(plot.left()), int(y_pos), int(plot.right()), int(y_pos))

        painter.setPen(QtGui.QColor("#374151"))
        painter.setFont(QtGui.QFont(painter.font().family(), 9))
        painter.drawText(6, int(plot.top()) + 6, compact_float_text(y_max))
        painter.drawText(6, int(plot.bottom()) + 4, compact_float_text(y_min))
        painter.drawText(int(plot.left()), rect.bottom() - 10, compact_float_text(x_min))
        painter.drawText(int(plot.right()) - 60, rect.bottom() - 10, compact_float_text(x_max))
        if self._x_label:
            painter.drawText(plot.center().x() - 40, rect.bottom() - 10, self._x_label)
        if self._y_label:
            painter.save()
            painter.translate(16, plot.center().y() + 40)
            painter.rotate(-90)
            painter.drawText(0, 0, self._y_label)
            painter.restore()

        legend_x = plot.left()
        legend_y = 34
        for idx, (name, _points) in enumerate(visible):
            color = QtGui.QColor(PLOT_COLORS[idx % len(PLOT_COLORS)])
            painter.setBrush(color)
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawEllipse(QtCore.QPointF(legend_x + 6, legend_y - 4), 4.0, 4.0)
            painter.setPen(QtGui.QColor("#1f2937"))
            painter.drawText(int(legend_x + 16), int(legend_y), name)
            legend_x += max(120, 18 + painter.fontMetrics().horizontalAdvance(name) + 22)

        for idx, (_name, points) in enumerate(visible):
            color = QtGui.QColor(PLOT_COLORS[idx % len(PLOT_COLORS)])
            path = QtGui.QPainterPath()
            for point_idx, (x_val, y_val) in enumerate(points):
                qpoint = QtCore.QPointF(map_x(x_val), map_y(y_val))
                if point_idx == 0:
                    path.moveTo(qpoint)
                else:
                    path.lineTo(qpoint)
            painter.setPen(QtGui.QPen(color, 2))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawPath(path)
            last_x, last_y = points[-1]
            painter.setBrush(color)
            painter.drawEllipse(QtCore.QPointF(map_x(last_x), map_y(last_y)), 3.0, 3.0)

        if self._selection_enabled and self._selection_range is not None:
            start_sel, end_sel = self._selection_range
            start_sel = max(x_min, min(x_max, float(start_sel)))
            end_sel = max(x_min, min(x_max, float(end_sel)))
            if end_sel < start_sel:
                start_sel, end_sel = end_sel, start_sel
            left_sel = map_x(start_sel)
            right_sel = map_x(end_sel)
            shade = QtGui.QColor(15, 23, 42, 20)
            painter.fillRect(QtCore.QRectF(plot.left(), plot.top(), max(0.0, left_sel - plot.left()), plot.height()), shade)
            painter.fillRect(QtCore.QRectF(right_sel, plot.top(), max(0.0, plot.right() - right_sel), plot.height()), shade)

            start_pen = QtGui.QPen(QtGui.QColor("#0f766e"), 2, QtCore.Qt.DashLine)
            end_pen = QtGui.QPen(QtGui.QColor("#b45309"), 2, QtCore.Qt.DashLine)
            painter.setPen(start_pen)
            painter.drawLine(QtCore.QPointF(left_sel, plot.top()), QtCore.QPointF(left_sel, plot.bottom()))
            painter.setPen(end_pen)
            painter.drawLine(QtCore.QPointF(right_sel, plot.top()), QtCore.QPointF(right_sel, plot.bottom()))

            painter.setPen(QtGui.QColor("#0f172a"))
            painter.setBrush(QtGui.QColor("#ffffff"))
            painter.drawText(int(left_sel + 4), int(plot.top()) + 14, f"S {compact_float_text(start_sel)}")
            painter.drawText(int(right_sel + 4), int(plot.top()) + 28, f"E {compact_float_text(end_sel)}")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton and self._plot_rect.contains(event.pos()):
            self._pan_active = True
            self._pan_last_pos = QtCore.QPointF(event.pos())
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if not self._selection_enabled or self._selection_range is None or not self._plot_rect.contains(event.pos()):
            return super().mousePressEvent(event)
        start_sel, end_sel = self._selection_range
        x_pos = float(event.pos().x())
        start_px = self._map_plot_x(start_sel)
        end_px = self._map_plot_x(end_sel)
        if abs(x_pos - start_px) <= abs(x_pos - end_px):
            self._active_cursor = "start"
        else:
            self._active_cursor = "end"
        self._move_cursor_to_pos(x_pos)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._pan_active and self._pan_last_pos is not None:
            delta = QtCore.QPointF(event.pos()) - self._pan_last_pos
            self._pan_last_pos = QtCore.QPointF(event.pos())
            self._pan_by_pixels(delta.x(), delta.y())
            event.accept()
            return
        if self._active_cursor is None or not self._selection_enabled:
            return super().mouseMoveEvent(event)
        self._move_cursor_to_pos(float(event.pos().x()))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.RightButton and self._pan_active:
            self._pan_active = False
            self._pan_last_pos = None
            self.unsetCursor()
            event.accept()
            return
        if self._active_cursor is None:
            return super().mouseReleaseEvent(event)
        self._move_cursor_to_pos(float(event.pos().x()))
        self._active_cursor = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            self.reset_zoom()
            event.accept()
            return
        if not self._selection_enabled or not self._plot_rect.contains(event.pos()):
            return super().mouseDoubleClickEvent(event)
        self._selection_range = (self._full_x_min, self._full_x_max)
        self.range_changed.emit(float(self._full_x_min), float(self._full_x_max))
        self.update()
        event.accept()

    def wheelEvent(self, event):
        pos = event.position() if hasattr(event, "position") else QtCore.QPointF(event.pos())
        if not self._plot_rect.contains(pos):
            return super().wheelEvent(event)
        delta = event.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(event)
        factor = 0.85 if delta > 0 else 1.18
        modifiers = event.modifiers()
        if modifiers & QtCore.Qt.ControlModifier:
            self._zoom_axis("x", self._map_value_x(pos.x()), factor)
            self._zoom_axis("y", self._map_value_y(pos.y()), factor)
        elif modifiers & QtCore.Qt.ShiftModifier:
            self._zoom_axis("y", self._map_value_y(pos.y()), factor)
        else:
            self._zoom_axis("x", self._map_value_x(pos.x()), factor)
        event.accept()

    def _move_cursor_to_pos(self, pos_x):
        if self._selection_range is None or self._active_cursor is None:
            return
        value = self._map_value_x(pos_x)
        start_sel, end_sel = self._selection_range
        if self._active_cursor == "start":
            start_sel = min(value, end_sel)
        else:
            end_sel = max(value, start_sel)
        self._selection_range = (float(start_sel), float(end_sel))
        self.range_changed.emit(float(start_sel), float(end_sel))
        self.update()


class SpectrumWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PV DAQ - Frequency Domain")
        self.resize(860, 640)
        root = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, root)
        layout.addWidget(splitter, 1)

        self.time_plot_widget = SeriesPlotWidget(self)
        self.time_plot_widget.set_axes(
            title="Time Domain",
            x_label="Time [s]",
            y_label="Value",
            empty_text="Capture data in the main window to view the time-domain signal.",
        )
        self.time_plot_widget.set_x_floor(0.0)
        self.time_plot_widget.setMinimumHeight(140)
        splitter.addWidget(self.time_plot_widget)

        self.plot_widget = SeriesPlotWidget(self)
        self.plot_widget.set_axes(
            title="Frequency Domain",
            x_label="Frequency [Hz]",
            y_label="Amplitude",
            empty_text="Capture data in the main window to view the FFT.",
        )
        self.plot_widget.set_x_floor(0.0)
        self.plot_widget.set_y_floor(0.0)
        self.plot_widget.setMinimumHeight(280)
        splitter.addWidget(self.plot_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 7)

        self.setCentralWidget(root)
        toolbar = self.addToolBar("View")
        toolbar.setMovable(False)
        reset_time_action = toolbar.addAction("Reset Time Zoom")
        reset_fft_action = toolbar.addAction("Reset FFT Zoom")
        reset_time_action.triggered.connect(self.time_plot_widget.reset_zoom)
        reset_fft_action.triggered.connect(self.plot_widget.reset_zoom)
        self.statusBar().showMessage("Idle")

    def set_spectrum(self, series, summary_text=""):
        self.plot_widget.set_series(series)
        self.statusBar().showMessage(str(summary_text or "Idle"))

    def set_plots(self, time_series, spectrum_series, summary_text=""):
        self.time_plot_widget.set_series(time_series)
        self.plot_widget.set_series(spectrum_series)
        self.statusBar().showMessage(str(summary_text or "Idle"))


class PvMonitorSource(QtCore.QObject):
    sample_ready = Signal(str, float, float)
    error_text = Signal(str)
    backend_text = Signal(str)

    def __init__(self, timeout=2.0, fallback_poll_ms=20, parent=None):
        super().__init__(parent)
        self.client = EpicsClient(timeout=timeout)
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.timeout.connect(self._poll_once)
        self._poll_pvs = []
        self._monitor_pvs = []
        self._pyepics_module = self.client._epics if getattr(self.client, "backend", "") == "pyepics" else None

        self._fallback_poll_ms = 20
        self.set_fallback_poll_ms(fallback_poll_ms)
        self.backend_text.emit(self.backend_name())

    def backend_name(self):
        backend = str(getattr(self.client, "backend", "") or "unknown")
        if self._pyepics_module is not None:
            return f"{backend} (monitor)"
        return f"{backend} (poll)"

    def set_timeout(self, timeout_s):
        self.client.timeout = float(timeout_s)

    def set_fallback_poll_ms(self, interval_ms):
        self._fallback_poll_ms = max(5, int(round(float(interval_ms))))
        if self._poll_timer.isActive():
            self._poll_timer.setInterval(self._fallback_poll_ms)

    def start(self, pv_names):
        self.stop()
        pvs = []
        seen = set()
        for pv in pv_names or []:
            name = str(pv or "").strip()
            if name and name not in seen:
                seen.add(name)
                pvs.append(name)
        if not pvs:
            return

        if self._pyepics_module is not None:
            self._start_pyepics_monitors(pvs)
            self.backend_text.emit(self.backend_name())
            return

        self._poll_pvs = list(pvs)
        self._poll_timer.start(self._fallback_poll_ms)
        self.backend_text.emit(self.backend_name())

    def stop(self):
        self._poll_timer.stop()
        self._poll_pvs = []
        for pv_obj in self._monitor_pvs:
            try:
                pv_obj.clear_callbacks()
            except Exception:
                pass
            try:
                pv_obj.auto_monitor = False
            except Exception:
                pass
            try:
                pv_obj.disconnect()
            except Exception:
                pass
        self._monitor_pvs = []

    def _start_pyepics_monitors(self, pv_names):
        self._monitor_pvs = []
        for name in pv_names:
            try:
                pv_obj = self._pyepics_module.PV(name, auto_monitor=True)
                if hasattr(pv_obj, "wait_for_connection") and not pv_obj.wait_for_connection(timeout=float(self.client.timeout)):
                    self.error_text.emit(f"PV monitor failed to connect: {name}")
                    continue
                pv_obj.add_callback(callback=self._make_callback(name))
                self._monitor_pvs.append(pv_obj)
            except Exception as ex:
                self.error_text.emit(f"PV monitor setup failed for {name}: {ex}")

    def _make_callback(self, pv_name):
        def _callback(pvname=None, value=None, timestamp=None, **_kwargs):
            num = _coerce_float(value)
            if num is None:
                return
            ts = float(timestamp) if timestamp not in (None, "") else time.time()
            if not math.isfinite(ts):
                ts = time.time()
            self.sample_ready.emit(str(pv_name or pvname or ""), ts, num)

        return _callback

    def _poll_once(self):
        for name in list(self._poll_pvs):
            try:
                raw = self.client.get(name, as_string=True)
                num = _coerce_float(raw)
                if num is None:
                    continue
                self.sample_ready.emit(name, time.time(), num)
            except Exception as ex:
                self.error_text.emit(f"Polling failed for {name}: {ex}")


class DaqWindow(QtWidgets.QMainWindow):
    def __init__(self, default_prefix="", initial_pvs=None, timeout=2.0):
        super().__init__()
        self.setWindowTitle("PV DAQ - Time Domain")
        self.resize(980, 680)

        self._capture_active = False
        self._capture_start_ts = None
        self._capture_goal = 2048
        self._samples_by_pv = {}
        self._analysis_by_pv = {}
        self._default_prefix = str(default_prefix or "").strip()
        self._pending_refresh = False
        self._spectrum_window_shown_once = False
        self._analysis_start_s = 0.0
        self._analysis_end_s = 0.0
        self._analysis_range_auto = True
        self._last_session_dir = Path.home()
        self._last_image_dir = Path.home()

        self.source = PvMonitorSource(timeout=timeout, fallback_poll_ms=20, parent=self)
        self.source.sample_ready.connect(self._on_sample_ready)
        self.source.error_text.connect(self._log)
        self.source.backend_text.connect(self._set_backend_text)

        self.spectrum_window = SpectrumWindow()
        self._build_ui()

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh_views)

        self.prefix_edit.setText(self._default_prefix)
        for pv in initial_pvs or []:
            self._add_pv(str(pv or "").strip(), log=False)
        self._refresh_views()

    def _build_ui(self):
        root = QtWidgets.QWidget(self)
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QtWidgets.QHBoxLayout()
        self.prefix_edit = QtWidgets.QLineEdit()
        self.prefix_edit.setPlaceholderText("Optional IOC prefix for short PV names")
        self.timeout_spin = CompactDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 30.0)
        self.timeout_spin.setDecimals(2)
        self.timeout_spin.setSingleStep(0.1)
        self.timeout_spin.setValue(float(self.source.client.timeout))
        self.sample_count_spin = QtWidgets.QSpinBox()
        self.sample_count_spin.setRange(8, 131072)
        self.sample_count_spin.setSingleStep(128)
        self.sample_count_spin.setValue(self._capture_goal)
        self.poll_interval_spin = QtWidgets.QSpinBox()
        self.poll_interval_spin.setRange(5, 2000)
        self.poll_interval_spin.setSingleStep(5)
        self.poll_interval_spin.setSuffix(" ms")
        self.poll_interval_spin.setValue(20)
        self.backend_label = QtWidgets.QLabel(self.source.backend_name())
        self.backend_label.setStyleSheet("color: #475569;")
        self.backend_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.open_app_combo = QtWidgets.QComboBox()
        self.open_app_combo.setMinimumWidth(170)
        self.open_app_combo.addItem(APP_LAUNCH_PLACEHOLDER, "")
        self.open_app_combo.addItem(APP_LAUNCH_DAQ, "daq")
        self.open_app_combo.addItem(APP_LAUNCH_STREAM, "stream")
        self.open_app_combo.addItem(APP_LAUNCH_AXIS, "axis")
        self.open_app_combo.addItem(APP_LAUNCH_CONTROLLER, "controller")
        self.open_app_combo.addItem(APP_LAUNCH_MOTION, "motion")
        self.open_app_combo.addItem(APP_LAUNCH_ISO230, "iso230")
        self.open_app_combo.addItem(APP_LAUNCH_CAQTDM_MAIN, "caqtdm_main")

        top_row.addWidget(QtWidgets.QLabel("IOC Prefix"))
        top_row.addWidget(self.prefix_edit, 1)
        top_row.addSpacing(8)
        top_row.addWidget(QtWidgets.QLabel("Timeout [s]"))
        top_row.addWidget(self.timeout_spin)
        top_row.addSpacing(8)
        top_row.addWidget(QtWidgets.QLabel("Samples / PV"))
        top_row.addWidget(self.sample_count_spin)
        top_row.addSpacing(8)
        top_row.addWidget(QtWidgets.QLabel("Fallback Poll"))
        top_row.addWidget(self.poll_interval_spin)
        top_row.addSpacing(12)
        top_row.addWidget(QtWidgets.QLabel("Backend"))
        top_row.addWidget(self.backend_label)
        top_row.addSpacing(12)
        top_row.addWidget(QtWidgets.QLabel("Launch"))
        top_row.addWidget(self.open_app_combo)
        layout.addLayout(top_row)

        proc_row = QtWidgets.QHBoxLayout()
        self.remove_mean_chk = QtWidgets.QCheckBox("Remove Mean")
        self.remove_mean_chk.setChecked(True)
        self.detrend_chk = QtWidgets.QCheckBox("Detrend")
        self.use_delta_chk = QtWidgets.QCheckBox("Use Delta / Rate")
        self.range_start_spin = CompactDoubleSpinBox()
        self.range_start_spin.setRange(0.0, 1e9)
        self.range_start_spin.setDecimals(4)
        self.range_start_spin.setSingleStep(0.1)
        self.range_end_spin = CompactDoubleSpinBox()
        self.range_end_spin.setRange(0.0, 1e9)
        self.range_end_spin.setDecimals(4)
        self.range_end_spin.setSingleStep(0.1)
        reset_range_btn = QtWidgets.QPushButton("Full Capture Range")
        reset_time_zoom_btn = QtWidgets.QPushButton("Reset Time Zoom")
        reset_range_btn.setAutoDefault(False)
        reset_range_btn.setDefault(False)
        reset_time_zoom_btn.setAutoDefault(False)
        reset_time_zoom_btn.setDefault(False)

        proc_row.addWidget(QtWidgets.QLabel("FFT Prep"))
        proc_row.addWidget(self.remove_mean_chk)
        proc_row.addWidget(self.detrend_chk)
        proc_row.addWidget(self.use_delta_chk)
        proc_row.addSpacing(16)
        proc_row.addWidget(QtWidgets.QLabel("Start [s]"))
        proc_row.addWidget(self.range_start_spin)
        proc_row.addWidget(QtWidgets.QLabel("End [s]"))
        proc_row.addWidget(self.range_end_spin)
        proc_row.addWidget(reset_range_btn)
        proc_row.addWidget(reset_time_zoom_btn)
        proc_row.addStretch(1)
        layout.addLayout(proc_row)

        file_row = QtWidgets.QHBoxLayout()
        self.log_toggle_btn = QtWidgets.QPushButton("Show Log")
        save_session_btn = QtWidgets.QPushButton("Save Session")
        load_session_btn = QtWidgets.QPushButton("Load Session")
        save_time_plot_btn = QtWidgets.QPushButton("Save Time Plot")
        save_fft_plot_btn = QtWidgets.QPushButton("Save FFT Plot")
        for btn in (self.log_toggle_btn, save_session_btn, load_session_btn, save_time_plot_btn, save_fft_plot_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
        file_row.addWidget(self.log_toggle_btn)
        file_row.addSpacing(12)
        file_row.addWidget(save_session_btn)
        file_row.addWidget(load_session_btn)
        file_row.addSpacing(12)
        file_row.addWidget(save_time_plot_btn)
        file_row.addWidget(save_fft_plot_btn)
        file_row.addStretch(1)
        layout.addLayout(file_row)

        pv_row = QtWidgets.QHBoxLayout()
        self.pv_edit = QtWidgets.QLineEdit()
        self.pv_edit.setPlaceholderText("PV name or suffix")
        add_btn = QtWidgets.QPushButton("Add PV")
        remove_btn = QtWidgets.QPushButton("Remove Selected")
        clear_pvs_btn = QtWidgets.QPushButton("Clear PVs")
        start_btn = QtWidgets.QPushButton("Start Capture")
        stop_btn = QtWidgets.QPushButton("Stop")
        clear_samples_btn = QtWidgets.QPushButton("Clear Samples")
        show_freq_btn = QtWidgets.QPushButton("Show FFT Window")
        for btn in (add_btn, remove_btn, clear_pvs_btn, start_btn, stop_btn, clear_samples_btn, show_freq_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
        self.start_btn = start_btn
        self.stop_btn = stop_btn

        pv_row.addWidget(QtWidgets.QLabel("PV"))
        pv_row.addWidget(self.pv_edit, 1)
        pv_row.addWidget(add_btn)
        pv_row.addWidget(remove_btn)
        pv_row.addWidget(clear_pvs_btn)
        pv_row.addSpacing(12)
        pv_row.addWidget(start_btn)
        pv_row.addWidget(stop_btn)
        pv_row.addWidget(clear_samples_btn)
        pv_row.addWidget(show_freq_btn)
        layout.addLayout(pv_row)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, self)
        layout.addWidget(splitter, 1)

        upper = QtWidgets.QWidget()
        upper_layout = QtWidgets.QHBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(8)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(QtWidgets.QLabel("Selected PVs"))
        self.pv_list = QtWidgets.QListWidget()
        self.pv_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.pv_list.setAlternatingRowColors(True)
        left_layout.addWidget(self.pv_list, 1)

        note = QtWidgets.QLabel(
            "FFT uses the captured timestamps to estimate the effective sample rate per PV. "
            "Signals are linearly resampled onto a uniform grid before the FFT is calculated. "
            "Wheel zooms horizontally, Shift+wheel zooms vertically, Ctrl+wheel zooms both axes, "
            "right-drag pans, and right double-click resets zoom. "
            "Drag the start/end markers in the time plot, or left double-click there to reset to the full capture."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #516079;")
        left_layout.addWidget(note)
        upper_layout.addWidget(left, 0)

        self.time_plot = SeriesPlotWidget()
        self.time_plot.set_axes(
            title="Time Domain",
            x_label="Time [s]",
            y_label="Value",
            empty_text="Add one or more PVs and start a capture.",
        )
        self.time_plot.set_x_floor(0.0)
        self.time_plot.set_selection_enabled(True)
        upper_layout.addWidget(self.time_plot, 1)
        splitter.addWidget(upper)

        lower = QtWidgets.QWidget()
        lower_layout = QtWidgets.QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(6)

        self.capture_status = QtWidgets.QLabel("Idle")
        self.capture_status.setStyleSheet("font-weight: 600; color: #0f172a;")
        lower_layout.addWidget(self.capture_status)

        self.stats_table = QtWidgets.QTableWidget(0, 8)
        self.stats_table.setHorizontalHeaderLabels(
            ["PV", "Samples", "Span [s]", "Fs [Hz]", "FFT N", "Peak [Hz]", "Peak Amp", "Last"]
        )
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.stats_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.stats_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 8):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        lower_layout.addWidget(self.stats_table, 1)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Log")
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(140)
        self.log.setVisible(False)
        lower_layout.addWidget(self.log)
        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.timeout_spin.valueChanged.connect(self._on_timeout_changed)
        self.poll_interval_spin.valueChanged.connect(self._on_poll_interval_changed)
        self.sample_count_spin.valueChanged.connect(self._on_sample_goal_changed)
        self.open_app_combo.activated.connect(self._on_open_app_selected)
        self.remove_mean_chk.toggled.connect(self._on_analysis_settings_changed)
        self.detrend_chk.toggled.connect(self._on_analysis_settings_changed)
        self.use_delta_chk.toggled.connect(self._on_analysis_settings_changed)
        self.range_start_spin.valueChanged.connect(self._on_analysis_range_spin_changed)
        self.range_end_spin.valueChanged.connect(self._on_analysis_range_spin_changed)
        self.time_plot.range_changed.connect(self._on_plot_range_changed)
        self.pv_edit.returnPressed.connect(self._add_pv_from_editor)
        add_btn.clicked.connect(self._add_pv_from_editor)
        remove_btn.clicked.connect(self._remove_selected_pvs)
        clear_pvs_btn.clicked.connect(self._clear_pvs)
        start_btn.clicked.connect(self.start_capture)
        stop_btn.clicked.connect(self.stop_capture)
        clear_samples_btn.clicked.connect(self.clear_samples)
        show_freq_btn.clicked.connect(self._show_spectrum_window)
        reset_range_btn.clicked.connect(self._reset_analysis_range_to_full_capture)
        reset_time_zoom_btn.clicked.connect(self.time_plot.reset_zoom)
        save_session_btn.clicked.connect(self.save_session)
        load_session_btn.clicked.connect(self.load_session)
        save_time_plot_btn.clicked.connect(lambda: self._save_plot_image(self.time_plot, "time"))
        save_fft_plot_btn.clicked.connect(lambda: self._save_plot_image(self.spectrum_window.plot_widget, "fft"))
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._spectrum_window_shown_once:
            self._spectrum_window_shown_once = True
            self._show_spectrum_window()

    def closeEvent(self, event):
        self.stop_capture()
        self.spectrum_window.close()
        super().closeEvent(event)

    def _set_backend_text(self, text):
        self.backend_label.setText(str(text or ""))

    def _on_timeout_changed(self, value):
        self.source.set_timeout(value)

    def _reset_open_app_combo(self):
        self.open_app_combo.blockSignals(True)
        self.open_app_combo.setCurrentIndex(0)
        self.open_app_combo.blockSignals(False)

    def _on_open_app_selected(self, index):
        action = str(self.open_app_combo.itemData(index) or "")
        try:
            if action == "daq":
                self._open_daq_window()
            elif action == "stream":
                self._open_stream_window()
            elif action == "axis":
                self._open_script_window("start_axis.sh", "axis cfg")
            elif action == "controller":
                self._open_script_window("start_cntrl.sh", "cntrl cfg")
            elif action == "motion":
                self._open_script_window("start_mtn.sh", "motion")
            elif action == "iso230":
                self._open_script_window("start_iso230.sh", "iso230")
            elif action == "caqtdm_main":
                self._open_caqtdm_main_panel()
        finally:
            self._reset_open_app_combo()

    def _current_prefix(self):
        return self.prefix_edit.text().strip() or self._default_prefix or "IOC:ECMC"

    def _open_script_window(self, script_name, label, extra_args=None):
        script = Path(__file__).with_name(script_name)
        if not script.exists():
            self._log(f"Launcher not found: {script.name}")
            return False
        cmd = ["bash", str(script), str(self._current_prefix())]
        for arg in extra_args or []:
            text = str(arg or "").strip()
            if text:
                cmd.append(text)
        try:
            subprocess.Popen(
                cmd,
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started {label} window")
            return True
        except Exception as ex:
            self._log(f"Failed to start {label} window: {ex}")
            return False

    def _open_stream_window(self):
        return self._open_script_window("start.sh", "stream")

    def _open_daq_window(self):
        return self._open_script_window("start_daq.sh", "daq", extra_args=self._selected_pvs())

    def _open_caqtdm_main_panel(self):
        ioc_prefix = self._current_prefix()
        macro = f"IOC={ioc_prefix}"
        try:
            cmd = f'caqtdm -macro "{macro}" ecmcMain.ui'
            subprocess.Popen(
                ["bash", "-lc", cmd],
                cwd=str(Path(__file__).resolve().parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started caQtDM main panel ({macro})")
        except Exception as ex:
            self._log(f"Failed to start caQtDM main panel: {ex}")

    def _toggle_log_panel(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_toggle_btn.setText("Hide Log" if visible else "Show Log")

    def _on_poll_interval_changed(self, value):
        self.source.set_fallback_poll_ms(value)

    def _on_sample_goal_changed(self, value):
        self._capture_goal = int(value)

    def _on_analysis_settings_changed(self, _checked=False):
        self._refresh_views()

    def _set_analysis_range(self, start_s, end_s, *, from_plot=False):
        start = max(0.0, float(start_s or 0.0))
        end = max(start, float(end_s or start))
        self._analysis_start_s = start
        self._analysis_end_s = end
        self._analysis_range_auto = False
        self.range_start_spin.blockSignals(True)
        self.range_end_spin.blockSignals(True)
        self.range_start_spin.setValue(start)
        self.range_end_spin.setValue(end)
        self.range_start_spin.blockSignals(False)
        self.range_end_spin.blockSignals(False)
        if not from_plot:
            self.time_plot.set_selection_range(start, end)
        self._refresh_views()

    def _analysis_controls_span(self):
        max_span = 0.0
        for samples in self._samples_by_pv.values():
            clean = _sanitize_samples(samples)
            if len(clean) >= 2:
                max_span = max(max_span, float(clean[-1][0]) - float(clean[0][0]))
        return max_span

    def _sync_analysis_range_controls(self, *, keep_manual=True):
        max_span = max(0.0, self._analysis_controls_span())
        self.range_start_spin.blockSignals(True)
        self.range_end_spin.blockSignals(True)
        self.range_start_spin.setRange(0.0, max(0.0, max_span))
        self.range_end_spin.setRange(0.0, max(0.0, max_span))
        self.range_start_spin.blockSignals(False)
        self.range_end_spin.blockSignals(False)
        if max_span <= 0.0:
            self._analysis_start_s = 0.0
            self._analysis_end_s = 0.0
            self.time_plot.set_selection_range(0.0, 0.0)
            self.range_start_spin.blockSignals(True)
            self.range_end_spin.blockSignals(True)
            self.range_start_spin.setValue(0.0)
            self.range_end_spin.setValue(0.0)
            self.range_start_spin.blockSignals(False)
            self.range_end_spin.blockSignals(False)
            self._analysis_range_auto = True
            return
        if self._analysis_range_auto or not keep_manual:
            self._analysis_start_s = 0.0
            self._analysis_end_s = max_span
        else:
            self._analysis_start_s = min(self._analysis_start_s, max_span)
            self._analysis_end_s = min(max(self._analysis_end_s, self._analysis_start_s), max_span)
        self.range_start_spin.blockSignals(True)
        self.range_end_spin.blockSignals(True)
        self.range_start_spin.setValue(self._analysis_start_s)
        self.range_end_spin.setValue(self._analysis_end_s)
        self.range_start_spin.blockSignals(False)
        self.range_end_spin.blockSignals(False)
        self.time_plot.set_selection_range(self._analysis_start_s, self._analysis_end_s)

    def _reset_analysis_range_to_full_capture(self):
        self._analysis_range_auto = True
        self._sync_analysis_range_controls(keep_manual=False)
        self._refresh_views()

    def _on_analysis_range_spin_changed(self, _value):
        self._set_analysis_range(self.range_start_spin.value(), self.range_end_spin.value())

    def _on_plot_range_changed(self, start_s, end_s):
        self._set_analysis_range(start_s, end_s, from_plot=True)

    def _resolved_pv_name(self, text):
        raw = str(text or "").strip()
        if not raw:
            return ""
        prefix = self.prefix_edit.text().strip()
        if prefix and ":" not in raw:
            return _join_prefix_pv(prefix, raw)
        return raw

    def _selected_pvs(self):
        return [self.pv_list.item(idx).text().strip() for idx in range(self.pv_list.count()) if self.pv_list.item(idx).text().strip()]

    def _session_payload(self):
        return {
            "version": 1,
            "prefix": self.prefix_edit.text().strip(),
            "timeout_s": float(self.timeout_spin.value()),
            "fallback_poll_ms": int(self.poll_interval_spin.value()),
            "sample_goal": int(self.sample_count_spin.value()),
            "pvs": self._selected_pvs(),
            "capture_start_ts": None if self._capture_start_ts is None else float(self._capture_start_ts),
            "samples_by_pv": {
                str(name): [[float(ts), float(val)] for ts, val in (self._samples_by_pv.get(name) or [])]
                for name in self._selected_pvs()
            },
            "analysis": {
                "remove_mean": bool(self.remove_mean_chk.isChecked()),
                "detrend": bool(self.detrend_chk.isChecked()),
                "use_delta": bool(self.use_delta_chk.isChecked()),
                "start_s": float(self._analysis_start_s),
                "end_s": float(self._analysis_end_s),
                "range_auto": bool(self._analysis_range_auto),
            },
            "plots": {
                "time_view": self.time_plot.view_state(),
                "fft_view": self.spectrum_window.plot_widget.view_state(),
            },
        }

    def _apply_session_payload(self, payload):
        data = dict(payload or {})
        self.stop_capture()

        prefix = str(data.get("prefix", ""))
        timeout_s = float(data.get("timeout_s", self.timeout_spin.value()))
        fallback_poll_ms = int(data.get("fallback_poll_ms", self.poll_interval_spin.value()))
        sample_goal = int(data.get("sample_goal", self.sample_count_spin.value()))
        pvs = [str(pv or "").strip() for pv in (data.get("pvs") or []) if str(pv or "").strip()]
        samples_by_pv = dict(data.get("samples_by_pv") or {})
        analysis = dict(data.get("analysis") or {})
        plots = dict(data.get("plots") or {})

        self.prefix_edit.setText(prefix)
        self.timeout_spin.setValue(timeout_s)
        self.poll_interval_spin.setValue(fallback_poll_ms)
        self.sample_count_spin.setValue(sample_goal)

        self.remove_mean_chk.blockSignals(True)
        self.detrend_chk.blockSignals(True)
        self.use_delta_chk.blockSignals(True)
        self.remove_mean_chk.setChecked(bool(analysis.get("remove_mean", True)))
        self.detrend_chk.setChecked(bool(analysis.get("detrend", False)))
        self.use_delta_chk.setChecked(bool(analysis.get("use_delta", False)))
        self.remove_mean_chk.blockSignals(False)
        self.detrend_chk.blockSignals(False)
        self.use_delta_chk.blockSignals(False)

        self.pv_list.clear()
        self._samples_by_pv = {}
        self._analysis_by_pv = {}
        for pv in pvs:
            self.pv_list.addItem(pv)
            rows = []
            for pair in samples_by_pv.get(pv, []) or []:
                try:
                    ts, val = pair
                    rows.append((float(ts), float(val)))
                except Exception:
                    continue
            self._samples_by_pv[pv] = rows
            self._analysis_by_pv[pv] = {}

        capture_start = data.get("capture_start_ts")
        self._capture_start_ts = None if capture_start is None else float(capture_start)
        self._analysis_start_s = max(0.0, float(analysis.get("start_s", 0.0)))
        self._analysis_end_s = max(self._analysis_start_s, float(analysis.get("end_s", self._analysis_start_s)))
        self._analysis_range_auto = bool(analysis.get("range_auto", False))
        self.time_plot.set_view_state(plots.get("time_view"))
        self.spectrum_window.plot_widget.set_view_state(plots.get("fft_view"))
        self._sync_analysis_range_controls(keep_manual=not self._analysis_range_auto)
        self._refresh_views()

    def save_session(self):
        suggested = Path.home() / "fft_capture.json"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save FFT Session",
            str(suggested),
            "FFT Session (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            payload = self._session_payload()
            out_path = Path(path)
            out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
            self._last_session_dir = out_path.parent
            self._log(f"Saved FFT session: {out_path}")
        except Exception as ex:
            self._log(f"Failed to save FFT session: {ex}")

    def load_session(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load FFT Session",
            str(self._last_session_dir),
            "FFT Session (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            in_path = Path(path)
            payload = json.loads(in_path.read_text())
            self._apply_session_payload(payload)
            self._last_session_dir = in_path.parent
            self._log(f"Loaded FFT session: {in_path}")
        except Exception as ex:
            self._log(f"Failed to load FFT session: {ex}")

    def _save_plot_image(self, widget, kind):
        suffix = "time" if str(kind) == "time" else "fft"
        suggested = Path.home() / f"fft_{suffix}.png"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            f"Save {suffix.upper()} Plot",
            str(suggested),
            "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg);;BMP Image (*.bmp);;All Files (*)",
        )
        if not path:
            return
        try:
            out_path = Path(path)
            pixmap = widget.grab()
            if pixmap.isNull():
                raise RuntimeError("Could not capture plot image")
            if not pixmap.save(str(out_path)):
                raise RuntimeError("Qt image save failed")
            self._last_image_dir = out_path.parent
            self._log(f"Saved {suffix} plot image: {out_path}")
        except Exception as ex:
            self._log(f"Failed to save {suffix} plot image: {ex}")

    def _add_pv(self, pv_name, log=True):
        name = self._resolved_pv_name(pv_name)
        if not name:
            return
        existing = {self.pv_list.item(idx).text().strip() for idx in range(self.pv_list.count())}
        if name in existing:
            return
        self.pv_list.addItem(name)
        self._samples_by_pv.setdefault(name, [])
        self._analysis_by_pv.setdefault(name, {})
        if log:
            self._log(f"Added PV: {name}")
        self._refresh_views()

    def _add_pv_from_editor(self):
        self._add_pv(self.pv_edit.text().strip())
        self.pv_edit.clear()

    def _remove_selected_pvs(self):
        selected = self.pv_list.selectedItems()
        if not selected:
            return
        removed = []
        for item in selected:
            removed.append(item.text().strip())
            self.pv_list.takeItem(self.pv_list.row(item))
        for name in removed:
            self._samples_by_pv.pop(name, None)
            self._analysis_by_pv.pop(name, None)
        self._log(f"Removed {len(removed)} PV(s)")
        self._refresh_views()

    def _clear_pvs(self):
        if self._capture_active:
            self.stop_capture()
        self.pv_list.clear()
        self._samples_by_pv.clear()
        self._analysis_by_pv.clear()
        self._analysis_range_auto = True
        self._log("Cleared PV list")
        self._refresh_views()

    def clear_samples(self):
        self._capture_start_ts = None
        for name in self._selected_pvs():
            self._samples_by_pv[name] = []
            self._analysis_by_pv[name] = {}
        self._analysis_range_auto = True
        self._log("Cleared captured samples")
        self._refresh_views()

    def start_capture(self):
        pvs = self._selected_pvs()
        if not pvs:
            self._log("No PVs selected")
            return
        self._capture_goal = int(self.sample_count_spin.value())
        self._capture_start_ts = None
        self._capture_active = True
        self._analysis_range_auto = True
        self._refresh_timer.start()
        for name in pvs:
            self._samples_by_pv[name] = []
            self._analysis_by_pv[name] = {}
        self.source.start(pvs)
        self._update_capture_status()
        self._log(f"Capture started for {len(pvs)} PV(s), target {self._capture_goal} samples per PV")

    def stop_capture(self):
        if not self._capture_active and not self._refresh_timer.isActive():
            return
        self._capture_active = False
        self.source.stop()
        self._refresh_timer.stop()
        self._refresh_views()
        self._update_capture_status()

    def _show_spectrum_window(self):
        if self.spectrum_window.isHidden():
            geo = self.geometry()
            self.spectrum_window.move(geo.right() + 24, geo.top())
        self.spectrum_window.show()
        self.spectrum_window.raise_()
        self.spectrum_window.activateWindow()

    def _on_sample_ready(self, pv_name, timestamp_s, value):
        name = str(pv_name or "").strip()
        if not self._capture_active or not name:
            return
        if name not in self._samples_by_pv:
            return
        samples = self._samples_by_pv.setdefault(name, [])
        if len(samples) >= self._capture_goal:
            return
        ts = float(timestamp_s)
        if not math.isfinite(ts):
            ts = time.time()
        val = float(value)
        samples.append((ts, val))
        if self._capture_start_ts is None:
            self._capture_start_ts = ts
        self._pending_refresh = True
        if all(len(self._samples_by_pv.get(pv, [])) >= self._capture_goal for pv in self._selected_pvs()):
            self._log("Capture completed")
            self.stop_capture()

    def _refresh_views(self):
        if not self._pending_refresh and self._capture_active:
            self._update_capture_status()
            return
        self._pending_refresh = False

        time_series = {}
        freq_series = {}
        summary_parts = []
        selected = self._selected_pvs()
        self._sync_analysis_range_controls()
        for pv in selected:
            samples = list(self._samples_by_pv.get(pv, []) or [])
            if samples and self._capture_start_ts is None:
                self._capture_start_ts = samples[0][0]
            if samples:
                origin = self._capture_start_ts if self._capture_start_ts is not None else samples[0][0]
                time_series[pv] = [(max(0.0, float(ts) - float(origin)), float(val)) for ts, val in samples]
            analysis = _analyze_signal(
                samples,
                origin_ts=self._capture_start_ts,
                analysis_start_s=self._analysis_start_s,
                analysis_end_s=self._analysis_end_s,
                remove_mean=self.remove_mean_chk.isChecked(),
                detrend=self.detrend_chk.isChecked(),
                use_delta=self.use_delta_chk.isChecked(),
            )
            self._analysis_by_pv[pv] = analysis
            if analysis.get("spectrum"):
                freq_series[pv] = analysis["spectrum"]
            if analysis.get("peak_freq_hz") is not None:
                summary_parts.append(
                    f"{pv}: peak {compact_float_text(analysis['peak_freq_hz'])} Hz @ {compact_float_text(analysis['peak_amplitude'])}"
                )

        self.time_plot.set_series(time_series)
        self.spectrum_window.set_plots(time_series, freq_series, " | ".join(summary_parts))
        self._reload_stats_table(selected)
        self._update_capture_status()

    def _reload_stats_table(self, pvs):
        self.stats_table.setRowCount(len(pvs))
        for row_idx, pv in enumerate(pvs):
            samples = list(self._samples_by_pv.get(pv, []) or [])
            analysis = dict(self._analysis_by_pv.get(pv, {}) or {})
            span_s = analysis.get("span_s")
            fs_hz = analysis.get("sample_rate_hz")
            fft_size = analysis.get("fft_size") or 0
            peak_hz = analysis.get("peak_freq_hz")
            peak_amp = analysis.get("peak_amplitude")
            last_value = samples[-1][1] if samples else None
            values = [
                pv,
                str(len(samples)),
                compact_float_text(span_s) if span_s is not None else "",
                compact_float_text(fs_hz) if fs_hz is not None else "",
                str(fft_size) if fft_size else "",
                compact_float_text(peak_hz) if peak_hz is not None else "",
                compact_float_text(peak_amp) if peak_amp is not None else "",
                compact_float_text(last_value) if last_value is not None else "",
            ]
            for column, text in enumerate(values):
                item = QtWidgets.QTableWidgetItem(text)
                if column > 0:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.stats_table.setItem(row_idx, column, item)

    def _update_capture_status(self):
        selected = self._selected_pvs()
        if not selected:
            self.capture_status.setText("No PVs selected")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            return
        counts = [len(self._samples_by_pv.get(pv, []) or []) for pv in selected]
        if self._capture_active:
            self.capture_status.setText(
                f"Capturing: min {min(counts)} / {self._capture_goal}, max {max(counts)} / {self._capture_goal}"
            )
        else:
            self.capture_status.setText(
                f"Idle: min {min(counts)} / {self._capture_goal}, max {max(counts)} / {self._capture_goal}"
            )
        self.start_btn.setEnabled(not self._capture_active)
        self.stop_btn.setEnabled(self._capture_active)

    def _log(self, message):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")


def main():
    ap = argparse.ArgumentParser(description="Qt app for timestamp-derived DAQ and FFT analysis of EPICS PVs")
    ap.add_argument("--prefix", default="", help="Optional default IOC prefix for short PV names")
    ap.add_argument("--pv", action="append", default=[], help="Initial PV name (can be passed multiple times)")
    ap.add_argument("--timeout", type=float, default=2.0, help="EPICS timeout in seconds")
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    window = DaqWindow(default_prefix=args.prefix, initial_pvs=args.pv, timeout=args.timeout)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
