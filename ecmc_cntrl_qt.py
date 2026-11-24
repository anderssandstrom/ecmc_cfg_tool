#!/usr/bin/env python3
import argparse
import json
import re
import time
from ecmc_controller_live import LivePVMonitor, derive
from ecmc_caqtdm import launch_caqtdm
from ecmc_diagnostic_presets import axis_diagnostic_pvs
from ecmc_app_launcher import launch_tool
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ecmc_demo import DemoClient
import ecmc_parameter_conversion as units
from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_command_client import CommandClient, command_transaction

from ecmc_stream_qt import (
    EpicsClient,
    _join_prefix_pv,
    _proc_pv_for_readback,
    compact_float_text,
    load_local_error_name_map,
    normalize_float_literals,
    query_value_indicates_error,
    summarize_error_text,
)


PLACEHOLDER_RE = re.compile(r'<[^>]+>')
APP_LAUNCH_PLACEHOLDER = 'Open app...'
APP_LAUNCH_CONTROLLER = 'New Cntrl App'
APP_LAUNCH_AXIS = 'Axis Cfg App'
APP_LAUNCH_MOTION = 'Motion App'
APP_LAUNCH_ISO230 = 'ISO230 App'
APP_LAUNCH_DAQ = 'DAQ App'
APP_LAUNCH_RTLOG = 'ecmc Log App'
APP_LAUNCH_CAQTDM_MAIN = 'caqtdm Main'
APP_LAUNCH_CAQTDM_AXIS = 'caqtdm Axis'


def _normalize_axis_object_id(value):
    s = str(value or '').strip().strip('"')
    if not s:
        return ''
    if re.fullmatch(r'[+-]?\d+\.0+', s):
        return s.split('.', 1)[0].lstrip('+')
    return s.lstrip('+')


def _normalize_axis_type_text(value):
    s = str(value or '').strip().strip('"')
    if not s:
        return ''
    up = s.upper()
    if up == 'REAL':
        return 'REAL'
    if up == 'VIRTUAL':
        return 'VIRTUAL'
    if re.fullmatch(r'[+-]?\d+(\.0+)?', s):
        try:
            iv = int(float(s))
        except Exception:
            iv = None
        if iv == 1:
            return 'REAL'
        if iv == 2:
            return 'VIRTUAL'
    return s


def _template_args(template):
    return PLACEHOLDER_RE.findall(template or '')


def _strip_prefix_and_kind(cmd):
    s = str(cmd or '').strip()
    if s.startswith('Cfg.'):
        s = s[4:]
    head = s.split('(', 1)[0]
    if head.startswith('Get'):
        return 'get', head[3:]
    if head.startswith('Set'):
        return 'set', head[3:]
    return 'other', head


def _build_pairs(commands, include_set_only=False):
    pairs = {}
    for c in commands:
        tmpl = c.get('command_named', c.get('command', ''))
        kind, base = _strip_prefix_and_kind(tmpl)
        if kind not in {'get', 'set'}:
            continue
        item = pairs.setdefault(base, {'name': base, 'get': '', 'set': '', 'group': _group_for_name(base)})
        item[kind] = tmpl
        if kind == 'set' and not item.get('get'):
            item['get'] = _derive_get_template_from_set(tmpl)
    if include_set_only:
        return [pairs[k] for k in sorted(pairs.keys(), key=lambda x: x.lower())]
    # Exclude set-only commands in table tuning view: every row should support readback.
    return [pairs[k] for k in sorted(pairs.keys(), key=lambda x: x.lower()) if pairs[k].get('get')]


def _replace_placeholders(template, args):
    out = str(template or '')
    for a in _template_args(out):
        if not args:
            return '', f'Missing value for {a}'
        out = out.replace(a, args.pop(0), 1)
    if args:
        return '', 'Too many values'
    return out, ''


def _split_csv(text):
    t = str(text or '').strip()
    if not t:
        return []
    return [x.strip() for x in t.split(',') if x.strip()]


def _derive_get_template_from_set(set_template):
    s = str(set_template or '').strip()
    m = re.match(r'^(Cfg\.)Set([A-Za-z0-9_]+)\((.*)\)$', s)
    if not m:
        return ''
    prefix, base, args = m.groups()
    args = [a.strip() for a in str(args).split(',') if a.strip()]
    axis_arg = args[0] if args else '<axisIndex>'
    return f'{prefix}Get{base}({axis_arg})'


def _group_for_name(name):
    low = str(name or '').lower()
    if 'scale' in low:
        return 'Scaling'
    if 'poslag' in low or 'enablelagmon' in low:
        return 'Position Lag Monitor'
    if 'attarget' in low:
        return 'At Target Monitor'
    if 'inner' in low:
        return 'Inner Loop PID'
    if 'ipart' in low:
        return 'Integrator Limits'
    if 'outhl' in low or 'outll' in low:
        return 'Output Limits'
    if 'cntrl' in low and any(k in low for k in ('kp', 'ki', 'kd', 'kff', 'deadband')):
        return 'PID Core'
    return 'Other'


