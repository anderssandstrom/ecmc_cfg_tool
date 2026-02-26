#!/usr/bin/env python3
import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from PyQt5 import QtCore, QtWidgets
except Exception:
    from PySide6 import QtCore, QtWidgets  # type: ignore

from ecmc_stream_qt import (
    CompactDoubleSpinBox,
    EpicsClient,
    _join_prefix_pv,
    _proc_pv_for_readback,
    compact_float_text,
    normalize_float_literals,
)


PLACEHOLDER_RE = re.compile(r"<([^>]+)>")


class YNode:
    def __init__(self, key, path, value="", comment="", children=None):
        self.key = key
        self.path = path
        self.value = value
        self.comment = comment
        self.children = [] if children is None else children


def _split_yaml_comment(line):
    out = []
    in_s = False
    in_d = False
    comment = ""
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            comment = line[i + 1 :].strip()
            break
        out.append(ch)
    return "".join(out).rstrip(), comment


def _strip_yaml_comment(line):
    code, _comment = _split_yaml_comment(line)
    return code


def parse_simple_yaml_tree(path):
    root = YNode("(root)", "")
    stack = [(-1, root)]
    list_counters = {}
    for raw in Path(path).read_text().splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue
        line, comment = _split_yaml_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        text = line.lstrip(" ")
        # Only use inline comments on the actual key/value line for tooltips.
        merged_comment = str(comment or "").strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]

        if text.startswith("- "):
            value = text[2:].strip()
            idx = list_counters.get(id(parent), 0)
            list_counters[id(parent)] = idx + 1
            key = f"[{idx}]"
            path_key = f"{parent.path}.{key}" if parent.path else key
            node = YNode(key=key, path=path_key, value=value, comment=merged_comment)
            parent.children.append(node)
            continue

        if ":" not in text:
            continue
        key, rest = text.split(":", 1)
        key = key.strip()
        value = rest.strip()
        path_key = f"{parent.path}.{key}" if parent.path else key
        node = YNode(key=key, path=path_key, value=value, comment=merged_comment)
        parent.children.append(node)
        if value == "":
            stack.append((indent, node))
    return root


def is_block_marked(value):
    return "block" in str(value or "").strip().lower()


def _template_placeholders(tmpl):
    return PLACEHOLDER_RE.findall(str(tmpl or ""))


def _strip_cmd_kind(tmpl):
    s = str(tmpl or "").strip()
    if s.startswith("Cfg."):
        s = s[4:]
    head = s.split("(", 1)[0]
    if head.startswith("Set"):
        return "set", head[3:]
    if head.startswith("Get"):
        return "get", head[3:]
    return "other", head


def _derive_get_from_set(set_tmpl):
    m = re.match(r"^(Cfg\.)Set([A-Za-z0-9_]+)\((.*)\)$", str(set_tmpl or "").strip())
    if not m:
        return ""
    prefix, base, args = m.groups()
    args = [a.strip() for a in args.split(",") if a.strip()]
    axis_arg = args[0] if args else "<axisIndex>"
    return f"{prefix}Get{base}({axis_arg})"


def build_axis_command_pairs(catalog):
    pairs = {}
    for c in catalog.get("commands", []):
        tmpl = c.get("command_named", c.get("command", ""))
        kind, base = _strip_cmd_kind(tmpl)
        if kind not in {"set", "get"}:
            continue
        if not base.startswith("Axis"):
            continue
        p = pairs.setdefault(base, {"name": base, "set": "", "get": ""})
        p[kind] = tmpl
    # keep simple axis commands:
    # set = axis + single value, get = axis-only
    out = {}
    for base, p in pairs.items():
        set_ph = _template_placeholders(p["set"])
        get_ph = _template_placeholders(p["get"])
        set_ok = bool(p["set"]) and len(set_ph) == 2
        get_ok = bool(p["get"]) and len(get_ph) == 1
        if set_ok or get_ok:
            out[base] = p
    return out


