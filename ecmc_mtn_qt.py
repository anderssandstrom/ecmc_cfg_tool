#!/usr/bin/env python3
import argparse
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore

from ecmc_stream_qt import EpicsClient, _join_prefix_pv, compact_float_text


def _to_float(text, name):
    s = str(text).strip()
    if not s:
        raise ValueError(f"{name} is empty")
    return float(s)


def _to_int(text, name):
    s = str(text).strip()
    if not s:
        raise ValueError(f"{name} is empty")
    return int(float(s))


def _truthy_pv(v):
    s = str(v).strip().strip('"').lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    # EPICS enum-as-string values (common for motor record menu fields)
    if s.startswith("enab"):
        return True
    if s.startswith("disab"):
        return False
    try:
        return float(s) != 0.0
    except Exception:
        return False


class MiniTrendWidget(QtWidgets.QWidget):
    def __init__(self, title, series_defs, max_points=40):
        super().__init__()
        self.title = str(title)
        # series_defs: list[(name, color_hex)]
        self.series_defs = [(str(n), QtGui.QColor(c)) for n, c in series_defs]
        self.data = {name: deque(maxlen=max(2, int(max_points))) for name, _c in self.series_defs}
        self.setMinimumHeight(74)
        self.setMaximumHeight(92)

    def _axis_label_text(self, v):
        try:
            x = float(v)
        except Exception:
            return str(v)
        # Keep Y-axis labels short so they fit in the compact widget.
        ax = abs(x)
        if ax == 0:
            return "0"
        if ax < 1e-3 or ax >= 1e4:
            return f"{x:.2g}"
        if ax < 1:
            return compact_float_text(x, sig_digits=3)
        return compact_float_text(x, sig_digits=4)

    def append_point(self, values_by_name):
        for name, _c in self.series_defs:
            v = values_by_name.get(name)
            try:
                self.data[name].append(float(v) if v is not None else None)
            except Exception:
                self.data[name].append(None)
        self.update()

    def clear(self):
        for q in self.data.values():
            q.clear()
        self.update()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = self.rect()
        p.fillRect(r, QtGui.QColor("#f5f7fa"))
        p.setPen(QtGui.QPen(QtGui.QColor("#c9d2dc"), 1))
        p.drawRect(r.adjusted(0, 0, -1, -1))

        # Reserve a small left margin for Y-axis labels.
        label_w = 72
        plot = r.adjusted(6 + label_w, 18, -6, -6)

        if plot.width() < 20 or plot.height() < 20:
            return

        # Collect min/max from visible numeric points.
        vals = []
        for name, _c in self.series_defs:
            vals.extend([v for v in self.data[name] if isinstance(v, (int, float))])
        if not vals:
            p.setPen(QtGui.QColor("#7a8794"))
            p.drawText(plot, QtCore.Qt.AlignCenter, "no data")
            return
        vmin = min(vals)
        vmax = max(vals)
        if vmax == vmin:
            pad = abs(vmax) * 0.05 or 1.0
            vmin -= pad
            vmax += pad
        else:
            pad = (vmax - vmin) * 0.05
            vmin -= pad
            vmax += pad

        # Grid
        p.setPen(QtGui.QPen(QtGui.QColor("#e0e6ec"), 1))
        for frac in (0.25, 0.5, 0.75):
            y = plot.top() + int(plot.height() * frac)
            p.drawLine(plot.left(), y, plot.right(), y)

        # Y-axis labels (min/max/current zero marker if visible)
        p.setPen(QtGui.QColor("#5b6773"))
        left_label_rect = QtCore.QRect(r.left() + 4, plot.top() - 6, label_w - 6, 14)
        p.drawText(left_label_rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, self._axis_label_text(vmax))
        left_label_rect.moveTop(plot.bottom() - 8)
        p.drawText(left_label_rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, self._axis_label_text(vmin))
        if vmin < 0 < vmax:
            y0 = plot.bottom() - (plot.height() * (0.0 - vmin) / (vmax - vmin))
            p.setPen(QtGui.QPen(QtGui.QColor("#c7cfd8"), 1, QtCore.Qt.DashLine))
            p.drawLine(plot.left(), int(y0), plot.right(), int(y0))
            zr = QtCore.QRect(r.left() + 4, int(y0) - 7, label_w - 6, 14)
            p.setPen(QtGui.QColor("#5b6773"))
            p.drawText(zr, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, "0")

        # Plot each series
        maxlen = max((len(self.data[name]) for name, _c in self.series_defs), default=0)
        if maxlen <= 1:
            maxlen = 2
        for name, color in self.series_defs:
            pts = list(self.data[name])
            if not pts:
                continue
            path = QtGui.QPainterPath()
            started = False
            for i, v in enumerate(pts):
                if v is None:
                    started = False
                    continue
                x = plot.left() + (plot.width() * i / max(1, len(pts) - 1))
                y = plot.bottom() - (plot.height() * (float(v) - vmin) / (vmax - vmin))
                pt = QtCore.QPointF(x, y)
                if not started:
                    path.moveTo(pt)
                    started = True
                else:
                    path.lineTo(pt)
            p.setPen(QtGui.QPen(color, 1.8))
            p.drawPath(path)

        # Legend
        x = plot.left()
        y = plot.top() - 4
        for name, color in self.series_defs:
            p.setPen(QtGui.QPen(color, 2))
            p.drawLine(x, y, x + 10, y)
            p.setPen(QtGui.QColor("#2f3e4d"))
            p.drawText(x + 13, y + 4, name)
            x += 70


