#!/usr/bin/env python3
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    from PyQt5 import QtCore, QtWidgets
except Exception:
    from PySide6 import QtCore, QtWidgets  # type: ignore

from ecmc_stream_qt import (
    EpicsClient,
    _join_prefix_pv,
    _proc_pv_for_readback,
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


class CntrlWindow(QtWidgets.QMainWindow):
    def __init__(self, catalog_path, default_cmd_pv, default_qry_pv, timeout, default_axis_id='1', title_prefix=''):
        super().__init__()
        p = str(title_prefix or '').strip()
        self.setWindowTitle(f'ecmc PID/Controller Tuning [{p}]' if p else 'ecmc PID/Controller Tuning')
        self.resize(1320, 860)
        self.client = EpicsClient(timeout=timeout)
        self.catalog = self._load_catalog(catalog_path)
        self.rows = _build_pairs(self.catalog.get('commands', []), include_set_only=False)
        self.rows_all = _build_pairs(self.catalog.get('commands', []), include_set_only=True)
        self._rows_all_by_name = {r['name']: r for r in self.rows_all}
        self._diagram_read_rows = []
        self._diagram_value_pairs = []
        self.default_axis_id = str(default_axis_id).strip() or '1'
        self._build_ui(default_cmd_pv, default_qry_pv, timeout)
        self._populate_table()
        self._log(f'Connected via backend: {self.client.backend}')

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

        cfg_group = QtWidgets.QGroupBox('PV Configuration')
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
        cfg_group.setVisible(False)

        top_row = QtWidgets.QHBoxLayout()
        self.pv_cfg_toggle = QtWidgets.QPushButton('Show PV Config')
        self.pv_cfg_toggle.setCheckable(True)
        self.pv_cfg_toggle.setChecked(False)
        self.pv_cfg_toggle.setAutoDefault(False)
        self.pv_cfg_toggle.setDefault(False)
        self.pv_cfg_toggle.toggled.connect(
            lambda checked: (
                cfg_group.setVisible(bool(checked)),
                self.pv_cfg_toggle.setText('Hide PV Config' if checked else 'Show PV Config'),
            )
        )
        top_row.addWidget(self.pv_cfg_toggle)
        top_row.addStretch(1)
        layout.addLayout(top_row)
        layout.addWidget(cfg_group)

        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Filter commands...')
        self.search.textChanged.connect(self._populate_table)
        search_row.addWidget(self.search)
        search_row.addWidget(QtWidgets.QLabel('View'))
        self.view_mode = QtWidgets.QComboBox()
        self.view_mode.addItems(['Flat', 'Schematic', 'Diagram'])
        self.view_mode.setCurrentText('Diagram')
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
        layout.addWidget(self.log, stretch=0)

    def _log(self, msg):
        t = datetime.now().strftime('%H:%M:%S')
        self.log.appendPlainText(f'[{t}] {msg}')

    def _filtered_rows(self):
        txt = self.search.text().strip().lower()
        if not txt:
            return self.rows
        return [r for r in self.rows if txt in r['name'].lower()]

    def _populate_table(self):
        if self.view_mode.currentText() == 'Diagram':
            self.stack.setCurrentIndex(1)
            self._populate_diagram()
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
        if self.view_mode.currentText() == 'Diagram':
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
        if self.view_mode.currentText() == 'Diagram':
            for set_edit, read_edit in self._diagram_value_pairs:
                if set_edit is None or read_edit is None:
                    continue
                val = read_edit.text().strip()
                if not val:
                    continue
                set_edit.setText(val)
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
            return
        ok, msg = self.read_raw_command(cmd)
        if ok and ': ' in msg:
            read_edit.setText(msg.split(': ', 1)[1].strip())
        else:
            read_edit.setText(msg)

    def _write_row(self, row_def, axis_edit, set_edit, read_edit):
        cmd, err = self._cmd_from_template(row_def.get('set', ''), axis_edit.text(), set_edit.text())
        if err:
            read_edit.setText(err)
            return
        ok, msg = self.send_raw_command(cmd)
        if not ok:
            read_edit.setText(msg)
            return
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
    ap.add_argument('--timeout', type=float, default=2.0, help='EPICS timeout in seconds')
    args = ap.parse_args()

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
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