EXPLICIT_PATH_TO_BASE = {
    "axis.autoEnable.enableTimeout": "AxisAutoEnableTimeout",
    "axis.autoEnable.disableTimeout": "AxisAutoDisableAfterTime",
    "drive.numerator": "AxisDrvScaleNum",
    "drive.denominator": "AxisDrvScaleDenom",
    "encoder.numerator": "AxisEncScaleNum",
    "encoder.denominator": "AxisEncScaleDenom",
    "controller.Kp": "AxisCntrlKp",
    "controller.Ki": "AxisCntrlKi",
    "controller.Kd": "AxisCntrlKd",
    "controller.Kff": "AxisCntrlKff",
    "controller.deadband.tol": "AxisCntrlDeadband",
    "controller.deadband.time": "AxisCntrlDeadbandTime",
    "controller.limits.minOutput": "AxisCntrlOutLL",
    "controller.limits.maxOutput": "AxisCntrlOutHL",
    "controller.limits.minIntegral": "AxisCntrlIPartLL",
    "controller.limits.maxIntegral": "AxisCntrlIPartHL",
    "controller.inner.Kp": "AxisCntrlInnerKp",
    "controller.inner.Ki": "AxisCntrlInnerKi",
    "controller.inner.Kd": "AxisCntrlInnerKd",
    "controller.inner.tol": "AxisCntrlInnerTol",
    "trajectory.axis.velocity": "AxisVel",
    "trajectory.axis.acceleration": "AxisAcc",
    "trajectory.axis.deceleration": "AxisDec",
    "trajectory.axis.emergencyDeceleration": "AxisEmergDeceleration",
    "trajectory.axis.jerk": "AxisJerk",
    "trajectory.jog.velocity": "AxisJogVel",
    "trajectory.source": "AxisTrajSourceType",
    "trajectory.modulo.range": "AxisModRange",
    "trajectory.modulo.type": "AxisModType",
    "softlimits.forward": "AxisSoftLimitPosFwd",
    "softlimits.backward": "AxisSoftLimitPosBwd",
    "softlimits.forwardEnable": "AxisEnableSoftLimitFwd",
    "softlimits.backwardEnable": "AxisEnableSoftLimitBwd",
    "monitoring.target.enable": "AxisMonEnableAtTargetMon",
    "monitoring.target.tolerance": "AxisMonAtTargetTol",
    "monitoring.target.time": "AxisMonAtTargetTime",
    "monitoring.lag.enable": "AxisMonEnableLagMon",
    "monitoring.lag.tolerance": "AxisMonPosLagTol",
    "monitoring.lag.time": "AxisMonPosLagTime",
    "monitoring.velocity.enable": "AxisMonEnableMaxVel",
    "monitoring.velocity.max": "AxisMonMaxVel",
    "monitoring.velocity.time.trajectory": "AxisMonMaxVelTrajILDelay",
    "monitoring.velocity.time.drive": "AxisMonMaxVelDriveILDelay",
    "monitoring.velocityDifference.enable": "AxisMonEnableVelocityDiff",
    "monitoring.velocityDifference.max": "AxisMonVelDiffTol",
    "monitoring.velocityDifference.time.trajectory": "AxisMonVelDiffTrajILDelay",
    "monitoring.velocityDifference.time.drive": "AxisMonVelDiffDriveILDelay",
    "monitoring.stall.enable": "AxisMonEnableStallMon",
    "monitoring.stall.time.timeout": "AxisMonStallMinTimeOut",
    "monitoring.stall.time.factor": "AxisMonStallTimeFactor",
    "monitoring.limits.stopAtBoth": "AxisMonStopAtAnyLimit",
    "plc.enable": "AxisPLCEnable",
    "plc.externalCommands": "AxisAllowCommandsFromPLC",
    "plc.velocity_filter.encoder.enable": "AxisPLCEncVelFilterEnable",
    "plc.velocity_filter.encoder.size": "AxisPLCEncVelFilterSize",
    "plc.velocity_filter.trajectory.enable": "AxisPLCTrajVelFilterEnable",
    "plc.velocity_filter.trajectory.size": "AxisPLCTrajVelFilterSize",
    "encoder.filter.velocity.enable": "AxisEncVelFilterEnable",
    "encoder.filter.velocity.size": "AxisEncVelFilterSize",
    "encoder.filter.position.enable": "AxisEncPosFilterEnable",
    "encoder.filter.position.size": "AxisEncPosFilterSize",
    "encoder.type": "AxisEncType",
    "encoder.bits": "AxisEncBits",
    "encoder.absBits": "AxisEncAbsBits",
    "encoder.absOffset": "AxisEncOffset",
    "encoder.source": "AxisEncSourceType",
    "encoder.lookuptable.enable": "AxisEncLookupTableEnable",
    "encoder.lookuptable.range": "AxisEncLookupTableRange",
    "encoder.lookuptable.scale": "AxisEncLookupTableScale",
    "encoder.homing.type": "AxisHomeSeqId",
    "encoder.homing.position": "AxisHomePosition",
    "encoder.homing.velocity.to": "AxisHomeVelTowardsCam",
    "encoder.homing.velocity.from": "AxisHomeVelOffCam",
    "encoder.homing.acceleration": "AxisHomeAcc",
    "encoder.homing.deceleration": "AxisHomeDec",
    "encoder.homing.postMoveEnable": "AxisHomePostMoveEnable",
    "encoder.homing.postMovePosition": "AxisHomePostMoveTargetPosition",
    "tweakDist": "AxisTweakDist",
    "input.homePolarity": "AxisMonHomeSwitchPolarity",
    "input.limit.forwardPolarity": "AxisMonLimitFwdPolarity",
    "input.limit.backwardPolarity": "AxisMonLimitBwdPolarity",
    "input.interlockPolarity": "AxisMonExtHWInterlockPolarity",
    "input.analog.interlockPolarity": "AxisMonAnalogInterlockPolarity",
    "input.analog.rawLimit": "AxisMonAnalogInterlockRawLimit",
    "input.analog.enable": "AxisMonEnableAnalogInterlock",
    "axis.features.blockCom": "AxisBlockCom",
    "axis.features.allowSrcChangeWhenEnabled": "AxisAllowSourceChangeWhenEnabled",
}

EXPLICIT_UNMATCHED_PATHS = {
    "axis.features.allowedFunctions.homing",
    "axis.features.allowedFunctions.constantVelocity",
    "axis.features.allowedFunctions.positioning",
}


def guess_axis_command_base(path_str, pairs):
    if path_str in EXPLICIT_UNMATCHED_PATHS:
        return None
    if path_str in EXPLICIT_PATH_TO_BASE and EXPLICIT_PATH_TO_BASE[path_str] in pairs:
        return EXPLICIT_PATH_TO_BASE[path_str]
    leaf = path_str.split(".")[-1]
    # Small heuristic fallback for common names.
    candidates = []
    low_path = path_str.lower()
    for base in pairs.keys():
        low = base.lower()
        score = 0
        if leaf.lower() in low:
            score += 1
        for part in path_str.split("."):
            if part and part.lower() in low:
                score += 1
        if score:
            candidates.append((score, len(low), base))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def scalar_text(v):
    return str(v or "").strip()


def fill_axis_command(template, axis_id, value):
    vals = [str(axis_id).strip(), str(value).strip()]
    out = str(template or "")
    for ph in _template_placeholders(out):
        if not vals:
            break
        out = out.replace(f"<{ph}>", vals.pop(0), 1)
    return normalize_float_literals(out)


