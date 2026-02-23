#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore

from ecmc_stream_qt import (
    EpicsClient,
    _join_prefix_pv,
    _proc_pv_for_readback,
    compact_float_text,
    normalize_float_literals,
)


PLACEHOLDER_RE = re.compile(r'<[^>]+>')


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


class ImageOverlayCanvas(QtWidgets.QWidget):
    def __init__(self, image_path):
        super().__init__()
        self._pixmap = QtGui.QPixmap(str(image_path)) if image_path else QtGui.QPixmap()
        self._base_w = self._pixmap.width() if not self._pixmap.isNull() else 1920
        self._base_h = self._pixmap.height() if not self._pixmap.isNull() else 1080
        self._items = []
        self._widget_index = {}
        self._calibration_enabled = False
        self._drag_widget = None
        self._drag_offset = QtCore.QPoint(0, 0)
        self.setMinimumHeight(560)

    def has_image(self):
        return not self._pixmap.isNull()

    def add_overlay_widget(self, rel_x, rel_y, widget, anchor='center', name=''):
        widget.setParent(self)
        widget.setProperty('overlayName', str(name or ''))
        widget.show()
        idx = len(self._items)
        self._items.append([float(rel_x), float(rel_y), widget, str(anchor)])
        self._widget_index[widget] = idx
        widget.installEventFilter(self)
        self._layout_items()

    def set_calibration_enabled(self, enabled):
        self._calibration_enabled = bool(enabled)
        # During calibration, child controls must not consume mouse events,
        # otherwise drag gestures never reach the overlay cell.
        for _x, _y, widget, _a in self._items:
            if not bool(widget.property('overlayCell')):
                continue
            for ch in widget.findChildren(QtWidgets.QWidget):
                ch.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, self._calibration_enabled)

    def overlay_positions(self):
        out = {}
        for rel_x, rel_y, widget, _anchor in self._items:
            name = str(widget.property('overlayName') or '').strip()
            if name:
                out[name] = [float(rel_x), float(rel_y)]
        return out

    def _target_rect(self):
        if self._base_w <= 0 or self._base_h <= 0:
            return QtCore.QRect(0, 0, self.width(), self.height())
        wr = float(self.width()) / float(self._base_w)
        hr = float(self.height()) / float(self._base_h)
        s = min(wr, hr)
        tw = int(self._base_w * s)
        th = int(self._base_h * s)
        x = (self.width() - tw) // 2
        y = (self.height() - th) // 2
        return QtCore.QRect(x, y, tw, th)

    def _layout_items(self):
        rect = self._target_rect()
        scale = min(float(rect.width()) / float(self._base_w), float(rect.height()) / float(self._base_h))
        for rel_x, rel_y, widget, anchor in self._items:
            x = int(rect.x() + rel_x * rect.width())
            y = int(rect.y() + rel_y * rect.height())
            hint = widget.sizeHint()
            if bool(widget.property('overlayCell')):
                # Keep overlay boxes fixed size; only position is image-relative.
                ww = int(widget.property('overlayBaseW') or hint.width())
                wh = int(widget.property('overlayBaseH') or hint.height())
            else:
                min_w = 30
                min_h = 18
                ww = max(min_w, int(hint.width() * scale))
                wh = max(min_h, int(hint.height() * scale))
            widget.resize(ww, wh)
            if anchor == 'left':
                widget.move(x, y - wh // 2)
            elif anchor == 'right':
                widget.move(x - ww, y - wh // 2)
            else:
                widget.move(x - ww // 2, y - wh // 2)

    def _event_global_point(self, event):
        gp = getattr(event, 'globalPos', None)
        if callable(gp):
            return gp()
        gp2 = getattr(event, 'globalPosition', None)
        if callable(gp2):
            return gp2().toPoint()
        return QtCore.QPoint(0, 0)

    def eventFilter(self, obj, event):
        if obj not in self._widget_index:
            return super().eventFilter(obj, event)
        if not self._calibration_enabled:
            return super().eventFilter(obj, event)

        et = event.type()
        if et == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
            self._drag_widget = obj
            self._drag_offset = event.pos()
            return True
        if et == QtCore.QEvent.MouseMove and self._drag_widget is obj and (event.buttons() & QtCore.Qt.LeftButton):
            idx = self._widget_index.get(obj)
            if idx is None:
                return True
            rel_x, rel_y, _w, anchor = self._items[idx]
            rect = self._target_rect()
            g = self._event_global_point(event)
            p = self.mapFromGlobal(g) - self._drag_offset
            ww = max(1, obj.width())
            wh = max(1, obj.height())
            if anchor == 'left':
                cx = p.x()
                cy = p.y() + wh // 2
            elif anchor == 'right':
                cx = p.x() + ww
                cy = p.y() + wh // 2
            else:
                cx = p.x() + ww // 2
                cy = p.y() + wh // 2
            if rect.width() > 0 and rect.height() > 0:
                rel_x = float(cx - rect.x()) / float(rect.width())
                rel_y = float(cy - rect.y()) / float(rect.height())
                rel_x = max(0.0, min(1.0, rel_x))
                rel_y = max(0.0, min(1.0, rel_y))
                self._items[idx][0] = rel_x
                self._items[idx][1] = rel_y
                self._layout_items()
            return True
        if et == QtCore.QEvent.MouseButtonRelease and self._drag_widget is obj:
            self._drag_widget = None
            return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_items()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor('#d9d9d9'))
        if not self._pixmap.isNull():
            p.drawPixmap(self._target_rect(), self._pixmap)


class CntrlWindow(QtWidgets.QMainWindow):
    def __init__(self, catalog_path, default_cmd_pv, default_qry_pv, timeout, default_axis_id='1', title_prefix='', sketch_image_path=''):
        super().__init__()
        p = str(title_prefix or '').strip()
        self.setWindowTitle(f'ecmc PID/Controller Tuning [{p}]' if p else 'ecmc PID/Controller Tuning')
        self.resize(920, 620)
        self.client = EpicsClient(timeout=timeout)
        self.catalog = self._load_catalog(catalog_path)
        self.rows = _build_pairs(self.catalog.get('commands', []), include_set_only=False)
        self.rows_all = _build_pairs(self.catalog.get('commands', []), include_set_only=True)
        self._rows_all_by_name = {r['name']: r for r in self.rows_all}
        self._diagram_read_rows = []
        self._diagram_value_pairs = []
        self._changes_by_axis = {}
        self._current_values_by_axis = {}
        self.default_axis_id = str(default_axis_id).strip() or '1'
        self.title_prefix = p
        self.sketch_image_path = str(sketch_image_path or '').strip()
        self._did_initial_read_all = False
        self._build_ui(default_cmd_pv, default_qry_pv, timeout)
        self._populate_table()
        self._log(f'Connected via backend: {self.client.backend}')
        QtCore.QTimer.singleShot(0, self._initial_read_all)

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

        cfg_group = QtWidgets.QGroupBox('General Configuration')
        cfg = QtWidgets.QGridLayout(cfg_group)
        self.cmd_pv = QtWidgets.QLineEdit(default_cmd_pv)
        self.qry_pv = QtWidgets.QLineEdit(default_qry_pv)
        self.timeout_edit = QtWidgets.QDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(timeout)
        self.timeout_edit.valueChanged.connect(lambda v: setattr(self.client, 'timeout', float(v)))
        cfg.addWidget(QtWidgets.QLabel('Command PV'), 0, 0)
        cfg.addWidget(self.cmd_pv, 0, 1)
        cfg.addWidget(QtWidgets.QLabel('Readback PV'), 1, 0)
        cfg.addWidget(self.qry_pv, 1, 1)
        cfg.addWidget(QtWidgets.QLabel('Timeout [s]'), 2, 0)
        cfg.addWidget(self.timeout_edit, 2, 1)

        tools_group = QtWidgets.QGroupBox('Layout Tools')
        tools_layout = QtWidgets.QVBoxLayout(tools_group)
        tools_layout.setContentsMargins(8, 8, 8, 8)
        path_row = QtWidgets.QHBoxLayout()
        self.sketch_image_edit = QtWidgets.QLineEdit(self.sketch_image_path)
        self.sketch_image_edit.editingFinished.connect(self._update_sketch_image)
        path_row.addWidget(QtWidgets.QLabel('Sketch Image'))
        path_row.addWidget(self.sketch_image_edit, stretch=1)
        tools_layout.addLayout(path_row)
        cfg_tools = QtWidgets.QHBoxLayout()
        self.calibrate_btn = QtWidgets.QPushButton('Calibrate')
        self.calibrate_btn.setCheckable(True)
        self.calibrate_btn.setAutoDefault(False)
        self.calibrate_btn.setDefault(False)
        self.calibrate_btn.toggled.connect(self._toggle_calibration)
        cfg_tools.addWidget(self.calibrate_btn)
        self.save_layout_btn = QtWidgets.QPushButton('Save Layout')
        self.save_layout_btn.setAutoDefault(False)
        self.save_layout_btn.setDefault(False)
        self.save_layout_btn.clicked.connect(self._save_current_layout)
        cfg_tools.addWidget(self.save_layout_btn)
        cfg_tools.addStretch(1)
        tools_layout.addLayout(cfg_tools)
        tools_layout.addStretch(1)

        cfg_panel = QtWidgets.QWidget()
        cfg_panel_layout = QtWidgets.QHBoxLayout(cfg_panel)
        cfg_panel_layout.setContentsMargins(0, 0, 0, 0)
        cfg_panel_layout.setSpacing(10)
        cfg_panel_layout.addWidget(cfg_group, stretch=1)
        cfg_panel_layout.addWidget(tools_group, stretch=0)
        cfg_panel.setVisible(False)

        top_row = QtWidgets.QHBoxLayout()
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
        top_row.addWidget(self.log_toggle_btn)
        self.changes_toggle_btn = QtWidgets.QPushButton('Show Changes')
        self.changes_toggle_btn.setCheckable(True)
        self.changes_toggle_btn.setChecked(False)
        self.changes_toggle_btn.setAutoDefault(False)
        self.changes_toggle_btn.setDefault(False)
        self.changes_toggle_btn.toggled.connect(self._toggle_changes_log_visible)
        top_row.addWidget(self.changes_toggle_btn)
        self.changed_yaml_btn = QtWidgets.QPushButton('Show Changed YAML')
        self.changed_yaml_btn.setAutoDefault(False)
        self.changed_yaml_btn.setDefault(False)
        self.changed_yaml_btn.clicked.connect(self._show_changed_yaml_window)
        top_row.addWidget(self.changed_yaml_btn)
        self.open_motion_btn = QtWidgets.QPushButton('Open Motion')
        self.open_motion_btn.setAutoDefault(False)
        self.open_motion_btn.setDefault(False)
        self.open_motion_btn.clicked.connect(self._open_motion_window)
        top_row.addWidget(self.open_motion_btn)
        top_row.addStretch(1)
        layout.addLayout(top_row)
        layout.addWidget(cfg_panel)

        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Filter commands...')
        self.search.textChanged.connect(self._populate_table)
        search_row.addWidget(self.search)
        search_row.addWidget(QtWidgets.QLabel('View'))
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(['Flat', 'Schematic', 'Diagram', 'Controller Sketch'])
        self.view_mode.setCurrentText('Controller Sketch')
        self.view_mode.currentTextChanged.connect(self._populate_table)
        search_row.addWidget(self.view_mode)
        search_row.addWidget(QtWidgets.QLabel('Axis All'))
        self.axis_all_edit = QtWidgets.QLineEdit(self.default_axis_id)
        self.axis_all_edit.setMaximumWidth(70)
        self.axis_all_edit.returnPressed.connect(self._apply_axis_all)
        search_row.addWidget(self.axis_all_edit)
        self.axis_all_btn = QtWidgets.QPushButton('Apply Axis')
        self.axis_all_btn.setAutoDefault(False)
        self.axis_all_btn.setDefault(False)
        self.axis_all_btn.clicked.connect(self._apply_axis_all)
        search_row.addWidget(self.axis_all_btn)
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
        layout.addWidget(self.log, stretch=0)
        self.changes_log = QtWidgets.QPlainTextEdit()
        self.changes_log.setReadOnly(True)
        self.changes_log.setVisible(False)
        self.changes_log.setPlaceholderText('Successful writes are tracked here for this session...')
        layout.addWidget(self.changes_log, stretch=0)

    def _log(self, msg):
        t = datetime.now().strftime('%H:%M:%S')
        self.log.appendPlainText(f'[{t}] {msg}')

    def _log_change(self, msg):
        t = datetime.now().strftime('%H:%M:%S')
        self.changes_log.appendPlainText(f'[{t}] {msg}')

    def _initial_read_all(self):
        if self._did_initial_read_all:
            return
        self._did_initial_read_all = True
        try:
            self._read_all_rows()
            self._copy_all_read_to_set()
        except Exception as ex:
            self._log(f'Initial Read All failed: {ex}')

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

    def _build_yaml_text_from_flat(self, axis_id, flat, title, changed_paths=None):
        flat = dict(flat or {})
        changed_paths = set(changed_paths or [])
        if not flat:
            return f'# No values available for axis {axis_id}\n'
        tree = {}
        for name, value in sorted(flat.items()):
            path = self._controller_yaml_key(name)
            cur = tree
            parts = [p for p in path.split('.') if p]
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value
        lines = [f'# {title} for axis {axis_id}', f'axisId: {self._yaml_scalar_text(axis_id)}']

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

    def _build_changed_yaml_text(self, axis_id):
        return self._build_yaml_text_from_flat(axis_id, self._changes_by_axis.get(str(axis_id).strip(), {}), 'Changed controller values')

    def _build_all_current_yaml_text(self, axis_id):
        axis = str(axis_id).strip()
        current = dict(self._current_values_by_axis.get(axis, {}))
        for k, v in self._changes_by_axis.get(axis, {}).items():
            current.setdefault(k, v)
        for row_def in self.rows_all:
            name = str(row_def.get('name', '')).strip()
            if name:
                current.setdefault(name, 'null')
        changed_paths = {self._controller_yaml_key(k) for k in self._changes_by_axis.get(axis, {}).keys()}
        return self._build_yaml_text_from_flat(
            axis_id,
            current,
            'Current controller values (session-known)',
            changed_paths=changed_paths,
        )

    def _show_changed_yaml_window(self):
        axis_id = self.axis_all_edit.text().strip() or self.default_axis_id
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f'Changed YAML (Axis {axis_id})')
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
                txt.setPlainText(self._build_changed_yaml_text(axis_id))
            else:
                txt.setPlainText(self._build_all_current_yaml_text(axis_id))

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

    def _filtered_rows(self):
        txt = self.search.text().strip().lower()
        if not txt:
            return self.rows
        return [r for r in self.rows if txt in r['name'].lower()]

    def _populate_table(self):
        mode = self.view_mode.currentText()
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
        self._diagram_read_rows = []
        self._diagram_value_pairs = []
        for i in reversed(range(self.diagram_layout.count())):
            item = self.diagram_layout.itemAt(i)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _row_def(self, name):
        return self._rows_all_by_name.get(name)

    def _make_sketch_cell(self, row_def, overlay=False):
        cell = QtWidgets.QWidget()
        if overlay:
            cell.setProperty('overlayCell', True)
            cell.setFixedSize(76, 28)
            cell.setProperty('overlayBaseW', 76)
            cell.setProperty('overlayBaseH', 28)
        cl = QtWidgets.QHBoxLayout(cell)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(1 if overlay else 2)

        edit = QtWidgets.QLineEdit('')
        if overlay:
            edit.setFixedSize(62, 28)
        else:
            edit.setFixedSize(96, 34)
        edit.setAlignment(QtCore.Qt.AlignCenter)
        base_style = (
            'QLineEdit {'
            ' border: 2px solid #0f3345;'
            ' background: #efefef;'
            ' color: #111;'
            f' font-size: {"11px" if overlay else "13px"};'
            '}'
        )
        edit.setStyleSheet(base_style)
        edit.setProperty('sketchValue', True)
        edit.setProperty('sketchOverlay', bool(overlay))
        edit.setProperty('sketchBaseStyle', base_style)
        edit.textChanged.connect(lambda _txt='', e=edit: self._on_sketch_value_text_changed(e))

        rb = QtWidgets.QPushButton('R')
        wb = QtWidgets.QPushButton('W')
        for b in (rb, wb):
            b.setAutoDefault(False)
            b.setDefault(False)
            if overlay:
                b.setFixedSize(14, 13)
            else:
                b.setFixedSize(20, 16)
            b.setStyleSheet(
                'QPushButton {'
                ' border: 1px solid #0f3345;'
                ' background: #e6eef2;'
                f' font-size: {"7px" if overlay else "8px"};'
                ' font-weight: 700;'
                '}'
            )

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
        btn_col = QtWidgets.QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(1)
        btn_col.addWidget(rb)
        btn_col.addWidget(wb)
        btn_col.addStretch(1)
        cl.addLayout(btn_col)
        return cell

    def _populate_controller_sketch(self):
        self._clear_diagram_layout()
        image_path = self.sketch_image_edit.text().strip() if hasattr(self, 'sketch_image_edit') else self.sketch_image_path
        if image_path:
            p = Path(image_path)
            if p.exists():
                self._populate_controller_sketch_overlay(str(p))
                return

        sketch = QtWidgets.QWidget()
        sketch.setStyleSheet('QWidget { background: #e1e1e1; color: #1e1e1e; }')
        grid = QtWidgets.QGridLayout(sketch)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(14)

        def _t(text, css=''):
            w = QtWidgets.QLabel(text)
            if css:
                w.setStyleSheet(css)
            return w

        hdr = _t('PID sets:', 'QLabel { font-size: 26px; font-weight: 700; color: #111; }')
        grid.addWidget(hdr, 0, 0, 1, 2)

        grid.addWidget(_t('outer PID: e >', 'QLabel { font-size: 18px; }'), 1, 0)
        grid.addWidget(_t('inner PID: e <', 'QLabel { font-size: 18px; }'), 2, 0)
        grid.addWidget(_t('tol.', 'QLabel { font-size: 18px; }'), 1, 1)
        grid.addWidget(self._make_sketch_cell(self._row_def('AxisCntrlInnerTol')), 2, 1)

        gains = QtWidgets.QWidget()
        gl = QtWidgets.QGridLayout(gains)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setHorizontalSpacing(10)
        gl.setVerticalSpacing(8)
        gl.addWidget(_t('outer', 'QLabel { font-size: 18px; font-weight: 600; }'), 0, 1)
        gl.addWidget(_t('inner', 'QLabel { font-size: 18px; font-weight: 600; }'), 0, 2)
        for rr, (lbl, outer, inner) in enumerate([
            ('Kp', 'AxisCntrlKp', 'AxisCntrlInnerKp'),
            ('Ki', 'AxisCntrlKi', 'AxisCntrlInnerKi'),
            ('Kd', 'AxisCntrlKd', 'AxisCntrlInnerKd'),
        ], start=1):
            gl.addWidget(_t(lbl, 'QLabel { font-size: 18px; font-weight: 600; }'), rr, 0)
            gl.addWidget(self._make_sketch_cell(self._row_def(outer)), rr, 1)
            gl.addWidget(self._make_sketch_cell(self._row_def(inner)), rr, 2)
        grid.addWidget(gains, 0, 2, 3, 2)

        ff_col = QtWidgets.QWidget()
        fl = QtWidgets.QGridLayout(ff_col)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setHorizontalSpacing(8)
        fl.setVerticalSpacing(8)
        fl.addWidget(_t('ff', 'QLabel { font-size: 18px; font-weight: 600; }'), 0, 0)
        fl.addWidget(_t('drv. scale', 'QLabel { font-size: 18px; font-weight: 600; }'), 1, 0)
        fl.addWidget(_t('Kff', 'QLabel { font-size: 18px; font-weight: 600; }'), 2, 0)
        fl.addWidget(self._make_sketch_cell(self._row_def('AxisCntrlKff')), 2, 1)
        fl.addWidget(_t('denom', 'QLabel { font-size: 18px; }'), 1, 2)
        fl.addWidget(self._make_sketch_cell(self._row_def('AxisDrvScaleDenom')), 1, 1)
        fl.addWidget(_t('num', 'QLabel { font-size: 18px; }'), 2, 2)
        fl.addWidget(self._make_sketch_cell(self._row_def('AxisDrvScaleNum')), 3, 1)
        grid.addWidget(ff_col, 0, 4, 4, 2)

        chain = QtWidgets.QWidget()
        cl = QtWidgets.QHBoxLayout(chain)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(14)

        def _blk(text, w=90, h=62, dark=True):
            b = QtWidgets.QLabel(text)
            b.setAlignment(QtCore.Qt.AlignCenter)
            if dark:
                b.setStyleSheet(
                    f'QLabel {{ background: #0f5b79; color: #f3f3f3; border: 2px solid #0f3345;'
                    f' font-size: 24px; font-weight: 700; min-width: {w}px; min-height: {h}px; }}'
                )
            else:
                b.setStyleSheet(
                    f'QLabel {{ background: transparent; color: #0f3345; border: 2px solid #0f3345;'
                    f' font-size: 28px; font-weight: 700; min-width: {w}px; min-height: {h}px; border-radius: {h//2}px; }}'
                )
            return b

        cl.addWidget(_blk('-', 54, 220, dark=False))
        cl.addWidget(_t('->', 'QLabel { font-size: 24px; color: #666; }'))

        pid_col = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(pid_col)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        pl.addWidget(_blk('P', 68, 58))
        pl.addWidget(_blk('I', 68, 58))
        pl.addWidget(_blk('D', 68, 58))
        cl.addWidget(pid_col)

        cl.addWidget(_t('->', 'QLabel { font-size: 24px; color: #666; }'))
        cl.addWidget(_blk('+', 54, 220, dark=False))
        cl.addWidget(_t('->', 'QLabel { font-size: 24px; color: #666; }'))
        cl.addWidget(_blk('+', 54, 220, dark=False))
        cl.addWidget(_t('->', 'QLabel { font-size: 24px; color: #666; }'))
        cl.addWidget(_blk('Deadband', 150, 220))
        cl.addWidget(_t('->', 'QLabel { font-size: 24px; color: #666; }'))
        cl.addWidget(_blk('Process', 210, 220))
        grid.addWidget(chain, 4, 1, 2, 6)

        deadband_vals = QtWidgets.QWidget()
        dl = QtWidgets.QGridLayout(deadband_vals)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setHorizontalSpacing(8)
        dl.addWidget(_t('tol.', 'QLabel { font-size: 18px; }'), 0, 0)
        dl.addWidget(self._make_sketch_cell(self._row_def('AxisCntrlDeadband')), 0, 1)
        dl.addWidget(_t('time [cyc]', 'QLabel { font-size: 18px; }'), 0, 2)
        dl.addWidget(self._make_sketch_cell(self._row_def('AxisCntrlDeadbandTime')), 0, 3)
        grid.addWidget(deadband_vals, 6, 4, 1, 3)

        at_target = QtWidgets.QWidget()
        atl = QtWidgets.QGridLayout(at_target)
        atl.setContentsMargins(0, 0, 0, 0)
        atl.setHorizontalSpacing(8)
        atl.setVerticalSpacing(6)
        atl.addWidget(_t('At target:', 'QLabel { font-size: 20px; font-weight: 600; }'), 0, 0, 1, 2)
        atl.addWidget(_t('tol.', 'QLabel { font-size: 18px; }'), 1, 0)
        atl.addWidget(self._make_sketch_cell(self._row_def('AxisMonAtTargetTol')), 1, 1)
        atl.addWidget(_t('time [cyc]', 'QLabel { font-size: 18px; }'), 1, 2)
        atl.addWidget(self._make_sketch_cell(self._row_def('AxisMonAtTargetTime')), 1, 3)
        grid.addWidget(at_target, 7, 0, 2, 4)

        enc = QtWidgets.QWidget()
        el = QtWidgets.QGridLayout(enc)
        el.setContentsMargins(0, 0, 0, 0)
        el.setHorizontalSpacing(8)
        el.setVerticalSpacing(6)
        el.addWidget(_t('Enc. scale', 'QLabel { font-size: 20px; font-weight: 600; }'), 0, 0, 1, 2)
        el.addWidget(_t('num', 'QLabel { font-size: 18px; }'), 1, 0)
        el.addWidget(self._make_sketch_cell(self._row_def('AxisEncScaleNum')), 1, 1)
        el.addWidget(_t('denom', 'QLabel { font-size: 18px; }'), 2, 0)
        el.addWidget(self._make_sketch_cell(self._row_def('AxisEncScaleDenom')), 2, 1)
        grid.addWidget(enc, 7, 4, 2, 2)

        for c in range(7):
            grid.setColumnStretch(c, 1)

        self.diagram_layout.addWidget(sketch, 0, 0)

    def _populate_controller_sketch_overlay(self, image_path):
        canvas = ImageOverlayCanvas(image_path)
        if not canvas.has_image():
            self._log(f'Cannot load sketch image: {image_path}')
            return

        self.diagram_layout.addWidget(canvas, 0, 0)
        self._current_overlay_canvas = canvas

        img_name = Path(image_path).name.lower()
        is_original = (
            img_name == 'original.png'
            or img_name.startswith('original.')
            or (canvas._base_w == 1696 and canvas._base_h == 856)
        )
        self._log(f'Controller sketch image: {img_name} ({canvas._base_w}x{canvas._base_h}), using {"original" if is_original else "default"} map')
        coords_default = {
            'AxisCntrlInnerTol': (0.165, 0.155),
            'AxisCntrlKp': (0.330, 0.090),
            'AxisCntrlKi': (0.330, 0.165),
            'AxisCntrlKd': (0.330, 0.240),
            'AxisCntrlInnerKp': (0.460, 0.090),
            'AxisCntrlInnerKi': (0.460, 0.165),
            'AxisCntrlInnerKd': (0.460, 0.240),
            'AxisDrvScaleDenom': (0.665, 0.090),
            'AxisCntrlKff': (0.665, 0.175),
            'AxisDrvScaleNum': (0.665, 0.260),
            'AxisCntrlDeadband': (0.635, 0.735),
            'AxisCntrlDeadbandTime': (0.850, 0.735),
            'AxisMonAtTargetTol': (0.100, 0.860),
            'AxisMonAtTargetTime': (0.360, 0.860),
            'AxisEncScaleNum': (0.670, 0.860),
            'AxisEncScaleDenom': (0.670, 0.935),
            'AxisCntrlIPartHL': (0.475, 0.365),
            'AxisCntrlIPartLL': (0.475, 0.530),
            'AxisCntrlOutHL': (0.675, 0.365),
            'AxisCntrlOutLL': (0.675, 0.530),
        }
        # Explicit per-field placement for original.png (1696x856), no global remap.
        if is_original:
            coords = {
                'AxisCntrlInnerTol': (408 / 1696.0, 149 / 856.0),
                'AxisCntrlKp': (528 / 1696.0, 105 / 856.0),
                'AxisCntrlKi': (528 / 1696.0, 176 / 856.0),
                'AxisCntrlKd': (528 / 1696.0, 248 / 856.0),
                'AxisCntrlInnerKp': (645 / 1696.0, 105 / 856.0),
                'AxisCntrlInnerKi': (645 / 1696.0, 176 / 856.0),
                'AxisCntrlInnerKd': (645 / 1696.0, 248 / 856.0),
                'AxisDrvScaleDenom': (996 / 1696.0, 145 / 856.0),
                'AxisDrvScaleNum': (996 / 1696.0, 194 / 856.0),
                'AxisCntrlKff': (996 / 1696.0, 293 / 856.0),
                'AxisCntrlIPartHL': (779 / 1696.0, 346 / 856.0),
                'AxisCntrlIPartLL': (779 / 1696.0, 483 / 856.0),
                'AxisCntrlOutHL': (1088 / 1696.0, 346 / 856.0),
                'AxisCntrlOutLL': (1088 / 1696.0, 483 / 856.0),
                'AxisCntrlDeadband': (1234 / 1696.0, 536 / 856.0),
                'AxisCntrlDeadbandTime': (1348 / 1696.0, 536 / 856.0),
                'AxisMonAtTargetTol': (265 / 1696.0, 633 / 856.0),
                'AxisMonAtTargetTime': (384 / 1696.0, 633 / 856.0),
                'AxisEncScaleNum': (995 / 1696.0, 730 / 856.0),
                'AxisEncScaleDenom': (995 / 1696.0, 785 / 856.0),
            }
            loaded = self._load_layout_for_image(image_path)
            if loaded:
                for k, v in loaded.items():
                    if k in coords and isinstance(v, (list, tuple)) and len(v) == 2:
                        try:
                            coords[k] = (float(v[0]), float(v[1]))
                        except Exception:
                            pass
            for name, (x, y) in coords.items():
                canvas.add_overlay_widget(x, y, self._make_sketch_cell(self._row_def(name), overlay=True), anchor='center', name=name)
        else:
            coords = coords_default
            for name, (x, y) in coords.items():
                canvas.add_overlay_widget(x, y, self._make_sketch_cell(self._row_def(name), overlay=True), anchor='center', name=name)
        canvas.set_calibration_enabled(bool(self.calibrate_btn.isChecked()))

    def _layout_file_for_image(self, image_path):
        p = Path(image_path)
        return p.with_suffix('.layout.json')

    def _load_layout_for_image(self, image_path):
        path = self._layout_file_for_image(image_path)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
        except Exception as ex:
            self._log(f'Failed to load layout {path.name}: {ex}')
        return {}

    def _save_current_layout(self):
        if not hasattr(self, '_current_overlay_canvas') or self._current_overlay_canvas is None:
            self._log('No overlay canvas to save')
            return
        image_path = self.sketch_image_edit.text().strip() if hasattr(self, 'sketch_image_edit') else self.sketch_image_path
        if not image_path:
            self._log('No sketch image path set')
            return
        out = self._current_overlay_canvas.overlay_positions()
        path = self._layout_file_for_image(image_path)
        try:
            path.write_text(json.dumps(out, indent=2, sort_keys=True))
            self._log(f'Saved overlay layout: {path.name} ({len(out)} fields)')
        except Exception as ex:
            self._log(f'Failed to save layout: {ex}')

    def _toggle_calibration(self, checked):
        if hasattr(self, '_current_overlay_canvas') and self._current_overlay_canvas is not None:
            self._current_overlay_canvas.set_calibration_enabled(bool(checked))
        self._log('Calibration mode ON (drag boxes, then Save Layout)' if checked else 'Calibration mode OFF')

    def _toggle_log_visible(self, checked):
        self.log.setVisible(bool(checked))
        self.log_toggle_btn.setText('Hide Log' if checked else 'Show Log')

    def _toggle_changes_log_visible(self, checked):
        self.changes_log.setVisible(bool(checked))
        self.changes_toggle_btn.setText('Hide Changes' if checked else 'Show Changes')

    def _update_sketch_image(self):
        self.sketch_image_path = self.sketch_image_edit.text().strip()
        if self.view_mode.currentText() == 'Controller Sketch':
            self._populate_table()

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
                    'AxisCntrlInnerTol', 'AxisCntrlInnerParams',
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
                    'AxisMonCntrlOutHL', 'AxisMonEnableCntrlOutHLMon',
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
        item.setToolTip(f"GET: {row_def.get('get') or '-'}\nSET: {row_def.get('set') or '-'}")
        self.table.setItem(r, 0, item)

        axis = QtWidgets.QLineEdit('1')
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

    def _read_all_rows(self):
        if self.view_mode.currentText() in {'Diagram', 'Controller Sketch'}:
            count = 0
            for row_def, read_edit in self._diagram_read_rows:
                self._read_row(row_def, self.axis_all_edit, read_edit)
                count += 1
            self._log(f'Read All completed ({count} rows)')
            return

        count = 0
        for r in range(self.table.rowCount()):
            name_item = self.table.item(r, 0)
            axis_edit = self.table.cellWidget(r, 1)
            read_edit = self.table.cellWidget(r, 4)
            if name_item is None or axis_edit is None or read_edit is None:
                continue
            name = name_item.text().strip()
            row_def = next((x for x in self.rows if x.get('name') == name), None)
            if not row_def or not row_def.get('get'):
                continue
            self._read_row(row_def, axis_edit, read_edit)
            count += 1
        self._log(f'Read All completed ({count} rows)')

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
            overlay = bool(widget.property('sketchOverlay'))
            font_sz = '11px' if overlay else '13px'
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
        overlay = bool(widget.property('sketchOverlay'))
        font_sz = '11px' if overlay else '13px'
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

    def send_raw_command(self, cmd):
        pv = self.cmd_pv.text().strip()
        cmd = normalize_float_literals((cmd or '').strip())
        if not pv:
            return False, 'ERROR: Command PV is empty'
        if not cmd:
            return False, 'ERROR: Command text is empty'
        try:
            self.client.put(pv, cmd, wait=True)
            msg = f'CMD -> {pv} ({len(cmd)} chars): {cmd}'
            self._log(msg)
            return True, msg
        except Exception as ex:
            msg = f'ERROR sending command ({len(cmd)} chars): {ex} | CMD={cmd}'
            self._log(msg)
            return False, msg

    def read_raw_command(self, cmd):
        ok, msg = self.send_raw_command(cmd)
        if not ok:
            return False, msg
        qp = self.qry_pv.text().strip()
        if not qp:
            return True, f'Command sent, no QRY PV configured: {cmd}'
        try:
            proc_pv = _proc_pv_for_readback(qp)
            self.client.put(proc_pv, 1, wait=True)
            val = self.client.get(qp, as_string=True)
            msg = f'QRY <- {qp}: {val}'
            self._log(msg)
            return True, msg
        except Exception as ex:
            msg = f'ERROR query read: {ex}'
            self._log(msg)
            return False, msg

    def _read_row(self, row_def, axis_edit, read_edit):
        cmd, err = self._cmd_from_template(row_def.get('get', ''), axis_edit.text(), '')
        if err:
            read_edit.setText(err)
            self._set_sketch_value_style(read_edit, False)
            return
        ok, msg = self.read_raw_command(cmd)
        if ok and ': ' in msg:
            val = msg.split(': ', 1)[1].strip()
            disp_val = compact_float_text(val)
            if bool(read_edit.property('sketchValue')):
                read_edit.setProperty('lastReadbackText', disp_val)
            read_edit.setText(disp_val)
            self._record_current_value(axis_edit.text().strip() or self.default_axis_id, row_def.get('name', ''), disp_val)
            if bool(read_edit.property('sketchValue')):
                self._update_value_match_visual(read_edit, read_edit)
        else:
            read_edit.setText(msg)
            self._set_sketch_value_style(read_edit, False)

    def _write_row(self, row_def, axis_edit, set_edit, read_edit):
        set_txt = set_edit.text().strip()
        cmd, err = self._cmd_from_template(row_def.get('set', ''), axis_edit.text(), set_edit.text())
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
        self._record_change(axis_id, row_def.get('name', ''), set_txt)
        if not row_def.get('get'):
            self._record_current_value(axis_id, row_def.get('name', ''), set_txt)
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
    ap.add_argument('--axis-id', default='1', help='Default axis id for Axis All')
    ap.add_argument('--sketch-image', default='', help='Path to background image for Controller Sketch overlay')
    ap.add_argument('--timeout', type=float, default=2.0, help='EPICS timeout in seconds')
    args = ap.parse_args()

    default_cmd_pv = args.cmd_pv.strip() if args.cmd_pv else _join_prefix_pv(args.prefix, 'MCU-Cmd.AOUT')
    default_qry_pv = args.qry_pv.strip() if args.qry_pv else _join_prefix_pv(args.prefix, 'MCU-Cmd.AINP')
    sketch_image = args.sketch_image.strip()
    if not sketch_image:
        base_dir = Path(__file__).resolve().parent
        for name in ('original.png', 'controller_sketch.png'):
            candidate = base_dir / name
            if candidate.exists():
                sketch_image = str(candidate)
                break

    app = QtWidgets.QApplication(sys.argv)
    w = CntrlWindow(
        catalog_path=args.catalog,
        default_cmd_pv=default_cmd_pv,
        default_qry_pv=default_qry_pv,
        timeout=args.timeout,
        default_axis_id=args.axis_id,
        title_prefix=args.prefix,
        sketch_image_path=sketch_image,
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
