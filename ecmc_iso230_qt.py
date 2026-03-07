#!/usr/bin/env python3
import argparse
import csv
import html
import math
import random
import tempfile
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore

from ecmc_mtn_qt import (
    _MotionPvMixin,
    _normalize_axis_object_id,
    _normalize_axis_type_text,
    _to_float,
    _truthy_pv,
)
from ecmc_stream_qt import EpicsClient, _join_prefix_pv, compact_float_text


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


def _fmt(value, sig_digits=10):
    if value is None:
        return ""
    return compact_float_text(value, sig_digits=sig_digits)


def _fmt_preview(value):
    return _fmt(value, sig_digits=7)


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
    return {
        "prefix": "DEMO:ECMC",
        "axis_id": "7",
        "motor": "DEMO:AXIS7",
        "reference_pv": "SIM:LASER:MEAS",
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
        "settle_s": 1.0,
        "samples_per_point": 5,
        "sample_interval_ms": 150,
        "velo": 25.0,
        "accl": 80.0,
        "accs": 120.0,
        "vmax": 40.0,
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
                rows.append(
                    {
                        "cycle": cycle,
                        "direction": direction,
                        "target": float(target),
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
        writer.writerow(
            [
                "cycle",
                "direction",
                "target",
                "reference_mean",
                "reference_std",
                "rbv_mean",
                "rbv_std",
                "command_mean",
                "ref_error",
                "rbv_error",
                "timestamp",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row["cycle"],
                    row["direction"],
                    row["target"],
                    row["reference_mean"],
                    row["reference_std"],
                    row["rbv_mean"],
                    row["rbv_std"],
                    row["command_mean"],
                    row["ref_error"],
                    row["rbv_error"],
                    row["timestamp"].isoformat(),
                ]
            )


class _TargetSweepSchematic(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = None
        self._message = "Enter range and approach margin to visualize the sweep."
        self.setMinimumHeight(220)

    def sizeHint(self):
        return QtCore.QSize(640, 220)

    def set_preview(self, settings=None, message=""):
        self._settings = dict(settings or {}) if settings else None
        self._message = str(message or "")
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
        painter.drawText(QtCore.QRectF(left, top + 14, right - left, 16), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "blue=tested span, red=approach margin")

        target_label_font = painter.font()
        target_label_font.setPointSize(max(7, target_label_font.pointSize() - 1))
        painter.setFont(target_label_font)
        painter.setPen(QtGui.QColor("#1d4ed8"))
        for x, target, idx in label_positions:
            label_y = top + 34 + ((idx % 2) * 14)
            painter.drawText(
                QtCore.QRectF(x - 32, label_y, 64, 12),
                QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                _fmt(target, 5),
            )

        range_label_y = rect.bottom() - 48
        margin_label_y = rect.bottom() - 30
        painter.setPen(QtGui.QColor("#0f172a"))
        painter.drawText(QtCore.QRectF(x_lo - 32, range_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(lo, 5)}")
        painter.drawText(QtCore.QRectF(x_hi - 32, range_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(hi, 5)}")
        painter.setPen(QtGui.QColor("#7c2d12"))
        painter.drawText(QtCore.QRectF(x_full_lo - 32, margin_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(full_lo, 5)}")
        painter.drawText(QtCore.QRectF(x_full_hi - 32, margin_label_y, 64, 14), QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, f"{_fmt(full_hi, 5)}")



class Iso230Window(_MotionPvMixin, QtWidgets.QMainWindow):
    SAMPLE_INTERVAL_MS = 150

    def __init__(self, prefix, axis_id, timeout, axis_id_was_provided=True):
        super().__init__()
        self._base_title = "ecmc ISO 230 Bidirectional Test"
        self.setWindowTitle(self._base_title)
        self.resize(1120, 760)

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
        self._last_status = {}
        self._measurements = []
        self._test_settings_cache = {}
        self._move_issued_at = 0.0
        self._demo_mode = False
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
        self.preview_report_btn = QtWidgets.QPushButton("Preview Report")
        self.export_report_btn = QtWidgets.QPushButton("Save Report (.md)")
        self.export_csv_btn = QtWidgets.QPushButton("Save CSV")
        self.axis_pick_combo = QtWidgets.QComboBox()
        self.axis_pick_combo.setMinimumWidth(180)
        self.axis_pick_combo.setMaximumWidth(260)
        for btn in (
            self.cfg_toggle_btn,
            self.log_toggle_btn,
            self.start_btn,
            self.abort_btn,
            self.load_demo_btn,
            self.preview_report_btn,
            self.export_report_btn,
            self.export_csv_btn,
        ):
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setMaximumHeight(24)
        self.abort_btn.setEnabled(False)
        self.axis_pick_combo.activated.connect(self._on_axis_combo_activated)
        self.cfg_toggle_btn.clicked.connect(self._toggle_config_panel)
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)
        self.start_btn.clicked.connect(self.start_test)
        self.abort_btn.clicked.connect(self.abort_test)
        self.load_demo_btn.clicked.connect(self.load_demo_data)
        self.preview_report_btn.clicked.connect(self.preview_report)
        self.export_report_btn.clicked.connect(self.export_report)
        self.export_csv_btn.clicked.connect(self.export_csv)
        top_row.addWidget(self.cfg_toggle_btn)
        top_row.addWidget(self.log_toggle_btn)
        top_row.addWidget(self.start_btn)
        top_row.addWidget(self.abort_btn)
        top_row.addWidget(self.load_demo_btn)
        top_row.addWidget(self.preview_report_btn)
        top_row.addWidget(self.export_report_btn)
        top_row.addWidget(self.export_csv_btn)
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
        cfg.setContentsMargins(6, 6, 6, 6)
        cfg.setSpacing(8)

        self.prefix_edit = QtWidgets.QLineEdit(self.default_prefix)
        self.axis_edit = QtWidgets.QLineEdit(self.default_axis_id)
        self.axis_edit.setMaximumWidth(70)
        self.timeout_edit = QtWidgets.QDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(float(timeout))
        self.timeout_edit.setMaximumWidth(90)
        self.timeout_edit.valueChanged.connect(self._set_timeout)

        self.axis_pfx_cfg_pv_edit = QtWidgets.QLineEdit()
        self.motor_name_cfg_pv_edit = QtWidgets.QLineEdit()
        self.motor_record_edit = QtWidgets.QLineEdit("")
        self.motor_record_edit.setPlaceholderText("Resolved motor record base PV")
        self.reference_pv_edit = QtWidgets.QLineEdit("")
        self.reference_pv_edit.setPlaceholderText("Defaults to <motor>-PosAct")

        self.range_min_edit = QtWidgets.QLineEdit("0")
        self.range_max_edit = QtWidgets.QLineEdit("10")
        self.range_min_edit.setMaximumWidth(110)
        self.range_max_edit.setMaximumWidth(110)
        self.target_count_spin = QtWidgets.QSpinBox()
        self.target_count_spin.setRange(0, 41)
        self.target_count_spin.setValue(0)
        self.target_count_spin.setSpecialValueText("Auto (ISO minimum)")
        self.target_count_spin.setMinimumWidth(170)
        self.reversal_margin_edit = QtWidgets.QLineEdit("")
        self.reversal_margin_edit.setPlaceholderText("Auto (5% of range)")
        self.reversal_margin_edit.setMaximumWidth(110)
        self.target_schematic = _TargetSweepSchematic()
        self.cycles_spin = QtWidgets.QSpinBox()
        self.cycles_spin.setRange(1, 20)
        self.cycles_spin.setValue(5)
        self.cycles_spin.setMaximumWidth(80)
        self.settle_spin = QtWidgets.QDoubleSpinBox()
        self.settle_spin.setRange(0.0, 120.0)
        self.settle_spin.setDecimals(2)
        self.settle_spin.setValue(1.0)
        self.settle_spin.setMaximumWidth(100)
        self.samples_spin = QtWidgets.QSpinBox()
        self.samples_spin.setRange(1, 50)
        self.samples_spin.setValue(5)
        self.samples_spin.setMaximumWidth(80)
        self.estimated_duration_value = QtWidgets.QLabel("-")
        self.estimated_duration_value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)

        self.motion_velo_edit = QtWidgets.QLineEdit("1")
        self.motion_acc_edit = QtWidgets.QLineEdit("1")
        self.motion_vmax_edit = QtWidgets.QLineEdit("")
        self.motion_accs_edit = QtWidgets.QLineEdit("")
        self.motion_velo_edit.setMaximumWidth(90)
        self.motion_acc_edit.setMaximumWidth(90)
        self.motion_vmax_edit.setMaximumWidth(110)
        self.motion_accs_edit.setMaximumWidth(110)
        self.motion_vmax_edit.setPlaceholderText("optional")
        self.motion_accs_edit.setPlaceholderText("optional")

        axis_apply_btn = QtWidgets.QPushButton("Apply Axis")
        resolve_btn = QtWidgets.QPushButton("Resolve Motor")
        read_status_btn = QtWidgets.QPushButton("Read Status")
        for btn in (axis_apply_btn, resolve_btn, read_status_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
            btn.setMaximumWidth(140)
        axis_apply_btn.clicked.connect(self._apply_axis_top)
        resolve_btn.clicked.connect(self.resolve_motor_record_name)
        read_status_btn.clicked.connect(self.refresh_status)

        self._update_cfg_pv_edits()
        self.prefix_edit.editingFinished.connect(self._update_cfg_pv_edits)
        self.axis_edit.editingFinished.connect(self._update_cfg_pv_edits)
        self.motor_record_edit.textChanged.connect(self._sync_reference_pv_default)
        self.range_min_edit.editingFinished.connect(self._on_range_inputs_changed)
        self.range_max_edit.editingFinished.connect(self._on_range_inputs_changed)
        self.target_count_spin.valueChanged.connect(self._update_duration_estimate)
        self.reversal_margin_edit.editingFinished.connect(self._update_duration_estimate)
        self.cycles_spin.valueChanged.connect(self._update_duration_estimate)
        self.settle_spin.valueChanged.connect(self._update_duration_estimate)
        self.samples_spin.valueChanged.connect(self._update_duration_estimate)
        self.motion_velo_edit.editingFinished.connect(self._update_duration_estimate)

        axis_box = QtWidgets.QGroupBox("1. Axis / PV Binding")
        axis_grid = QtWidgets.QGridLayout(axis_box)
        axis_grid.setContentsMargins(6, 6, 6, 6)
        axis_grid.setHorizontalSpacing(8)
        axis_grid.setVerticalSpacing(4)
        axis_grid.addWidget(QtWidgets.QLabel("IOC Prefix"), 0, 0)
        axis_grid.addWidget(self.prefix_edit, 0, 1)
        axis_grid.addWidget(QtWidgets.QLabel("Axis ID"), 0, 2)
        axis_grid.addWidget(self.axis_edit, 0, 3)
        axis_grid.addWidget(axis_apply_btn, 0, 4)
        axis_grid.addWidget(QtWidgets.QLabel("Timeout [s]"), 0, 5)
        axis_grid.addWidget(self.timeout_edit, 0, 6)
        axis_grid.addWidget(QtWidgets.QLabel("Axis Prefix PV"), 1, 0)
        axis_grid.addWidget(self.axis_pfx_cfg_pv_edit, 1, 1, 1, 3)
        axis_grid.addWidget(QtWidgets.QLabel("Motor Name PV"), 1, 4)
        axis_grid.addWidget(self.motor_name_cfg_pv_edit, 1, 5, 1, 2)
        axis_grid.addWidget(QtWidgets.QLabel("Motor Record"), 2, 0)
        axis_grid.addWidget(self.motor_record_edit, 2, 1, 1, 3)
        axis_grid.addWidget(resolve_btn, 2, 4)
        axis_grid.addWidget(QtWidgets.QLabel("Reference PV"), 2, 5)
        axis_grid.addWidget(self.reference_pv_edit, 2, 6)
        axis_grid.addWidget(read_status_btn, 2, 7)
        cfg.addWidget(axis_box)

        plan_box = QtWidgets.QGroupBox("2. Test Range / Target Generation")
        plan_box_layout = QtWidgets.QVBoxLayout(plan_box)
        plan_box_layout.setContentsMargins(8, 8, 8, 8)
        plan_box_layout.setSpacing(8)

        plan_form = QtWidgets.QFormLayout()
        plan_form.setHorizontalSpacing(12)
        plan_form.setVerticalSpacing(6)
        plan_form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)

        range_row = QtWidgets.QHBoxLayout()
        range_row.setContentsMargins(0, 0, 0, 0)
        range_row.setSpacing(6)
        range_row.addWidget(QtWidgets.QLabel("Min"))
        range_row.addWidget(self.range_min_edit)
        range_row.addSpacing(12)
        range_row.addWidget(QtWidgets.QLabel("Max"))
        range_row.addWidget(self.range_max_edit)
        range_row.addStretch(1)
        plan_form.addRow("Measured range", range_row)

        target_row = QtWidgets.QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(6)
        target_row.addWidget(QtWidgets.QLabel("Count override"))
        target_row.addWidget(self.target_count_spin)
        target_row.addSpacing(12)
        target_row.addWidget(QtWidgets.QLabel("Approach margin"))
        target_row.addWidget(self.reversal_margin_edit)
        target_row.addStretch(1)
        plan_form.addRow("Targets", target_row)
        plan_box_layout.addLayout(plan_form)
        self.target_schematic.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.target_schematic.setFixedHeight(220)
        plan_box_layout.addWidget(self.target_schematic)

        run_box = QtWidgets.QGroupBox("3. Sequence / Motion")
        run_form = QtWidgets.QFormLayout(run_box)
        run_form.setContentsMargins(8, 8, 8, 8)
        run_form.setHorizontalSpacing(12)
        run_form.setVerticalSpacing(6)
        run_form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignTop)

        seq_row = QtWidgets.QHBoxLayout()
        seq_row.setContentsMargins(0, 0, 0, 0)
        seq_row.setSpacing(6)
        seq_row.addWidget(QtWidgets.QLabel("Cycles"))
        seq_row.addWidget(self.cycles_spin)
        seq_row.addSpacing(10)
        seq_row.addWidget(QtWidgets.QLabel("Settle [s]"))
        seq_row.addWidget(self.settle_spin)
        seq_row.addSpacing(10)
        seq_row.addWidget(QtWidgets.QLabel("Samples / point"))
        seq_row.addWidget(self.samples_spin)
        seq_row.addStretch(1)
        run_form.addRow("Sequence", seq_row)

        estimate_row = QtWidgets.QHBoxLayout()
        estimate_row.setContentsMargins(0, 0, 0, 0)
        estimate_row.setSpacing(6)
        estimate_row.addWidget(self.estimated_duration_value)
        estimate_row.addStretch(1)
        run_form.addRow("Estimated duration", estimate_row)

        motion_row = QtWidgets.QHBoxLayout()
        motion_row.setContentsMargins(0, 0, 0, 0)
        motion_row.setSpacing(6)
        motion_row.addWidget(QtWidgets.QLabel("VELO"))
        motion_row.addWidget(self.motion_velo_edit)
        motion_row.addSpacing(8)
        motion_row.addWidget(QtWidgets.QLabel("ACCL"))
        motion_row.addWidget(self.motion_acc_edit)
        motion_row.addSpacing(8)
        motion_row.addWidget(QtWidgets.QLabel("VMAX"))
        motion_row.addWidget(self.motion_vmax_edit)
        motion_row.addSpacing(8)
        motion_row.addWidget(QtWidgets.QLabel("ACCS"))
        motion_row.addWidget(self.motion_accs_edit)
        motion_row.addStretch(1)
        run_form.addRow("Motor fields", motion_row)

        run_note = QtWidgets.QLabel(
            "Cycles, settle time and sampling define the ISO 230 sequence. Leave optional VMAX/ACCS blank to keep the current motor settings."
        )
        run_note.setWordWrap(True)
        run_note.setStyleSheet("color: #516079;")
        run_form.addRow("", run_note)

        lower_setup_row = QtWidgets.QHBoxLayout()
        lower_setup_row.setContentsMargins(0, 0, 0, 0)
        lower_setup_row.setSpacing(8)
        lower_setup_row.addWidget(plan_box, 7)
        lower_setup_row.addWidget(run_box, 1)
        cfg.addLayout(lower_setup_row)

        layout.addWidget(self.cfg_group)
        self.cfg_group.setVisible(True)

        mid_row = QtWidgets.QHBoxLayout()
        mid_row.setContentsMargins(0, 0, 0, 0)
        mid_row.setSpacing(8)

        left_col = QtWidgets.QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        self.summary_group = QtWidgets.QGroupBox("ISO 230 Summary")
        self.summary_group.setMinimumHeight(162)
        self.summary_group.setMaximumHeight(162)
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
        self.summary_table.horizontalHeader().setMinimumSectionSize(72)
        left_col.addWidget(self.summary_table, 1)
        mid_row.addLayout(left_col, 1)

        right_col = QtWidgets.QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(8)
        self.status_group = QtWidgets.QGroupBox("Live Status")
        self.status_group.setMinimumHeight(162)
        self.status_group.setMaximumHeight(162)
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
        self.results_table.horizontalHeader().setMinimumSectionSize(72)
        right_col.addWidget(self.results_table, 1)
        mid_row.addLayout(right_col, 2)
        layout.addLayout(mid_row, 1)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
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
        self.progress_label.setMinimumWidth(110)
        self.progress_label.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self._refresh_axis_pick_combo()
        self._sync_reversal_margin_default()
        self.preview_targets()
        self._update_duration_estimate()
        self._update_summary_labels({})
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

    def _axis_id_text(self):
        return self.axis_edit.text().strip() or self.default_axis_id

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
            script = QtCore.QFileInfo(__file__).dir().filePath("start_iso230.sh")
            prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
            try:
                subprocess.Popen(
                    ["bash", str(script), str(prefix), str(axis_id)],
                    cwd=str(QtCore.QFileInfo(script).absolutePath()),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._log(f"Started new ISO 230 window for axis {axis_id} (prefix {prefix})")
            except Exception as ex:
                self._log(f"Failed to start new ISO 230 window: {ex}")
            self._sync_axis_combo_to_axis_id(self._axis_id_text())
            return
        self.axis_edit.setText(axis_id)
        self._apply_axis_top()

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

    def _sync_reference_pv_default(self, _text=None):
        motor = self.motor_record_edit.text().strip()
        default_ref = f"{motor}-PosAct" if motor else ""
        current_ref = self.reference_pv_edit.text().strip()
        if not current_ref or current_ref == self._last_auto_reference_pv:
            self.reference_pv_edit.setText(default_ref)
        self._last_auto_reference_pv = default_ref

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

    def _read_motor_soft_limits(self):
        if not self.motor_record_edit.text().strip():
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

    def _update_progress_display(self):
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
            axis_pfx_pv = self.axis_pfx_cfg_pv_edit.text().strip()
            axis_pfx = self._read_cfg_pv(axis_pfx_pv) if axis_pfx_pv else ""
            motor_cfg_pv = self.motor_name_cfg_pv_edit.text().strip()
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
            self._update_window_title()
            self._log(f"Resolved motor record: {resolved} (axis_pfx='{axis_pfx}', motor='{motor_name}')")
            self._init_motion_settings_from_pv()
            self.refresh_status()
        except Exception as ex:
            self._update_window_title()
            self._log(f"Resolve failed: {ex}")

    def _update_window_title(self):
        motor = self.motor_record_edit.text().strip()
        if motor:
            self.setWindowTitle(f"{self._base_title} [{motor}]")
        else:
            self.setWindowTitle(self._base_title)

    def _pv(self, field):
        base = self.motor_record_edit.text().strip()
        if not base:
            raise RuntimeError("Motor record is not resolved")
        return f"{base}.{field}"

    def _motor_base(self):
        base = self.motor_record_edit.text().strip()
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
        if not self.motor_record_edit.text().strip():
            return
        try:
            self.motion_velo_edit.setText(compact_float_text(self.client.get(self._pv("VELO"), as_string=True)))
        except Exception:
            pass
        try:
            self.motion_acc_edit.setText(compact_float_text(self.client.get(self._pv("ACCL"), as_string=True)))
        except Exception:
            pass
        try:
            self.motion_vmax_edit.setText(compact_float_text(self.client.get(self._pv("VMAX"), as_string=True)))
        except Exception:
            self.motion_vmax_edit.setText("")
        try:
            self.motion_accs_edit.setText(compact_float_text(self.client.get(self._pv("ACCS"), as_string=True)))
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
            if demo_row is not None:
                vals = {
                    "VAL": compact_float_text(demo_row.get("command_mean")),
                    "RBV": compact_float_text(demo_row.get("rbv_mean")),
                    "DMOV": "1",
                    "CNEN": "1",
                    "REF": compact_float_text(demo_row.get("reference_mean")),
                }
            else:
                vals = {"VAL": "", "RBV": "", "DMOV": "1", "CNEN": "1", "REF": ""}
            for field in ("VAL", "RBV", "DMOV", "CNEN", "REF"):
                self.status_fields[field].setText(str(vals.get(field, "")))
            self._last_status = dict(vals)
            return vals
        if not self.motor_record_edit.text().strip():
            return vals
        for field in ("VAL", "RBV", "DMOV", "CNEN"):
            try:
                vals[field] = str(self.client.get(self._pv(field), as_string=True)).strip()
            except Exception as ex:
                vals[field] = f"ERR: {ex}"
            self.status_fields[field].setText(compact_float_text(vals[field]))
        ref_pv = self.reference_pv_edit.text().strip()
        if ref_pv:
            try:
                vals["REF"] = str(self.client.get(ref_pv, as_string=True)).strip()
            except Exception as ex:
                vals["REF"] = f"ERR: {ex}"
            self.status_fields["REF"].setText(compact_float_text(vals["REF"]))
        self._last_status = dict(vals)
        return vals

    def _periodic_status_tick(self):
        if self._demo_mode:
            return
        try:
            self.refresh_status()
        except Exception:
            pass

    def _target_preview_settings(self):
        motor = self.motor_record_edit.text().strip()
        ref_pv = self.reference_pv_edit.text().strip()
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
        if not settings["reference_pv"]:
            raise RuntimeError("Reference measurement PV is required")
        return settings

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
            f"Targets ({len(targets)}): {', '.join(_fmt(v, sig_digits=8) for v in targets)}",
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
            tooltip_lines.append("Run note: enter the reference PV before starting the sequence.")
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
            self._test_plan = self._build_test_plan(settings)
            self._test_plan_index = -1
            self._current_step = None
            self._current_phase = "idle"
            self._test_settings_cache = settings
            self._set_test_running_state(True)
            self._update_progress_display()
            self._log(
                "Starting ISO 230-style bidirectional positioning test: "
                f"{len(settings['targets'])} targets, {settings['cycles']} cycle(s), reference PV={settings['reference_pv']}"
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
        ref_pv = self._test_settings_cache["reference_pv"]
        ref_raw = self.client.get(ref_pv, as_string=True)
        rbv_raw = self.client.get(self._pv("RBV"), as_string=True)
        val_raw = self.client.get(self._pv("VAL"), as_string=True)
        return {
            "cycle": step["cycle"],
            "direction": step["direction"],
            "target": float(step["target"]),
            "reference": _to_float(ref_raw, "Reference PV"),
            "rbv": _to_float(rbv_raw, "RBV"),
            "command": _to_float(val_raw, "VAL"),
            "timestamp": datetime.now(),
        }

    def _finalize_measurement(self, step, samples):
        ref_vals = [s["reference"] for s in samples]
        rbv_vals = [s["rbv"] for s in samples]
        cmd_vals = [s["command"] for s in samples]
        target = float(step["target"])
        row = {
            "cycle": int(step["cycle"]),
            "direction": step["direction"],
            "target": target,
            "reference_mean": _mean(ref_vals),
            "reference_std": _stddev(ref_vals),
            "rbv_mean": _mean(rbv_vals),
            "rbv_std": _stddev(rbv_vals),
            "command_mean": _mean(cmd_vals),
            "ref_error": (_mean(ref_vals) - target) if _mean(ref_vals) is not None else None,
            "rbv_error": (_mean(rbv_vals) - target) if _mean(rbv_vals) is not None else None,
            "timestamp": samples[-1]["timestamp"],
        }
        self._measurements.append(row)
        self._append_results_row(row)
        self._latest_metrics = self._compute_metrics(self._measurements)
        self._update_summary_labels(self._latest_metrics)
        self._populate_summary_table(self._latest_metrics.get("per_target", []))
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
        bidirectional_upper = []
        bidirectional_lower = []
        overall_unidirectional_repeatability = None
        overall_bidirectional_repeatability = None
        for key in sorted(grouped.keys(), key=lambda k: grouped[k]["target"]):
            bucket = grouped[key]
            fwd = bucket["forward"]
            rev = bucket["reverse"]
            both = list(fwd) + list(rev)
            fwd_mean = _mean(fwd)
            rev_mean = _mean(rev)
            fwd_repeat = (max(fwd) - min(fwd)) if fwd else None
            rev_repeat = (max(rev) - min(rev)) if rev else None
            if fwd_mean is not None and rev_mean is not None:
                mean_bidir = 0.5 * (float(fwd_mean) + float(rev_mean))
                reversal = abs(float(fwd_mean) - float(rev_mean))
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
            bidir_repeat = (bidir_upper - bidir_lower) if (bidir_upper is not None and bidir_lower is not None) else None
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
                    "forward_min": min(fwd) if fwd else None,
                    "forward_max": max(fwd) if fwd else None,
                    "reverse_min": min(rev) if rev else None,
                    "reverse_max": max(rev) if rev else None,
                    "forward_count": len(fwd),
                    "reverse_count": len(rev),
                    "reversal_value": reversal,
                    "forward_repeatability": fwd_repeat,
                    "reverse_repeatability": rev_repeat,
                    "unidirectional_repeatability": unidir_repeat,
                    "bidirectional_repeatability": bidir_repeat,
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
        maximum_reversal_value = max(reversal_values) if reversal_values else None
        bidirectional_systematic_deviation = None
        if range_mean_bidirectional is not None and mean_reversal_value is not None:
            bidirectional_systematic_deviation = range_mean_bidirectional + mean_reversal_value
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

    def _build_iso230_svg(self, settings, metrics):
        rows = list(metrics.get("per_target", []) or [])
        if len(rows) < 2:
            return "<p><em>Graph unavailable: at least two measured target positions are required.</em></p>"

        width = 980
        height = 520
        margin_l = 90
        margin_r = 26
        margin_t = 26
        margin_b = 70
        plot_w = width - margin_l - margin_r
        plot_h = height - margin_t - margin_b

        x_vals = [float(r["target"]) for r in rows]
        y_candidates = []
        for row in rows:
            for key in ("forward_min", "forward_max", "reverse_min", "reverse_max", "forward_mean", "reverse_mean"):
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
            grid.append(text(margin_l - 12, yy + 4, _fmt(yv, sig_digits=6), size=12, anchor="end", fill="#334155"))
        x_tick_step = max(1, int(round((len(rows) - 1) / 6.0)))
        for idx, row in enumerate(rows):
            if idx % x_tick_step != 0 and idx not in {0, len(rows) - 1}:
                continue
            xx = map_x(row["target"])
            grid.append(line(xx, margin_t, xx, margin_t + plot_h, grid_color, 1.0, dash="4 5", opacity=0.6))
            grid.append(text(xx, margin_t + plot_h + 24, _fmt(row["target"], sig_digits=6), size=12, anchor="middle", fill="#334155"))

        zero_y = map_y(0.0)
        grid.append(line(margin_l, zero_y, margin_l + plot_w, zero_y, axis_color, 1.7))
        grid.append(text(margin_l + plot_w - 4, zero_y - 8, "zero error", size=11, anchor="end", fill="#0f172a", weight="600"))

        forward_points = []
        reverse_points = []
        overlays = []
        for row in rows:
            x_base = map_x(row["target"])
            x_f = x_base - 8.0
            x_r = x_base + 8.0
            fmin = row.get("forward_min")
            fmax = row.get("forward_max")
            rmin = row.get("reverse_min")
            rmax = row.get("reverse_max")
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
            return f'<polyline fill="none" stroke="{stroke}" stroke-width="2.8" points="{pts}" />'

        series = []
        if len(forward_points) >= 2:
            series.append(polyline(forward_points, forward_color))
        if len(reverse_points) >= 2:
            series.append(polyline(reverse_points, reverse_color))
        for x, y in forward_points:
            series.append(circle(x, y, 4.8, forward_color))
        for x, y in reverse_points:
            series.append(square(x, y, 9.0, reverse_color))

        legend_x = margin_l + 10
        legend_y = margin_t + 14
        legend = [
            line(legend_x, legend_y, legend_x + 22, legend_y, forward_color, 3.0),
            circle(legend_x + 11, legend_y, 4.8, forward_color),
            text(legend_x + 30, legend_y + 4, "Forward mean error", size=12, anchor="start"),
            line(legend_x + 210, legend_y, legend_x + 232, legend_y, reverse_color, 3.0),
            square(legend_x + 221, legend_y, 9.0, reverse_color),
            text(legend_x + 240, legend_y + 4, "Reverse mean error", size=12, anchor="start"),
            line(legend_x + 460, legend_y - 6, legend_x + 460, legend_y + 12, backlash_color, 2.0, dash="5 4"),
            text(legend_x + 472, legend_y + 4, "Reversal gap", size=12, anchor="start"),
        ]

        annotations = []
        bidir_accuracy = metrics.get("bidirectional_accuracy")
        bidir_systematic = metrics.get("bidirectional_systematic_deviation")
        bidir_repeat = metrics.get("bidirectional_repeatability")
        stats_box_w = 244
        stats_box_h = 92
        stats_box_x = margin_l + plot_w - stats_box_w - 20
        stats_box_y = margin_t + 14
        annotations.append(
            f'<rect x="{stats_box_x}" y="{stats_box_y}" width="{stats_box_w}" height="{stats_box_h}" fill="#ffffff" stroke="#94a3b8" stroke-width="1.2" rx="8" ry="8" />'
        )
        annotations.append(text(stats_box_x + 14, stats_box_y + 24, "Summary metrics", size=12, anchor="start", weight="700"))
        annotations.append(text(stats_box_x + 14, stats_box_y + 48, f"BiDir accuracy: {_fmt(bidir_accuracy)}", size=12, anchor="start"))
        annotations.append(text(stats_box_x + 14, stats_box_y + 68, f"BiDir systematic: {_fmt(bidir_systematic)}", size=12, anchor="start"))
        annotations.append(text(stats_box_x + 14, stats_box_y + 88, f"BiDir repeatability: {_fmt(bidir_repeat)}", size=12, anchor="start"))

        title = [
            text(width / 2.0, 18, "Bidirectional Positioning Error Graph", size=16, anchor="middle", weight="700"),
            text(width / 2.0, height - 18, f"Target position on axis {settings.get('axis_id', '')}", size=13, anchor="middle", weight="600"),
            (
                f'<g transform="translate(24 {margin_t + (plot_h / 2.0):.2f}) rotate(-90)">'
                + text(0, 0, "Reference error relative to commanded target", size=13, anchor="middle", weight="600")
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

        graph_svg = self._build_iso230_svg(settings, metrics)
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
            f"- Reference PV: `{settings.get('reference_pv', '')}`",
            f"- Range: `{_fmt(settings.get('range_min'))} .. {_fmt(settings.get('range_max'))}`",
            f"- Target generation mode: `{settings.get('target_mode', '')}`",
            f"- Target generation rule: `{settings.get('target_rule_note', '')}`",
            f"- Base interval: `{_fmt(settings.get('base_interval'))}`",
            f"- Targets: `{', '.join(_fmt(v, sig_digits=8) for v in settings.get('targets', []))}`",
            f"- Cycles: `{settings.get('cycles', '')}`",
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
            "Bidirectional positioning error relative to commanded target. Forward mean error is shown in blue, reverse mean error in amber, repeatability ranges as vertical bars, and reversal value as the dashed violet segment at each target.",
            "",
            graph_svg,
            "",
            "## Notes",
            "",
            "- This workflow uses ISO 230-style bidirectional positioning terminology derived from the supplied reference document and is not presented as certified ISO 230-2 compliance evidence.",
            "- Mean bidirectional positional deviation is calculated as the average of the forward and reverse mean reference errors at each target.",
            "- Reversal value is calculated as the absolute difference between forward and reverse mean reference errors at each target.",
            "- Bidirectional repeatability is calculated from the combined forward and reverse error band at each target.",
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

        lines.extend(
            [
                "",
                "## Raw Measured Points",
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
            ("Reference PV", settings.get("reference_pv", "")),
            ("Range", f"{_fmt_preview(settings.get('range_min'))} .. {_fmt_preview(settings.get('range_max'))}"),
            ("Target generation mode", settings.get("target_mode", "")),
            ("Target generation rule", settings.get("target_rule_note", "")),
            ("Base interval", _fmt_preview(settings.get("base_interval"))),
            ("Targets", ", ".join(_fmt_preview(v) for v in settings.get("targets", []))),
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

        status_box = QtWidgets.QGroupBox("Status")
        status_form = QtWidgets.QFormLayout(status_box)
        status_form.setLabelAlignment(QtCore.Qt.AlignRight)
        status_rows = [
            ("State", metrics.get("state", "")),
            ("Bidirectional accuracy", _fmt_preview(metrics.get("bidirectional_accuracy"))),
            ("Bidirectional systematic deviation", _fmt_preview(metrics.get("bidirectional_systematic_deviation"))),
            ("Range of mean bidirectional positional deviation", _fmt_preview(metrics.get("range_mean_bidirectional_deviation"))),
            ("Bidirectional repeatability", _fmt_preview(metrics.get("bidirectional_repeatability"))),
            ("Unidirectional repeatability", _fmt_preview(metrics.get("unidirectional_repeatability"))),
            ("Mean reversal value", _fmt_preview(metrics.get("mean_reversal_value"))),
            ("Maximum reversal value", _fmt_preview(metrics.get("maximum_reversal_value"))),
            ("Measured points", str(len(rows))),
        ]
        for label, value in status_rows:
            v = QtWidgets.QLabel(str(value or "-"))
            if label in {"State", "Bidirectional accuracy", "Bidirectional repeatability"}:
                v.setStyleSheet("font-weight: 700; color: #102046;")
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            status_form.addRow(label, v)
        layout.addWidget(status_box)

        cfg_box = QtWidgets.QGroupBox("Configuration")
        cfg_form = QtWidgets.QFormLayout(cfg_box)
        cfg_form.setLabelAlignment(QtCore.Qt.AlignRight)
        cfg_rows = [
            ("IOC prefix", settings.get("prefix", "")),
            ("Axis ID", settings.get("axis_id", "")),
            ("Motor record", settings.get("motor", "")),
            ("Reference PV", settings.get("reference_pv", "")),
            ("Range", f"{_fmt_preview(settings.get('range_min'))} .. {_fmt_preview(settings.get('range_max'))}"),
            ("Target generation mode", settings.get("target_mode", "")),
            ("Target generation rule", settings.get("target_rule_note", "")),
            ("Base interval", _fmt_preview(settings.get("base_interval"))),
            ("Targets", ", ".join(_fmt_preview(v) for v in settings.get("targets", []))),
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
        layout.addWidget(cfg_box)
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

        browser = QtWidgets.QTextBrowser(root)
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet("background: white; border: 1px solid #d7e0eb; border-radius: 8px;")
        graph_uri = "data:image/svg+xml;utf8," + urllib.parse.quote(graph_svg)
        browser.setHtml(
            "<html><body style='margin:12px;background:#ffffff;'>"
            f"<img src='{graph_uri}' style='width:100%;height:auto;' alt='Bidirectional positioning error graph'>"
            "</body></html>"
        )
        layout.addWidget(browser, 1)
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

    def _apply_report_dataset(self, settings, rows, state="Loaded"):
        self._demo_mode = str(state).lower() == "demo"
        self.prefix_edit.setText(str(settings.get("prefix", "")))
        self.axis_edit.setText(str(settings.get("axis_id", "")))
        self.motor_record_edit.setText(str(settings.get("motor", "")))
        self.reference_pv_edit.setText(str(settings.get("reference_pv", "")))
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

        self._test_settings_cache = dict(settings)
        self._measurements = list(rows)
        self._latest_metrics = self._compute_metrics(self._measurements)
        self._latest_metrics["state"] = state
        self._latest_report_markdown = self._build_report_markdown()
        self._reload_results_table()
        self._populate_summary_table(self._latest_metrics.get("per_target", []))
        self._update_summary_labels(self._latest_metrics)
        self._update_duration_estimate()
        if not self._test_active:
            self._test_plan = self._build_test_plan(settings)
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
        if not self._measurements:
            self.load_demo_data()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("ISO 230 Report Preview")
        dlg.resize(1220, 860)
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

    def export_report(self):
        if not self._latest_report_markdown:
            self._latest_report_markdown = self._build_report_markdown()
        default_name = f"iso230_report_axis_{self._axis_id_text() or 'unknown'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        path, _flt = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save ISO 230 Report",
            str(Path.cwd() / default_name),
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
            str(Path.cwd() / default_name),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fp:
                writer = csv.writer(fp)
                writer.writerow(
                    [
                        "cycle",
                        "direction",
                        "target",
                        "reference_mean",
                        "reference_std",
                        "rbv_mean",
                        "rbv_std",
                        "command_mean",
                        "ref_error",
                        "rbv_error",
                        "timestamp",
                    ]
                )
                for row in self._measurements:
                    writer.writerow(
                        [
                            row["cycle"],
                            row["direction"],
                            row["target"],
                            row["reference_mean"],
                            row["reference_std"],
                            row["rbv_mean"],
                            row["rbv_std"],
                            row["command_mean"],
                            row["ref_error"],
                            row["rbv_error"],
                            row["timestamp"].isoformat(),
                        ]
                    )
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
