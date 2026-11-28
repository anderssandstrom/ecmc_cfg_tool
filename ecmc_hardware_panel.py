"""Native EtherCAT process signal browser with explicit write controls."""
import re
from ecmc_compact_layout import column_count, visible_width, BoundedTabWidget
from threading import Lock
from qt_compat import QtCore, QtWidgets
from ecmc_epics import EpicsClient
from ecmc_hardware_catalog import hardware_pvs, hardware_writable_pvs, hardware_views, hardware_commands
from ecmc_hardware_options import variant, read_options, allowed_signal, demo_options, overview_signal, option_pages, option_tab
from ecmc_hardware_write import WriteTask, EnumTask
from ecmc_hardware_demo import HardwareDemo
from ecmc_waveform_widget import WaveformValue, waveform_summary
from ecmc_hardware_widgets import HardwareSymbol, CompactBit, SignalValue, WordValue, BooleanValue, BooleanStatus, AnalogValue, boolean_value, signal_role, AlarmSummary, signal_caption, digital_pair_status, is_bit_array, DigitalArrayValue
from ecmc_ds402_widget import DS402Status, DS402Word, DS402StateMap, controlword_dialog
from ecmc_ds402 import word_number, decode_state, TARGETS, transition_word
from ecmc_ds402_task import StateTask


class ReadSignals(QtCore.QObject):
    finished = QtCore.Signal(object) if hasattr(QtCore, "Signal") else QtCore.pyqtSignal(object)


class ReadTask(QtCore.QRunnable):
    def __init__(self, pvs, timeout):
        super().__init__()
        self.pvs, self.timeout = pvs, timeout
        self.signals = ReadSignals()

    def run(self):
        result = []
        try:
            client = EpicsClient(timeout=self.timeout)
        except Exception as ex:
            self.signals.finished.emit([(pv, None, str(ex)) for pv in self.pvs])
            return
        for pv in self.pvs:
            try:
                result.append((pv, client.get(pv), ""))
            except Exception as ex:
                result.append((pv, None, str(ex)))
        self.signals.finished.emit(result)


class OptionsTask(QtCore.QRunnable):
    def __init__(self, base, key, channels, timeout):
        super().__init__()
        self.args = base, key, channels, timeout
        self.signals = ReadSignals()

    def run(self):
        try:
            result = read_options(*self.args)
        except Exception:
            result = {}, "configuration unavailable"
        self.signals.finished.emit(result)


