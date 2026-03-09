#!/usr/bin/env python3
import argparse
import csv
import html
import json
import math
import random
import re
import tempfile
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from qt_compat import QSvgWidget, QtCore, QtGui, QtWidgets

from ecmc_mtn_qt import (
    _MotionPvMixin,
    _normalize_axis_object_id,
    _normalize_axis_type_text,
    _to_float,
    _truthy_pv,
)
from ecmc_stream_qt import EpicsClient, _join_prefix_pv, compact_float_text

_FORMAT_DECIMALS = 5
_MAX_REFERENCE_PVS = 5
_UI_SCALE = 0.7
APP_LAUNCH_PLACEHOLDER = "Open app..."
APP_LAUNCH_ISO230 = "New ISO230 App"
APP_LAUNCH_AXIS = "Axis Cfg App"
APP_LAUNCH_CONTROLLER = "Cntrl Cfg App"
APP_LAUNCH_MOTION = "Motion App"
APP_LAUNCH_FFT = "FFT App"
APP_LAUNCH_CAQTDM_MAIN = "caqtdm Main"
APP_LAUNCH_CAQTDM_AXIS = "caqtdm Axis"


def _scaled_px(value):
    return max(1, int(round(float(value) * _UI_SCALE)))


def _mean(values):
    vals = [float(v) for v in values]
    if not vals:
        return None
    return math.fsum(vals) / float(len(vals))


def _stddev(values):
    vals = [float(v) for v in values]
    if len(vals) < 2:
        return 0.0 if vals else None
    mu = _mean(vals)
    if mu is None:
        return None
    var = math.fsum((v - mu) * (v - mu) for v in vals) / float(len(vals) - 1)
    return math.sqrt(max(0.0, var))


