#!/usr/bin/env python3
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
except Exception:
    from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore


PLACEHOLDER_RE = re.compile(r'<([^>]+)>')
FLOAT_LITERAL_RE = re.compile(r'(?<![A-Za-z0-9_])([+-]?(?:(?:\d+\.\d*)|(?:\.\d+))(?:[eE][+-]?\d+)?)(?![A-Za-z0-9_])')
FLOAT_DISPLAY_RE = re.compile(r'^[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+(?:\.\d*)?[eE][+-]?\d+)|(?:\.\d+[eE][+-]?\d+))$')


class EpicsClient:
    def __init__(self, timeout=2.0):
        self.timeout = timeout
        self.backend = None
        self._epics = None
        self._cli_available = bool(shutil.which('caput') and shutil.which('caget'))

        try:
            import epics  # type: ignore

            self._epics = epics
            self.backend = 'pyepics'
            return
        except Exception:
            pass

        if self._cli_available:
            self.backend = 'cli'
            return

        raise RuntimeError('No EPICS client available. Install pyepics or ensure caget/caput are in PATH.')

    def _is_missing_ca_dll_error(self, ex):
        msg = str(ex).lower()
        return ('cannot find epics ca dll' in msg) or ('cannot load ca dll' in msg)

    def _fallback_to_cli_if_possible(self, ex):
        if self.backend == 'pyepics' and self._cli_available and self._is_missing_ca_dll_error(ex):
            self.backend = 'cli'
            self._epics = None
            return True
        return False

    def put(self, pv, value, wait=True):
        if self.backend == 'pyepics':
            try:
                ok = self._epics.caput(pv, value, wait=wait, timeout=self.timeout)
                if ok is None:
                    raise RuntimeError(f'caput failed for {pv}')
                return
            except Exception as ex:
                if not self._fallback_to_cli_if_possible(ex):
                    raise

        proc = subprocess.run(
            ['caput', '-t', pv, str(value)],
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'caput failed for {pv}')

    def get(self, pv, as_string=True):
        if self.backend == 'pyepics':
            try:
                val = self._epics.caget(pv, as_string=as_string, timeout=self.timeout)
                if val is None:
                    raise RuntimeError(f'caget failed for {pv}')
                return str(val)
            except Exception as ex:
                if not self._fallback_to_cli_if_possible(ex):
                    raise

        proc = subprocess.run(
            ['caget', '-t', pv],
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'caget failed for {pv}')
        return proc.stdout.strip()


def placeholders_in_template(template):
    names = []
    seen = set()
    for n in PLACEHOLDER_RE.findall(template or ''):
        if n not in seen:
            seen.add(n)
            names.append(n)
    return names


def placeholders_in_template_all(template):
    return PLACEHOLDER_RE.findall(template or '')


def placeholders_in_parser_signature(parser_sig):
    # Keep duplicates/order, since parser signatures often repeat types
    # like <float>,<float>,<float>.
    return placeholders_in_template_all(parser_sig or '')


def fill_template(template, values):
    out = str(template or '')
    for name in placeholders_in_template(out):
        v = values.get(name, '').strip()
        out = out.replace(f'<{name}>', v if v else f'<{name}>')
    return out


def _join_prefix_pv(prefix, suffix):
    p = str(prefix or '').strip()
    s = str(suffix or '').strip()
    if not p:
        return s
    if p.endswith(':'):
        return f'{p}{s}'
    return f'{p}:{s}'


def _proc_pv_for_readback(pv):
    p = str(pv or '').strip()
    if not p:
        return ''
    if '.' in p:
        return f"{p.split('.', 1)[0]}.PROC"
    return f'{p}.PROC'


def _trim_float_literal_zeros(token):
    t = str(token or '').strip()
    if not t:
        return t
    exp = ''
    base = t
    for sep in ('e', 'E'):
        if sep in base:
            i = base.find(sep)
            exp = base[i:]
            base = base[:i]
            break

    sign = ''
    if base[:1] in '+-':
        sign = base[:1]
        base = base[1:]

    if '.' not in base:
        return t

    int_part, frac_part = base.split('.', 1)
    frac_part = frac_part.rstrip('0')
    if not int_part:
        int_part = '0'
    if frac_part:
        # Compact fractional literals to save PV payload bytes: 0.3 -> .3, -0.3 -> -.3
        if int_part == '0':
            int_part = ''
        return f'{sign}{int_part}.{frac_part}{exp}'
    return f'{sign}{int_part}{exp}'


def normalize_float_literals(cmd):
    s = str(cmd or '')
    return FLOAT_LITERAL_RE.sub(lambda m: _trim_float_literal_zeros(m.group(1)), s)


def compact_float_text(value, sig_digits=15):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        v = float(value)
    else:
        s = str(value or '').strip()
        if not s:
            return s
        if not FLOAT_DISPLAY_RE.match(s):
            return str(value)
        # Preserve plain integer strings exactly as entered/read.
        if '.' not in s and 'e' not in s.lower():
            return s
        try:
            v = float(s)
        except Exception:
            return str(value)

    out = f'{v:.{int(sig_digits)}g}'
    if out in {'-0', '+0'}:
        return '0'
    return out


def compact_query_message_value(msg):
    s = str(msg or '')
    if not s.startswith('QRY <- ') or ': ' not in s:
        return s
    head, val = s.rsplit(': ', 1)
    return f'{head}: {compact_float_text(val)}'


class CompactDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def textFromValue(self, value):
        return compact_float_text(value)