class MotionWindow(QtWidgets.QMainWindow):
    def __init__(self, prefix, axis_id, timeout):
        super().__init__()
        self._base_title = "ecmc Axis Motion Control"
        self.setWindowTitle(self._base_title)
        # Slightly smaller base font to compact the whole UI.
        _f = self.font()
        if _f.pointSize() > 0:
            _f.setPointSize(max(8, _f.pointSize() - 1))
            self.setFont(_f)
        self.resize(700, 340)

        self.client = EpicsClient(timeout=timeout)
        self.default_prefix = str(prefix or "").strip()
        self.default_axis_id = str(axis_id or "1").strip() or "1"

        self._seq_active = False
        self._seq_idle_until = None
        self._seq_next_target = None
        self._seq_params = {}
        self._seq_scan_points = []
        self._seq_scan_dir = 1
        self._seq_scan_idx = 0
        self._seq_timer = QtCore.QTimer(self)
        self._seq_timer.setInterval(250)
        self._seq_timer.timeout.connect(self._sequence_tick)
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(200)
        self._status_timer.timeout.connect(self._periodic_status_tick)
        self._spinner_chars = ["|", "/", "-", "\\"]
        self._spinner_index = 0
        self._last_rbv_text = None
        self._positions_initialized = False
        self._trend_monitor_values = {"PosAct": None, "PosSet": None, "PosErr": None}
        self._trend_monitor_pvs = {}
        self._trend_use_monitor = False
        self._active_motion_mode = None
        self._is_motor_moving = False
        self._jog_stop_dialog = None
        self._did_startup_axis_presence_check = False
        self._startup_axis_probe_ok = False
        self._last_status_vals = {}
        self._axis_combo_updating = False
        self._axis_combo_open_new_instance = False

        self._build_ui(timeout)
        self._log(f"Connected via backend: {self.client.backend}")
        self._status_timer.start()
        QtCore.QTimer.singleShot(0, self._startup_axis_presence_check)

    def _build_ui(self, timeout):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(4)
        self.cfg_toggle_btn = QtWidgets.QPushButton("Show Config")
        self.cfg_toggle_btn.setAutoDefault(False)
        self.cfg_toggle_btn.setDefault(False)
        self.cfg_toggle_btn.clicked.connect(self._toggle_config_panel)
        self.log_toggle_btn = QtWidgets.QPushButton("Show Log")
        self.log_toggle_btn.setAutoDefault(False)
        self.log_toggle_btn.setDefault(False)
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)
        self.graphs_toggle_btn = QtWidgets.QPushButton("Show Graphs")
        self.graphs_toggle_btn.setAutoDefault(False)
        self.graphs_toggle_btn.setDefault(False)
        self.graphs_toggle_btn.clicked.connect(self._toggle_graphs_panel)
        self.open_cntrl_btn = QtWidgets.QPushButton("Cntrl Cfg App")
        self.open_cntrl_btn.setAutoDefault(False)
        self.open_cntrl_btn.setDefault(False)
        self.open_cntrl_btn.clicked.connect(self._open_controller_window)
        self.open_axis_btn = QtWidgets.QPushButton("Axis Cfg App")
        self.open_axis_btn.setAutoDefault(False)
        self.open_axis_btn.setDefault(False)
        self.open_axis_btn.clicked.connect(self._open_axis_window)
        self.axis_pick_combo = QtWidgets.QComboBox()
        self.axis_pick_combo.setMinimumWidth(170)
        self.axis_pick_combo.setMaximumWidth(260)
        self.axis_pick_combo.activated.connect(self._on_axis_combo_activated)
        for w in (
            self.cfg_toggle_btn,
            self.log_toggle_btn,
            self.graphs_toggle_btn,
            self.open_cntrl_btn,
            self.open_axis_btn,
            self.axis_pick_combo,
        ):
            try:
                w.setMaximumHeight(24)
            except Exception:
                pass
        top_row.addWidget(self.cfg_toggle_btn)
        top_row.addWidget(self.log_toggle_btn)
        top_row.addWidget(self.graphs_toggle_btn)
        top_row.addWidget(self.open_cntrl_btn)
        top_row.addWidget(self.open_axis_btn)
        top_row.addStretch(1)
        axis_sel_col = QtWidgets.QVBoxLayout()
        axis_sel_col.setContentsMargins(0, 0, 0, 0)
        axis_sel_col.setSpacing(2)
        axis_sel_row = QtWidgets.QHBoxLayout()
        axis_sel_row.setContentsMargins(0, 0, 0, 0)
        axis_sel_row.setSpacing(4)
        axis_sel_row.addWidget(QtWidgets.QLabel("Axis"))
        axis_sel_row.addWidget(self.axis_pick_combo)
        axis_sel_col.addLayout(axis_sel_row)
        self.caqtdm_axis_btn = QtWidgets.QPushButton("caqtdm Axis")
        self.caqtdm_axis_btn.setAutoDefault(False)
        self.caqtdm_axis_btn.setDefault(False)
        self.caqtdm_axis_btn.setMaximumHeight(22)
        self.caqtdm_axis_btn.clicked.connect(self._open_caqtdm_axis_panel)
        axis_sel_col.addWidget(self.caqtdm_axis_btn)
        top_row.addLayout(axis_sel_col)
        layout.addLayout(top_row)

        self.cfg_group = QtWidgets.QGroupBox("Axis / Motor Record")
        cfg = QtWidgets.QGridLayout(self.cfg_group)
        cfg.setContentsMargins(6, 6, 6, 6)
        cfg.setHorizontalSpacing(4)
        cfg.setVerticalSpacing(3)

        self.prefix_edit = QtWidgets.QLineEdit(self.default_prefix)
        self.axis_edit = QtWidgets.QLineEdit(self.default_axis_id)
        self.axis_edit.setMaximumWidth(70)
        self.timeout_edit = QtWidgets.QDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(float(timeout))
        self.timeout_edit.valueChanged.connect(self._set_timeout)

        self.axis_pfx_cfg_pv_edit = QtWidgets.QLineEdit()
        self.motor_name_cfg_pv_edit = QtWidgets.QLineEdit()
        self.motor_record_edit = QtWidgets.QLineEdit("")
        self.motor_record_edit.setPlaceholderText("Resolved motor record base PV (editable override)")

        self.auto_refresh_status = QtWidgets.QCheckBox("Refresh status after actions")
        self.auto_refresh_status.setChecked(True)

        self._update_cfg_pv_edits()

        self.prefix_edit.editingFinished.connect(self._update_cfg_pv_edits)
        self.axis_edit.editingFinished.connect(self._update_cfg_pv_edits)

        resolve_btn = QtWidgets.QPushButton("Resolve Motor Record")
        resolve_btn.setAutoDefault(False)
        resolve_btn.setDefault(False)
        resolve_btn.clicked.connect(self.resolve_motor_record_name)

        refresh_btn = QtWidgets.QPushButton("Read Status")
        refresh_btn.setAutoDefault(False)
        refresh_btn.setDefault(False)
        refresh_btn.clicked.connect(self.refresh_status)
        axis_apply_btn = QtWidgets.QPushButton("Apply Axis")
        axis_apply_btn.setAutoDefault(False)
        axis_apply_btn.setDefault(False)
        axis_apply_btn.clicked.connect(self._apply_axis_top)
        self.caqtdm_main_btn = QtWidgets.QPushButton("caqtdm Main")
        self.caqtdm_main_btn.setAutoDefault(False)
        self.caqtdm_main_btn.setDefault(False)
        self.caqtdm_main_btn.clicked.connect(self._open_caqtdm_main_panel)

        cfg.addWidget(QtWidgets.QLabel("IOC Prefix"), 0, 0)
        cfg.addWidget(self.prefix_edit, 0, 1)
        cfg.addWidget(QtWidgets.QLabel("Axis ID"), 0, 2)
        cfg.addWidget(self.axis_edit, 0, 3)
        cfg.addWidget(axis_apply_btn, 0, 4)
        cfg.addWidget(QtWidgets.QLabel("Timeout [s]"), 0, 5)
        cfg.addWidget(self.timeout_edit, 0, 6)

        cfg.addWidget(QtWidgets.QLabel("Axis Prefix PV"), 1, 0)
        cfg.addWidget(self.axis_pfx_cfg_pv_edit, 1, 1, 1, 3)
        cfg.addWidget(QtWidgets.QLabel("Motor Name PV"), 1, 4)
        cfg.addWidget(self.motor_name_cfg_pv_edit, 1, 5, 1, 2)

        cfg.addWidget(QtWidgets.QLabel("Motor Record"), 2, 0)
        cfg.addWidget(self.motor_record_edit, 2, 1, 1, 5)
        cfg.addWidget(resolve_btn, 2, 6)

        cfg.addWidget(self.auto_refresh_status, 3, 0, 1, 3)
        cfg.addWidget(self.caqtdm_main_btn, 3, 5)
        cfg.addWidget(refresh_btn, 3, 6)
        layout.addWidget(self.cfg_group)

        self._build_motion_settings_group(layout)
        motion_row = QtWidgets.QHBoxLayout()
        motion_row.setSpacing(2)
        self.move_group = self._build_move_group()
        self.tweak_group = self._build_tweak_group()
        self.jog_group = self._build_jog_group()
        motion_row.addWidget(self.move_group, 2)
        motion_row.addWidget(self.tweak_group, 0)
        motion_row.addWidget(self.jog_group, 1)
        layout.addLayout(motion_row)
        self.seq_group = self._build_sequence_group(layout)
        self._build_status_group(layout)
        self._build_trend_group(layout)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        layout.addWidget(self.log, stretch=1)
        self.cfg_group.setVisible(False)
        self.trends_group.setVisible(False)
        self.log.setVisible(False)
        self._refresh_axis_pick_combo()

    def _toggle_config_panel(self):
        visible = not self.cfg_group.isVisible()
        self.cfg_group.setVisible(visible)
        self.cfg_toggle_btn.setText("Hide Config" if visible else "Show Config")
        self._resize_to_contents()

    def _toggle_log_panel(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_toggle_btn.setText("Hide Log" if visible else "Show Log")
        self._resize_to_contents()

    def _toggle_graphs_panel(self):
        visible = not self.trends_group.isVisible()
        self.trends_group.setVisible(visible)
        self.graphs_toggle_btn.setText("Hide Graphs" if visible else "Show Graphs")
        self._resize_to_contents()

    def _resize_to_contents(self):
        # Recompute after Qt has applied visibility/layout changes.
        def _do():
            try:
                self.adjustSize()
            except Exception:
                pass
        QtCore.QTimer.singleShot(0, _do)

    def _apply_axis_top(self):
        axis_txt = self.axis_edit.text().strip() or self.default_axis_id
        self.axis_edit.setText(axis_txt)
        if self._seq_active or self._active_motion_mode in {"move", "jog", "sequence"} or self._is_motor_moving:
            try:
                self._log(f"Axis change requested while motion active; stopping motion before switching to axis {axis_txt}")
                self.stop_motion()
            except Exception as ex:
                self._log(f"Failed to stop motion during axis change: {ex}")
            self._close_jog_stop_dialog()
        self._update_cfg_pv_edits()
        self._positions_initialized = False
        self._sync_axis_combo_to_axis_id(axis_txt)
        self.resolve_motor_record_name()

    def _sync_axis_combo_to_axis_id(self, axis_id):
        if not hasattr(self, "axis_pick_combo"):
            return
        want = str(axis_id or "").strip()
        if not want:
            return
        idx = self.axis_pick_combo.findData(want, role=QtCore.Qt.UserRole)
        if idx >= 0:
            self._axis_combo_updating = True
            self.axis_pick_combo.setCurrentIndex(idx)
            self._axis_combo_updating = False

    def _axis_combo_install_open_new_item(self):
        if not hasattr(self, "axis_pick_combo") or self.axis_pick_combo.count() <= 0:
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
        if not hasattr(self, "axis_pick_combo"):
            return
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
            return
        self.axis_pick_combo.clear()
        self.axis_pick_combo.addItem("Open New Instance", "__open_new__")
        self._axis_combo_install_open_new_item()
        for ax in axes:
            axis_id = str(ax.get("axis_id", "") or "").strip()
            axis_type = str(ax.get("axis_type", "") or "")
            motor_name = str(ax.get("motor_name", "") or "")
            tdisp = "REAL" if axis_type.upper() == "REAL" else ("Virtual" if axis_type else "?")
            label = f"{axis_id} | {tdisp}"
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
            script = QtCore.QFileInfo(__file__).dir().filePath("start_mtn.sh")
            prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
            try:
                subprocess.Popen(
                    ["bash", str(script), str(prefix), str(axis_id)],
                    cwd=str(QtCore.QFileInfo(script).absolutePath()),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._log(f"Started new motion window for axis {axis_id} (prefix {prefix})")
            except Exception as ex:
                self._log(f"Failed to start new motion window: {ex}")
            self._sync_axis_combo_to_axis_id(self._axis_id_text())
            return
        self.axis_edit.setText(axis_id)
        self._apply_axis_top()

    def _prompt_axis_selection_via_combo(self, reason_msg=None):
        if reason_msg:
            self._log(reason_msg)
        if not self.cfg_group.isVisible():
            self.cfg_group.setVisible(True)
            self.cfg_toggle_btn.setText("Hide Config")
        self._refresh_axis_pick_combo()
        self._resize_to_contents()
        try:
            self.axis_pick_combo.setFocus(QtCore.Qt.OtherFocusReason)
        except Exception:
            pass
        QtCore.QTimer.singleShot(0, self.axis_pick_combo.showPopup)

    def _discover_axes_from_ioc(self):
        prefix = self.prefix_edit.text().strip() or self.default_prefix
        if not prefix:
            raise RuntimeError("IOC prefix is empty")
        cur = str(self.client.get(_join_prefix_pv(prefix, "MCU-Cfg-AX-FrstObjId"), as_string=True) or "").strip().strip('"')
        out = []
        seen = set()
        while cur and cur != "-1":
            axis_id = str(cur).strip()
            if not axis_id or axis_id in seen:
                break
            seen.add(axis_id)
            axis_pfx = ""
            motor_name = ""
            try:
                axis_pfx = str(self.client.get(_join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Pfx"), as_string=True) or "").strip().strip('"')
            except Exception:
                pass
            try:
                motor_name = str(self.client.get(_join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Nam"), as_string=True) or "").strip().strip('"')
            except Exception:
                pass
            out.append({
                "axis_id": axis_id,
                "motor": self._combine_motor_record(axis_pfx, motor_name),
                "motor_name": motor_name,
                "axis_type": "",
            })
            if out[-1]["motor"]:
                try:
                    out[-1]["axis_type"] = str(self.client.get(f"{out[-1]['motor']}-Type", as_string=True) or "").strip().strip('"')
                except Exception:
                    out[-1]["axis_type"] = ""
            cur = str(self.client.get(_join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-NxtObjId"), as_string=True) or "").strip().strip('"')
        return out

    def _resolve_axis_selector_to_id(self, selector):
        s = str(selector or "").strip()
        if not s:
            return ""
        if re.fullmatch(r"\d+", s):
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

    def _open_axis_picker_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Select Axis")
        dlg.resize(520, 320)
        lay = QtWidgets.QVBoxLayout(dlg)
        info = QtWidgets.QLabel("Discovering axes from IOC configuration...")
        lay.addWidget(info)
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Axis ID", "Type", "Motor", "Motor Name"])
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        lay.addWidget(table, 1)
        btn_row = QtWidgets.QHBoxLayout()
        open_new_chk = QtWidgets.QCheckBox("Open New Instance")
        refresh_btn = QtWidgets.QPushButton("Refresh")
        select_btn = QtWidgets.QPushButton("Select")
        close_btn = QtWidgets.QPushButton("Close")
        for b in (refresh_btn, select_btn, close_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
        btn_row.addWidget(open_new_chk)
        btn_row.addWidget(refresh_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(select_btn)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        def populate():
            table.setRowCount(0)
            try:
                axes = self._discover_axes_from_ioc()
            except Exception as ex:
                info.setText(f"Axis discovery failed: {ex}")
                return
            info.setText(f"Found {len(axes)} axis(es)")
            cur_axis = self._axis_id_text()
            sel_row = -1
            for r, ax in enumerate(axes):
                table.insertRow(r)
                axis_type = str(ax.get("axis_type", "") or "")
                type_disp = "REAL" if axis_type.upper() == "REAL" else ("Virtual" if axis_type else "?")
                vals = [str(ax.get("axis_id", "") or ""), type_disp, str(ax.get("motor", "") or ""), str(ax.get("motor_name", "") or "")]
                for c, txt in enumerate(vals):
                    it = QtWidgets.QTableWidgetItem(txt)
                    if c in (0, 1):
                        it.setTextAlignment(QtCore.Qt.AlignCenter)
                    table.setItem(r, c, it)
                if vals[0] == cur_axis:
                    sel_row = r
            if sel_row >= 0:
                table.selectRow(sel_row)

        def apply_selected():
            r = table.currentRow()
            if r < 0:
                return
            it = table.item(r, 0)
            if it is None:
                return
            axis_id = it.text().strip()
            if open_new_chk.isChecked():
                script = QtCore.QFileInfo(__file__).dir().filePath("start_mtn.sh")
                prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
                try:
                    subprocess.Popen(
                        ["bash", str(script), str(prefix), str(axis_id)],
                        cwd=str(QtCore.QFileInfo(script).absolutePath()),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._log(f"Started new motion window for axis {axis_id} (prefix {prefix})")
                except Exception as ex:
                    self._log(f"Failed to start new motion window: {ex}")
                    return
            else:
                self.axis_edit.setText(axis_id)
                self._apply_axis_top()
            dlg.accept()

        refresh_btn.clicked.connect(populate)
        select_btn.clicked.connect(apply_selected)
        close_btn.clicked.connect(dlg.reject)
        table.itemDoubleClicked.connect(lambda _it: apply_selected())
        populate()
        dlg.exec_()

    def _startup_axis_presence_check(self):
        if self._did_startup_axis_presence_check:
            return
        self._did_startup_axis_presence_check = True
        prefix = self.prefix_edit.text().strip() or self.default_prefix
        cur_axis = self._axis_id_text()
        resolved_id = self._resolve_axis_selector_to_id(cur_axis)
        if resolved_id and resolved_id != cur_axis:
            self._log(f'Axis selector "{cur_axis}" resolved to axis {resolved_id}')
            self.axis_edit.setText(resolved_id)
            self._apply_axis_top()
            cur_axis = resolved_id
        if not prefix:
            self._log("Startup axis probe skipped: IOC prefix unavailable; opening axis picker")
            self._open_axis_picker_dialog()
            return
        try:
            probe_pv = _join_prefix_pv(prefix, f"MCU-Cfg-AX{cur_axis}-Pfx")
            raw = self.client.get(probe_pv, as_string=True)
        except Exception as ex:
            self._log(f"Startup axis probe failed for axis {cur_axis}: {ex}; opening axis picker")
            self._open_axis_picker_dialog()
            return
        if str(raw or "").strip().strip('"'):
            self._startup_axis_probe_ok = True
            self.resolve_motor_record_name()
            return
        self._log(f"Axis {cur_axis} probe returned empty; opening axis picker")
        self._open_axis_picker_dialog()

    def _build_motion_settings_group(self, parent_layout):
        g = QtWidgets.QGroupBox("Shared Motion Settings")
        l = QtWidgets.QGridLayout(g)
        l.setContentsMargins(6, 6, 6, 6)
        l.setHorizontalSpacing(4)
        l.setVerticalSpacing(3)
        self.motion_velo_edit = QtWidgets.QLineEdit("1")
        self.motion_acc_edit = QtWidgets.QLineEdit("1")
        self.motion_vmax_edit = QtWidgets.QLineEdit("")
        self.motion_accs_edit = QtWidgets.QLineEdit("")
        self.motion_vmax_edit.setPlaceholderText("optional")
        self.motion_accs_edit.setPlaceholderText("optional")
        for e in (self.motion_velo_edit, self.motion_acc_edit, self.motion_vmax_edit, self.motion_accs_edit):
            e.setMaximumHeight(24)
        self.motion_velo_edit.setMaximumWidth(72)
        self.motion_acc_edit.setMaximumWidth(72)
        self.motion_vmax_edit.setMaximumWidth(72)
        self.motion_accs_edit.setMaximumWidth(72)
        self.drive_enable_btn = QtWidgets.QPushButton("Drive: ?")
        stop_btn = QtWidgets.QPushButton("STOP")
        kill_btn = QtWidgets.QPushButton("KILL")
        for b in (self.drive_enable_btn, stop_btn, kill_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setMaximumHeight(24)
        self.drive_enable_btn.clicked.connect(self.toggle_drive_enable)
        self._set_drive_enable_button_style(None)
        stop_btn.setStyleSheet(
            "QPushButton { background: #f39c12; color: #111; font-weight: 700; border: 1px solid #b86f00; padding: 4px 8px; }"
            "QPushButton:pressed { background: #d98500; }"
        )
        kill_btn.setStyleSheet(
            "QPushButton { background: #8b1e1e; color: #fff; font-weight: 700; border: 1px solid #5e1111; padding: 4px 8px; }"
            "QPushButton:pressed { background: #6f1717; }"
        )
        stop_btn.clicked.connect(self.stop_motion)
        kill_btn.clicked.connect(self.kill_motion)
        l.addWidget(QtWidgets.QLabel("VELO / JVEL"), 0, 0)
        l.addWidget(self.motion_velo_edit, 0, 1)
        l.addWidget(QtWidgets.QLabel("ACCL"), 0, 2)
        l.addWidget(self.motion_acc_edit, 0, 3)
        l.addWidget(QtWidgets.QLabel("VMAX"), 0, 4)
        l.addWidget(self.motion_vmax_edit, 0, 5)
        l.addWidget(QtWidgets.QLabel("ACCS"), 0, 6)
        l.addWidget(self.motion_accs_edit, 0, 7)
        l.addWidget(self.drive_enable_btn, 0, 8)
        l.addWidget(stop_btn, 0, 9)
        l.addWidget(kill_btn, 0, 10)
        g.setMaximumHeight(62)
        parent_layout.addWidget(g)

    def _build_move_group(self, parent_layout=None):
        g = QtWidgets.QGroupBox("1. Move To Position")
        l = QtWidgets.QGridLayout(g)
        l.setContentsMargins(6, 6, 6, 6)
        l.setHorizontalSpacing(4)
        l.setVerticalSpacing(3)

        self.move_pos_edit = QtWidgets.QLineEdit("0")
        self.move_pos_edit.setMaximumHeight(24)
        self.move_pos_edit.setMaximumWidth(118)
        self.move_relative_chk = QtWidgets.QCheckBox("Rel")

        move_btn = QtWidgets.QPushButton("Move")
        for b in (move_btn,):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setMaximumHeight(24)
            b.setFixedWidth(46)
        move_btn.clicked.connect(self.move_to_position)

        l.addWidget(QtWidgets.QLabel("Position"), 0, 0)
        l.addWidget(self.move_pos_edit, 0, 1)
        l.addWidget(self.move_relative_chk, 0, 2)
        l.addWidget(move_btn, 0, 3)
        g.setMaximumHeight(62)

        if parent_layout is not None and hasattr(parent_layout, "addWidget"):
            parent_layout.addWidget(g)
        return g

    def _build_sequence_group(self, parent_layout):
        g = QtWidgets.QGroupBox("4. Sequence (A <-> B)")
        l = QtWidgets.QGridLayout(g)
        l.setContentsMargins(6, 6, 6, 6)
        l.setHorizontalSpacing(4)
        l.setVerticalSpacing(3)

        self.seq_a_edit = QtWidgets.QLineEdit("0")
        self.seq_b_edit = QtWidgets.QLineEdit("10")
        self.seq_idle_edit = QtWidgets.QLineEdit("0")
        self.seq_steps_edit = QtWidgets.QLineEdit("1")
        self.seq_relative_chk = QtWidgets.QCheckBox("Rel")
        for e in (self.seq_a_edit, self.seq_b_edit, self.seq_idle_edit, self.seq_steps_edit):
            e.setMaximumHeight(24)
        for e in (self.seq_a_edit, self.seq_b_edit, self.seq_idle_edit):
            e.setMaximumWidth(64)
        self.seq_steps_edit.setMaximumWidth(44)
        self.seq_state_label = QtWidgets.QLabel("Stopped")
        self.seq_state_label.setMinimumHeight(20)
        self.seq_state_label.setMinimumWidth(96)
        self.seq_state_label.setMaximumWidth(96)
        self.seq_state_label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Preferred)

        start_btn = QtWidgets.QPushButton("Start Sequence")
        for b in (start_btn,):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setMaximumHeight(24)
        start_btn.clicked.connect(self.start_sequence)

        l.addWidget(QtWidgets.QLabel("Pos A"), 0, 0)
        l.addWidget(self.seq_a_edit, 0, 1)
        l.addWidget(QtWidgets.QLabel("Steps"), 0, 2)
        l.addWidget(self.seq_steps_edit, 0, 3)
        l.addWidget(QtWidgets.QLabel("Pos B"), 0, 4)
        l.addWidget(self.seq_b_edit, 0, 5)
        l.addWidget(QtWidgets.QLabel("Idle [s]"), 0, 6)
        l.addWidget(self.seq_idle_edit, 0, 7)
        l.addWidget(self.seq_relative_chk, 0, 8)
        l.addWidget(start_btn, 0, 9)
        l.addWidget(QtWidgets.QLabel("State"), 0, 10)
        l.addWidget(self.seq_state_label, 0, 11)
        g.setMaximumHeight(62)

        parent_layout.addWidget(g)
        return g

    def _build_jog_group(self, parent_layout=None):
        g = QtWidgets.QGroupBox("3. Endless Motion (Forward / Backward)")
        l = QtWidgets.QGridLayout(g)
        l.setContentsMargins(6, 6, 6, 6)
        l.setHorizontalSpacing(4)
        l.setVerticalSpacing(3)

        fwd_btn = QtWidgets.QPushButton("Endless Fwd")
        bwd_btn = QtWidgets.QPushButton("Endless Bwd")
        for b in (fwd_btn, bwd_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setMaximumHeight(24)
            b.setFixedWidth(84)
        fwd_btn.clicked.connect(self.start_jog_forward)
        bwd_btn.clicked.connect(self.start_jog_backward)

        l.addWidget(bwd_btn, 0, 0)
        l.addWidget(fwd_btn, 0, 1)
        g.setMaximumHeight(62)

        if parent_layout is not None and hasattr(parent_layout, "addWidget"):
            parent_layout.addWidget(g)
        return g

    def _build_tweak_group(self, parent_layout=None):
        g = QtWidgets.QGroupBox("2. Tweak")
        l = QtWidgets.QGridLayout(g)
        l.setContentsMargins(6, 6, 6, 6)
        l.setHorizontalSpacing(4)
        l.setVerticalSpacing(3)

        self.tweak_step_edit = QtWidgets.QLineEdit("1")
        self.tweak_step_edit.setMaximumHeight(24)
        self.tweak_step_edit.setMaximumWidth(72)

        twr_btn = QtWidgets.QPushButton("<-")
        twf_btn = QtWidgets.QPushButton("->")
        for b in (twr_btn, twf_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setMaximumHeight(24)
            b.setFixedWidth(36)
        twr_btn.clicked.connect(self.tweak_reverse)
        twf_btn.clicked.connect(self.tweak_forward)

        l.addWidget(twr_btn, 0, 0)
        l.addWidget(self.tweak_step_edit, 0, 1)
        l.addWidget(twf_btn, 0, 2)
        g.setMaximumHeight(62)
        g.setMaximumWidth(148)

        if parent_layout is not None and hasattr(parent_layout, "addWidget"):
            parent_layout.addWidget(g)
        return g

    def _build_status_group(self, parent_layout):
        g = QtWidgets.QGroupBox("Motor Record Status")
        l = QtWidgets.QGridLayout(g)
        l.setContentsMargins(6, 6, 6, 6)
        l.setHorizontalSpacing(4)
        l.setVerticalSpacing(3)
        self.status_fields = {}
        self.status_extra_fields = {}
        self.rbv_motion_label = QtWidgets.QLabel("idle")
        self.rbv_motion_label.setMinimumWidth(92)
        self.rbv_motion_label.setMaximumWidth(92)
        self.rbv_motion_label.setMinimumHeight(22)
        self.rbv_motion_label.setMaximumHeight(22)
        self.rbv_motion_label.setAlignment(QtCore.Qt.AlignCenter)
        self.rbv_motion_label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        self.rbv_motion_label.setStyleSheet(
            "QLabel { background: #d8ead2; color: #173b17; font-weight: 700; padding: 2px 6px; border: 1px solid #9fbe95; }"
        )
        names = [("VAL", 0, 0), ("RBV", 0, 2), ("DMOV", 0, 4), ("VELO", 1, 0), ("ACCL", 1, 2), ("VMAX", 1, 4), ("CNEN", 1, 6)]
        for name, r, c in names:
            l.addWidget(QtWidgets.QLabel(name), r, c)
            e = QtWidgets.QLineEdit("")
            e.setReadOnly(True)
            e.setMaximumHeight(24)
            e.setMaximumWidth(88)
            if name == "RBV":
                e.setMinimumWidth(100)
                e.setMaximumWidth(116)
                e.setMaximumHeight(28)
                e.setStyleSheet(
                    "QLineEdit { font-size: 14px; font-weight: 700; background: #eef6ff; border: 2px solid #6f97c6; }"
                )
            l.addWidget(e, r, c + 1)
            self.status_fields[name] = e
        l.addWidget(QtWidgets.QLabel("Motion"), 0, 6)
        l.addWidget(self.rbv_motion_label, 0, 7)
        l.addWidget(QtWidgets.QLabel("ErrId"), 2, 0)
        self.errid_status_edit = QtWidgets.QLineEdit("")
        self.errid_status_edit.setReadOnly(True)
        self.errid_status_edit.setMaximumHeight(24)
        self.errid_status_edit.setMaximumWidth(90)
        l.addWidget(self.errid_status_edit, 2, 1)
        self.status_extra_fields["ErrId"] = self.errid_status_edit
        self.reset_err_btn = QtWidgets.QPushButton("Reset")
        self.reset_err_btn.setAutoDefault(False)
        self.reset_err_btn.setDefault(False)
        self.reset_err_btn.setMaximumHeight(24)
        self.reset_err_btn.clicked.connect(self.reset_error)
        l.addWidget(self.reset_err_btn, 2, 2)
        self.reset_all_err_btn = QtWidgets.QPushButton("Reset All")
        self.reset_all_err_btn.setAutoDefault(False)
        self.reset_all_err_btn.setDefault(False)
        self.reset_all_err_btn.setMaximumHeight(24)
        self.reset_all_err_btn.clicked.connect(self.reset_all_errors)
        l.addWidget(self.reset_all_err_btn, 2, 3)
        l.addWidget(QtWidgets.QLabel("MsgTxt"), 2, 4)
        self.msgtxt_status_edit = QtWidgets.QLineEdit("")
        self.msgtxt_status_edit.setReadOnly(True)
        self.msgtxt_status_edit.setMaximumHeight(24)
        self.msgtxt_status_edit.setMinimumWidth(150)
        l.addWidget(self.msgtxt_status_edit, 2, 5, 1, 3)
        self.status_extra_fields["MsgTxt"] = self.msgtxt_status_edit
        g.setMaximumHeight(112)
        parent_layout.addWidget(g)

    def _build_trend_group(self, parent_layout):
        self.trends_group = QtWidgets.QGroupBox("")
        l = QtWidgets.QGridLayout(self.trends_group)
        l.setContentsMargins(4, 4, 4, 4)
        l.setHorizontalSpacing(4)
        l.setVerticalSpacing(3)
        # Trend append runs at ~5 Hz (timer 200 ms), so 50 points ~= 10 s history.
        self.trend_pos_widget = MiniTrendWidget("PosAct / PosSet", [("PosAct", "#1f77b4"), ("PosSet", "#ff7f0e")], max_points=50)
        self.trend_err_widget = MiniTrendWidget("PosErr", [("PosErr", "#d62728")], max_points=50)
        l.addWidget(self.trend_pos_widget, 0, 0)
        l.addWidget(self.trend_err_widget, 1, 0)
        parent_layout.addWidget(self.trends_group)

    def _set_timeout(self, value):
        self.client.timeout = float(value)

    def _log(self, msg):
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _axis_id_text(self):
        return self.axis_edit.text().strip() or self.default_axis_id

    def _set_active_motion_mode(self, mode):
        self._active_motion_mode = mode
        self._update_motion_group_enable_state()

    def _clear_active_motion_mode(self):
        self._active_motion_mode = None
        self._update_motion_group_enable_state()
        self._close_jog_stop_dialog()

    def _update_motion_group_enable_state(self):
        groups = {
            "move": getattr(self, "move_group", None),
            "tweak": getattr(self, "tweak_group", None),
            "jog": getattr(self, "jog_group", None),
            "sequence": getattr(self, "seq_group", None),
        }
        active = self._active_motion_mode
        for name, grp in groups.items():
            if grp is None:
                continue
            grp.setEnabled(active is None or active == name)

    def _update_active_mode_from_status(self, _vals=None):
        if self._seq_active:
            self._set_active_motion_mode("sequence")
            return
        if self._active_motion_mode in {"move", "tweak", "jog"} and not self._is_motor_moving:
            self._clear_active_motion_mode()

    def _close_jog_stop_dialog(self):
        dlg = getattr(self, "_jog_stop_dialog", None)
        if dlg is None:
            return
        try:
            dlg.close()
        except Exception:
            pass
        self._jog_stop_dialog = None

    def _show_jog_stop_dialog(self, activity_label):
        self._close_jog_stop_dialog()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Motion Active")
        dlg.setModal(False)
        dlg.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)
        v = QtWidgets.QVBoxLayout(dlg)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)
        motor = self.motor_record_edit.text().strip() if hasattr(self, "motor_record_edit") else ""
        msg_txt = f"{activity_label} is active"
        if motor:
            msg_txt += f"\n{motor}"
        msg = QtWidgets.QLabel(msg_txt)
        msg.setWordWrap(True)
        msg.setStyleSheet("QLabel { font-weight: 600; }")
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(8)
        stop_btn = QtWidgets.QPushButton("STOP")
        stop_btn.setMinimumSize(160, 54)
        stop_btn.setStyleSheet(
            "QPushButton { background: #f39c12; color: #111; font-weight: 700; font-size: 18px; border: 2px solid #b86f00; padding: 6px 12px; }"
            "QPushButton:pressed { background: #d98500; }"
        )
        stop_btn.clicked.connect(self.stop_motion)
        stop_btn.clicked.connect(dlg.close)
        kill_btn = QtWidgets.QPushButton("KILL")
        kill_btn.setMinimumSize(160, 54)
        kill_btn.setStyleSheet(
            "QPushButton { background: #8b1e1e; color: #fff; font-weight: 700; font-size: 18px; border: 2px solid #5e1111; padding: 6px 12px; }"
            "QPushButton:pressed { background: #6f1717; }"
        )
        kill_btn.clicked.connect(self.kill_motion)
        kill_btn.clicked.connect(dlg.close)
        btn_row.addWidget(stop_btn)
        btn_row.addWidget(kill_btn)
        v.addWidget(msg)
        v.addLayout(btn_row)
        dlg.finished.connect(lambda _=0: setattr(self, "_jog_stop_dialog", None))
        self._jog_stop_dialog = dlg
        dlg.adjustSize()
        dlg.show()

    def _open_controller_window(self):
        script = QtCore.QFileInfo(__file__).dir().filePath("start_cntrl.sh")
        if not QtCore.QFileInfo(script).exists():
            self._log(f"Launcher not found: start_cntrl.sh")
            return
        axis_id = self._axis_id_text()
        prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
        try:
            subprocess.Popen(
                ["bash", str(script), str(prefix), str(axis_id)],
                cwd=str(QtCore.QFileInfo(script).absolutePath()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started controller window for axis {axis_id} (prefix {prefix})")
        except Exception as ex:
            self._log(f"Failed to start controller window: {ex}")

    def _open_axis_window(self):
        script = QtCore.QFileInfo(__file__).dir().filePath("start_axis.sh")
        if not QtCore.QFileInfo(script).exists():
            self._log(f"Launcher not found: start_axis.sh")
            return
        axis_id = self._axis_id_text()
        prefix = self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"
        try:
            subprocess.Popen(
                ["bash", str(script), str(prefix), str(axis_id)],
                cwd=str(QtCore.QFileInfo(script).absolutePath()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started axis window for axis {axis_id} (prefix {prefix})")
        except Exception as ex:
            self._log(f"Failed to start axis window: {ex}")

    def _open_caqtdm_axis_panel(self):
        ioc_prefix = self.prefix_edit.text().strip() or self.default_prefix or ""
        axis_id = self._axis_id_text()
        motor_prefix = ""
        axis_name = ""
        try:
            pfx_pv = self.axis_pfx_cfg_pv_edit.text().strip() if hasattr(self, "axis_pfx_cfg_pv_edit") else ""
            if pfx_pv:
                motor_prefix = self._read_cfg_pv(pfx_pv)
        except Exception:
            motor_prefix = ""
        try:
            nam_pv = self.motor_name_cfg_pv_edit.text().strip() if hasattr(self, "motor_name_cfg_pv_edit") else ""
            if nam_pv:
                axis_name = self._read_cfg_pv(nam_pv)
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
                cwd=str(QtCore.QFileInfo(__file__).dir().absolutePath()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started caQtDM axis panel ({macro})")
        except Exception as ex:
            self._log(f"Failed to start caQtDM axis panel: {ex}")

    def _open_caqtdm_main_panel(self):
        ioc_prefix = self.prefix_edit.text().strip() or self.default_prefix or ""
        macro = f"IOC={ioc_prefix}"
        try:
            cmd = f'caqtdm -macro "{macro}" ecmcMain.ui'
            subprocess.Popen(
                ["bash", "-lc", cmd],
                cwd=str(QtCore.QFileInfo(__file__).dir().absolutePath()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started caQtDM main panel ({macro})")
        except Exception as ex:
            self._log(f"Failed to start caQtDM main panel: {ex}")

    def _update_window_title(self):
        motor = self.motor_record_edit.text().strip() if hasattr(self, "motor_record_edit") else ""
        if motor:
            self.setWindowTitle(f"{self._base_title} [{motor}]")
        else:
            self.setWindowTitle(self._base_title)
        self._update_open_controller_button_state()

    def _update_open_controller_button_state(self):
        self.open_cntrl_btn.setEnabled(True)

    def _update_cfg_pv_edits(self):
        prefix = self.prefix_edit.text().strip()
        axis_id = self._axis_id_text()
        axis_pfx_pv = _join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Pfx")
        self.axis_pfx_cfg_pv_edit.setText(axis_pfx_pv)

        # Motor name/suffix is expected in ...-Nam.
        guessed = _join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-Nam")
        if not self.motor_name_cfg_pv_edit.text().strip() or "MCU-Cfg-AX" in self.motor_name_cfg_pv_edit.text():
            self.motor_name_cfg_pv_edit.setText(guessed)

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
        if s.startswith((".", "-", ":")):
            return f"{base}{s}"
        return f"{base}{s}"

    def _put(self, field, value, quiet=False, wait=False):
        pv = self._pv(field)
        # Motion writes should not block the GUI thread; otherwise STOP/KILL clicks
        # cannot be processed while a move is active.
        self.client.put(pv, value, wait=bool(wait))
        if not quiet:
            mode = "wait" if wait else "nowait"
            self._log(f"PUT [{mode}] {pv} = {value}")

    def _get(self, field):
        pv = self._pv(field)
        val = self.client.get(pv, as_string=True)
        self._log(f"GET {pv} -> {val}")
        return val

    def _read_cfg_pv(self, pv):
        return str(self.client.get(pv, as_string=True)).strip().strip('"')

    def resolve_motor_record_name(self):
        try:
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
            if hasattr(self, "trend_pos_widget"):
                self.trend_pos_widget.clear()
            if hasattr(self, "trend_err_widget"):
                self.trend_err_widget.clear()
            self._setup_trend_monitors()
            self._log(f"Resolved motor record: {resolved} (axis_pfx='{axis_pfx}', motor='{motor_name}')")
            vals = self.refresh_status()
            self._init_shared_motion_settings_from_pv()
            self._init_positions_from_rbv(vals, force=True)
        except Exception as ex:
            self._update_window_title()
            self._log(f"Resolve failed: {ex}")

    def _candidate_motor_name_pvs(self):
        prefix = self.prefix_edit.text().strip()
        axis_id = self._axis_id_text()
        suffixes = [
            f"MCU-Cfg-AX{axis_id}-Nam",
            f"MCU-Cfg-AX{axis_id}-Mtr",
            f"MCU-Cfg-AX{axis_id}-MtrName",
            f"MCU-Cfg-AX{axis_id}-Motor",
            f"MCU-Cfg-AX{axis_id}-MotorName",
            f"MCU-Cfg-AX{axis_id}-Pfx",  # user note may use same PV; handle gracefully
        ]
        return [_join_prefix_pv(prefix, s) for s in suffixes]

    def _combine_motor_record(self, axis_pfx, motor_name):
        a = str(axis_pfx or "").strip()
        m = str(motor_name or "").strip()
        if a and m:
            # If the second part is already a full PV base, use it directly.
            if m.startswith(a) or ":" in m:
                return m
            if a.endswith(":"):
                return f"{a}{m}"
            return f"{a}:{m}"
        return a or m

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

    def _set_jog_params(self, velo, accl, accs=None, vmax=None):
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
        self._put("ACCL", accl)
        try:
            self._put("JVEL", velo)
        except Exception as ex:
            self._log(f"JVEL unavailable, using VELO ({ex})")
            self._put("VELO", velo)

    def _shared_motion_params(self):
        velo = _to_float(self.motion_velo_edit.text(), "VELO/JVEL")
        accl = _to_float(self.motion_acc_edit.text(), "ACCL")
        vmax_txt = self.motion_vmax_edit.text().strip() if hasattr(self, "motion_vmax_edit") else ""
        accs_txt = self.motion_accs_edit.text().strip() if hasattr(self, "motion_accs_edit") else ""
        vmax = _to_float(vmax_txt, "VMAX") if vmax_txt else None
        accs = _to_float(accs_txt, "ACCS") if accs_txt else None
        return velo, accl, accs, vmax

    def _init_shared_motion_settings_from_pv(self):
        if not self.motor_record_edit.text().strip():
            return
        try:
            v = self.client.get(self._pv("VELO"), as_string=True)
            self.motion_velo_edit.setText(compact_float_text(v))
        except Exception as ex:
            self._log(f"Init VELO from PV failed: {ex}")
        try:
            a = self.client.get(self._pv("ACCL"), as_string=True)
            self.motion_acc_edit.setText(compact_float_text(a))
        except Exception as ex:
            self._log(f"Init ACCL from PV failed: {ex}")
        try:
            vm = self.client.get(self._pv("VMAX"), as_string=True)
            self.motion_vmax_edit.setText(compact_float_text(vm))
        except Exception:
            if hasattr(self, "motion_vmax_edit"):
                self.motion_vmax_edit.setText("")
        # ACCS is optional on some motor records.
        try:
            s = self.client.get(self._pv("ACCS"), as_string=True)
            self.motion_accs_edit.setText(compact_float_text(s))
        except Exception:
            if hasattr(self, "motion_accs_edit"):
                self.motion_accs_edit.setText("")
        try:
            t = self.client.get(self._pv("TWV"), as_string=True)
            if hasattr(self, "tweak_step_edit"):
                self.tweak_step_edit.setText(compact_float_text(t))
        except Exception:
            pass

    def _refresh_status_if_enabled(self):
        if self.auto_refresh_status.isChecked():
            self.refresh_status()

    def refresh_status(self):
        if not self.motor_record_edit.text().strip():
            return {}
        vals = {}
        for f in list(self.status_fields.keys()):
            try:
                raw = self.client.get(self._pv(f), as_string=True)
                vals[f] = str(raw).strip()
                txt = compact_float_text(raw)
                self.status_fields[f].setText(txt)
            except Exception as ex:
                self.status_fields[f].setText(f"ERR: {ex}")
                vals[f] = None
        for name, suffix in (("ErrId", "-ErrId"), ("MsgTxt", "-MsgTxt")):
            w = self.status_extra_fields.get(name) if hasattr(self, "status_extra_fields") else None
            if w is None:
                continue
            try:
                raw = self.client.get(self._motor_suffix_pv(suffix), as_string=True)
                txt = str(raw).strip()
                vals[name] = txt
                w.setText(compact_float_text(txt) if name == "ErrId" else txt)
            except Exception as ex:
                vals[name] = None
                w.setText(f"ERR: {ex}")
        self._update_motion_indicator(vals)
        self._update_drive_enable_button_from_status(vals)
        self._update_active_mode_from_status(vals)
        self._last_status_vals = dict(vals)
        return vals

    def _errid_active(self, vals=None):
        vals = dict(vals or getattr(self, "_last_status_vals", {}) or {})
        err = vals.get("ErrId")
        if err is None:
            return False
        s = str(err).strip().strip('"')
        if not s:
            return False
        try:
            return float(s) != 0.0
        except Exception:
            # Treat any non-empty, non-zero-ish text as active error.
            return s.lower() not in {"0", "none", "ok"}

    def _set_drive_enable_button_style(self, enabled):
        if enabled is True:
            self.drive_enable_btn.setText("Enabled")
            self.drive_enable_btn.setStyleSheet(
                "QPushButton { background: #22c55e; color: #062b12; font-weight: 700; border: 1px solid #168a42; padding: 4px 8px; }"
                "QPushButton:pressed { background: #1faa52; }"
            )
        elif enabled is False:
            self.drive_enable_btn.setText("Enable")
            self.drive_enable_btn.setStyleSheet(
                "QPushButton { background: #e6e6e6; color: #222; font-weight: 700; border: 1px solid #a8a8a8; padding: 4px 8px; }"
                "QPushButton:pressed { background: #d7d7d7; }"
            )
        else:
            self.drive_enable_btn.setText("Enable")
            self.drive_enable_btn.setStyleSheet(
                "QPushButton { background: #e6e6e6; color: #222; font-weight: 700; border: 1px solid #a8a8a8; padding: 4px 8px; }"
                "QPushButton:pressed { background: #d7d7d7; }"
            )

    def _update_drive_enable_button_from_status(self, vals):
        cnen = vals.get("CNEN")
        if cnen is None:
            self._set_drive_enable_button_style(None)
            return
        s = str(cnen).strip().strip('"')
        # Prefer explicit numeric CNEN semantics: 1=enabled, 0=disabled.
        try:
            n = int(float(s))
            if n == 1:
                self._set_drive_enable_button_style(True)
                return
            if n == 0:
                self._set_drive_enable_button_style(False)
                return
        except Exception:
            pass
        self._set_drive_enable_button_style(_truthy_pv(s))

    def toggle_drive_enable(self):
        try:
            cur = None
            try:
                cur = self.client.get(self._pv("CNEN"), as_string=True)
            except Exception:
                cur = None
            next_val = 0 if _truthy_pv(cur) else 1
            self._put("CNEN", next_val)
            self._log(f"Drive {'enabled' if next_val else 'disabled'} (CNEN={next_val})")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._log(f"Drive toggle failed: {ex}")

    def reset_error(self):
        try:
            pv = f"{self._motor_base()}-ErrRst"
            self.client.put(pv, 1, wait=False)
            self._log(f"PUT [nowait] {pv} = 1")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._log(f"Reset failed: {ex}")

    def reset_all_errors(self):
        try:
            prefix = self.prefix_edit.text().strip() if hasattr(self, "prefix_edit") else ""
            prefix = prefix or self.default_prefix
            if not prefix:
                raise RuntimeError("IOC prefix is empty")
            pv = _join_prefix_pv(prefix, "MCU-ErrRst")
            self.client.put(pv, 1, wait=False)
            self._log(f"PUT [nowait] {pv} = 1")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._log(f"Reset All failed: {ex}")

    def _init_positions_from_rbv(self, vals=None, force=False):
        vals = dict(vals or {})
        rbv_raw = vals.get("RBV")
        if rbv_raw is None and self.status_fields.get("RBV") is not None:
            rbv_raw = self.status_fields["RBV"].text().strip()
        if rbv_raw is None:
            return
        try:
            rbv = float(str(rbv_raw).strip())
        except Exception:
            return
        if (not force) and self._positions_initialized:
            return
        a_txt = compact_float_text(rbv)
        b_txt = compact_float_text(rbv + 1.0)
        self.move_pos_edit.setText(a_txt)
        self.seq_a_edit.setText(a_txt)
        self.seq_b_edit.setText(b_txt)
        self._positions_initialized = True
        self._log(f"Initialized positions from RBV={a_txt} (PosA={a_txt}, PosB={b_txt})")

    def _update_motion_indicator(self, vals):
        rbv_now = vals.get("RBV")
        movn = _truthy_pv(vals.get("MOVN")) if vals.get("MOVN") is not None else False
        dmov = _truthy_pv(vals.get("DMOV")) if vals.get("DMOV") is not None else True
        rbv_changed = (rbv_now is not None and self._last_rbv_text is not None and rbv_now != self._last_rbv_text)
        moving = bool(movn or (not dmov) or rbv_changed)
        self._is_motor_moving = moving
        self._last_rbv_text = rbv_now if rbv_now is not None else self._last_rbv_text

        rbv_field = self.status_fields.get("RBV")
        if rbv_field is not None:
            if moving:
                rbv_field.setStyleSheet(
                    "QLineEdit { font-size: 14px; font-weight: 700; background: #fff1c9; border: 2px solid #f39c12; color: #111; }"
                )
            else:
                rbv_field.setStyleSheet(
                    "QLineEdit { font-size: 14px; font-weight: 700; background: #eef6ff; border: 2px solid #6f97c6; }"
                )

        if moving:
            ch = self._spinner_chars[self._spinner_index % len(self._spinner_chars)]
            self._spinner_index += 1
            self.rbv_motion_label.setText(f"{ch} MOVING")
            self.rbv_motion_label.setStyleSheet(
                "QLabel { background: #ffd89a; color: #5a3200; font-weight: 700; padding: 2px 6px; border: 1px solid #cf8d2a; }"
            )
        else:
            self.rbv_motion_label.setText("idle")
            self.rbv_motion_label.setStyleSheet(
                "QLabel { background: #d8ead2; color: #173b17; font-weight: 700; padding: 2px 6px; border: 1px solid #9fbe95; }"
            )

    def _periodic_status_tick(self):
        try:
            self.refresh_status()
            if hasattr(self, "trends_group") and self.trends_group.isVisible():
                if self._trend_use_monitor:
                    self._append_trend_from_cached_monitors()
                else:
                    self._poll_trend_signals()
        except Exception:
            pass

    def _setup_trend_monitors(self):
        self._trend_use_monitor = False
        self._trend_monitor_values = {"PosAct": None, "PosSet": None, "PosErr": None}
        # Best effort cleanup of previous PV monitor objects.
        for _name, pv in list(self._trend_monitor_pvs.items()):
            try:
                if hasattr(pv, "clear_callbacks"):
                    pv.clear_callbacks()
            except Exception:
                pass
            try:
                if hasattr(pv, "disconnect"):
                    pv.disconnect()
            except Exception:
                pass
        self._trend_monitor_pvs = {}

        if getattr(self.client, "backend", None) != "pyepics" or getattr(self.client, "_epics", None) is None:
            return
        if not self.motor_record_edit.text().strip():
            return
        ep = self.client._epics

        def _cb_factory(sig_name):
            def _cb(pvname=None, value=None, char_value=None, **_kws):
                v = value if value is not None else char_value
                self._trend_monitor_values[sig_name] = v
            return _cb

        try:
            mapping = {"PosAct": "-PosAct", "PosSet": "-PosSet", "PosErr": "-PosErr"}
            for sig_name, suffix in mapping.items():
                pvname = self._motor_suffix_pv(suffix)
                pv = ep.PV(pvname, auto_monitor=True, callback=_cb_factory(sig_name))
                self._trend_monitor_pvs[sig_name] = pv
            self._trend_use_monitor = True
            self._log("Trend graphs using pyepics monitors (UI throttled)")
        except Exception as ex:
            self._trend_use_monitor = False
            self._log(f"Trend monitor setup failed, using polling ({ex})")

    def _append_trend_from_cached_monitors(self):
        vals = dict(self._trend_monitor_values)
        if all(vals.get(k) is None for k in ("PosAct", "PosSet", "PosErr")):
            # No monitor samples yet, fallback once.
            self._poll_trend_signals()
            return
        if hasattr(self, "trend_pos_widget"):
            self.trend_pos_widget.append_point({"PosAct": vals.get("PosAct"), "PosSet": vals.get("PosSet")})
        if hasattr(self, "trend_err_widget"):
            self.trend_err_widget.append_point({"PosErr": vals.get("PosErr")})

    def _poll_trend_signals(self):
        if not self.motor_record_edit.text().strip():
            return
        vals = {}
        for key, suffix in (("PosAct", "-PosAct"), ("PosSet", "-PosSet"), ("PosErr", "-PosErr")):
            try:
                vals[key] = float(self.client.get(self._motor_suffix_pv(suffix), as_string=True))
            except Exception:
                vals[key] = None
        if hasattr(self, "trend_pos_widget"):
            self.trend_pos_widget.append_point({"PosAct": vals.get("PosAct"), "PosSet": vals.get("PosSet")})
        if hasattr(self, "trend_err_widget"):
            self.trend_err_widget.append_point({"PosErr": vals.get("PosErr")})

    def move_to_position(self):
        try:
            self._set_active_motion_mode("move")
            pos = _to_float(self.move_pos_edit.text(), "Position")
            velo, accl, accs, vmax = self._shared_motion_params()
            self._set_move_params(velo, accl, accs=accs, vmax=vmax)
            if hasattr(self, "move_relative_chk") and self.move_relative_chk.isChecked():
                self._put("RLV", pos)
            else:
                self._put("VAL", pos)
            self._show_jog_stop_dialog("Move to position")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._clear_active_motion_mode()
            self._log(f"Move failed: {ex}")

    def _tweak(self, direction):
        try:
            self._set_active_motion_mode("tweak")
            twv = _to_float(self.tweak_step_edit.text(), "TWV")
            self._put("TWV", twv)
            if direction == "F":
                self._put("TWF", 1)
                self._log(f"Tweak forward (TWV={compact_float_text(twv)})")
            else:
                self._put("TWR", 1)
                self._log(f"Tweak reverse (TWV={compact_float_text(twv)})")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._clear_active_motion_mode()
            self._log(f"Tweak failed: {ex}")

    def tweak_forward(self):
        self._tweak("F")

    def tweak_reverse(self):
        self._tweak("R")

    def start_sequence(self):
        try:
            self._set_active_motion_mode("sequence")
            a = _to_float(self.seq_a_edit.text(), "Pos A")
            b = _to_float(self.seq_b_edit.text(), "Pos B")
            velo, accl, accs, vmax = self._shared_motion_params()
            idle_s = _to_float(self.seq_idle_edit.text(), "Idle time")
            steps = int(float(self.seq_steps_edit.text().strip() or "2"))
            if idle_s < 0:
                raise ValueError("Idle time must be >= 0")
            if steps < 1:
                raise ValueError("Steps must be >= 1")
            if bool(self.seq_relative_chk.isChecked()):
                rbv = _to_float(self.client.get(self._pv("RBV"), as_string=True), "RBV")
                a = rbv + a
                b = rbv + b
            # Step-scan semantics: "steps" is the number of increments (commands)
            # between A and B, so there are steps+1 points including both ends.
            scan_points = [a + (b - a) * (float(i) / float(steps)) for i in range(steps + 1)]

            self._seq_params = {
                "a": a, "b": b, "velo": velo, "accl": accl, "accs": accs, "vmax": vmax, "idle": idle_s, "steps": steps
            }
            self._seq_scan_points = list(scan_points)
            self._seq_scan_dir = 1
            self._seq_scan_idx = 0
            self._seq_next_target = scan_points[1]
            self._seq_idle_until = None
            self._seq_active = True
            self.seq_state_label.setText("Step 1")
            self._sequence_move_to(scan_points[0])
            self._show_jog_stop_dialog("Sequence (A <-> B)")
            self._seq_timer.start()
        except Exception as ex:
            self._clear_active_motion_mode()
            self._log(f"Sequence start failed: {ex}")
            self.seq_state_label.setText("Error")

    def _sequence_move_to(self, target):
        p = self._seq_params
        self._set_move_params(p["velo"], p["accl"], accs=p.get("accs"), vmax=p.get("vmax"))
        self._put("VAL", target)
        self._log(f"Sequence target -> {compact_float_text(target)}")
        self._refresh_status_if_enabled()

    def _sequence_tick(self):
        if not self._seq_active:
            self._seq_timer.stop()
            return
        try:
            if self._errid_active():
                err_txt = ""
                try:
                    err_txt = str(self.status_extra_fields.get("ErrId").text() or "").strip()
                except Exception:
                    err_txt = ""
                self._log(f"Sequence stopped due to active error (ErrId={err_txt or '?'})")
                self.stop_sequence()
                self.seq_state_label.setText("Stopped on error")
                return
            now = time.monotonic()
            if self._seq_idle_until is not None:
                remaining = self._seq_idle_until - now
                if remaining > 0:
                    self.seq_state_label.setText(f"Idle {remaining:.1f}s")
                    return
                pts = list(self._seq_scan_points or [])
                if len(pts) < 2:
                    raise RuntimeError("Sequence points unavailable")
                if self._seq_scan_dir >= 0:
                    if self._seq_scan_idx < len(pts) - 1:
                        self._seq_scan_idx += 1
                    else:
                        self._seq_scan_dir = -1
                        self._seq_scan_idx -= 1
                else:
                    if self._seq_scan_idx > 0:
                        self._seq_scan_idx -= 1
                    else:
                        self._seq_scan_dir = 1
                        self._seq_scan_idx += 1
                target = pts[self._seq_scan_idx]
                self._seq_next_target = target
                self.seq_state_label.setText(f"Step {self._seq_scan_idx + 1}")
                self._seq_idle_until = None
                self._sequence_move_to(target)
                return

            dmov = self.client.get(self._pv("DMOV"), as_string=True)
            if _truthy_pv(dmov):
                idle_s = float(self._seq_params.get("idle", 0.0))
                self._seq_idle_until = now + idle_s
                self.seq_state_label.setText(f"Reached target; idle {idle_s:.1f}s")
                self._refresh_status_if_enabled()
            else:
                self.seq_state_label.setText("Moving...")
        except Exception as ex:
            self._log(f"Sequence error: {ex}")
            self.stop_sequence()

    def stop_sequence(self):
        self._seq_active = False
        self._seq_idle_until = None
        self._seq_scan_points = []
        self._seq_timer.stop()
        self.seq_state_label.setText("Stopped")
        self._clear_active_motion_mode()

    def start_jog_forward(self):
        self._start_jog(direction="F")

    def start_jog_backward(self):
        self._start_jog(direction="R")

    def _start_jog(self, direction):
        try:
            label = "forward" if direction == "F" else "backward"
            ans = QtWidgets.QMessageBox.question(
                self,
                "Confirm Endless Motion",
                f"Execute endless {label} motion for axis {self._axis_id_text()}?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if ans != QtWidgets.QMessageBox.Yes:
                self._log(f"Endless {label} motion cancelled")
                return
            self._set_active_motion_mode("jog")
            velo, accl, accs, vmax = self._shared_motion_params()
            self._set_jog_params(velo, accl, accs=accs, vmax=vmax)
            if direction == "F":
                try:
                    self._put("JOGR", 0, quiet=True)
                except Exception:
                    pass
                self._put("JOGF", 1)
                self._log("Endless forward motion started")
                self._show_jog_stop_dialog("Endless forward motion")
            else:
                try:
                    self._put("JOGF", 0, quiet=True)
                except Exception:
                    pass
                self._put("JOGR", 1)
                self._log("Endless backward motion started")
                self._show_jog_stop_dialog("Endless backward motion")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._clear_active_motion_mode()
            self._log(f"Jog start failed: {ex}")

    def stop_motion(self):
        # Also stop local sequence state, if active.
        self._seq_active = False
        self._seq_idle_until = None
        self._seq_timer.stop()
        self.seq_state_label.setText("Stopped")
        self._clear_active_motion_mode()
        self._close_jog_stop_dialog()
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
                    # 0=Stop in standard motor record SPMG menu
                    self._put("SPMG", 0)
                    stop_ok = True
                except Exception as ex:
                    self._log(f"SPMG stop failed ({ex})")
            if not stop_ok:
                raise RuntimeError("No supported stop field worked (tried STOP and SPMG)")
            self._log("Stop requested")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._log(f"Stop failed: {ex}")

    def kill_motion(self):
        # KILL means disable controller/drive via motor record field CNEN=0.
        self._seq_active = False
        self._seq_idle_until = None
        self._seq_timer.stop()
        self.seq_state_label.setText("Stopped")
        self._clear_active_motion_mode()
        self._close_jog_stop_dialog()
        try:
            try:
                self._put("JOGF", 0, quiet=True)
            except Exception:
                pass
            try:
                self._put("JOGR", 0, quiet=True)
            except Exception:
                pass
            try:
                self._put("STOP", 1, quiet=True)
            except Exception:
                pass
            self._put("CNEN", 0)
            self._log("KILL requested (CNEN=0)")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._log(f"KILL failed: {ex}")

    def closeEvent(self, event):
        # Safety: if any motion mode is active, request stop before closing.
        try:
            if self._seq_active or self._active_motion_mode in {"move", "jog", "sequence"} or self._is_motor_moving:
                self._log("Window closing: stop requested for active motion")
                try:
                    self.stop_motion()
                except Exception as ex:
                    self._log(f"Stop on close failed: {ex}")
        finally:
            try:
                self._close_jog_stop_dialog()
            except Exception:
                pass
            super().closeEvent(event)


def main():
    ap = argparse.ArgumentParser(description="Qt app for motor-record-based motion tests")
    ap.add_argument("--prefix", default="", help="IOC prefix (e.g. IOC:ECMC)")
    ap.add_argument("--axis-id", default="1", help="Axis ID")
    ap.add_argument("--timeout", type=float, default=2.0, help="EPICS timeout [s]")
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    w = MotionWindow(prefix=args.prefix, axis_id=args.axis_id, timeout=args.timeout)
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