class HardwarePanel(QtWidgets.QWidget):
    def __init__(self, prefix, master, slaves, timeout=1.0, demo=False, on_plot=None):
        super().__init__()
        self.timeout, self.demo, self.on_plot = timeout, demo, on_plot
        self._task = None
        self._options_task = None
        self._options_note = ""
        self._expert_definitions = []
        self._expert_built = False
        self._closed = False
        self._write_task = None
        self._state_controls = []
        self.writable = {}
        self.commands = {}
        self._enum_task = None
        self.rows = {}
        self._monitor_lock = Lock()
        self._monitor_generation = 0
        self._active_scope = set()
        self._overview_pvs = set()
        self._option_tabs = None
        self._option_page_pvs = []
        self._option_grids = []
        self._expert_pvs = []
        self._expert_selector = None
        self._scope_initialized = False
        self._monitors = {}
        self._monitor_pending = {}
        self._monitor_connected = {}
        self._monitor_timer = QtCore.QTimer(self)
        self._monitor_timer.setInterval(250)
        self._monitor_timer.timeout.connect(self._monitor_tick)
        self._demo_model = HardwareDemo() if demo else None
        self._demo_timer = QtCore.QTimer(self)
        self._demo_timer.setInterval(250)
        self._demo_timer.timeout.connect(self._demo_tick)
        layout = QtWidgets.QVBoxLayout(self)
        self.selector = QtWidgets.QComboBox()
        for slave in slaves:
            self.selector.addItem(
                f"m{slave.get('master', master)} s{int(slave['id']):03d}  {slave.get('name', '')}",
                slave)
        self.prefix, self.master = prefix, master
        layout.addWidget(self.selector)
        self.selector.setVisible(self.selector.count() > 1)
        self.device_header = QtWidgets.QWidget()
        self.device_header_layout = QtWidgets.QHBoxLayout(self.device_header)
        self.device_header_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.device_header)
        self.status = QtWidgets.QLabel("Hardware diagnostics")
        layout.addWidget(self.status)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Signal", "Value", "Status", "PV"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.views = BoundedTabWidget()
        self.views.setSizePolicy(QtWidgets.QSizePolicy.Ignored,QtWidgets.QSizePolicy.Expanding)
        self.cards = QtWidgets.QScrollArea()
        self.cards.setWidgetResizable(True)
        self.cards.viewport().installEventFilter(self)
        self._channel_grid = None
        self._channel_widgets = []
        self._channel_columns = 0
        self.views.addTab(self.cards, "Overview")
        self.expert = QtWidgets.QScrollArea()
        self.expert.setWidgetResizable(True)
        self.views.addTab(self.expert, "Expert")
        self.views.addTab(self.table, "All PVs")
        layout.addWidget(self.views, 1)
        self.card_values = {}
        self._command_buttons = {}
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.table_menu)
        self.table.cellClicked.connect(self._table_click)
        buttons = QtWidgets.QHBoxLayout()
        self.refresh_button = QtWidgets.QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        self.live_check = QtWidgets.QCheckBox('Live')
        self.live_check.setChecked(True)
        self.live_check.setEnabled(not demo)
        self.live_check.toggled.connect(lambda: self._scope_changed(force=True))
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.live_check)
        self.write_enable = QtWidgets.QCheckBox("Enable simulated writes" if demo else "Enable writes")
        self._write_protected = False
        self.write_enable.setChecked(True)
        self.write_enable.setEnabled(True)
        self.write_enable.toggled.connect(self._update_edit_cursors)
        buttons.addWidget(self.write_enable)
        buttons.addStretch(1)
        self.write_enable.setToolTip("Arms direct single-click Boolean and controlword bit writes, which may enable motion. Numeric edits still require confirmation.")
        layout.addLayout(buttons)
        self.selector.currentIndexChanged.connect(lambda _: self.write_enable.setChecked(not self._write_protected))
        self.selector.currentIndexChanged.connect(self.refresh)
        self.views.currentChanged.connect(self._scope_changed)
        self.refresh()

    def set_write_protected(self, protected):
        self._write_protected = bool(protected)
        if protected and isinstance(self._write_task, StateTask):
            self._write_task.cancelled.set()
        self.write_enable.setChecked(not protected)
        self._update_edit_cursors()

    def refresh(self, *_):
        if self._closed or self._task is not None or self._write_task is not None or self._options_task is not None:
            return
        slave = self.selector.currentData()
        if not slave:
            return
        self._stop_monitors()
        self._scope_initialized = False
        candidates = slave.get("panels") or [slave.get("panel"), slave.get("name")]
        candidates = list(candidates) + [slave.get("panel"), slave.get("name")]
        key, channels = variant(candidates)
        self._options_note = ""
        if key and not self.demo:
            self.writable = {}
            self.commands = {}
            self.selector.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.live_check.setEnabled(False)
            self.status.setText("Reading hardware configuration…")
            base = f"{self.prefix.rstrip(':')}:m{int(slave.get('master', self.master))}s{int(slave['id']):03d}-"
            self._options_task = OptionsTask(base, key, channels, min(self.timeout, 1.0))
            self._options_task.signals.finished.connect(self._options_finished)
            QtCore.QThreadPool.globalInstance().start(self._options_task)
            return
        self._build_panel(slave, candidates, key, channels, demo_options(key, channels) if key and self.demo else {})

    def _options_finished(self, result):
        self._options_task = None
        if self._closed:
            return
        self.selector.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.live_check.setEnabled(True)
        slave = self.selector.currentData()
        candidates = list(slave.get("panels") or []) + [slave.get("panel"), slave.get("name")]
        key, channels = variant(candidates)
        opts, source = result
        self._options_note = " | " + source
        self._build_panel(slave, candidates, key, channels, opts)

    def _build_panel(self, slave, candidates, key, channels, opts):
        if key:
            candidates = [key]
        pairs = hardware_pvs(self.prefix, slave.get("master", self.master), slave["id"], candidates)
        if key:
            from ecmc_hardware_catalog import GENERIC
            pairs = [(s,pv) for s,pv in pairs if s in GENERIC or allowed_signal(s,key,channels,opts)]
        self.writable = hardware_writable_pvs(self.prefix, slave.get("master", self.master), slave["id"], candidates)
        self.commands = hardware_commands(self.prefix, slave.get('master', self.master), slave['id'], candidates)
        if key:
            self.writable = {}
            self.commands = {}
        for pv in self.commands:
            self.writable.setdefault(pv, 'caMessageButton')
        self.table.setRowCount(len(pairs))
        self.rows = {}
        for row, (label, pv) in enumerate(pairs):
            self.rows[pv] = row
            for col, value in enumerate((label, "—", "Demo: not read" if self.demo else "Pending", pv)):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        overview, experts = hardware_views(self.prefix, slave.get('master', self.master), slave['id'], candidates)
        if key:
            overview = [(s,pv) for s,pv in pairs if overview_signal(s)]
            details = [(s,pv) for s,pv in pairs if not overview_signal(s)]
            experts = [(name, [(s,pv) for s,pv in rows if pv in self.rows]) for name,rows in experts]
            experts = [(name, rows) for name,rows in experts if rows]
            if details:
                experts.insert(0, ('Configured diagnostics', details))
        self._overview_pvs = {pv for _, pv in overview}
        self._expert_pvs = [{pv for _, pv in pairs} for _, pairs in experts]
        self.build_cards(pairs if key else overview, opt=bool(key))
        self._expert_definitions = experts
        self._expert_built = False
        self._expert_selector = None
        self.expert.setWidget(QtWidgets.QLabel('Open Expert to load diagnostics'))
        self._scope_initialized = True
        self._scope_changed(force=True)

    def _visible_scope(self):
        if self.views.currentWidget() is self.table:
            return set(self.rows)
        if self.views.currentWidget() is self.expert:
            index = self._expert_selector.currentIndex() if self._expert_selector is not None else -1
            selected = self._expert_pvs[index] if 0 <= index < len(self._expert_pvs) else set()
            return set(self.device_values) | selected
        if self._option_tabs is not None:
            index=self._option_tabs.currentIndex()
            selected=self._option_page_pvs[index] if 0 <= index < len(self._option_page_pvs) else set()
            return set(self.device_values) | selected | set(self._alarm_values)
        return set(self.device_values) | self._overview_pvs

    def _invalidate_scope(self):
        for pv in self.rows:
            row = self.rows[pv]
            self.table.item(row, 1).setText("—")
            self.table.item(row, 2).setText("Paused")
            self.update_card(pv, None, "Not currently monitored")
        self._update_summary()
        self._update_edit_cursors()

    def _scope_changed(self, *_args, force=False):
        if not self._scope_initialized or self._closed:
            return
        if self.isVisible() and self.views.currentWidget() is self.expert and not self._expert_built:
            self._expert_built = True
            self.build_experts(self._expert_definitions)
        scope = self._visible_scope() if self.isVisible() else set()
        if scope == self._active_scope and not force:
            return
        self._stop_monitors()
        self._demo_timer.stop()
        self._active_scope = scope
        self._invalidate_scope()
        if not scope:
            self.status.setText("Monitoring paused — hardware tab hidden")
            return
        if self.demo:
            self._demo_tick()
            self._update_edit_cursors()
            self._demo_timer.start()
            self.status.setText(f"DEMO — {len(scope)} visible simulated PVs; no network requests")
            return
        if self._task is not None or self._write_task is not None:
            return
        if self.live_check.isChecked() and self._start_monitors():
            return
        self.status.setText("Reading visible PVs…")
        self.selector.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.live_check.setEnabled(False)
        self._snapshot_scope = set(scope)
        self._snapshot_generation = self._monitor_generation
        self._task = ReadTask(sorted(scope), self.timeout)
        self._update_edit_cursors()
        self._task.signals.finished.connect(self._finished)
        QtCore.QThreadPool.globalInstance().start(self._task)

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0, self._scope_changed)
        QtCore.QTimer.singleShot(0, self._reflow_channels)

    def hideEvent(self, event):
        if isinstance(self._write_task, StateTask):
            self._write_task.cancelled.set()
        super().hideEvent(event)
        if self._scope_initialized and not self._closed:
            self._scope_changed(force=True)

    def _stop_monitors(self):
        self._monitor_timer.stop()
        with self._monitor_lock:
            self._monitor_generation += 1
            self._monitor_pending.clear()
            self._monitor_connected.clear()
        old, self._monitors = self._monitors, {}
        for pv in old.values():
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass

    def _start_monitors(self):
        if self.demo:
            return False
        try:
            import epics
        except ImportError:
            return False
        generation = self._monitor_generation
        for name in sorted(self._active_scope):
            def connection(pvname=None, conn=None, key=name, **kwargs):
                with self._monitor_lock:
                    if generation != self._monitor_generation:
                        return
                    self._monitor_connected[key] = bool(conn)
                    if not conn:
                        self._monitor_pending[key] = (None, "Disconnected")
            def sample(pvname=None, value=None, severity=None, key=name, **kwargs):
                with self._monitor_lock:
                    if generation != self._monitor_generation or self._monitor_connected.get(key) is False:
                        return
                    self._monitor_pending[key] = (None, "Invalid alarm") if severity == 3 else (value, "")
            try:
                self._monitors[name] = epics.PV(name, auto_monitor=True,
                    callback=sample, connection_callback=connection)
            except Exception as ex:
                with self._monitor_lock:
                    self._monitor_pending[name] = (None, str(ex))
        if not self._monitors:
            self._stop_monitors()
            return False
        self.status.setText("Live CA monitoring — connecting…")
        self._monitor_timer.start()
        self._update_edit_cursors()
        return True

    def _monitor_tick(self):
        if self._closed:
            self._stop_monitors()
            return
        with self._monitor_lock:
            pending, self._monitor_pending = self._monitor_pending, {}
            connected = sum(self._monitor_connected.values())
        permissions_changed = False
        for pv, (value, error) in pending.items():
            row = self.rows.get(pv)
            if row is None:
                continue
            previous = self.table.item(row, 2).text() == 'Read OK'
            self._apply_value(pv, value, error)
            permissions_changed |= previous != (value is not None)
        if self._write_task is None:
            self.status.setText(f"Live CA monitoring — {connected}/{len(self._monitors)} connected | {len(self._monitors)} subscriptions" + self._options_note)
        if pending:
            self._update_summary()
        if permissions_changed:
            self._update_edit_cursors()

    def _demo_value(self, pv):
        slave = self.selector.currentData() or {}
        from ecmc_hardware_options import legacy_names
        candidates = list(slave.get("panels") or []) + [slave.get("panel"), slave.get("name")]
        key, channels = variant(candidates)
        if key:
            suffix = pv.rsplit(":", 1)[-1].split("-", 1)[-1]
            if suffix.startswith("Cfg"):
                if suffix == "CfgChannels":
                    return channels
                opts = demo_options(key, channels)
                for name, record in legacy_names(key, channels).items():
                    if suffix == record:
                        return opts[name]
        return self._demo_model.value(pv, slave.get("name", ""), slave.get("panel", ""))

    def _demo_tick(self):
        if self._closed or not self.demo:
            self._demo_timer.stop()
            return
        for pv in self._active_scope:
            self._apply_value(pv, self._demo_value(pv), '', simulated=True)
        self._update_summary()

    def _apply_value(self, pv, value, error, simulated=False):
        row = self.rows.get(pv)
        if row is None or pv not in self._active_scope:
            return
        is_waveform = pv.endswith("-Arr") and not is_bit_array(pv)
        summary = waveform_summary(value) if is_waveform else None
        text = (f"{summary[0]} samples · last {summary[1]:.6g}" if summary else "—") if is_waveform else str(value) if value is not None else "—"
        state = "Simulated" if simulated else "Read OK" if value is not None else "Unavailable"
        item = self.table.item(row, 2)
        if not is_waveform and self.table.item(row, 1).text() == text and item.text() == state and item.toolTip() == error:
            return
        self.table.item(row, 1).setText(text)
        item.setText(state)
        item.setToolTip(error)
        self.update_card(pv, value, error)

    def _finished(self, results):
        self._task = None
        if self._closed:
            return
        self.selector.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.live_check.setEnabled(True)
        if self._snapshot_generation != self._monitor_generation:
            self._scope_changed(force=True)
            return
        available = 0
        for pv, value, error in results:
            self._apply_value(pv, value, error)
            available += value is not None
        self._update_summary()
        self.status.setText(f"{available}/{len(results)} visible PVs read (snapshot)" + self._options_note)
        self._update_edit_cursors()

    def _bind(self, widget, pv):
        widget.setToolTip(pv)
        widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, w=widget, pv=pv: self.pv_menu(w.mapToGlobal(pos), pv))

    def _click_write(self, pv):
        if not self._can_write(pv):
            return False
        self._write_pv(pv)
        return True

    def _table_click(self, row, column):
        if column == 1 and self.table.item(row, 3) is not None:
            self._click_write(self.table.item(row, 3).text())

    def _update_edit_cursors(self, *_):
        for mapping,targets,apply in self._state_controls:
            target=targets.currentText()
            trigger=mapping.get({'Operation enabled':'TrgDS402Ena','Switch on disabled':'TrgDS402Dis'}.get(target))
            pv=trigger if trigger in self.commands else mapping.get('Cmd')
            row=self.rows.get(mapping.get('Stat'))
            apply.setEnabled(bool(row is not None and self.table.item(row,2).text() in ('Read OK','Simulated') and self._can_write(pv)))
        for pv, buttons in self._command_buttons.items():
            for button in buttons:
                button.setEnabled(self._can_write(pv))
        for pv, widgets in self.card_values.items():
            for _, widget in widgets:
                editable = self._can_write(pv) and not (isinstance(widget, WordValue) and (getattr(widget, 'status_word', False) or pv.endswith('-RB')))
                if getattr(widget, '_editable', None) != editable:
                    widget.setCursor(QtCore.Qt.PointingHandCursor if editable else QtCore.Qt.ArrowCursor)
                    widget.set_editable(editable)

    def _value_widget(self, name, pv, primary=False):
        # EL70x1 original panel defines both words as bits 0..15.
        slave = self.selector.currentData() or {}
        panel = str(slave.get('panel') or '')
        candidates = slave.get('panels') or [panel, slave.get('name', '')]
        known_word = any(str(c).removeprefix('ecmc').removesuffix('.ui') == 'EL70x1' for c in candidates) and name in ('Drv01-Cmd', 'Drv01-Stat')
        kind = self.writable.get(pv)
        if is_bit_array(name):
            value = DigitalArrayValue(lambda pv=pv: self._click_write(pv), bit_callback=(lambda old,bit,pv=pv:self._direct_array_bit(pv,old,bit)) if kind=='caByteController' and name.startswith('BO') and not name.endswith('-RB') else None)
        elif name.endswith('-Arr'):
            value = WaveformValue()
        elif kind == 'caToggleButton':
            value = BooleanValue(lambda old, new, pv=pv: self._direct_boolean(pv, old, new))
        elif kind is None and (re.search(r'(?:^|-)(BI|BO)\d+(-RB)?$', name) or name.endswith(('Alrm','Rdy','RdyStat','RdyToEna','EnaAct','EnaCmd-RB'))):
            alarm = name.endswith('Alrm')
            value = BooleanStatus(('#b7791f' if name.endswith('WrnAlrm') else '#c43c3c') if alarm else '#26804b', 'FAULT' if alarm else 'On', 'OK' if alarm else 'Off')
        elif re.fullmatch(r'(AI|AO)\d+(-RB)?', name):
            value = AnalogValue(lambda pv=pv: self._click_write(pv), command=name.startswith('AO') and not name.endswith('-RB'))
        elif known_word:
            value = WordValue(lambda pv=pv: self._click_write(pv),
                              bit_callback=(lambda old, bit, pv=pv: self._direct_bit(pv, old, bit)) if kind == 'caByteController' and name.endswith('-Cmd') else None)
        else:
            value = SignalValue("—", lambda pv=pv: self._click_write(pv))
        if not isinstance(value, WaveformValue):
            value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if primary and not isinstance(value, WaveformValue):
            font = value.font()
            font.setPointSize(max(13, font.pointSize() + 4))
            font.setBold(True)
            value.setFont(font)
        self._bind(value, pv)
        self.card_values.setdefault(pv, []).append((name, value))
        return value

    def _group(self, title, rows):
        kind = re.sub(r"\d+$", "", title)
        number = title[len(kind):]
        display = {'Drv':'Drive', 'Enc':'Encoder', 'Tp':'Touch probe', 'AI':'Analog input', 'AO':'Analog output'}.get(kind)
        card = QtWidgets.QGroupBox(f'{display} {number}' if display else 'Channel '+number if kind=='CH' else title)
        card.setMaximumWidth(540)
        outer = QtWidgets.QVBoxLayout(card)
        outer.setContentsMargins(4, 2, 4, 2)
        outer.setSpacing(3)
        kind = re.sub(r"\d+$", "", title)
        if kind in ('AI', 'AO'):
            outer.addWidget(self._analog_channel(kind, rows))
            return card
        primary = [r for r in rows if signal_role(kind, r[2]) == "primary"] if kind in ("Drv", "Enc") else []
        status = [r for r in rows if signal_role(kind, r[2]) == "status"] if kind in ("Drv", "Enc") else []
        detail = [r for r in rows if r not in primary and r not in status]
        ds402 = kind == 'Drv' and self._is_ds402(title)
        if ds402:
            dedicated = [r for r in rows if r[2] in ('Stat', 'Cmd', 'Cmd-RB', 'TrgDS402Ena', 'TrgDS402Dis')]
            if dedicated:
                outer.addWidget(self._ds402_group(dedicated))
                detail = [r for r in detail if r not in dedicated]
                primary = [r for r in primary if r not in dedicated]
                status = [r for r in status if r not in dedicated]
        if primary:
            hero = QtWidgets.QHBoxLayout()
            # Values are more useful than a repeated decorative schematic.
            measurements = QtWidgets.QVBoxLayout()
            for name, pv, short in primary:
                line = QtWidgets.QHBoxLayout()
                key = QtWidgets.QLabel(signal_caption(short))
                self._bind(key, pv)
                line.addWidget(key)
                line.addWidget(self._value_widget(name, pv, primary=True), 1)
                measurements.addLayout(line)
            hero.addLayout(measurements, 1)
            outer.addLayout(hero)
        if status:
            leds = QtWidgets.QGridLayout()
            leds.setHorizontalSpacing(5)
            leds.setVerticalSpacing(1)
            for i, (name, pv, short) in enumerate(status):
                label = QtWidgets.QLabel(signal_caption(short))
                self._bind(label, pv)
                leds.addWidget(label, i // 2, (i % 2) * 2)
                leds.addWidget(self._value_widget(name, pv), i // 2, (i % 2) * 2 + 1)
            outer.addLayout(leds)
        command_rows = [(name,pv) for name,pv,short in rows if pv in self.commands and not (ds402 and short in ('Cmd','TrgDS402Ena','TrgDS402Dis'))]
        if command_rows:
            actions = QtWidgets.QHBoxLayout()
            for name,pv in command_rows:
                button = QtWidgets.QPushButton(self.commands[pv]['label'])
                button.setEnabled(False)
                self._bind(button,pv)
                button.clicked.connect(lambda checked=False, pv=pv: self._execute_command(pv))
                self._command_buttons.setdefault(pv,[]).append(button)
                actions.addWidget(button)
            outer.addLayout(actions)
        detail = [r for r in detail if self.writable.get(r[1]) != 'caMessageButton']
        if detail:
            form = QtWidgets.QGridLayout()
            form.setVerticalSpacing(2)
            for row, (name, pv, short) in enumerate(detail):
                key = QtWidgets.QLabel(signal_caption(short))
                self._bind(key, pv)
                form.addWidget(key, row, 0)
                form.addWidget(self._value_widget(name, pv), row, 1)
            form.setColumnStretch(1, 1)
            outer.addLayout(form)
        return card

    def _is_ds402(self, title):
        # Require explicit DS402 trigger PVs in the catalog, not merely Cmd/Stat.
        suffixes = {pv.rsplit(':', 1)[-1].split('-', 1)[-1] for pv in self.rows}
        return title + '-TrgDS402Ena' in suffixes and title + '-TrgDS402Dis' in suffixes

    def _state_request(self, mapping, target):
        status=mapping.get('Stat')
        control=mapping.get('Cmd')
        trigger=mapping.get({'Operation enabled':'TrgDS402Ena','Switch on disabled':'TrgDS402Dis'}.get(target))
        if trigger not in self.commands:
            trigger=None
        if self.writable.get(control)!='caByteController':
            control=None
        if not status or not self._can_write(trigger or control):
            return
        row=self.rows.get(status)
        if row is None or self.table.item(row,2).text() not in ('Read OK','Simulated'):
            return
        if not self.demo and QtWidgets.QMessageBox.question(
                self,'Confirm drive state',f'Request {target}? This may enable motion or remove torque.',
                QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:
            return
        if not self._can_write(trigger or control):
            return
        if self.demo:
            try:
                if trigger:
                    self._demo_model.write(trigger,1,ds402=True)
                else:
                    for _ in range(8):
                        state=decode_state(self._demo_value(status))
                        if state==target or (target=='Reset fault' and state!='Fault'):
                            break
                        word=transition_word(state,target,word_number(self._demo_value(control)))
                        if word is None: break
                        self._demo_model.write(control,word,ds402=True)
                self._demo_tick()
                if target!='Reset fault' and decode_state(self._demo_value(status))!=target:
                    raise RuntimeError('Target not reached')
            except Exception as ex:
                QtWidgets.QMessageBox.warning(self,'State request',str(ex))
            return
        self.selector.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.status.setText('Requesting '+target)
        self._write_task=StateTask(status,control,trigger,target,self.timeout)
        self._write_task.signals.finished.connect(self._write_finished)
        self._update_edit_cursors()
        QtCore.QThreadPool.globalInstance().start(self._write_task)

    def _ds402_group(self, rows):
        group = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(group)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(3)
        by_suffix = {short: (name,pv) for name,pv,short in rows}
        mapping={short:pv for short,(name,pv) in by_suffix.items()}
        if 'Stat' in by_suffix:
            name,pv=by_suffix['Stat']
            state=DS402Status()
            self._bind(state,pv)
            self.card_values.setdefault(pv,[]).append((name,state))
            layout.addWidget(state)
            targets=QtWidgets.QComboBox()
            for target in TARGETS:
                trigger=mapping.get({'Operation enabled':'TrgDS402Ena','Switch on disabled':'TrgDS402Dis'}.get(target))
                if trigger in self.commands or self.writable.get(mapping.get('Cmd'))=='caByteController':
                    targets.addItem(target)
            if targets.count():
                line=QtWidgets.QHBoxLayout()
                targets.setCurrentText('Operation enabled')
                apply=QtWidgets.QPushButton('Apply')
                line.addWidget(targets,1)
                line.addWidget(apply)
                layout.addLayout(line)
                apply.clicked.connect(lambda checked=False,m=mapping,t=targets:self._state_request(m,t.currentText()))
                self._state_controls.append((mapping,targets,apply))
                targets.currentTextChanged.connect(self._update_edit_cursors)
                apply.setEnabled(False)
        details=QtWidgets.QToolButton()
        details.setText('Words / bits')
        details.setCheckable(True)
        details.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        details.setArrowType(QtCore.Qt.RightArrow)
        layout.addWidget(details,0,QtCore.Qt.AlignLeft)
        pane=QtWidgets.QWidget()
        grid=QtWidgets.QGridLayout(pane)
        grid.setContentsMargins(0,0,0,0)
        grid.setSpacing(2)
        for row,short in enumerate(('Cmd','Cmd-RB','Stat')):
            if short not in by_suffix: continue
            name,pv=by_suffix[short]
            label=QtWidgets.QLabel({'Cmd':'CW','Cmd-RB':'CW RB','Stat':'SW'}[short])
            self._bind(label,pv)
            grid.addWidget(label,row,0)
            word=DS402Word(lambda pv=pv,short=short:self._click_write(pv) if short=='Cmd' else False,
                          status=short!='Cmd',
                          bit_callback=(lambda old,bit,pv=pv:self._direct_bit(pv,old,bit)) if short=='Cmd' and self.writable.get(pv)=='caByteController' else None)
            self._bind(word,pv)
            self.card_values.setdefault(pv,[]).append((name,word))
            grid.addWidget(word,row,1)
            number=SignalValue('—',lambda pv=pv,short=short:self._click_write(pv) if short=='Cmd' else False)
            self._bind(number,pv)
            number.setProperty('ds402_hex',True)
            self.card_values.setdefault(pv,[]).append((name,number))
            grid.addWidget(number,row,2)
        pane.hide()
        details.toggled.connect(pane.setVisible)
        details.toggled.connect(lambda on:details.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow))
        layout.addWidget(pane)
        return group

    def build_cards(self, pairs, opt=False):
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.card_values = {}
        self._command_buttons = {}
        self._state_controls = []
        self.device_values = {}
        self._digital_pairs = []
        self._alarm_values = {}
        self._hardware_alarm = AlarmSummary()
        layout.addWidget(self._hardware_alarm)
        header = self.device_header_layout
        while header.count():
            item = header.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._option_tabs=None
        self._option_page_pvs=[]
        self._option_grids=[]
        content=[]
        for name,pv in pairs:
            if name.endswith('Alrm') or name.endswith(('-NotValid','-OvrLoad')) or name in ('Online', 'Operational'):
                self._alarm_values[pv] = (name, None)
            if name == "PnlTyp":
                continue
            if name in ("HWType", "Online", "Operational") or name.startswith("Alstate-"):
                label = QtWidgets.QLabel("—" if name == "HWType" else "○ " + name.replace("Alstate-", ""))
                self._bind(label, pv)
                header.addWidget(label)
                self.device_values[pv] = (name, label)
                continue
            content.append((name,pv))
        header.addStretch(1)
        if opt:
            tabs=BoundedTabWidget()
            tabs.setUsesScrollButtons(True)
            tabs.setSizePolicy(QtWidgets.QSizePolicy.Ignored,QtWidgets.QSizePolicy.Expanding)
            self._option_tabs=tabs
            for title,rows in option_pages(content):
                page=QtWidgets.QWidget()
                page_layout=QtWidgets.QVBoxLayout(page)
                page_layout.setContentsMargins(3,3,3,3)
                page_layout.setSpacing(3)
                grid,widgets=self._build_channel_page(page_layout,rows)
                scroll=QtWidgets.QScrollArea()
                scroll.setWidgetResizable(True)
                scroll.setWidget(page)
                scroll.viewport().installEventFilter(self)
                tabs.addTab(scroll,title)
                self._option_grids.append((grid,widgets))
                self._option_page_pvs.append({pv for _,pv in rows})
            layout.addWidget(tabs,1)
            tabs.currentChanged.connect(self._option_page_changed)
            self._channel_grid=None
            self._channel_widgets=[]
            self._channel_columns=0
        else:
            self._channel_grid,self._channel_widgets=self._build_channel_page(layout,content)
            self._channel_columns=0
        self.cards.setWidget(root)
        if opt:
            self._select_option_grid()
        self._reflow_channels()
        QtCore.QTimer.singleShot(0,self._reflow_channels)

    def _select_option_grid(self):
        if self._option_tabs is None:
            return
        index=self._option_tabs.currentIndex()
        if 0<=index<len(self._option_grids):
            self._channel_grid,self._channel_widgets=self._option_grids[index]
            self._channel_columns=0

    def _option_page_changed(self,*_):
        if isinstance(self._write_task,StateTask):
            self._write_task.cancelled.set()
        self._select_option_grid()
        self._reflow_channels()
        QtCore.QTimer.singleShot(0,self._reflow_channels)
        self._scope_changed()

    def _build_channel_page(self,layout,pairs):
        channels, other, digital = {}, [], {'BI': [], 'BO': []}
        for name, pv in pairs:
            io = re.fullmatch(r"(BI|BO)(\d+)(.*)", name)
            if io:
                digital[io[1]].append((name, pv, int(io[2]), io[3]))
                continue
            m = re.fullmatch(r"(Drv|Enc|Tp|AI|AO|BI|BO|CH)(\d+)(?:-?(.*))", name)
            if m:
                kind, number, suffix = m.groups()
                channels.setdefault(int(number), {}).setdefault(kind + number, []).append((name, pv, suffix or "Value"))
            else:
                other.append((name, pv, name))
        for kind, signals in digital.items():
            if signals:
                layout.addWidget(self._digital_group(kind, signals), 0, QtCore.Qt.AlignLeft)
        grid = QtWidgets.QGridLayout()
        widgets = []
        for i, (ch, groups) in enumerate(sorted(channels.items())):
            col = QtWidgets.QWidget()
            col.setMaximumWidth(360)
            col.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
            stack = QtWidgets.QVBoxLayout(col)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(3)
            title = QtWidgets.QLabel(f"Channel {ch:02d}")
            title.setStyleSheet("font-weight: bold;")
            if len(groups)>1 or not all(g.startswith("CH") for g in groups):
                stack.addWidget(title)
            else:
                title.deleteLater()
            for group, rows in sorted(groups.items(), key=lambda item: ({"Drv":0,"Enc":1,"Tp":2}.get(re.sub(r"\d+$","",item[0]),3),item[0])):
                stack.addWidget(self._group(group, rows))
            stack.addStretch(1)
            widgets.append(col)
            grid.addWidget(col, i, 0, QtCore.Qt.AlignTop)
        grid.setColumnStretch(0, 0)
        layout.addLayout(grid)
        grid.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)
        if other:
            layout.addWidget(self._group("Other signals", other))
        layout.addStretch(1)
        return grid,widgets

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.Resize and (watched is self.cards.viewport() or (self._option_tabs is not None and self._option_tabs.currentWidget() is not None and watched is self._option_tabs.currentWidget().viewport())):
            QtCore.QTimer.singleShot(0, self._reflow_channels)
        return super().eventFilter(watched, event)

    def _reflow_channels(self):
        if self._closed or self._channel_grid is None or not self._channel_widgets:
            return
        required = max(w.minimumSizeHint().width() for w in self._channel_widgets)
        preferred = max(300, required, max(w.sizeHint().width() for w in self._channel_widgets))
        preferred = min(540, preferred)
        viewport=self._option_tabs.currentWidget().viewport() if self._option_tabs is not None and self._option_tabs.currentWidget() is not None else self.cards.viewport()
        columns = column_count(visible_width(viewport)-12, len(self._channel_widgets), preferred)
        if columns == self._channel_columns:
            return
        self._channel_columns = columns
        grid = self._channel_grid
        for w in self._channel_widgets:
            grid.removeWidget(w)
        for i, w in enumerate(self._channel_widgets):
            grid.addWidget(w, i//columns, i%columns, QtCore.Qt.AlignTop)
        for i in range(grid.columnCount()):
            grid.setColumnStretch(i, 0)
        grid.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignTop)

    def _analog_channel(self, kind, rows):
        root = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(root)
        grid.setContentsMargins(0, 0, 0, 0)
        measurements = [(name, pv, short) for name,pv,short in rows
                        if re.fullmatch(r'(AI|AO)\d+(-RB|-Arr)?', name)]
        for i,(name,pv,short) in enumerate(measurements):
            column = i
            caption = 'Output samples (command)' if name.endswith('-Arr') and kind=='AO' else 'Input samples' if name.endswith('-Arr') else 'Input value' if kind == 'AI' else 'Command' if not name.endswith('-RB') else 'Readback'
            title = QtWidgets.QLabel(caption)
            self._bind(title,pv)
            grid.addWidget(title,0,column)
            grid.addWidget(self._value_widget(name,pv,primary=True),1,column)
            grid.setColumnStretch(column,1)
        note = QtWidgets.QLabel('PV units · readback is not necessarily a measured output' if kind=='AO' else 'PV units · history autoscaled')
        root.setToolTip(note.text())
        note.deleteLater()
        for i,(name,pv,short) in enumerate(r for r in rows if r not in measurements):
            grid.addWidget(QtWidgets.QLabel(signal_caption(short)),3+i,0)
            grid.addWidget(self._value_widget(name,pv),3+i,1)
        return root

    def _digital_group(self, kind, signals):
        card = QtWidgets.QGroupBox("Digital inputs" if kind == "BI" else "Digital outputs")
        grid = QtWidgets.QGridLayout(card)
        grid.setContentsMargins(5,3,5,3)
        grid.setSpacing(3)
        columns = {"": "IN" if kind=="BI" else "Cmd", "-RB":"RB",
                   "-OvrTmpAlrm":"Temp", "-OpnLdAlrm":"Load",
                   "-OvrCurrAlrm":"Curr", "-ShrtCircAlrm":"Short",
                   "-OvrLoad":"Overload", "-Stat":"Status"}
        channels=sorted({ch for _,_,ch,_ in signals})
        by_channel={}
        for name,pv,ch,suffix in signals:
            by_channel.setdefault(ch,{})[suffix]=(name,pv)
        suffixes=[s for s in columns if any(s in row for row in by_channel.values())]
        suffixes+=sorted({s for row in by_channel.values() for s in row if s not in columns})
        for start in (0,):
            offset=0
            grid.addWidget(QtWidgets.QLabel("CH"),0,offset)
            for j,suffix in enumerate(suffixes,1):
                heading=QtWidgets.QLabel(columns.get(suffix,signal_caption(suffix.lstrip("-"))))
                heading.setAlignment(QtCore.Qt.AlignCenter)
                grid.addWidget(heading,0,offset+j)
            for row,ch in enumerate(channels,1):
                grid.addWidget(QtWidgets.QLabel(f"{kind}{ch:02d}"),row,offset)
                entries=by_channel[ch]
                for j,suffix in enumerate(suffixes,1):
                    if suffix not in entries:
                        continue
                    name,pv=entries[suffix]
                    if suffix in ("","-RB","-OvrLoad") or suffix.endswith("Alrm"):
                        callback=(lambda old,new,pv=pv:self._direct_boolean(pv,old,new)) if self.writable.get(pv)=="caToggleButton" else None
                        value=CompactBit("#c43c3c" if suffix.endswith("Alrm") or suffix=="-OvrLoad" else "#26804b",callback)
                        self._bind(value,pv)
                        self.card_values.setdefault(pv,[]).append((name,value))
                    else:
                        value=self._value_widget(name,pv)
                    grid.addWidget(value,row,offset+j,QtCore.Qt.AlignCenter)
        return card

    def _update_summary(self):
        faults, warnings, unknown = [], [], []
        for name,value in self._alarm_values.values():
            if value is None:
                unknown.append(name)
            elif (name in ('Online','Operational') and value == 0) or (name not in ('Online','Operational') and value == 1):
                (warnings if name.endswith('WrnAlrm') else faults).append(signal_caption(name))
        severity = 'fault' if faults else 'warning' if warnings else 'unknown' if unknown else 'ok'
        messages = faults + warnings
        if unknown:
            messages.append('Unavailable: '+', '.join(unknown))
        self._hardware_alarm.set_summary(severity, ' · '.join(messages) or 'No active reported hardware alarms')
        for cmd,rb,label in self._digital_pairs:
            values = [self.table.item(self.rows[pv],1).text() if pv in self.rows else None for pv in (cmd,rb)]
            status = digital_pair_status(*values)
            if label.text() != status:
                label.setText(status)
                label.setStyleSheet('color: #b7791f; font-weight: bold;' if status=='Mismatch' else '')

    def build_experts(self, experts):
        self._expert_selector = None
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        if not experts:
            layout.addWidget(QtWidgets.QLabel("No expert panel definitions in the bundled catalog for this slave."))
        else:
            selector = QtWidgets.QComboBox()
            self._expert_selector = selector
            stack = QtWidgets.QStackedWidget()
            for title, pairs in experts:
                selector.addItem(title)
                page = QtWidgets.QWidget()
                column = QtWidgets.QVBoxLayout(page)
                groups = {}
                for name, pv in pairs:
                    match = re.fullmatch(r"(Drv|Enc|Tp|AI|AO|BI|BO|CH)(\d+)(?:-?(.*))", name)
                    group = match[1] + match[2] if match else "Other signals"
                    short = match[3] or "Value" if match else name
                    groups.setdefault(group, []).append((name, pv, short))
                for group, rows in sorted(groups.items()):
                    column.addWidget(self._group(group, rows))
                column.addStretch(1)
                stack.addWidget(page)
            selector.currentIndexChanged.connect(stack.setCurrentIndex)
            selector.currentIndexChanged.connect(self._scope_changed)
            layout.addWidget(selector)
            layout.addWidget(stack)
        layout.addStretch(1)
        self.expert.setWidget(root)

    def update_card(self, pv, value, error):
        if pv in getattr(self, '_alarm_values', {}):
            name, _ = self._alarm_values[pv]
            self._alarm_values[pv] = (name, boolean_value(value))
        truth = {"1": True, "0": False, "1.0": True, "0.0": False,
                 "true": True, "false": False, "yes": True, "no": False}.get(str(value).strip().lower())
        if pv in self.device_values:
            name, label = self.device_values[pv]
            label.setToolTip(pv + ("\n" + error if error else ""))
            if name == "HWType":
                label.setText(str(value) if value is not None else "Unknown")
                label.setStyleSheet("font-weight: bold;")
                return
            caption = name.replace("Alstate-", "")
            if name.startswith("Alstate-"):
                color = ("#26804b" if name == "Alstate-Op" else "#c43c3c") if truth else "#888"
            else:
                color = "#26804b" if truth is True else "#c43c3c" if truth is False else "#888"
            label.setText(("● " if truth is not None else "○ ") + caption)
            label.setStyleSheet("font-weight: bold; color: " + color + ";")
            return
        if pv not in self.card_values:
            return
        for name, label in self.card_values[pv]:
            label.setToolTip(pv + ("\n" + error if error else ""))
            if isinstance(label, WaveformValue):
                label.set_value(value)
                continue
            if isinstance(label, AnalogValue):
                label.set_value(value)
                label.setToolTip(pv + '\nRaw PV value; history autoscaled (not time calibrated)' + ('\n' + error if error else ''))
                continue
            if isinstance(label, (BooleanValue, BooleanStatus)):
                label.set_value(value)
                continue
            if isinstance(label, (DS402Status, DS402StateMap)):
                label.set_status(value)
                continue
            if label.property('ds402_hex'):
                number = word_number(value)
                label.setText('—' if number is None else f'0x{number:04X}')
                continue
            if isinstance(label, WordValue):
                label.set_word(value)
                continue
            if value is None:
                label.setText("—")
                label.setStyleSheet("color: #888;")
                continue
            alarm = name.endswith("Alrm")
            positive = name.endswith(("Rdy", "RdyStat", "RdyToEna"))
            digital = bool(re.search(r"(?:^|-)(BI|BO)\d+(-RB)?$", name))
            if truth is not None and (alarm or positive or digital):
                color = ("#c43c3c" if truth == alarm else "#26804b") if (alarm or positive) else ("#26804b" if truth else "#888")
                label.setText(("● FAULT" if truth else "● OK") if alarm else ("● YES" if truth else "● NO") if positive else ("● 1" if truth else "○ 0"))
                label.setToolTip(f"{pv}: {value}")
                label.setStyleSheet("font-weight: bold; color: " + color + ";")
            else:
                label.setText(str(value))
                label.setStyleSheet("font-weight: bold;")

    def table_menu(self, pos):
        item = self.table.itemAt(pos)
        if item is not None:
            self.pv_menu(self.table.viewport().mapToGlobal(pos),
                         self.table.item(item.row(), 3).text())

    def pv_menu(self, global_pos, pv):
        menu = QtWidgets.QMenu(self)
        trend = menu.addAction("Trend PV")
        row = self.rows.get(pv)
        trend.setEnabled((not pv.endswith("-Arr") or is_bit_array(pv)) and (self.demo or self.on_plot is not None) and row is not None
                         and self.table.item(row, 2).text() in ("Read OK", "Simulated"))
        trend.triggered.connect(lambda: self._trend_pv(pv))
        add = menu.addAction("Add to existing trend…")
        add.setEnabled(trend.isEnabled() and self.on_plot is not None)
        add.triggered.connect(lambda: self._trend_pv(pv, add=True))
        if pv.endswith("-Arr") and not is_bit_array(pv):
            action = menu.addAction("Inspect waveform in panel")
            action.triggered.connect(lambda: self.views.setCurrentWidget(self.cards))
        if pv in self.commands:
            command = menu.addAction(self.commands[pv]["label"])
            command.setEnabled(self._can_write(pv))
            command.triggered.connect(lambda: self._execute_command(pv))
        if pv in self.writable and self.writable[pv] != "caMessageButton":
            write = menu.addAction("Write PV…")
            write.setEnabled(self._can_write(pv))
            write.triggered.connect(lambda: self._write_pv(pv))
        copy = menu.addAction("Copy PV name")
        copy.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(pv))
        if hasattr(menu, "exec"):
            menu.exec(global_pos)
        else:
            menu.exec_(global_pos)

    def _trend_pv(self, pv, add=False):
        provider = None
        if self.demo:
            slave = self.selector.currentData() or {}
            model = self._demo_model
            provider = lambda name: model.value(name, slave.get("name", ""), slave.get("panel", ""))
        if self.on_plot:
            self.on_plot([pv], add=add, demo_value=provider)
        elif self.demo:
            from ecmc_hardware_trend import HardwareTrendWindow
            window = HardwareTrendWindow([pv], parent=self, demo_value=provider)
            window.show()

    def _can_write(self, pv):
        row = self.rows.get(pv)
        return (not (is_bit_array(pv or "") and not re.search(r"-BO\d+-Arr$",pv or "")) and not self._closed and self.isVisible() and pv in self._active_scope and not self._write_protected and self.write_enable.isChecked()
                and self._task is None and self._write_task is None and self._enum_task is None
                and pv in self.writable and row is not None
                and self.table.item(row, 2).text() in ("Read OK", "Simulated"))

    def _write_pv(self, pv):
        if not self._can_write(pv):
            return
        current = self.table.item(self.rows[pv], 1).text()
        if self.writable[pv] == 'caByteController' and any(isinstance(widget, DS402Word) and not widget.status_word for _, widget in self.card_values.get(pv, [])):
            original = word_number(current)
            value = controlword_dialog(self, original)
            if value is not None and self._can_write(pv):
                if word_number(self.table.item(self.rows[pv], 1).text()) != original:
                    QtWidgets.QMessageBox.warning(self, 'Controlword changed', 'The live controlword changed during editing. Reopen the editor.')
                    return
                self._submit_write(pv, value)
            return
        if self.writable[pv] == 'caMessageButton':
            self._execute_command(pv)
            return
        if self.writable[pv] == 'caMenu' and not self.demo:
            self._enum_task = EnumTask(pv, self.timeout)
            self._enum_task.signals.finished.connect(lambda choices, error: self._enum_ready(pv, choices, error))
            self._update_edit_cursors()
            QtCore.QThreadPool.globalInstance().start(self._enum_task)
            return
        if self.writable[pv] == "caToggleButton":
            value, ok = QtWidgets.QInputDialog.getItem(self, "Write hardware PV", pv, ["0", "1"], 1 if current.strip() in ("1", "True", "Yes") else 0, False)
        else:
            value, ok = QtWidgets.QInputDialog.getText(
                self, "Write hardware PV", pv + "\\nEnter new numeric value:", text=current)
        if not ok:
            return
        try:
            from ecmc_hardware_write import parse_write_value
            value = parse_write_value(value, self.writable[pv])
        except ValueError as ex:
            QtWidgets.QMessageBox.warning(self, "Invalid value", str(ex))
            return
        if is_bit_array(pv) and word_number(value) is None:
            QtWidgets.QMessageBox.warning(self,"Invalid value","Enter a 16-bit unsigned integer.")
            return
        self._submit_write(pv, value)

    def _execute_command(self, pv):
        if self._can_write(pv) and pv in self.commands:
            self._submit_write(pv, self.commands[pv]['release'])

    def _enum_ready(self, pv, choices, error):
        self._enum_task = None
        if self._closed:
            return
        self._update_edit_cursors()
        if not self._can_write(pv):
            return
        if error or not choices:
            QtWidgets.QMessageBox.warning(self, 'Enum unavailable', error or 'No enum labels')
            return
        current = self.table.item(self.rows[pv], 1).text()
        labels = [f'{i}: {label}' for i, label in enumerate(choices)]
        try:
            index = int(current)
        except ValueError:
            index = choices.index(current) if current in choices else 0
        selected, ok = QtWidgets.QInputDialog.getItem(self, 'Select enum value', pv, labels, max(0, min(index, len(labels)-1)), False)
        if ok:
            self._submit_write(pv, labels.index(selected))

    def _direct_boolean(self, pv, old, new):
        if self.writable.get(pv) != 'caToggleButton' or not self._can_write(pv):
            return
        if boolean_value(self.table.item(self.rows[pv], 1).text()) != old:
            return
        self._submit_write(pv, new, direct=True, expected=old)

    def _direct_array_bit(self,pv,old,bit):
        if not re.search(r'-BO\d+-Arr$',pv) or self.writable.get(pv)!='caByteController' or not self._can_write(pv):
            return
        if 0<=bit<16 and word_number(self.table.item(self.rows[pv],1).text())==old:
            self._submit_write(pv,old ^ (1<<bit),expected=old)

    def _direct_bit(self, pv, old, bit):
        if self.writable.get(pv) != 'caByteController' or not pv.endswith('-Cmd') or not self._can_write(pv):
            return
        if not 0 <= bit < 16 or word_number(self.table.item(self.rows[pv], 1).text()) != old:
            return
        self._submit_write(pv, old ^ (1 << bit), direct=True, expected=old)

    def _submit_write(self, pv, value, direct=False, expected=None):
        if not self._can_write(pv):
            return
        if self.demo:
            if self._can_write(pv):
                self._demo_model.write(pv, value, ds402=bool(re.search(r'-Drv\d+-', pv)) and self._is_ds402(re.search(r'-(Drv\d+)-', pv)[1]))
                self._demo_tick()
            return
        if not direct and QtWidgets.QMessageBox.question(
                self, "Confirm hardware write",
                f"Write {value} to {pv}?\nThis may directly affect connected hardware.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if not self._can_write(pv):
            return
        self.selector.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.write_enable.setEnabled(False)
        self.status.setText("Writing " + pv)
        self._write_task = WriteTask(pv, value, self.timeout, self.writable[pv], expected=expected)
        self._update_edit_cursors()
        self._write_task.signals.finished.connect(self._write_finished)
        QtCore.QThreadPool.globalInstance().start(self._write_task)

    def _write_finished(self, error):
        self._write_task = None
        if self._closed:
            return
        self.selector.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.write_enable.setEnabled(True)
        if error:
            QtWidgets.QMessageBox.warning(self, "Write not confirmed", error)
        if self._monitor_timer.isActive():
            self._update_edit_cursors()
        else:
            self._scope_changed(force=True)

    def plot_selected(self):
        pvs = [self.table.item(i.row(), 3).text()
               for i in self.table.selectionModel().selectedRows()
               if self.table.item(i.row(), 2).text() == "Read OK"]
        if pvs and self.on_plot:
            self.on_plot(pvs)

    def closeEvent(self, event):
        self._closed = True
        if isinstance(self._write_task, StateTask):
            self._write_task.cancelled.set()
        self._demo_timer.stop()
        self._stop_monitors()
        super().closeEvent(event)
,pv) or self.writable.get(pv)!='caByteController' or not self._can_write(pv):
            return
        if 0<=bit<16 and word_number(self.table.item(self.rows[pv],1).text())==old:
            self._submit_write(pv,old ^ (1<<bit),expected=old)

    def _direct_bit(self, pv, old, bit):
        if self.writable.get(pv) != 'caByteController' or not pv.endswith('-Cmd') or not self._can_write(pv):
            return
        if not 0 <= bit < 16 or word_number(self.table.item(self.rows[pv], 1).text()) != old:
            return
        self._submit_write(pv, old ^ (1 << bit), direct=True, expected=old)

    def _submit_write(self, pv, value, direct=False, expected=None):
        if not self._can_write(pv):
            return
        if self.demo:
            if self._can_write(pv):
                self._demo_model.write(pv, value, ds402=bool(re.search(r'-Drv\d+-', pv)) and self._is_ds402(re.search(r'-(Drv\d+)-', pv)[1]))
                self._demo_tick()
            return
        if not direct and QtWidgets.QMessageBox.question(
                self, "Confirm hardware write",
                f"Write {value} to {pv}?\nThis may directly affect connected hardware.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if not self._can_write(pv):
            return
        self.selector.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.write_enable.setEnabled(False)
        self.status.setText("Writing " + pv)
        self._write_task = WriteTask(pv, value, self.timeout, self.writable[pv], expected=expected)
        self._update_edit_cursors()
        self._write_task.signals.finished.connect(self._write_finished)
        QtCore.QThreadPool.globalInstance().start(self._write_task)

    def _write_finished(self, error):
        self._write_task = None
        if self._closed:
            return
        self.selector.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.write_enable.setEnabled(True)
        if error:
            QtWidgets.QMessageBox.warning(self, "Write not confirmed", error)
        if self._monitor_timer.isActive():
            self._update_edit_cursors()
        else:
            self._scope_changed(force=True)

    def plot_selected(self):
        pvs = [self.table.item(i.row(), 3).text()
               for i in self.table.selectionModel().selectedRows()
               if self.table.item(i.row(), 2).text() == "Read OK"]
        if pvs and self.on_plot:
            self.on_plot(pvs)

    def closeEvent(self, event):
        self._closed = True
        if isinstance(self._write_task, StateTask):
            self._write_task.cancelled.set()
        self._demo_timer.stop()
        self._stop_monitors()
        super().closeEvent(event)
