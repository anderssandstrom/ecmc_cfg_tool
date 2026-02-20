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
    from PyQt5 import QtCore, QtWidgets
except Exception:
    from PySide6 import QtCore, QtWidgets  # type: ignore


PLACEHOLDER_RE = re.compile(r'<([^>]+)>')


class EpicsClient:
    def __init__(self, timeout=2.0):
        self.timeout = timeout
        self.backend = None
        self._epics = None

        try:
            import epics  # type: ignore

            self._epics = epics
            self.backend = 'pyepics'
            return
        except Exception:
            pass

        if shutil.which('caput') and shutil.which('caget'):
            self.backend = 'cli'
            return

        raise RuntimeError('No EPICS client available. Install pyepics or ensure caget/caput are in PATH.')

    def put(self, pv, value, wait=True):
        if self.backend == 'pyepics':
            ok = self._epics.caput(pv, value, wait=wait, timeout=self.timeout)
            if ok is None:
                raise RuntimeError(f'caput failed for {pv}')
            return

        proc = subprocess.run(['caput', '-t', pv, str(value)], text=True, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'caput failed for {pv}')

    def get(self, pv, as_string=True):
        if self.backend == 'pyepics':
            val = self._epics.caget(pv, as_string=as_string, timeout=self.timeout)
            if val is None:
                raise RuntimeError(f'caget failed for {pv}')
            return str(val)

        proc = subprocess.run(['caget', '-t', pv], text=True, capture_output=True)
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
            self.spin = QtWidgets.QDoubleSpinBox()
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
            self.spin = QtWidgets.QDoubleSpinBox()
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
                self.edit.setText(f'{float(val):g}')
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
        self.result.setText(msg)


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
                return f'{float(w.value()):g}'
            return str(int(w.value()))
        if isinstance(w, QtWidgets.QDoubleSpinBox):
            return f'{float(w.value()):g}'
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
        copy_btn = QtWidgets.QPushButton('Copy')
        write_btn.setMaximumWidth(58)
        read_btn.setMaximumWidth(58)
        copy_btn.setMaximumWidth(58)
        actions_l.addWidget(write_btn)
        actions_l.addWidget(read_btn)
        actions_l.addWidget(copy_btn)
        self.table.setCellWidget(row_idx, 2, actions)

        result = QtWidgets.QLabel('')
        result.setWordWrap(False)
        self.table.setCellWidget(row_idx, 3, result)

        row = {
            'template': template,
            'param_names': param_names,
            'param_widgets': param_widgets,
            'param_widget_list': param_widget_list,
            'result': result,
        }
        self.rows.append(row)

        write_btn.clicked.connect(lambda _=False, i=row_idx: self._write_row(i))
        read_btn.clicked.connect(lambda _=False, i=row_idx: self._read_row(i))
        copy_btn.clicked.connect(lambda _=False, i=row_idx: self._copy_row(i))

    def _build_command(self, row_idx):
        row = self.rows[row_idx]
        values = {n: self._widget_value(w) for n, w in row['param_widgets'].items()}
        return fill_template(row['template'], values)

    def _write_row(self, row_idx):
        cmd = self._build_command(row_idx).strip()
        ok, msg = self.parent_window.send_raw_command(cmd)
        self.rows[row_idx]['result'].setText(msg)

    def _read_row(self, row_idx):
        cmd = self._build_command(row_idx).strip()
        ok, msg = self.parent_window.read_raw_command(cmd)
        self.rows[row_idx]['result'].setText(msg)

    def _copy_row(self, row_idx):
        cmd = self._build_command(row_idx).strip()
        QtWidgets.QApplication.clipboard().setText(cmd)
        self.rows[row_idx]['result'].setText('Copied')


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, catalog_path, favorites_path, default_cmd_pv, default_qry_pv, timeout):
        super().__init__()
        self.setWindowTitle('ecmc Stream Command Client')
        self.resize(1280, 840)

        self.client = EpicsClient(timeout=timeout)
        self.catalog = self._load_catalog(catalog_path)
        self.favorites_path = Path(favorites_path)
        self.favorites = self._load_favorites(self.favorites_path)
        self._child_windows = []

        self._build_ui(default_cmd_pv, default_qry_pv, timeout)
        self._populate_commands()
        self._populate_favorites()
        self._log(f'Connected via backend: {self.client.backend}')

    def _load_catalog(self, path):
        p = Path(path)
        if not p.exists():
            return {'commands': []}
        try:
            return json.loads(p.read_text())
        except Exception:
            return {'commands': []}

    def _load_favorites(self, path):
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                return [str(x) for x in data]
            if isinstance(data, dict) and isinstance(data.get('favorites'), list):
                return [str(x) for x in data.get('favorites', [])]
        except Exception:
            pass
        return []

    def _save_favorites(self):
        payload = {'favorites': self.favorites}
        self.favorites_path.write_text(json.dumps(payload, indent=2) + '\n')

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
        self.timeout_edit.valueChanged.connect(self._set_timeout)

        cfg.addWidget(QtWidgets.QLabel('Command PV'), 0, 0)
        cfg.addWidget(self.cmd_pv, 0, 1)
        cfg.addWidget(QtWidgets.QLabel('Query PV'), 1, 0)
        cfg.addWidget(self.qry_pv, 1, 1)
        cfg.addWidget(QtWidgets.QLabel('Timeout [s]'), 2, 0)
        cfg.addWidget(self.timeout_edit, 2, 1)
        layout.addWidget(cfg_group)

        split = QtWidgets.QSplitter()
        split.setOrientation(QtCore.Qt.Horizontal)
        layout.addWidget(split, stretch=1)

        left = QtWidgets.QWidget()
        left_l = QtWidgets.QVBoxLayout(left)

        send_group = QtWidgets.QGroupBox('Send Command')
        send_l = QtWidgets.QVBoxLayout(send_group)
        self.command_edit = QtWidgets.QLineEdit('GetControllerError()')
        self.command_edit.returnPressed.connect(self.send_command)
        btn_row = QtWidgets.QHBoxLayout()
        send_btn = QtWidgets.QPushButton('Send to CMD PV')
        send_btn.clicked.connect(self.send_command)
        clear_btn = QtWidgets.QPushButton('Clear Command')
        clear_btn.clicked.connect(lambda: self.command_edit.setText(''))
        fav_add_btn = QtWidgets.QPushButton('Add to Favorites')
        fav_add_btn.clicked.connect(self._add_current_to_favorites)
        btn_row.addWidget(send_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addWidget(fav_add_btn)
        send_l.addWidget(self.command_edit)
        send_l.addLayout(btn_row)
        left_l.addWidget(send_group)

        fav_group = QtWidgets.QGroupBox('Favorites')
        fav_l = QtWidgets.QVBoxLayout(fav_group)
        self.favorite_filter = QtWidgets.QLineEdit()
        self.favorite_filter.setPlaceholderText('Filter favorites...')
        self.favorite_filter.textChanged.connect(self._populate_favorites)
        fav_l.addWidget(self.favorite_filter)
        self.favorite_list = QtWidgets.QListWidget()
        self.favorite_list.itemDoubleClicked.connect(self._use_selected_favorite)
        fav_l.addWidget(self.favorite_list)
        fav_btns = QtWidgets.QHBoxLayout()
        fav_use = QtWidgets.QPushButton('Use Favorite')
        fav_use.clicked.connect(self._use_selected_favorite)
        fav_del = QtWidgets.QPushButton('Remove Favorite')
        fav_del.clicked.connect(self._remove_selected_favorite)
        fav_save = QtWidgets.QPushButton('Save Favorites')
        fav_save.clicked.connect(self._save_favorites_clicked)
        fav_btns.addWidget(fav_use)
        fav_btns.addWidget(fav_del)
        fav_btns.addWidget(fav_save)
        fav_l.addLayout(fav_btns)
        left_l.addWidget(fav_group)

        qry_group = QtWidgets.QGroupBox('Query PV')
        qry_l = QtWidgets.QHBoxLayout(qry_group)
        proc_btn = QtWidgets.QPushButton('PROC + Read QRY')
        proc_btn.clicked.connect(self.proc_and_read_query)
        read_btn = QtWidgets.QPushButton('Read QRY Only')
        read_btn.clicked.connect(self.read_query_only)
        qry_l.addWidget(proc_btn)
        qry_l.addWidget(read_btn)
        left_l.addWidget(qry_group)

        self.response = QtWidgets.QPlainTextEdit()
        self.response.setReadOnly(True)
        left_l.addWidget(self.response, stretch=1)

        split.addWidget(left)

        right = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right)

        search_row = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Filter commands or descriptions...')
        self.search.textChanged.connect(self._populate_commands)
        self.runtime_only = QtWidgets.QCheckBox('Runtime-safe only')
        self.runtime_only.toggled.connect(self._populate_commands)
        search_row.addWidget(self.search)
        search_row.addWidget(self.runtime_only)
        right_l.addLayout(search_row)

        self.command_list = QtWidgets.QListWidget()
        self.command_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.command_list.currentRowChanged.connect(self._show_command_details)
        self.command_list.itemDoubleClicked.connect(self._use_selected_command)
        right_l.addWidget(self.command_list, stretch=1)

        btns = QtWidgets.QHBoxLayout()
        insert_btn = QtWidgets.QPushButton('Insert Template')
        insert_btn.clicked.connect(self._use_selected_command)
        copy_btn = QtWidgets.QPushButton('Copy Template')
        copy_btn.clicked.connect(self._copy_selected_command)
        add_fav_btn = QtWidgets.QPushButton('Template -> Favorites')
        add_fav_btn.clicked.connect(self._add_selected_template_to_favorites)
        multi_btn = QtWidgets.QPushButton('Open Selected Panel')
        multi_btn.clicked.connect(self._open_selected_panel)
        btns.addWidget(insert_btn)
        btns.addWidget(copy_btn)
        btns.addWidget(add_fav_btn)
        btns.addWidget(multi_btn)
        right_l.addLayout(btns)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        right_l.addWidget(self.details, stretch=1)

        split.addWidget(right)
        split.setSizes([680, 620])

    def _set_timeout(self, value):
        self.client.timeout = float(value)

    def _log(self, msg):
        t = datetime.now().strftime('%H:%M:%S')
        self.response.appendPlainText(f'[{t}] {msg}')

    def _filtered_commands(self):
        txt = self.search.text().strip().lower()
        cmds = self.catalog.get('commands', [])
        runtime_only = bool(self.runtime_only.isChecked())

        out = []
        for c in cmds:
            if runtime_only and not bool(c.get('runtime_safe', False)):
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
        self.filtered = self._filtered_commands()
        self.command_list.clear()
        for c in self.filtered:
            label = (
                f"[{c.get('runtime_class', 'unknown')}] "
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

    def _use_selected_command(self):
        c = self._selected_command()
        if not c:
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
        if not commands:
            self._log('ERROR: Select one or more commands first')
            return
        dlg = MultiCommandDialog(self, commands)
        self._child_windows.append(dlg)
        dlg.show()

    def _add_to_favorites(self, command_text):
        cmd = command_text.strip()
        if not cmd:
            self._log('ERROR: Empty command cannot be added to favorites')
            return
        if cmd in self.favorites:
            self._log('Favorite already exists')
            return
        self.favorites.append(cmd)
        self._populate_favorites()
        self._save_favorites()
        self._log(f'Added favorite: {cmd}')

    def _populate_favorites(self):
        self.favorite_list.clear()
        txt = self.favorite_filter.text().strip().lower() if hasattr(self, 'favorite_filter') else ''
        for cmd in self.favorites:
            if txt and txt not in cmd.lower():
                continue
            self.favorite_list.addItem(cmd)

    def _add_current_to_favorites(self):
        self._add_to_favorites(self.command_edit.text())

    def _add_selected_template_to_favorites(self):
        c = self._selected_command()
        if c:
            self._add_to_favorites(c.get('command_named', c.get('command', '')))

    def _selected_favorite_text(self):
        item = self.favorite_list.currentItem()
        if not item:
            return ''
        return item.text().strip()

    def _use_selected_favorite(self):
        cmd = self._selected_favorite_text()
        if not cmd:
            return
        self.command_edit.setText(cmd)

    def _remove_selected_favorite(self):
        cmd = self._selected_favorite_text()
        if not cmd:
            self._log('ERROR: No favorite selected')
            return
        self.favorites = [x for x in self.favorites if x != cmd]
        self._populate_favorites()
        self._save_favorites()
        self._log(f'Removed favorite: {cmd}')

    def _save_favorites_clicked(self):
        self._save_favorites()
        self._log(f'Saved favorites: {self.favorites_path}')

    def send_raw_command(self, cmd):
        pv = self.cmd_pv.text().strip()
        cmd = (cmd or '').strip()
        if not pv:
            msg = 'ERROR: Command PV is empty'
            self._log(msg)
            return False, msg
        if not cmd:
            msg = 'ERROR: Command text is empty'
            self._log(msg)
            return False, msg
        try:
            self.client.put(pv, cmd, wait=True)
            msg = f'CMD -> {pv}: {cmd}'
            self._log(msg)
            return True, msg
        except Exception as ex:
            msg = f'ERROR sending command: {ex}'
            self._log(msg)
            return False, msg

    def read_raw_command(self, cmd):
        ok, msg = self.send_raw_command(cmd)
        if not ok:
            return False, msg

        qp = self.qry_pv.text().strip()
        if not qp:
            msg = f'Command sent, no QRY PV configured: {cmd}'
            self._log(msg)
            return True, msg

        try:
            self.client.put(f'{qp}.PROC', 1, wait=True)
            val = self.client.get(qp, as_string=True)
            msg = f'QRY <- {qp}: {val}'
            self._log(msg)
            return True, msg
        except Exception as ex:
            msg = f'ERROR query read: {ex}'
            self._log(msg)
            return False, msg

    def send_command(self):
        self.send_raw_command(self.command_edit.text())

    def proc_and_read_query(self):
        qp = self.qry_pv.text().strip()
        if not qp:
            self._log('ERROR: Query PV is empty')
            return
        try:
            self.client.put(f'{qp}.PROC', 1, wait=True)
            val = self.client.get(qp, as_string=True)
            self._log(f'QRY <- {qp}: {val}')
        except Exception as ex:
            self._log(f'ERROR query read: {ex}')

    def read_query_only(self):
        qp = self.qry_pv.text().strip()
        if not qp:
            self._log('ERROR: Query PV is empty')
            return
        try:
            val = self.client.get(qp, as_string=True)
            self._log(f'QRY <- {qp}: {val}')
        except Exception as ex:
            self._log(f'ERROR query read: {ex}')


def main():
    ap = argparse.ArgumentParser(description='Qt app to send ecmc commands via EPICS PVs')
    ap.add_argument('--catalog', default='ecmc_commands.json', help='Path to command catalog JSON')
    ap.add_argument('--favorites', default='ecmc_favorites.json', help='Path to favorites JSON')
    ap.add_argument('--cmd-pv', default='IOC:ECMC:CMD', help='Command PV name')
    ap.add_argument('--qry-pv', default='IOC:ECMC:QRY', help='Query PV name')
    ap.add_argument('--timeout', type=float, default=2.0, help='EPICS timeout in seconds')
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow(
        catalog_path=args.catalog,
        favorites_path=args.favorites,
        default_cmd_pv=args.cmd_pv,
        default_qry_pv=args.qry_pv,
        timeout=args.timeout,
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