class SpinBoxWithButtons(QtWidgets.QWidget):
    def __init__(self, float_mode=False):
        super().__init__()
        self.float_mode = bool(float_mode)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if self.float_mode:
            self.spin = CompactDoubleSpinBox()
            self.spin.setDecimals(4)
            self.spin.setSingleStep(0.1)
        else:
            self.spin = QtWidgets.QSpinBox()
            self.spin.setSingleStep(1)

        self.spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        self.spin.setFixedSize(100, 24)
        self.spin.setKeyboardTracking(False)
        self.spin.setFocusPolicy(QtCore.Qt.StrongFocus)

        btn_col = QtWidgets.QWidget()
        btn_col_l = QtWidgets.QVBoxLayout(btn_col)
        btn_col_l.setContentsMargins(2, 0, 0, 0)
        btn_col_l.setSpacing(0)
        self.up_btn = QtWidgets.QPushButton('^')
        self.down_btn = QtWidgets.QPushButton('v')
        self.up_btn.setFixedSize(20, 12)
        self.down_btn.setFixedSize(20, 12)
        self.up_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self.down_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self.up_btn.setCursor(QtCore.Qt.ArrowCursor)
        self.down_btn.setCursor(QtCore.Qt.ArrowCursor)
        arrow_btn_style = (
            'QPushButton {'
            ' background: #6a6a6a;'
            ' color: #ffffff;'
            ' border: 1px solid #4c4c4c;'
            ' border-radius: 2px;'
            ' padding: 0px;'
            ' font-size: 10px;'
            ' font-weight: 700;'
            '}'
            'QPushButton:pressed { background: #4f4f4f; }'
        )
        self.up_btn.setStyleSheet(arrow_btn_style)
        self.down_btn.setStyleSheet(arrow_btn_style)
        self.up_btn.clicked.connect(self.spin.stepUp)
        self.down_btn.clicked.connect(self.spin.stepDown)
        btn_col_l.addWidget(self.up_btn)
        btn_col_l.addWidget(self.down_btn)

        layout.addWidget(self.spin)
        layout.addWidget(btn_col)
        self.setFixedSize(120, 24)

    def setRange(self, low, high):
        self.spin.setRange(low, high)

    def setSingleStep(self, step):
        self.spin.setSingleStep(step)

    def setDecimals(self, dec):
        if isinstance(self.spin, QtWidgets.QDoubleSpinBox):
            self.spin.setDecimals(dec)

    def setHexMode(self):
        if isinstance(self.spin, QtWidgets.QSpinBox):
            self.spin.setDisplayIntegerBase(16)
            self.spin.setPrefix('0x')

    def value(self):
        return self.spin.value()

    def setValue(self, value):
        self.spin.setValue(value)

    def on_value_changed(self, cb):
        self.spin.valueChanged.connect(cb)


class ParamInputWidget(QtWidgets.QWidget):
    def __init__(self, name, ptype):
        super().__init__()
        self.name = name
        self.ptype = (ptype or '').lower()
        self._updating = False
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.edit = QtWidgets.QLineEdit('')
        self.edit.setPlaceholderText(self.name)
        self.edit.setMaximumWidth(140)
        layout.addWidget(self.edit)

        self.spin = None
        self.slider = None
        if self.ptype in {'int', 'uint'}:
            self.spin = QtWidgets.QSpinBox()
            self.spin.setRange(-1000000, 1000000)
            self.spin.setSingleStep(1)
            self.spin.setMaximumWidth(95)
            self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.slider.setRange(-1000, 1000)
            self.slider.setSingleStep(1)
            self.slider.setPageStep(10)
            self.slider.setMaximumWidth(160)
            layout.addWidget(self.spin)
            layout.addWidget(self.slider)
            self.spin.valueChanged.connect(self._spin_to_text)
            self.slider.valueChanged.connect(self._slider_to_spin)
            self.edit.editingFinished.connect(self._text_to_spin)
        elif self.ptype in {'float', 'double'}:
            self.spin = CompactDoubleSpinBox()
            self.spin.setRange(-1e6, 1e6)
            self.spin.setDecimals(4)
            self.spin.setSingleStep(0.1)
            self.spin.setMaximumWidth(110)
            self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self.slider.setRange(-1000, 1000)
            self.slider.setSingleStep(1)
            self.slider.setPageStep(10)
            self.slider.setMaximumWidth(160)
            layout.addWidget(self.spin)
            layout.addWidget(self.slider)
            self.spin.valueChanged.connect(self._spin_to_text)
            self.slider.valueChanged.connect(self._slider_to_spin)
            self.edit.editingFinished.connect(self._text_to_spin)

    def _slider_to_spin(self, val):
        if self._updating or self.spin is None:
            return
        self._updating = True
        try:
            if isinstance(self.spin, QtWidgets.QDoubleSpinBox):
                self.spin.setValue(val / 10.0)
            else:
                self.spin.setValue(val)
        finally:
            self._updating = False

    def _spin_to_text(self, val):
        if self._updating:
            return
        self._updating = True
        try:
            if isinstance(self.spin, QtWidgets.QDoubleSpinBox):
                self.edit.setText(compact_float_text(val))
                if self.slider is not None:
                    self.slider.setValue(int(round(float(val) * 10.0)))
            else:
                self.edit.setText(str(int(val)))
                if self.slider is not None:
                    self.slider.setValue(int(val))
        finally:
            self._updating = False

    def _text_to_spin(self):
        if self._updating or self.spin is None:
            return
        t = self.edit.text().strip()
        if not t:
            return
        self._updating = True
        try:
            if isinstance(self.spin, QtWidgets.QDoubleSpinBox):
                v = float(t)
                self.spin.setValue(v)
                if self.slider is not None:
                    self.slider.setValue(int(round(v * 10.0)))
            else:
                v = int(float(t))
                self.spin.setValue(v)
                if self.slider is not None:
                    self.slider.setValue(v)
        except Exception:
            pass
        finally:
            self._updating = False

    def text(self):
        return self.edit.text()

    def set_text(self, value):
        if isinstance(self.spin, QtWidgets.QDoubleSpinBox):
            self.edit.setText(compact_float_text(value))
        else:
            self.edit.setText(str(value))
        self._text_to_spin()


