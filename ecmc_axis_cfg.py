#!/usr/bin/env python3
import argparse
import csv
import json
import re
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
    children: list = field(default_factory=list)


def _strip_yaml_comment(line):
    out = []
    in_s = False
    in_d = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "#" and not in_s and not in_d:
            break
        out.append(ch)
    return "".join(out).rstrip()


def parse_simple_yaml_tree(path):
    root = YNode("(root)", "")
    stack = [(-1, root)]
    list_counters = {}
    pending_container = None

    for raw in Path(path).read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        line = _strip_yaml_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        text = line.lstrip(" ")

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        parent = stack[-1][1]

        if text.startswith("- "):
            value = text[2:].strip()
            idx = list_counters.get(id(parent), 0)
            list_counters[id(parent)] = idx + 1
            key = f"[{idx}]"
            path_key = f"{parent.path}.{key}" if parent.path else key
            node = YNode(key=key, path=path_key, value=value)
            parent.children.append(node)
            continue

        if ":" not in text:
            continue
        key, rest = text.split(":", 1)
        key = key.strip()
        value = rest.strip()
        path_key = f"{parent.path}.{key}" if parent.path else key
        node = YNode(key=key, path=path_key, value=value)
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
        self.resize(1400, 900)
        self.client = EpicsClient(timeout=timeout)
        self.catalog = self._load_catalog(catalog_path)
        self.command_pairs = build_axis_command_pairs(self.catalog)
        self.yaml_path = Path(yaml_path)
        self.mapping_path = Path(mapping_path) if mapping_path else Path(yaml_path).with_suffix(".command_map.csv")
        self.yaml_cmd_map = {}
        self.axis_id_default = str(axis_id).strip() or "1"
        self._leaf_rows = []
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

    def _build_ui(self, default_cmd_pv, default_qry_pv, timeout):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        cfg_group = QtWidgets.QGroupBox("Configuration")
        cfg = QtWidgets.QGridLayout(cfg_group)
        self.cmd_pv = QtWidgets.QLineEdit(default_cmd_pv)
        self.qry_pv = QtWidgets.QLineEdit(default_qry_pv)
        self.axis_edit = QtWidgets.QLineEdit(self.axis_id_default)
        self.axis_edit.setMaximumWidth(80)
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
        self.show_unmatched = QtWidgets.QCheckBox("Show unmatched")
        self.show_unmatched.setChecked(True)
        self.show_unmatched.toggled.connect(self._load_yaml_tree)
        self.show_blocked = QtWidgets.QCheckBox('Show "block" fields')
        self.show_blocked.setChecked(False)
        self.show_blocked.toggled.connect(self._load_yaml_tree)

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
        cfg.addWidget(self.show_unmatched, 0, 4)
        cfg.addWidget(self.show_blocked, 1, 4)
        layout.addWidget(cfg_group)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(["Field", "Set Value", "Readback", "Command", "W", "R", "Status"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(False)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(6, QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.tree, stretch=1)

        action_row = QtWidgets.QHBoxLayout()
        read_all = QtWidgets.QPushButton("Read Matched")
        read_all.setAutoDefault(False)
        read_all.setDefault(False)
        read_all.clicked.connect(self._read_all_matched)
        write_all = QtWidgets.QPushButton("Write Filled")
        write_all.setAutoDefault(False)
        write_all.setDefault(False)
        write_all.clicked.connect(self._write_filled_matched)
        copy_btn = QtWidgets.QPushButton("Copy Read->Set")
        copy_btn.setAutoDefault(False)
        copy_btn.setDefault(False)
        copy_btn.clicked.connect(self._copy_read_to_set)
        action_row.addWidget(read_all)
        action_row.addWidget(write_all)
        action_row.addWidget(copy_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=0)

    def _log(self, msg):
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

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
        self.tree.expandToDepth(1)

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

    def _add_tree_node(self, parent_item, node):
        item = QtWidgets.QTreeWidgetItem([node.key])
        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)

        if node.children:
            for ch in node.children:
                self._add_tree_node(item, ch)
            return

        val = scalar_text(node.value)
        blocked = is_block_marked(val)
        if blocked and not self.show_blocked.isChecked():
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

        if not matched and not self.show_unmatched.isChecked():
            item.setHidden(True)
            return

        set_edit = QtWidgets.QLineEdit("")
        set_edit.setPlaceholderText(val if val else "value")
        read_edit = QtWidgets.QLineEdit("")
        read_edit.setReadOnly(True)
        cmd_label = QtWidgets.QLineEdit(pair["set"] if pair else "")
        cmd_label.setReadOnly(True)
        if blocked:
            status_txt = "blocked"
        elif not matched:
            status_txt = "unmatched"
        else:
            has_set = bool(pair.get("set"))
            has_get = bool(pair.get("get"))
            if has_set and has_get:
                status_txt = "matched"
            elif has_set:
                status_txt = "missing getter"
            elif has_get:
                status_txt = "missing setter"
            else:
                status_txt = "unmatched"
        status = QtWidgets.QLabel(status_txt)

        self.tree.setItemWidget(item, 1, set_edit)
        self.tree.setItemWidget(item, 2, read_edit)
        self.tree.setItemWidget(item, 3, cmd_label)
        self.tree.setItemWidget(item, 6, status)

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
                b.setMaximumWidth(32)
            w_btn.setEnabled(bool(pair.get("set")))
            r_btn.setEnabled(bool(pair.get("get")))
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
            row["read_edit"].setText(msg.split(": ", 1)[1].strip())
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
