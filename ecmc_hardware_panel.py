"""Native EtherCAT process signal browser with explicit write controls."""
import re
from threading import Lock
from qt_compat import QtCore, QtWidgets
from ecmc_epics import EpicsClient
from ecmc_hardware_catalog import hardware_pvs, hardware_writable_pvs, hardware_views, hardware_commands
from ecmc_hardware_write import WriteTask, EnumTask
from ecmc_hardware_demo import HardwareDemo
from ecmc_hardware_widgets import HardwareSymbol, SignalValue, WordValue, BooleanValue, boolean_value, signal_role
from ecmc_ds402_widget import DS402Status, DS402Word, DS402StateMap, controlword_dialog
from ecmc_ds402 import word_number


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


class HardwarePanel(QtWidgets.QWidget):
    def __init__(self, prefix, master, slaves, timeout=1.0, demo=False, on_plot=None):
        super().__init__()
        self.timeout, self.demo, self.on_plot = timeout, demo, on_plot
        self._task = None
        self._closed = False
        self._write_task = None
        self.writable = {}
        self.commands = {}
        self._enum_task = None
        self.rows = {}
        self._monitor_lock = Lock()
        self._monitor_generation = 0
        self._active_scope = set()
        self._overview_pvs = set()
        self._expert_pvs = []
        self._expert_selector = None
        self._scope_initialized = False
        self._monitors = {}
        self._monitor_pending = {}
        self._monitor_connected = {}
        self._monitor_timer = QtCore.QTimer(self)
        self._monitor_timer.setInterval(100)
        self._monitor_timer.timeout.connect(self._monitor_tick)
        self._demo_model = HardwareDemo() if demo else None
        self._demo_timer = QtCore.QTimer(self)
        self._demo_timer.setInterval(100)
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
        self.views = QtWidgets.QTabWidget()
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
        self.live_check.toggled.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.live_check)
        self.write_enable = QtWidgets.QCheckBox("Enable simulated writes" if demo else "Enable writes")
        self.write_enable.setEnabled(True)
        self.write_enable.toggled.connect(self._update_edit_cursors)
        buttons.addWidget(self.write_enable)
        buttons.addStretch(1)
        self.write_enable.setToolTip("Arms direct single-click Boolean and controlword bit writes, which may enable motion. Numeric edits still require confirmation.")
        layout.addLayout(buttons)
        self.selector.currentIndexChanged.connect(lambda _: self.write_enable.setChecked(False))
        self.selector.currentIndexChanged.connect(self.refresh)
        self.views.currentChanged.connect(self._scope_changed)
        self.refresh()

    def refresh(self, *_):
        if self._closed or self._task is not None or self._write_task is not None:
            return
        slave = self.selector.currentData()
        if not slave:
            return
        self._stop_monitors()
        self._scope_initialized = False
        candidates = slave.get("panels") or [slave.get("panel"), slave.get("name")]
        pairs = hardware_pvs(self.prefix, slave.get("master", self.master), slave["id"], candidates)
        self.writable = hardware_writable_pvs(self.prefix, slave.get("master", self.master), slave["id"], candidates)
        self.commands = hardware_commands(self.prefix, slave.get('master', self.master), slave['id'], candidates)
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
        self._overview_pvs = {pv for _, pv in overview}
        self._expert_pvs = [{pv for _, pv in pairs} for _, pairs in experts]
        self.build_cards(overview)
        self.build_experts(experts)
        self._scope_initialized = True
        self._scope_changed(force=True)

    def _visible_scope(self):
        if self.views.currentWidget() is self.table:
            return set(self.rows)
        if self.views.currentWidget() is self.expert:
            index = self._expert_selector.currentIndex() if self._expert_selector is not None else -1
            selected = self._expert_pvs[index] if 0 <= index < len(self._expert_pvs) else set()
            return set(self.device_values) | selected
        return set(self.device_values) | self._overview_pvs

    def _invalidate_scope(self):
        for pv in self.rows:
            row = self.rows[pv]
            self.table.item(row, 1).setText("—")
            self.table.item(row, 2).setText("Paused")
            self.update_card(pv, None, "Not currently monitored")
        self._update_edit_cursors()

    def _scope_changed(self, *_args, force=False):
        if not self._scope_initialized or self._closed:
            return
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

    def hideEvent(self, event):
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
        for pv, (value, error) in pending.items():
            row = self.rows.get(pv)
            if row is None:
                continue
            self._apply_value(pv, value, error)
        if self._write_task is None:
            self.status.setText(f"Live CA monitoring — {connected}/{len(self._monitors)} connected | {len(self._monitors)} subscriptions")
        if pending:
            self._update_edit_cursors()

    def _demo_value(self, pv):
        slave = self.selector.currentData() or {}
        return self._demo_model.value(pv, slave.get("name", ""), slave.get("panel", ""))

    def _demo_tick(self):
        if self._closed or not self.demo:
            self._demo_timer.stop()
            return
        for pv in self._active_scope:
            self._apply_value(pv, self._demo_value(pv), '', simulated=True)

    def _apply_value(self, pv, value, error, simulated=False):
        row = self.rows.get(pv)
        if row is None or pv not in self._active_scope:
            return
        text = str(value) if value is not None else "—"
        state = "Simulated" if simulated else "Read OK" if value is not None else "Unavailable"
        item = self.table.item(row, 2)
        if self.table.item(row, 1).text() == text and item.text() == state and item.toolTip() == error:
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
        self.status.setText(f"{available}/{len(results)} visible PVs read (snapshot)")
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
        for pv, buttons in self._command_buttons.items():
            for button in buttons:
                button.setEnabled(self._can_write(pv))
        for pv, widgets in self.card_values.items():
            for _, widget in widgets:
                editable = self._can_write(pv) and not (isinstance(widget, WordValue) and (getattr(widget, 'status_word', False) or pv.endswith('-RB')))
                widget.setCursor(QtCore.Qt.PointingHandCursor if editable else QtCore.Qt.ArrowCursor)
                widget.set_editable(editable)

    def _value_widget(self, name, pv, primary=False):
        # EL70x1 original panel defines both words as bits 0..15.
        slave = self.selector.currentData() or {}
        panel = str(slave.get('panel') or '')
        candidates = slave.get('panels') or [panel, slave.get('name', '')]
        known_word = any(str(c).removeprefix('ecmc').removesuffix('.ui') == 'EL70x1' for c in candidates) and name in ('Drv01-Cmd', 'Drv01-Stat')
        kind = self.writable.get(pv)
        if kind == 'caToggleButton':
            value = BooleanValue(lambda old, new, pv=pv: self._direct_boolean(pv, old, new))
        elif known_word:
            value = WordValue(lambda pv=pv: self._click_write(pv),
                              bit_callback=(lambda old, bit, pv=pv: self._direct_bit(pv, old, bit)) if kind == 'caByteController' and name.endswith('-Cmd') else None)
        else:
            value = SignalValue("—", lambda pv=pv: self._click_write(pv))
        value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        if primary:
            font = value.font()
            font.setPointSize(max(13, font.pointSize() + 4))
            font.setBold(True)
            value.setFont(font)
        self._bind(value, pv)
        self.card_values.setdefault(pv, []).append((name, value))
        return value

    def _group(self, title, rows):
        card = QtWidgets.QGroupBox(title)
        card.setMaximumWidth(540)
        outer = QtWidgets.QVBoxLayout(card)
        outer.setContentsMargins(5, 4, 5, 4)
        outer.setSpacing(3)
        kind = re.sub(r"\d+$", "", title)
        primary = [r for r in rows if signal_role(kind, r[2]) == "primary"] if kind in ("Drv", "Enc") else []
        status = [r for r in rows if signal_role(kind, r[2]) == "status"] if kind in ("Drv", "Enc") else []
        detail = [r for r in rows if r not in primary and r not in status]
        ds402 = kind == 'Drv' and self._is_ds402(title)
        if ds402:
            dedicated = [r for r in rows if r[2] in ('Stat', 'Cmd', 'Cmd-RB')]
            if dedicated:
                outer.addWidget(self._ds402_group(dedicated))
                detail = [r for r in detail if r not in dedicated]
        if primary:
            hero = QtWidgets.QHBoxLayout()
            hero.addWidget(HardwareSymbol(kind))
            measurements = QtWidgets.QVBoxLayout()
            for name, pv, short in primary:
                line = QtWidgets.QHBoxLayout()
                key = QtWidgets.QLabel(short)
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
                label = QtWidgets.QLabel(short)
                self._bind(label, pv)
                leds.addWidget(label, i // 2, (i % 2) * 2)
                leds.addWidget(self._value_widget(name, pv), i // 2, (i % 2) * 2 + 1)
            outer.addLayout(leds)
        command_rows = [(name,pv) for name,pv,_ in rows if pv in self.commands]
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
                key = QtWidgets.QLabel(short)
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

    def _ds402_group(self, rows):
        group = QtWidgets.QGroupBox("CiA 402")
        layout = QtWidgets.QGridLayout(group)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(2)
        by_suffix = {short: (name, pv) for name, pv, short in rows}
        row = 0
        if 'Stat' in by_suffix:
            name, pv = by_suffix['Stat']
            state = DS402Status()
            self._bind(state, pv)
            self.card_values.setdefault(pv, []).append((name, state))
            layout.addWidget(state, row, 0, 1, 3)
            row += 1
        for short in ('Cmd', 'Cmd-RB', 'Stat'):
            if short not in by_suffix:
                continue
            name, pv = by_suffix[short]
            label = QtWidgets.QLabel({'Cmd': 'CW', 'Cmd-RB': 'CW RB', 'Stat': 'SW'}[short])
            self._bind(label, pv)
            layout.addWidget(label, row, 0)
            word = DS402Word(lambda pv=pv: self._click_write(pv), status=short == 'Stat',
                             bit_callback=(lambda old, bit, pv=pv: self._direct_bit(pv, old, bit)) if short == 'Cmd' and self.writable.get(pv) == 'caByteController' else None)
            self._bind(word, pv)
            self.card_values.setdefault(pv, []).append((name, word))
            layout.addWidget(word, row, 1)
            number = SignalValue("—", lambda pv=pv: self._click_write(pv))
            self._bind(number, pv)
            self.card_values.setdefault(pv, []).append((name, number))
            number.setProperty("ds402_hex", True)
            layout.addWidget(number, row, 2)
            row += 1
        if 'Stat' in by_suffix:
            name, pv = by_suffix['Stat']
            details = QtWidgets.QToolButton()
            details.setText("State details")
            details.setCheckable(True)
            details.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            details.setArrowType(QtCore.Qt.RightArrow)
            layout.addWidget(details, row, 0, 1, 3)
            state_map = DS402StateMap()
            self._bind(state_map, pv)
            self.card_values.setdefault(pv, []).append((name, state_map))
            state_map.hide()
            details.toggled.connect(state_map.setVisible)
            details.toggled.connect(lambda on: details.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow))
            layout.addWidget(state_map, row+1, 0, 1, 3)
        layout.setColumnStretch(1, 1)
        return group

    def build_cards(self, pairs):
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.card_values = {}
        self._command_buttons = {}
        self.device_values = {}
        header = self.device_header_layout
        while header.count():
            item = header.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        channels, other, digital = {}, [], {'BI': [], 'BO': []}
        for name, pv in pairs:
            if name == "PnlTyp":
                continue
            if name in ("HWType", "Online", "Operational") or name.startswith("Alstate-"):
                label = QtWidgets.QLabel("—" if name == "HWType" else "○ " + name.replace("Alstate-", ""))
                self._bind(label, pv)
                header.addWidget(label)
                self.device_values[pv] = (name, label)
                continue
            io = re.fullmatch(r"(BI|BO)(\d+)(-RB)?", name)
            if io:
                digital[io[1]].append((name, pv, int(io[2]), bool(io[3])))
                continue
            m = re.fullmatch(r"(Drv|Enc|Tp|AI|AO|BI|BO)(\d+)(?:-?(.*))", name)
            if m:
                kind, number, suffix = m.groups()
                channels.setdefault(int(number), {}).setdefault(kind + number, []).append((name, pv, suffix or "Value"))
            else:
                other.append((name, pv, name))
        header.addStretch(1)
        for kind, signals in digital.items():
            if signals:
                layout.addWidget(self._digital_group(kind, signals), 0, QtCore.Qt.AlignLeft)
        grid = QtWidgets.QGridLayout()
        self._channel_grid = grid
        self._channel_widgets = []
        self._channel_columns = 0
        for i, (ch, groups) in enumerate(sorted(channels.items())):
            col = QtWidgets.QWidget()
            stack = QtWidgets.QVBoxLayout(col)
            stack.setContentsMargins(0, 0, 0, 0)
            stack.setSpacing(3)
            title = QtWidgets.QLabel(f"Channel {ch:02d}")
            title.setStyleSheet("font-weight: bold;")
            stack.addWidget(title)
            for group, rows in sorted(groups.items(), key=lambda item: ({"Drv":0,"Enc":1,"Tp":2}.get(re.sub(r"\d+$","",item[0]),3),item[0])):
                stack.addWidget(self._group(group, rows))
            stack.addStretch(1)
            self._channel_widgets.append(col)
            grid.addWidget(col, i, 0, QtCore.Qt.AlignTop)
        grid.setColumnStretch(0, 1)
        layout.addLayout(grid)
        if other:
            layout.addWidget(self._group("Other signals", other))
        layout.addStretch(1)
        self.cards.setWidget(root)
        self._reflow_channels()

    def eventFilter(self, watched, event):
        if watched is self.cards.viewport() and event.type() == QtCore.QEvent.Resize:
            QtCore.QTimer.singleShot(0, self._reflow_channels)
        return super().eventFilter(watched, event)

    def _reflow_channels(self):
        if self._closed or self._channel_grid is None or not self._channel_widgets:
            return
        required = max(w.minimumSizeHint().width() for w in self._channel_widgets)
        columns = 2 if len(self._channel_widgets) > 1 and self.cards.viewport().width() >= 2*max(270, required)+20 else 1
        if columns == self._channel_columns:
            return
        self._channel_columns = columns
        grid = self._channel_grid
        for w in self._channel_widgets:
            grid.removeWidget(w)
        for i, w in enumerate(self._channel_widgets):
            grid.addWidget(w, i//columns, i%columns, QtCore.Qt.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1 if columns == 2 else 0)

    def _digital_group(self, kind, signals):
        card = QtWidgets.QGroupBox("Digital inputs" if kind == "BI" else "Digital outputs")
        grid = QtWidgets.QGridLayout(card)
        grid.setSpacing(4)
        channels = sorted({ch for _, _, ch, _ in signals})
        for index, ch in enumerate(channels):
            column = index % 8 + 1
            row = index // 8 * 4
            title = QtWidgets.QLabel(f"{ch:02d}")
            title.setAlignment(QtCore.Qt.AlignCenter)
            grid.addWidget(title, row, column)
            for name, pv, number, rb in signals:
                if number != ch:
                    continue
                value = self._value_widget(name, pv)
                value.setAlignment(QtCore.Qt.AlignCenter)
                value.setMinimumWidth(35)
                grid.addWidget(value, row + (2 if rb else 1), column)
                label = QtWidgets.QLabel("RB" if rb else ("IN" if kind == "BI" else "CMD"))
                grid.addWidget(label, row + (2 if rb else 1), 0) if column == 1 else label.deleteLater()
        return card

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
                    match = re.fullmatch(r"(Drv|Enc|Tp|AI|AO|BI|BO)(\d+)(?:-?(.*))", name)
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
            if isinstance(label, BooleanValue):
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
        trend.setEnabled((self.demo or self.on_plot is not None) and row is not None
                         and self.table.item(row, 2).text() in ("Read OK", "Simulated"))
        trend.triggered.connect(lambda: self._trend_pv(pv))
        add = menu.addAction("Add to existing trend…")
        add.setEnabled(trend.isEnabled() and self.on_plot is not None)
        add.triggered.connect(lambda: self._trend_pv(pv, add=True))
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
        return (not self._closed and self.isVisible() and pv in self._active_scope and self.write_enable.isChecked()
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
                self._demo_model.write(pv, value)
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
        self._demo_timer.stop()
        self._stop_monitors()
        super().closeEvent(event)