class CommandEditorRow(QtWidgets.QGroupBox):
    def __init__(self, parent_window, command_data):
        title = command_data.get('command_named', command_data.get('command', ''))
        super().__init__(title)
        self.parent_window = parent_window
        self.command_data = command_data
        self.template = command_data.get('command_named', command_data.get('command', ''))
        self.param_names = placeholders_in_template(self.template)
        parser_placeholders = placeholders_in_parser_signature(command_data.get('parser_command', ''))
        self.param_types = {}
        for i, name in enumerate(self.param_names):
            t = parser_placeholders[i] if i < len(parser_placeholders) else ''
            self.param_types[name] = t
        self.param_widgets = {}
        self._build_ui()
        self._update_preview()

    def _build_ui(self):
        self.setFlat(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.template_line = QtWidgets.QLineEdit(self.template)
        self.template_line.setReadOnly(True)
        layout.addWidget(self.template_line)

        toggle_row = QtWidgets.QHBoxLayout()
        self.toggle_btn = QtWidgets.QToolButton()
        self.toggle_btn.setText('Parameters')
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(False)
        self.toggle_btn.toggled.connect(self._toggle_params)
        toggle_row.addWidget(self.toggle_btn)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        self.params_widget = QtWidgets.QWidget()
        self.params_widget.setVisible(False)
        if self.param_names:
            grid = QtWidgets.QGridLayout(self.params_widget)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(4)
            cols = 2
            for p in self.param_names:
                w = ParamInputWidget(p, self.param_types.get(p, ''))
                w.edit.textChanged.connect(self._update_preview)
                self.param_widgets[p] = w
            for i, p in enumerate(self.param_names):
                r = i // cols
                c = i % cols
                cell = QtWidgets.QWidget()
                cell_l = QtWidgets.QVBoxLayout(cell)
                cell_l.setContentsMargins(0, 0, 0, 0)
                cell_l.setSpacing(2)
                cell_l.addWidget(QtWidgets.QLabel(p))
                cell_l.addWidget(self.param_widgets[p])
                grid.addWidget(cell, r, c)
        layout.addWidget(self.params_widget)

        self.preview = QtWidgets.QLineEdit('')
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)

        btns = QtWidgets.QHBoxLayout()
        fill_btn = QtWidgets.QPushButton('Fill 0')
        fill_btn.clicked.connect(self._fill_zeroes)
        write_btn = QtWidgets.QPushButton('Write')
        write_btn.clicked.connect(self._write_command)
        read_btn = QtWidgets.QPushButton('Read')
        read_btn.clicked.connect(self._read_command)
        copy_btn = QtWidgets.QPushButton('Copy')
        copy_btn.clicked.connect(self._copy_preview)
        btns.addWidget(fill_btn)
        btns.addWidget(write_btn)
        btns.addWidget(read_btn)
        btns.addWidget(copy_btn)
        layout.addLayout(btns)

        self.result = QtWidgets.QLabel('')
        self.result.setWordWrap(True)
        layout.addWidget(self.result)

    def _toggle_params(self, checked):
        self.params_widget.setVisible(bool(checked))

    def _values(self):
        return {k: w.text() for k, w in self.param_widgets.items()}

    def command_text(self):
        return fill_template(self.template, self._values())

    def _update_preview(self):
        self.preview.setText(self.command_text())

    def _fill_zeroes(self):
        for w in self.param_widgets.values():
            if not w.text().strip():
                w.set_text('0')

    def _copy_preview(self):
        QtWidgets.QApplication.clipboard().setText(self.command_text())
        self.parent_window._log('Copied multi-command row preview to clipboard')

    def _write_command(self):
        cmd = self.command_text().strip()
        ok, msg = self.parent_window.send_raw_command(cmd)
        self.result.setText(msg)

    def _read_command(self):
        cmd = self.command_text().strip()
        ok, msg = self.parent_window.read_raw_command(cmd)
        self.result.setText(compact_query_message_value(msg))