class CntrlWindow(QtWidgets.QMainWindow):
    def __init__(
        self,
        catalog_path,
        default_cmd_pv,
        default_qry_pv,
        timeout,
        default_axis_id='1',
        title_prefix='',
        error_db_path='',
        axis_id_was_provided=True,
        demo=False,
    ):
        super().__init__()
        p = str(title_prefix or '').strip()
        self._base_title = f'ecmc Axis Controller Configurator [{p}]' if p else 'ecmc Axis Controller Configurator'
        self.setWindowTitle(self._base_title)
        _f = self.font()
        if _f.pointSize() > 0:
            _f.setPointSize(max(8, _f.pointSize() - 1))
            self.setFont(_f)
        self.resize(920, 504)
        self.demo = bool(demo)
        if self.demo:
            self._base_title += ' [OFFLINE DEMO]'
            self.setWindowTitle(self._base_title)
        self.client = DemoClient(timeout=timeout) if self.demo else EpicsClient(timeout=timeout)
        self.catalog = self._load_catalog(catalog_path)
        self.error_name_by_code = load_local_error_name_map(error_db_path)
        self.rows = _build_pairs(self.catalog.get('commands', []), include_set_only=False)
        self.rows_all = _build_pairs(self.catalog.get('commands', []), include_set_only=True)
        # These commands are present in ECMC 11.2.0 but may be absent from older catalogs.
        for name in ["AxisCntrlResetIAtRmp","AxisCntrlFreezeIAtRmp","AxisMonSlvCtrlDbTol","AxisMonSlvCtrlDbTime","AxisMonEnableAtTargetMon","AxisMonEnableLagMon","AxisMonPosLagTol","AxisMonPosLagTime"]:
            if not any(row['name'] == name for row in self.rows_all):
                row = {'name': name, 'get': f'Get{name}(<axisIndex>)',
                       'set': f'Cfg.Set{name}(<axisIndex>,<value>)', 'group': _group_for_name(name)}
                self.rows_all.append(row)
                self.rows.append(row)
        self._rows_all_by_name = {r['name']: r for r in self.rows_all}
        self._diagram_read_rows = []
        self._diagram_value_pairs = []
        self._changes_by_axis = {}
        self._current_values_by_axis = {}
        self._axis_is_real_cache = {}
        self.default_axis_id = str(default_axis_id).strip() or '1'
        self._axis_id_was_provided = bool(axis_id_was_provided)
        self.title_prefix = p
        self._did_initial_read_all = False
        self._did_real_axis_startup_check = False
        self._closing_due_to_non_real = False
        self._did_startup_axis_presence_check = False
        self._startup_axis_probe_ok = False
        self._axis_combo_updating = False
        self._axis_combo_open_new_instance = False
        self._read_all_in_progress = False
        self._read_all_cancel_requested = False
        self._tuning_window = None
        self._live_monitor = None
        self._live_identity = None
        self._live_qualified_since = None
        self._controller_scene = None
        self._controller_mode = 'UNKNOWN'
        self._controller_mode_identity = None
        self._live_timer = QtCore.QTimer(self)
        self._live_timer.setInterval(200)
        self._live_timer.timeout.connect(self._update_live)
        self._build_ui(default_cmd_pv, default_qry_pv, timeout)
        self._populate_table()
        self._log(f'Connected via backend: {self.client.backend}')
        if self.error_name_by_code:
            self._log(f'Loaded local error DB: {len(self.error_name_by_code)} codes')
        QtCore.QTimer.singleShot(0, self._startup_axis_presence_check)

    def _load_catalog(self, path):
        p = Path(path)
        if not p.exists():
            return {'commands': []}
        try:
            return json.loads(p.read_text())
        except Exception:
            return {'commands': []}

    def _build_ui(self, default_cmd_pv, default_qry_pv, timeout):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        cfg_group = QtWidgets.QGroupBox('General Configuration')
        cfg = QtWidgets.QGridLayout(cfg_group)
        cfg.setContentsMargins(6, 6, 6, 6)
        cfg.setHorizontalSpacing(4)
        cfg.setVerticalSpacing(3)
        self.cmd_pv = QtWidgets.QLineEdit(default_cmd_pv)
        self.cmd_pv.editingFinished.connect(self._update_window_title_with_motor)
        self.cmd_pv.editingFinished.connect(self._refresh_controller_context)
        self.qry_pv = QtWidgets.QLineEdit(default_qry_pv)
        self.timeout_edit = QtWidgets.QDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(timeout)
        self.timeout_edit.valueChanged.connect(lambda v: setattr(self.client, 'timeout', float(v)))
        for w in (self.cmd_pv, self.qry_pv, self.timeout_edit):
            try:
                w.setMaximumHeight(24)
            except Exception:
                pass
        cfg.addWidget(QtWidgets.QLabel('Command PV'), 0, 0)
        cfg.addWidget(self.cmd_pv, 0, 1)
        cfg.addWidget(QtWidgets.QLabel('Readback PV'), 1, 0)
        cfg.addWidget(self.qry_pv, 1, 1)
        cfg.addWidget(QtWidgets.QLabel('Timeout [s]'), 2, 0)
        cfg.addWidget(self.timeout_edit, 2, 1)
        cfg.addWidget(QtWidgets.QLabel('Axis All'), 3, 0)
        self.axis_all_edit = QtWidgets.QLineEdit(self.default_axis_id)
        self.axis_all_edit.setMaximumWidth(70)
        self.axis_all_edit.returnPressed.connect(self._apply_axis_all)
        self.axis_all_edit.editingFinished.connect(self._update_window_title_with_motor)
        cfg.addWidget(self.axis_all_edit, 3, 1)
        self.axis_all_btn = QtWidgets.QPushButton('Apply Axis')
        self.axis_all_btn.setAutoDefault(False)
        self.axis_all_btn.setDefault(False)
        self.axis_all_btn.clicked.connect(self._apply_axis_all)
        self.axis_all_btn.setMaximumHeight(24)
        cfg.addWidget(self.axis_all_btn, 3, 2)
        self.caqtdm_main_btn = QtWidgets.QPushButton('caqtdm Main')
        self.caqtdm_main_btn.setAutoDefault(False)
        self.caqtdm_main_btn.setDefault(False)
        self.caqtdm_main_btn.clicked.connect(self._open_caqtdm_main_panel)
        self.caqtdm_main_btn.setMaximumHeight(24)
        cfg.addWidget(self.caqtdm_main_btn, 3, 3)

        cfg_panel = QtWidgets.QWidget()
        cfg_panel_layout = QtWidgets.QHBoxLayout(cfg_panel)
        cfg_panel_layout.setContentsMargins(0, 0, 0, 0)
        cfg_panel_layout.setSpacing(6)
        cfg_panel_layout.addWidget(cfg_group, stretch=1)
        cfg_panel.setVisible(False)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(4)
        self.pv_cfg_toggle = QtWidgets.QPushButton('Show Config')
        self.pv_cfg_toggle.setCheckable(True)
        self.pv_cfg_toggle.setChecked(False)
        self.pv_cfg_toggle.setAutoDefault(False)
        self.pv_cfg_toggle.setDefault(False)
        self.pv_cfg_toggle.toggled.connect(
            lambda checked: (
                cfg_panel.setVisible(bool(checked)),
                self.pv_cfg_toggle.setText('Hide Config' if checked else 'Show Config'),
            )
        )
        top_row.addWidget(self.pv_cfg_toggle)
        self.log_toggle_btn = QtWidgets.QPushButton('Show Log')
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setChecked(False)
        self.log_toggle_btn.setAutoDefault(False)
        self.log_toggle_btn.setDefault(False)
        self.log_toggle_btn.toggled.connect(self._toggle_log_visible)
        self.changes_toggle_btn = QtWidgets.QPushButton('Show Changes')
        self.changes_toggle_btn.setCheckable(True)
        self.changes_toggle_btn.setChecked(False)
        self.changes_toggle_btn.setAutoDefault(False)
        self.changes_toggle_btn.setDefault(False)
        self.changes_toggle_btn.toggled.connect(self._toggle_changes_log_visible)
        cfg.addWidget(self.log_toggle_btn, 4, 0)
        cfg.addWidget(self.changes_toggle_btn, 4, 1)
        self.yaml_btn = QtWidgets.QPushButton('Show YAML')
        self.yaml_btn.setAutoDefault(False)
        self.yaml_btn.setDefault(False)
        self.yaml_btn.clicked.connect(self._show_yaml_window)
        top_row.addWidget(self.yaml_btn)
        self.tuning_btn = QtWidgets.QPushButton('Tuning')
        self.tuning_btn.setMaximumHeight(24)
        self.tuning_btn.clicked.connect(self._open_tuning_window)
        top_row.addWidget(self.tuning_btn)
        self.open_app_combo = QtWidgets.QComboBox()
        self.open_app_combo.setMinimumWidth(170)
        self.open_app_combo.addItem(APP_LAUNCH_PLACEHOLDER, '')
        self.open_app_combo.addItem(APP_LAUNCH_CONTROLLER, 'controller')
        self.open_app_combo.addItem(APP_LAUNCH_AXIS, 'axis')
        self.open_app_combo.addItem(APP_LAUNCH_MOTION, 'motion')
        self.open_app_combo.addItem(APP_LAUNCH_ISO230, 'iso230')
        self.open_app_combo.addItem(APP_LAUNCH_DAQ, 'daq')
        self.open_app_combo.addItem(APP_LAUNCH_RTLOG, 'rtlog')
        self.open_app_combo.addItem(APP_LAUNCH_CAQTDM_MAIN, 'caqtdm_main')
        self.open_app_combo.addItem(APP_LAUNCH_CAQTDM_AXIS, 'caqtdm_axis')
        self.open_app_combo.activated.connect(self._on_open_app_selected)
        if self.demo:
            self.open_app_combo.setEnabled(False)
            self.open_app_combo.setToolTip('External IOC applications are disabled in offline demo mode.')
        top_row.addWidget(QtWidgets.QLabel('Launch'))
        top_row.addWidget(self.open_app_combo)
        self.axis_pick_combo = QtWidgets.QComboBox()
        self.axis_pick_combo.setMinimumWidth(170)
        self.axis_pick_combo.setMaximumWidth(300)
        self.axis_pick_combo.activated.connect(self._on_axis_combo_activated)
        for w in (
            self.pv_cfg_toggle,
            self.yaml_btn,
            self.open_app_combo,
        ):
            w.setMaximumHeight(24)
        top_row.addStretch(1)
        top_row.addWidget(QtWidgets.QLabel('Axis'))
        top_row.addWidget(self.axis_pick_combo)
        layout.addLayout(top_row)
        layout.addWidget(cfg_panel)

        search_row = QtWidgets.QHBoxLayout()
        search_row.setSpacing(4)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Filter commands...')
        self.search.textChanged.connect(self._populate_table)
        search_row.addWidget(self.search)
        search_row.addWidget(QtWidgets.QLabel('View'))
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(['Flat', 'Schematic', 'Diagram', 'Controller Sketch'])
        self.view_mode.setCurrentText('Controller Sketch')
        self.view_mode.currentTextChanged.connect(self._on_view_mode_changed)
        search_row.addWidget(self.view_mode)
        self.pid_units = QtWidgets.QCheckBox('Ti/Td [ms]')
        self.time_units = QtWidgets.QCheckBox('Time [ms]')
        self.rate_label = QtWidgets.QLabel('Rate: —')
        for control in (self.pid_units, self.time_units):
            search_row.addWidget(control)
            control.toggled.connect(self._on_view_mode_changed)
        search_row.addWidget(self.rate_label)
        self.live_check = QtWidgets.QCheckBox('Live')
        self.live_check.setToolTip('Read-only CA monitor diagnostics (5 Hz GUI refresh; IOC PV update rate may be slower)')
        self.live_check.toggled.connect(self._toggle_live)
        search_row.addWidget(self.live_check)
        self.read_all_btn = QtWidgets.QPushButton('Read All')
        self.read_all_btn.setAutoDefault(False)
        self.read_all_btn.setDefault(False)
        self.read_all_btn.clicked.connect(self._read_all_rows)
        search_row.addWidget(self.read_all_btn)
        self.copy_read_to_set_btn = QtWidgets.QPushButton('Copy Read->Set')
        self.copy_read_to_set_btn.setAutoDefault(False)
        self.copy_read_to_set_btn.setDefault(False)
        self.copy_read_to_set_btn.clicked.connect(self._copy_all_read_to_set)
        search_row.addWidget(self.copy_read_to_set_btn)
        for w in (
            self.search,
            self.view_mode,
            self.read_all_btn,
            self.copy_read_to_set_btn,
        ):
            try:
                w.setMaximumHeight(24)
            except Exception:
                pass
        layout.addLayout(search_row)

        self.stack = QtWidgets.QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        table_page = QtWidgets.QWidget()
        table_layout = QtWidgets.QVBoxLayout(table_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Command', 'Axis', 'Set Value', 'Write', 'Read Value', 'Read'])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeToContents)
        table_layout.addWidget(self.table)
        self.stack.addWidget(table_page)

        self.diagram_scroll = QtWidgets.QScrollArea()
        self.diagram_scroll.setWidgetResizable(True)
        self.diagram_root = QtWidgets.QWidget()
        self.diagram_layout = QtWidgets.QGridLayout(self.diagram_root)
        self.diagram_layout.setContentsMargins(8, 8, 8, 8)
        self.diagram_layout.setHorizontalSpacing(10)
        self.diagram_layout.setVerticalSpacing(10)
        self.diagram_scroll.setWidget(self.diagram_root)
        self.stack.addWidget(self.diagram_scroll)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setVisible(False)
        self.log.setMaximumHeight(110)
        layout.addWidget(self.log, stretch=0)
        self.changes_log = QtWidgets.QPlainTextEdit()
        self.changes_log.setReadOnly(True)
        self.changes_log.setVisible(False)
        self.changes_log.setPlaceholderText('Successful writes are tracked here for this session...')
        self.changes_log.setMaximumHeight(110)
        layout.addWidget(self.changes_log, stretch=0)
        progress_row = QtWidgets.QHBoxLayout()
        self.read_progress = QtWidgets.QProgressBar()
        self.read_progress.setMinimum(0)
        self.read_progress.setMaximum(100)
        self.read_progress.setValue(0)
        self.read_progress.setFormat('Read All: %v/%m')
        self.read_progress.setVisible(False)
        self.read_backend_label = QtWidgets.QLabel('')
        self.read_backend_label.setVisible(False)
        self.cancel_read_all_btn = QtWidgets.QPushButton('Cancel Read All')
        self.cancel_read_all_btn.setAutoDefault(False)
        self.cancel_read_all_btn.setDefault(False)
        self.cancel_read_all_btn.setVisible(False)
        self.cancel_read_all_btn.clicked.connect(lambda _=False: self._request_read_all_cancel())
        progress_row.addWidget(self.read_progress, 1)
        progress_row.addWidget(self.read_backend_label, 0)
        progress_row.addWidget(self.cancel_read_all_btn, 0)
        layout.addLayout(progress_row)
        self._refresh_axis_pick_combo()

    def _log(self, msg):
        t = datetime.now().strftime('%H:%M:%S')
        self.log.appendPlainText(f'[{t}] {msg}')

    def _log_change(self, msg):
        t = datetime.now().strftime('%H:%M:%S')
        self.changes_log.appendPlainText(f'[{t}] {msg}')

    def _set_read_all_busy(self, busy, total=0):
        self._read_all_in_progress = bool(busy)
        self.read_all_btn.setEnabled(not busy)
        if hasattr(self, 'copy_read_to_set_btn'):
            self.copy_read_to_set_btn.setEnabled(not busy)
        if busy:
            self._read_all_cancel_requested = False
            self.read_progress.setMaximum(max(1, int(total)))
            self.read_progress.setValue(0)
            self.read_progress.setVisible(True)
            be = str(getattr(self.client, 'backend', '') or '').strip().lower()
            self.read_backend_label.setText('CLI (slow)' if be == 'cli' else 'EPICS')
            self.read_backend_label.setVisible(True)
            self.cancel_read_all_btn.setEnabled(True)
            self.cancel_read_all_btn.setVisible(True)
        else:
            self.read_progress.setVisible(False)
            self.read_progress.setValue(0)
            self.read_backend_label.setVisible(False)
            self.cancel_read_all_btn.setVisible(False)
            self.cancel_read_all_btn.setEnabled(False)
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 5)

    def _update_read_all_progress(self, done):
        if not self._read_all_in_progress:
            return
        self.read_progress.setValue(max(0, int(done)))
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 5)

    def _request_read_all_cancel(self):
        if not self._read_all_in_progress:
            return
        self._read_all_cancel_requested = True
        self.cancel_read_all_btn.setEnabled(False)
        self.cancel_read_all_btn.setText('Cancelling...')

    def _ioc_prefix_for_title(self):
        cmd_pv = self.cmd_pv.text().strip() if hasattr(self, 'cmd_pv') else ''
        m = re.match(r'^(.*):MCU-Cmd\.AOUT$', cmd_pv)
        return m.group(1) if m else self.title_prefix

    def _cli_caget_value(self, pv):
        if getattr(self.client, 'backend', None) != 'cli':
            return ''
        try:
            proc = subprocess.run(
                ['caget', '-t', '-w', str(float(getattr(self.client, 'timeout', 2.0))), pv],
                universal_newlines=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except Exception:
            return ''
        if proc.returncode != 0:
            return ''
        s = str(proc.stdout or '').strip()
        if not s:
            return ''
        if s.startswith(str(pv)):
            s = s[len(str(pv)):].strip()
        if ' SEVR:' in s:
            s = s.split(' SEVR:', 1)[0].rstrip()
        if ' STAT:' in s:
            s = s.split(' STAT:', 1)[0].rstrip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
            s = s[1:-1]
        return s.strip()

    def _get_pv_best_effort(self, pv, as_string=True):
        try:
            val = self.client.get(pv, as_string=as_string)
            txt = str(val or '').strip()
            if txt:
                return txt
        except Exception:
            pass
        return self._cli_caget_value(pv)

    def _combine_motor_record(self, axis_pfx, motor_name):
        a = str(axis_pfx or '').strip()
        m = str(motor_name or '').strip()
        if a and m:
            if m.startswith(a) or ':' in m:
                return m
            return f'{a}{m}' if a.endswith(':') else f'{a}:{m}'
        return a or m

    def _resolve_motor_record_name(self, axis_id):
        prefix = self._ioc_prefix_for_title()
        if not prefix:
            return ''
        a = str(axis_id or '').strip() or self.default_axis_id
        axis_pfx = ''
        motor_name = ''
        try:
            axis_pfx = str(self._get_pv_best_effort(_join_prefix_pv(prefix, f'MCU-Cfg-AX{a}-Pfx'), as_string=True)).strip().strip('"')
        except Exception:
            pass
        try:
            motor_name = str(self._get_pv_best_effort(_join_prefix_pv(prefix, f'MCU-Cfg-AX{a}-Nam'), as_string=True)).strip().strip('"')
        except Exception:
            pass
        return self._combine_motor_record(axis_pfx, motor_name)

    def _discover_axes_from_ioc(self):
        prefix = self._ioc_prefix_for_title()
        if not prefix:
            raise RuntimeError('Cannot determine IOC prefix from title/cmd PV')
        cur = _normalize_axis_object_id(self._get_pv_best_effort(_join_prefix_pv(prefix, 'MCU-Cfg-AX-FrstObjId'), as_string=True) or '')
        out = []
        seen = set()
        while cur and cur != '-1':
            axis_id = str(cur).strip()
            if not axis_id or axis_id in seen:
                break
            if not re.fullmatch(r'\d+', axis_id):
                self._log(f'Axis discovery: invalid object id "{axis_id}" from MCU-Cfg-AX-FrstObjId/NxtObjId; stopping traversal')
                break
            seen.add(axis_id)
            axis_pfx = ''
            motor_name = ''
            try:
                axis_pfx = str(self._get_pv_best_effort(_join_prefix_pv(prefix, f'MCU-Cfg-AX{axis_id}-Pfx'), as_string=True) or '').strip().strip('"')
            except Exception:
                pass
            try:
                motor_name = str(self._get_pv_best_effort(_join_prefix_pv(prefix, f'MCU-Cfg-AX{axis_id}-Nam'), as_string=True) or '').strip().strip('"')
            except Exception:
                pass
            out.append({
                'axis_id': axis_id,
                'motor': self._combine_motor_record(axis_pfx, motor_name),
                'motor_name': motor_name,
                'axis_type': '',
            })
            if out[-1]['motor']:
                try:
                    raw_type = self._get_pv_best_effort(f"{out[-1]['motor']}-Type", as_string=True) or ''
                    out[-1]['axis_type'] = _normalize_axis_type_text(raw_type)
                except Exception:
                    out[-1]['axis_type'] = ''
            cur = _normalize_axis_object_id(self._get_pv_best_effort(_join_prefix_pv(prefix, f'MCU-Cfg-AX{axis_id}-NxtObjId'), as_string=True) or '')
        return out

    def _resolve_axis_selector_to_id(self, selector):
        s = str(selector or '').strip()
        if not s:
            return ''
        if re.fullmatch(r'\d+', s):
            return s
        try:
            axes = self._discover_axes_from_ioc()
        except Exception:
            return ''
        want = s.lower()
        for ax in axes:
            axis_id = str(ax.get('axis_id', '') or '').strip()
            motor_name = str(ax.get('motor_name', '') or '').strip()
            motor = str(ax.get('motor', '') or '').strip()
            if want in {motor_name.lower(), motor.lower()}:
                return axis_id
            if motor and motor.split(':')[-1].lower() == want:
                return axis_id
        return ''

    def _open_axis_picker_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle('Select Axis')
        dlg.resize(520, 320)
        lay = QtWidgets.QVBoxLayout(dlg)
        info = QtWidgets.QLabel('Discovering axes from IOC configuration...')
        lay.addWidget(info)
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(['Axis ID', 'Type', 'Motor', 'Motor Name'])
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
        open_new_chk = QtWidgets.QCheckBox('Open New Instance')
        refresh_btn = QtWidgets.QPushButton('Refresh')
        select_btn = QtWidgets.QPushButton('Select')
        close_btn = QtWidgets.QPushButton('Close')
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
                info.setText(f'Axis discovery failed: {ex}')
                return
            info.setText(f'Found {len(axes)} axis(es)')
            cur_axis = self.axis_all_edit.text().strip() or self.default_axis_id
            sel_row = -1
            for r, ax in enumerate(axes):
                table.insertRow(r)
                axis_type = str(ax.get('axis_type', '') or '')
                type_disp = 'REAL' if axis_type.upper() == 'REAL' else ('Virtual' if axis_type else '?')
                is_virtual = (type_disp == 'Virtual')
                vals = [str(ax.get('axis_id', '') or ''), type_disp, str(ax.get('motor', '') or ''), str(ax.get('motor_name', '') or '')]
                for c, txt in enumerate(vals):
                    it = QtWidgets.QTableWidgetItem(txt)
                    if c in (0, 1):
                        it.setTextAlignment(QtCore.Qt.AlignCenter)
                    if is_virtual:
                        it.setForeground(QtGui.QBrush(QtGui.QColor('#9a9a9a')))
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
            type_item = table.item(r, 1)
            type_txt = (type_item.text().strip() if type_item is not None else '')
            if type_txt and type_txt.upper() != 'REAL':
                QtWidgets.QMessageBox.warning(self, 'Non-REAL Axis', 'Controller app does not allow selecting a virtual axis.')
                return
            axis_id = it.text().strip()
            if open_new_chk.isChecked():
                if not self._open_new_controller_window(axis_id=axis_id):
                    return
            else:
                self.axis_all_edit.setText(axis_id)
                self._apply_axis_all()
                if not self._startup_axis_probe_ok:
                    self._startup_axis_probe_ok = True
                    self._run_startup_after_axis_probe()
            dlg.accept()

        refresh_btn.clicked.connect(populate)
        select_btn.clicked.connect(apply_selected)
        close_btn.clicked.connect(dlg.reject)
        table.itemDoubleClicked.connect(lambda _it: apply_selected())
        populate()
        dlg.exec_()

    def _startup_axis_presence_check(self):
        if self.demo:
            self._did_startup_axis_presence_check = True
            self._startup_axis_probe_ok = True
            self._initial_read_all()
            return
        if self._did_startup_axis_presence_check:
            return
        self._did_startup_axis_presence_check = True
        prefix = self._ioc_prefix_for_title()
        cur_axis = self.axis_all_edit.text().strip() or self.default_axis_id
        if not self._axis_id_was_provided:
            first_axis = self._read_first_axis_id()
            if first_axis:
                if first_axis != cur_axis:
                    self._log(f'No startup axis provided, using first axis from IOC: {first_axis}')
                    self.axis_all_edit.setText(first_axis)
                    self._apply_axis_all()
                    cur_axis = first_axis
            else:
                self._log('No startup axis provided and first-axis discovery failed; opening axis picker')
                self._open_axis_picker_dialog()
                return
        resolved_id = self._resolve_axis_selector_to_id(cur_axis)
        if resolved_id and resolved_id != cur_axis:
            self._log(f'Axis selector "{cur_axis}" resolved to axis {resolved_id}')
            self.axis_all_edit.setText(resolved_id)
            self._apply_axis_all()
            cur_axis = resolved_id
        if not prefix:
            self._log('Startup axis probe skipped: IOC prefix unavailable; opening axis picker')
            self._open_axis_picker_dialog()
            return
        try:
            probe_pv = _join_prefix_pv(prefix, f'MCU-Cfg-AX{cur_axis}-Pfx')
            raw = self.client.get(probe_pv, as_string=True)
        except Exception as ex:
            self._log(f'Startup axis probe failed for axis {cur_axis}: {ex}; opening axis picker')
            self._open_axis_picker_dialog()
            return
        if str(raw or '').strip().strip('"'):
            self._startup_axis_probe_ok = True
            self._run_startup_after_axis_probe()
            return
        self._log(f'Axis {cur_axis} probe returned empty; opening axis picker')
        self._open_axis_picker_dialog()

    def _read_first_axis_id(self):
        prefix = self._ioc_prefix_for_title()
        if not prefix:
            return ''
        first_obj_pv = _join_prefix_pv(prefix, 'MCU-Cfg-AX-FrstObjId')
        try:
            raw = self._get_pv_best_effort(first_obj_pv, as_string=True)
            axis_id = _normalize_axis_object_id(raw)
            if axis_id and axis_id != '-1' and re.fullmatch(r'\d+', axis_id):
                return axis_id
        except Exception as ex:
            self._log(f'Failed reading first axis id from {first_obj_pv}: {ex}')
        return ''

    def _run_startup_after_axis_probe(self):
        self._update_window_title_with_motor()
        self._startup_real_axis_check()
        self._initial_read_all()

    def _update_window_title_with_motor(self):
        axis_id = self.axis_all_edit.text().strip() if hasattr(self, 'axis_all_edit') else self.default_axis_id
        try:
            motor = self._resolve_motor_record_name(axis_id)
        except Exception:
            motor = ''
        self.setWindowTitle(f'{self._base_title} [{motor}]' if motor else self._base_title)

    def _initial_read_all(self):
        if not self._startup_axis_probe_ok:
            return
        if self._did_initial_read_all:
            return
        if self._closing_due_to_non_real:
            return
        self._did_initial_read_all = True
        self._refresh_controller_context()
        try:
            if not self._ensure_axis_is_real(self.axis_all_edit.text().strip() or self.default_axis_id, purpose='startup controller read', close_on_fail=True):
                return
            if not self._read_all_rows():
                return
            self._copy_all_read_to_set()
        except Exception as ex:
            self._log(f'Initial Read All failed: {ex}')

    def _startup_real_axis_check(self):
        if not self._startup_axis_probe_ok:
            return
        if self._did_real_axis_startup_check:
            return
        self._did_real_axis_startup_check = True
        axis_id = self.axis_all_edit.text().strip() if hasattr(self, 'axis_all_edit') else self.default_axis_id
        if self._ensure_axis_is_real(axis_id or self.default_axis_id, purpose='open controller app', close_on_fail=False):
            return
        self._log('Opening axis picker because controller app started on a virtual/non-REAL axis')
        self._open_axis_picker_dialog()

    def _reset_open_app_combo(self):
        self.open_app_combo.blockSignals(True)
        self.open_app_combo.setCurrentIndex(0)
        self.open_app_combo.blockSignals(False)

    def _on_open_app_selected(self, index):
        action = str(self.open_app_combo.itemData(index) or '')
        try:
            if action == 'controller':
                self._open_new_controller_window()
            elif action == 'axis':
                self._open_axis_window()
            elif action == 'motion':
                self._open_motion_window()
            elif action == 'iso230':
                self._open_iso230_window()
            elif action == 'daq':
                self._open_daq_window()
            elif action == 'rtlog':
                self._open_rtlog_window()
            elif action == 'caqtdm_main':
                self._open_caqtdm_main_panel()
            elif action == 'caqtdm_axis':
                self._open_caqtdm_axis_panel()
        finally:
            self._reset_open_app_combo()

    def _open_new_controller_window(self, axis_id=None):
        target_axis = str(axis_id or self.axis_all_edit.text().strip() or self.default_axis_id).strip() or self.default_axis_id
        if not self._ensure_axis_is_real(target_axis, purpose='open controller app', close_on_fail=False):
            return False
        script = Path(__file__).with_name('start_cntrl.sh')
        if not script.exists():
            self._log(f'Launcher not found: {script.name}')
            return False
        prefix = self.title_prefix or ''
        if not prefix:
            cmd_pv = self.cmd_pv.text().strip()
            m = re.match(r'^(.*):MCU-Cmd\.AOUT$', cmd_pv)
            prefix = m.group(1) if m else 'IOC:ECMC'
        try:
            subprocess.Popen(
                ['bash', str(script), str(prefix), str(target_axis)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f'Started new controller window for axis {target_axis} (prefix {prefix})')
            return True
        except Exception as ex:
            self._log(f'Failed to start new controller window: {ex}')
            return False

    def _open_daq_window(self):
        prefix = self._ioc_prefix_for_title() or 'IOC:ECMC'
        try:
            base = self._resolve_motor_record_name(self.axis_all_edit.text().strip() or self.default_axis_id)
            pvs = axis_diagnostic_pvs(base, profile="controller", extra=())
            launch_tool("daq", prefix, *pvs)
            self._log(f"Started DAQ window (prefix {prefix}, {len(pvs)} preset PVs)")
        except Exception as ex:
            self._log(f"Failed to start DAQ window: {ex}")

    def _open_rtlog_window(self):
        script = Path(__file__).with_name('start_rtlog.sh')
        if not script.exists():
            self._log(f'Launcher not found: {script.name}')
            return
        prefix = self.title_prefix or ''
        if not prefix:
            cmd_pv = self.cmd_pv.text().strip()
            m = re.match(r'^(.*):MCU-Cmd\.AOUT$', cmd_pv)
            prefix = m.group(1) if m else 'IOC:ECMC'
        try:
            subprocess.Popen(
                ['bash', str(script), str(prefix), str(self.axis_all_edit.text().strip() or self.default_axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f'Started RT logger window (prefix {prefix})')
        except Exception as ex:
            self._log(f'Failed to start RT logger window: {ex}')

    def _motor_type_for_axis(self, axis_id):
        axis = str(axis_id or '').strip() or self.default_axis_id
        motor = self._resolve_motor_record_name(axis)
        if not motor:
            return '', ''
        try:
            v = self._get_pv_best_effort(f'{motor}-Type', as_string=True)
        except Exception:
            v = ''
        typ = _normalize_axis_type_text(v)
        return motor, typ

    def _ensure_axis_is_real(self, axis_id, purpose='controller command', close_on_fail=False):
        axis = str(axis_id or '').strip() or self.default_axis_id
        cached = self._axis_is_real_cache.get(axis)
        if cached is not None:
            return bool(cached)
        try:
            motor, typ = self._motor_type_for_axis(axis)
        except Exception as ex:
            self._log(f'Cannot verify axis type for axis {axis}: {ex}')
            self._axis_is_real_cache[axis] = True
            return True
        if str(typ).upper() == 'REAL':
            self._axis_is_real_cache[axis] = True
            return True
        if not str(typ).strip():
            self._axis_is_real_cache[axis] = True
            return True
        self._axis_is_real_cache[axis] = False
        motor_txt = f' [{motor}]' if motor else ''
        msg = f'Axis {axis}{motor_txt} is not REAL (Type={typ or "?"}); cannot {purpose}.'
        self._log(msg)
        try:
            QtWidgets.QMessageBox.warning(self, 'Non-REAL Axis', msg)
        except Exception:
            pass
        if close_on_fail:
            self._closing_due_to_non_real = True
            QtCore.QTimer.singleShot(0, self.close)
        return False

    def _open_motion_window(self):
        script = Path(__file__).with_name('start_mtn.sh')
        if not script.exists():
            self._log(f'Launcher not found: {script.name}')
            return
        axis_id = self.axis_all_edit.text().strip() or self.default_axis_id
        prefix = self.title_prefix or ''
        if not prefix:
            cmd_pv = self.cmd_pv.text().strip()
            m = re.match(r'^(.*):MCU-Cmd\.AOUT$', cmd_pv)
            prefix = m.group(1) if m else 'IOC:ECMC'
        try:
            subprocess.Popen(
                ['bash', str(script), str(prefix), str(axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f'Started motion window for axis {axis_id} (prefix {prefix})')
        except Exception as ex:
            self._log(f'Failed to start motion window: {ex}')

    def _open_axis_window(self):
        script = Path(__file__).with_name('start_axis.sh')
        if not script.exists():
            self._log(f'Launcher not found: {script.name}')
            return
        axis_id = self.axis_all_edit.text().strip() or self.default_axis_id
        prefix = self.title_prefix or ''
        if not prefix:
            cmd_pv = self.cmd_pv.text().strip()
            m = re.match(r'^(.*):MCU-Cmd\.AOUT$', cmd_pv)
            prefix = m.group(1) if m else 'IOC:ECMC'
        try:
            subprocess.Popen(
                ['bash', str(script), str(prefix), str(axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f'Started axis window for axis {axis_id} (prefix {prefix})')
        except Exception as ex:
            self._log(f'Failed to start axis window: {ex}')

    def _open_iso230_window(self):
        script = Path(__file__).with_name('start_iso230.sh')
        if not script.exists():
            self._log(f'Launcher not found: {script.name}')
            return
        axis_id = self.axis_all_edit.text().strip() or self.default_axis_id
        prefix = self.title_prefix or ''
        if not prefix:
            cmd_pv = self.cmd_pv.text().strip()
            m = re.match(r'^(.*):MCU-Cmd\.AOUT$', cmd_pv)
            prefix = m.group(1) if m else 'IOC:ECMC'
        try:
            subprocess.Popen(
                ['bash', str(script), str(prefix), str(axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f'Started ISO230 window for axis {axis_id} (prefix {prefix})')
        except Exception as ex:
            self._log(f'Failed to start ISO230 window: {ex}')

    def _open_caqtdm_axis_panel(self):
        axis_id = self.axis_all_edit.text().strip() or self.default_axis_id
        ioc_prefix = self._ioc_prefix_for_title() or ''
        motor_prefix = ''
        axis_name = ''
        try:
            raw = self.client.get(_join_prefix_pv(ioc_prefix, f'MCU-Cfg-AX{axis_id}-Pfx'), as_string=True)
            motor_prefix = str(raw or '').strip().strip('"')
        except Exception:
            motor_prefix = ''
        try:
            raw = self.client.get(_join_prefix_pv(ioc_prefix, f'MCU-Cfg-AX{axis_id}-Nam'), as_string=True)
            axis_name = str(raw or '').strip().strip('"')
        except Exception:
            axis_name = ''
        motor_base = self._resolve_motor_record_name(axis_id)
        if not motor_prefix and motor_base and ':' in motor_base:
            motor_prefix = motor_base.rsplit(':', 1)[0]
        if not axis_name and motor_base:
            axis_name = motor_base.rsplit(':', 1)[-1]
        motor_prefix = str(motor_prefix or '').rstrip(':')
        macro = f'DEV={motor_prefix},IOC={ioc_prefix},Axis={axis_name},AX_ID={axis_id}'
        try:
            launch_caqtdm('ecmcAxis.ui', macro)
            self._log(f'Started caQtDM axis panel ({macro})')
        except Exception as ex:
            self._log(f'Failed to start caQtDM axis panel: {ex}')

    def _open_caqtdm_main_panel(self):
        ioc_prefix = self._ioc_prefix_for_title() or ''
        macro = f'IOC={ioc_prefix}'
        try:
            launch_caqtdm('ecmcMain.ui', macro)
            self._log(f'Started caQtDM main panel ({macro})')
        except Exception as ex:
            self._log(f'Failed to start caQtDM main panel: {ex}')

    def _record_change(self, axis_id, key, value):
        axis = str(axis_id).strip() or self.default_axis_id
        if not key:
            return
        self._changes_by_axis.setdefault(axis, {})[str(key)] = str(value)

    def _record_current_value(self, axis_id, key, value):
        axis = str(axis_id).strip() or self.default_axis_id
        if not key:
            return
        self._current_values_by_axis.setdefault(axis, {})[str(key)] = str(value)

    def _cached_current_value(self, axis_id, key):
        axis = str(axis_id).strip() or self.default_axis_id
        if not key:
            return ''
        return str(self._current_values_by_axis.get(axis, {}).get(str(key), '') or '')

    def _seed_value_widgets_from_cache(self, row_def, axis_text, set_edit, read_edit):
        if not row_def:
            return
        name = str(row_def.get('name', '') or '')
        if not name:
            return
        cached = self._cached_current_value(axis_text, name)
        if read_edit is not None and read_edit.property('timeRepresentation') and self._unit_path(row_def, read_edit) in units.REVERSE:
            return
        if not cached:
            return
        # Fill readback if empty.
        if read_edit is not None and hasattr(read_edit, 'text') and not read_edit.text().strip():
            read_edit.setText(cached)
        # Fill set field if empty.
        if set_edit is not None and hasattr(set_edit, 'text') and not set_edit.text().strip():
            set_edit.setText(cached)
        # Sketch view uses same widget for set/read and needs a target marker for green match.
        if set_edit is read_edit and read_edit is not None and bool(read_edit.property('sketchValue')):
            read_edit.setProperty('lastReadbackText', compact_float_text(cached))
            read_edit.setProperty('lastWriteTargetText', compact_float_text(cached))
            self._update_value_match_visual(read_edit, read_edit)
        else:
            self._update_value_match_visual(set_edit, read_edit)

    def _yaml_scalar_text(self, value):
        s = str(value)
        low = s.lower()
        if low in {'true', 'false', 'null'}:
            return low
        try:
            float(s)
            return s
        except Exception:
            pass
        if re.fullmatch(r'0x[0-9a-fA-F]+', s):
            return f"'{s}'"
        if s == '' or ' ' in s or any(ch in s for ch in [':', '#', '[', ']', '{', '}', ',']) or s.strip() != s:
            return "'" + s.replace("'", "''") + "'"
        return s

    def _controller_yaml_key(self, cmd_name):
        m = {
            'AxisCntrlResetIAtRmp': 'controller.resetIAtRmp',
            'AxisCntrlFreezeIAtRmp': 'controller.freezeIAtRmp',
            'AxisMonSlvCtrlDbTol': 'monitoring.target.slaveControlDeadband.tolerance',
            'AxisMonSlvCtrlDbTime': 'monitoring.target.slaveControlDeadband.time',
            'AxisMonEnableAtTargetMon': 'monitoring.target.enable',
            'AxisMonEnableLagMon': 'monitoring.lag.enable',
            'AxisMonPosLagTol': 'monitoring.lag.tolerance',
            'AxisMonPosLagTime': 'monitoring.lag.time',
            'AxisCntrlKp': 'controller.Kp',
            'AxisCntrlKi': 'controller.Ki',
            'AxisCntrlKd': 'controller.Kd',
            'AxisCntrlKff': 'controller.Kff',
            'AxisCntrlDeadband': 'controller.deadband.tol',
            'AxisCntrlDeadbandTime': 'controller.deadband.time',
            'AxisCntrlOutLL': 'controller.limits.minOutput',
            'AxisCntrlOutHL': 'controller.limits.maxOutput',
            'AxisCntrlIPartLL': 'controller.limits.minIntegral',
            'AxisCntrlIPartHL': 'controller.limits.maxIntegral',
            'AxisCntrlInnerKp': 'controller.inner.Kp',
            'AxisCntrlInnerKi': 'controller.inner.Ki',
            'AxisCntrlInnerKd': 'controller.inner.Kd',
            'AxisCntrlInnerTol': 'controller.inner.tol',
            'AxisDrvScaleNum': 'drive.numerator',
            'AxisDrvScaleDenom': 'drive.denominator',
            'AxisEncScaleNum': 'encoder.numerator',
            'AxisEncScaleDenom': 'encoder.denominator',
            'AxisMonAtTargetTol': 'monitoring.target.tolerance',
            'AxisMonAtTargetTime': 'monitoring.target.time',
        }
        return m.get(cmd_name, f'commands.{cmd_name}')

    def _controller_yaml_relevant_names(self):
        names = []
        for row_def in self.rows_all:
            name = str(row_def.get('name', '')).strip()
            if not name:
                continue
            path = self._controller_yaml_key(name)
            if path.startswith(('controller.', 'monitoring.target.', 'monitoring.lag.', 'drive.', 'encoder.')):
                names.append(name)
        return names

    def _build_yaml_text_from_flat(
        self,
        axis_id,
        flat,
        title,
        changed_paths=None,
        motor_name=None,
        include_axis_group_metadata=True,
    ):
        flat = dict(flat or {})
        changed_paths = set(changed_paths or [])
        if not flat:
            return f'# No values available for axis {axis_id}\n'
        motor_name = str(motor_name or "").strip()
        motor_comment = f" [{motor_name}]" if motor_name else ""
        tree = {}
        paths = {self._controller_yaml_key(k): v for k,v in flat.items()}
        current = {self._controller_yaml_key(k): v for k,v in self._current_values_by_axis.get(str(axis_id), {}).items()}
        if self.pid_units.isChecked() or self.time_units.isChecked():
            paths, changed_paths = units.export_values(paths, current, self._sample_rate(), self.pid_units.isChecked(), self.time_units.isChecked(), changed_paths)
        for path, value in sorted(paths.items()):
            cur = tree
            parts = [p for p in path.split('.') if p]
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
        if include_axis_group_metadata:
            # Ensure axis group is present as lower-case key and keep it first in output.
            axis_key = None
            for k in tree.keys():
                if str(k).lower() == "axis":
                    axis_key = k
                    break
            if axis_key is None:
                axis_key = "axis"
                new_tree = {axis_key: {}}
                new_tree.update(tree)
                tree = new_tree
            axis_node = tree.get(axis_key)
            if not isinstance(axis_node, dict):
                axis_node = {"value": axis_node}
                tree[axis_key] = axis_node
            axis_node = {"id": self._yaml_scalar_text(axis_id), **{k: axis_node[k] for k in axis_node if k != "id"}}
            tree[axis_key] = axis_node

        lines = [f'# {title} for axis {axis_id}{motor_comment}']

        def emit(node, indent=0, prefix=''):
            pad = ' ' * indent
            for k, v in node.items():
                path = f'{prefix}.{k}' if prefix else k
                if isinstance(v, dict):
                    lines.append(f'{pad}{k}:')
                    emit(v, indent + 2, path)
                else:
                    line = f'{pad}{k}: {self._yaml_scalar_text(v)}'
                    if path in changed_paths:
                        line += '  # CHANGED'
                    lines.append(line)

        emit(tree, 0, '')
        return '\n'.join(lines) + '\n'

    def _build_changed_yaml_text(self, axis_id, motor_name=None):
        axis = str(axis_id).strip()
        allowed = set(self._controller_yaml_relevant_names())
        current_changes = {
            k: v for k, v in self._changes_by_axis.get(axis, {}).items() if k in allowed
        }
        return self._build_yaml_text_from_flat(axis, current_changes, 'Changed controller values', motor_name=motor_name, include_axis_group_metadata=False)

    def _build_all_current_yaml_text(self, axis_id, motor_name=None):
        axis = str(axis_id).strip()
        allowed = set(self._controller_yaml_relevant_names())
        current = {
            k: v
            for k, v in self._current_values_by_axis.get(axis, {}).items()
            if k in allowed
        }
        for k, v in self._changes_by_axis.get(axis, {}).items():
            if k in allowed:
                current.setdefault(k, v)
        for name in allowed:
            current.setdefault(name, 'null')
        changed_paths = {
            self._controller_yaml_key(k)
            for k in self._changes_by_axis.get(axis, {}).keys()
            if k in allowed
        }
        return self._build_yaml_text_from_flat(
            axis_id,
            current,
            'Current controller values (session-known)',
            motor_name=motor_name,
            changed_paths=changed_paths,
        )

    def _show_changed_yaml_window(self):
        axis_id = self.axis_all_edit.text().strip() or self.default_axis_id
        try:
            motor_name = str(self._resolve_motor_record_name(axis_id) or "").strip()
        except Exception:
            motor_name = ""
        suffix = f" [{motor_name}]" if motor_name else ""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f'Changed YAML (Axis {axis_id}){suffix}')
        dlg.resize(640, 520)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel(f'Session controller writes for axis: {axis_id}'))
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel('View'))
        mode_combo = QtWidgets.QComboBox()
        mode_combo.addItems(['Changed fields', 'All fields (current)'])
        mode_row.addWidget(mode_combo)
        mode_row.addStretch(1)
        lay.addLayout(mode_row)
        txt = QtWidgets.QPlainTextEdit()
        txt.setReadOnly(True)
        lay.addWidget(txt, 1)

        def refresh_text():
            if mode_combo.currentIndex() == 0:
                txt.setPlainText(self._build_changed_yaml_text(axis_id, motor_name=motor_name))
            else:
                txt.setPlainText(self._build_all_current_yaml_text(axis_id, motor_name=motor_name))

        mode_combo.currentIndexChanged.connect(lambda _=0: refresh_text())
        refresh_text()
        btn_row = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton('Copy')
        copy_btn.setAutoDefault(False)
        copy_btn.setDefault(False)
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(txt.toPlainText()))
        close_btn = QtWidgets.QPushButton('Close')
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)
        dlg.exec_()

    def _show_yaml_window(self):
        axis_id = self.axis_all_edit.text().strip() or self.default_axis_id
        try:
            motor_name = str(self._resolve_motor_record_name(axis_id) or "").strip()
        except Exception:
            motor_name = ""
        suffix = f" [{motor_name}]" if motor_name else ""
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f'YAML (Axis {axis_id}){suffix}')
        dlg.resize(640, 520)
        lay = QtWidgets.QVBoxLayout(dlg)
        lay.addWidget(QtWidgets.QLabel('Current controller YAML values (session-known).'))
        filter_row = QtWidgets.QHBoxLayout()
        changed_only_chk = QtWidgets.QCheckBox('Show only changed values')
        filter_row.addWidget(changed_only_chk)
        filter_row.addStretch(1)
        lay.addLayout(filter_row)
        txt = QtWidgets.QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        def refresh_text():
            if changed_only_chk.isChecked():
                txt.setPlainText(self._build_changed_yaml_text(axis_id, motor_name=motor_name))
            else:
                txt.setPlainText(self._build_all_current_yaml_text(axis_id, motor_name=motor_name))
        changed_only_chk.toggled.connect(refresh_text)
        refresh_text()
        lay.addWidget(txt, 1)
        btn_row = QtWidgets.QHBoxLayout()
        copy_btn = QtWidgets.QPushButton('Copy')
        copy_btn.setAutoDefault(False)
        copy_btn.setDefault(False)
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(txt.toPlainText()))
        close_btn = QtWidgets.QPushButton('Close')
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)
        dlg.exec_()

    def _filtered_rows(self):
        if self.view_mode.currentText() in {'Diagram', 'Controller Sketch'}:
            return self.rows
        txt = self.search.text().strip().lower()
        if not txt:
            return self.rows
        return [r for r in self.rows if txt in r['name'].lower()]

    def _on_view_mode_changed(self, _mode=''):
        self._populate_table()
        try:
            if self._read_all_rows():
                self._copy_all_read_to_set()
        except Exception as ex:
            self._log(f'View-change Read/Copy failed: {ex}')

    def _populate_table(self):
        mode = self.view_mode.currentText()
        if mode != 'Controller Sketch':
            self._stop_live()
        self.live_check.setEnabled(mode == 'Controller Sketch')
        if hasattr(self, 'search'):
            self.search.setVisible(mode not in {'Diagram', 'Controller Sketch'})
        if mode == 'Diagram':
            self.stack.setCurrentIndex(1)
            self._populate_diagram()
            return
        if mode == 'Controller Sketch':
            self.stack.setCurrentIndex(1)
            self._populate_controller_sketch()
            return

        self.stack.setCurrentIndex(0)
        data = self._filtered_rows()
        self.table.setRowCount(0)
        if self.view_mode.currentText() == 'Schematic':
            order = {
                'PID Core': 0,
                'Inner Loop PID': 1,
                'Integrator Limits': 2,
                'Output Limits': 3,
                'At Target Monitor': 4,
                'Scaling': 5,
                'Other': 6,
            }
            data = sorted(data, key=lambda r: (order.get(r.get('group', 'Other'), 99), r['name'].lower()))
            current_group = None
            for row_def in data:
                group = row_def.get('group', 'Other')
                if group != current_group:
                    self._insert_group_row(group)
                    current_group = group
                self._insert_command_row(row_def)
            return

        for row_def in sorted(data, key=lambda r: r['name'].lower()):
            self._insert_command_row(row_def)


    def _clear_diagram_layout(self):
        self._controller_scene = None
        self._stop_live()
        self._diagram_read_rows = []
        self._diagram_value_pairs = []
        for i in reversed(range(self.diagram_layout.count())):
            item = self.diagram_layout.itemAt(i)
            w = item.widget()
            if w is not None:
                self.diagram_layout.removeWidget(w)
                w.hide()
                w.deleteLater()

    def _row_def(self, name):
        return self._rows_all_by_name.get(name)

    def _command_tooltip(self, row_def):
        if not row_def:
            return ''
        return f"GET: {row_def.get('get') or '-'}\nSET: {row_def.get('set') or '-'}"

    def _make_sketch_cell(self, row_def):
        cell = QtWidgets.QWidget()
        cl = QtWidgets.QHBoxLayout(cell)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(1)

        edit = QtWidgets.QLineEdit('')
        edit.setFixedSize(60, 19)
        edit.setAlignment(QtCore.Qt.AlignCenter)
        base_style = (
            'QLineEdit {'
            ' border: 2px solid #0f3345;'
            ' background: #efefef;'
            ' color: #111;'
            ' font-size: 13px;'
            '}'
        )
        edit.setStyleSheet(base_style)
        edit.setProperty('sketchValue', True)
        edit.setProperty('timeRepresentation', True)
        edit.setProperty('sketchBaseStyle', base_style)
        edit.textChanged.connect(lambda _txt='', e=edit: self._on_sketch_value_text_changed(e))
        edit.setToolTip(self._command_tooltip(row_def))

        rb = QtWidgets.QPushButton('R')
        wb = QtWidgets.QPushButton('W')
        for b in (rb, wb):
            b.setAutoDefault(False)
            b.setDefault(False)
            b.setFixedSize(14, 19)
            b.setStyleSheet(
                'QPushButton {'
                ' border: 1px solid #0f3345;'
                ' background: #e6eef2;'
                ' font-size: 8px;'
                ' font-weight: 700;'
                '}'
            )
            b.setToolTip(self._command_tooltip(row_def))

        if row_def is None:
            edit.setEnabled(False)
            rb.setEnabled(False)
            wb.setEnabled(False)
        else:
            rb.setEnabled(bool(row_def.get('get')))
            wb.setEnabled(bool(row_def.get('set')))
            rb.clicked.connect(lambda _=False, rd=row_def, e=edit: self._read_row(rd, self.axis_all_edit, e))
            wb.clicked.connect(lambda _=False, rd=row_def, e=edit: self._write_row(rd, self.axis_all_edit, e, e))
            if row_def.get('set'):
                edit.returnPressed.connect(lambda rd=row_def, e=edit: self._write_row(rd, self.axis_all_edit, e, e))
            if row_def.get('get'):
                self._diagram_read_rows.append((row_def, edit))
            self._diagram_value_pairs.append((edit, edit))
            self._seed_value_widgets_from_cache(row_def, self.axis_all_edit.text(), edit, edit)

        cl.addWidget(edit)
        cl.addWidget(rb)
        cl.addWidget(wb)
        cell.setFixedSize(90, 19)
        return cell

    def _sample_rate(self):
        prefix = self._ioc_prefix_for_title()
        try:
            hz = units.rate(self._get_pv_best_effort(_join_prefix_pv(prefix, 'MCU-Cfg-Rate')))
            self.rate_label.setText(f'{hz:g} Hz')
            return hz
        except (ValueError, TypeError):
            self.rate_label.setText('Rate: unavailable')
            return None

    def _unit_path(self, row_def, widget):
        old = self._controller_yaml_key(row_def['name'])
        if not widget.property('timeRepresentation'):
            return old
        return units.display_path(old, self.pid_units.isChecked(), self.time_units.isChecked())

    def _pid_kp(self, axis, path):
        name = 'AxisCntrlInnerKp' if path.startswith('controller.inner.') else 'AxisCntrlKp'
        ok, msg = self.read_raw_command(f'Get{name}({axis})', quiet=True)
        if not ok:
            raise ValueError('Kp read failed')
        return units.number(msg.split(': ', 1)[1])

    def _convert_value(self, row_def, widget, axis, value, write=False):
        path = self._unit_path(row_def, widget)
        if path not in units.REVERSE:
            return value
        hz = self._sample_rate()
        kp = self._pid_kp(axis, path) if units.REVERSE[path] in units.PID else None
        return (units.to_runtime if write else units.from_runtime)(path, value, hz, kp)

    def _context_identity(self):
        return (self.cmd_pv.text().strip(), self.axis_all_edit.text().strip() or self.default_axis_id)

    def _refresh_controller_context(self):
        if not self._startup_axis_probe_ok or self._closing_due_to_non_real:
            return
        ident = self._context_identity()
        if ident == self._controller_mode_identity:
            return
        self._stop_live()
        self._controller_mode_identity = ident
        self._controller_mode = 'UNKNOWN'
        self._controller_base = self._resolve_motor_record_name(ident[1])
        # Prefer the configured mode PV. Older IOC configurations can lack it.
        if self._controller_base:
            try:
                mode = str(self.client.get(self._controller_base+'-DrvMode', as_string=True)).strip().upper()
                if mode in ('CSV','CSP'):
                    self._controller_mode = mode
            except Exception:
                pass
        if self._controller_mode == 'UNKNOWN':
            # One-time compatibility fallback, never part of live polling.
            ok, msg = self.read_raw_command(f'GetAxisDrvMode({ident[1]})', quiet=True)
            if ok and ': ' in msg:
                self._controller_mode = {'0':'CSV','1':'CSP'}.get(msg.rsplit(': ',1)[1].strip(),'UNKNOWN')
        if self.view_mode.currentText() == 'Controller Sketch':
            self._populate_controller_sketch()

    def _populate_controller_sketch(self):
        self._clear_diagram_layout()
        from ecmc_controller_diagram import ControllerDiagram
        self._controller_scene = ControllerDiagram(self._make_sketch_cell, self._row_def,
            self.pid_units.isChecked(), self.time_units.isChecked(), mode=self._controller_mode)
        self.diagram_layout.addWidget(self._controller_scene, 0, 0)
        if self.live_check.isChecked():
            self._toggle_live(True)

    def _stop_live(self):
        self._live_timer.stop()
        if self._live_monitor is not None:
            self._live_monitor.close()
            self._live_monitor = None
        self._live_identity = None
        self._live_qualified_since = None
        if self._controller_scene is not None:
            self._controller_scene.clear_live()

    def _toggle_live(self, checked):
        self._stop_live()
        if not checked or not self._startup_axis_probe_ok or self.view_mode.currentText() != 'Controller Sketch':
            return
        ident = self._context_identity()
        if ident != self._controller_mode_identity:
            self._refresh_controller_context()
            return
        base = getattr(self, '_controller_base', '')
        if not base:
            self._controller_scene.set_live_message('Axis PV prefix unavailable')
            return
        self._live_monitor = LivePVMonitor(self.client, base, demo=self.demo)
        self._live_identity = ident
        if self._live_monitor.reason:
            self._controller_scene.set_live_message(self._live_monitor.reason)
            return
        self._live_rate = self._sample_rate()
        self._live_timer.start()
        self._update_live()

    def _update_live(self):
        if self._live_monitor is None or self._controller_scene is None:
            return
        if self._context_identity() != self._live_identity:
            self._stop_live()
            return
        values = self._live_monitor.snapshot()
        axis = self._live_identity[1]
        cfg = dict(self._current_values_by_axis.get(axis, {}))
        cfg['sample_rate'] = self._live_rate
        now = time.monotonic()
        _, condition = derive(values, cfg, self._controller_mode, None)
        if condition == 'qualifying':
            if self._live_qualified_since is None:
                self._live_qualified_since = now
        else:
            self._live_qualified_since = None
        elapsed = now-self._live_qualified_since if self._live_qualified_since is not None else None
        inner, deadband = derive(values, cfg, self._controller_mode, elapsed)
        self._controller_scene.update_live(values, inner, deadband)

    def _open_tuning_window(self):
        if self._tuning_window is not None and self._tuning_window.isVisible():
            self._tuning_window.raise_()
            self._tuning_window.activateWindow()
            return
        if not self._startup_axis_probe_ok:
            return
        axis = self.axis_all_edit.text().strip() or self.default_axis_id
        if not self._ensure_axis_is_real(axis, purpose='controller tuning'):
            return
        base = self._resolve_motor_record_name(axis)
        if not base:
            self._log('Tuning: axis PV prefix unavailable')
            return
        from ecmc_tuning_qt import TuningWindow
        def read_config():
            if (self.axis_all_edit.text().strip() or self.default_axis_id) != axis or self._resolve_motor_record_name(axis) != base:
                raise RuntimeError('Controller selection changed; reopen tuning for this axis')
            values = {}
            for key, name in (
                ('lo','GetAxisSoftLimitPosBwd'), ('hi','GetAxisSoftLimitPosFwd'),
                ('lo_enabled','GetAxisEnableSoftLimitBwd'), ('hi_enabled','GetAxisEnableSoftLimitFwd'),
                ('vmax','GetAxisMonMaxVel'), ('vmax_enabled','GetAxisMonEnableMaxVel')):
                ok, reply = self.read_raw_command(f'{name}({axis})', quiet=True)
                if not ok:
                    raise RuntimeError(f'Cannot read native scan limits: {name}')
                values[key] = float(reply.rsplit(': ',1)[1])
            return values
        identity = self._context_identity()
        def read_lag():
            if self._context_identity() != identity:
                return None
            try:
                enabled = float(self._cached_current_value(axis, 'AxisMonEnableLagMon'))
                tol = float(self._cached_current_value(axis, 'AxisMonPosLagTol'))
                return tol if enabled == 1 and 0 < tol < float('inf') else None
            except (TypeError, ValueError):
                return None
        def jerk_context():
            if self._context_identity()!=identity or self._resolve_motor_record_name(axis)!=base:
                raise RuntimeError('Controller selection changed; reopen tuning for this axis')
        def read_jerk():
            jerk_context()
            ok, reply = self.read_raw_command(f'GetAxisJerk({axis})', quiet=True)
            if not ok:
                raise RuntimeError(reply)
            return float(reply.rsplit(': ',1)[1])
        def write_jerk(value):
            jerk_context()
            ok, reply = self.read_raw_command(f'Cfg.SetAxisJerk({axis},{value:.17g})', quiet=True)
            if not ok:
                raise RuntimeError(reply)
            actual=read_jerk()
            if abs(actual-value)>max(1e-12,abs(value)*1e-8):
                raise RuntimeError(f'Jerk readback mismatch: {actual:g} != {value:g}')
        self._tuning_window = TuningWindow(self, self.client, base, demo=self.demo,
            read_config=read_config, read_lag=read_lag, read_jerk=read_jerk, write_jerk=write_jerk)
        self._tuning_window.show()

    def closeEvent(self, event):
        if self._tuning_window is not None and self._tuning_window.isVisible():
            if not self._tuning_window.close():
                event.ignore()
                return
        self._stop_live()
        super().closeEvent(event)

    def _toggle_log_visible(self, checked):
        self.log.setVisible(bool(checked))
        self.log_toggle_btn.setText('Hide Log' if checked else 'Show Log')

    def _toggle_changes_log_visible(self, checked):
        self.changes_log.setVisible(bool(checked))
        self.changes_toggle_btn.setText('Hide Changes' if checked else 'Show Changes')

    def _populate_diagram(self):
        self._diagram_read_rows = []
        self._diagram_value_pairs = []
        for i in reversed(range(self.diagram_layout.count())):
            item = self.diagram_layout.itemAt(i)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Signal flow row: Setpoint -> Sum -> PID -> Limiter -> Drive Scale -> Process
        flow = QtWidgets.QWidget()
        fl = QtWidgets.QHBoxLayout(flow)
        fl.setContentsMargins(4, 4, 4, 4)
        fl.setSpacing(6)
        fl.addWidget(self._make_flow_block('Setpoint', '#dff4ff'))
        fl.addWidget(QtWidgets.QLabel('-->'))
        fl.addWidget(self._make_flow_block('Sum (+,-)', '#fff8dc'))
        fl.addWidget(QtWidgets.QLabel('-->'))
        fl.addWidget(self._make_flow_block('PID Controller', '#e8ffe8'))
        fl.addWidget(QtWidgets.QLabel('-->'))
        fl.addWidget(self._make_flow_block('Limiter', '#ffe8e8'))
        fl.addWidget(QtWidgets.QLabel('-->'))
        fl.addWidget(self._make_flow_block('Drive Scale', '#f1ecff'))
        fl.addWidget(QtWidgets.QLabel('-->'))
        fl.addWidget(self._make_flow_block('Process', '#f5f5f5'))
        self.diagram_layout.addWidget(flow, 0, 0, 1, 2)

        # Feedback hint row.
        fb = QtWidgets.QLabel('Feedback: Process --> Encoder Scale --> Sum (-)')
        fb.setStyleSheet('QLabel { color: #444; font-style: italic; padding-left: 8px; }')
        self.diagram_layout.addWidget(fb, 1, 0, 1, 2)

        used = set()
        self.diagram_layout.addWidget(
            self._make_param_panel(
                'PID + Inner Loop',
                [
                    'AxisCntrlKp', 'AxisCntrlKi', 'AxisCntrlKd', 'AxisCntrlKff',
                    'AxisCntrlDeadband', 'AxisCntrlDeadbandTime',
                    'AxisCntrlInnerKp', 'AxisCntrlInnerKi', 'AxisCntrlInnerKd',
                    'AxisCntrlInnerTol',
                ],
                used,
            ),
            2, 0
        )
        self.diagram_layout.addWidget(
            self._make_param_panel(
                'Limiter + Integrator',
                [
                    'AxisCntrlOutHL', 'AxisCntrlOutLL',
                    'AxisCntrlIPartHL', 'AxisCntrlIPartLL',
                ],
                used,
            ),
            2, 1
        )
        self.diagram_layout.addWidget(
            self._make_param_panel(
                'Scaling',
                ['AxisDrvScaleNum', 'AxisDrvScaleDenom', 'AxisEncScaleNum', 'AxisEncScaleDenom'],
                used,
            ),
            3, 0
        )
        self.diagram_layout.addWidget(
            self._make_param_panel(
                'At Target',
                ['AxisMonAtTargetTol', 'AxisMonAtTargetTime', 'AxisMonEnableAtTargetMon'],
                used,
            ),
            3, 1
        )

        leftovers = sorted(
            [n for n, rd in self._rows_all_by_name.items() if n not in used and rd.get('get')],
            key=str.lower,
        )
        if leftovers:
            self.diagram_layout.addWidget(self._make_param_panel('Other', leftovers, used), 4, 0, 1, 2)

    def _make_flow_block(self, title, color):
        w = QtWidgets.QFrame()
        w.setFrameShape(QtWidgets.QFrame.StyledPanel)
        w.setStyleSheet(
            'QFrame {'
            f' background: {color};'
            ' border: 1px solid #888;'
            ' border-radius: 6px;'
            ' padding: 6px;'
            '}'
        )
        l = QtWidgets.QVBoxLayout(w)
        l.setContentsMargins(8, 6, 8, 6)
        lbl = QtWidgets.QLabel(title)
        lbl.setAlignment(QtCore.Qt.AlignCenter)
        lbl.setStyleSheet('QLabel { font-weight: 700; }')
        l.addWidget(lbl)
        return w

    def _make_param_panel(self, title, names, used):
        box = QtWidgets.QGroupBox(title)
        lay = QtWidgets.QGridLayout(box)
        lay.setHorizontalSpacing(6)
        lay.setVerticalSpacing(4)
        lay.addWidget(QtWidgets.QLabel('Parameter'), 0, 0)
        lay.addWidget(QtWidgets.QLabel('Set'), 0, 1)
        lay.addWidget(QtWidgets.QLabel('W'), 0, 2)
        lay.addWidget(QtWidgets.QLabel('Read'), 0, 3)
        lay.addWidget(QtWidgets.QLabel('R'), 0, 4)

        r = 1
        for n in names:
            row_def = self._rows_all_by_name.get(n)
            if not row_def:
                continue
            if not row_def.get('get'):
                continue
            used.add(n)
            label = QtWidgets.QLabel(n.replace('Axis', ''))
            set_edit = QtWidgets.QLineEdit('')
            set_edit.setPlaceholderText('value[,value...]')
            read_edit = QtWidgets.QLineEdit('')
            read_edit.setReadOnly(True)
            read_btn = QtWidgets.QPushButton('R')
            write_btn = QtWidgets.QPushButton('W')
            for b in (read_btn, write_btn):
                b.setAutoDefault(False)
                b.setDefault(False)
                b.setMaximumWidth(30)
            read_btn.setEnabled(bool(row_def.get('get')))
            write_btn.setEnabled(bool(row_def.get('set')))

            read_btn.clicked.connect(lambda _=False, rd=row_def, rv=read_edit: self._read_row(rd, self.axis_all_edit, rv))
            write_btn.clicked.connect(lambda _=False, rd=row_def, sv=set_edit, rv=read_edit: self._write_row(rd, self.axis_all_edit, sv, rv))
            if row_def.get('get'):
                self._diagram_read_rows.append((row_def, read_edit))
            self._diagram_value_pairs.append((set_edit, read_edit))
            self._seed_value_widgets_from_cache(row_def, self.axis_all_edit.text(), set_edit, read_edit)

            lay.addWidget(label, r, 0)
            lay.addWidget(set_edit, r, 1)
            lay.addWidget(write_btn, r, 2)
            lay.addWidget(read_edit, r, 3)
            lay.addWidget(read_btn, r, 4)
            r += 1
        return box

    def _insert_group_row(self, title):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setSpan(r, 0, 1, 6)
        item = QtWidgets.QTableWidgetItem(f'[{title}]')
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setFlags(QtCore.Qt.ItemIsEnabled)
        item.setBackground(QtCore.Qt.lightGray)
        self.table.setItem(r, 0, item)
        self.table.setRowHeight(r, 26)

    def _insert_command_row(self, row_def):
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QtWidgets.QTableWidgetItem(row_def['name'])
        item.setToolTip(self._command_tooltip(row_def))
        self.table.setItem(r, 0, item)

        axis_default = self.axis_all_edit.text().strip() if hasattr(self, 'axis_all_edit') else ''
        axis = QtWidgets.QLineEdit(axis_default or self.default_axis_id)
        axis.setMaximumWidth(70)
        self.table.setCellWidget(r, 1, axis)

        set_val = QtWidgets.QLineEdit('')
        set_val.setPlaceholderText('value or comma-separated values')
        self.table.setCellWidget(r, 2, set_val)

        read_btn = QtWidgets.QPushButton('Read')
        write_btn = QtWidgets.QPushButton('Write')
        read_btn.setAutoDefault(False)
        read_btn.setDefault(False)
        write_btn.setAutoDefault(False)
        write_btn.setDefault(False)
        self.table.setCellWidget(r, 3, write_btn)

        read_val = QtWidgets.QLineEdit('')
        read_val.setReadOnly(True)
        self.table.setCellWidget(r, 4, read_val)
        self.table.setCellWidget(r, 5, read_btn)

        read_btn.setEnabled(bool(row_def.get('get')))
        write_btn.setEnabled(bool(row_def.get('set')))

        read_btn.clicked.connect(lambda _=False, rd=row_def, ax=axis, rv=read_val: self._read_row(rd, ax, rv))
        write_btn.clicked.connect(lambda _=False, rd=row_def, ax=axis, sv=set_val, rv=read_val: self._write_row(rd, ax, sv, rv))
        self._seed_value_widgets_from_cache(row_def, axis.text(), set_val, read_val)

    def _apply_axis_all(self):
        axis_value = self.axis_all_edit.text().strip()
        if not axis_value:
            self._log('Axis All is empty')
            return
        updated = 0
        for r in range(self.table.rowCount()):
            axis_edit = self.table.cellWidget(r, 1)
            if axis_edit is None:
                continue
            axis_edit.setText(axis_value)
            updated += 1
        self._log(f'Applied axis {axis_value} to {updated} rows')
        self._sync_axis_combo_to_axis_id(axis_value)
        self._update_window_title_with_motor()
        self._refresh_controller_context()
        if self._startup_axis_probe_ok and not self._closing_due_to_non_real:
            try:
                if self._read_all_rows():
                    self._copy_all_read_to_set()
            except Exception as ex:
                self._log(f'Axis change read/copy failed: {ex}')

    def _sync_axis_combo_to_axis_id(self, axis_id):
        if not hasattr(self, 'axis_pick_combo'):
            return
        want = str(axis_id or '').strip()
        if not want:
            return
        idx = self.axis_pick_combo.findData(want, role=QtCore.Qt.UserRole)
        if idx >= 0:
            self._axis_combo_updating = True
            self.axis_pick_combo.setCurrentIndex(idx)
            self._axis_combo_updating = False

    def _axis_combo_install_open_new_item(self):
        if not hasattr(self, 'axis_pick_combo') or self.axis_pick_combo.count() <= 0:
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
        if not hasattr(self, 'axis_pick_combo'):
            return
        current_axis = self.axis_all_edit.text().strip() if hasattr(self, 'axis_all_edit') else self.default_axis_id
        self._axis_combo_updating = True
        self.axis_pick_combo.clear()
        self.axis_pick_combo.addItem('Open New Instance', '__open_new__')
        self._axis_combo_install_open_new_item()
        self.axis_pick_combo.addItem(f'Axis {current_axis}', current_axis)
        try:
            axes = self._discover_axes_from_ioc()
        except Exception:
            self._axis_combo_updating = False
            return
        self.axis_pick_combo.clear()
        self.axis_pick_combo.addItem('Open New Instance', '__open_new__')
        self._axis_combo_install_open_new_item()
        for ax in axes:
            axis_id = str(ax.get('axis_id', '') or '').strip()
            axis_type = str(ax.get('axis_type', '') or '')
            motor_name = str(ax.get('motor_name', '') or '')
            tdisp = 'REAL' if axis_type.upper() == 'REAL' else ('Virtual' if axis_type else '?')
            label = f'{axis_id} | {tdisp}'
            if motor_name:
                label += f' | {motor_name}'
            self.axis_pick_combo.addItem(label, axis_id)
            i = self.axis_pick_combo.count() - 1
            self.axis_pick_combo.setItemData(i, axis_type, QtCore.Qt.UserRole + 1)
            if tdisp == 'Virtual':
                self.axis_pick_combo.setItemData(i, QtGui.QBrush(QtGui.QColor('#9a9a9a')), QtCore.Qt.ForegroundRole)
        if self.axis_pick_combo.count() <= 1:
            self.axis_pick_combo.addItem(f'Axis {current_axis}', current_axis)
        self._axis_combo_updating = False
        self._sync_axis_combo_to_axis_id(current_axis)

    def _on_axis_combo_activated(self, _index):
        if self._axis_combo_updating:
            return
        axis_id = str(self.axis_pick_combo.currentData(QtCore.Qt.UserRole) or '').strip()
        if axis_id == '__open_new__':
            self._axis_combo_toggle_open_new_item()
            self._sync_axis_combo_to_axis_id(self.axis_all_edit.text().strip() or self.default_axis_id)
            QtCore.QTimer.singleShot(0, self.axis_pick_combo.showPopup)
            return
        if not axis_id:
            return
        axis_type = str(self.axis_pick_combo.currentData(QtCore.Qt.UserRole + 1) or '').strip()
        if axis_type and axis_type.upper() != 'REAL':
            QtWidgets.QMessageBox.warning(self, 'Non-REAL Axis', 'Controller app does not allow selecting a virtual axis.')
            self._sync_axis_combo_to_axis_id(self.axis_all_edit.text().strip() or self.default_axis_id)
            return
        if self._axis_combo_open_new_instance:
            self._open_new_controller_window(axis_id=axis_id)
            self._sync_axis_combo_to_axis_id(self.axis_all_edit.text().strip() or self.default_axis_id)
            return
        self.axis_all_edit.setText(axis_id)
        self._apply_axis_all()
        if not self._startup_axis_probe_ok:
            self._startup_axis_probe_ok = True
            self._run_startup_after_axis_probe()

    def _prompt_axis_selection_via_combo(self, reason_msg=None):
        if reason_msg:
            self._log(reason_msg)
        if hasattr(self, 'pv_cfg_toggle') and not self.pv_cfg_toggle.isChecked():
            self.pv_cfg_toggle.setChecked(True)
        self._refresh_axis_pick_combo()
        try:
            self.axis_pick_combo.setFocus(QtCore.Qt.OtherFocusReason)
        except Exception:
            pass
        QtCore.QTimer.singleShot(0, self.axis_pick_combo.showPopup)

    def _read_all_rows(self):
        if self._read_all_in_progress:
            return False
        if not self._ensure_axis_is_real(self.axis_all_edit.text().strip() or self.default_axis_id, purpose='read controller values'):
            return False
        if self.view_mode.currentText() in {'Diagram', 'Controller Sketch'}:
            rows = [(rd, self.axis_all_edit, re) for rd, re in self._diagram_read_rows if rd and rd.get('get')]
        else:
            rows = []
            by_name = {r.get('name'): r for r in self.rows}
            for r in range(self.table.rowCount()):
                name_item = self.table.item(r, 0)
                axis_edit = self.table.cellWidget(r, 1)
                read_edit = self.table.cellWidget(r, 4)
                if name_item is None or axis_edit is None or read_edit is None:
                    continue
                name = name_item.text().strip()
                row_def = by_name.get(name)
                if not row_def or not row_def.get('get'):
                    continue
                rows.append((row_def, axis_edit, read_edit))

        self._sample_rate()
        count = 0
        self._set_read_all_busy(True, total=len(rows))
        try:
            for row_def, axis_edit, read_edit in rows:
                if self._read_all_cancel_requested:
                    self._log(f'Read All cancelled ({count}/{len(rows)})')
                    return False
                if not self._read_row(row_def, axis_edit, read_edit, quiet=True):
                    self._log(f'Read unavailable: {row_def.get("name", "?")} (continuing)')
                    continue
                count += 1
                self._update_read_all_progress(count)
            self._log(f'Read All completed ({count} rows)')
            return True
        finally:
            self._set_read_all_busy(False)
            self.cancel_read_all_btn.setText('Cancel Read All')

    def _copy_all_read_to_set(self):
        copied = 0
        if self.view_mode.currentText() in {'Diagram', 'Controller Sketch'}:
            for set_edit, read_edit in self._diagram_value_pairs:
                if set_edit is None or read_edit is None:
                    continue
                val = read_edit.text().strip()
                if not val:
                    continue
                set_edit.setText(val)
                if set_edit is read_edit and bool(read_edit.property('sketchValue')):
                    read_edit.setProperty('lastWriteTargetText', compact_float_text(val))
                self._update_value_match_visual(set_edit, read_edit)
                copied += 1
            self._log(f'Copied readback to set fields ({copied} rows)')
            return

        for r in range(self.table.rowCount()):
            set_edit = self.table.cellWidget(r, 2)
            read_edit = self.table.cellWidget(r, 4)
            if set_edit is None or read_edit is None:
                continue
            val = read_edit.text().strip()
            if not val:
                continue
            set_edit.setText(val)
            self._update_value_match_visual(set_edit, read_edit)
            copied += 1
        self._log(f'Copied readback to set fields ({copied} rows)')

    def _cmd_from_template(self, template, axis_text, value_text):
        ph = _template_args(template)
        args = []
        if ph:
            args.append(axis_text.strip())
        args.extend(_split_csv(value_text))
        cmd, err = _replace_placeholders(template, args)
        if err:
            return '', f'{err} for template {template}'
        return normalize_float_literals(cmd.strip()), ''

    def _values_match_text(self, a, b):
        sa = str(a or '').strip()
        sb = str(b or '').strip()
        if not sa or not sb:
            return False
        if sa == sb:
            return True
        try:
            return float(sa) == float(sb)
        except Exception:
            return False

    def _set_sketch_value_style(self, widget, matched):
        if widget is None or not bool(widget.property('sketchValue')):
            return
        base = widget.property('sketchBaseStyle')
        if not base:
            return
        if matched:
            font_sz = '13px'
            widget.setStyleSheet(
                'QLineEdit {'
                ' border: 2px solid #9fbe95;'
                ' background: #d8ead2;'
                ' color: #173b17;'
                f' font-size: {font_sz};'
                ' font-weight: 700;'
                '}'
            )
        else:
            widget.setStyleSheet(str(base))

    def _set_sketch_pending_style(self, widget):
        if widget is None or not bool(widget.property('sketchValue')):
            return
        font_sz = '13px'
        widget.setStyleSheet(
            'QLineEdit {'
            ' border: 2px solid #d3a6a6;'
            ' background: #f6d6d6;'
            ' color: #4a1212;'
            f' font-size: {font_sz};'
            ' font-weight: 700;'
            '}'
        )

    def _on_sketch_value_text_changed(self, widget):
        if widget is None or not bool(widget.property('sketchValue')):
            return
        txt = widget.text().strip()
        last_read = str(widget.property('lastReadbackText') or '').strip()
        if last_read and txt and not self._values_match_text(txt, last_read):
            self._set_sketch_pending_style(widget)
            return
        # Fall back to green/base state based on confirmed match state.
        self._update_value_match_visual(widget, widget)

    def _update_value_match_visual(self, set_edit, read_edit):
        # Table/diagram rows compare set vs read. Sketch cells use a single widget,
        # so compare readback against the last value attempted to write.
        if read_edit is None:
            return
        if set_edit is read_edit and bool(read_edit.property('sketchValue')):
            target = str(read_edit.property('lastWriteTargetText') or '').strip()
            matched = self._values_match_text(target, read_edit.text())
            self._set_sketch_value_style(read_edit, matched)
            return
        matched = self._values_match_text(getattr(set_edit, 'text', lambda: '')(), getattr(read_edit, 'text', lambda: '')())
        self._set_sketch_value_style(read_edit, matched)

    def send_raw_command(self, cmd, quiet=False, wait=True):
        pv = self.cmd_pv.text().strip()
        cmd = normalize_float_literals((cmd or '').strip())
        if not pv:
            return False, 'ERROR: Command PV is empty'
        if not cmd:
            return False, 'ERROR: Command text is empty'
        try:
            CommandClient(self.client, pv, "").send(cmd, wait=bool(wait))
            msg = f'CMD -> {pv} ({len(cmd)} chars): {cmd}'
            if not quiet:
                self._log(msg)
            return True, msg
        except Exception as ex:
            msg = f'ERROR sending command ({len(cmd)} chars): {ex} | CMD={cmd}'
            if not quiet:
                self._log(msg)
            return False, msg

    def read_raw_command(self, cmd, quiet=False):
        pv = self.cmd_pv.text().strip()
        if not pv:
            return False, "ERROR: Command PV is empty"
        with command_transaction(pv):
            ok, msg = self.send_raw_command(cmd, quiet=quiet, wait=True)
            if not ok:
                return False, msg
            qp = self.qry_pv.text().strip()
            if not qp:
                return True, f'Command sent, no QRY PV configured: {cmd}'
            try:
                proc_pv = _proc_pv_for_readback(qp)
                self.client.put(proc_pv, 1, wait=True)
                val = self.client.get(qp, as_string=True)
                if query_value_indicates_error(val):
                    short = summarize_error_text(val, self.error_name_by_code)
                    msg = f'QRY ERROR <- {qp}: {short}'
                    if not quiet:
                        self._log(msg)
                    return False, msg
                msg = f'QRY <- {qp}: {val}'
                if not quiet:
                    self._log(msg)
                return True, msg
            except Exception as ex:
                msg = f'ERROR query read: {ex}'
                if not quiet:
                    self._log(msg)
                return False, msg

    def _read_row(self, row_def, axis_edit, read_edit, quiet=False):
        if not self._ensure_axis_is_real(axis_edit.text(), purpose=f'read {row_def.get("name","controller value")}'):
            if read_edit is not None:
                read_edit.setText('Axis Type != REAL')
            return False
        cmd, err = self._cmd_from_template(row_def.get('get', ''), axis_edit.text(), '')
        if err:
            read_edit.setText(err)
            self._set_sketch_value_style(read_edit, False)
            return False
        ok, msg = self.read_raw_command(cmd, quiet=quiet)
        if ok and ': ' in msg:
            val = msg.split(': ', 1)[1].strip()
            raw_value = compact_float_text(val)
            try:
                disp_val = self._convert_value(row_def, read_edit, axis_edit.text(), raw_value)
            except (ValueError, TypeError, OverflowError) as ex:
                read_edit.setText('N/A')
                self._log(str(ex))
                return False
            if bool(read_edit.property('sketchValue')):
                read_edit.setProperty('lastReadbackText', disp_val)
            read_edit.setText(disp_val)
            self._record_current_value(axis_edit.text().strip() or self.default_axis_id, row_def.get('name', ''), raw_value)
            if bool(read_edit.property('sketchValue')):
                self._update_value_match_visual(read_edit, read_edit)
            return True
        else:
            read_edit.setText(summarize_error_text(msg, self.error_name_by_code))
            self._set_sketch_value_style(read_edit, False)
            return False

    def _write_row(self, row_def, axis_edit, set_edit, read_edit):
        if not self._ensure_axis_is_real(axis_edit.text(), purpose=f'write {row_def.get("name","controller value")}'):
            if read_edit is not None:
                read_edit.setText('Axis Type != REAL')
            self._set_sketch_value_style(read_edit, False)
            return
        set_txt = set_edit.text().strip()
        try:
            runtime_value = self._convert_value(row_def, set_edit, axis_edit.text(), set_txt, write=True)
        except (ValueError, TypeError, OverflowError) as ex:
            self._log(str(ex))
            return
        cmd, err = self._cmd_from_template(row_def.get('set', ''), axis_edit.text(), runtime_value)
        if err:
            read_edit.setText(err)
            self._set_sketch_value_style(read_edit, False)
            return
        ok, msg = self.send_raw_command(cmd)
        if not ok:
            read_edit.setText(msg)
            self._set_sketch_value_style(read_edit, False)
            return
        axis_id = axis_edit.text().strip() or self.default_axis_id
        self._record_change(axis_id, row_def.get('name', ''), runtime_value)
        if not row_def.get('get'):
            self._record_current_value(axis_id, row_def.get('name', ''), runtime_value)
        self._log_change(
            f"WRITE axis={axis_id} cmd={row_def.get('name','')} value={set_txt} | {cmd}"
        )
        if bool(read_edit.property('sketchValue')):
            read_edit.setProperty('lastWriteTargetText', compact_float_text(set_txt))
        else:
            self._update_value_match_visual(set_edit, read_edit)
        # Auto-read after write so the displayed value reflects what is now active.
        if row_def.get('get'):
            self._read_row(row_def, axis_edit, read_edit)
            return
        read_edit.setText('OK')


def main():
    ap = argparse.ArgumentParser(description='Qt app for ecmc controller tuning commands')
    ap.add_argument('--catalog', default='ecmc_commands_cntrl.json', help='Path to filtered command catalog JSON')
    ap.add_argument('--prefix', default='', help='PV prefix (e.g. IOC:ECMC)')
    ap.add_argument('--cmd-pv', default='', help='Command PV name (overrides --prefix)')
    ap.add_argument('--qry-pv', default='', help='Readback PV name (overrides --prefix)')
    ap.add_argument('--axis-id', default='', help='Default axis id for Axis All')
    ap.add_argument('--timeout', type=float, default=2.0, help='EPICS timeout in seconds')
    ap.add_argument('--error-db', default='', help='Path to local error DB JSON (default: ecmc_error_codes.json)')
    ap.add_argument('--demo', action='store_true', help='Offline demo with simulated configuration values')
    args = ap.parse_args()
    if args.demo:
        args.prefix = 'DEMO'
        args.cmd_pv = ''
        args.qry_pv = ''
        args.axis_id = '1'

    default_cmd_pv = args.cmd_pv.strip() if args.cmd_pv else _join_prefix_pv(args.prefix, 'MCU-Cmd.AOUT')
    default_qry_pv = args.qry_pv.strip() if args.qry_pv else _join_prefix_pv(args.prefix, 'MCU-Cmd.AINP')

    app = QtWidgets.QApplication(sys.argv)
    w = CntrlWindow(
        catalog_path=args.catalog,
        default_cmd_pv=default_cmd_pv,
        default_qry_pv=default_qry_pv,
        timeout=args.timeout,
        default_axis_id=args.axis_id,
        title_prefix=args.prefix,
        error_db_path=args.error_db,
        axis_id_was_provided=bool((args.axis_id or '').strip()),
        demo=args.demo,
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