def _format_duration(seconds):
    total = max(0, int(round(float(seconds or 0.0))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _set_format_decimals(decimals):
    global _FORMAT_DECIMALS
    _FORMAT_DECIMALS = max(0, int(decimals))


def _auto_iso230_target_count(span):
    s = abs(float(span))
    if s <= 2000.0:
        return max(5, int(math.ceil((5.0 * s) / 1000.0)))
    return max(2, int(round(s / 250.0)) + 1)


def _generate_iso230_targets(range_min, range_max, requested_count=0):
    lo = float(range_min)
    hi = float(range_max)
    if hi <= lo:
        raise ValueError("Range Max must be greater than Range Min")

    span = hi - lo
    count = int(requested_count or 0)
    if count <= 0:
        count = _auto_iso230_target_count(span)
    count = max(2, count)
    if count == 2:
        return [lo, hi], {
            "count": 2,
            "mode": "two-point",
            "base_interval": span,
            "rule_note": "Endpoints only",
        }

    if span <= 2000.0:
        mode = "iso-short-travel"
        rule_note = "ISO-style non-uniform targets: minimum five random target positions per metre"
        jitter_fraction = 0.30
        min_gap_fraction = 0.35
    else:
        mode = "iso-long-travel"
        rule_note = "ISO-style non-uniform targets: random positions with average interval about 250 mm"
        jitter_fraction = 0.45
        min_gap_fraction = 0.25

    base_interval = span / float(count - 1)
    rng = random.Random(f"iso230|{lo:.9f}|{hi:.9f}|{count}|{mode}")
    points = [lo]
    prev = lo
    for idx in range(1, count - 1):
        nominal = lo + float(idx) * base_interval
        remaining_after = (count - 1) - idx
        min_gap = min_gap_fraction * base_interval
        min_allowed = prev + min_gap
        max_allowed = hi - (remaining_after * min_gap)
        lower = max(min_allowed, nominal - (jitter_fraction * base_interval))
        upper = min(max_allowed, nominal + (jitter_fraction * base_interval))
        if upper <= lower:
            chosen = 0.5 * (min_allowed + max_allowed)
        else:
            chosen = rng.uniform(lower, upper)
        points.append(chosen)
        prev = chosen
    points.append(hi)
    return points, {
        "count": count,
        "mode": mode,
        "base_interval": base_interval,
        "rule_note": rule_note,
    }


def _fmt(value, decimals=None):
    if value is None:
        return ""
    num = _float_or_none(value)
    if num is None:
        return str(value)
    dec = _FORMAT_DECIMALS if decimals is None else max(0, int(decimals))
    return f"{float(num):.{dec}f}"


def _fmt_preview(value):
    return _fmt(value)


def _float_or_none(text):
    try:
        return float(str(text).strip())
    except Exception:
        return None


def _table_item(text, *, align_right=False):
    item = QtWidgets.QTableWidgetItem(str(text))
    if align_right:
        item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
    return item


def _float_key(value):
    return f"{float(value):.12g}"


def _demo_settings():
    targets, target_meta = _generate_iso230_targets(0.0, 1600.0, 0)
    reference_pvs = ["SIM:LASER:MEAS", "", "", "", ""]
    return {
        "prefix": "DEMO:ECMC",
        "axis_id": "7",
        "motor": "DEMO:AXIS7",
        "reference_pvs": reference_pvs,
        "reference_slot": 0,
        "reference_pv": reference_pvs[0],
        "range_min": targets[0],
        "range_max": targets[-1],
        "span": targets[-1] - targets[0],
        "targets": targets,
        "target_count": len(targets),
        "target_mode": target_meta.get("mode"),
        "target_rule_note": target_meta.get("rule_note"),
        "base_interval": target_meta.get("base_interval"),
        "reversal_margin": 80.0,
        "cycles": 5,
        "settle_s": 0.0,
        "samples_per_point": 5,
        "sample_interval_ms": 150,
        "velo": 25.0,
        "accl": 80.0,
        "accs": 120.0,
        "vmax": 40.0,
        "display_decimals": _FORMAT_DECIMALS,
    }


def _build_demo_measurements(settings, seed=2302):
    rng = random.Random(int(seed))
    rows = []
    span = float(settings["span"]) or 1.0
    mid = 0.5 * (float(settings["range_min"]) + float(settings["range_max"]))
    now = datetime.now().replace(microsecond=0)
    index = 0
    for cycle in range(1, int(settings["cycles"]) + 1):
        drift = 0.00012 * (cycle - 1)
        for direction in ("forward", "reverse"):
            targets = settings["targets"] if direction == "forward" else list(reversed(settings["targets"]))
            for target in targets:
                norm = (float(target) - mid) / max(span * 0.5, 1e-9)
                systematic = (
                    -0.0014
                    + 0.0026 * norm
                    + 0.0013 * (norm ** 2)
                    - 0.0007 * (norm ** 3)
                )
                reversal = 0.0034 - 0.0011 * abs(norm)
                direction_offset = (-0.5 * reversal) if direction == "forward" else (0.5 * reversal)
                local_noise = rng.gauss(0.0, 0.00022)
                reference_mean = float(target) + systematic + direction_offset + drift + local_noise
                rbv_bias = rng.gauss(0.0, 0.00035) + 0.00025 * norm
                rbv_mean = reference_mean + rbv_bias
                ref_std = 0.00018 + abs(rng.gauss(0.0, 0.00005))
                rbv_std = 0.00023 + abs(rng.gauss(0.0, 0.00007))
                reference_stats = {
                    0: {
                        "slot": 0,
                        "pv": settings.get("reference_pv", ""),
                        "mean": reference_mean,
                        "std": ref_std,
                        "error": reference_mean - float(target),
                    }
                }
                rows.append(
                    {
                        "cycle": cycle,
                        "direction": direction,
                        "target": float(target),
                        "reference_stats": reference_stats,
                        "reference_slot": 0,
                        "reference_pv": settings.get("reference_pv", ""),
                        "reference_mean": reference_mean,
                        "reference_std": ref_std,
                        "rbv_mean": rbv_mean,
                        "rbv_std": rbv_std,
                        "command_mean": float(target),
                        "ref_error": reference_mean - float(target),
                        "rbv_error": rbv_mean - float(target),
                        "timestamp": now + timedelta(seconds=3 * index),
                    }
                )
                index += 1
    return rows


def _write_demo_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        headers = [
            "cycle",
            "direction",
            "target",
            "selected_reference_slot",
            "selected_reference_pv",
            "reference_mean",
            "reference_std",
            "rbv_mean",
            "rbv_std",
            "command_mean",
            "ref_error",
            "rbv_error",
        ]
        for idx in range(_MAX_REFERENCE_PVS):
            prefix = f"ref{idx + 1}"
            headers.extend([f"{prefix}_pv", f"{prefix}_mean", f"{prefix}_std", f"{prefix}_error"])
        headers.append("timestamp")
        writer.writerow(headers)
        for row in rows:
            record = [
                row["cycle"],
                row["direction"],
                row["target"],
                row.get("reference_slot"),
                row.get("reference_pv"),
                row["reference_mean"],
                row["reference_std"],
                row["rbv_mean"],
                row["rbv_std"],
                row["command_mean"],
                row["ref_error"],
                row["rbv_error"],
            ]
            stats = dict(row.get("reference_stats") or {})
            for idx in range(_MAX_REFERENCE_PVS):
                stat = stats.get(idx, {})
                record.extend([stat.get("pv", ""), stat.get("mean"), stat.get("std"), stat.get("error")])
            record.append(row["timestamp"].isoformat())
            writer.writerow(
                record
            )


def _settings_reference_pvs(settings):
    pvs = list(settings.get("reference_pvs") or [])
    if not pvs and settings.get("reference_pv"):
        pvs = [settings.get("reference_pv")]
    while len(pvs) < _MAX_REFERENCE_PVS:
        pvs.append("")
    return pvs[:_MAX_REFERENCE_PVS]


def _reference_pv_summary_text(settings):
    pvs = [pv for pv in _settings_reference_pvs(settings) if pv]
    if not pvs:
        return ""
    return ", ".join(f"Ref {idx + 1}={pv}" for idx, pv in enumerate(pvs))


def _nonselected_reference_slots(settings):
    selected_slot = settings.get("reference_slot")
    try:
        selected_slot = None if selected_slot is None else int(selected_slot)
    except Exception:
        selected_slot = None
    out = []
    for idx, pv in enumerate(_settings_reference_pvs(settings)):
        if not pv or idx == selected_slot:
            continue
        out.append((idx, pv))
    return out


def _serialize_reference_stats(stats):
    out = {}
    for key, value in dict(stats or {}).items():
        try:
            slot = int(key)
        except Exception:
            continue
        out[str(slot)] = {
            "slot": value.get("slot"),
            "pv": value.get("pv", ""),
            "mean": value.get("mean"),
            "std": value.get("std"),
            "error": value.get("error"),
        }
    return out


def _deserialize_reference_stats(stats):
    out = {}
    for key, value in dict(stats or {}).items():
        try:
            slot = int(key)
        except Exception:
            continue
        out[slot] = {
            "slot": value.get("slot"),
            "pv": value.get("pv", ""),
            "mean": value.get("mean"),
            "std": value.get("std"),
            "error": value.get("error"),
        }
    return out


def _parse_saved_timestamp(text):
    s = str(text or "").strip()
    if not s:
        return datetime.now().replace(microsecond=0)
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f")
    except Exception:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        pass
    try:
        return datetime.strptime(s.replace("Z", ""), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().replace(microsecond=0)


class _TargetSweepSchematic(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = None
        self._message = "Enter range and approach margin to visualize the sweep."
        self._live_actual = None
        self._live_target = None
        self._live_phase = ""
        self.setMinimumHeight(220)

    def sizeHint(self):
        return QtCore.QSize(_scaled_px(640), 220)

    def set_preview(self, settings=None, message=""):
        self._settings = dict(settings or {}) if settings else None
        self._message = str(message or "")
        self.update()

    def set_live_state(self, actual=None, target=None, phase=""):
        self._live_actual = actual
        self._live_target = target
        self._live_phase = str(phase or "")
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(rect, QtGui.QColor("#f8fafc"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#cbd5e1"), 1.0))
        painter.drawRoundedRect(rect, 8, 8)

        if not self._settings:
            painter.setPen(QtGui.QColor("#64748b"))
            painter.drawText(rect.adjusted(12, 12, -12, -12), QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, self._message)
            return

        settings = self._settings
        lo = float(settings.get("range_min") or 0.0)
        hi = float(settings.get("range_max") or 0.0)
        margin = float(settings.get("reversal_margin") or 0.0)
        targets = [float(v) for v in settings.get("targets", [])]
        full_lo = lo - margin
        full_hi = hi + margin
        if full_hi <= full_lo:
            painter.setPen(QtGui.QColor("#b91c1c"))
            painter.drawText(rect.adjusted(12, 12, -12, -12), QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, "Unable to draw sweep preview.")
            return

        left = rect.left() + 36
        right = rect.right() - 20
        top = rect.top() + 12
        bar_y = rect.center().y() - 6
        tested_top = bar_y - 16
        tested_bottom = bar_y + 16
        target_top = bar_y - 30

        def map_x(value):
            return left + ((float(value) - full_lo) / (full_hi - full_lo)) * max(1.0, (right - left))

        x_lo = map_x(lo)
        x_hi = map_x(hi)
        x_full_lo = map_x(full_lo)
        x_full_hi = map_x(full_hi)

        painter.fillRect(QtCore.QRectF(x_full_lo, tested_top, max(1.0, x_lo - x_full_lo), tested_bottom - tested_top), QtGui.QColor("#fee2e2"))
        painter.fillRect(QtCore.QRectF(x_lo, tested_top, max(1.0, x_hi - x_lo), tested_bottom - tested_top), QtGui.QColor("#dbeafe"))
        painter.fillRect(QtCore.QRectF(x_hi, tested_top, max(1.0, x_full_hi - x_hi), tested_bottom - tested_top), QtGui.QColor("#fee2e2"))

        painter.setPen(QtGui.QPen(QtGui.QColor("#475569"), 2.0))
        painter.drawLine(QtCore.QPointF(x_full_lo, bar_y), QtCore.QPointF(x_full_hi, bar_y))
        painter.setPen(QtGui.QPen(QtGui.QColor("#0f172a"), 2.4))
        painter.drawLine(QtCore.QPointF(x_lo, bar_y), QtCore.QPointF(x_hi, bar_y))

        painter.setPen(QtGui.QPen(QtGui.QColor("#94a3b8"), 1.0, QtCore.Qt.DashLine))
        painter.drawLine(QtCore.QPointF(x_lo, top + 8), QtCore.QPointF(x_lo, rect.bottom() - 56))
        painter.drawLine(QtCore.QPointF(x_hi, top + 8), QtCore.QPointF(x_hi, rect.bottom() - 56))

        target_pen = QtGui.QPen(QtGui.QColor("#2563eb"), 1.8)
        painter.setPen(target_pen)
        painter.setBrush(QtGui.QColor("#2563eb"))
        label_positions = []
        for idx, target in enumerate(targets):
            x = map_x(target)
            painter.drawLine(QtCore.QPointF(x, target_top), QtCore.QPointF(x, tested_bottom + 8))
            painter.drawEllipse(QtCore.QPointF(x, bar_y), 3.2, 3.2)
            label_positions.append((x, target, idx))

        painter.setPen(QtGui.QPen(QtGui.QColor("#dc2626"), 1.8))
        painter.setBrush(QtGui.QColor("#ffffff"))
        for x in (x_full_lo, x_full_hi):
            painter.drawEllipse(QtCore.QPointF(x, bar_y), 4.0, 4.0)

        live_actual = None
        try:
            if self._live_actual is not None:
                live_actual = float(self._live_actual)
        except Exception:
            live_actual = None
        live_target = None
        try:
            if self._live_target is not None:
                live_target = float(self._live_target)
        except Exception:
            live_target = None

        if live_target is not None:
            xt = map_x(max(full_lo, min(full_hi, live_target)))
            painter.setPen(QtGui.QPen(QtGui.QColor("#7c3aed"), 2.0, QtCore.Qt.DashLine))
            painter.drawLine(QtCore.QPointF(xt, top + 10), QtCore.QPointF(xt, rect.bottom() - 56))
            painter.setPen(QtGui.QPen(QtGui.QColor("#7c3aed"), 1.8))
            painter.setBrush(QtGui.QColor("#ede9fe"))
            painter.drawEllipse(QtCore.QPointF(xt, bar_y), 5.0, 5.0)

        if live_actual is not None:
            xa = map_x(max(full_lo, min(full_hi, live_actual)))
            phase = str(self._live_phase or "").lower()
            live_color = QtGui.QColor("#0f766e")
            if "settl" in phase:
                live_color = QtGui.QColor("#d97706")
            elif "sampl" in phase:
                live_color = QtGui.QColor("#2563eb")
            elif "abort" in phase or "error" in phase:
                live_color = QtGui.QColor("#b91c1c")
            painter.setPen(QtGui.QPen(live_color, 2.4))
            painter.drawLine(QtCore.QPointF(xa, top + 6), QtCore.QPointF(xa, rect.bottom() - 52))
            painter.setBrush(live_color)
            painter.drawEllipse(QtCore.QPointF(xa, bar_y), 5.8, 5.8)

        painter.setPen(QtGui.QColor("#1e293b"))
        title_font = painter.font()
        title_font.setPointSize(max(9, title_font.pointSize()))
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(QtCore.QRectF(left, top - 2, right - left, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "Sweep schematic")

        body_font = painter.font()
        body_font.setBold(False)
        body_font.setPointSize(max(8, body_font.pointSize() - 1))
        painter.setFont(body_font)
        painter.setPen(QtGui.QColor("#475569"))
        live_note = ""
        if live_actual is not None:
            live_note = f", teal/orange marker=actual motion ({self._live_phase or 'live'})"
        painter.drawText(
            QtCore.QRectF(left, top + 14, right - left, 16),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            "blue=tested span, red=approach margin, violet=current target" + live_note,
        )

        target_label_font = painter.font()
        target_label_font.setPointSize(max(7, target_label_font.pointSize() - 1))
        painter.setFont(target_label_font)
        painter.setPen(QtGui.QColor("#1d4ed8"))
        for x, target, idx in label_positions:
            label_y = top + 34 + ((idx % 2) * 14)
            painter.drawText(
                QtCore.QRectF(x - 32, label_y, 64, 12),
                QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                _fmt(target),
            )

        range_label_y = rect.bottom() - 48
        margin_label_y = rect.bottom() - 30
        painter.setPen(QtGui.QColor("#0f172a"))
        painter.drawText(QtCore.QRectF(x_lo - 32, range_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(lo)}")
        painter.drawText(QtCore.QRectF(x_hi - 32, range_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(hi)}")
        painter.setPen(QtGui.QColor("#7c2d12"))
        painter.drawText(QtCore.QRectF(x_full_lo - 32, margin_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(full_lo)}")
        painter.drawText(QtCore.QRectF(x_full_hi - 32, margin_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(full_hi)}")



class Iso230Window(_MotionPvMixin, QtWidgets.QMainWindow):
    SAMPLE_INTERVAL_MS = 150

    def __init__(self, prefix, axis_id, timeout, axis_id_was_provided=True):
        super().__init__()
        self._base_title = "ecmc ISO 230 Bidirectional Test"
        self.setWindowTitle(self._base_title)
        self._apply_ui_scale()
        self.resize(_scaled_px(1120), _scaled_px(760))

        self.client = EpicsClient(timeout=timeout)
        self.default_prefix = str(prefix or "").strip()
        self.default_axis_id = str(axis_id or "1").strip() or "1"
        self._axis_id_was_provided = bool(axis_id_was_provided)

        self._axis_combo_updating = False
        self._axis_combo_open_new_instance = False
        self._did_startup_axis_presence_check = False
        self._startup_axis_probe_ok = False

        self._test_timer = QtCore.QTimer(self)
        self._test_timer.setInterval(200)
        self._test_timer.timeout.connect(self._test_tick)

        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(500)
        self._status_timer.timeout.connect(self._periodic_status_tick)

        self._test_active = False
        self._test_plan = []
        self._test_plan_index = -1
        self._current_step = None
        self._current_phase = "idle"
        self._settle_deadline = 0.0
        self._sample_buffer = []
        self._next_sample_at = 0.0
        self._latest_metrics = {}
        self._latest_report_markdown = ""
        self._operator_comments = ""
        self._last_status = {}
        self._measurements = []
        self._test_settings_cache = {}
        self._move_issued_at = 0.0
        self._demo_mode = False
        self._committed_axis_pfx_cfg_pv = ""
        self._committed_motor_name_cfg_pv = ""
        self._committed_motor_record = ""
        self._committed_reference_pvs = [""] * _MAX_REFERENCE_PVS
        self._poll_failure_cache = {}
        self._last_auto_reference_pv = ""
        self._last_auto_reversal_margin = ""
        self._last_auto_range_min = ""
        self._last_auto_range_max = ""
        self._motor_soft_limits = None

        self._build_ui(timeout)
        self._log(f"Connected via backend: {self.client.backend}")
        if getattr(self.client, "backend", None) == "cli":
            self._status_timer.setInterval(900)
            self._log("CLI backend detected: status polling set to 900 ms")
        self._status_timer.start()
        QtCore.QTimer.singleShot(0, self._startup_axis_presence_check)

    def _apply_ui_scale(self):
        font = QtGui.QFont(self.font())
        point_size = font.pointSizeF()
        if point_size <= 0:
            point_size = float(QtGui.QFontInfo(font).pointSize())
        if point_size > 0:
            font.setPointSizeF(max(7.0, point_size * _UI_SCALE))
            self.setFont(font)

    def _build_ui(self, timeout):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        top_row = QtWidgets.QHBoxLayout()
        self.cfg_toggle_btn = QtWidgets.QPushButton("Hide Setup")
        self.log_toggle_btn = QtWidgets.QPushButton("Show Log")
        self.start_btn = QtWidgets.QPushButton("Start ISO 230 Test")
        self.abort_btn = QtWidgets.QPushButton("Abort")
        self.load_demo_btn = QtWidgets.QPushButton("Load Demo Data")
        self.data_btn = QtWidgets.QToolButton()
        self.data_btn.setText("Data")
        self.preview_report_btn = QtWidgets.QPushButton("Preview Report")
        self.export_report_btn = QtWidgets.QPushButton("Save Report (.md)")
        self.export_csv_btn = QtWidgets.QPushButton("Save CSV")
        self.help_btn = QtWidgets.QPushButton("Calc Help")
        self.open_app_combo = QtWidgets.QComboBox()
        self.open_app_combo.setMinimumWidth(_scaled_px(180))
        self.axis_pick_combo = QtWidgets.QComboBox()
        self.axis_pick_combo.setMinimumWidth(_scaled_px(180))
        self.axis_pick_combo.setMaximumWidth(_scaled_px(260))
        top_control_height = 28
        self.open_app_combo.setMinimumHeight(top_control_height)
        self.axis_pick_combo.setMinimumHeight(top_control_height)
        self.data_btn.setMinimumHeight(top_control_height)
        self.open_app_combo.addItem(APP_LAUNCH_PLACEHOLDER, "")
        self.open_app_combo.addItem(APP_LAUNCH_ISO230, "iso230")
        self.open_app_combo.addItem(APP_LAUNCH_AXIS, "axis")
        self.open_app_combo.addItem(APP_LAUNCH_CONTROLLER, "controller")
        self.open_app_combo.addItem(APP_LAUNCH_MOTION, "motion")
        self.open_app_combo.addItem(APP_LAUNCH_FFT, "fft")
        self.open_app_combo.addItem(APP_LAUNCH_CAQTDM_MAIN, "caqtdm_main")
        self.open_app_combo.addItem(APP_LAUNCH_CAQTDM_AXIS, "caqtdm_axis")
        for btn in (
            self.cfg_toggle_btn,
            self.log_toggle_btn,
            self.start_btn,
            self.abort_btn,
            self.load_demo_btn,
            self.preview_report_btn,
            self.export_report_btn,
            self.export_csv_btn,
            self.help_btn,
        ):
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setMinimumHeight(top_control_height)
        data_menu = QtWidgets.QMenu(self.data_btn)
        open_data_action = data_menu.addAction("Open Data...")
        save_data_action = data_menu.addAction("Save Data...")
        open_data_action.triggered.connect(self.load_session_file)
        save_data_action.triggered.connect(self.save_session_file)
        self.data_btn.setMenu(data_menu)
        self.data_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.abort_btn.setEnabled(False)
        self.open_app_combo.activated.connect(self._on_open_app_selected)
        self.axis_pick_combo.activated.connect(self._on_axis_combo_activated)
        self.cfg_toggle_btn.clicked.connect(self._toggle_config_panel)
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)
        self.start_btn.clicked.connect(self.start_test)
        self.abort_btn.clicked.connect(self.abort_test)
        self.load_demo_btn.clicked.connect(self.load_demo_data)
        self.preview_report_btn.clicked.connect(self.preview_report)
        self.export_report_btn.clicked.connect(self.export_report)
        self.export_csv_btn.clicked.connect(self.export_csv)
        self.help_btn.clicked.connect(self._show_calculation_help)
        top_row.addWidget(self.cfg_toggle_btn)
        top_row.addWidget(self.start_btn)
        top_row.addWidget(self.abort_btn)
        top_row.addWidget(self.preview_report_btn)
        top_row.addWidget(self.export_report_btn)
        top_row.addWidget(self.help_btn)
        top_row.addWidget(QtWidgets.QLabel("Launch"))
        top_row.addWidget(self.open_app_combo)
        top_row.addStretch(1)
        axis_col = QtWidgets.QVBoxLayout()
        axis_col.setContentsMargins(0, 0, 0, 0)
        axis_col.setSpacing(2)
        axis_row = QtWidgets.QHBoxLayout()
        axis_row.setContentsMargins(0, 0, 0, 0)
        axis_row.setSpacing(2)
        axis_row.addWidget(QtWidgets.QLabel("Axis"))
        axis_row.addWidget(self.axis_pick_combo)
        axis_col.addLayout(axis_row)
        top_row.addLayout(axis_col)
        layout.addLayout(top_row)

        self.cfg_group = QtWidgets.QGroupBox("Run Setup")
        cfg = QtWidgets.QVBoxLayout(self.cfg_group)
        cfg.setContentsMargins(4, 4, 4, 4)
        cfg.setSpacing(6)

        self.prefix_edit = QtWidgets.QLineEdit(self.default_prefix)
        self.prefix_edit.setMinimumWidth(_scaled_px(180))
        self.axis_edit = QtWidgets.QLineEdit(self.default_axis_id)
        self.axis_edit.setMaximumWidth(_scaled_px(70))
        self.timeout_edit = QtWidgets.QDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(float(timeout))
        self.timeout_edit.setMaximumWidth(_scaled_px(90))
        self.timeout_edit.valueChanged.connect(self._set_timeout)

        self.axis_pfx_cfg_pv_edit = QtWidgets.QLineEdit()
        self.motor_name_cfg_pv_edit = QtWidgets.QLineEdit()
        self.motor_record_edit = QtWidgets.QLineEdit("")
        self.motor_record_edit.setPlaceholderText("Resolved motor record base PV")
        self.reference_pv_edits = []
        self.reference_value_edits = []
        for idx in range(_MAX_REFERENCE_PVS):
            edit = QtWidgets.QComboBox()
            edit.setEditable(True)
            edit.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            edit.setMinimumWidth(_scaled_px(260))
            if idx == 0:
                edit.lineEdit().setPlaceholderText("Defaults to <motor>-PosAct")
            else:
                edit.lineEdit().setPlaceholderText("Optional additional reference PV")
            self.reference_pv_edits.append(edit)
            value_edit = QtWidgets.QLineEdit("")
            value_edit.setReadOnly(True)
            value_edit.setPlaceholderText("value")
            self.reference_value_edits.append(value_edit)
        self.report_reference_combo = QtWidgets.QComboBox()
        self.report_reference_combo.setMinimumWidth(_scaled_px(220))

        self.range_min_edit = QtWidgets.QLineEdit("0")
        self.range_max_edit = QtWidgets.QLineEdit("10")
        self.range_min_edit.setMaximumWidth(_scaled_px(110))
        self.range_max_edit.setMaximumWidth(_scaled_px(110))
        self.target_count_spin = QtWidgets.QSpinBox()
        self.target_count_spin.setRange(0, 41)
        self.target_count_spin.setValue(0)
        self.target_count_spin.setSpecialValueText("Auto (ISO minimum)")
        self.target_count_spin.setMinimumWidth(_scaled_px(120))
        self.reversal_margin_edit = QtWidgets.QLineEdit("")
        self.reversal_margin_edit.setPlaceholderText("Auto (5% of range)")
        self.reversal_margin_edit.setMaximumWidth(_scaled_px(160))
        self.target_schematic = _TargetSweepSchematic()
        self.cycles_spin = QtWidgets.QSpinBox()
        self.cycles_spin.setRange(1, 20)
        self.cycles_spin.setValue(5)
        self.cycles_spin.setMaximumWidth(_scaled_px(80))
        self.settle_spin = QtWidgets.QDoubleSpinBox()
        self.settle_spin.setRange(0.0, 120.0)
        self.settle_spin.setDecimals(2)
        self.settle_spin.setValue(0.0)
        self.settle_spin.setMaximumWidth(_scaled_px(100))
        self.samples_spin = QtWidgets.QSpinBox()
        self.samples_spin.setRange(1, 50)
        self.samples_spin.setValue(5)
        self.samples_spin.setMaximumWidth(_scaled_px(80))
        self.decimals_spin = QtWidgets.QSpinBox()
        self.decimals_spin.setRange(0, 8)
        self.decimals_spin.setValue(_FORMAT_DECIMALS)
        self.decimals_spin.setMaximumWidth(_scaled_px(80))
        self.estimated_duration_value = QtWidgets.QLabel("-")
        self.estimated_duration_value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.motion_velo_edit = QtWidgets.QLineEdit("1")
        self.motion_acc_edit = QtWidgets.QLineEdit("1")
        self.motion_vmax_edit = QtWidgets.QLineEdit("")
        self.motion_accs_edit = QtWidgets.QLineEdit("")
        self.motion_velo_edit.setMinimumWidth(_scaled_px(72))
        self.motion_acc_edit.setMinimumWidth(_scaled_px(72))
        self.motion_vmax_edit.setMinimumWidth(_scaled_px(92))
        self.motion_accs_edit.setMinimumWidth(_scaled_px(92))
        self.motion_velo_edit.setMaximumWidth(_scaled_px(130))
        self.motion_acc_edit.setMaximumWidth(_scaled_px(130))
        self.motion_vmax_edit.setMaximumWidth(_scaled_px(150))
        self.motion_accs_edit.setMaximumWidth(_scaled_px(150))
        self.motion_vmax_edit.setPlaceholderText("optional")
        self.motion_accs_edit.setPlaceholderText("optional")

        axis_apply_btn = QtWidgets.QPushButton("Apply Axis")
        resolve_btn = QtWidgets.QPushButton("Resolve Motor")
        read_status_btn = QtWidgets.QPushButton("Read Status")
        for btn in (axis_apply_btn, resolve_btn, read_status_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setMaximumWidth(_scaled_px(140))
        axis_apply_btn.clicked.connect(self._apply_axis_top)
        resolve_btn.clicked.connect(self.resolve_motor_record_name)
        read_status_btn.clicked.connect(self.refresh_status)

        self._update_cfg_pv_edits()
        self.prefix_edit.editingFinished.connect(self._update_cfg_pv_edits)
        self.axis_edit.editingFinished.connect(self._update_cfg_pv_edits)
        self.axis_pfx_cfg_pv_edit.returnPressed.connect(self._commit_cfg_pv_edits)
        self.motor_name_cfg_pv_edit.returnPressed.connect(self._commit_cfg_pv_edits)
        self.motor_record_edit.returnPressed.connect(self._commit_motor_record_edit)
        for edit in self.reference_pv_edits:
            edit.activated.connect(self._commit_reference_pv_edits)
            if edit.lineEdit() is not None:
                edit.lineEdit().editingFinished.connect(self._commit_reference_pv_edits)
        self.report_reference_combo.currentIndexChanged.connect(self._on_reference_selection_changed)
        self.range_min_edit.editingFinished.connect(self._on_range_inputs_changed)
        self.range_max_edit.editingFinished.connect(self._on_range_inputs_changed)
        self.target_count_spin.valueChanged.connect(self._update_duration_estimate)
        self.reversal_margin_edit.editingFinished.connect(self._update_duration_estimate)
        self.cycles_spin.valueChanged.connect(self._update_duration_estimate)
        self.settle_spin.valueChanged.connect(self._update_duration_estimate)
        self.samples_spin.valueChanged.connect(self._update_duration_estimate)
        self.motion_velo_edit.editingFinished.connect(self._update_duration_estimate)
        self.decimals_spin.valueChanged.connect(self._on_decimals_changed)

        axis_box = QtWidgets.QWidget()
        axis_layout = QtWidgets.QHBoxLayout(axis_box)
        axis_layout.setContentsMargins(4, 4, 4, 4)
        axis_layout.setSpacing(8)

        axis_left = QtWidgets.QWidget()
        axis_grid = QtWidgets.QGridLayout(axis_left)
        axis_grid.setContentsMargins(0, 0, 0, 0)
        axis_grid.setHorizontalSpacing(6)
        axis_grid.setVerticalSpacing(3)
        axis_grid.addWidget(QtWidgets.QLabel("IOC Prefix"), 0, 0)
        axis_grid.addWidget(self.prefix_edit, 0, 1)
        axis_grid.addWidget(QtWidgets.QLabel("Axis ID"), 0, 2)
        axis_grid.addWidget(self.axis_edit, 0, 3)
        axis_grid.addWidget(QtWidgets.QLabel("Timeout [s]"), 0, 4)
        axis_grid.addWidget(self.timeout_edit, 0, 5)
        axis_grid.addWidget(axis_apply_btn, 0, 6)
        axis_grid.addWidget(QtWidgets.QLabel("Axis Prefix PV"), 1, 0)
        axis_grid.addWidget(self.axis_pfx_cfg_pv_edit, 1, 1, 1, 2)
        axis_grid.addWidget(QtWidgets.QLabel("Motor Name PV"), 1, 3)
        axis_grid.addWidget(self.motor_name_cfg_pv_edit, 1, 4, 1, 3)
        axis_grid.addWidget(QtWidgets.QLabel("Motor Record"), 2, 0)
        axis_grid.addWidget(self.motor_record_edit, 2, 1, 1, 4)
        axis_grid.addWidget(resolve_btn, 2, 5)
        axis_grid.addWidget(read_status_btn, 2, 6)
        motion_row = QtWidgets.QHBoxLayout()
        motion_row.setContentsMargins(0, 0, 0, 0)
        motion_row.setSpacing(6)
        motion_row.addWidget(QtWidgets.QLabel("VELO"))
        motion_row.addWidget(self.motion_velo_edit)
        motion_row.addWidget(QtWidgets.QLabel("ACCL"))
        motion_row.addWidget(self.motion_acc_edit)
        motion_row.addWidget(QtWidgets.QLabel("VMAX"))
        motion_row.addWidget(self.motion_vmax_edit)
        motion_row.addWidget(QtWidgets.QLabel("ACCS"))
        motion_row.addWidget(self.motion_accs_edit)
        motion_row.addStretch(1)
        axis_grid.addLayout(motion_row, 3, 0, 1, 7)
        axis_grid.setColumnStretch(1, 2)
        axis_grid.setColumnStretch(4, 3)
        axis_grid.setColumnStretch(6, 1)

        axis_right = QtWidgets.QWidget()
        axis_right_layout = QtWidgets.QVBoxLayout(axis_right)
        axis_right_layout.setContentsMargins(0, 0, 0, 0)
        axis_right_layout.setSpacing(2)
        ref_header = QtWidgets.QLabel("Reference PVs")
        ref_header.setStyleSheet("font-weight: 600;")
        axis_right_layout.addWidget(ref_header)
        ref_panel = QtWidgets.QWidget()
        ref_grid = QtWidgets.QGridLayout(ref_panel)
        ref_grid.setContentsMargins(0, 0, 0, 0)
        ref_grid.setHorizontalSpacing(6)
        ref_grid.setVerticalSpacing(4)
        ref_grid.addWidget(QtWidgets.QLabel("PV"), 0, 1)
        ref_grid.addWidget(QtWidgets.QLabel("Value"), 0, 2)
        for idx, edit in enumerate(self.reference_pv_edits):
            row = idx + 1
            ref_grid.addWidget(QtWidgets.QLabel(f"Ref {idx + 1}"), row, 0)
            ref_grid.addWidget(edit, row, 1)
            ref_grid.addWidget(self.reference_value_edits[idx], row, 2)
        ref_grid.addWidget(QtWidgets.QLabel("Use For Report"), _MAX_REFERENCE_PVS + 1, 0)
        ref_grid.addWidget(self.report_reference_combo, _MAX_REFERENCE_PVS + 1, 1, 1, 2)
        axis_right_layout.addWidget(ref_panel)
        axis_right_layout.addStretch(1)

        axis_layout.addWidget(axis_left, 5)
        axis_layout.addWidget(axis_right, 3)
        plan_box = QtWidgets.QWidget()
        plan_box_layout = QtWidgets.QVBoxLayout(plan_box)
        plan_box_layout.setContentsMargins(6, 6, 6, 6)
        plan_box_layout.setSpacing(6)

        plan_grid = QtWidgets.QGridLayout()
        plan_grid.setContentsMargins(0, 0, 0, 0)
        plan_grid.setHorizontalSpacing(8)
        plan_grid.setVerticalSpacing(4)
        plan_grid.addWidget(QtWidgets.QLabel("Min"), 0, 0)
        plan_grid.addWidget(self.range_min_edit, 0, 1)
        plan_grid.addWidget(QtWidgets.QLabel("Max"), 0, 2)
        plan_grid.addWidget(self.range_max_edit, 0, 3)
        plan_grid.addWidget(QtWidgets.QLabel("Approach margin"), 0, 4)
        plan_grid.addWidget(self.reversal_margin_edit, 0, 5)
        plan_grid.addWidget(QtWidgets.QLabel("Count override"), 0, 6)
        plan_grid.addWidget(self.target_count_spin, 0, 7)
        plan_grid.addWidget(QtWidgets.QLabel("Cycles"), 1, 0)
        plan_grid.addWidget(self.cycles_spin, 1, 1)
        plan_grid.addWidget(QtWidgets.QLabel("Settle [s]"), 1, 2)
        plan_grid.addWidget(self.settle_spin, 1, 3)
        plan_grid.addWidget(QtWidgets.QLabel("Samples / point"), 1, 4)
        plan_grid.addWidget(self.samples_spin, 1, 5)
        plan_grid.addWidget(QtWidgets.QLabel("Decimals"), 1, 6)
        plan_grid.addWidget(self.decimals_spin, 1, 7)
        plan_grid.addWidget(QtWidgets.QLabel("Estimated duration"), 2, 0)
        plan_grid.addWidget(self.estimated_duration_value, 2, 1, 1, 7)
        plan_grid.setColumnStretch(8, 1)

        plan_box_layout.addLayout(plan_grid)
        self.target_schematic.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.target_schematic.setFixedHeight(200)
        plan_box_layout.addWidget(self.target_schematic)

        tools_box = QtWidgets.QWidget()
        tools_layout = QtWidgets.QVBoxLayout(tools_box)
        tools_layout.setContentsMargins(6, 6, 6, 6)
        tools_layout.setSpacing(6)
        tools_grid = QtWidgets.QGridLayout()
        tools_grid.setContentsMargins(0, 0, 0, 0)
        tools_grid.setHorizontalSpacing(8)
        tools_grid.setVerticalSpacing(6)
        tools_grid.addWidget(self.log_toggle_btn, 0, 0)
        tools_grid.addWidget(self.load_demo_btn, 0, 1)
        tools_grid.addWidget(self.data_btn, 1, 0)
        tools_grid.addWidget(self.export_csv_btn, 1, 1)
        tools_grid.setColumnStretch(2, 1)
        tools_layout.addLayout(tools_grid)
        tools_note = QtWidgets.QLabel("Less frequently used actions.")
        tools_note.setStyleSheet("color: #516079;")
        tools_layout.addWidget(tools_note)
        tools_layout.addStretch(1)

        self.cfg_tabs = QtWidgets.QTabWidget()
        self.cfg_tabs.addTab(axis_box, "Axis / PV")
        self.cfg_tabs.addTab(plan_box, "Range / Targets")
        self.cfg_tabs.addTab(tools_box, "Tools")
        cfg.addWidget(self.cfg_tabs)

        layout.addWidget(self.cfg_group)
        self.cfg_group.setVisible(True)

        mid_row = QtWidgets.QHBoxLayout()
        mid_row.setContentsMargins(0, 0, 0, 0)
        mid_row.setSpacing(8)

        self.lower_tabs = QtWidgets.QTabWidget()

        summary_tab = QtWidgets.QWidget()
        summary_tab.setMinimumHeight(_scaled_px(430))
        left_col = QtWidgets.QVBoxLayout(summary_tab)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        self.summary_group = QtWidgets.QGroupBox("ISO 230 Summary")
        self.summary_group.setMinimumHeight(_scaled_px(162))
        self.summary_group.setMaximumHeight(_scaled_px(162))
        summary_layout = QtWidgets.QGridLayout(self.summary_group)
        summary_layout.setContentsMargins(6, 6, 6, 6)
        summary_layout.setHorizontalSpacing(8)
        summary_layout.setVerticalSpacing(4)
        self.summary_labels = {}
        summary_fields = [
            ("state", "State"),
            ("target_count", "Targets"),
            ("samples_total", "Samples"),
            ("bidirectional_accuracy", "BiDir Accuracy"),
            ("bidirectional_systematic_deviation", "BiDir Systematic"),
            ("bidirectional_repeatability", "BiDir Repeat"),
            ("unidirectional_repeatability", "Uni Repeat"),
            ("mean_reversal_value", "Mean Reversal"),
            ("maximum_reversal_value", "Max Reversal"),
            ("fit_slope", "Interpolation Slope"),
            ("fit_intercept", "Interpolation Offset"),
        ]
        for idx, (key, title) in enumerate(summary_fields):
            row = idx // 2
            col = (idx % 2) * 2
            summary_layout.addWidget(QtWidgets.QLabel(title), row, col)
            label = QtWidgets.QLabel("-")
            label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            summary_layout.addWidget(label, row, col + 1)
            self.summary_labels[key] = label
        left_col.addWidget(self.summary_group)

        self.summary_table = QtWidgets.QTableWidget(0, 8)
        self.summary_table.setHorizontalHeaderLabels(
            [
                "Target",
                "Mean BiDir Dev",
                "Reversal",
                "Uni Repeat",
                "BiDir Repeat",
                "Fwd Mean Err",
                "Rev Mean Err",
                "Max |Err|",
            ]
        )
        self.summary_table.verticalHeader().setVisible(False)
        self.summary_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.summary_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.horizontalHeader().setMinimumSectionSize(_scaled_px(72))
        left_col.addWidget(self.summary_table, 1)
        graph_tab = QtWidgets.QWidget()
        graph_tab.setMinimumHeight(_scaled_px(430))
        live_graph_layout = QtWidgets.QVBoxLayout(graph_tab)
        live_graph_layout.setContentsMargins(6, 6, 6, 6)
        live_graph_layout.setSpacing(6)
        self.live_graph_note = QtWidgets.QLabel("Step: Idle")
        self.live_graph_note.setWordWrap(True)
        self.live_graph_note.setStyleSheet("color: #516079; font-weight: 600;")
        live_graph_layout.addWidget(self.live_graph_note)
        self.live_graph_frame = QtWidgets.QFrame()
        self.live_graph_frame.setStyleSheet("background: white; border: 1px solid #d7e0eb; border-radius: 8px;")
        self.live_graph_frame.setMinimumHeight(_scaled_px(250))
        self.live_graph_frame_layout = QtWidgets.QVBoxLayout(self.live_graph_frame)
        self.live_graph_frame_layout.setContentsMargins(4, 4, 4, 4)
        self.live_graph_svg = QSvgWidget()
        self.live_graph_svg.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.live_graph_placeholder = QtWidgets.QLabel()
        self.live_graph_placeholder.setAlignment(QtCore.Qt.AlignCenter)
        self.live_graph_placeholder.setWordWrap(True)
        self.live_graph_placeholder.setStyleSheet("color: #516079; background: transparent; border: none;")
        self.live_graph_frame_layout.addWidget(self.live_graph_svg, 1)
        self.live_graph_frame_layout.addWidget(self.live_graph_placeholder, 1)
        live_graph_layout.addWidget(self.live_graph_frame, 1)
        self.lower_tabs.addTab(graph_tab, "Live graph progress")

        self.lower_tabs.addTab(summary_tab, "ISO230 summary")

        status_tab = QtWidgets.QWidget()
        status_tab.setMinimumHeight(_scaled_px(430))
        right_col = QtWidgets.QVBoxLayout(status_tab)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)
        self.status_group = QtWidgets.QGroupBox("Live Status")
        self.status_group.setMinimumHeight(_scaled_px(162))
        self.status_group.setMaximumHeight(_scaled_px(162))
        status_layout = QtWidgets.QGridLayout(self.status_group)
        status_layout.setContentsMargins(6, 6, 6, 6)
        status_layout.setHorizontalSpacing(4)
        status_layout.setVerticalSpacing(3)
        self.status_fields = {}
        status_names = [("VAL", 0, 0), ("RBV", 0, 2), ("DMOV", 1, 0), ("CNEN", 1, 2), ("REF", 2, 0)]
        for name, row, col in status_names:
            status_layout.addWidget(QtWidgets.QLabel(name), row, col)
            edit = QtWidgets.QLineEdit("")
            edit.setReadOnly(True)
            status_layout.addWidget(edit, row, col + 1)
            self.status_fields[name] = edit
        self.step_label = QtWidgets.QLabel("Idle")
        self.step_label.setWordWrap(True)
        status_layout.addWidget(QtWidgets.QLabel("Step"), 2, 2)
        status_layout.addWidget(self.step_label, 2, 3)
        right_col.addWidget(self.status_group)

        self.results_table = QtWidgets.QTableWidget(0, 10)
        self.results_table.setHorizontalHeaderLabels(
            [
                "Cycle",
                "Dir",
                "Target",
                "Ref Mean",
                "Ref Std",
                "RBV Mean",
                "RBV Std",
                "Ref Err",
                "RBV Err",
                "Timestamp",
            ]
        )
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.results_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.results_table.horizontalHeader().setStretchLastSection(True)
        self.results_table.horizontalHeader().setMinimumSectionSize(_scaled_px(72))
        right_col.addWidget(self.results_table, 1)
        self.lower_tabs.addTab(status_tab, "Live status")

        mid_row.addWidget(self.lower_tabs, 1)
        layout.addLayout(mid_row, 1)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(_scaled_px(140))
        self.log.setVisible(False)
        layout.addWidget(self.log)

        progress_row = QtWidgets.QHBoxLayout()
        progress_row.setContentsMargins(0, 2, 0, 0)
        progress_row.setSpacing(8)
        progress_row.addWidget(QtWidgets.QLabel("Test Progress"))
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Idle")
        progress_row.addWidget(self.progress_bar, 1)
        self.progress_label = QtWidgets.QLabel("0 / 0 steps")
        self.progress_label.setMinimumWidth(_scaled_px(110))
        self.progress_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self._refresh_axis_pick_combo()
        self._refresh_reference_selector()
        self._sync_reversal_margin_default()
        self.preview_targets()
        self._update_duration_estimate()
        self._update_summary_labels({})
        self._update_live_graph()
        self._update_progress_display()

    def _set_timeout(self, value):
        self.client.timeout = float(value)

    def _toggle_config_panel(self):
        visible = not self.cfg_group.isVisible()
        self.cfg_group.setVisible(visible)
        self.cfg_toggle_btn.setText("Hide Setup" if visible else "Show Setup")

    def _toggle_log_panel(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_toggle_btn.setText("Hide Log" if visible else "Show Log")

    def _log(self, msg):
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _update_live_graph(self):
        if not hasattr(self, "live_graph_svg"):
            return
        settings = getattr(self, "_test_settings_cache", None) or {}
        metrics = dict(self._latest_metrics or {})
        if not settings:
            self.live_graph_svg.hide()
            self.live_graph_placeholder.setText("Configure an axis and start or load data to see the live graph.")
            self.live_graph_placeholder.show()
            return
        graph_svg = self._build_iso230_svg(settings, metrics, width=760, height=340, compact=True)
        if str(graph_svg).lstrip().startswith("<svg"):
            self.live_graph_placeholder.hide()
            self.live_graph_svg.load(QtCore.QByteArray(graph_svg.encode("utf-8")))
            self.live_graph_svg.show()
            return
        self.live_graph_svg.hide()
        msg = re.sub(r"<[^>]+>", "", str(graph_svg)).strip() or "Live graph unavailable."
        self.live_graph_placeholder.setText(msg)
        self.live_graph_placeholder.show()

    def _reset_open_app_combo(self):
        self.open_app_combo.blockSignals(True)
        self.open_app_combo.setCurrentIndex(0)
        self.open_app_combo.blockSignals(False)

    def _on_open_app_selected(self, index):
        action = str(self.open_app_combo.itemData(index) or "")
        try:
            if action == "iso230":
                self._open_new_iso230_window()
            elif action == "axis":
                self._open_axis_window()
            elif action == "controller":
                self._open_controller_window()
            elif action == "motion":
                self._open_motion_window()
            elif action == "fft":
                self._open_fft_window()
            elif action == "caqtdm_main":
                self._open_caqtdm_main_panel()
            elif action == "caqtdm_axis":
                self._open_caqtdm_axis_panel()
        finally:
            self._reset_open_app_combo()

    def _axis_id_text(self):
        return self.axis_edit.text().strip() or self.default_axis_id

    def _open_fft_window(self):
        script = Path(__file__).with_name("start_fft.sh")
        if not script.exists():
            self._log(f"Launcher not found: {script.name}")
            return
        prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
        try:
            subprocess.Popen(
                ["bash", str(script), str(prefix)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started FFT window (prefix {prefix})")
        except Exception as ex:
            self._log(f"Failed to start FFT window: {ex}")

    def _open_caqtdm_main_panel(self):
        ioc_prefix = self.prefix_edit.text().strip() or self.default_prefix or ""
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

    def _sync_axis_combo_to_axis_id(self, axis_id):
        want = str(axis_id or "").strip()
        if not want:
            return
        idx = self.axis_pick_combo.findData(want, role=QtCore.Qt.UserRole)
        if idx >= 0:
            self._axis_combo_updating = True
            self.axis_pick_combo.setCurrentIndex(idx)
            self._axis_combo_updating = False

    def _axis_combo_install_open_new_item(self):
        if self.axis_pick_combo.count() <= 0:
            return
        try:
            item = self.axis_pick_combo.model().item(0)
            if item is None:
                return
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setData(
                QtCore.Qt.Checked if self._axis_combo_open_new_instance else QtCore.Qt.Unchecked,
                QtCore.Qt.CheckStateRole,
            )
        except Exception:
            pass

    def _axis_combo_toggle_open_new_item(self):
        self._axis_combo_open_new_instance = not bool(self._axis_combo_open_new_instance)
        self._axis_combo_install_open_new_item()

    def _refresh_axis_pick_combo(self):
        current_axis = self._axis_id_text()
        self._axis_combo_updating = True
        self.axis_pick_combo.clear()
        self.axis_pick_combo.addItem("Open New Instance", "__open_new__")
        self._axis_combo_install_open_new_item()
        self.axis_pick_combo.addItem(f"Axis {current_axis}", current_axis)
        try:
            axes = self._discover_axes_from_ioc()
        except Exception:
            self._axis_combo_updating = False
            self._sync_axis_combo_to_axis_id(current_axis)
            return
        self.axis_pick_combo.clear()
        self.axis_pick_combo.addItem("Open New Instance", "__open_new__")
        self._axis_combo_install_open_new_item()
        for ax in axes:
            axis_id = str(ax.get("axis_id", "") or "").strip()
            axis_type = str(ax.get("axis_type", "") or "")
            motor_name = str(ax.get("motor_name", "") or "")
            type_disp = "REAL" if axis_type.upper() == "REAL" else ("Virtual" if axis_type else "?")
            label = f"{axis_id} | {type_disp}"
            if motor_name:
                label += f" | {motor_name}"
            self.axis_pick_combo.addItem(label, axis_id)
        if self.axis_pick_combo.count() == 0:
            self.axis_pick_combo.addItem("Open New Instance", "__open_new__")
            self._axis_combo_install_open_new_item()
            self.axis_pick_combo.addItem(f"Axis {current_axis}", current_axis)
        self._axis_combo_updating = False
        self._sync_axis_combo_to_axis_id(current_axis)

    def _on_axis_combo_activated(self, _index):
        if self._axis_combo_updating:
            return
        axis_id = str(self.axis_pick_combo.currentData(QtCore.Qt.UserRole) or "").strip()
        if axis_id == "__open_new__":
            self._axis_combo_toggle_open_new_item()
            self._sync_axis_combo_to_axis_id(self._axis_id_text())
            QtCore.QTimer.singleShot(0, self.axis_pick_combo.showPopup)
            return
        if not axis_id:
            return
        if self._axis_combo_open_new_instance:
            self._open_new_iso230_window(axis_id=axis_id)
            self._sync_axis_combo_to_axis_id(self._axis_id_text())
            return
        self.axis_edit.setText(axis_id)
        self._apply_axis_top()

    def _open_new_iso230_window(self, axis_id=None):
        script = QtCore.QFileInfo(__file__).dir().filePath("start_iso230.sh")
        if not QtCore.QFileInfo(script).exists():
            self._log("Launcher not found: start_iso230.sh")
            return False
        target_axis = str(axis_id or self._axis_id_text()).strip() or self.default_axis_id
        prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
        try:
            subprocess.Popen(
                ["bash", str(script), str(prefix), str(target_axis)],
                cwd=str(QtCore.QFileInfo(script).absolutePath()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started new ISO 230 window for axis {target_axis} (prefix {prefix})")
            return True
        except Exception as ex:
            self._log(f"Failed to start new ISO 230 window: {ex}")
            return False

    def _open_script_window(self, script_name, label):
        script = Path(__file__).with_name(script_name)
        if not script.exists():
            self._log(f"Launcher not found: {script.name}")
            return
        axis_id = self._axis_id_text()
        prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
        try:
            subprocess.Popen(
                ["bash", str(script), str(prefix), str(axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started {label} window for axis {axis_id} (prefix {prefix})")
        except Exception as ex:
            self._log(f"Failed to start {label} window: {ex}")

    def _open_axis_window(self):
        self._open_script_window("start_axis.sh", "axis")

    def _open_controller_window(self):
        self._open_script_window("start_cntrl.sh", "controller")

    def _open_motion_window(self):
        self._open_script_window("start_mtn.sh", "motion")

    def _open_caqtdm_axis_panel(self):
        axis_id = self._axis_id_text()
        ioc_prefix = self.prefix_edit.text().strip() or self.default_prefix or ""
        motor_prefix = ""
        axis_name = ""
        try:
            pfx_pv = self.axis_pfx_cfg_pv_edit.text().strip() if hasattr(self, "axis_pfx_cfg_pv_edit") else ""
            if pfx_pv:
                raw = self._get_pv_best_effort(pfx_pv, as_string=True)
                motor_prefix = str(raw or "").strip().strip('"')
        except Exception:
            motor_prefix = ""
        try:
            nam_pv = self.motor_name_cfg_pv_edit.text().strip() if hasattr(self, "motor_name_cfg_pv_edit") else ""
            if nam_pv:
                raw = self._get_pv_best_effort(nam_pv, as_string=True)
                axis_name = str(raw or "").strip().strip('"')
        except Exception:
            axis_name = ""
        motor_base = self.motor_record_edit.text().strip() if hasattr(self, "motor_record_edit") else ""
        if not motor_prefix and motor_base and ":" in motor_base:
            motor_prefix = motor_base.rsplit(":", 1)[0]
        if not axis_name and motor_base:
            axis_name = motor_base.rsplit(":", 1)[-1]
        motor_prefix = str(motor_prefix or "").rstrip(":")
        macro = f"DEV={motor_prefix},IOC={ioc_prefix},Axis={axis_name},AX_ID={axis_id}"
        try:
            cmd = f'caqtdm -macro "{macro}" ecmcAxis.ui'
            subprocess.Popen(
                ["bash", "-lc", cmd],
                cwd=str(Path(__file__).resolve().parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started caQtDM axis panel ({macro})")
        except Exception as ex:
            self._log(f"Failed to start caQtDM axis panel: {ex}")

    def _discover_axes_from_ioc(self):
        prefix = self.prefix_edit.text().strip() or self.default_prefix
        if not prefix:
            raise RuntimeError("IOC prefix is empty")
        cur = _normalize_axis_object_id(
            self._get_pv_best_effort(_join_prefix_pv(prefix, "MCU-Cfg-AX-FrstObjId"), as_string=True) or ""
        )
        out = []
        seen = set()
        while cur and cur != "-1":
            axis_id = str(cur).strip()
            if not axis_id or axis_id in seen:
                break
            if not axis_id.isdigit():
                self._log(f'Axis discovery: invalid object id "{axis_id}" from IOC configuration')
                break
            seen.add(axis_id)
            axis_pfx = ""
            motor_name = ""
            try:
                axis_pfx = str(
                    self._get_pv_best_effort(_join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Pfx"), as_string=True) or ""
                ).strip().strip('"')
            except Exception:
                pass
            try:
                motor_name = str(
                    self._get_pv_best_effort(_join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Nam"), as_string=True) or ""
                ).strip().strip('"')
            except Exception:
                pass
            motor = self._combine_motor_record(axis_pfx, motor_name)
            axis_type = ""
            if motor:
                try:
                    axis_type = _normalize_axis_type_text(self._get_pv_best_effort(f"{motor}-Type", as_string=True) or "")
                except Exception:
                    axis_type = ""
            out.append({"axis_id": axis_id, "motor": motor, "motor_name": motor_name, "axis_type": axis_type})
            cur = _normalize_axis_object_id(
                self._get_pv_best_effort(_join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-NxtObjId"), as_string=True) or ""
            )
        return out

    def _resolve_axis_selector_to_id(self, selector):
        s = str(selector or "").strip()
        if not s:
            return ""
        if s.isdigit():
            return s
        try:
            axes = self._discover_axes_from_ioc()
        except Exception:
            return ""
        want = s.lower()
        for ax in axes:
            axis_id = str(ax.get("axis_id", "") or "").strip()
            motor_name = str(ax.get("motor_name", "") or "").strip()
            motor = str(ax.get("motor", "") or "").strip()
            if want in {motor_name.lower(), motor.lower()}:
                return axis_id
            if motor and motor.split(":")[-1].lower() == want:
                return axis_id
        return ""

    def _startup_axis_presence_check(self):
        if self._did_startup_axis_presence_check:
            return
        self._did_startup_axis_presence_check = True
        prefix = self.prefix_edit.text().strip() or self.default_prefix
        cur_axis = self._axis_id_text()
        if not self._axis_id_was_provided:
            first_axis = self._read_first_axis_id()
            if first_axis:
                if first_axis != cur_axis:
                    self._log(f"No startup axis provided, using first axis from IOC: {first_axis}")
                    self.axis_edit.setText(first_axis)
                    self._apply_axis_top()
                    cur_axis = first_axis
            else:
                self._log("No startup axis provided and first-axis discovery failed")
                return
        resolved_id = self._resolve_axis_selector_to_id(cur_axis)
        if resolved_id and resolved_id != cur_axis:
            self._log(f'Axis selector "{cur_axis}" resolved to axis {resolved_id}')
            self.axis_edit.setText(resolved_id)
            self._apply_axis_top()
            cur_axis = resolved_id
        if not prefix:
            self._log("Startup axis probe skipped: IOC prefix unavailable")
            return
        try:
            probe_pv = _join_prefix_pv(prefix, f"MCU-Cfg-AX{cur_axis}-Pfx")
            raw = self.client.get(probe_pv, as_string=True)
        except Exception as ex:
            self._log(f"Startup axis probe failed for axis {cur_axis}: {ex}")
            return
        if str(raw or "").strip().strip('"'):
            self._startup_axis_probe_ok = True
            self.resolve_motor_record_name()
            return
        self._log(f"Axis {cur_axis} probe returned empty")

    def _read_first_axis_id(self):
        prefix = self.prefix_edit.text().strip() or self.default_prefix
        if not prefix:
            return ""
        first_obj_pv = _join_prefix_pv(prefix, "MCU-Cfg-AX-FrstObjId")
        try:
            raw = self._get_pv_best_effort(first_obj_pv, as_string=True)
            axis_id = _normalize_axis_object_id(raw)
            if axis_id and axis_id != "-1" and axis_id.isdigit():
                return axis_id
        except Exception as ex:
            self._log(f"Failed reading first axis id from {first_obj_pv}: {ex}")
        return ""

    def _apply_axis_top(self):
        if self._test_active:
            self.abort_test()
        self._demo_mode = False
        axis_txt = self.axis_edit.text().strip() or self.default_axis_id
        self.axis_edit.setText(axis_txt)
        self._update_cfg_pv_edits()
        self._sync_axis_combo_to_axis_id(axis_txt)
        self.resolve_motor_record_name()

    def _update_cfg_pv_edits(self):
        prefix = self.prefix_edit.text().strip()
        axis_id = self._axis_id_text()
        self.axis_pfx_cfg_pv_edit.setText(_join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Pfx"))
        guessed = _join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Nam")
        if not self.motor_name_cfg_pv_edit.text().strip() or "MCU-Cfg-AX" in self.motor_name_cfg_pv_edit.text():
            self.motor_name_cfg_pv_edit.setText(guessed)
        self._commit_cfg_pv_edits()

    def _committed_motor_record_text(self):
        return str(self._committed_motor_record or "").strip()

    def _commit_cfg_pv_edits(self):
        self._committed_axis_pfx_cfg_pv = self.axis_pfx_cfg_pv_edit.text().strip()
        self._committed_motor_name_cfg_pv = self.motor_name_cfg_pv_edit.text().strip()
        self._poll_failure_cache.clear()

    def _commit_motor_record_edit(self):
        self._committed_motor_record = self.motor_record_edit.text().strip()
        self._poll_failure_cache.clear()
        self._sync_reference_pv_default()
        self._update_window_title()
        try:
            self.refresh_status()
        except Exception:
            pass

    def _reference_pv_presets(self):
        motor = self._committed_motor_record_text()
        if not motor:
            return []
        presets = []
        for pv in (
            f"{motor}-PosAct",
            f"{motor}-Enc01-PosAct",
            f"{motor}-Enc02-PosAct",
        ):
            if pv not in presets:
                presets.append(pv)
        return presets

    def _reference_pv_text(self, widget):
        try:
            return widget.currentText().strip()
        except Exception:
            return widget.text().strip()

    def _set_reference_pv_text(self, widget, text):
        value = str(text or "").strip()
        try:
            widget.setEditText(value)
        except Exception:
            widget.setText(value)

    def _refresh_reference_pv_presets(self):
        presets = self._reference_pv_presets()
        for edit in self.reference_pv_edits:
            current = self._reference_pv_text(edit)
            values = []
            for pv in presets + ([current] if current else []):
                pv = str(pv or "").strip()
                if pv and pv not in values:
                    values.append(pv)
            edit.blockSignals(True)
            edit.clear()
            for pv in values:
                edit.addItem(pv)
            self._set_reference_pv_text(edit, current)
            edit.blockSignals(False)

    def _commit_reference_pv_edits(self):
        self._committed_reference_pvs = [self._reference_pv_text(edit) for edit in self.reference_pv_edits]
        self._refresh_reference_pv_presets()
        self._poll_failure_cache.clear()
        self._on_reference_pvs_changed()

    def _configured_reference_pvs(self):
        return list(self._committed_reference_pvs)

    def _read_polled_pv(self, pv):
        name = str(pv or "").strip()
        if not name:
            return "", ""
        cached_error = self._poll_failure_cache.get(name)
        if cached_error:
            return "", cached_error
        try:
            value = str(self.client.get(name, as_string=True)).strip()
            return value, ""
        except Exception as ex:
            err = f"ERR: {ex}"
            self._poll_failure_cache[name] = err
            self._log(f"Polling disabled for {name} after read failure. Press Enter in the PV field to retry.")
            return "", err

    def _selected_reference_slot(self, reference_pvs=None):
        pvs = list(reference_pvs if reference_pvs is not None else self._configured_reference_pvs())
        current = self.report_reference_combo.currentData(QtCore.Qt.UserRole)
        if current is not None:
            try:
                slot = int(current)
            except Exception:
                slot = None
            else:
                if 0 <= slot < len(pvs) and pvs[slot]:
                    return slot
        for idx, pv in enumerate(pvs):
            if pv:
                return idx
        return None

    def _selected_reference_pv(self, reference_pvs=None):
        pvs = list(reference_pvs if reference_pvs is not None else self._configured_reference_pvs())
        slot = self._selected_reference_slot(pvs)
        if slot is None:
            return ""
        return pvs[slot]

    def _refresh_reference_selector(self):
        pvs = self._configured_reference_pvs()
        selected_slot = self._selected_reference_slot(pvs)
        self.report_reference_combo.blockSignals(True)
        self.report_reference_combo.clear()
        for idx, pv in enumerate(pvs):
            if not pv:
                continue
            self.report_reference_combo.addItem(f"Ref {idx + 1}: {pv}", idx)
        if self.report_reference_combo.count() == 0:
            self.report_reference_combo.addItem("No reference PV configured", -1)
            self.report_reference_combo.setEnabled(False)
        else:
            self.report_reference_combo.setEnabled(True)
            combo_index = self.report_reference_combo.findData(
                selected_slot if selected_slot is not None else -1,
                role=QtCore.Qt.UserRole,
            )
            self.report_reference_combo.setCurrentIndex(max(0, combo_index))
        self.report_reference_combo.blockSignals(False)

    def _on_reference_pvs_changed(self, _text=None):
        self._refresh_reference_selector()
        if self._measurements:
            self._reproject_measurements_for_selected_reference()
        else:
            self.preview_targets()
            try:
                self.refresh_status()
            except Exception:
                pass

    def _on_reference_selection_changed(self, _index):
        if self._measurements:
            self._reproject_measurements_for_selected_reference()
        else:
            try:
                self.refresh_status()
            except Exception:
                pass

    def _sync_reference_pv_default(self, _text=None):
        motor = self._committed_motor_record_text()
        default_ref = f"{motor}-PosAct" if motor else ""
        self._refresh_reference_pv_presets()
        current_ref = self._reference_pv_text(self.reference_pv_edits[0])
        changed = False
        if not current_ref or current_ref == self._last_auto_reference_pv:
            self._set_reference_pv_text(self.reference_pv_edits[0], default_ref)
            changed = True
        self._last_auto_reference_pv = default_ref
        if changed:
            self._commit_reference_pv_edits()
        self._refresh_reference_selector()

    def _sync_reversal_margin_default(self):
        try:
            range_min = _to_float(self.range_min_edit.text(), "Range Min")
            range_max = _to_float(self.range_max_edit.text(), "Range Max")
            if range_max <= range_min:
                return
            auto_margin = abs(range_max - range_min) * 0.05
            auto_text = _fmt(auto_margin)
        except Exception:
            return
        current_text = self.reversal_margin_edit.text().strip()
        if not current_text or current_text == self._last_auto_reversal_margin:
            self.reversal_margin_edit.setText(auto_text)
        self._last_auto_reversal_margin = auto_text

    def _on_range_inputs_changed(self):
        self._sync_reversal_margin_default()
        self._update_duration_estimate()

    def _on_decimals_changed(self, value):
        _set_format_decimals(value)
        self._refresh_formatted_outputs()

    def _refresh_formatted_outputs(self):
        self._sync_reversal_margin_default()
        self._update_duration_estimate()
        self._update_summary_labels(self._latest_metrics or {})
        self._populate_summary_table((self._latest_metrics or {}).get("per_target", []))
        self._reload_results_table()
        self._update_live_graph()
        if self._measurements:
            self._latest_report_markdown = self._build_report_markdown()
        if self._last_status:
            for field in ("VAL", "RBV", "DMOV", "CNEN", "REF"):
                if field in self.status_fields:
                    self.status_fields[field].setText(_fmt(self._last_status.get(field)))

    def _read_motor_soft_limits(self):
        if not self._committed_motor_record_text():
            return None
        for low_field, high_field in (("LLM", "HLM"), ("DLLM", "DHLM")):
            try:
                low = _to_float(self.client.get(self._pv(low_field), as_string=True), low_field)
                high = _to_float(self.client.get(self._pv(high_field), as_string=True), high_field)
            except Exception:
                continue
            if high <= low:
                continue
            if abs(low) < 1e-18 and abs(high) < 1e-18:
                continue
            return {"low": low, "high": high, "fields": (low_field, high_field)}
        return None

    def _sync_range_defaults_from_soft_limits(self, soft_limits=None):
        limits = soft_limits if soft_limits is not None else self._motor_soft_limits
        if not limits:
            return
        low = float(limits["low"])
        high = float(limits["high"])
        if high <= low:
            return

        current_min_text = self.range_min_edit.text().strip()
        current_max_text = self.range_max_edit.text().strip()
        current_margin_text = self.reversal_margin_edit.text().strip()

        current_min_val = _float_or_none(current_min_text)
        current_max_val = _float_or_none(current_max_text)
        initial_default_range = (
            current_min_val is not None
            and current_max_val is not None
            and abs(current_min_val - 0.0) <= 1e-9
            and abs(current_max_val - 10.0) <= 1e-9
        )
        using_auto_range = (
            not current_min_text
            or not current_max_text
            or (
                current_min_text == self._last_auto_range_min
                and current_max_text == self._last_auto_range_max
            )
            or initial_default_range
        )
        if not using_auto_range:
            return

        using_auto_margin = (not current_margin_text) or (current_margin_text == self._last_auto_reversal_margin)
        limit_span = high - low
        if using_auto_margin:
            target_span = limit_span / 1.1
            auto_margin = target_span * 0.05
        else:
            auto_margin = _to_float(current_margin_text, "Approach Margin Outside Targets")
            target_span = limit_span - (2.0 * auto_margin)
        if target_span <= 0.0:
            return

        auto_min = low + auto_margin
        auto_max = high - auto_margin
        auto_min_text = _fmt(auto_min)
        auto_max_text = _fmt(auto_max)

        self.range_min_edit.setText(auto_min_text)
        self.range_max_edit.setText(auto_max_text)
        self._last_auto_range_min = auto_min_text
        self._last_auto_range_max = auto_max_text
        if using_auto_margin:
            auto_margin_text = _fmt(auto_margin)
            self.reversal_margin_edit.setText(auto_margin_text)
            self._last_auto_reversal_margin = auto_margin_text
        self._log(
            "Initialized measured range from motor soft limits "
            f"({limits['fields'][0]}={_fmt(low)}, {limits['fields'][1]}={_fmt(high)}) "
            f"to {_fmt(auto_min)} .. {_fmt(auto_max)}"
        )

    def _sequence_preview_settings(self):
        settings = self._target_preview_settings()
        velo, accl, accs, vmax = self._shared_motion_params()
        settings.update(
            {
                "cycles": int(self.cycles_spin.value()),
                "settle_s": float(self.settle_spin.value()),
                "samples_per_point": int(self.samples_spin.value()),
                "sample_interval_ms": self.SAMPLE_INTERVAL_MS,
                "velo": velo,
                "accl": accl,
                "accs": accs,
                "vmax": vmax,
                "display_decimals": int(self.decimals_spin.value()),
                "axis_prefix_cfg_pv": self.axis_pfx_cfg_pv_edit.text().strip(),
                "motor_name_cfg_pv": self.motor_name_cfg_pv_edit.text().strip(),
            }
        )
        return settings

    def _estimate_test_duration(self, settings):
        plan = self._build_test_plan(settings)
        velo = abs(float(settings.get("velo") or 0.0))
        if velo <= 1e-18:
            raise ValueError("VELO must be > 0 for duration estimate")
        settle_s = max(0.0, float(settings.get("settle_s") or 0.0))
        sample_interval_s = max(0.0, float(settings.get("sample_interval_ms") or 0.0) / 1000.0)
        samples_per_point = max(1, int(settings.get("samples_per_point") or 1))
        move_overhead_s = 0.35

        motion_s = 0.0
        for prev_step, step in zip(plan, plan[1:]):
            distance = abs(float(step["target"]) - float(prev_step["target"]))
            motion_s += (distance / velo) + move_overhead_s
        if plan:
            motion_s += move_overhead_s

        settle_total_s = len(plan) * settle_s
        measured_steps = sum(1 for step in plan if step.get("measure"))
        sampling_total_s = measured_steps * max(0, samples_per_point - 1) * sample_interval_s
        total_s = motion_s + settle_total_s + sampling_total_s
        return {
            "total_s": total_s,
            "motion_s": motion_s,
            "settle_s": settle_total_s,
            "sampling_s": sampling_total_s,
            "measured_steps": measured_steps,
            "step_count": len(plan),
            "excludes_initial_approach": True,
        }

    def _update_duration_estimate(self, *_args):
        try:
            settings = self._sequence_preview_settings()
            estimate = self._estimate_test_duration(settings)
            text = f"~{_format_duration(estimate['total_s'])}"
            if estimate.get("excludes_initial_approach"):
                text += " + approach"
            self.estimated_duration_value.setText(text)
            self.estimated_duration_value.setToolTip(
                "Approximate sequence duration based on target travel, VELO, settle time and sampling. "
                f"Motion={_format_duration(estimate['motion_s'])}, "
                f"Settle={_format_duration(estimate['settle_s'])}, "
                f"Sampling={_format_duration(estimate['sampling_s'])}. "
                "The initial move from the current axis position to the first pre-position target is not included."
            )
        except Exception as ex:
            self.estimated_duration_value.setText("-")
            self.estimated_duration_value.setToolTip(f"Duration estimate unavailable: {ex}")
        self._update_target_schematic()

    def _update_target_schematic(self):
        try:
            self.target_schematic.set_preview(self._target_preview_settings(), "")
        except Exception as ex:
            self.target_schematic.set_preview(None, f"Sweep schematic unavailable: {ex}")
        self._update_target_schematic_live_state()

    def _update_target_schematic_live_state(self):
        if not hasattr(self, "target_schematic"):
            return
        actual = None
        target = None
        try:
            actual = _float_or_none((self._last_status or {}).get("RBV"))
        except Exception:
            actual = None
        try:
            if self._current_step:
                target = _float_or_none(self._current_step.get("target"))
        except Exception:
            target = None
        phase = str(getattr(self, "_current_phase", "") or "")
        self.target_schematic.set_live_state(actual=actual, target=target, phase=phase)

    def _update_progress_display(self):
        if hasattr(self, "live_graph_note"):
            step_text = ""
            try:
                step_text = str(self.step_label.text() or "").strip()
            except Exception:
                step_text = ""
            self.live_graph_note.setText(f"Step: {step_text or 'Idle'}")
        total_steps = max(0, len(self._test_plan or []))
        if self._test_active and total_steps > 0:
            completed_steps = min(total_steps, max(0, self._test_plan_index))
            active_index = min(total_steps, max(1, self._test_plan_index + 1))
            self.progress_bar.setRange(0, total_steps)
            self.progress_bar.setValue(active_index)
            self.progress_bar.setFormat(f"{int(round((active_index / total_steps) * 100.0))}%")
            self.progress_label.setText(f"{completed_steps} / {total_steps} done")
            return
        if total_steps > 0 and self._current_phase in {"done", "aborted", "error"}:
            final_value = total_steps if self._current_phase == "done" else min(total_steps, max(0, self._test_plan_index))
            self.progress_bar.setRange(0, total_steps)
            self.progress_bar.setValue(final_value)
            if self._current_phase == "done":
                self.progress_bar.setFormat("Complete")
                self.progress_label.setText(f"{total_steps} / {total_steps} done")
            elif self._current_phase == "aborted":
                self.progress_bar.setFormat("Aborted")
                self.progress_label.setText(f"{final_value} / {total_steps} done")
            else:
                self.progress_bar.setFormat("Error")
                self.progress_label.setText(f"{final_value} / {total_steps} done")
            return
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")
        self.progress_label.setText("0 / 0 steps")

    def _read_cfg_pv(self, pv):
        return str(self.client.get(pv, as_string=True)).strip().strip('"')

    def _candidate_motor_name_pvs(self):
        prefix = self.prefix_edit.text().strip()
        axis_id = self._axis_id_text()
        suffixes = [
            f"MCU-Cfg-AX{axis_id}-Nam",
            f"MCU-Cfg-AX{axis_id}-Mtr",
            f"MCU-Cfg-AX{axis_id}-MtrName",
            f"MCU-Cfg-AX{axis_id}-Motor",
            f"MCU-Cfg-AX{axis_id}-MotorName",
            f"MCU-Cfg-AX{axis_id}-Pfx",
        ]
        return [_join_prefix_pv(prefix, s) for s in suffixes]

    def _combine_motor_record(self, axis_pfx, motor_name):
        a = str(axis_pfx or "").strip()
        m = str(motor_name or "").strip()
        if a and m:
            if m.startswith(a) or ":" in m:
                return m
            if a.endswith(":"):
                return f"{a}{m}"
            return f"{a}:{m}"
        return a or m

    def resolve_motor_record_name(self):
        try:
            self._demo_mode = False
            axis_pfx_pv = self._committed_axis_pfx_cfg_pv
            axis_pfx = self._read_cfg_pv(axis_pfx_pv) if axis_pfx_pv else ""
            motor_cfg_pv = self._committed_motor_name_cfg_pv
            motor_name = ""
            tried = []
            for pv in [motor_cfg_pv] + self._candidate_motor_name_pvs():
                pv = str(pv or "").strip()
                if not pv or pv in tried:
                    continue
                tried.append(pv)
                try:
                    motor_name = self._read_cfg_pv(pv)
                    if motor_name:
                        if pv != motor_cfg_pv:
                            self.motor_name_cfg_pv_edit.setText(pv)
                            self._log(f"Resolved motor-name PV using fallback: {pv}")
                        break
                except Exception:
                    continue
            if not axis_pfx and not motor_name:
                raise RuntimeError("Could not read axis prefix or motor name PV")
            resolved = self._combine_motor_record(axis_pfx, motor_name)
            self.motor_record_edit.setText(resolved)
            self._committed_motor_record = resolved
            self._sync_reference_pv_default()
            self._update_window_title()
            self._log(f"Resolved motor record: {resolved} (axis_pfx='{axis_pfx}', motor='{motor_name}')")
            self._init_motion_settings_from_pv()
            self.refresh_status()
        except Exception as ex:
            self._update_window_title()
            self._log(f"Resolve failed: {ex}")

    def _update_window_title(self):
        motor = self._committed_motor_record_text()
        if motor:
            self.setWindowTitle(f"{self._base_title} [{motor}]")
        else:
            self.setWindowTitle(self._base_title)

    def _pv(self, field):
        base = self._committed_motor_record_text()
        if not base:
            raise RuntimeError("Motor record is not resolved")
        return f"{base}.{field}"

    def _motor_base(self):
        base = self._committed_motor_record_text()
        if not base:
            raise RuntimeError("Motor record is not resolved")
        return base

    def _motor_suffix_pv(self, suffix):
        base = self._motor_base()
        s = str(suffix or "").strip()
        if not s:
            raise RuntimeError("Empty motor suffix")
        return f"{base}{s}"

    def _put(self, field, value, quiet=False, wait=False):
        pv = self._pv(field)
        self.client.put(pv, value, wait=bool(wait))
        if not quiet:
            mode = "wait" if wait else "nowait"
            self._log(f"PUT [{mode}] {pv} = {value}")

    def _init_motion_settings_from_pv(self):
        if not self._committed_motor_record_text():
            return
        try:
            self.motion_velo_edit.setText(_fmt(self.client.get(self._pv("VELO"), as_string=True)))
        except Exception:
            pass
        try:
            self.motion_acc_edit.setText(_fmt(self.client.get(self._pv("ACCL"), as_string=True)))
        except Exception:
            pass
        try:
            self.motion_vmax_edit.setText(_fmt(self.client.get(self._pv("VMAX"), as_string=True)))
        except Exception:
            self.motion_vmax_edit.setText("")
        try:
            self.motion_accs_edit.setText(_fmt(self.client.get(self._pv("ACCS"), as_string=True)))
        except Exception:
            self.motion_accs_edit.setText("")
        self._motor_soft_limits = self._read_motor_soft_limits()
        self._sync_range_defaults_from_soft_limits(self._motor_soft_limits)
        self._update_duration_estimate()

    def _shared_motion_params(self):
        velo = _to_float(self.motion_velo_edit.text(), "VELO")
        accl = _to_float(self.motion_acc_edit.text(), "ACCL")
        vmax_txt = self.motion_vmax_edit.text().strip()
        accs_txt = self.motion_accs_edit.text().strip()
        vmax = _to_float(vmax_txt, "VMAX") if vmax_txt else None
        accs = _to_float(accs_txt, "ACCS") if accs_txt else None
        return velo, accl, accs, vmax

    def _set_move_params(self, velo, accl, accs=None, vmax=None):
        if vmax is not None:
            try:
                self._put("VMAX", vmax)
            except Exception as ex:
                self._log(f"VMAX unavailable ({ex})")
        if accs is not None:
            try:
                self._put("ACCS", accs)
            except Exception as ex:
                self._log(f"ACCS unavailable ({ex})")
        self._put("VELO", velo)
        self._put("ACCL", accl)

    def refresh_status(self):
        vals = {}
        if self._demo_mode:
            demo_row = self._measurements[-1] if self._measurements else None
            stats = self._reference_stats_for_row(demo_row) if demo_row is not None else {}
            if demo_row is not None:
                vals = {
                    "VAL": _fmt(demo_row.get("command_mean")),
                    "RBV": _fmt(demo_row.get("rbv_mean")),
                    "DMOV": "1",
                    "CNEN": "1",
                    "REF": _fmt(demo_row.get("reference_mean")),
                }
            else:
                vals = {"VAL": "", "RBV": "", "DMOV": "1", "CNEN": "1", "REF": ""}
            for field in ("VAL", "RBV", "DMOV", "CNEN", "REF"):
                self.status_fields[field].setText(str(vals.get(field, "")))
            for idx, value_edit in enumerate(self.reference_value_edits):
                stat = stats.get(idx, {})
                value_edit.setText(_fmt(stat.get("mean")))
            self._last_status = dict(vals)
            self._update_target_schematic_live_state()
            return vals
        if self._committed_motor_record_text():
            for field in ("VAL", "RBV", "DMOV", "CNEN"):
                vals[field], err = self._read_polled_pv(self._pv(field))
                if err:
                    vals[field] = err
                self.status_fields[field].setText(_fmt(vals[field]))
        else:
            for field in ("VAL", "RBV", "DMOV", "CNEN"):
                vals[field] = ""
                self.status_fields[field].setText("")
        ref_pv = self._selected_reference_pv()
        if ref_pv:
            vals["REF"], err = self._read_polled_pv(ref_pv)
            if err:
                vals["REF"] = err
            self.status_fields["REF"].setText(_fmt(vals["REF"]))
        else:
            vals["REF"] = ""
            self.status_fields["REF"].setText("")
        ref_values = {}
        for idx, ref_pv in enumerate(self._configured_reference_pvs()):
            if not ref_pv:
                self.reference_value_edits[idx].setText("")
                continue
            raw, err = self._read_polled_pv(ref_pv)
            if err:
                raw = err
            else:
                ref_values[idx] = raw
            self.reference_value_edits[idx].setText(_fmt(raw))
        vals["REFS"] = dict(ref_values)
        self._last_status = dict(vals)
        self._update_target_schematic_live_state()
        return vals

    def _periodic_status_tick(self):
        if self._demo_mode:
            return
        try:
            self.refresh_status()
        except Exception:
            pass

    def _target_preview_settings(self):
        motor = self._committed_motor_record_text()
        reference_pvs = self._configured_reference_pvs()
        reference_slot = self._selected_reference_slot(reference_pvs)
        ref_pv = self._selected_reference_pv(reference_pvs)
        range_min = _to_float(self.range_min_edit.text(), "Range Min")
        range_max = _to_float(self.range_max_edit.text(), "Range Max")
        if range_max <= range_min:
            raise ValueError("Range Max must be greater than Range Min")
        span = range_max - range_min
        target_count = int(self.target_count_spin.value() or 0)
        targets, target_meta = _generate_iso230_targets(range_min, range_max, target_count)
        target_count = int(target_meta.get("count") or len(targets))
        margin_txt = self.reversal_margin_edit.text().strip()
        if margin_txt:
            reversal_margin = _to_float(margin_txt, "Approach Margin Outside Targets")
        else:
            reversal_margin = abs(span) * 0.05
        if reversal_margin < 0:
            raise ValueError("Approach Margin Outside Targets must be >= 0")
        return {
            "prefix": self.prefix_edit.text().strip() or self.default_prefix,
            "axis_id": self._axis_id_text(),
            "motor": motor,
            "reference_pvs": reference_pvs,
            "reference_slot": reference_slot,
            "reference_pv": ref_pv,
            "range_min": range_min,
            "range_max": range_max,
            "span": span,
            "targets": targets,
            "target_count": target_count,
            "target_mode": target_meta.get("mode"),
            "target_rule_note": target_meta.get("rule_note"),
            "base_interval": target_meta.get("base_interval"),
            "reversal_margin": reversal_margin,
        }

    def _test_settings(self):
        settings = self._sequence_preview_settings()
        if not settings["motor"]:
            raise RuntimeError("Resolve a motor record before starting the test")
        if not any(settings.get("reference_pvs") or []):
            raise RuntimeError("At least one reference measurement PV is required")
        return settings

    def _reference_stats_for_row(self, row):
        stats = dict(row.get("reference_stats") or {})
        if stats:
            return stats
        pv = str(row.get("reference_pv", "") or "").strip()
        if not pv and self._test_settings_cache:
            pvs = list(self._test_settings_cache.get("reference_pvs") or [])
            slot = row.get("reference_slot")
            if slot is not None:
                try:
                    slot = int(slot)
                except Exception:
                    slot = None
                else:
                    if 0 <= slot < len(pvs):
                        pv = pvs[slot]
            if not pv:
                pv = str(self._test_settings_cache.get("reference_pv", "") or "").strip()
        if row.get("reference_mean") is None and row.get("reference_std") is None and row.get("ref_error") is None:
            return {}
        return {
            0: {
                "slot": 0,
                "pv": pv,
                "mean": row.get("reference_mean"),
                "std": row.get("reference_std"),
                "error": row.get("ref_error"),
            }
        }

    def _project_row_reference(self, row, settings=None, reference_slot=None):
        active_settings = settings or self._test_settings_cache or {}
        reference_pvs = list(active_settings.get("reference_pvs") or [])
        stats = self._reference_stats_for_row(row)
        row["reference_stats"] = stats
        slot = reference_slot
        if slot is None:
            slot = active_settings.get("reference_slot")
        try:
            slot = None if slot is None else int(slot)
        except Exception:
            slot = None
        chosen = stats.get(slot) if slot is not None else None
        if chosen is None and stats:
            first_key = sorted(stats.keys())[0]
            chosen = stats[first_key]
            slot = int(first_key)
        if chosen is None:
            chosen = {"slot": None, "pv": "", "mean": None, "std": None, "error": None}
            slot = None
        pv = chosen.get("pv", "")
        if slot is not None and not pv and 0 <= slot < len(reference_pvs):
            pv = reference_pvs[slot]
        row["reference_slot"] = slot
        row["reference_pv"] = pv
        row["reference_mean"] = chosen.get("mean")
        row["reference_std"] = chosen.get("std")
        row["ref_error"] = chosen.get("error")
        return row

    def _reproject_measurements_for_selected_reference(self):
        if not self._measurements:
            return
        reference_pvs = self._configured_reference_pvs()
        reference_slot = self._selected_reference_slot(reference_pvs)
        selected_pv = self._selected_reference_pv(reference_pvs)
        self._test_settings_cache["reference_pvs"] = reference_pvs
        self._test_settings_cache["reference_slot"] = reference_slot
        self._test_settings_cache["reference_pv"] = selected_pv
        for row in self._measurements:
            self._project_row_reference(row, self._test_settings_cache, reference_slot)
        self._latest_metrics = self._compute_metrics(self._measurements)
        if self._demo_mode and self._latest_metrics:
            self._latest_metrics["state"] = "Demo"
        self._update_summary_labels(self._latest_metrics)
        self._populate_summary_table(self._latest_metrics.get("per_target", []))
        self._reload_results_table()
        self._update_live_graph()
        self._latest_report_markdown = self._build_report_markdown()
        self.preview_targets()
        try:
            self.refresh_status()
        except Exception:
            pass

    def _build_test_plan(self, settings):
        targets = list(settings["targets"])
        margin = float(settings["reversal_margin"])
        plan = []
        for cycle in range(1, int(settings["cycles"]) + 1):
            plan.append(
                {
                    "cycle": cycle,
                    "direction": "prep",
                    "target": settings["range_min"] - margin,
                    "measure": False,
                    "label": f"Cycle {cycle}: pre-position below min",
                }
            )
            for target in targets:
                plan.append(
                    {
                        "cycle": cycle,
                        "direction": "forward",
                        "target": target,
                        "measure": True,
                        "label": f"Cycle {cycle}: forward -> {compact_float_text(target)}",
                    }
                )
            plan.append(
                {
                    "cycle": cycle,
                    "direction": "prep",
                    "target": settings["range_max"] + margin,
                    "measure": False,
                    "label": f"Cycle {cycle}: pre-position above max",
                }
            )
            for target in reversed(targets):
                plan.append(
                    {
                        "cycle": cycle,
                        "direction": "reverse",
                        "target": target,
                        "measure": True,
                        "label": f"Cycle {cycle}: reverse -> {compact_float_text(target)}",
                    }
                )
        return plan

    def preview_targets(self):
        try:
            settings = self._target_preview_settings()
        except Exception as ex:
            self._log(f"Target preview unavailable: {ex}")
            self.target_schematic.set_preview(None, f"Sweep schematic unavailable: {ex}")
            self.target_schematic.setToolTip(f"Target preview unavailable: {ex}")
            return
        targets = settings["targets"]
        self.target_schematic.set_preview(settings, "")
        tooltip_lines = [
            f"Axis: {settings['axis_id']}",
            f"Range: {_fmt(settings['range_min'])} .. {_fmt(settings['range_max'])}",
            f"Targets ({len(targets)}): {', '.join(_fmt(v) for v in targets)}",
            f"Approach margin outside targets: {_fmt(settings['reversal_margin'])}",
            f"Generation mode: {settings.get('target_mode') or 'custom'}",
        ]
        try:
            sequence_settings = self._sequence_preview_settings()
            estimate = self._estimate_test_duration(sequence_settings)
            tooltip_lines.append(f"Estimated duration: ~{_format_duration(estimate['total_s'])} + initial approach")
        except Exception:
            pass
        if not settings["motor"]:
            tooltip_lines.append("Run note: resolve the motor record before starting the sequence.")
        if not settings["reference_pv"]:
            tooltip_lines.append("Run note: enter at least one reference PV before starting the sequence.")
        elif len([pv for pv in settings.get("reference_pvs", []) if pv]) > 1:
            tooltip_lines.append(f"Report reference: {settings['reference_pv']}")
        self.target_schematic.setToolTip("\n".join(tooltip_lines))

    def _set_test_running_state(self, running):
        self._test_active = bool(running)
        self.start_btn.setEnabled(not running)
        self.abort_btn.setEnabled(running)
        self._update_progress_display()

    def start_test(self):
        try:
            self._demo_mode = False
            settings = self._test_settings()
            self.preview_targets()
            self._set_move_params(settings["velo"], settings["accl"], accs=settings.get("accs"), vmax=settings.get("vmax"))
            self._measurements = []
            self._latest_metrics = {}
            self._latest_report_markdown = ""
            self.results_table.setRowCount(0)
            self.summary_table.setRowCount(0)
            self._update_summary_labels({"state": "Running"})
            self._update_live_graph()
            self._test_plan = self._build_test_plan(settings)
            self._test_plan_index = -1
            self._current_step = None
            self._current_phase = "idle"
            self._test_settings_cache = settings
            self._set_test_running_state(True)
            if hasattr(self, "lower_tabs"):
                self.lower_tabs.setCurrentIndex(0)
            self._update_progress_display()
            self._log(
                "Starting ISO 230-style bidirectional positioning test: "
                f"{len(settings['targets'])} targets, {settings['cycles']} cycle(s), "
                f"{len([pv for pv in settings.get('reference_pvs', []) if pv])} reference PV(s), "
                f"report reference={settings['reference_pv']}"
            )
            self._advance_test_step()
            self._test_timer.start()
        except Exception as ex:
            self._set_test_running_state(False)
            self._log(f"Failed to start ISO 230 test: {ex}")
            self._update_summary_labels({"state": f"Error: {ex}"})

    def _advance_test_step(self):
        self._test_plan_index += 1
        if self._test_plan_index >= len(self._test_plan):
            self._finish_test()
            return
        self._current_step = dict(self._test_plan[self._test_plan_index])
        self._current_phase = "wait_motion_start"
        self._sample_buffer = []
        self._next_sample_at = 0.0
        self._update_progress_display()
        try:
            self._put("VAL", self._current_step["target"])
        except Exception as ex:
            self._fail_test(f"Move failed for target {_fmt(self._current_step['target'])}: {ex}")
            return
        self._move_issued_at = time.monotonic()
        self.step_label.setText(self._current_step["label"])
        self._log(f"Executing step {self._test_plan_index + 1}/{len(self._test_plan)}: {self._current_step['label']}")

    def _enter_settle_phase(self):
        self._settle_deadline = time.monotonic() + float(self._test_settings_cache["settle_s"])
        self._current_phase = "settling"
        if self._current_step and self._current_step.get("measure"):
            self.step_label.setText(f"{self._current_step['label']} | settling")
        elif self._current_step:
            self.step_label.setText(f"{self._current_step['label']} | ready")

    def _test_tick(self):
        if not self._test_active or not self._current_step:
            self._test_timer.stop()
            return
        try:
            if self._current_phase == "wait_motion_start":
                dmov = self.client.get(self._pv("DMOV"), as_string=True)
                if not _truthy_pv(dmov):
                    self._current_phase = "waiting_dmov"
                    return
                if (time.monotonic() - self._move_issued_at) >= 0.35:
                    self._enter_settle_phase()
                return

            if self._current_phase == "waiting_dmov":
                dmov = self.client.get(self._pv("DMOV"), as_string=True)
                if _truthy_pv(dmov):
                    self._enter_settle_phase()
                return

            if self._current_phase == "settling":
                remaining = self._settle_deadline - time.monotonic()
                if remaining > 0:
                    if self._current_step.get("measure"):
                        self.step_label.setText(f"{self._current_step['label']} | settling {remaining:.1f}s")
                    return
                if not self._current_step.get("measure"):
                    self._advance_test_step()
                    return
                self._current_phase = "sampling"
                self._next_sample_at = 0.0
                self._sample_buffer = []

            if self._current_phase == "sampling":
                now = time.monotonic()
                if now < self._next_sample_at:
                    return
                sample = self._capture_sample(self._current_step)
                self._sample_buffer.append(sample)
                need = int(self._test_settings_cache["samples_per_point"])
                self.step_label.setText(
                    f"{self._current_step['label']} | sample {len(self._sample_buffer)}/{need}"
                )
                self._next_sample_at = now + float(self._test_settings_cache["sample_interval_ms"]) / 1000.0
                if len(self._sample_buffer) >= need:
                    self._finalize_measurement(self._current_step, self._sample_buffer)
                    self._advance_test_step()
        except Exception as ex:
            self._fail_test(f"Test execution failed: {ex}")

    def _capture_sample(self, step):
        reference_values = {}
        for idx, ref_pv in enumerate(self._test_settings_cache.get("reference_pvs") or []):
            if not ref_pv:
                continue
            ref_raw = self.client.get(ref_pv, as_string=True)
            reference_values[idx] = _to_float(ref_raw, f"Reference PV {idx + 1}")
        rbv_raw = self.client.get(self._pv("RBV"), as_string=True)
        val_raw = self.client.get(self._pv("VAL"), as_string=True)
        return {
            "cycle": step["cycle"],
            "direction": step["direction"],
            "target": float(step["target"]),
            "references": reference_values,
            "rbv": _to_float(rbv_raw, "RBV"),
            "command": _to_float(val_raw, "VAL"),
            "timestamp": datetime.now(),
        }

    def _finalize_measurement(self, step, samples):
        rbv_vals = [s["rbv"] for s in samples]
        cmd_vals = [s["command"] for s in samples]
        target = float(step["target"])
        reference_stats = {}
        reference_pvs = list(self._test_settings_cache.get("reference_pvs") or [])
        for idx, ref_pv in enumerate(reference_pvs):
            if not ref_pv:
                continue
            ref_vals = [s.get("references", {}).get(idx) for s in samples if idx in s.get("references", {})]
            if not ref_vals:
                continue
            ref_mean = _mean(ref_vals)
            reference_stats[idx] = {
                "slot": idx,
                "pv": ref_pv,
                "mean": ref_mean,
                "std": _stddev(ref_vals),
                "error": (ref_mean - target) if ref_mean is not None else None,
            }
        row = {
            "cycle": int(step["cycle"]),
            "direction": step["direction"],
            "target": target,
            "reference_stats": reference_stats,
            "rbv_mean": _mean(rbv_vals),
            "rbv_std": _stddev(rbv_vals),
            "command_mean": _mean(cmd_vals),
            "rbv_error": (_mean(rbv_vals) - target) if _mean(rbv_vals) is not None else None,
            "timestamp": samples[-1]["timestamp"],
        }
        self._project_row_reference(row, self._test_settings_cache, self._test_settings_cache.get("reference_slot"))
        self._measurements.append(row)
        self._append_results_row(row)
        self._latest_metrics = self._compute_metrics(self._measurements)
        self._update_summary_labels(self._latest_metrics)
        self._populate_summary_table(self._latest_metrics.get("per_target", []))
        self._update_live_graph()
        self._latest_report_markdown = self._build_report_markdown()
        self._log(
            f"Measured {row['direction']} target {_fmt(target)}: "
            f"ref={_fmt(row['reference_mean'])}, err={_fmt(row['ref_error'])}"
        )

    def _append_results_row(self, row):
        r = self.results_table.rowCount()
        self.results_table.insertRow(r)
        vals = [
            str(row["cycle"]),
            str(row["direction"]),
            _fmt(row["target"]),
            _fmt(row["reference_mean"]),
            _fmt(row["reference_std"]),
            _fmt(row["rbv_mean"]),
            _fmt(row["rbv_std"]),
            _fmt(row["ref_error"]),
            _fmt(row["rbv_error"]),
            row["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
        ]
        for c, txt in enumerate(vals):
            self.results_table.setItem(r, c, QtWidgets.QTableWidgetItem(txt))
        self.results_table.scrollToBottom()

    def _reload_results_table(self):
        self.results_table.setRowCount(0)
        for row in self._measurements:
            self._append_results_row(row)

    def _compute_metrics(self, rows):
        grouped = {}
        all_errors = []
        regression_points = []
        for row in rows:
            key = _float_key(row["target"])
            bucket = grouped.setdefault(
                key,
                {
                    "target": float(row["target"]),
                    "forward": [],
                    "reverse": [],
                },
            )
            if row["ref_error"] is not None:
                all_errors.append(float(row["ref_error"]))
            if row["reference_mean"] is not None:
                regression_points.append((float(row["target"]), float(row["reference_mean"])))
            if row["direction"] == "forward" and row["ref_error"] is not None:
                bucket["forward"].append(float(row["ref_error"]))
            elif row["direction"] == "reverse" and row["ref_error"] is not None:
                bucket["reverse"].append(float(row["ref_error"]))

        per_target = []
        max_abs_error = None
        mean_bidirectional_values = []
        reversal_values = []
        directional_mean_values = []
        bidirectional_upper = []
        bidirectional_lower = []
        forward_axis_repeatability = None
        reverse_axis_repeatability = None
        overall_unidirectional_repeatability = None
        overall_bidirectional_repeatability = None
        for key in sorted(grouped.keys(), key=lambda k: grouped[k]["target"]):
            bucket = grouped[key]
            fwd = bucket["forward"]
            rev = bucket["reverse"]
            both = list(fwd) + list(rev)
            fwd_mean = _mean(fwd)
            rev_mean = _mean(rev)
            fwd_std = _stddev(fwd)
            rev_std = _stddev(rev)
            fwd_repeat = (4.0 * float(fwd_std)) if fwd_std is not None else None
            rev_repeat = (4.0 * float(rev_std)) if rev_std is not None else None
            if fwd_mean is not None:
                directional_mean_values.append(float(fwd_mean))
            if rev_mean is not None:
                directional_mean_values.append(float(rev_mean))
            if fwd_mean is not None and rev_mean is not None:
                mean_bidir = 0.5 * (float(fwd_mean) + float(rev_mean))
                reversal = float(fwd_mean) - float(rev_mean)
            else:
                mean_bidir = fwd_mean if fwd_mean is not None else rev_mean
                reversal = None
            unidir_repeat = max(v for v in (fwd_repeat, rev_repeat) if v is not None) if (fwd_repeat is not None or rev_repeat is not None) else None
            upper_candidates = []
            lower_candidates = []
            if fwd_mean is not None:
                f_half = 0.5 * float(fwd_repeat or 0.0)
                upper_candidates.append(float(fwd_mean) + f_half)
                lower_candidates.append(float(fwd_mean) - f_half)
            if rev_mean is not None:
                r_half = 0.5 * float(rev_repeat or 0.0)
                upper_candidates.append(float(rev_mean) + r_half)
                lower_candidates.append(float(rev_mean) - r_half)
            bidir_upper = max(upper_candidates) if upper_candidates else None
            bidir_lower = min(lower_candidates) if lower_candidates else None
            if fwd_std is not None and rev_std is not None and reversal is not None:
                bidir_repeat = max(
                    math.sqrt((2.0 * float(fwd_std) * float(fwd_std)) + (2.0 * float(rev_std) * float(rev_std)) + (float(reversal) * float(reversal))),
                    float(fwd_repeat or 0.0),
                    float(rev_repeat or 0.0),
                )
            else:
                bidir_repeat = None
            point_max_abs = max((abs(v) for v in both), default=None)
            if point_max_abs is not None:
                max_abs_error = point_max_abs if max_abs_error is None else max(max_abs_error, point_max_abs)
            if mean_bidir is not None:
                mean_bidirectional_values.append(float(mean_bidir))
            if reversal is not None:
                reversal_values.append(float(reversal))
            if bidir_upper is not None:
                bidirectional_upper.append(float(bidir_upper))
            if bidir_lower is not None:
                bidirectional_lower.append(float(bidir_lower))
            if fwd_repeat is not None:
                forward_axis_repeatability = (
                    float(fwd_repeat)
                    if forward_axis_repeatability is None
                    else max(forward_axis_repeatability, float(fwd_repeat))
                )
            if rev_repeat is not None:
                reverse_axis_repeatability = (
                    float(rev_repeat)
                    if reverse_axis_repeatability is None
                    else max(reverse_axis_repeatability, float(rev_repeat))
                )
            if unidir_repeat is not None:
                overall_unidirectional_repeatability = (
                    unidir_repeat
                    if overall_unidirectional_repeatability is None
                    else max(overall_unidirectional_repeatability, unidir_repeat)
                )
            if bidir_repeat is not None:
                overall_bidirectional_repeatability = (
                    bidir_repeat
                    if overall_bidirectional_repeatability is None
                    else max(overall_bidirectional_repeatability, bidir_repeat)
                )
            per_target.append(
                {
                    "target": bucket["target"],
                    "forward_mean": fwd_mean,
                    "reverse_mean": rev_mean,
                    "mean_bidirectional_deviation": mean_bidir,
                    "forward_std": fwd_std,
                    "reverse_std": rev_std,
                    "forward_min": min(fwd) if fwd else None,
                    "forward_max": max(fwd) if fwd else None,
                    "reverse_min": min(rev) if rev else None,
                    "reverse_max": max(rev) if rev else None,
                    "forward_count": len(fwd),
                    "reverse_count": len(rev),
                    "reversal_value": reversal,
                    "reversal_magnitude": abs(float(reversal)) if reversal is not None else None,
                    "forward_repeatability": fwd_repeat,
                    "reverse_repeatability": rev_repeat,
                    "unidirectional_repeatability": unidir_repeat,
                    "bidirectional_repeatability": bidir_repeat,
                    "forward_lower_limit": (float(fwd_mean) - (0.5 * float(fwd_repeat))) if (fwd_mean is not None and fwd_repeat is not None) else None,
                    "forward_upper_limit": (float(fwd_mean) + (0.5 * float(fwd_repeat))) if (fwd_mean is not None and fwd_repeat is not None) else None,
                    "reverse_lower_limit": (float(rev_mean) - (0.5 * float(rev_repeat))) if (rev_mean is not None and rev_repeat is not None) else None,
                    "reverse_upper_limit": (float(rev_mean) + (0.5 * float(rev_repeat))) if (rev_mean is not None and rev_repeat is not None) else None,
                    "bidirectional_upper_limit": bidir_upper,
                    "bidirectional_lower_limit": bidir_lower,
                    "max_abs_error": point_max_abs,
                }
            )

        mean_bias = _mean(all_errors)
        regression = self._linear_regression(regression_points)
        range_mean_bidirectional = (
            max(mean_bidirectional_values) - min(mean_bidirectional_values)
            if len(mean_bidirectional_values) >= 2
            else None
        )
        mean_reversal_value = _mean(reversal_values)
        maximum_reversal_value = max((abs(v) for v in reversal_values), default=None)
        bidirectional_systematic_deviation = (
            max(directional_mean_values) - min(directional_mean_values)
            if len(directional_mean_values) >= 2
            else None
        )
        bidirectional_accuracy = (
            max(bidirectional_upper) - min(bidirectional_lower)
            if bidirectional_upper and bidirectional_lower
            else None
        )
        return {
            "state": "Complete" if (self._test_active is False and rows) else ("Running" if self._test_active else "Idle"),
            "target_count": len(grouped),
            "samples_total": len(rows),
            "max_abs_error": max_abs_error,
            "range_mean_bidirectional_deviation": range_mean_bidirectional,
            "mean_reversal_value": mean_reversal_value,
            "maximum_reversal_value": maximum_reversal_value,
            "forward_axis_repeatability": forward_axis_repeatability,
            "reverse_axis_repeatability": reverse_axis_repeatability,
            "unidirectional_repeatability": overall_unidirectional_repeatability,
            "bidirectional_repeatability": overall_bidirectional_repeatability,
            "bidirectional_systematic_deviation": bidirectional_systematic_deviation,
            "bidirectional_accuracy": bidirectional_accuracy,
            "mean_bias": mean_bias,
            "linearity": regression.get("max_residual"),
            "fit_slope": regression.get("slope"),
            "fit_intercept": regression.get("intercept"),
            "per_target": per_target,
        }

    def _build_iso230_svg(self, settings, metrics, width=980, height=520, compact=False):
        rows = list(metrics.get("per_target", []) or [])
        if len(rows) < 2:
            return "<p><em>Graph unavailable: at least two measured target positions are required.</em></p>"

        if compact:
            width = int(width or 760)
            height = int(height or 360)
            margin_l = 68
            margin_r = 18
            margin_t = 18
            margin_b = 52
            tick_font = 10
            label_font = 11
            title_font = 13
            axis_font = 11
            legend_font = 10
            point_radius = 3.6
            square_size = 7.0
            line_width = 2.2
            stats_box_w = 210
            stats_box_h = 82
        else:
            width = int(width or 980)
            height = int(height or 520)
            margin_l = 90
            margin_r = 26
            margin_t = 26
            margin_b = 70
            tick_font = 12
            label_font = 12
            title_font = 16
            axis_font = 13
            legend_font = 12
            point_radius = 4.8
            square_size = 9.0
            line_width = 2.8
            stats_box_w = 244
            stats_box_h = 92
        plot_w = width - margin_l - margin_r
        plot_h = height - margin_t - margin_b

        x_vals = [float(r["target"]) for r in rows]
        y_candidates = []
        for row in rows:
            for key in (
                "forward_lower_limit",
                "forward_upper_limit",
                "reverse_lower_limit",
                "reverse_upper_limit",
                "forward_mean",
                "reverse_mean",
            ):
                value = row.get(key)
                if value is not None:
                    y_candidates.append(float(value))
        if not y_candidates:
            return "<p><em>Graph unavailable: no reference-error values were measured.</em></p>"

        x_min = min(x_vals)
        x_max = max(x_vals)
        if abs(x_max - x_min) < 1e-18:
            x_max = x_min + 1.0

        y_min = min(y_candidates)
        y_max = max(y_candidates)
        if y_min > 0.0:
            y_min = 0.0
        if y_max < 0.0:
            y_max = 0.0
        y_span = y_max - y_min
        if y_span <= 0.0:
            y_span = max(abs(y_max), 1.0)
            y_min -= 0.5 * y_span
            y_max += 0.5 * y_span
        pad = max(0.08 * y_span, 1e-9)
        y_min -= pad
        y_max += pad

        def map_x(value):
            return margin_l + ((float(value) - x_min) / (x_max - x_min)) * plot_w

        def map_y(value):
            return margin_t + plot_h - ((float(value) - y_min) / (y_max - y_min)) * plot_h

        def line(x1, y1, x2, y2, stroke, width_px=1.0, dash=None, opacity=None):
            parts = [
                f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                f'stroke="{stroke}" stroke-width="{width_px:.2f}"'
            ]
            if dash:
                parts.append(f' stroke-dasharray="{dash}"')
            if opacity is not None:
                parts.append(f' opacity="{opacity:.3f}"')
            parts.append(" />")
            return "".join(parts)

        def text(x, y, value, size=12, anchor="middle", fill="#1f2937", weight="400"):
            return (
                f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" '
                f'fill="{fill}" font-family="Helvetica, Arial, sans-serif" font-weight="{weight}">'
                f"{html.escape(str(value))}</text>"
            )

        def circle(x, y, radius, fill, stroke="#ffffff", stroke_width=1.5):
            return (
                f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{stroke_width:.2f}" />'
            )

        def square(x, y, size, fill, stroke="#ffffff", stroke_width=1.5):
            half = size / 2.0
            return (
                f'<rect x="{x - half:.2f}" y="{y - half:.2f}" width="{size:.2f}" height="{size:.2f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width:.2f}" rx="1.2" ry="1.2" />'
            )

        forward_color = "#1d4ed8"
        reverse_color = "#d97706"
        backlash_color = "#7c3aed"
        forward_fill = "#93c5fd"
        reverse_fill = "#fcd34d"
        axis_color = "#0f172a"
        grid_color = "#cbd5e1"
        bg_color = "#f8fafc"

        grid = [
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="{bg_color}" rx="12" ry="12" />',
            f'<rect x="{margin_l}" y="{margin_t}" width="{plot_w}" height="{plot_h}" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.2" />',
        ]
        y_ticks = 6
        for i in range(y_ticks + 1):
            frac = i / y_ticks
            yv = y_min + (y_max - y_min) * frac
            yy = map_y(yv)
            grid.append(line(margin_l, yy, margin_l + plot_w, yy, grid_color, 1.0, dash="4 5", opacity=0.85))
            grid.append(text(margin_l - 12, yy + 4, _fmt(yv), size=tick_font, anchor="end", fill="#334155"))
        x_tick_step = max(1, int(round((len(rows) - 1) / 6.0)))
        for idx, row in enumerate(rows):
            if idx % x_tick_step != 0 and idx not in {0, len(rows) - 1}:
                continue
            xx = map_x(row["target"])
            grid.append(line(xx, margin_t, xx, margin_t + plot_h, grid_color, 1.0, dash="4 5", opacity=0.6))
            grid.append(text(xx, margin_t + plot_h + (20 if compact else 24), _fmt(row["target"]), size=tick_font, anchor="middle", fill="#334155"))

        zero_y = map_y(0.0)
        grid.append(line(margin_l, zero_y, margin_l + plot_w, zero_y, axis_color, 1.7))
        grid.append(text(margin_l + plot_w - 4, zero_y - 8, "zero error", size=legend_font, anchor="end", fill="#0f172a", weight="600"))

        forward_points = []
        reverse_points = []
        overlays = []
        for row in rows:
            x_base = map_x(row["target"])
            x_f = x_base - 8.0
            x_r = x_base + 8.0
            fmin = row.get("forward_lower_limit")
            fmax = row.get("forward_upper_limit")
            rmin = row.get("reverse_lower_limit")
            rmax = row.get("reverse_upper_limit")
            fmean = row.get("forward_mean")
            rmean = row.get("reverse_mean")
            if fmin is not None and fmax is not None:
                overlays.append(line(x_f, map_y(fmin), x_f, map_y(fmax), forward_fill, 8.0, opacity=0.9))
            if rmin is not None and rmax is not None:
                overlays.append(line(x_r, map_y(rmin), x_r, map_y(rmax), reverse_fill, 8.0, opacity=0.9))
            if fmean is not None and rmean is not None:
                overlays.append(line(x_base, map_y(fmean), x_base, map_y(rmean), backlash_color, 2.0, dash="5 4", opacity=0.9))
            if fmean is not None:
                forward_points.append((x_f, map_y(fmean)))
            if rmean is not None:
                reverse_points.append((x_r, map_y(rmean)))

        def polyline(points, stroke):
            pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            return f'<polyline fill="none" stroke="{stroke}" stroke-width="{line_width:.1f}" points="{pts}" />'

        series = []
        if len(forward_points) >= 2:
            series.append(polyline(forward_points, forward_color))
        if len(reverse_points) >= 2:
            series.append(polyline(reverse_points, reverse_color))
        for x, y in forward_points:
            series.append(circle(x, y, point_radius, forward_color))
        for x, y in reverse_points:
            series.append(square(x, y, square_size, reverse_color))

        legend_x = margin_l + 10
        legend_y = margin_t + 14
        legend = [
            line(legend_x, legend_y, legend_x + 22, legend_y, forward_color, 3.0),
            circle(legend_x + 11, legend_y, point_radius, forward_color),
            text(legend_x + 30, legend_y + 4, "Forward mean error", size=legend_font, anchor="start"),
            line(legend_x + 210, legend_y, legend_x + 232, legend_y, reverse_color, 3.0),
            square(legend_x + 221, legend_y, square_size, reverse_color),
            text(legend_x + 240, legend_y + 4, "Reverse mean error", size=legend_font, anchor="start"),
            line(legend_x + 460, legend_y - 6, legend_x + 460, legend_y + 12, backlash_color, 2.0, dash="5 4"),
            text(legend_x + 472, legend_y + 4, "Reversal gap", size=legend_font, anchor="start"),
        ]

        annotations = []
        bidir_accuracy = metrics.get("bidirectional_accuracy")
        bidir_systematic = metrics.get("bidirectional_systematic_deviation")
        bidir_repeat = metrics.get("bidirectional_repeatability")
        if not compact:
            stats_box_x = margin_l + plot_w - stats_box_w - 20
            stats_box_y = margin_t + 14
            annotations.append(
                f'<rect x="{stats_box_x}" y="{stats_box_y}" width="{stats_box_w}" height="{stats_box_h}" fill="#ffffff" stroke="#94a3b8" stroke-width="1.2" rx="8" ry="8" />'
            )
            annotations.append(text(stats_box_x + 14, stats_box_y + 22, "Summary metrics", size=legend_font, anchor="start", weight="700"))
            annotations.append(text(stats_box_x + 14, stats_box_y + 42, f"BiDir accuracy: {_fmt(bidir_accuracy)}", size=legend_font, anchor="start"))
            annotations.append(text(stats_box_x + 14, stats_box_y + 60, f"BiDir systematic: {_fmt(bidir_systematic)}", size=legend_font, anchor="start"))
            annotations.append(text(stats_box_x + 14, stats_box_y + 78, f"BiDir repeatability: {_fmt(bidir_repeat)}", size=legend_font, anchor="start"))

        title = [
            text(width / 2.0, 18, "Bidirectional Positioning Error Graph", size=title_font, anchor="middle", weight="700"),
            text(width / 2.0, height - 18, f"Target position on axis {settings.get('axis_id', '')}", size=axis_font, anchor="middle", weight="600"),
            (
                f'<g transform="translate(24 {margin_t + (plot_h / 2.0):.2f}) rotate(-90)">'
                + text(0, 0, "Reference error relative to commanded target", size=axis_font, anchor="middle", weight="600")
                + "</g>"
            ),
        ]

        svg_parts = title + grid + overlays + series + legend + annotations
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="Bidirectional positioning error graph">'
            + "".join(svg_parts)
            + "</svg>"
        )

    def _linear_regression(self, points):
        pts = [(float(x), float(y)) for x, y in points]
        if len(pts) < 2:
            return {"slope": None, "intercept": None, "max_residual": None}
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x_mean = _mean(xs)
        y_mean = _mean(ys)
        if x_mean is None or y_mean is None:
            return {"slope": None, "intercept": None, "max_residual": None}
        den = math.fsum((x - x_mean) * (x - x_mean) for x in xs)
        if abs(den) < 1e-18:
            return {"slope": None, "intercept": None, "max_residual": None}
        num = math.fsum((x - x_mean) * (y - y_mean) for x, y in pts)
        slope = num / den
        intercept = y_mean - slope * x_mean
        residuals = [abs(y - (intercept + slope * x)) for x, y in pts]
        return {"slope": slope, "intercept": intercept, "max_residual": max(residuals) if residuals else None}

    def _update_summary_labels(self, metrics):
        values = {
            "state": metrics.get("state", "Idle"),
            "target_count": str(metrics.get("target_count", "-")),
            "samples_total": str(metrics.get("samples_total", "-")),
            "bidirectional_accuracy": _fmt(metrics.get("bidirectional_accuracy")),
            "bidirectional_systematic_deviation": _fmt(metrics.get("bidirectional_systematic_deviation")),
            "bidirectional_repeatability": _fmt(metrics.get("bidirectional_repeatability")),
            "unidirectional_repeatability": _fmt(metrics.get("unidirectional_repeatability")),
            "mean_reversal_value": _fmt(metrics.get("mean_reversal_value")),
        }
        for key, label in self.summary_labels.items():
            label.setText(values.get(key, "-") or "-")

    def _populate_summary_table(self, rows):
        self.summary_table.setRowCount(0)
        for row in rows:
            r = self.summary_table.rowCount()
            self.summary_table.insertRow(r)
            vals = [
                _fmt(row["target"]),
                _fmt(row.get("mean_bidirectional_deviation")),
                _fmt(row.get("reversal_value")),
                _fmt(row.get("unidirectional_repeatability")),
                _fmt(row.get("bidirectional_repeatability")),
                _fmt(row["forward_mean"]),
                _fmt(row["reverse_mean"]),
                _fmt(row["max_abs_error"]),
            ]
            for c, txt in enumerate(vals):
                self.summary_table.setItem(r, c, QtWidgets.QTableWidgetItem(txt))

    def _finish_test(self):
        self._test_timer.stop()
        self._set_test_running_state(False)
        self._current_phase = "done"
        self._current_step = None
        self.step_label.setText("Complete")
        self._latest_metrics = self._compute_metrics(self._measurements)
        self._latest_metrics["state"] = "Complete"
        self._update_summary_labels(self._latest_metrics)
        self._populate_summary_table(self._latest_metrics.get("per_target", []))
        self._update_live_graph()
        self._latest_report_markdown = self._build_report_markdown()
        self._log(f"ISO 230 test complete with {len(self._measurements)} measured points")
        self._update_progress_display()

    def _fail_test(self, message):
        self._test_timer.stop()
        self._set_test_running_state(False)
        self._current_phase = "error"
        self.step_label.setText(message)
        self._log(message)
        self._latest_metrics = self._compute_metrics(self._measurements)
        self._latest_metrics["state"] = f"Error: {message}"
        self._update_summary_labels(self._latest_metrics)
        self._update_live_graph()
        self._update_progress_display()

    def abort_test(self):
        if not self._test_active:
            return
        self._log("ISO 230 test aborted by user")
        self._test_timer.stop()
        self._set_test_running_state(False)
        self._current_phase = "aborted"
        self.step_label.setText("Aborted")
        try:
            self.stop_motion()
        except Exception as ex:
            self._log(f"Abort stop failed: {ex}")
        self._latest_metrics = self._compute_metrics(self._measurements)
        self._latest_metrics["state"] = "Aborted"
        self._update_summary_labels(self._latest_metrics)
        self._update_live_graph()
        self._update_progress_display()

    def stop_motion(self):
        try:
            try:
                self._put("JOGF", 0, quiet=True)
            except Exception:
                pass
            try:
                self._put("JOGR", 0, quiet=True)
            except Exception:
                pass
            stop_ok = False
            try:
                self._put("STOP", 1)
                stop_ok = True
            except Exception as ex:
                self._log(f"STOP field failed ({ex}), trying SPMG=0")
            if not stop_ok:
                try:
                    self._put("SPMG", 0)
                    stop_ok = True
                except Exception as ex:
                    self._log(f"SPMG stop failed ({ex})")
            if not stop_ok:
                raise RuntimeError("No supported stop field worked")
        except Exception as ex:
            self._log(f"Stop failed: {ex}")

    def _build_report_markdown(self):
        settings = getattr(self, "_test_settings_cache", None)
        metrics = dict(self._latest_metrics or {})
        rows = list(self._measurements or [])
        if not settings:
            return "# ISO 230 Report\n\nNo report data available.\n"
        operator_comments = str(self._operator_comments or "").strip()

        graph_svg = self._build_iso230_svg(settings, metrics, width=760, height=340, compact=True)
        lines = [
            "# ecmc ISO 230 Bidirectional Positioning Report",
            "",
            f"Generated: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`",
            "",
            "## Status",
            "",
            f"- State: `{metrics.get('state', '')}`",
            f"- Bidirectional accuracy: `{_fmt(metrics.get('bidirectional_accuracy'))}`",
            f"- Bidirectional systematic deviation: `{_fmt(metrics.get('bidirectional_systematic_deviation'))}`",
            f"- Range of mean bidirectional positional deviation: `{_fmt(metrics.get('range_mean_bidirectional_deviation'))}`",
            f"- Bidirectional repeatability: `{_fmt(metrics.get('bidirectional_repeatability'))}`",
            f"- Unidirectional repeatability: `{_fmt(metrics.get('unidirectional_repeatability'))}`",
            f"- Mean reversal value: `{_fmt(metrics.get('mean_reversal_value'))}`",
            f"- Maximum reversal value: `{_fmt(metrics.get('maximum_reversal_value'))}`",
            f"- Mean bias: `{_fmt(metrics.get('mean_bias'))}`",
            f"- Linearity residual: `{_fmt(metrics.get('linearity'))}`",
            f"- Linear fit slope: `{_fmt(metrics.get('fit_slope'))}`",
            f"- Linear fit intercept: `{_fmt(metrics.get('fit_intercept'))}`",
            "",
            "## Configuration",
            "",
            f"- IOC prefix: `{settings.get('prefix', '')}`",
            f"- Axis ID: `{settings.get('axis_id', '')}`",
            f"- Motor record: `{settings.get('motor', '')}`",
            f"- Configured reference PVs: `{_reference_pv_summary_text(settings)}`",
            f"- Reference used for report calculations: `{settings.get('reference_pv', '')}`",
            f"- Range: `{_fmt(settings.get('range_min'))} .. {_fmt(settings.get('range_max'))}`",
            f"- Target generation mode: `{settings.get('target_mode', '')}`",
            f"- Target generation rule: `{settings.get('target_rule_note', '')}`",
            f"- Base interval: `{_fmt(settings.get('base_interval'))}`",
            f"- Targets: `{', '.join(_fmt(v) for v in settings.get('targets', []))}`",
            f"- Cycles: `{settings.get('cycles', '')}`",
            f"- Display decimals: `{settings.get('display_decimals', _FORMAT_DECIMALS)}`",
            f"- Settle time: `{_fmt(settings.get('settle_s'))} s`",
            f"- Samples per point: `{settings.get('samples_per_point', '')}`",
            f"- Sample interval: `{settings.get('sample_interval_ms', '')} ms`",
            f"- Approach margin outside targets: `{_fmt(settings.get('reversal_margin'))}`",
            (
                "- Motion parameters: "
                f"`VELO={_fmt(settings.get('velo'))} "
                f"ACCL={_fmt(settings.get('accl'))} "
                f"VMAX={_fmt(settings.get('vmax'))} "
                f"ACCS={_fmt(settings.get('accs'))}`"
            ),
            "",
            "## Graph",
            "",
            "Bidirectional positioning error relative to commanded target. Forward mean error is shown in blue, reverse mean error in amber, ISO repeatability intervals are shown as vertical bars, and the reversal value is the dashed violet segment at each target.",
            "",
            graph_svg,
            "",
            "## Notes",
            "",
            "- This workflow uses ISO 230-style bidirectional positioning terminology derived from the supplied reference document and is not presented as certified ISO 230-2 compliance evidence.",
            "- Mean bidirectional positional deviation is calculated as the average of the forward and reverse mean reference errors at each target.",
            "- Reversal value at a target is calculated as forward mean error minus reverse mean error; the axis reversal value is the maximum absolute reversal over all targets.",
            "- Unidirectional repeatability is calculated as 4 times the sample standard deviation for each direction at each target.",
                "- Bidirectional repeatability is calculated as max(sqrt(2*s_f^2 + 2*s_r^2 + B_i^2), R_i^+, R_i^-).",
                "",
                "## Per-Target Results",
                "",
                "| Target | Mean BiDir Dev | Reversal | Uni Repeat | BiDir Repeat | Fwd Mean Err | Rev Mean Err | Max Abs Err |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]

        for row in metrics.get("per_target", []):
            lines.append(
                f"| {_fmt(row.get('target'))} | {_fmt(row.get('mean_bidirectional_deviation'))} | {_fmt(row.get('reversal_value'))} | "
                f"{_fmt(row.get('unidirectional_repeatability'))} | {_fmt(row.get('bidirectional_repeatability'))} | "
                f"{_fmt(row.get('forward_mean'))} | {_fmt(row.get('reverse_mean'))} | "
                f"{_fmt(row.get('max_abs_error'))} |"
                    )

        if operator_comments:
            lines.extend(
                [
                    "",
                    "## Operator Comments",
                    "",
                ]
            )
            lines.extend(operator_comments.splitlines())

        lines.extend(
            [
                "",
                "## Raw Measured Points",
                "",
                f"Selected reference for report/error columns: `{settings.get('reference_pv', '')}`",
                "",
                "| Cycle | Direction | Target | Ref Mean | Ref Std | RBV Mean | RBV Std | Ref Err | RBV Err | Timestamp |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )

        for row in rows:
            lines.append(
                f"| {row.get('cycle')} | {row.get('direction')} | {_fmt(row.get('target'))} | "
                f"{_fmt(row.get('reference_mean'))} | {_fmt(row.get('reference_std'))} | "
                f"{_fmt(row.get('rbv_mean'))} | {_fmt(row.get('rbv_std'))} | "
                f"{_fmt(row.get('ref_error'))} | {_fmt(row.get('rbv_error'))} | "
                f"{row.get('timestamp').strftime('%Y-%m-%d %H:%M:%S')} |"
            )

        appendix_refs = _nonselected_reference_slots(settings)
        if appendix_refs:
            lines.extend(
                [
                    "",
                    "## Appendix: Additional Reference PVs",
                    "",
                    "The following reference PVs were acquired during the test but were not used for the main report calculations.",
                ]
            )
            for slot, pv in appendix_refs:
                lines.extend(
                    [
                        "",
                        f"### Ref {slot + 1}: `{pv}`",
                        "",
                        "| Cycle | Direction | Target | Ref Mean | Ref Std | Ref Err | Timestamp |",
                        "| --- | --- | --- | --- | --- | --- | --- |",
                    ]
                )
                for row in rows:
                    stats = dict(row.get("reference_stats") or {})
                    stat = dict(stats.get(slot) or {})
                    if not stat:
                        continue
                    lines.append(
                        f"| {row.get('cycle')} | {row.get('direction')} | {_fmt(row.get('target'))} | "
                        f"{_fmt(stat.get('mean'))} | {_fmt(stat.get('std'))} | {_fmt(stat.get('error'))} | "
                        f"{row.get('timestamp').strftime('%Y-%m-%d %H:%M:%S')} |"
                    )

        return "\n".join(lines) + "\n"

    def _build_report_preview_html(self):
        settings = getattr(self, "_test_settings_cache", None)
        metrics = dict(self._latest_metrics or {})
        rows = list(self._measurements or [])
        if not settings:
            return "<html><body><p>No report data loaded.</p></body></html>"

        def h(text):
            return html.escape(str(text))

        graph_svg = self._build_iso230_svg(settings, metrics)
        graph_uri = "data:image/svg+xml;utf8," + urllib.parse.quote(graph_svg)
        preview_row_limit = 18
        per_target_rows = []
        for row in metrics.get("per_target", []):
            per_target_rows.append(
                "<tr>"
                f"<td>{h(_fmt_preview(row.get('target')))}</td>"
                f"<td>{h(_fmt_preview(row.get('mean_bidirectional_deviation')))}</td>"
                f"<td>{h(_fmt_preview(row.get('reversal_value')))}</td>"
                f"<td>{h(_fmt_preview(row.get('unidirectional_repeatability')))}</td>"
                f"<td>{h(_fmt_preview(row.get('bidirectional_repeatability')))}</td>"
                f"<td>{h(_fmt_preview(row.get('forward_mean')))}</td>"
                f"<td>{h(_fmt_preview(row.get('reverse_mean')))}</td>"
                f"<td>{h(_fmt_preview(row.get('max_abs_error')))}</td>"
                "</tr>"
            )

        raw_rows = []
        for row in rows[:preview_row_limit]:
            raw_rows.append(
                "<tr>"
                f"<td>{h(row.get('cycle'))}</td>"
                f"<td>{h(row.get('direction'))}</td>"
                f"<td>{h(_fmt_preview(row.get('target')))}</td>"
                f"<td>{h(_fmt_preview(row.get('reference_mean')))}</td>"
                f"<td>{h(_fmt_preview(row.get('reference_std')))}</td>"
                f"<td>{h(_fmt_preview(row.get('rbv_mean')))}</td>"
                f"<td>{h(_fmt_preview(row.get('rbv_std')))}</td>"
                f"<td>{h(_fmt_preview(row.get('ref_error')))}</td>"
                f"<td>{h(_fmt_preview(row.get('rbv_error')))}</td>"
                f"<td>{h(row.get('timestamp').strftime('%Y-%m-%d %H:%M:%S'))}</td>"
                "</tr>"
            )
        metrics_rows = [
            ("State", metrics.get("state", "")),
            ("Bidirectional accuracy", _fmt_preview(metrics.get("bidirectional_accuracy"))),
            ("Bidirectional systematic deviation", _fmt_preview(metrics.get("bidirectional_systematic_deviation"))),
            ("Range of mean bidirectional positional deviation", _fmt_preview(metrics.get("range_mean_bidirectional_deviation"))),
            ("Bidirectional repeatability", _fmt_preview(metrics.get("bidirectional_repeatability"))),
            ("Unidirectional repeatability", _fmt_preview(metrics.get("unidirectional_repeatability"))),
            ("Mean reversal value", _fmt_preview(metrics.get("mean_reversal_value"))),
            ("Maximum reversal value", _fmt_preview(metrics.get("maximum_reversal_value"))),
            ("Linear fit slope", _fmt_preview(metrics.get("fit_slope"))),
            ("Linear fit offset", _fmt_preview(metrics.get("fit_intercept"))),
        ]
        metric_rows_html = []
        for idx in range(0, len(metrics_rows), 2):
            left = metrics_rows[idx]
            right = metrics_rows[idx + 1] if idx + 1 < len(metrics_rows) else ("", "")
            metric_rows_html.append(
                "<tr>"
                f"<th>{h(left[0])}</th><td class='value-cell'>{h(left[1] or '-')}</td>"
                f"<th>{h(right[0])}</th><td class='value-cell'>{h(right[1] or '-')}</td>"
                "</tr>"
            )

        config_rows = [
            ("IOC prefix", settings.get("prefix", "")),
            ("Axis ID", settings.get("axis_id", "")),
            ("Motor record", settings.get("motor", "")),
            ("Configured reference PVs", _reference_pv_summary_text(settings)),
            ("Reference used for report", settings.get("reference_pv", "")),
            ("Range", f"{_fmt_preview(settings.get('range_min'))} .. {_fmt_preview(settings.get('range_max'))}"),
            ("Target generation mode", settings.get("target_mode", "")),
            ("Target generation rule", settings.get("target_rule_note", "")),
            ("Base interval", _fmt_preview(settings.get("base_interval"))),
            ("Targets", ", ".join(_fmt_preview(v) for v in settings.get("targets", []))),
            ("Display decimals", settings.get("display_decimals", _FORMAT_DECIMALS)),
            ("Cycles", settings.get("cycles", "")),
            ("Settle time", f"{_fmt_preview(settings.get('settle_s'))} s"),
            ("Samples per point", settings.get("samples_per_point", "")),
            ("Sample interval", f"{settings.get('sample_interval_ms', '')} ms"),
            ("Approach margin outside targets", _fmt_preview(settings.get("reversal_margin"))),
            (
                "Motion parameters",
                f"VELO={_fmt_preview(settings.get('velo'))}  ACCL={_fmt_preview(settings.get('accl'))}  "
                f"VMAX={_fmt_preview(settings.get('vmax'))}  ACCS={_fmt_preview(settings.get('accs'))}",
            ),
        ]
        config_rows_html = []
        for label, value in config_rows:
            config_rows_html.append(
                "<tr>"
                f"<th>{h(label)}</th>"
                f"<td><code>{h(value)}</code></td>"
                "</tr>"
            )

        raw_note = ""
        if len(rows) > preview_row_limit:
            raw_note = (
                f"<div class='note'>Showing the first {preview_row_limit} of {len(rows)} measured points. "
                "Use <b>Save Markdown</b> for the full report.</div>"
            )

        return f"""<html>
<head>
<meta charset="utf-8">
<style>
body {{ font-family: Helvetica, Arial, sans-serif; color: #172033; margin: 0; background: #e9eef5; }}
.page {{ max-width: 1180px; margin: 0 auto; padding: 18px; }}
h1 {{ margin: 0; font-size: 28px; line-height: 1.15; color: #0f1b3d; }}
h2 {{ margin: 0 0 10px 0; font-size: 20px; color: #12213f; }}
.subtitle {{ margin-top: 8px; color: #475569; font-size: 13px; }}
.hero {{ background: linear-gradient(135deg, #ffffff 0%, #eef4ff 100%); border: 1px solid #d7e0eb; border-radius: 14px; padding: 18px 20px; margin-bottom: 16px; }}
.card {{ background: #ffffff; border: 1px solid #d7e0eb; border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }}
code {{ font-family: Menlo, Monaco, monospace; background: #f3f6fa; padding: 1px 4px; border-radius: 4px; }}
.graph-shell {{ background: linear-gradient(180deg, #f8fbff 0%, #ffffff 100%); border: 1px solid #dbe6f3; border-radius: 12px; padding: 12px; }}
.graph-shell img {{ width: 100%; height: auto; display: block; border-radius: 8px; }}
.note {{ color: #475569; font-size: 13px; margin-bottom: 10px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 12px; table-layout: fixed; }}
th, td {{ border: 1px solid #d8e0ea; padding: 7px 8px; text-align: left; vertical-align: top; word-wrap: break-word; }}
th {{ background: #eef4fb; color: #24324a; }}
tr:nth-child(even) td {{ background: #fafcfe; }}
.metrics-table th {{ width: 23%; }}
.metrics-table td {{ width: 27%; }}
.value-cell {{ font-weight: 700; color: #102046; }}
.compact th, .compact td {{ font-size: 11px; padding: 6px 7px; }}
</style>
</head>
<body>
<div class="page">
<div class="hero">
<h1>ecmc ISO 230 Bidirectional Positioning Report</h1>
<div class="subtitle">Generated <code>{h(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</code> from the current test dataset.</div>
</div>

<div class="card">
<h2>Status</h2>
<table class="metrics-table">
<tbody>
{''.join(metric_rows_html)}
</tbody>
</table>
</div>

<div class="card">
<h2>Configuration</h2>
<table>
<tbody>
{''.join(config_rows_html)}
</tbody>
</table>
</div>

<div class="card">
<h2>Graph</h2>
<div class="note">Forward mean error is blue, reverse mean error is amber, and the dashed violet segments show the reversal gap at each target.</div>
<div class="graph-shell"><img src="{graph_uri}" alt="Bidirectional positioning error graph"></div>
</div>

<div class="card">
<h2>Per-Target Results</h2>
<table>
<thead>
<tr>
<th>Target</th>
<th>Mean BiDir Dev</th>
<th>Reversal</th>
<th>Uni Repeat</th>
<th>BiDir Repeat</th>
<th>Fwd Mean Err</th>
<th>Rev Mean Err</th>
<th>Max |Err|</th>
</tr>
</thead>
<tbody>
{''.join(per_target_rows)}
</tbody>
</table>
</div>

<div class="card">
<h2>Raw Measured Points</h2>
{raw_note}
<table class="compact">
<thead>
<tr>
<th>Cycle</th>
<th>Direction</th>
<th>Target</th>
<th>Ref Mean</th>
<th>Ref Std</th>
<th>RBV Mean</th>
<th>RBV Std</th>
<th>Ref Err</th>
<th>RBV Err</th>
<th>Timestamp</th>
</tr>
</thead>
<tbody>
{''.join(raw_rows)}
</tbody>
</table>
</div>
</div>
</body>
</html>"""

    def _build_preview_summary_tab(self):
        settings = getattr(self, "_test_settings_cache", None) or {}
        metrics = dict(self._latest_metrics or {})
        rows = list(self._measurements or [])

        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        title = QtWidgets.QLabel("ecmc ISO 230 Bidirectional Positioning Report")
        title.setStyleSheet("font-size: 24px; font-weight: 700; color: #0f1b3d;")
        subtitle = QtWidgets.QLabel(
            f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} from the current test dataset"
        )
        subtitle.setStyleSheet("color: #516079;")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(12)

        status_box = QtWidgets.QGroupBox("Status")
        status_form = QtWidgets.QFormLayout(status_box)
        status_form.setLabelAlignment(QtCore.Qt.AlignRight)
        status_form.setHorizontalSpacing(10)
        status_form.setVerticalSpacing(6)
        status_rows = [
            ("State", metrics.get("state", "")),
            ("Bidirectional accuracy", _fmt_preview(metrics.get("bidirectional_accuracy"))),
            ("Bidirectional systematic deviation", _fmt_preview(metrics.get("bidirectional_systematic_deviation"))),
            ("Range of mean bidirectional positional deviation", _fmt_preview(metrics.get("range_mean_bidirectional_deviation"))),
            ("Bidirectional repeatability", _fmt_preview(metrics.get("bidirectional_repeatability"))),
            ("Unidirectional repeatability", _fmt_preview(metrics.get("unidirectional_repeatability"))),
            ("Mean reversal value", _fmt_preview(metrics.get("mean_reversal_value"))),
            ("Maximum reversal value", _fmt_preview(metrics.get("maximum_reversal_value"))),
            ("Linear fit slope", _fmt_preview(metrics.get("fit_slope"))),
            ("Linear fit offset", _fmt_preview(metrics.get("fit_intercept"))),
            ("Measured points", str(len(rows))),
        ]
        for label, value in status_rows:
            v = QtWidgets.QLabel(str(value or "-"))
            if label in {"State", "Bidirectional accuracy", "Bidirectional repeatability"}:
                v.setStyleSheet("font-weight: 700; color: #102046;")
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            status_form.addRow(label, v)
        top_row.addWidget(status_box, 4)

        cfg_box = QtWidgets.QGroupBox("Configuration")
        cfg_form = QtWidgets.QFormLayout(cfg_box)
        cfg_form.setLabelAlignment(QtCore.Qt.AlignRight)
        cfg_form.setHorizontalSpacing(10)
        cfg_form.setVerticalSpacing(6)
        cfg_rows = [
            ("IOC prefix", settings.get("prefix", "")),
            ("Axis ID", settings.get("axis_id", "")),
            ("Motor record", settings.get("motor", "")),
            ("Configured reference PVs", _reference_pv_summary_text(settings)),
            ("Reference used for report", settings.get("reference_pv", "")),
            ("Range", f"{_fmt_preview(settings.get('range_min'))} .. {_fmt_preview(settings.get('range_max'))}"),
            ("Target generation mode", settings.get("target_mode", "")),
            ("Target generation rule", settings.get("target_rule_note", "")),
            ("Base interval", _fmt_preview(settings.get("base_interval"))),
            ("Targets", ", ".join(_fmt_preview(v) for v in settings.get("targets", []))),
            ("Display decimals", settings.get("display_decimals", _FORMAT_DECIMALS)),
            ("Cycles", settings.get("cycles", "")),
            ("Settle time", f"{_fmt_preview(settings.get('settle_s'))} s"),
            ("Samples per point", settings.get("samples_per_point", "")),
            ("Sample interval", f"{settings.get('sample_interval_ms', '')} ms"),
            ("Approach margin outside targets", _fmt_preview(settings.get("reversal_margin"))),
        ]
        for label, value in cfg_rows:
            v = QtWidgets.QLabel(str(value or "-"))
            v.setWordWrap(True)
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            cfg_form.addRow(label, v)
        top_row.addWidget(cfg_box, 6)
        layout.addLayout(top_row)

        comments_box = QtWidgets.QGroupBox("Operator Comments")
        comments_layout = QtWidgets.QVBoxLayout(comments_box)
        comments_layout.setContentsMargins(8, 8, 8, 8)
        comments_layout.setSpacing(6)
        comments_note = QtWidgets.QLabel("These comments are included in the exported report and saved in session files.")
        comments_note.setWordWrap(True)
        comments_note.setStyleSheet("color: #516079;")
        comments_edit = QtWidgets.QPlainTextEdit()
        comments_edit.setPlaceholderText("Enter operator observations, setup notes, exceptions, or environmental comments...")
        comments_edit.setPlainText(self._operator_comments)
        comments_edit.setFixedHeight(_scaled_px(120))
        comments_edit.textChanged.connect(lambda: self._set_operator_comments(comments_edit.toPlainText()))
        comments_layout.addWidget(comments_note)
        comments_layout.addWidget(comments_edit)
        layout.addWidget(comments_box)
        layout.addStretch(1)
        return root

    def _build_preview_graph_tab(self):
        settings = getattr(self, "_test_settings_cache", None) or {}
        metrics = dict(self._latest_metrics or {})
        graph_svg = self._build_iso230_svg(settings, metrics)

        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        note = QtWidgets.QLabel(
            "Forward mean error is blue, reverse mean error is amber, and the dashed violet segments show the reversal gap at each target."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #516079;")
        layout.addWidget(note)

        frame = QtWidgets.QFrame(root)
        frame.setStyleSheet("background: white; border: 1px solid #d7e0eb; border-radius: 8px;")
        frame_layout = QtWidgets.QVBoxLayout(frame)
        frame_layout.setContentsMargins(4, 4, 4, 4)
        graph_svg_widget = QSvgWidget(frame)
        graph_svg_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        graph_placeholder = QtWidgets.QLabel(frame)
        graph_placeholder.setAlignment(QtCore.Qt.AlignCenter)
        graph_placeholder.setWordWrap(True)
        graph_placeholder.setStyleSheet("color: #516079; background: transparent; border: none;")
        frame_layout.addWidget(graph_svg_widget, 1)
        frame_layout.addWidget(graph_placeholder, 1)
        if str(graph_svg).lstrip().startswith("<svg"):
            graph_placeholder.hide()
            graph_svg_widget.load(QtCore.QByteArray(graph_svg.encode("utf-8")))
            graph_svg_widget.show()
        else:
            graph_svg_widget.hide()
            msg = re.sub(r"<[^>]+>", "", str(graph_svg)).strip() or "Graph unavailable."
            graph_placeholder.setText(msg)
            graph_placeholder.show()
        layout.addWidget(frame, 1)
        return root

    def _build_preview_per_target_tab(self):
        rows = list((self._latest_metrics or {}).get("per_target", []) or [])
        table = QtWidgets.QTableWidget(len(rows), 8)
        table.setHorizontalHeaderLabels(
            [
                "Target",
                "Mean BiDir Dev",
                "Reversal",
                "Uni Repeat",
                "BiDir Repeat",
                "Fwd Mean Err",
                "Rev Mean Err",
                "Max |Err|",
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        for r, row in enumerate(rows):
            vals = [
                _fmt_preview(row.get("target")),
                _fmt_preview(row.get("mean_bidirectional_deviation")),
                _fmt_preview(row.get("reversal_value")),
                _fmt_preview(row.get("unidirectional_repeatability")),
                _fmt_preview(row.get("bidirectional_repeatability")),
                _fmt_preview(row.get("forward_mean")),
                _fmt_preview(row.get("reverse_mean")),
                _fmt_preview(row.get("max_abs_error")),
            ]
            for c, val in enumerate(vals):
                table.setItem(r, c, _table_item(val, align_right=True))
        return table

    def _build_preview_raw_tab(self):
        rows = list(self._measurements or [])
        table = QtWidgets.QTableWidget(len(rows), 10)
        table.setHorizontalHeaderLabels(
            [
                "Cycle",
                "Direction",
                "Target",
                "Ref Mean",
                "Ref Std",
                "RBV Mean",
                "RBV Std",
                "Ref Err",
                "RBV Err",
                "Timestamp",
            ]
        )
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        for r, row in enumerate(rows):
            vals = [
                str(row.get("cycle")),
                str(row.get("direction")),
                _fmt_preview(row.get("target")),
                _fmt_preview(row.get("reference_mean")),
                _fmt_preview(row.get("reference_std")),
                _fmt_preview(row.get("rbv_mean")),
                _fmt_preview(row.get("rbv_std")),
                _fmt_preview(row.get("ref_error")),
                _fmt_preview(row.get("rbv_error")),
                row.get("timestamp").strftime("%Y-%m-%d %H:%M:%S"),
            ]
            for c, val in enumerate(vals):
                table.setItem(r, c, _table_item(val, align_right=(c not in {1, 9})))
        return table

    def _current_session_settings(self):
        settings = dict(self._test_settings_cache or {})
        try:
            settings.update(self._sequence_preview_settings())
        except Exception:
            settings.setdefault("display_decimals", int(self.decimals_spin.value()))
        settings["axis_prefix_cfg_pv"] = self.axis_pfx_cfg_pv_edit.text().strip()
        settings["motor_name_cfg_pv"] = self.motor_name_cfg_pv_edit.text().strip()
        settings["motor"] = self._committed_motor_record_text() or settings.get("motor", "")
        settings["reference_pvs"] = self._configured_reference_pvs()
        settings["reference_slot"] = self._selected_reference_slot(settings["reference_pvs"])
        settings["reference_pv"] = self._selected_reference_pv(settings["reference_pvs"])
        return settings

    def _set_operator_comments(self, text):
        self._operator_comments = str(text or "")
        if self._test_settings_cache:
            self._latest_report_markdown = self._build_report_markdown()

    def _serialize_session_payload(self):
        settings = self._current_session_settings()
        return {
            "file_type": "ecmc_iso230_session",
            "version": 1,
            "saved_at": datetime.now().isoformat(),
            "state": (self._latest_metrics or {}).get("state", "Saved"),
            "operator_comments": self._operator_comments,
            "settings": settings,
            "measurements": [
                {
                    "cycle": row.get("cycle"),
                    "direction": row.get("direction"),
                    "target": row.get("target"),
                    "reference_slot": row.get("reference_slot"),
                    "reference_pv": row.get("reference_pv", ""),
                    "reference_mean": row.get("reference_mean"),
                    "reference_std": row.get("reference_std"),
                    "rbv_mean": row.get("rbv_mean"),
                    "rbv_std": row.get("rbv_std"),
                    "command_mean": row.get("command_mean"),
                    "ref_error": row.get("ref_error"),
                    "rbv_error": row.get("rbv_error"),
                    "reference_stats": _serialize_reference_stats(row.get("reference_stats")),
                    "timestamp": row.get("timestamp").isoformat() if row.get("timestamp") else "",
                }
                for row in self._measurements
            ],
        }

    def save_session_file(self):
        payload = self._serialize_session_payload()
        default_name = f"iso230_session_axis_{self._axis_id_text() or 'unknown'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path, _flt = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save ISO 230 Session Data",
            str(Path.home() / default_name),
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self._log(f"Saved session data: {path}")
        except Exception as ex:
            self._log(f"Failed to save session data: {ex}")

    def load_session_file(self):
        if self._test_active:
            self.abort_test()
        path, _flt = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open ISO 230 Session Data",
            str(Path.home()),
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            file_type = str(payload.get("file_type", "") or "").strip()
            has_session_shape = isinstance(payload, dict) and (
                "settings" in payload or "measurements" in payload
            )
            if file_type and file_type != "ecmc_iso230_session":
                raise RuntimeError("Unsupported session file")
            if not file_type and not has_session_shape:
                raise RuntimeError("Unsupported session file")
            settings = dict(payload.get("settings") or {})
            rows = []
            for source_row in list(payload.get("measurements") or []):
                row = dict(source_row)
                timestamp_txt = str(row.get("timestamp") or "").strip()
                row["timestamp"] = _parse_saved_timestamp(timestamp_txt)
                row["reference_stats"] = _deserialize_reference_stats(row.get("reference_stats"))
                rows.append(row)
            state = str(payload.get("state") or "Loaded")
            self._apply_report_dataset(settings, rows, state=state)
            self._set_operator_comments(payload.get("operator_comments", ""))
            self.step_label.setText(f"Loaded session: {Path(path).name}")
            self._log(f"Loaded session data: {path}")
        except Exception as ex:
            self._log(f"Failed to load session data: {ex}")

    def _apply_report_dataset(self, settings, rows, state="Loaded"):
        self._demo_mode = str(state).lower() == "demo"
        self._poll_failure_cache.clear()
        decimals = int(settings.get("display_decimals", _FORMAT_DECIMALS))
        self.decimals_spin.blockSignals(True)
        self.decimals_spin.setValue(decimals)
        self.decimals_spin.blockSignals(False)
        _set_format_decimals(decimals)
        self.prefix_edit.setText(str(settings.get("prefix", "")))
        self.axis_edit.setText(str(settings.get("axis_id", "")))
        self.axis_pfx_cfg_pv_edit.setText(str(settings.get("axis_prefix_cfg_pv", self.axis_pfx_cfg_pv_edit.text())))
        self.motor_name_cfg_pv_edit.setText(str(settings.get("motor_name_cfg_pv", self.motor_name_cfg_pv_edit.text())))
        self._commit_cfg_pv_edits()
        self.motor_record_edit.setText(str(settings.get("motor", "")))
        self._committed_motor_record = str(settings.get("motor", "") or "")
        self._last_auto_reference_pv = f"{self._committed_motor_record}-PosAct" if self._committed_motor_record else ""
        reference_pvs = _settings_reference_pvs(settings)
        self._refresh_reference_pv_presets()
        for edit in self.reference_pv_edits:
            edit.blockSignals(True)
        for idx, edit in enumerate(self.reference_pv_edits):
            self._set_reference_pv_text(edit, str(reference_pvs[idx]))
        for edit in self.reference_pv_edits:
            edit.blockSignals(False)
        self._committed_reference_pvs = list(reference_pvs)
        self._refresh_reference_selector()
        reference_slot = settings.get("reference_slot")
        try:
            reference_slot = None if reference_slot is None else int(reference_slot)
        except Exception:
            reference_slot = None
        if reference_slot is None or not (0 <= reference_slot < len(reference_pvs)) or not reference_pvs[reference_slot]:
            reference_slot = self._selected_reference_slot(reference_pvs)
        combo_index = self.report_reference_combo.findData(reference_slot, role=QtCore.Qt.UserRole)
        if combo_index >= 0:
            self.report_reference_combo.setCurrentIndex(combo_index)
        self.range_min_edit.setText(_fmt(settings.get("range_min")))
        self.range_max_edit.setText(_fmt(settings.get("range_max")))
        self._last_auto_range_min = _fmt(settings.get("range_min"))
        self._last_auto_range_max = _fmt(settings.get("range_max"))
        target_count = int(settings.get("target_count") or 0)
        self.target_count_spin.setValue(target_count if target_count <= self.target_count_spin.maximum() else self.target_count_spin.maximum())
        self.reversal_margin_edit.setText(_fmt(settings.get("reversal_margin")))
        try:
            self._last_auto_reversal_margin = _fmt(abs(float(settings.get("range_max")) - float(settings.get("range_min"))) * 0.05)
        except Exception:
            self._last_auto_reversal_margin = ""
        self.cycles_spin.setValue(int(settings.get("cycles") or 1))
        self.settle_spin.setValue(float(settings.get("settle_s") or 0.0))
        self.samples_spin.setValue(int(settings.get("samples_per_point") or 1))
        self.motion_velo_edit.setText(_fmt(settings.get("velo")))
        self.motion_acc_edit.setText(_fmt(settings.get("accl")))
        self.motion_vmax_edit.setText(_fmt(settings.get("vmax")))
        self.motion_accs_edit.setText(_fmt(settings.get("accs")))

        normalized_settings = dict(settings)
        normalized_settings["reference_pvs"] = reference_pvs
        normalized_settings["reference_slot"] = reference_slot
        normalized_settings["reference_pv"] = self._selected_reference_pv(reference_pvs)
        self._test_settings_cache = normalized_settings
        self._measurements = []
        for source_row in rows:
            row = dict(source_row)
            self._project_row_reference(row, self._test_settings_cache, reference_slot)
            self._measurements.append(row)
        self._latest_metrics = self._compute_metrics(self._measurements)
        self._latest_metrics["state"] = state
        self._latest_report_markdown = self._build_report_markdown()
        self._reload_results_table()
        self._populate_summary_table(self._latest_metrics.get("per_target", []))
        self._update_summary_labels(self._latest_metrics)
        self._update_live_graph()
        self._update_duration_estimate()
        if not self._test_active:
            self._test_plan = self._build_test_plan(self._test_settings_cache)
            self._test_plan_index = len(self._test_plan) if str(state).lower() == "demo" else 0
            self._current_phase = "done" if str(state).lower() == "demo" else "idle"
        self._update_progress_display()
        self.preview_targets()
        self.refresh_status()

    def load_demo_data(self):
        if self._test_active:
            self.abort_test()
        settings = _demo_settings()
        rows = _build_demo_measurements(settings)
        self._apply_report_dataset(settings, rows, state="Demo")
        self.step_label.setText("Demo data loaded")
        self._log(f"Loaded synthetic ISO 230 demo data with {len(rows)} measured points")

    def preview_report(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("ISO 230 Report Preview")
        dlg.resize(_scaled_px(1220), _scaled_px(860))
        layout = QtWidgets.QVBoxLayout(dlg)
        tabs = QtWidgets.QTabWidget(dlg)
        tabs.addTab(self._build_preview_summary_tab(), "Summary")
        tabs.addTab(self._build_preview_graph_tab(), "Graph")
        tabs.addTab(self._build_preview_per_target_tab(), "Per-Target")
        tabs.addTab(self._build_preview_raw_tab(), "Raw Data")
        layout.addWidget(tabs, 1)
        btn_row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Save Markdown")
        close_btn = QtWidgets.QPushButton("Close")
        for btn in (save_btn, close_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
        save_btn.clicked.connect(self.export_report)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addStretch(1)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec_()

    def _show_calculation_help(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("ISO 230 Calculation Help")
        dlg.resize(_scaled_px(980), _scaled_px(760))
        layout = QtWidgets.QVBoxLayout(dlg)

        intro = QtWidgets.QLabel(
            "This dialog summarizes the formulas currently used by the ISO 230 app for the reported metrics."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #516079;")
        layout.addWidget(intro)

        browser = QtWidgets.QTextBrowser(dlg)
        browser.setStyleSheet("background: white; border: 1px solid #d7e0eb; border-radius: 8px;")
        browser.setHtml(
            """
            <html><body style="font-family: Helvetica, Arial, sans-serif; color: #1f2937; margin: 14px;">
            <h2 style="margin-top: 0;">Implemented calculation summary</h2>
            <p>
            The app groups measured reference errors by target position and by approach direction.
            Forward means positive-direction approach. Reverse means negative-direction approach.
            The ISO metrics are based on the selected reference PV, not the motor RBV field.
            </p>
            <h3>Per-sample quantity</h3>
            <pre style="background:#f8fafc;border:1px solid #d7e0eb;padding:10px;border-radius:8px;">x_ij = P_ij - P_i</pre>
            <p>In the app this is the selected reference reading minus the commanded target.</p>
            <h3>Per-target means and spread</h3>
            <pre style="background:#f8fafc;border:1px solid #d7e0eb;padding:10px;border-radius:8px;">
xbar_i^+ = mean(forward errors at target i)
xbar_i^- = mean(reverse errors at target i)
xbar_i   = 0.5 * (xbar_i^+ + xbar_i^-)
B_i      = xbar_i^+ - xbar_i^-
s_i^+    = sample stddev(forward errors at target i)
s_i^-    = sample stddev(reverse errors at target i)
R_i^+    = 4 * s_i^+
R_i^-    = 4 * s_i^-
            </pre>
            <h3>Per-target bidirectional repeatability</h3>
            <pre style="background:#f8fafc;border:1px solid #d7e0eb;padding:10px;border-radius:8px;">
R_i = max(
    sqrt(2*s_i^+*s_i^+ + 2*s_i^-*s_i^- + B_i*B_i),
    R_i^+,
    R_i^-
)
            </pre>
            <p>
            The graph draws directional repeatability intervals as
            <code>xbar_i^+ +/- 2*s_i^+</code> and <code>xbar_i^- +/- 2*s_i^-</code>.
            </p>
            <h3>Axis-level metrics</h3>
            <pre style="background:#f8fafc;border:1px solid #d7e0eb;padding:10px;border-radius:8px;">
R^+ = max_i(R_i^+)
R^- = max_i(R_i^-)
R   = max_i(R_i)

M = max_i(xbar_i) - min_i(xbar_i)

E = max_i(xbar_i^+, xbar_i^-) - min_i(xbar_i^+, xbar_i^-)

B_mean = mean_i(B_i)
B_max  = max_i(abs(B_i))

A = max_i(xbar_i^+ + 2*s_i^+, xbar_i^- + 2*s_i^-)
  - min_i(xbar_i^+ - 2*s_i^+, xbar_i^- - 2*s_i^-)
            </pre>
            <h3>How the UI labels map</h3>
            <ul>
            <li><b>BiDir Accuracy</b> = A</li>
            <li><b>BiDir Systematic</b> = E</li>
            <li><b>BiDir Repeatability</b> = R</li>
            <li><b>Uni Repeat</b> = max(R^+, R^-)</li>
            <li><b>Mean Reversal</b> = B_mean</li>
            <li><b>Maximum Reversal</b> in the report = B_max</li>
            <li><b>Range of mean bidirectional positional deviation</b> in the report = M</li>
            </ul>
            <h3>Notes</h3>
            <ul>
            <li>The linear-fit values in the report are convenience diagnostics, not ISO 230 core metrics.</li>
            <li>This popup documents the current app implementation and is not a substitute for the standard itself.</li>
            </ul>
            </body></html>
            """
        )
        layout.addWidget(browser, 1)

        btn_row = QtWidgets.QHBoxLayout()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec_()

    def export_report(self):
        if not self._latest_report_markdown:
            self._latest_report_markdown = self._build_report_markdown()
        default_name = f"iso230_report_axis_{self._axis_id_text() or 'unknown'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path, _flt = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save ISO 230 Report",
            str(Path.home() / default_name),
            "Markdown Files (*.md);;All Files (*)",
        )
        if not path:
            return
        try:
            Path(path).write_text(self._latest_report_markdown, encoding="utf-8")
            self._log(f"Saved report: {path}")
        except Exception as ex:
            self._log(f"Failed to save report: {ex}")

    def export_csv(self):
        if not self._measurements:
            self._log("No measured data to export")
            return
        default_name = f"iso230_raw_axis_{self._axis_id_text() or 'unknown'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _flt = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save ISO 230 Raw Data CSV",
            str(Path.home() / default_name),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                headers = [
                    "cycle",
                    "direction",
                    "target",
                    "selected_reference_slot",
                    "selected_reference_pv",
                    "reference_mean",
                    "reference_std",
                    "rbv_mean",
                    "rbv_std",
                    "command_mean",
                    "ref_error",
                    "rbv_error",
                ]
                for idx in range(_MAX_REFERENCE_PVS):
                    prefix = f"ref{idx + 1}"
                    headers.extend([f"{prefix}_pv", f"{prefix}_mean", f"{prefix}_std", f"{prefix}_error"])
                headers.append("timestamp")
                writer.writerow(headers)
                for row in self._measurements:
                    record = [
                        row["cycle"],
                        row["direction"],
                        row["target"],
                        row.get("reference_slot"),
                        row.get("reference_pv", ""),
                        row["reference_mean"],
                        row["reference_std"],
                        row["rbv_mean"],
                        row["rbv_std"],
                        row["command_mean"],
                        row["ref_error"],
                        row["rbv_error"],
                    ]
                    stats = dict(row.get("reference_stats") or {})
                    for idx in range(_MAX_REFERENCE_PVS):
                        stat = stats.get(idx, {})
                        record.extend([stat.get("pv", ""), stat.get("mean"), stat.get("std"), stat.get("error")])
                    record.append(row["timestamp"].isoformat())
                    writer.writerow(record)
            self._log(f"Saved CSV: {path}")
        except Exception as ex:
            self._log(f"Failed to save CSV: {ex}")

    def closeEvent(self, event):
        try:
            if self._test_active:
                self.abort_test()
        finally:
            super().closeEvent(event)


def build_demo_report_files(report_path, csv_path=None, seed=2302):
    settings = _demo_settings()
    rows = _build_demo_measurements(settings, seed=seed)
    dummy = Iso230Window.__new__(Iso230Window)
    dummy._test_active = False
    dummy._test_settings_cache = settings
    dummy._measurements = rows
    dummy._latest_metrics = dummy._compute_metrics(rows)
    dummy._latest_metrics["state"] = "Demo"
    report_md = dummy._build_report_markdown()

    report_target = Path(report_path)
    report_target.write_text(report_md, encoding="utf-8")

    if csv_path:
        _write_demo_csv(csv_path, rows)

    return report_target, (Path(csv_path) if csv_path else None)


def main():
    ap = argparse.ArgumentParser(description="Qt app for ISO 230-style bidirectional axis tests")
    ap.add_argument("--prefix", default="", help="IOC prefix (e.g. IOC:ECMC)")
    ap.add_argument("--axis-id", default="", help="Axis ID")
    ap.add_argument("--timeout", type=float, default=2.0, help="EPICS timeout [s]")
    ap.add_argument("--demo-report-out", default="", help="Write a synthetic Markdown report without connecting to EPICS")
    ap.add_argument("--demo-csv-out", default="", help="Optional CSV path for synthetic raw data")
    ap.add_argument("--demo-seed", type=int, default=2302, help="Random seed for synthetic report generation")
    args = ap.parse_args()

    if str(args.demo_report_out or "").strip():
        report_path, csv_path = build_demo_report_files(
            report_path=args.demo_report_out,
            csv_path=(args.demo_csv_out or "").strip() or None,
            seed=args.demo_seed,
        )
        print(f"Wrote demo report: {report_path}")
        if csv_path is not None:
            print(f"Wrote demo CSV: {csv_path}")
        return

    app = QtWidgets.QApplication(sys.argv)
    w = Iso230Window(
        prefix=args.prefix,
        axis_id=args.axis_id,
        timeout=args.timeout,
        axis_id_was_provided=bool((args.axis_id or "").strip()),
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