class MultiCommandDialog(QtWidgets.QDialog):
    def __init__(self, parent_window, commands):
        super().__init__(parent_window)
        self.parent_window = parent_window
        self.commands = commands
        self.rows = []
        self.setWindowTitle('Multi Command Editor')
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        info = QtWidgets.QLabel(
            'Edit parameters per command. "Write" sends command to CMD PV. '
            '"Read" sends command to CMD PV and then reads QRY PV.'
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['Command', 'Inline Params', 'Actions', 'Result'])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(3, 240)
        layout.addWidget(self.table, stretch=1)

        self.max_params = 0
        for c in self.commands:
            t = c.get('command_named', c.get('command', ''))
            self.max_params = max(self.max_params, len(placeholders_in_template(t)))

        self._add_broadcast_table_row()
        for c in self.commands:
            self._add_row(c)

        close_btn = QtWidgets.QPushButton('Close')
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)
        self._fit_to_contents()

    def _set_widget_value(self, w, value):
        try:
            if isinstance(w, SpinBoxWithButtons):
                if isinstance(w.spin, QtWidgets.QDoubleSpinBox):
                    w.setValue(float(value))
                else:
                    w.setValue(int(round(float(value))))
                return
            if isinstance(w, QtWidgets.QDoubleSpinBox):
                w.setValue(float(value))
                return
            if isinstance(w, QtWidgets.QSpinBox):
                w.setValue(int(round(float(value))))
                return
            w.setText(str(value))
        except Exception:
            pass

    def _add_broadcast_table_row(self):
        if self.max_params <= 0:
            return

        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self.table.setRowHeight(row_idx, 52)

        set_all_item = QtWidgets.QTableWidgetItem('Set all')
        set_all_item.setToolTip('Set parameter value by position for all commands below.')
        self.table.setItem(row_idx, 0, set_all_item)

        inline = QtWidgets.QWidget()
        inline_l = QtWidgets.QGridLayout(inline)
        inline_l.setContentsMargins(2, 0, 2, 0)
        inline_l.setHorizontalSpacing(4)
        inline_l.setVerticalSpacing(1)

        for i in range(self.max_params):
            lbl = QtWidgets.QLabel(f'P{i + 1}')
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            lbl.setStyleSheet('QLabel { color: #666; font-size: 10px; }')
            inline_l.addWidget(lbl, 0, i)

            spin = SpinBoxWithButtons(float_mode=True)
            spin.setRange(-1e6, 1e6)
            spin.setDecimals(4)
            spin.setSingleStep(0.1)
            spin.on_value_changed(lambda v, idx=i: self._broadcast_param(idx, v))
            inline_l.addWidget(spin, 1, i)
            inline_l.setColumnMinimumWidth(i, 156)

        self.table.setCellWidget(row_idx, 1, inline)

        actions = QtWidgets.QLabel('')
        self.table.setCellWidget(row_idx, 2, actions)

        result = QtWidgets.QLabel('')
        self.table.setCellWidget(row_idx, 3, result)

    def _broadcast_param(self, index, value):
        for r in self.rows:
            plist = r.get('param_widget_list', [])
            if index >= len(plist):
                continue
            self._set_widget_value(plist[index], value)

    def _fit_to_contents(self):
        self.table.resizeColumnsToContents()
        self.table.resizeRowsToContents()

        frame = 2 * self.table.frameWidth()
        vheader_w = self.table.verticalHeader().width()
        hheader_h = self.table.horizontalHeader().height()

        table_w = frame + vheader_w
        for c in range(self.table.columnCount()):
            table_w += self.table.columnWidth(c)

        table_h = frame + hheader_h
        for r in range(self.table.rowCount()):
            table_h += self.table.rowHeight(r)

        # Add approximate margins for labels/buttons/layout chrome.
        wanted_w = table_w + 80
        wanted_h = table_h + 140

        screen = QtWidgets.QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            wanted_w = min(wanted_w, int(g.width() * 0.9))
            wanted_h = min(wanted_h, int(g.height() * 0.9))

        self.resize(max(760, wanted_w), max(240, wanted_h))

    def _make_param_widget(self, ptype):
        t = (ptype or '').lower()
        if t in {'int', 'uint', 'i64', 'u64', 'hex', 'hex64', 'char'}:
            w = SpinBoxWithButtons(float_mode=False)
            if t in {'uint', 'u64', 'hex', 'hex64'}:
                w.setRange(0, 1000000)
            else:
                w.setRange(-1000000, 1000000)
            w.setSingleStep(1)
            if t in {'hex', 'hex64'}:
                w.setHexMode()
            return w
        if t in {'float', 'double'}:
            w = SpinBoxWithButtons(float_mode=True)
            w.setRange(-1e6, 1e6)
            w.setDecimals(4)
            w.setSingleStep(0.1)
            return w
        # Fallback: infer numeric params from common naming patterns.
        num_hints = (
            'index', 'idx', 'id', 'axis', 'slave', 'master', 'enable',
            'value', 'vel', 'acc', 'dec', 'time', 'timeout', 'size',
            'count', 'bit', 'mask', 'offset', 'mode', 'cmd', 'pos'
        )
        text_hints = ('name', 'file', 'path', 'expr', 'string', 'cfg')
        if any(h in self._current_param_name.lower() for h in num_hints) and not any(
            h in self._current_param_name.lower() for h in text_hints
        ):
            w = SpinBoxWithButtons(float_mode=True)
            w.setRange(-1e6, 1e6)
            w.setDecimals(4)
            w.setSingleStep(0.1)
            return w

        w = QtWidgets.QLineEdit('')
        w.setFixedWidth(120)
        w.setFixedHeight(24)
        return w

    def _widget_value(self, w):
        if isinstance(w, SpinBoxWithButtons):
            if isinstance(w.spin, QtWidgets.QDoubleSpinBox):
                return compact_float_text(w.value())
            return str(int(w.value()))
        if isinstance(w, QtWidgets.QDoubleSpinBox):
            return compact_float_text(w.value())
        if isinstance(w, QtWidgets.QSpinBox):
            return str(int(w.value()))
        return w.text().strip()

    def _add_row(self, command_data):
        row_idx = self.table.rowCount()
        self.table.insertRow(row_idx)
        self.table.setRowHeight(row_idx, 42)

        template = command_data.get('command_named', command_data.get('command', ''))
        params = command_data.get('param_names', []) or []
        row_tooltip = (
            f"Command: {template}\n"
            f"Parameters: {', '.join(params) if params else '-'}\n"
            f"Description: {command_data.get('description', '') or '-'}"
        )
        name_item = QtWidgets.QTableWidgetItem(command_data.get('name', template))
        name_item.setToolTip(row_tooltip)
        self.table.setItem(row_idx, 0, name_item)

        # Build inline parameter editor by replacing <param> with widgets.
        inline = QtWidgets.QWidget()
        inline_l = QtWidgets.QGridLayout(inline)
        inline_l.setContentsMargins(2, 0, 2, 0)
        inline_l.setHorizontalSpacing(4)
        inline_l.setVerticalSpacing(1)

        param_names = placeholders_in_template(template)
        parser_types = placeholders_in_parser_signature(command_data.get('parser_command', ''))
        param_widgets = {}
        param_widget_list = []
        for i in range(self.max_params):
            if i < len(param_names):
                p_name = param_names[i]
                p_type = parser_types[i] if i < len(parser_types) else ''
                self._current_param_name = p_name
                pw = self._make_param_widget(p_type)
                pw.setToolTip(f"Parameter: {p_name}" + (f" (type: {p_type})" if p_type else ""))
                param_widgets[p_name] = pw
                param_widget_list.append(pw)
                inline_l.addWidget(pw, 0, i)
            else:
                spacer = QtWidgets.QLineEdit('')
                spacer.setEnabled(False)
                spacer.setFixedWidth(120)
                spacer.setFixedHeight(24)
                spacer.setStyleSheet('QLineEdit { background: #f4f4f4; color: #f4f4f4; border: 1px solid #eee; }')
                inline_l.addWidget(spacer, 0, i)

            inline_l.setColumnMinimumWidth(i, 156)

        self.table.setCellWidget(row_idx, 1, inline)

        actions = QtWidgets.QWidget()
        actions_l = QtWidgets.QHBoxLayout(actions)
        actions_l.setContentsMargins(2, 0, 2, 0)
        actions_l.setSpacing(3)
        write_btn = QtWidgets.QPushButton('Write')
        read_btn = QtWidgets.QPushButton('Read')
        for btn in (write_btn, read_btn):
            # Prevent Enter/Return in any editor from triggering an implicit
            # "default" action button on the dialog.
            btn.setAutoDefault(False)
            btn.setDefault(False)
        write_btn.setMaximumWidth(58)
        read_btn.setMaximumWidth(58)
        actions_l.addWidget(write_btn)
        actions_l.addWidget(read_btn)
        self.table.setCellWidget(row_idx, 2, actions)

        result = QtWidgets.QLabel('')
        result.setWordWrap(False)
        self.table.setCellWidget(row_idx, 3, result)

        data_idx = len(self.rows)
        row = {
            'template': template,
            'param_names': param_names,
            'param_widgets': param_widgets,
            'param_widget_list': param_widget_list,
            'result': result,
        }
        self.rows.append(row)

        write_btn.clicked.connect(lambda _=False, i=data_idx: self._write_row(i))
        read_btn.clicked.connect(lambda _=False, i=data_idx: self._read_row(i))

    def _build_command(self, row_idx):
        row = self.rows[row_idx]
        values = {n: self._widget_value(w) for n, w in row['param_widgets'].items()}
        return fill_template(row['template'], values)

    def _write_row(self, row_idx):
        cmd = self._build_command(row_idx).strip()
        if self.parent_window._is_blocked_command_text(cmd):
            self.rows[row_idx]['result'].setText('Blocked command')
            self.parent_window._log('Blocked command cannot be written from Selected Panel')
            return
        ok, msg = self.parent_window.send_raw_command(cmd)
        self.rows[row_idx]['result'].setText(msg)

    def _read_row(self, row_idx):
        cmd = self._build_command(row_idx).strip()
        ok, msg = self.parent_window.read_raw_command(cmd)
        self.rows[row_idx]['result'].setText(compact_query_message_value(msg))

    def _copy_row(self, row_idx):
        cmd = self._build_command(row_idx).strip()
        QtWidgets.QApplication.clipboard().setText(cmd)
        self.rows[row_idx]['result'].setText('Copied')


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, catalog_path, blocklist_path, default_cmd_pv, default_qry_pv, timeout):
        super().__init__()
        self.setWindowTitle('ecmc Command Parser')
        self.resize(640, 480)

        self.client = EpicsClient(timeout=timeout)
        self.catalog = self._load_catalog(catalog_path)
        self._blocklist_load_error = ''
        self.blocked_commands = self._load_blocklist(blocklist_path)
        self._blocked_category_count = self._apply_blocked_category()
        self._child_windows = []

        self._build_ui(default_cmd_pv, default_qry_pv, timeout)
        self._populate_commands()
        self._log(f'Connected via backend: {self.client.backend}')
        if self._blocklist_load_error:
            self._log(f'Blocklist load error: {self._blocklist_load_error}')
        else:
            self._log(f'Blocklist loaded: {len(self.blocked_commands)} entries; marked {self._blocked_category_count} commands as Blocked')

    def _load_catalog(self, path):
        p = Path(path)
        if not p.exists():
            return {'commands': []}
        try:
            return json.loads(p.read_text())
        except Exception:
            return {'commands': []}

    def _load_blocklist(self, path):
        p = Path(path) if path else Path('ecmc_commands_blocklist_all.json')
        if not p.exists():
            return set()
        try:
            data = json.loads(p.read_text())
        except Exception as ex:
            self._blocklist_load_error = str(ex)
            return set()
        if isinstance(data, dict):
            items = data.get('commands', [])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        return {str(x).strip() for x in items if str(x).strip()}

    def _apply_blocked_category(self):
        if not self.blocked_commands:
            return 0
        count = 0
        for c in self.catalog.get('commands', []):
            named = str(c.get('command_named', c.get('command', ''))).strip()
            if named in self.blocked_commands:
                c['category'] = 'Blocked'
                count += 1
        return count

    def _build_ui(self, default_cmd_pv, default_qry_pv, timeout):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)

        top_row = QtWidgets.QHBoxLayout()
        self.cfg_toggle_btn = QtWidgets.QPushButton('Show Config')
        self.cfg_toggle_btn.setCheckable(True)
        self.cfg_toggle_btn.setChecked(False)
        self.cfg_toggle_btn.setAutoDefault(False)
        self.cfg_toggle_btn.setDefault(False)
        top_row.addWidget(self.cfg_toggle_btn)
        self.log_toggle_btn = QtWidgets.QPushButton('Show Log')
        self.log_toggle_btn.setCheckable(True)
        self.log_toggle_btn.setChecked(False)
        self.log_toggle_btn.setAutoDefault(False)
        self.log_toggle_btn.setDefault(False)
        top_row.addWidget(self.log_toggle_btn)
        self.open_cntrl_btn = QtWidgets.QPushButton('Cntrl Cfg App')
        self.open_cntrl_btn.setAutoDefault(False)
        self.open_cntrl_btn.setDefault(False)
        self.open_cntrl_btn.clicked.connect(self._open_cntrl_window)
        top_row.addWidget(self.open_cntrl_btn)
        self.open_mtn_btn = QtWidgets.QPushButton('Motion App')
        self.open_mtn_btn.setAutoDefault(False)
        self.open_mtn_btn.setDefault(False)
        self.open_mtn_btn.clicked.connect(self._open_motion_window)
        top_row.addWidget(self.open_mtn_btn)
        self.open_axis_btn = QtWidgets.QPushButton('Axis Cfg App')
        self.open_axis_btn.setAutoDefault(False)
        self.open_axis_btn.setDefault(False)
        self.open_axis_btn.clicked.connect(self._open_axis_window)
        top_row.addWidget(self.open_axis_btn)
        top_row.addStretch(1)
        self.caqtdm_main_btn = QtWidgets.QPushButton('caqtdm Main')
        self.caqtdm_main_btn.setAutoDefault(False)
        self.caqtdm_main_btn.setDefault(False)
        self.caqtdm_main_btn.clicked.connect(self._open_caqtdm_main_panel)
        top_row.addWidget(self.caqtdm_main_btn)
        layout.addLayout(top_row)

        cfg_group = QtWidgets.QGroupBox('PV Configuration')
        cfg = QtWidgets.QGridLayout(cfg_group)
        self.cmd_pv = QtWidgets.QLineEdit(default_cmd_pv)
        self.qry_pv = QtWidgets.QLineEdit(default_qry_pv)
        self.timeout_edit = CompactDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(timeout)
        self.timeout_edit.valueChanged.connect(self._set_timeout)

        cfg.addWidget(QtWidgets.QLabel('Command PV'), 0, 0)
        cfg.addWidget(self.cmd_pv, 0, 1)
        cfg.addWidget(QtWidgets.QLabel('Query PV'), 1, 0)
        cfg.addWidget(self.qry_pv, 1, 1)
        cfg.addWidget(QtWidgets.QLabel('Timeout [s]'), 2, 0)
        cfg.addWidget(self.timeout_edit, 2, 1)
        cfg_group.setVisible(False)
        self.cfg_toggle_btn.toggled.connect(
            lambda checked: (
                cfg_group.setVisible(bool(checked)),
                self.cfg_toggle_btn.setText('Hide Config' if checked else 'Show Config'),
            )
        )
        layout.addWidget(cfg_group)

        main_split = QtWidgets.QSplitter()
        main_split.setOrientation(QtCore.Qt.Vertical)

        upper = QtWidgets.QWidget()
        upper_l = QtWidgets.QVBoxLayout(upper)
        upper_l.setContentsMargins(0, 0, 0, 0)
        upper_l.setSpacing(4)

        send_group = QtWidgets.QGroupBox('Send Command')
        send_l = QtWidgets.QVBoxLayout(send_group)
        send_l.setContentsMargins(6, 6, 6, 6)
        send_l.setSpacing(3)
        self.command_edit = QtWidgets.QLineEdit('GetControllerError()')
        self.command_edit.returnPressed.connect(self.send_command)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(3)
        send_btn = QtWidgets.QPushButton('Send to CMD PV')
        send_btn.clicked.connect(self.send_command)
        clear_btn = QtWidgets.QPushButton('Clear Command')
        clear_btn.clicked.connect(lambda: self.command_edit.setText(''))
        proc_btn = QtWidgets.QPushButton('PROC + Read QRY')
        proc_btn.clicked.connect(self.proc_and_read_query)
        read_btn = QtWidgets.QPushButton('Read QRY Only')
        read_btn.clicked.connect(self.read_query_only)
        for btn in (send_btn, clear_btn, proc_btn, read_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
        btn_row.addWidget(send_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(proc_btn)
        btn_row.addWidget(read_btn)
        send_l.addWidget(self.command_edit)
        send_l.addLayout(btn_row)
        self.readback_edit = QtWidgets.QLineEdit('')
        self.readback_edit.setReadOnly(True)
        self.readback_edit.setPlaceholderText('Latest readback/result...')
        send_l.addWidget(self.readback_edit)
        upper_l.addWidget(send_group)

        self.response = QtWidgets.QPlainTextEdit()
        self.response.setReadOnly(True)

        main_split.addWidget(upper)

        lower = QtWidgets.QWidget()
        lower_l = QtWidgets.QVBoxLayout(lower)
        lower_l.setContentsMargins(0, 0, 0, 0)
        lower_l.setSpacing(4)

        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Filter commands or descriptions...')
        self.search.textChanged.connect(self._populate_commands)
        self.show_all_commands = QtWidgets.QCheckBox('All commands')
        self.show_all_commands.setChecked(False)
        self.show_all_commands.toggled.connect(self._populate_commands)
        search_row.addWidget(self.search)
        search_row.addWidget(self.show_all_commands)
        lower_l.addLayout(search_row)

        browser_split = QtWidgets.QSplitter()
        browser_split.setOrientation(QtCore.Qt.Vertical)

        self.command_list = QtWidgets.QListWidget()
        self.command_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.command_list.currentRowChanged.connect(self._show_command_details)
        self.command_list.itemDoubleClicked.connect(self._use_selected_command)
        browser_split.addWidget(self.command_list)

        btns = QtWidgets.QHBoxLayout()
        insert_btn = QtWidgets.QPushButton('Insert Command')
        insert_btn.clicked.connect(self._use_selected_command)
        multi_btn = QtWidgets.QPushButton('Open Selected Panel')
        multi_btn.clicked.connect(self._open_selected_panel)
        btns.addWidget(insert_btn)
        btns.addWidget(multi_btn)
        lower_l.addLayout(btns)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        browser_split.addWidget(self.details)
        browser_split.setSizes([220, 110])
        lower_l.addWidget(browser_split, stretch=1)

        main_split.addWidget(lower)
        main_split.setStretchFactor(0, 0)
        main_split.setStretchFactor(1, 1)
        main_split.setSizes([95, 315])
        layout.addWidget(main_split, stretch=1)

        self.response.setVisible(False)
        self.response.setMaximumHeight(120)
        self.log_toggle_btn.toggled.connect(
            lambda checked: (
                self.response.setVisible(bool(checked)),
                self.log_toggle_btn.setText('Hide Log' if checked else 'Show Log'),
            )
        )
        layout.addWidget(self.response, stretch=0)

    def _set_timeout(self, value):
        self.client.timeout = float(value)

    def _ioc_prefix_for_title(self):
        cmd_pv = self.cmd_pv.text().strip() if hasattr(self, 'cmd_pv') else ''
        m = re.match(r'^(.*):MCU-Cmd\.AOUT$', cmd_pv)
        return m.group(1) if m else ''

    def _open_caqtdm_main_panel(self):
        ioc_prefix = self._ioc_prefix_for_title() or ''
        macro = f'IOC={ioc_prefix}'
        try:
            cmd = f'caqtdm -macro "{macro}" ecmcMain.ui'
            subprocess.Popen(
                ['bash', '-lc', cmd],
                cwd=str(Path(__file__).resolve().parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f'Started caQtDM main panel ({macro})')
        except Exception as ex:
            self._log(f'Failed to start caQtDM main panel: {ex}')

    def _open_script_window(self, script_name, label, axis_id='1'):
        script = Path(__file__).with_name(script_name)
        if not script.exists():
            self._log(f'Launcher not found: {script.name}')
            return
        prefix = self._ioc_prefix_for_title() or 'IOC:ECMC'
        try:
            subprocess.Popen(
                ['bash', str(script), str(prefix), str(axis_id)],
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f'Started {label} window for axis {axis_id} (prefix {prefix})')
        except Exception as ex:
            self._log(f'Failed to start {label} window: {ex}')

    def _open_motion_window(self):
        self._open_script_window('start_mtn.sh', 'motion')

    def _open_cntrl_window(self):
        self._open_script_window('start_cntrl.sh', 'controller')

    def _open_axis_window(self):
        self._open_script_window('start_axis.sh', 'axis')

    def _is_config_only_command(self, cmd):
        return str(cmd or '').strip().startswith('Cfg.')

    def _confirm_config_only_command(self, cmd):
        if not self._is_config_only_command(cmd):
            return True
        text = str(cmd or '').strip()
        ret = QtWidgets.QMessageBox.question(
            self,
            'Confirm Config Command',
            (
                'This is a config-only command and may change configuration.\n'
                'WARNING: The IOC might crash if objects are created.\n\n'
                f'Execute?\n{text}'
            ),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        return ret == QtWidgets.QMessageBox.Yes

    def _set_readback_field(self, text):
        if hasattr(self, 'readback_edit'):
            self.readback_edit.setText(str(text or ''))

    def _log(self, msg):
        t = datetime.now().strftime('%H:%M:%S')
        self.response.appendPlainText(f'[{t}] {msg}')

    def _filtered_commands(self):
        txt = self.search.text().strip().lower()
        cmds = self.catalog.get('commands', [])
        show_all = bool(self.show_all_commands.isChecked()) if hasattr(self, 'show_all_commands') else False

        out = []
        for c in cmds:
            if (not show_all) and str(c.get('category', '') or '').strip().lower() == 'blocked':
                continue
            if not txt:
                out.append(c)
                continue
            hay = ' | '.join(
                [
                    c.get('command', ''),
                    c.get('command_named', c.get('command', '')),
                    c.get('name', ''),
                    c.get('category', ''),
                    c.get('description', ''),
                ]
            ).lower()
            if txt in hay:
                out.append(c)
        return out

    def _populate_commands(self):
        self.filtered = sorted(
            self._filtered_commands(),
            key=lambda c: (
                str(c.get('category', 'General') or '').lower(),
                str(c.get('command_named', c.get('command', '')) or '').lower(),
            ),
        )
        self.command_list.clear()
        for c in self.filtered:
            label = (
                f"[{c.get('category', 'General')}] "
                f"{c.get('command_named', c.get('command', ''))}"
            )
            params = c.get('param_names', []) or []
            tooltip = (
                f"Command: {c.get('command_named', c.get('command', ''))}\n"
                f"Parameters: {', '.join(params) if params else '-'}"
            )
            item = QtWidgets.QListWidgetItem(label)
            item.setToolTip(tooltip)
            if str(c.get('category', '') or '').strip().lower() == 'blocked':
                item.setForeground(QtGui.QColor('#8a8a8a'))
            self.command_list.addItem(item)
        if self.filtered:
            self.command_list.setCurrentRow(0)
        else:
            self.details.setPlainText('')

    def _show_command_details(self, row):
        if row < 0 or row >= len(getattr(self, 'filtered', [])):
            self.details.setPlainText('')
            return

        c = self.filtered[row]
        params = c.get('param_names', []) or []
        lines = [
            f"Command: {c.get('command', '')}",
            f"Command (named): {c.get('command_named', c.get('command', ''))}",
            f"Parameters: {', '.join(params) if params else '-'}",
            f"Runtime safe: {c.get('runtime_safe', False)}",
            f"Runtime class: {c.get('runtime_class', 'unknown')}",
            f"Runtime note: {c.get('runtime_note', '-') or '-'}",
            f"Name: {c.get('name', '')}",
            f"Category: {c.get('category', '')}",
            f"Description: {c.get('description', '') or '(no header description found)'}",
            f"Header: {c.get('header_source', '') or '-'}",
            f"Header example: {c.get('header_example', '') or '-'}",
            f"Parser command: {c.get('parser_command', '') or '-'}",
            f"Parser source: {c.get('parser_source', '')}",
        ]
        self.details.setPlainText('\n'.join(lines))

    def _selected_command(self):
        row = self.command_list.currentRow()
        if row < 0 or row >= len(getattr(self, 'filtered', [])):
            return None
        return self.filtered[row]

    def _selected_commands(self):
        rows = sorted({idx.row() for idx in self.command_list.selectedIndexes()})
        return [self.filtered[r] for r in rows if 0 <= r < len(self.filtered)]

    def _is_blocked_catalog_command(self, cmd_obj):
        if not cmd_obj:
            return False
        return str(cmd_obj.get('category', '') or '').strip().lower() == 'blocked'

    def _is_blocked_command_text(self, cmd_text):
        txt = normalize_float_literals(str(cmd_text or '').strip())
        if not txt:
            return False
        return txt in self.blocked_commands

    def _use_selected_command(self):
        c = self._selected_command()
        if not c:
            return
        if self._is_blocked_catalog_command(c):
            self._log('Blocked command cannot be inserted into Send Command field')
            return
        self.command_edit.setText(c.get('command_named', c.get('command', '')))

    def _copy_selected_command(self):
        c = self._selected_command()
        if not c:
            return
        QtWidgets.QApplication.clipboard().setText(c.get('command_named', c.get('command', '')))
        self._log('Copied selected command template to clipboard')

    def _open_selected_panel(self):
        commands = self._selected_commands()
        commands = [c for c in commands if not self._is_blocked_catalog_command(c)]
        if not commands:
            self._log('ERROR: Select one or more non-blocked commands first')
            return
        dlg = MultiCommandDialog(self, commands)
        self._child_windows.append(dlg)
        dlg.show()

    def send_raw_command(self, cmd):
        pv = self.cmd_pv.text().strip()
        cmd = normalize_float_literals((cmd or '').strip())
        if not pv:
            msg = 'ERROR: Command PV is empty'
            self._log(msg)
            return False, msg
        if not cmd:
            msg = 'ERROR: Command text is empty'
            self._log(msg)
            return False, msg
        if not self._confirm_config_only_command(cmd):
            msg = f'Canceled config command: {cmd}'
            self._log(msg)
            self._set_readback_field(msg)
            return False, msg
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
            self._set_readback_field(msg)
            return False, msg

        qp = self.qry_pv.text().strip()
        if not qp:
            msg = f'Command sent, no QRY PV configured: {cmd}'
            self._log(msg)
            self._set_readback_field(msg)
            return True, msg

        try:
            val = self.client.get(qp, as_string=True)
            msg = compact_query_message_value(f'QRY <- {qp}: {val}')
            self._log(msg)
            self._set_readback_field(val)
            return True, msg
        except Exception as ex:
            msg = f'ERROR query read: {ex}'
            self._log(msg)
            self._set_readback_field(msg)
            return False, msg

    def send_command(self):
        self.read_raw_command(self.command_edit.text())

    def proc_and_read_query(self):
        qp = self.qry_pv.text().strip()
        if not qp:
            self._log('ERROR: Query PV is empty')
            return
        try:
            proc_pv = _proc_pv_for_readback(qp)
            self.client.put(proc_pv, 1, wait=True)
            val = self.client.get(qp, as_string=True)
            self._set_readback_field(val)
            self._log(compact_query_message_value(f'QRY <- {qp}: {val}'))
        except Exception as ex:
            self._set_readback_field(f'ERROR query read: {ex}')
            self._log(f'ERROR query read: {ex}')

    def read_query_only(self):
        qp = self.qry_pv.text().strip()
        if not qp:
            self._log('ERROR: Query PV is empty')
            return
        try:
            val = self.client.get(qp, as_string=True)
            self._set_readback_field(val)
            self._log(compact_query_message_value(f'QRY <- {qp}: {val}'))
        except Exception as ex:
            self._set_readback_field(f'ERROR query read: {ex}')
            self._log(f'ERROR query read: {ex}')


def main():
    ap = argparse.ArgumentParser(description='Qt app to send ecmc commands via EPICS PVs')
    ap.add_argument('--catalog', default='ecmc_commands.json', help='Path to command catalog JSON')
    ap.add_argument('--blocklist', default='ecmc_commands_blocklist_all.json', help='Path to command blocklist JSON')
    ap.add_argument('--prefix', default='', help='PV prefix (e.g. IOC:ECMC)')
    ap.add_argument('--cmd-pv', default='', help='Command PV name (overrides --prefix)')
    ap.add_argument('--qry-pv', default='', help='Query PV name/readback PV (overrides --prefix)')
    ap.add_argument('--timeout', type=float, default=2.0, help='EPICS timeout in seconds')
    args = ap.parse_args()

    default_cmd_pv = args.cmd_pv.strip() if args.cmd_pv else _join_prefix_pv(args.prefix, 'MCU-Cmd.AOUT')
    default_qry_pv = args.qry_pv.strip() if args.qry_pv else _join_prefix_pv(args.prefix, 'MCU-Cmd.AINP')
    if not default_cmd_pv:
        default_cmd_pv = 'IOC:ECMC:CMD'
    if not default_qry_pv:
        default_qry_pv = 'IOC:ECMC:QRY'

    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow(
        catalog_path=args.catalog,
        blocklist_path=args.blocklist,
        default_cmd_pv=default_cmd_pv,
        default_qry_pv=default_qry_pv,
        timeout=args.timeout,
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
