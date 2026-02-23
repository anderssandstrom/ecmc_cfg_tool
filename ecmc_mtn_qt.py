#!/usr/bin/env python3
import argparse
import sys
import time
from datetime import datetime

try:
    from PyQt5 import QtCore, QtWidgets
except Exception:
    from PySide6 import QtCore, QtWidgets  # type: ignore

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


class MotionWindow(QtWidgets.QMainWindow):
    def __init__(self, prefix, axis_id, timeout):
        super().__init__()
        self.setWindowTitle("ecmc Motor Record Motion")
        self.resize(680, 500)

        self.client = EpicsClient(timeout=timeout)
        self.default_prefix = str(prefix or "").strip()
        self.default_axis_id = str(axis_id or "1").strip() or "1"

        self._seq_active = False
        self._seq_idle_until = None
        self._seq_next_target = None
        self._seq_params = {}
        self._seq_timer = QtCore.QTimer(self)
        self._seq_timer.setInterval(250)
        self._seq_timer.timeout.connect(self._sequence_tick)
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(250)
        self._status_timer.timeout.connect(self._periodic_status_tick)
        self._spinner_chars = ["|", "/", "-", "\\"]
        self._spinner_index = 0
        self._last_rbv_text = None
        self._positions_initialized = False

        self._build_ui(timeout)
        self._log(f"Connected via backend: {self.client.backend}")
        self._status_timer.start()
        self.resolve_motor_record_name()

    def _build_ui(self, timeout):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        top_row = QtWidgets.QHBoxLayout()
        self.cfg_toggle_btn = QtWidgets.QPushButton("Show Config")
        self.cfg_toggle_btn.setAutoDefault(False)
        self.cfg_toggle_btn.setDefault(False)
        self.cfg_toggle_btn.clicked.connect(self._toggle_config_panel)
        self.log_toggle_btn = QtWidgets.QPushButton("Show Log")
        self.log_toggle_btn.setAutoDefault(False)
        self.log_toggle_btn.setDefault(False)
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)
        self.axis_top_edit = QtWidgets.QLineEdit(self.default_axis_id)
        self.axis_top_edit.setMaximumWidth(80)
        self.axis_top_edit.editingFinished.connect(self._apply_axis_top)
        self.axis_top_btn = QtWidgets.QPushButton("Apply Axis")
        self.axis_top_btn.setAutoDefault(False)
        self.axis_top_btn.setDefault(False)
        self.axis_top_btn.clicked.connect(self._apply_axis_top)
        top_row.addWidget(self.cfg_toggle_btn)
        top_row.addWidget(self.log_toggle_btn)
        top_row.addWidget(QtWidgets.QLabel("Axis"))
        top_row.addWidget(self.axis_top_edit)
        top_row.addWidget(self.axis_top_btn)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.cfg_group = QtWidgets.QGroupBox("Axis / Motor Record")
        cfg = QtWidgets.QGridLayout(self.cfg_group)

        self.prefix_edit = QtWidgets.QLineEdit(self.default_prefix)
        self.axis_edit = QtWidgets.QLineEdit(self.default_axis_id)
        self.axis_edit.setMaximumWidth(90)
        self.axis_edit.editingFinished.connect(lambda: self.axis_top_edit.setText(self.axis_edit.text()))
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

        cfg.addWidget(QtWidgets.QLabel("IOC Prefix"), 0, 0)
        cfg.addWidget(self.prefix_edit, 0, 1)
        cfg.addWidget(QtWidgets.QLabel("Axis ID"), 0, 2)
        cfg.addWidget(self.axis_edit, 0, 3)
        cfg.addWidget(QtWidgets.QLabel("Timeout [s]"), 0, 4)
        cfg.addWidget(self.timeout_edit, 0, 5)

        cfg.addWidget(QtWidgets.QLabel("Axis Prefix PV"), 1, 0)
        cfg.addWidget(self.axis_pfx_cfg_pv_edit, 1, 1, 1, 3)
        cfg.addWidget(QtWidgets.QLabel("Motor Name PV"), 1, 4)
        cfg.addWidget(self.motor_name_cfg_pv_edit, 1, 5)

        cfg.addWidget(QtWidgets.QLabel("Motor Record"), 2, 0)
        cfg.addWidget(self.motor_record_edit, 2, 1, 1, 4)
        cfg.addWidget(resolve_btn, 2, 5)

        cfg.addWidget(self.auto_refresh_status, 3, 0, 1, 3)
        cfg.addWidget(refresh_btn, 3, 5)
        layout.addWidget(self.cfg_group)

        self._build_motion_settings_group(layout)
        self._build_move_group(layout)
        self._build_sequence_group(layout)
        self._build_jog_group(layout)
        self._build_status_group(layout)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=1)
        self.cfg_group.setVisible(False)
        self.log.setVisible(False)

    def _toggle_config_panel(self):
        visible = not self.cfg_group.isVisible()
        self.cfg_group.setVisible(visible)
        self.cfg_toggle_btn.setText("Hide Config" if visible else "Show Config")

    def _toggle_log_panel(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_toggle_btn.setText("Hide Log" if visible else "Show Log")

    def _apply_axis_top(self):
        axis_txt = self.axis_top_edit.text().strip() or self.default_axis_id
        self.axis_top_edit.setText(axis_txt)
        self.axis_edit.setText(axis_txt)
        self._update_cfg_pv_edits()
        self._positions_initialized = False
        self.resolve_motor_record_name()

    def _build_motion_settings_group(self, parent_layout):
        g = QtWidgets.QGroupBox("Shared Motion Settings")
        l = QtWidgets.QGridLayout(g)
        self.motion_velo_edit = QtWidgets.QLineEdit("1")
        self.motion_acc_edit = QtWidgets.QLineEdit("1")
        self.motion_accs_edit = QtWidgets.QLineEdit("")
        self.motion_accs_edit.setPlaceholderText("optional")
        self.drive_enable_btn = QtWidgets.QPushButton("Drive: ?")
        stop_btn = QtWidgets.QPushButton("STOP")
        kill_btn = QtWidgets.QPushButton("KILL (CNEN=0)")
        for b in (self.drive_enable_btn, stop_btn, kill_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
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
        l.addWidget(QtWidgets.QLabel("ACCS"), 0, 4)
        l.addWidget(self.motion_accs_edit, 0, 5)
        l.addWidget(self.drive_enable_btn, 0, 6)
        l.addWidget(stop_btn, 0, 7)
        l.addWidget(kill_btn, 0, 8)
        parent_layout.addWidget(g)

    def _build_move_group(self, parent_layout):
        g = QtWidgets.QGroupBox("1. Move To Position")
        l = QtWidgets.QGridLayout(g)

        self.move_pos_edit = QtWidgets.QLineEdit("0")
        self.move_relative_chk = QtWidgets.QCheckBox("Relative")

        move_btn = QtWidgets.QPushButton("Move")
        for b in (move_btn,):
            b.setAutoDefault(False)
            b.setDefault(False)
        move_btn.clicked.connect(self.move_to_position)

        l.addWidget(QtWidgets.QLabel("Position"), 0, 0)
        l.addWidget(self.move_pos_edit, 0, 1)
        l.addWidget(self.move_relative_chk, 0, 2)
        l.addWidget(move_btn, 0, 3)

        parent_layout.addWidget(g)

    def _build_sequence_group(self, parent_layout):
        g = QtWidgets.QGroupBox("2. Sequence (A <-> B)")
        l = QtWidgets.QGridLayout(g)

        self.seq_a_edit = QtWidgets.QLineEdit("0")
        self.seq_b_edit = QtWidgets.QLineEdit("10")
        self.seq_idle_edit = QtWidgets.QLineEdit("0.5")
        self.seq_state_label = QtWidgets.QLabel("Stopped")

        start_btn = QtWidgets.QPushButton("Start Sequence")
        for b in (start_btn,):
            b.setAutoDefault(False)
            b.setDefault(False)
        start_btn.clicked.connect(self.start_sequence)

        l.addWidget(QtWidgets.QLabel("Pos A"), 0, 0)
        l.addWidget(self.seq_a_edit, 0, 1)
        l.addWidget(QtWidgets.QLabel("Pos B"), 0, 2)
        l.addWidget(self.seq_b_edit, 0, 3)

        l.addWidget(QtWidgets.QLabel("Idle [s]"), 0, 4)
        l.addWidget(self.seq_idle_edit, 0, 5)
        l.addWidget(start_btn, 0, 6)
        l.addWidget(QtWidgets.QLabel("State"), 1, 0)
        l.addWidget(self.seq_state_label, 1, 1, 1, 3)

        parent_layout.addWidget(g)

    def _build_jog_group(self, parent_layout):
        g = QtWidgets.QGroupBox("3/4. Endless Motion (Jog)")
        l = QtWidgets.QGridLayout(g)

        fwd_btn = QtWidgets.QPushButton("Endless Forward")
        bwd_btn = QtWidgets.QPushButton("Endless Backward")
        for b in (fwd_btn, bwd_btn):
            b.setAutoDefault(False)
            b.setDefault(False)
        fwd_btn.clicked.connect(self.start_jog_forward)
        bwd_btn.clicked.connect(self.start_jog_backward)

        l.addWidget(fwd_btn, 0, 0)
        l.addWidget(bwd_btn, 0, 1)

        parent_layout.addWidget(g)

    def _build_status_group(self, parent_layout):
        g = QtWidgets.QGroupBox("Motor Record Status")
        l = QtWidgets.QGridLayout(g)
        self.status_fields = {}
        self.rbv_motion_label = QtWidgets.QLabel("idle")
        self.rbv_motion_label.setMinimumWidth(90)
        self.rbv_motion_label.setStyleSheet(
            "QLabel { background: #d8ead2; color: #173b17; font-weight: 700; padding: 2px 6px; border: 1px solid #9fbe95; }"
        )
        names = [("VAL", 0, 0), ("RBV", 0, 2), ("DMOV", 0, 4), ("MOVN", 0, 6), ("VELO", 1, 0), ("ACCL", 1, 2), ("ACCS", 1, 4), ("CNEN", 1, 6)]
        for name, r, c in names:
            l.addWidget(QtWidgets.QLabel(name), r, c)
            e = QtWidgets.QLineEdit("")
            e.setReadOnly(True)
            if name == "RBV":
                e.setMinimumWidth(120)
                e.setStyleSheet(
                    "QLineEdit { font-size: 16px; font-weight: 700; background: #eef6ff; border: 2px solid #6f97c6; }"
                )
            l.addWidget(e, r, c + 1)
            self.status_fields[name] = e
        l.addWidget(QtWidgets.QLabel("Motion"), 2, 0)
        l.addWidget(self.rbv_motion_label, 2, 1)
        parent_layout.addWidget(g)

    def _set_timeout(self, value):
        self.client.timeout = float(value)

    def _log(self, msg):
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _axis_id_text(self):
        return self.axis_edit.text().strip() or self.default_axis_id

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
            self._log(f"Resolved motor record: {resolved} (axis_pfx='{axis_pfx}', motor='{motor_name}')")
            vals = self.refresh_status()
            self._init_positions_from_rbv(vals, force=True)
        except Exception as ex:
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

    def _set_move_params(self, velo, accl, accs=None):
        if accs is not None:
            try:
                self._put("ACCS", accs)
            except Exception as ex:
                self._log(f"ACCS unavailable ({ex})")
        self._put("VELO", velo)
        self._put("ACCL", accl)

    def _set_jog_params(self, velo, accl, accs=None):
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
        accs_txt = self.motion_accs_edit.text().strip() if hasattr(self, "motion_accs_edit") else ""
        accs = _to_float(accs_txt, "ACCS") if accs_txt else None
        return velo, accl, accs

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
        self._update_motion_indicator(vals)
        self._update_drive_enable_button_from_status(vals)
        return vals

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
        self._last_rbv_text = rbv_now if rbv_now is not None else self._last_rbv_text

        rbv_field = self.status_fields.get("RBV")
        if rbv_field is not None:
            if moving:
                rbv_field.setStyleSheet(
                    "QLineEdit { font-size: 16px; font-weight: 700; background: #fff1c9; border: 2px solid #f39c12; color: #111; }"
                )
            else:
                rbv_field.setStyleSheet(
                    "QLineEdit { font-size: 16px; font-weight: 700; background: #eef6ff; border: 2px solid #6f97c6; }"
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
        except Exception:
            pass

    def move_to_position(self):
        try:
            pos = _to_float(self.move_pos_edit.text(), "Position")
            velo, accl, accs = self._shared_motion_params()
            self._set_move_params(velo, accl, accs=accs)
            if hasattr(self, "move_relative_chk") and self.move_relative_chk.isChecked():
                self._put("RLV", pos)
            else:
                self._put("VAL", pos)
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._log(f"Move failed: {ex}")

    def start_sequence(self):
        try:
            a = _to_float(self.seq_a_edit.text(), "Pos A")
            b = _to_float(self.seq_b_edit.text(), "Pos B")
            velo, accl, accs = self._shared_motion_params()
            idle_s = _to_float(self.seq_idle_edit.text(), "Idle time")
            if idle_s < 0:
                raise ValueError("Idle time must be >= 0")

            self._seq_params = {"a": a, "b": b, "velo": velo, "accl": accl, "accs": accs, "idle": idle_s}
            self._seq_next_target = b
            self._seq_idle_until = None
            self._seq_active = True
            self.seq_state_label.setText("Moving to A")
            self._sequence_move_to(a)
            self._seq_timer.start()
        except Exception as ex:
            self._log(f"Sequence start failed: {ex}")
            self.seq_state_label.setText("Error")

    def _sequence_move_to(self, target):
        p = self._seq_params
        self._set_move_params(p["velo"], p["accl"], accs=p.get("accs"))
        self._put("VAL", target)
        self._log(f"Sequence target -> {compact_float_text(target)}")
        self._refresh_status_if_enabled()

    def _sequence_tick(self):
        if not self._seq_active:
            self._seq_timer.stop()
            return
        try:
            now = time.monotonic()
            if self._seq_idle_until is not None:
                remaining = self._seq_idle_until - now
                if remaining > 0:
                    self.seq_state_label.setText(f"Idle {remaining:.1f}s")
                    return
                target = self._seq_next_target
                self._seq_next_target = self._seq_params["a"] if target == self._seq_params["b"] else self._seq_params["b"]
                self.seq_state_label.setText(f"Moving to {compact_float_text(target)}")
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
        self._seq_timer.stop()
        self.seq_state_label.setText("Stopped")

    def start_jog_forward(self):
        self._start_jog(direction="F")

    def start_jog_backward(self):
        self._start_jog(direction="R")

    def _start_jog(self, direction):
        try:
            velo, accl, accs = self._shared_motion_params()
            self._set_jog_params(velo, accl, accs=accs)
            if direction == "F":
                try:
                    self._put("JOGR", 0, quiet=True)
                except Exception:
                    pass
                self._put("JOGF", 1)
                self._log("Endless forward motion started")
            else:
                try:
                    self._put("JOGF", 0, quiet=True)
                except Exception:
                    pass
                self._put("JOGR", 1)
                self._log("Endless backward motion started")
            self._refresh_status_if_enabled()
        except Exception as ex:
            self._log(f"Jog start failed: {ex}")

    def stop_motion(self):
        # Also stop local sequence state, if active.
        self._seq_active = False
        self._seq_idle_until = None
        self._seq_timer.stop()
        self.seq_state_label.setText("Stopped")
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