class AxisYamlConfigWindow(QtWidgets.QMainWindow):
    def __init__(self, catalog_path, yaml_path, mapping_path, default_cmd_pv, default_qry_pv, timeout, axis_id="1", title_prefix=""):
        super().__init__()
        self._base_title = f"ecmc Axis Configurator [{title_prefix}]" if title_prefix else "ecmc Axis Configurator"
        self.setWindowTitle(self._base_title)
        _f = self.font()
        if _f.pointSize() > 0:
            _f.setPointSize(max(8, _f.pointSize() - 1))
            self.setFont(_f)
        self.resize(620, 340)
        self.client = EpicsClient(timeout=timeout)
        self.catalog = self._load_catalog(catalog_path)
        self.catalog_desc_by_named = self._build_catalog_description_index(self.catalog)
        self.command_pairs = build_axis_command_pairs(self.catalog)
        self.yaml_path = Path(yaml_path)
        self.mapping_path = Path(mapping_path) if mapping_path else Path(yaml_path).with_suffix(".command_map.csv")
        self.yaml_cmd_map = {}
        self.axis_id_default = str(axis_id).strip() or "1"
        self.title_prefix = str(title_prefix or "").strip()
        self._leaf_rows = []
        self._changes_by_axis = {}
        self._current_values_by_axis = {}
        self._original_values_by_axis = {}
        self._axis_is_real_cache = {}
        self._did_initial_read_copy = False
        self._did_startup_axis_presence_check = False
        self._startup_axis_probe_ok = False
        self._axis_combo_updating = False
        self._axis_combo_open_new_instance = False
        self._build_ui(default_cmd_pv, default_qry_pv, timeout)
        self._load_yaml_tree()
        self._log(f"Connected via backend: {self.client.backend}")
        QtCore.QTimer.singleShot(0, self._startup_axis_presence_check)

    def _load_catalog(self, path):
        p = Path(path)
        if not p.exists():
            return {"commands": []}
        try:
            return json.loads(p.read_text())
        except Exception:
            return {"commands": []}

    def _build_catalog_description_index(self, catalog):
        out = {}
        for c in catalog.get("commands", []):
            named = str(c.get("command_named", "") or c.get("command", "")).strip()
            if not named:
                continue
            desc = str(c.get("description", "") or "").strip()
            if desc:
                out[named] = " ".join(desc.split())
        return out

    def _build_ui(self, default_cmd_pv, default_qry_pv, timeout):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        top_row = QtWidgets.QHBoxLayout()
        self.cfg_toggle_btn = QtWidgets.QPushButton("Show Config")
        self.cfg_toggle_btn.setAutoDefault(False)
        self.cfg_toggle_btn.setDefault(False)
        self.cfg_toggle_btn.clicked.connect(self._toggle_config_panel)
        self.log_toggle_btn = QtWidgets.QPushButton("Show Log")
        self.log_toggle_btn.setAutoDefault(False)
        self.log_toggle_btn.setDefault(False)
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)
        self.changes_toggle_btn = QtWidgets.QPushButton("Show Changes")
        self.changes_toggle_btn.setAutoDefault(False)
        self.changes_toggle_btn.setDefault(False)
        self.changes_toggle_btn.clicked.connect(self._toggle_changes_panel)
        self.changed_yaml_btn = QtWidgets.QPushButton("Show Changed YAML")
        self.changed_yaml_btn.setAutoDefault(False)
        self.changed_yaml_btn.setDefault(False)
        self.changed_yaml_btn.clicked.connect(self._show_changed_yaml_window)
        self.open_cntrl_btn = QtWidgets.QPushButton("Cntrl Cfg App")
        self.open_cntrl_btn.setAutoDefault(False)
        self.open_cntrl_btn.setDefault(False)
        self.open_cntrl_btn.clicked.connect(self._open_controller_window)
        self.open_mtn_btn = QtWidgets.QPushButton("Motion App")
        self.open_mtn_btn.setAutoDefault(False)
        self.open_mtn_btn.setDefault(False)
        self.open_mtn_btn.clicked.connect(self._open_motion_window)
        self.axis_pick_combo = QtWidgets.QComboBox()
        self.axis_pick_combo.setMinimumWidth(170)
        self.axis_pick_combo.setMaximumWidth(300)
        self.axis_pick_combo.activated.connect(self._on_axis_combo_activated)
        top_row.addWidget(self.cfg_toggle_btn)
        top_row.addWidget(self.changed_yaml_btn)
        top_row.addWidget(self.open_cntrl_btn)
        top_row.addWidget(self.open_mtn_btn)
        top_row.addStretch(1)
        top_row.addWidget(QtWidgets.QLabel("Axis"))
        top_row.addWidget(self.axis_pick_combo)
        layout.addLayout(top_row)

        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter keys...")
        self.search.textChanged.connect(self._apply_tree_filter)
        self.caqtdm_axis_btn = QtWidgets.QPushButton("caqtdm Axis")
        self.caqtdm_axis_btn.setAutoDefault(False)
        self.caqtdm_axis_btn.setDefault(False)
        self.caqtdm_axis_btn.clicked.connect(self._open_caqtdm_axis_panel)
        search_row.addWidget(self.search, 1)
        layout.addLayout(search_row)

        self.cfg_group = QtWidgets.QGroupBox("Configuration")
        cfg = QtWidgets.QGridLayout(self.cfg_group)
        self.cmd_pv = QtWidgets.QLineEdit(default_cmd_pv)
        self.cmd_pv.editingFinished.connect(self._update_window_title_with_motor)
        self.qry_pv = QtWidgets.QLineEdit(default_qry_pv)
        self.axis_edit = QtWidgets.QLineEdit(self.axis_id_default)
        self.axis_edit.setMaximumWidth(80)
        self.axis_edit.editingFinished.connect(self._update_window_title_with_motor)
        axis_apply_btn = QtWidgets.QPushButton("Apply Axis")
        axis_apply_btn.setAutoDefault(False)
        axis_apply_btn.setDefault(False)
        axis_apply_btn.clicked.connect(lambda: self._read_and_copy_current_axis(reason="apply axis"))
        self.timeout_edit = CompactDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(timeout)
        self.timeout_edit.valueChanged.connect(lambda v: setattr(self.client, "timeout", float(v)))
        self.yaml_edit = QtWidgets.QLineEdit(str(self.yaml_path))
        self.yaml_edit.editingFinished.connect(self._reload_yaml_from_edit)
        reload_btn = QtWidgets.QPushButton("Reload YAML")
        reload_btn.setAutoDefault(False)
        reload_btn.setDefault(False)
        reload_btn.clicked.connect(self._reload_yaml_from_edit)
        self.caqtdm_main_btn = QtWidgets.QPushButton("caqtdm Main")
        self.caqtdm_main_btn.setAutoDefault(False)
        self.caqtdm_main_btn.setDefault(False)
        self.caqtdm_main_btn.clicked.connect(self._open_caqtdm_main_panel)

        cfg.addWidget(QtWidgets.QLabel("Command PV"), 0, 0)
        cfg.addWidget(self.cmd_pv, 0, 1)
        cfg.addWidget(QtWidgets.QLabel("Query PV"), 1, 0)
        cfg.addWidget(self.qry_pv, 1, 1)
        cfg.addWidget(QtWidgets.QLabel("Axis ID"), 0, 2)
        cfg.addWidget(self.axis_edit, 0, 3)
        cfg.addWidget(axis_apply_btn, 0, 4)
        cfg.addWidget(QtWidgets.QLabel("Timeout [s]"), 1, 2)
        cfg.addWidget(self.timeout_edit, 1, 3)
        cfg.addWidget(QtWidgets.QLabel("YAML Template"), 2, 0)
        cfg.addWidget(self.yaml_edit, 2, 1, 1, 3)
        cfg.addWidget(reload_btn, 2, 4)
        cfg.addWidget(self.log_toggle_btn, 3, 0)
        cfg.addWidget(self.changes_toggle_btn, 3, 1)
        cfg.addWidget(self.caqtdm_main_btn, 3, 2)
        layout.addWidget(self.cfg_group)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(["Field", "Set Value", "W", "", "Readback", "R", "Command", "Status"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(7, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.tree, stretch=1)

        action_row = QtWidgets.QHBoxLayout()
        self.read_all_btn = QtWidgets.QPushButton("Read All")
        self.read_all_btn.setAutoDefault(False)
        self.read_all_btn.setDefault(False)
        self.read_all_btn.clicked.connect(self._read_all_matched)
        self.write_all_btn = QtWidgets.QPushButton("Write Filled")
        self.write_all_btn.setAutoDefault(False)
        self.write_all_btn.setDefault(False)
        self.write_all_btn.clicked.connect(self._write_filled_matched)
        self.copy_btn = QtWidgets.QPushButton("Copy Read->Set")
        self.copy_btn.setAutoDefault(False)
        self.copy_btn.setDefault(False)
        self.copy_btn.clicked.connect(self._copy_read_to_set)
        action_row.addWidget(self.read_all_btn)
        action_row.addWidget(self.write_all_btn)
        action_row.addWidget(self.copy_btn)
        action_row.addStretch(1)
        search_row.addLayout(action_row)
        search_row.addWidget(self.caqtdm_axis_btn)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=0)
        self.changes_log = QtWidgets.QPlainTextEdit()
        self.changes_log.setReadOnly(True)
        self.changes_log.setPlaceholderText("Successful writes are tracked here for this session...")
        layout.addWidget(self.changes_log, stretch=0)
        self.cfg_group.setVisible(False)
        self.log.setVisible(False)
        self.changes_log.setVisible(False)
        self._refresh_axis_pick_combo()

    def _toggle_config_panel(self):
        visible = not self.cfg_group.isVisible()
        self.cfg_group.setVisible(visible)
        self.cfg_toggle_btn.setText("Hide Config" if visible else "Show Config")

    def _toggle_log_panel(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_toggle_btn.setText("Hide Log" if visible else "Show Log")

    def _toggle_changes_panel(self):
        visible = not self.changes_log.isVisible()
        self.changes_log.setVisible(visible)
        self.changes_toggle_btn.setText("Hide Changes" if visible else "Show Changes")

    def _open_controller_window(self):
        script = Path(__file__).with_name("start_cntrl.sh")
        if not script.exists():
            self._log(f"Launcher not found: {script.name}")
            return
        axis_id = self._axis_id()
        prefix = self.title_prefix or ""
        if not prefix:
            cmd_pv = self.cmd_pv.text().strip()
            m = re.match(r"^(.*):MCU-Cmd\\.AOUT$", cmd_pv)
            prefix = m.group(1) if m else "IOC:ECMC"
        try:
            subprocess.Popen(
                ["bash", str(script), str(prefix), str(axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started controller window for axis {axis_id} (prefix {prefix})")
        except Exception as ex:
            self._log(f"Failed to start controller window: {ex}")

    def _open_motion_window(self):
        script = Path(__file__).with_name("start_mtn.sh")
        if not script.exists():
            self._log(f"Launcher not found: {script.name}")
            return
        axis_id = self._axis_id()
        prefix = self.title_prefix or ""
        if not prefix:
            cmd_pv = self.cmd_pv.text().strip()
            m = re.match(r"^(.*):MCU-Cmd\\.AOUT$", cmd_pv)
            prefix = m.group(1) if m else "IOC:ECMC"
        try:
            subprocess.Popen(
                ["bash", str(script), str(prefix), str(axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started motion window for axis {axis_id} (prefix {prefix})")
        except Exception as ex:
            self._log(f"Failed to start motion window: {ex}")

    def _apply_tree_filter(self):
        needle = self.search.text().strip().lower() if hasattr(self, "search") else ""
        root = self.tree.invisibleRootItem()

        def visit(item):
            item_path = str(item.data(0, QtCore.Qt.UserRole) or "").lower()
            item_key = item.text(0).lower()
            self_match = (not needle) or (needle in item_key) or (needle in item_path)
            child_visible = False
            for i in range(item.childCount()):
                if visit(item.child(i)):
                    child_visible = True
            visible = self_match or child_visible
            item.setHidden(not visible)
            return visible

        for i in range(root.childCount()):
            visit(root.child(i))

    def _log(self, msg):
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _log_change(self, msg):
        self.changes_log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _record_change(self, axis_id, yaml_key, value):
        axis_key = str(axis_id).strip() or self.axis_id_default
        if not yaml_key:
            return
        key = str(yaml_key)
        new_val = str(value)
        axis_vals = self._current_values_by_axis.get(axis_key, {})
        prev_val = axis_vals.get(key)
        if prev_val is not None and prev_val != new_val:
            axis_orig = self._original_values_by_axis.setdefault(axis_key, {})
            axis_orig.setdefault(key, str(prev_val))
        axis_changes = self._changes_by_axis.setdefault(axis_key, {})
        axis_changes[key] = new_val

    def _record_current_value(self, axis_id, yaml_key, value):
        axis_key = str(axis_id).strip() or self.axis_id_default
        if not yaml_key:
            return
        axis_vals = self._current_values_by_axis.setdefault(axis_key, {})
        axis_vals[str(yaml_key)] = str(value)

    def _yaml_scalar_text(self, value):
        s = str(value)
        low = s.lower()
        if low in {"true", "false", "null"}:
            return low
        try:
            float(s)
            return s
        except Exception:
            pass
        if re.fullmatch(r"0x[0-9a-fA-F]+", s):
            return f"'{s}'"
        if s == "" or any(ch in s for ch in [":", "#", "{", "}", "[", "]", ","]) or s.strip() != s or " " in s:
            return "'" + s.replace("'", "''") + "'"
        return s

    def _build_yaml_text_from_flat(self, axis_id, flat, title, changed_paths=None, original_values=None):
        flat = dict(flat or {})
        changed_paths = set(changed_paths or [])
        original_values = dict(original_values or {})
        if not flat:
            return f"# No values available for axis {axis_id}\n"

        tree = {}
        for path, value in sorted(flat.items()):
            cur = tree
            parts = [p for p in str(path).split(".") if p]
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            if parts:
                cur[parts[-1]] = value

        lines = [f"# {title} for axis {axis_id}", "axisId: " + self._yaml_scalar_text(axis_id)]

        def emit(node, indent=0, prefix=""):
            pad = " " * indent
            for k, v in node.items():
                path = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    lines.append(f"{pad}{k}:")
                    emit(v, indent + 2, path)
                else:
                    line = f"{pad}{k}: {self._yaml_scalar_text(v)}"
                    if path in changed_paths:
                        line += "  # CHANGED"
                        if path in original_values:
                            orig_txt = str(original_values.get(path, "")).replace("\n", "\\n")
                            line += f", was {self._yaml_scalar_text(orig_txt)}"
                    lines.append(line)

        emit(tree, 0, "")
        return "\n".join(lines) + "\n"

    def _build_changed_yaml_text(self, axis_id):
        axis_key = str(axis_id).strip()
        return self._build_yaml_text_from_flat(
            axis_id,
            self._changes_by_axis.get(axis_key, {}),
            "Changed values",
            changed_paths=set(self._changes_by_axis.get(axis_key, {}).keys()),
            original_values=self._original_values_by_axis.get(axis_key, {}),
        )

    def _build_all_current_yaml_text(self, axis_id):
        axis_key = str(axis_id).strip()
        current = dict(self._current_values_by_axis.get(axis_key, {}))
        changed = self._changes_by_axis.get(axis_key, {})
        # Fill write-only rows from session changes if no readback exists.
        for k, v in (changed or {}).items():
            current.setdefault(k, v)
        # Include all known leaf keys with null if never read/written this session.
        for row in self._leaf_rows:
            path = str(row.get("path", "") or "")
            if path:
                current.setdefault(path, "null")
        changed_paths = set((changed or {}).keys())
        return self._build_yaml_text_from_flat(
            axis_id,
            current,
            "Current values (session-known)",
            changed_paths=changed_paths,
            original_values=self._original_values_by_axis.get(axis_key, {}),
        )

    def _show_changed_yaml_window(self):
        axis_id = self._axis_id()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"Changed YAML (Axis {axis_id})")
        dlg.resize(640, 520)
        lay = QtWidgets.QVBoxLayout(dlg)
        info = QtWidgets.QLabel(f"Session changes for selected axis: {axis_id}")
        lay.addWidget(info)
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("View"))
        mode_combo = QtWidgets.QComboBox()
        mode_combo.addItems(["Changed fields", "All fields (current)"])
        mode_row.addWidget(mode_combo)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)
        edit = QtWidgets.QPlainTextEdit()
        edit.setReadOnly(True)
        lay.addWidget(edit, 1)

        def refresh_text():
            if mode_combo.currentIndex() == 0:
                edit.setPlainText(self._build_changed_yaml_text(axis_id))
            else:
                edit.setPlainText(self._build_all_current_yaml_text(axis_id))

        mode_combo.currentIndexChanged.connect(lambda _=0: refresh_text())
        refresh_text()
        btns = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton("Copy")
        copy_btn.setAutoDefault(False)
        copy_btn.setDefault(False)
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(edit.toPlainText()))
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(dlg.accept)
        btns.addWidget(copy_btn)
        btns.addStretch(1)
        btns.addWidget(close_btn)
        lay.addLayout(btns)
        dlg.exec_()

    def _reload_yaml_from_edit(self):
        self.yaml_path = Path(self.yaml_edit.text().strip())
        self._load_yaml_tree()

    def _load_yaml_tree(self):
        self.tree.clear()
        self._leaf_rows = []
        self.yaml_cmd_map = self._load_yaml_command_map()
        if not self.yaml_path.exists():
            self._log(f"YAML file not found: {self.yaml_path}")
            return
        try:
            root = parse_simple_yaml_tree(self.yaml_path)
        except Exception as ex:
            self._log(f"Failed to parse YAML template: {ex}")
            return

        for child in root.children:
            self._add_tree_node(None, child)
        # Start collapsed: only top-level rows are visible.
        self.tree.collapseAll()
        self._apply_tree_filter()

    def _load_yaml_command_map(self):
        p = self.mapping_path
        if not p or not p.exists():
            return {}
        out = {}
        try:
            with p.open(newline="") as f:
                r = csv.DictReader(f)
                for row in r:
                    key = str(row.get("yaml_key", "")).strip()
                    if not key:
                        continue
                    out[key] = {
                        "get": str(row.get("getter", "")).strip(),
                        "set": str(row.get("setter", "")).strip(),
                    }
            self._log(f"Loaded YAML command map: {p.name} ({len(out)} rows)")
        except Exception as ex:
            self._log(f"Failed to load command map {p.name}: {ex}")
        return out

    def _build_tooltip(self, node, pair, status_txt):
        lines = []
        if node.path:
            lines.append(f"Key: {node.path}")
        if node.comment:
            lines.append(f"Description: {node.comment}")
        if node.value:
            lines.append(f"Template: {node.value}")
        if pair:
            get_cmd = str(pair.get("get", "") or "").strip()
            set_cmd = str(pair.get("set", "") or "").strip()
            lines.append(f"Getter: {get_cmd or '-'}")
            if get_cmd:
                gd = self.catalog_desc_by_named.get(get_cmd, "")
                if gd:
                    lines.append(f"Getter desc: {gd}")
            lines.append(f"Setter: {set_cmd or '-'}")
            if set_cmd:
                sd = self.catalog_desc_by_named.get(set_cmd, "")
                if sd:
                    lines.append(f"Setter desc: {sd}")
        else:
            lines.append("Getter: -")
            lines.append("Setter: -")
        if status_txt:
            lines.append(f"Status: {status_txt}")
        return "\n".join(lines)

    def _add_tree_node(self, parent_item, node):
        item = QtWidgets.QTreeWidgetItem([node.key])
        item.setData(0, QtCore.Qt.UserRole, node.path)
        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)

        if node.children:
            group_tip = self._build_tooltip(node, None, "group")
            for col in range(self.tree.columnCount()):
                item.setToolTip(col, group_tip)
            for ch in node.children:
                self._add_tree_node(item, ch)
            return

        val = scalar_text(node.value)
        blocked = is_block_marked(val)
        if blocked:
            item.setHidden(True)
            return

        base = None
        pair = None
        custom = self.yaml_cmd_map.get(node.path) if not blocked else None
        if custom:
            pair = {"name": node.path, "get": custom.get("get", ""), "set": custom.get("set", "")}
            matched = bool(pair.get("get") or pair.get("set"))
        else:
            base = None if blocked else guess_axis_command_base(node.path, self.command_pairs)
            pair = self.command_pairs.get(base) if base else None
            matched = bool(pair)

        set_edit = QtWidgets.QLineEdit("")
        set_edit.setPlaceholderText(val if val else "value")
        read_edit = QtWidgets.QLineEdit("")
        read_edit.setReadOnly(True)
        cmd_label = QtWidgets.QLineEdit(pair["set"] if pair else "")
        cmd_label.setReadOnly(True)
        cmd_label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        cmd_label.setCursorPosition(0)
        if blocked:
            meta_status_txt = "blocked"
        elif not matched:
            meta_status_txt = "unmatched"
        else:
            has_set = bool(pair.get("set"))
            has_get = bool(pair.get("get"))
            if has_set and has_get:
                meta_status_txt = "matched"
            elif has_set:
                meta_status_txt = "missing getter"
            elif has_get:
                meta_status_txt = "missing setter"
            else:
                meta_status_txt = "unmatched"
        status = QtWidgets.QLabel("")
        tooltip = self._build_tooltip(node, pair, meta_status_txt)
        for col in range(self.tree.columnCount()):
            item.setToolTip(col, tooltip)
        for w in (set_edit, read_edit, cmd_label, status):
            w.setToolTip(tooltip)

        copy_one_btn = QtWidgets.QPushButton("<-")
        copy_one_btn.setAutoDefault(False)
        copy_one_btn.setDefault(False)
        copy_one_btn.setMaximumWidth(36)
        copy_one_btn.setToolTip(tooltip)
        copy_one_btn.clicked.connect(lambda _=False, se=set_edit, re=read_edit: se.setText(re.text()))

        self.tree.setItemWidget(item, 1, set_edit)
        self.tree.setItemWidget(item, 3, copy_one_btn)
        self.tree.setItemWidget(item, 4, read_edit)
        self.tree.setItemWidget(item, 6, cmd_label)
        self.tree.setItemWidget(item, 7, status)

        row = {
            "item": item,
            "path": node.path,
            "template_value": val,
            "set_edit": set_edit,
            "read_edit": read_edit,
            "cmd_label": cmd_label,
            "pair": pair,
            "blocked": blocked,
            "status": status,
        }
        self._leaf_rows.append(row)
        set_edit.returnPressed.connect(lambda rr=row: self._write_row(rr))

        if matched and not blocked:
            w_btn = QtWidgets.QPushButton("W")
            r_btn = QtWidgets.QPushButton("R")
            for b in (w_btn, r_btn):
                b.setAutoDefault(False)
                b.setDefault(False)
                b.setMaximumWidth(40)
            w_btn.setEnabled(bool(pair.get("set")))
            r_btn.setEnabled(bool(pair.get("get")))
            w_btn.setToolTip(tooltip)
            r_btn.setToolTip(tooltip)
            w_btn.clicked.connect(lambda _=False, rr=row: self._write_row(rr))
            r_btn.clicked.connect(lambda _=False, rr=row: self._read_row(rr))
            self.tree.setItemWidget(item, 2, w_btn)
            self.tree.setItemWidget(item, 5, r_btn)
        else:
            placeholder = QtWidgets.QLabel("")
            self.tree.setItemWidget(item, 2, placeholder)
            self.tree.setItemWidget(item, 5, QtWidgets.QLabel(""))

    def _axis_id(self):
        a = self.axis_edit.text().strip()
        return a if a else self.axis_id_default

    def _set_axis_id(self, axis_id):
        a = str(axis_id or "").strip()
        if not a:
            return
        self.axis_edit.setText(a)
        self._sync_axis_combo_to_axis_id(a)
        self._update_window_title_with_motor()

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
        current_axis = self._axis_id()
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
        if self.axis_pick_combo.count() <= 1:
            self.axis_pick_combo.addItem(f"Axis {current_axis}", current_axis)
        self._axis_combo_updating = False
        self._sync_axis_combo_to_axis_id(current_axis)

    def _on_axis_combo_activated(self, _index):
        if self._axis_combo_updating:
            return
        axis_id = str(self.axis_pick_combo.currentData(QtCore.Qt.UserRole) or "").strip()
        if axis_id == "__open_new__":
            self._axis_combo_toggle_open_new_item()
            self._sync_axis_combo_to_axis_id(self._axis_id())
            QtCore.QTimer.singleShot(0, self.axis_pick_combo.showPopup)
            return
        if not axis_id:
            return
        if self._axis_combo_open_new_instance:
            script = Path(__file__).with_name("start_axis.sh")
            prefix = self.title_prefix or ""
            if not prefix:
                cmd_pv = self.cmd_pv.text().strip()
                m = re.match(r"^(.*):MCU-Cmd\.AOUT$", cmd_pv)
                prefix = m.group(1) if m else "IOC:ECMC"
            try:
                subprocess.Popen(
                    ["bash", str(script), str(prefix), str(axis_id), str(self.yaml_path)],
                    cwd=str(script.parent),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                self._log(f"Started new axis cfg window for axis {axis_id} (prefix {prefix})")
            except Exception as ex:
                self._log(f"Failed to start new axis cfg window: {ex}")
            self._sync_axis_combo_to_axis_id(self._axis_id())
            return
        self._set_axis_id(axis_id)
        self._read_and_copy_current_axis(reason="axis selection")

    def _prompt_axis_selection_via_combo(self, reason_msg=None):
        if reason_msg:
            self._log(reason_msg)
        if not self.cfg_group.isVisible():
            self.cfg_group.setVisible(True)
            self.cfg_toggle_btn.setText("Hide Config")
        self._refresh_axis_pick_combo()
        try:
            self.axis_pick_combo.setFocus(QtCore.Qt.OtherFocusReason)
        except Exception:
            pass
        QtCore.QTimer.singleShot(0, self.axis_pick_combo.showPopup)

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

    def _ioc_prefix_for_title(self):
        if self.title_prefix:
            return self.title_prefix
        cmd_pv = self.cmd_pv.text().strip() if hasattr(self, "cmd_pv") else ""
        m = re.match(r"^(.*):MCU-Cmd\.AOUT$", cmd_pv)
        return m.group(1) if m else ""

    def _open_caqtdm_main_panel(self):
        ioc_prefix = self._ioc_prefix_for_title() or ""
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

    def _open_caqtdm_axis_panel(self):
        axis_id = self._axis_id()
        ioc_prefix = self._ioc_prefix_for_title() or ""
        motor_prefix = ""
        axis_name = ""
        try:
            raw = self.client.get(_join_prefix_pv(ioc_prefix, f"MCU-Cfg-AX{axis_id}-Pfx"), as_string=True)
            motor_prefix = str(raw or "").strip().strip('"')
        except Exception:
            motor_prefix = ""
        try:
            raw = self.client.get(_join_prefix_pv(ioc_prefix, f"MCU-Cfg-AX{axis_id}-Nam"), as_string=True)
            axis_name = str(raw or "").strip().strip('"')
        except Exception:
            axis_name = ""
        motor_base = self._resolve_motor_record_name(axis_id)
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
        prefix = self._ioc_prefix_for_title()
        if not prefix:
            raise RuntimeError("Cannot determine IOC prefix from title/cmd PV")

        first_pv = _join_prefix_pv(prefix, "MCU-Cfg-AX-FrstObjId")
        first_raw = self.client.get(first_pv, as_string=True)
        cur = str(first_raw or "").strip().strip('"')
        axes = []
        visited = set()
        steps = 0
        while cur and cur != "-1":
            if cur in visited:
                break
            visited.add(cur)
            steps += 1
            if steps > 10000:
                break
            axis_id = str(cur).strip()
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
            motor = self._combine_motor_record(axis_pfx, motor_name)
            axis_type = ""
            if motor:
                try:
                    axis_type = str(self.client.get(f"{motor}-Type", as_string=True) or "").strip().strip('"')
                except Exception:
                    axis_type = ""
            axes.append({"axis_id": axis_id, "motor": motor, "axis_prefix": axis_pfx, "motor_name": motor_name, "axis_type": axis_type})
            nxt_pv = _join_prefix_pv(prefix, f"MCU-Cfg-AX{axis_id}-NxtObjId")
            nxt_raw = self.client.get(nxt_pv, as_string=True)
            cur = str(nxt_raw or "").strip().strip('"')
        return axes

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
            current_axis = self._axis_id()
            current_row = -1
            for r, ax in enumerate(axes):
                table.insertRow(r)
                axis_id = str(ax.get("axis_id", "") or "")
                motor = str(ax.get("motor", "") or "")
                motor_name = str(ax.get("motor_name", "") or "")
                axis_type = str(ax.get("axis_type", "") or "")
                type_disp = "REAL" if axis_type.upper() == "REAL" else ("Virtual" if axis_type else "?")
                for c, txt in enumerate((axis_id, type_disp, motor, motor_name)):
                    it = QtWidgets.QTableWidgetItem(txt)
                    if c in (0, 1):
                        it.setTextAlignment(QtCore.Qt.AlignCenter)
                    table.setItem(r, c, it)
                if axis_id == current_axis:
                    current_row = r
            if current_row >= 0:
                table.selectRow(current_row)

        def apply_selected():
            r = table.currentRow()
            if r < 0:
                return
            it = table.item(r, 0)
            if it is None:
                return
            axis_id = it.text().strip()
            if open_new_chk.isChecked():
                script = Path(__file__).with_name("start_axis.sh")
                prefix = self.title_prefix or ""
                if not prefix:
                    cmd_pv = self.cmd_pv.text().strip()
                    m = re.match(r"^(.*):MCU-Cmd\\.AOUT$", cmd_pv)
                    prefix = m.group(1) if m else "IOC:ECMC"
                try:
                    subprocess.Popen(
                        ["bash", str(script), str(prefix), str(axis_id), str(self.yaml_path)],
                        cwd=str(script.parent),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._log(f"Started new axis cfg window for axis {axis_id} (prefix {prefix})")
                except Exception as ex:
                    self._log(f"Failed to start new axis cfg window: {ex}")
                    return
            else:
                self._set_axis_id(axis_id)
                self._read_and_copy_current_axis(reason="axis selection")
            dlg.accept()

        refresh_btn.clicked.connect(populate)
        select_btn.clicked.connect(apply_selected)
        close_btn.clicked.connect(dlg.reject)
        table.itemDoubleClicked.connect(lambda _item: apply_selected())

        populate()
        dlg.exec_()

    def _startup_axis_presence_check(self):
        if self._did_startup_axis_presence_check:
            return
        self._did_startup_axis_presence_check = True
        prefix = self._ioc_prefix_for_title()
        current = self._axis_id()
        resolved_id = self._resolve_axis_selector_to_id(current)
        if resolved_id and resolved_id != current:
            self._log(f'Axis selector "{current}" resolved to axis {resolved_id}')
            self._set_axis_id(resolved_id)
            current = resolved_id
        if not prefix:
            self._log("Startup axis probe skipped: IOC prefix unavailable; opening axis picker")
            self._open_axis_picker_dialog()
            return
        try:
            probe_pv = _join_prefix_pv(prefix, f"MCU-Cfg-AX{current}-Pfx")
            raw = self.client.get(probe_pv, as_string=True)
        except Exception as ex:
            self._log(f"Startup axis probe failed for axis {current}: {ex}; opening axis picker")
            self._open_axis_picker_dialog()
            return
        if str(raw or "").strip().strip('"'):
            self._startup_axis_probe_ok = True
            self._update_window_title_with_motor()
            self._initial_read_all_and_copy()
            return
        self._log(f'Axis {current} probe returned empty; opening axis picker')
        self._open_axis_picker_dialog()

    def _combine_motor_record(self, axis_pfx, motor_name):
        a = str(axis_pfx or "").strip()
        m = str(motor_name or "").strip()
        if a and m:
            if m.startswith(a) or ":" in m:
                return m
            return f"{a}{m}" if a.endswith(":") else f"{a}:{m}"
        return a or m

    def _resolve_motor_record_name(self, axis_id):
        prefix = self._ioc_prefix_for_title()
        if not prefix:
            return ""
        a = str(axis_id or "").strip() or self.axis_id_default
        axis_pfx = ""
        motor_name = ""
        try:
            axis_pfx = str(self.client.get(_join_prefix_pv(prefix, f"MCU-Cfg-AX{a}-Pfx"), as_string=True)).strip().strip('"')
        except Exception:
            pass
        try:
            motor_name = str(self.client.get(_join_prefix_pv(prefix, f"MCU-Cfg-AX{a}-Nam"), as_string=True)).strip().strip('"')
        except Exception:
            pass
        return self._combine_motor_record(axis_pfx, motor_name)

    def _update_window_title_with_motor(self):
        try:
            motor = self._resolve_motor_record_name(self._axis_id())
        except Exception:
            motor = ""
        self.setWindowTitle(f"{self._base_title} [{motor}]" if motor else self._base_title)
        self._update_open_controller_button_state()

    def _update_open_controller_button_state(self):
        self.open_cntrl_btn.setEnabled(True)

    def _axis_is_real(self, axis_id=None):
        axis = str(axis_id or self._axis_id()).strip() or self.axis_id_default
        cached = self._axis_is_real_cache.get(axis)
        if cached is not None:
            return bool(cached)
        try:
            motor = self._resolve_motor_record_name(axis)
            if not motor:
                self._axis_is_real_cache[axis] = False
                return False
            t = str(self.client.get(f"{motor}-Type", as_string=True) or "").strip().strip('"')
            is_real = (t.upper() == "REAL")
            self._axis_is_real_cache[axis] = is_real
            return is_real
        except Exception:
            self._axis_is_real_cache[axis] = False
            return False

    def _row_blocked_for_virtual_axis(self, row):
        path = str((row or {}).get("path", "") or "")
        if not path.startswith(("drive.", "controller.")):
            return False
        return not self._axis_is_real(self._axis_id())

    def send_raw_command(self, cmd):
        pv = self.cmd_pv.text().strip()
        cmd = normalize_float_literals((cmd or "").strip())
        if not pv:
            return False, "ERROR: Command PV is empty"
        if not cmd:
            return False, "ERROR: Command text is empty"
        try:
            self.client.put(pv, cmd, wait=True)
            msg = f"CMD -> {pv}: {cmd}"
            self._log(msg)
            return True, msg
        except Exception as ex:
            msg = f"ERROR sending command: {ex} | CMD={cmd}"
            self._log(msg)
            return False, msg

    def read_raw_command(self, cmd):
        ok, msg = self.send_raw_command(cmd)
        if not ok:
            return False, msg
        qp = self.qry_pv.text().strip()
        if not qp:
            return True, "Command sent, no QRY PV configured"
        try:
            self.client.put(_proc_pv_for_readback(qp), 1, wait=True)
            val = self.client.get(qp, as_string=True)
            msg = f"QRY <- {qp}: {val}"
            self._log(msg)
            return True, msg
        except Exception as ex:
            msg = f"ERROR query read: {ex}"
            self._log(msg)
            return False, msg

    def _write_row(self, row):
        pair = row.get("pair")
        if not pair or not pair.get("set"):
            row["status"].setText("missing setter")
            return
        if self._row_blocked_for_virtual_axis(row):
            row["status"].setText("virtual axis")
            row["read_edit"].setText("Blocked for virtual axis")
            return
        value = row["set_edit"].text().strip() or row.get("template_value", "")
        if not value or is_block_marked(value):
            row["status"].setText("no value")
            return
        cmd = fill_axis_command(pair["set"], self._axis_id(), value)
        ok, msg = self.send_raw_command(cmd)
        row["status"].setText("OK" if ok else "ERR")
        if ok:
            axis_id = self._axis_id()
            self._record_change(axis_id, row.get("path", ""), value)
            # If getter is missing, write is the best current value we have.
            if not pair.get("get"):
                self._record_current_value(axis_id, row.get("path", ""), value)
            self._log_change(f'WRITE axis={self._axis_id()} key={row.get("path","")} value={value} | {cmd}')
            self._read_row(row)
        else:
            row["read_edit"].setText(msg)

    def _read_row(self, row):
        pair = row.get("pair")
        if not pair or not pair.get("get"):
            row["status"].setText("missing getter")
            return None
        if self._row_blocked_for_virtual_axis(row):
            row["status"].setText("virtual axis")
            row["read_edit"].setText("Blocked for virtual axis")
            return None
        cmd = fill_axis_command(pair["get"], self._axis_id(), "")
        ok, msg = self.read_raw_command(cmd)
        row["status"].setText("OK" if ok else "ERR")
        if ok and ": " in msg:
            val = msg.split(": ", 1)[1].strip()
            disp_val = compact_float_text(val)
            row["read_edit"].setText(disp_val)
            self._record_current_value(self._axis_id(), row.get("path", ""), disp_val)
        else:
            row["read_edit"].setText(msg)
        return bool(ok)

    def _read_all_matched(self, abort_on_error=False):
        count = 0
        failed = False
        for row in self._leaf_rows:
            if row.get("blocked"):
                continue
            pair = row.get("pair")
            if not pair or not pair.get("get"):
                continue
            ok = self._read_row(row)
            count += 1
            if ok is False:
                failed = True
                if abort_on_error:
                    self._log(f'Read matched rows aborted after failure at key="{row.get("path","")}" ({count} attempted)')
                    return False
        self._log(f"Read matched rows: {count}" + (" (with errors)" if failed else ""))
        return not failed

    def _initial_read_all_and_copy(self):
        if not self._startup_axis_probe_ok:
            return
        if self._did_initial_read_copy:
            return
        self._did_initial_read_copy = True
        try:
            self._read_and_copy_current_axis(reason="startup")
        except Exception as ex:
            self._log(f"Startup Read All / Copy failed: {ex}")

    def _read_and_copy_current_axis(self, reason=""):
        self._startup_axis_probe_ok = True
        ok = self._read_all_matched(abort_on_error=True)
        if ok:
            self._copy_read_to_set()
            return True
        if reason:
            self._log(f'Copy Read->Set skipped after {reason} because Read All aborted on first read error')
        return False

    def _write_filled_matched(self):
        count = 0
        for row in self._leaf_rows:
            if row.get("blocked") or not row.get("pair"):
                continue
            v = row["set_edit"].text().strip()
            if not v:
                continue
            self._write_row(row)
            count += 1
        self._log(f"Wrote filled matched rows: {count}")

    def _copy_read_to_set(self):
        count = 0
        for row in self._leaf_rows:
            txt = row["read_edit"].text().strip()
            if not txt:
                continue
            row["set_edit"].setText(txt)
            count += 1
        self._log(f"Copied readback to set fields: {count}")


def main():
    ap = argparse.ArgumentParser(description="Qt app for axis YAML template config mapped to ecmc commands")
    ap.add_argument("--catalog", default="ecmc_commands.json")
    ap.add_argument("--yaml", default="axis_template.yaml")
    ap.add_argument("--mapping", default="")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--cmd-pv", default="")
    ap.add_argument("--qry-pv", default="")
    ap.add_argument("--axis-id", default="1")
    ap.add_argument("--timeout", type=float, default=2.0)
    args = ap.parse_args()

    default_cmd_pv = args.cmd_pv.strip() if args.cmd_pv else _join_prefix_pv(args.prefix, "MCU-Cmd.AOUT")
    default_qry_pv = args.qry_pv.strip() if args.qry_pv else _join_prefix_pv(args.prefix, "MCU-Cmd.AINP")

    app = QtWidgets.QApplication(sys.argv)
    w = AxisYamlConfigWindow(
        catalog_path=args.catalog,
        yaml_path=args.yaml,
        mapping_path=args.mapping,
        default_cmd_pv=default_cmd_pv,
        default_qry_pv=default_qry_pv,
        timeout=args.timeout,
        axis_id=args.axis_id,
        title_prefix=args.prefix,
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
