#!/usr/bin/env python3
import argparse
import csv
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

try:
    from PyQt5 import QtCore, QtWidgets
except Exception:
    from PySide6 import QtCore, QtWidgets  # type: ignore

from ecmc_stream_qt import EpicsClient, _join_prefix_pv, _proc_pv_for_readback, normalize_float_literals


PLACEHOLDER_RE = re.compile(r"<([^>]+)>")


@dataclass
class YNode:
    key: str
    path: str
    value: str = ""
    comment: str = ""
    children: list = field(default_factory=list)


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
    pending_comment_lines = []

    for raw in Path(path).read_text().splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            txt = raw.lstrip()[1:].strip()
            if txt:
                pending_comment_lines.append(txt)
            continue
        line, comment = _split_yaml_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        text = line.lstrip(" ")
        merged_comment = "\n".join(pending_comment_lines + ([comment] if comment else []))
        pending_comment_lines = []

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
        self.setWindowTitle(f"ecmc Axis YAML Config [{title_prefix}]" if title_prefix else "ecmc Axis YAML Config")
        self.resize(920, 620)
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
        self._build_ui(default_cmd_pv, default_qry_pv, timeout)
        self._load_yaml_tree()
        self._log(f"Connected via backend: {self.client.backend}")

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
        self.open_cntrl_btn = QtWidgets.QPushButton("Open Controller")
        self.open_cntrl_btn.setAutoDefault(False)
        self.open_cntrl_btn.setDefault(False)
        self.open_cntrl_btn.clicked.connect(self._open_controller_window)
        top_row.addWidget(self.cfg_toggle_btn)
        top_row.addWidget(self.log_toggle_btn)
        top_row.addWidget(self.changes_toggle_btn)
        top_row.addWidget(self.changed_yaml_btn)
        top_row.addWidget(self.open_cntrl_btn)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter keys...")
        self.search.textChanged.connect(self._apply_tree_filter)
        self.axis_top_edit = QtWidgets.QLineEdit(self.axis_id_default)
        self.axis_top_edit.setMaximumWidth(80)
        self.axis_top_edit.editingFinished.connect(lambda: self.axis_edit.setText(self.axis_top_edit.text()))
        search_row.addWidget(self.search, 1)
        search_row.addWidget(QtWidgets.QLabel("Axis"))
        search_row.addWidget(self.axis_top_edit)
        layout.addLayout(search_row)

        self.cfg_group = QtWidgets.QGroupBox("Configuration")
        cfg = QtWidgets.QGridLayout(self.cfg_group)
        self.cmd_pv = QtWidgets.QLineEdit(default_cmd_pv)
        self.qry_pv = QtWidgets.QLineEdit(default_qry_pv)
        self.axis_edit = QtWidgets.QLineEdit(self.axis_id_default)
        self.axis_edit.setMaximumWidth(80)
        self.axis_edit.editingFinished.connect(lambda: self.axis_top_edit.setText(self.axis_edit.text()))
        self.timeout_edit = QtWidgets.QDoubleSpinBox()
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

        cfg.addWidget(QtWidgets.QLabel("Command PV"), 0, 0)
        cfg.addWidget(self.cmd_pv, 0, 1)
        cfg.addWidget(QtWidgets.QLabel("Query PV"), 1, 0)
        cfg.addWidget(self.qry_pv, 1, 1)
        cfg.addWidget(QtWidgets.QLabel("Axis ID"), 0, 2)
        cfg.addWidget(self.axis_edit, 0, 3)
        cfg.addWidget(QtWidgets.QLabel("Timeout [s]"), 1, 2)
        cfg.addWidget(self.timeout_edit, 1, 3)
        cfg.addWidget(QtWidgets.QLabel("YAML Template"), 2, 0)
        cfg.addWidget(self.yaml_edit, 2, 1, 1, 3)
        cfg.addWidget(reload_btn, 2, 4)
        layout.addWidget(self.cfg_group)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(["Field", "Set Value", "", "Readback", "W", "R", "Command", "Status"])
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
        axis_changes = self._changes_by_axis.setdefault(axis_key, {})
        axis_changes[str(yaml_key)] = str(value)

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

    def _build_yaml_text_from_flat(self, axis_id, flat, title, changed_paths=None):
        flat = dict(flat or {})
        changed_paths = set(changed_paths or [])
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
                    lines.append(line)

        emit(tree, 0, "")
        return "\n".join(lines) + "\n"

    def _build_changed_yaml_text(self, axis_id):
        return self._build_yaml_text_from_flat(axis_id, self._changes_by_axis.get(str(axis_id).strip(), {}), "Changed values")

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
        return self._build_yaml_text_from_flat(axis_id, current, "Current values (session-known)", changed_paths=changed_paths)

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
        self.tree.setItemWidget(item, 2, copy_one_btn)
        self.tree.setItemWidget(item, 3, read_edit)
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
            self.tree.setItemWidget(item, 4, w_btn)
            self.tree.setItemWidget(item, 5, r_btn)
        else:
            placeholder = QtWidgets.QLabel("")
            self.tree.setItemWidget(item, 4, placeholder)
            self.tree.setItemWidget(item, 5, QtWidgets.QLabel(""))

    def _axis_id(self):
        a = self.axis_edit.text().strip()
        return a if a else self.axis_id_default

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
            return
        cmd = fill_axis_command(pair["get"], self._axis_id(), "")
        ok, msg = self.read_raw_command(cmd)
        row["status"].setText("OK" if ok else "ERR")
        if ok and ": " in msg:
            val = msg.split(": ", 1)[1].strip()
            row["read_edit"].setText(val)
            self._record_current_value(self._axis_id(), row.get("path", ""), val)
        else:
            row["read_edit"].setText(msg)

    def _read_all_matched(self):
        count = 0
        for row in self._leaf_rows:
            if row.get("pair") and not row.get("blocked"):
                self._read_row(row)
                count += 1
        self._log(f"Read matched rows: {count}")

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
