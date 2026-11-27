"""Visibility-scoped axis commissioning controls."""
import math
import time
from qt_compat import QtCore, QtWidgets
from ecmc_axis_diagnostics import position_trend_pvs, axis_signals, NATIVE_EDIT, MOTOR_EDIT, NATIVE_ACTIONS, MOTOR_ACTIONS, BOOL_COMMANDS, BOOL_STATUS, MOTION_ENUMS, format_axis_value, axis_status_value, decode_msta, MSTA_BITS, axis_alarm_summary
from ecmc_hardware_widgets import SignalValue, BooleanValue, BooleanStatus, AlarmSummary
from ecmc_axis_write import AxisWriteTask
from ecmc_axis_position import PositionTask, HomeTask, VelocityTask, MotorStopTask, KillTask, EncoderModeTask, SourceModeTask
from ecmc_axis_overview import AxisPositionBar, AxisStatusIcon
from ecmc_encoder_homing_panel import EncoderHomingPanel
from ecmc_encoder_overview import EncoderOverview
from ecmc_demo_model import DemoMotion
from ecmc_source_mode import SOURCE_COMMANDS, source_label
from ecmc_homing_spec import HOME_PROCEDURES, home_procedure_label
from ecmc_homing_indicators import HomingIndicators
from ecmc_hardware_write import parse_write_value, EnumTask, WriteTask

class AxisExpertPanel(QtWidgets.QWidget):
    def __init__(self, base, axis_id, demo=False, on_plot=None, actions=(), interface="native"):
        super().__init__()
        self.base,self.demo,self.on_plot = base,demo,on_plot
        self._closed = False
        self._pvs = {}
        self._labels = {}
        self._started = time.monotonic()
        self._demo_motion={True:DemoMotion(10),False:DemoMotion(15)}
        self._demo_tick_time=self._started
        self._overrides = {}
        self._writes = set()
        self._enum_task = None
        self._precision = 8
        self._generation = 0
        self._edit_widgets = {}
        self._source_widgets = {}
        self._action_buttons = []
        self._position_task = None
        self._jog_task = None
        self._jog_button = None
        self._jog_native = None
        self._jog_stops = set()
        self._jog_buttons = []
        self._motion_widgets = []
        self._overview = None
        self._encoder_homing = None
        self._encoder_overview = None
        self._status_icons = []
        self._msta_icons = []
        self._stop_pool = QtCore.QThreadPool(self)
        self._stop_pool.setMaxThreadCount(1)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4,4,4,4)
        layout.setSpacing(3)
        title = QtWidgets.QLabel(f"Axis {axis_id} — {base}")
        title.setWordWrap(True)
        layout.addWidget(title)
        self.interface = QtWidgets.QComboBox()
        self.interface.addItem("ECMC native interface","native")
        self.interface.addItem("EPICS motor record","motor")
        self.interface.setCurrentIndex(1 if interface == "motor" else 0)
        layout.addWidget(self.interface)
        self.status = QtWidgets.QLabel("Paused")
        layout.addWidget(self.status)
        self.alarm_summary = AlarmSummary()
        layout.addWidget(self.alarm_summary)
        controls = QtWidgets.QHBoxLayout()
        self.arm = QtWidgets.QCheckBox("Enable writes")
        self._write_protected = False
        self.arm.setChecked(True)
        self.arm.setToolTip("Controlled by navigator write protection when embedded.")
        self.arm.toggled.connect(self._update_controls)
        self.arm.toggled.connect(lambda on: None if on else self._release_jog())
        controls.addWidget(self.arm)
        if self.on_plot:
            trend = QtWidgets.QPushButton("Trend position")
            trend.setToolTip("Actual and target position on the same plot")
            trend.clicked.connect(lambda checked=False: self._trend_position())
            controls.addWidget(trend)
        controls.addStretch()
        precision = QtWidgets.QSpinBox()
        precision.setRange(4,12)
        precision.setValue(self._precision)
        precision.setToolTip("Significant digits for floating point values")
        precision.valueChanged.connect(self._set_precision)
        controls.addWidget(QtWidgets.QLabel("Digits"))
        controls.addWidget(precision)
        self.stop_button = QtWidgets.QPushButton("STOP")
        self.stop_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed,QtWidgets.QSizePolicy.Fixed)
        self.stop_button.setStyleSheet("font-weight: bold; color: #c43c3c;")
        self.stop_button.clicked.connect(self._stop_motion)
        controls.addWidget(self.stop_button)
        self.kill_button = QtWidgets.QPushButton("KILL")
        self.kill_button.setToolTip("Disable drive and request stop (not a safety-rated emergency stop)")
        self.kill_button.setStyleSheet("font-weight: bold; color: #c43c3c;")
        self.kill_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed,QtWidgets.QSizePolicy.Fixed)
        self.kill_button.clicked.connect(self._kill_motion)
        controls.addWidget(self.kill_button)
        layout.addLayout(controls)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll,1)
        buttons = QtWidgets.QHBoxLayout()
        for caption,action in actions:
            b = QtWidgets.QPushButton(caption)
            b.clicked.connect(lambda checked=False, action=action: action())
            buttons.addWidget(b)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._tick)
        self.interface.currentIndexChanged.connect(self._rebuild)
        self._rebuild()
        QtWidgets.QApplication.instance().applicationStateChanged.connect(self._jog_app_state)

    def set_write_protected(self, protected):
        self._write_protected = bool(protected)
        if protected:
            self._release_jog()
            if self._position_task is not None:
                self._position_task.cancelled.set()
        self.arm.setChecked(not protected)
        self._update_controls()

    def _rebuild(self,*_):
        self._release_jog()
        self._generation += 1
        self._stop()
        self.arm.setChecked(not self._write_protected)
        self._labels = {}
        self._encoder_homing = None
        self._encoder_overview = None
        self.home_selector = None
        self.home_status = None
        self.home_indicators = None
        self._edit_widgets = {}
        self._source_widgets = {}
        self._action_buttons = []
        root = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4,4,4,4)
        layout.setSpacing(4)
        native = self.interface.currentData() == "native"
        editable = NATIVE_EDIT if native else MOTOR_EDIT
        self._overview = AxisPositionBar(native,self._stage_target,self._edit_graph_limit)
        self._status_icons = []
        self._msta_icons = []
        overview_row = QtWidgets.QHBoxLayout()
        states = (("Enabled","⏻","-EnaAct",False),("Homed","⌂","-Homed",False),
                  ("Moving","↔","-Moving",False),("At target","◎","-AtTarget",False),
                  ("RT","◷","-InRT",False),("Error","!","-ErrId",True)) if native else (
                  ("Enable cmd","⏻",".CNEN",False),("Done","◎",".DMOV",False),
                  ("Homed","⌂","msta:Homed",False),("Moving","↔","msta:Moving",False),
                  ("Problem","!","msta:Problem",True),("Comm","!","msta:Communication error",True))
        for caption,glyph,key,bad in states:
            icon = AxisStatusIcon(caption,glyph,key,bad)
            self._status_icons.append(icon)
            overview_row.addWidget(icon)
        overview_row.addStretch()
        layout.addLayout(overview_row)
        self.position_summary = QtWidgets.QWidget()
        summary_grid = QtWidgets.QGridLayout(self.position_summary)
        summary_grid.setContentsMargins(2,0,2,0)
        summary_grid.setSpacing(3)
        self._summary_values = {}
        summary_fields = (('Actual position','-PosAct'),('Target position','-TgtPosCmd'),('Actual velocity','-VelAct'),('Following error','-PosErr')) if native else (('Actual position','.RBV'),('Requested position','.VAL'),('Velocity setting','.VELO'))
        for i,(caption,suffix) in enumerate(summary_fields):
            label = QtWidgets.QLabel(caption)
            value = QtWidgets.QLabel('—')
            font = value.font()
            font.setPointSize(max(15,font.pointSize()+4) if i<2 else max(11,font.pointSize()))
            font.setBold(True)
            value.setFont(font)
            if i<2:
                value.setStyleSheet('color: '+('#287bc0' if i==0 else '#26804b'))
            summary_grid.addWidget(label,0,i)
            summary_grid.addWidget(value,1,i)
            summary_grid.setColumnStretch(i,1)
            value.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            value.customContextMenuRequested.connect(lambda pos,w=value,pv=self.base+suffix:self._menu(w,pos,pv))
            self._summary_values[suffix] = value
        layout.addWidget(self.position_summary)
        layout.addWidget(self._overview)
        command = QtWidgets.QGroupBox("Move")
        command_grid = QtWidgets.QGridLayout(command)
        command_grid.setContentsMargins(4,2,4,2)
        command_grid.setSpacing(3)
        self.move_mode = QtWidgets.QComboBox()
        self.move_mode.addItem("Absolute","abs")
        self.move_mode.addItem("Relative","rel")
        self.move_mode.addItem("Velocity","vel")
        self.move_target = QtWidgets.QLineEdit()
        self.move_target.setPlaceholderText("Position / distance")
        self.move_target.setMaximumWidth(160)
        self.move_target.textChanged.connect(self._refresh_draft)
        self.move_mode.currentIndexChanged.connect(self._refresh_draft)
        self.move_mode.currentIndexChanged.connect(self._mode_changed)
        self.move_velocity = QtWidgets.QLineEdit()
        self.move_velocity.setPlaceholderText("Velocity")
        self.move_velocity.setMaximumWidth(100)
        self.move_button = QtWidgets.QPushButton("▶ Move")
        self.move_button.setStyleSheet("font-weight: bold; color: #26804b;")
        self.move_button.setSizePolicy(QtWidgets.QSizePolicy.Fixed,QtWidgets.QSizePolicy.Fixed)
        self.move_button.clicked.connect(self._move)
        command_grid.addWidget(self.move_mode,0,0)
        command_grid.addWidget(self.move_target,0,1)
        command_grid.addWidget(self.move_velocity,0,2)
        command_grid.addWidget(self.move_button,0,3)
        command_grid.setColumnStretch(4,1)
        self._motion_widgets = [self.move_mode,self.move_target,self.move_velocity,self.move_button]
        layout.addWidget(command)
        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(4)
        self.enable_button=BooleanValue(lambda old,new:self._write("-EnaCmd" if native else ".CNEN",(new,)))
        self.enable_button.setFixedWidth(76)
        self.enable_button.setToolTip(self.base+("-EnaCmd-RB" if native else ".CNEN"))
        action_row.addWidget(QtWidgets.QLabel("Drive enable"))
        action_row.addWidget(self.enable_button)
        self._shortcut_readbacks={}
        available = {caption:(suffix,values) for caption,suffix,values in
                     (NATIVE_ACTIONS if native else MOTOR_ACTIONS)}
        for caption,short in (("Tweak −","◀ Tweak"),("Tweak +","Tweak ▶"),
                              ("Home reverse","Home −"),("Home forward","Home +"),
                              ("Reset error","↻ Reset error")):
            if caption not in available and not (native and caption=="Home reverse"):
                continue
            button = QtWidgets.QPushButton(short)
            button.setStyleSheet("QPushButton:pressed { background-color: #26804b; color: white; }")
            button.setAccessibleName(caption)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed,QtWidgets.QSizePolicy.Fixed)
            button.setToolTip(caption)
            if native and caption == "Home reverse":
                button.setText("Home")
                button.setAccessibleName("Home configured procedure")
                button.setToolTip("Home using configured ECMC procedure")
                button.clicked.connect(self._home)
                self._action_buttons.append(button)
            elif caption in available:
                suffix,values = available[caption]
                button.clicked.connect(lambda checked=False,suffix=suffix,values=values: self._write(suffix,values))
                self._action_buttons.append(button)
                if suffix in ("-TwkBwdCmd","-TwkFwdCmd"):
                    self._shortcut_readbacks[button]=suffix
            else:
                button.setEnabled(False)
                button.setToolTip(caption+" unavailable in this interface")
            action_row.addWidget(button)
        action_row.addStretch()
        layout.addLayout(action_row)
        jog_row=QtWidgets.QHBoxLayout()
        self.jog_speed=QtWidgets.QLineEdit()
        self.jog_speed.setPlaceholderText("Jog speed")
        self.jog_speed.setMaximumWidth(110)
        jog_row.addWidget(QtWidgets.QLabel("Hold to jog"))
        jog_row.addWidget(self.jog_speed)
        self._jog_buttons=[]
        for direction,title in ((-1,"◀ Jog"),(1,"Jog ▶")):
            button=QtWidgets.QPushButton(title)
            button.setAutoRepeat(False)
            button.setFocusPolicy(QtCore.Qt.NoFocus)
            button.setStyleSheet("QPushButton:pressed { background-color: #26804b; color: white; }")
            button.setToolTip("Hold to move; release to request stop. Not a safety-rated dead-man control.")
            button.pressed.connect(lambda d=direction,b=button:self._press_jog(d,b))
            button.released.connect(self._release_jog)
            button.installEventFilter(self)
            self._jog_buttons.append(button)
            jog_row.addWidget(button)
        jog_row.addStretch()
        layout.addLayout(jog_row)
        self.detail_tabs=QtWidgets.QTabWidget()
        layout.addWidget(self.detail_tabs,1)
        pages={}
        for title in (("Motion","Encoders","Homing","Limits","Trajectory","Controller","Diagnostics") if native else ("Motion","Limits","Diagnostics")):
            page=QtWidgets.QWidget()
            page_layout=QtWidgets.QVBoxLayout(page)
            page_layout.setContentsMargins(4,4,4,4)
            pages[title]=page_layout
            self.detail_tabs.addTab(page,title)
        from ecmc_axis_readiness_panel import AxisReadiness
        self._readiness=AxisReadiness(self.base,native,None if self.demo else self.on_plot)
        self.detail_tabs.addTab(self._readiness,"Readiness")
        if native:
            self._encoder_overview=EncoderOverview(self.base,demo=self.demo,on_plot=self.on_plot,on_select=self._select_encoder_mode)
            pages["Encoders"].addWidget(self._encoder_overview)
        for index,(group,rows) in enumerate(axis_signals(self.base,self.interface.currentData())):
            box = QtWidgets.QGroupBox(group)
            grid = QtWidgets.QGridLayout(box)
            grid.setContentsMargins(6,5,6,5)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(2)
            compact = True
            soft = group == "ECMC soft limits"
            if soft:
                grid.addWidget(QtWidgets.QLabel("Backward"),0,1)
                grid.addWidget(QtWidgets.QLabel("Forward"),0,2)
                for j,caption in enumerate(("Position (dial)","Enabled RB","Interlock","Enable command"),1):
                    grid.addWidget(QtWidgets.QLabel(caption),j,0)
            position = 0
            for i,(label,pv) in enumerate(rows):
                suffix = pv[len(self.base):]
                name = QtWidgets.QLabel(label)
                name.setWordWrap(True)
                if suffix in SOURCE_COMMANDS:
                    value = SignalValue('—',lambda suffix=suffix:self._select_source(suffix))
                    self._source_widgets[suffix]=value
                elif suffix in BOOL_COMMANDS:
                    value = BooleanValue(lambda old,new,pv=pv: self._toggle_boolean(pv,old,new))
                elif suffix in BOOL_STATUS:
                    alarm = suffix in ("-Blocked","-SumIlockFwd","-SumIlockBwd",".LVIO","-SftLimFwdIlock","-SftLimBwdIlock")
                    limit = suffix in ("-LimFwd","-LimBwd",".HLS",".LLS")
                    value = BooleanStatus("#c43c3c" if alarm else "#b7791f" if limit else "#26804b", "Active" if alarm or limit else "On", "OK" if alarm or limit else "Off")
                else:
                    value = SignalValue("—",lambda pv=pv: self._edit(pv))
                value.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                value.setToolTip(pv)
                if suffix in ("-PosAct",".RBV","-TgtPosCmd",".VAL"):
                    font = value.font()
                    font.setPointSize(max(14,font.pointSize()+4))
                    font.setBold(True)
                    value.setFont(font)
                if suffix in editable or suffix in BOOL_COMMANDS or suffix in MOTION_ENUMS:
                    self._edit_widgets[pv] = value
                if soft:
                    locations = {"-CfgDLLM":(1,1),"-CfgDHLM":(1,2),"-SftLimBwdEna-RB":(2,1),"-SftLimFwdEna-RB":(2,2),"-SftLimBwdIlock":(3,1),"-SftLimFwdIlock":(3,2),"-SftLimBwdEna":(4,1),"-SftLimFwdEna":(4,2)}
                    row,col = locations[suffix]
                    grid.addWidget(value,row,col)
                    name.deleteLater()
                elif compact and suffix not in BOOL_COMMANDS and suffix not in ("-MsgTxt","-IlockTxt"):
                    row, col = position//2, (position%2)*2
                    grid.addWidget(name,row,col)
                    grid.addWidget(value,row,col+1)
                    position += 1
                else:
                    if compact and position % 2:
                        position += 1
                    row = position//2 if compact else i
                    grid.addWidget(name,row,0)
                    grid.addWidget(value,row,1,1,3 if compact else 1)
                    position += 2 if compact else 1
                value.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
                value.customContextMenuRequested.connect(lambda pos,v=value,pv=pv: self._menu(v,pos,pv))
                self._labels[pv] = value
            grid.setColumnStretch(1,1)
            if soft:
                grid.setColumnStretch(2,1)
            elif compact:
                grid.setColumnStretch(3,1)
            if group == "Homing":
                home_row=QtWidgets.QHBoxLayout()
                self.home_selector=QtWidgets.QComboBox()
                self.home_selector.addItem("Use current IOC procedure",None)
                for number,caption in HOME_PROCEDURES:
                    self.home_selector.addItem(f"{number}: {caption}",number)
                home_row.addWidget(self.home_selector,1)
                home=QtWidgets.QPushButton("Home axis")
                home.clicked.connect(lambda checked=False:self._home(self.home_selector.currentData()))
                self._action_buttons.append(home)
                home_row.addWidget(home)
                grid.addLayout(home_row,grid.rowCount(),0,1,4)
                self.home_status=QtWidgets.QLabel("Homing status unavailable")
                self.home_status.setWordWrap(True)
                grid.addWidget(self.home_status,grid.rowCount(),0,1,4)
                self.home_indicators=HomingIndicators()
                grid.addWidget(self.home_indicators,grid.rowCount(),0,1,4)
                self._encoder_homing = EncoderHomingPanel(self.base,demo=self.demo,on_plot=self.on_plot)
                grid.addWidget(self._encoder_homing,grid.rowCount(),0,1,4)
            tab=("Motion" if group.startswith("Motion") else "Homing" if group=="Homing" else
                 "Limits" if group in ("Limits","ECMC soft limits","Interlocks") else
                 "Encoders" if group=="Encoder source" else group if group in ("Controller","Trajectory") else "Diagnostics")
            pages[tab].addWidget(box)
        if not native:
            toggle = QtWidgets.QToolButton()
            toggle.setText("Motor status bits (MSTA)")
            toggle.setCheckable(True)
            toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            toggle.setArrowType(QtCore.Qt.RightArrow)
            details = QtWidgets.QWidget()
            bits = QtWidgets.QGridLayout(details)
            bits.setContentsMargins(4,2,4,2)
            for i,caption in enumerate(MSTA_BITS):
                icon = AxisStatusIcon(f"{i}: {caption}","●","msta:"+caption,
                                      caption in ("Problem","Slip/stall","Communication error","Plus limit","Minus limit"))
                self._msta_icons.append(icon)
                bits.addWidget(icon,i//2,i%2)
            toggle.toggled.connect(details.setVisible)
            toggle.toggled.connect(lambda on,b=toggle: b.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow))
            pages["Diagnostics"].addWidget(toggle)
            pages["Diagnostics"].addWidget(details)
            details.hide()
        for page_layout in pages.values():
            page_layout.addStretch()
        self.scroll.setWidget(root)
        self._update_controls()
        self._start()

    def _update_controls(self,*_):
        armed = self.arm.isChecked() and self.isVisible() and not self._writes and self._enum_task is None and not self._closed and self._jog_native is None and not self._jog_stops
        if hasattr(self,"enable_button"):
            self.enable_button.set_editable(armed and self.enable_button.value is not None)
        for b in self._action_buttons + self._motion_widgets:
            b.setEnabled(armed)
        self.interface.setEnabled(not self._writes and self._jog_native is None and not self._jog_stops)
        for button in self._jog_buttons:
            button.setEnabled(armed or button is self._jog_button)
        if hasattr(self,"jog_speed"): self.jog_speed.setEnabled(armed)
        if self._overview is not None:
            self._overview.set_armed(armed)
        for w in self._source_widgets.values():
            w.set_editable(armed and w.text() in ("Internal","PLC"))
        for pv,w in self._edit_widgets.items():
            available = w.value is not None if isinstance(w,BooleanValue) else w.text() != "—"
            w.set_editable(armed and available)
            w.setCursor(QtCore.Qt.PointingHandCursor if armed else QtCore.Qt.ArrowCursor)

    def _select_source(self,suffix):
        if not self.arm.isChecked() or self._writes or self._enum_task is not None or not self.isVisible() or self.interface.currentData()!="native":
            return False
        widget=self._source_widgets.get(suffix)
        if widget is None or widget.text() not in ("Internal","PLC"):
            return False
        choice,ok=QtWidgets.QInputDialog.getItem(self,"Select source",self.base+SOURCE_COMMANDS[suffix],
                ["Internal","PLC"],int(widget.text()=="PLC"),False)
        if not ok: return True
        mode=int(choice=="PLC")
        if not self.arm.isChecked() or self._writes or not self.isVisible() or self.interface.currentData()!="native":
            return True
        if self.demo:
            if any(self._demo_value(self.base+k)!=0 for k in ("-EnaAct","-EnaCmd-RB","-Busy","-Moving")):
                QtWidgets.QMessageBox.warning(self,"Source selection","Disable drive before changing source")
            else:
                self._overrides[suffix]=mode
                self._tick()
            return True
        if QtWidgets.QMessageBox.question(self,"Confirm source change",f"Select {choice}? Verify the external feedback/setpoint before enabling.",
                QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)==QtWidgets.QMessageBox.Yes:
            if self.arm.isChecked() and not self._writes and self.isVisible() and self.interface.currentData()=="native":
                self._launch_position_task(SourceModeTask(self.base,suffix,mode))
        return True

    def _refresh_draft(self,*_):
        if self._overview is not None and hasattr(self,"move_target"):
            self._overview.set_pending(self.move_target.text() if self.move_mode.currentData()=="abs" else None)

    def _stage_target(self,value):
        if not self.arm.isChecked() or self._writes or self._enum_task is not None or not self.isVisible() or self._jog_native is not None or self._jog_stops:
            return
        self.move_mode.setCurrentIndex(0)
        self.move_target.setText(format_axis_value(value,self._precision))
        if not self.move_velocity.text():
            suffix = "-TgtVelCmd" if self.interface.currentData()=="native" else ".VELO"
            label = self._labels.get(self.base+suffix)
            if label is not None and label.text()!="—":
                self.move_velocity.setText(label.text())

    def _edit_graph_limit(self,suffix):
        self._edit(self.base+suffix)

    def _edit(self,pv):
        if not self.arm.isChecked() or self._writes or not self.isVisible() or pv not in self._edit_widgets or self._labels[pv].text()=="—":
            return False
        suffix = pv[len(self.base):]
        if suffix in MOTION_ENUMS:
            return self._select_motion_mode(pv)
        text,ok = QtWidgets.QInputDialog.getText(self,"Edit axis setting",pv,text=self._labels[pv].text())
        if ok:
            try:
                value = parse_write_value(text,"caTextEntry")
            except ValueError as ex:
                QtWidgets.QMessageBox.warning(self,"Invalid value",str(ex))
                return True
            self._write(suffix,(value,))
        return True

    def _set_precision(self,value):
        self._precision = value
        if self.isVisible():
            self._tick()

    def _toggle_boolean(self,pv,old,new):
        suffix = pv[len(self.base):]
        widget = self._edit_widgets.get(pv)
        if suffix not in BOOL_COMMANDS or not isinstance(widget,BooleanValue) or widget.value != old:
            return
        self._write(suffix,(new,))

    def _select_motion_mode(self,pv):
        if self._enum_task is not None:
            return False
        if self.demo:
            QtWidgets.QMessageBox.information(self,"Motion mode","Demo does not emulate IOC enum metadata.")
            return True
        task = EnumTask(pv,1.0)
        generation = self._generation
        self._enum_task = task
        def ready(choices,error):
            self._enum_task = None
            self._update_controls()
            if self._closed or generation != self._generation or not self.arm.isChecked() or not self.isVisible() or self.interface.currentData() != "native":
                return
            if error or not choices:
                QtWidgets.QMessageBox.warning(self,"Motion mode unavailable",error or "No enum labels")
                return
            labels = [f"{i}: {label}" for i,label in enumerate(choices)]
            current = self._labels[pv].text()
            try:
                index = int(current)
            except ValueError:
                index = choices.index(current) if current in choices else 0
            choice,ok = QtWidgets.QInputDialog.getItem(self,"Motion command",pv,labels,max(0,min(index,len(labels)-1)),False)
            if ok and generation == self._generation:
                self._write(pv[len(self.base):],(labels.index(choice),),enum=True)
        task.signals.finished.connect(ready)
        self._update_controls()
        QtCore.QThreadPool.globalInstance().start(task)
        return True

    def _select_encoder_mode(self,mode):
        if mode is None or self.interface.currentData()!="native" or not self.arm.isChecked() or self._writes or not self.isVisible():
            return
        if not self._encoder_overview.isVisible() or not self._encoder_overview.apply.isEnabled():
            return
        if self.demo:
            self._overrides["-EncMode-RB"]=mode
            self._overrides["-SetEncIdCmd-RB"]=mode or 1
            self._tick()
            return
        if QtWidgets.QMessageBox.question(self,"Encoder selection","Change encoder selection mode? Verify coordinate consistency before enabling the drive.",
                QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:
            return
        if self.arm.isChecked() and not self._writes and self.isVisible() and self.interface.currentData()=="native":
            self._launch_position_task(EncoderModeTask(self.base,mode))

    def _home(self,procedure=None):
        if isinstance(procedure,bool): procedure=None
        if self.interface.currentData()!="native" or not self.arm.isChecked() or self._writes or self._enum_task is not None or not self.isVisible():
            return
        if self.demo:
            if procedure is not None: self._overrides["-movHomCmd"]=procedure
            self._overrides["-Homed"]=1
            self._tick()
            return
        if QtWidgets.QMessageBox.question(self,"Home axis",
                ("Execute current IOC homing procedure?" if procedure is None else f"Select and execute procedure {procedure}: {home_procedure_label(procedure)}?"),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if not self.arm.isChecked() or self._writes or not self.isVisible() or self.interface.currentData()!="native":
            return
        self._launch_position_task(HomeTask(self.base,procedure))

    def _mode_changed(self,*_):
        velocity_mode = self.move_mode.currentData()=="vel"
        self.move_target.setVisible(not velocity_mode)
        self.move_velocity.setPlaceholderText("Signed velocity" if velocity_mode else "Velocity")
        self.move_button.setText("▶ Start" if velocity_mode else "▶ Move")

    def _move(self):
        if not self.arm.isChecked() or self._writes or self._enum_task is not None or not self.isVisible():
            return
        if self.move_mode.currentData()=="vel":
            self._start_velocity()
            return
        try:
            target = float(self.move_target.text())
            velocity = float(self.move_velocity.text())
            if not math.isfinite(target) or not math.isfinite(velocity) or velocity <= 0:
                raise ValueError()
        except ValueError:
            QtWidgets.QMessageBox.warning(self,"Invalid move","Enter a finite position and positive velocity.")
            return
        native = self.interface.currentData()=="native"
        relative = self.move_mode.currentData()=="rel"
        if self.demo:
            actual = self._demo_value(self.base+("-PosAct" if native else ".RBV"))
            target = actual+target if relative else target
            if self._demo_value(self.base+("-EnaAct" if native else ".CNEN"))!=1:
                return
            self._demo_motion[native].start(target,velocity)
            self._overrides.update({"-TgtPosCmd":target,"-TgtVelCmd":velocity} if native else {".VAL":target,".VELO":velocity})
            self._tick()
            return
        self._launch_position_task(PositionTask(self.base,native,relative,target,velocity))

    def _start_velocity(self):
        try:
            speed = float(self.move_velocity.text())
            if not math.isfinite(speed) or speed == 0:
                raise ValueError()
        except ValueError:
            QtWidgets.QMessageBox.warning(self,"Invalid velocity","Enter a nonzero signed velocity.")
            return
        native = self.interface.currentData()=="native"
        if self.demo:
            if self._demo_value(self.base+("-EnaAct" if native else ".CNEN"))!=1:
                return
            self._demo_motion[native].start(self._demo_motion[native].position,speed,continuous=True)
            self._tick()
            return
        if QtWidgets.QMessageBox.question(self,"Continuous motion",
                "Start continuous motion? Use STOP to end it.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if not self.arm.isChecked() or self._writes or not self.isVisible() or native != (self.interface.currentData()=="native"):
            return
        self._launch_position_task(VelocityTask(self.base,native,speed))

    def _jog_app_state(self,state):
        if state!=QtCore.Qt.ApplicationActive:
            self._release_jog()

    def eventFilter(self,obj,event):
        if obj is self._jog_button and event.type() in (QtCore.QEvent.UngrabMouse,QtCore.QEvent.Hide,QtCore.QEvent.WindowDeactivate):
            self._release_jog()
        return super().eventFilter(obj,event)

    def _press_jog(self,direction,button):
        if self._jog_native is not None or self._jog_stops or self._writes or self._enum_task is not None or not self.arm.isChecked() or not self.isVisible() or self._closed:
            return
        try:
            speed=float(self.jog_speed.text())
            if not math.isfinite(speed) or speed<=0: raise ValueError()
        except ValueError:
            self.status.setText("Enter a positive jog speed")
            return
        native=self.interface.currentData()=="native"
        self._jog_native=native
        self._jog_button=button
        if self.demo:
            if self._demo_value(self.base+("-EnaAct" if native else ".CNEN"))!=1:
                self._release_jog()
                return
            model=self._demo_motion[native]
            model.start(model.position,direction*speed,continuous=True)
            self._update_controls()
            return
        task=VelocityTask(self.base,native,direction*speed)
        self._jog_task=task
        self._writes.add(task)
        def done(error):
            self._writes.discard(task)
            self._jog_task=None
            cancelled=task.cancelled.is_set()
            if cancelled or error:
                # A stop sent before the final start write completed may have
                # raced with it. Always send another stop after completion.
                self._release_jog()
                self._queue_jog_stop(native)
            if error and not cancelled and not self._closed:
                self.status.setText("Jog start not confirmed: "+error)
            if not self._closed: self._update_controls()
        task.signals.finished.connect(done)
        self._update_controls()
        QtCore.QThreadPool.globalInstance().start(task)

    def _queue_jog_stop(self,native):
        task=AxisWriteTask(self.base+"-StpCmd",(1,)) if native else MotorStopTask(self.base)
        self._jog_stops.add(task)
        def done(error):
            self._jog_stops.discard(task)
            if not self._closed:
                self._update_controls()
                if error:
                    self.arm.setChecked(False)
                    QtWidgets.QMessageBox.warning(self,"Jog stop not confirmed",error+"; verify axis motion immediately.")
        task.signals.finished.connect(done)
        self._stop_pool.start(task)

    def _release_jog(self):
        native=self._jog_native
        if native is None: return
        self._jog_native=None
        button=self._jog_button
        self._jog_button=None
        if button is not None: button.setDown(False)
        if self._jog_task is not None:
            self._jog_task.cancelled.set()
        if self.demo:
            self._demo_motion[native].stop()
        else:
            self._queue_jog_stop(native)
        if not self._closed: self._update_controls()

    def _launch_position_task(self,task):
        self._position_task = task
        self._writes.add(task)
        def done(error):
            self._writes.discard(task)
            self._position_task = None
            if not self._closed:
                self._update_controls()
                if error:
                    QtWidgets.QMessageBox.warning(self,"Move not confirmed",error)
        task.signals.finished.connect(done)
        self._update_controls()
        QtCore.QThreadPool.globalInstance().start(task)

    def cancel_pending_motion(self):
        self._release_jog()
        for model in self._demo_motion.values(): model.stop()
        if self._position_task is not None:
            self._position_task.cancelled.set()
        self.arm.setChecked(False)

    def _kill_motion(self):
        self.cancel_pending_motion()
        native = self.interface.currentData()=="native"
        if self.demo:
            if native:
                self._overrides.update({"-EnaCmd":0,"-EnaCmd-RB":0,"-EnaAct":0,"-Moving":0,"-Busy":0})
            else:
                self._overrides.update({".CNEN":0,".DMOV":1})
            self._tick()
            return
        task = KillTask(self.base,native)
        self._writes.add(task)
        def done(error):
            self._writes.discard(task)
            if not self._closed:
                self._update_controls()
                if error:
                    QtWidgets.QMessageBox.warning(self,"Kill not fully confirmed",error)
        task.signals.finished.connect(done)
        self._update_controls()
        self._stop_pool.start(task)

    def _stop_motion(self):
        self._release_jog()
        for model in self._demo_motion.values(): model.stop()
        if self._position_task is not None:
            self._position_task.cancelled.set()
        self.arm.setChecked(False)
        self._write("-StpCmd" if self.interface.currentData()=="native" else ".STOP",(1,),stop=True)

    def _write(self,suffix,values,stop=False,enum=False):
        if self._closed or not self.isVisible() or (not stop and (not self.arm.isChecked() or self._writes or self._enum_task is not None or self._jog_native is not None or self._jog_stops)):
            return
        native = self.interface.currentData()=="native"
        pv = self.base+suffix
        if not stop:
            allowed = NATIVE_EDIT if native else MOTOR_EDIT
            actions = NATIVE_ACTIONS if native else MOTOR_ACTIONS
            if suffix not in allowed and not (suffix in BOOL_COMMANDS and suffix in ("-SftLimFwdEna","-SftLimBwdEna") and native and values in ((0,),(1,))) and not (enum and native and suffix in MOTION_ENUMS) and not any(suffix==s and tuple(values)==v for _,s,v in actions):
                return
            if not self.demo and QtWidgets.QMessageBox.question(self,"Confirm axis write",
                    f"Write {values} to {pv}? This may start motion.",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
                return
            if not self.arm.isChecked() or not self.isVisible() or self._writes or native != (self.interface.currentData()=="native"):
                return
        if self.demo:
            self._overrides[suffix] = values[-1]
            if suffix in ("-SftLimFwdEna","-SftLimBwdEna"):
                self._overrides[suffix+"-RB"] = values[-1]
            if suffix=="-EnaCmd":
                self._overrides.update({"-EnaCmd-RB":values[-1],"-EnaAct":values[-1]})
            if suffix==".VAL" and self._demo_value(self.base+".CNEN")==1:
                self._demo_motion[False].start(values[-1],max(.001,abs(self._demo_value(self.base+".VELO"))))
            if suffix=="-ExeCmd":
                target=self._demo_value(self.base+"-TgtPosCmd")
                if self._demo_value(self.base+"-EnaAct")==1:
                    self._demo_motion[True].start(target,max(.001,abs(self._demo_value(self.base+"-TgtVelCmd"))))
            self._tick()
            return
        task = MotorStopTask(self.base) if stop and not native else WriteTask(pv,values[0],1.0,"caMenu") if enum else AxisWriteTask(pv,values)
        self._writes.add(task)
        def done(error):
            self._writes.discard(task)
            if not self._closed:
                self._update_controls()
                if error:
                    QtWidgets.QMessageBox.warning(self,"Axis write not confirmed",error)
        task.signals.finished.connect(done)
        self._update_controls()
        (self._stop_pool if stop else QtCore.QThreadPool.globalInstance()).start(task)

    def _trend_position(self):
        if self.on_plot:
            self.on_plot(position_trend_pvs(self.base, self.interface.currentData()),
                         demo_value=self._demo_value if self.demo else None)

    def _menu(self,widget,pos,pv):
        menu = QtWidgets.QMenu(self)
        if self.on_plot:
            action = menu.addAction("Trend PV")
            action.triggered.connect(lambda: self.on_plot([pv],demo_value=self._demo_value if self.demo else None))
            action = menu.addAction("Add PV to existing trend")
            action.triggered.connect(lambda: self.on_plot([pv], add=True, demo_value=self._demo_value if self.demo else None))
            action = menu.addAction("Trend actual / target position")
            action.triggered.connect(self._trend_position)
        action = menu.addAction("Copy PV name")
        action.triggered.connect(lambda: QtWidgets.QApplication.clipboard().setText(pv))
        if hasattr(menu,"exec"):
            menu.exec(widget.mapToGlobal(pos))
        else:
            menu.exec_(widget.mapToGlobal(pos))

    def _demo_value(self,pv):
        suffix = pv[len(self.base):]
        if suffix in self._overrides:
            return self._overrides[suffix]
        t = time.monotonic()-self._started
        actual = 10+0.01*math.sin(t)
        values = {"-PosAct":actual,"-PosSet":10,"-TgtPosCmd":10,"-PosErr":10-actual,
                  "-VelAct":0,"-TgtVelCmd":1,"-AccCmd":2,"-DecCmd":2,
                  "-TgtPosCmd.EGU":"mm","-EnaCmd":1,"-EnaCmd-RB":1,"-EnaAct":1,"-Homed":1,
                  "-CfgDLLM":-50,"-CfgDHLM":50,"-LimFwd":1,"-LimBwd":1,"-SftLimFwdEna":1,"-SftLimBwdEna":1,"-SftLimFwdEna-RB":1,"-SftLimBwdEna-RB":1,
                  "-Enc01-HmeProc":11,"-Enc01-HmeVelToCam":1,"-Enc01-HmeVelFrmCam":0.2,"-Enc01-HmeAcc":2,"-Enc01-HmeDec":2,"-Enc01-Homed":1,"-movHomCmd":14,"-AtTarget":1,"-InRT":1,"-EncCnt":2,"-SetEncIdCmd-RB":1,
                  "-MsgTxt":"","-IlockTxt":"","-Ctrl-Kp":1,"-Ctrl-Ki":0,"-Ctrl-Kd":0,
                  ".RBV":actual+5,".VAL":15,".VELO":1,".EGU":"mm",".DMOV":1,
                  ".CNEN":1,".MSTA":0x4102,".HLM":50,".LLM":-50,".TWV":0.1}
        return values.get(suffix,0)

    def _start(self):
        if self._closed or not self.isVisible() or self.timer.isActive():
            return
        if not self.demo:
            try:
                import epics
                for pv in set(self._labels) | {self.base+s for s in self._shortcut_readbacks.values()}:
                    try:
                        self._pvs[pv] = epics.PV(pv,auto_monitor=True)
                    except Exception:
                        pass
            except ImportError:
                self.status.setText("Channel Access unavailable")
                return
        self.timer.start()
        self._tick()

    def _stop(self):
        self.timer.stop()
        for pv in self._pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self._pvs.clear()
        for label in self._labels.values():
            if isinstance(label,(BooleanValue,BooleanStatus)):
                label.set_value(None)
            else:
                label.setText("—")
        if self._overview is not None:
            self._overview.set_values({})
            self._overview.set_armed(False)
            self._overview.set_pending(None)
        for label in getattr(self,"_summary_values",{}).values():
            label.setText("—")
        for icon in self._status_icons + self._msta_icons:
            icon.set_values({})
        if hasattr(self,"enable_button"): self.enable_button.set_value(None)
        for button in getattr(self,"_shortcut_readbacks",{}):
            button.setStyleSheet("QPushButton:pressed { background-color: #26804b; color: white; }")
        if getattr(self,"home_indicators",None) is not None: self.home_indicators.update_samples({})
        if hasattr(self,'_readiness'): self._readiness.update_samples({})
        self.status.setText("Paused")
        self.alarm_summary.set_summary("unknown", "Monitoring paused")
        self.status.setStyleSheet("")

    def _tick(self):
        if not self.isVisible():
            self._stop()
            return
        if self.demo:
            now=time.monotonic()
            dt=now-self._demo_tick_time
            self._demo_tick_time=now
            for native,model in self._demo_motion.items():
                low,high=("-CfgDLLM","-CfgDHLM") if native else (".LLM",".HLM")
                if self._demo_value(self.base+("-EnaAct" if native else ".CNEN"))!=1: model.stop()
                model.step(dt,self._demo_value(self.base+low),self._demo_value(self.base+high))
            x,m=self._demo_motion[True],self._demo_motion[False]
            self._overrides.update({"-PosAct":x.position,"-PosSet":x.position,"-VelAct":x.velocity,
                "-PosErr":0,"-Busy":int(x.active),"-Moving":int(x.active),"-AtTarget":int(not x.active),
                ".RBV":m.position,".DMOV":int(not m.active),".MSTA":0x4100 | (0x400 if m.active else 2)})
        count = 0
        samples = {}
        for name,label in self._labels.items():
            value = None
            if self.demo:
                value = self._demo_value(name)
            else:
                pv = self._pvs.get(name)
                try:
                    if pv is not None and pv.connected and pv.severity != 3:
                        value = pv.get(use_monitor=True,timeout=0.001)
                except Exception:
                    pass
            samples[name[len(self.base):]] = value
            count += value is not None
            if isinstance(label,(BooleanValue,BooleanStatus)):
                label.set_value(axis_status_value(name[len(self.base):],value))
            else:
                suffix = name[len(self.base):]
                text = source_label(value) if suffix in SOURCE_COMMANDS else format_axis_value(value,self._precision)
                color = ""
                if suffix in ("-ErrId","-WrnId"):
                    try:
                        code = int(value)
                        text = f"0x{code:X}" if code else "0"
                        color = ("#c43c3c" if suffix=="-ErrId" else "#b7791f") if code else "#26804b"
                    except (TypeError,ValueError,OverflowError):
                        pass
                elif suffix in ("-PosAct",".RBV","-VelAct"):
                    color = "#287bc0" if value is not None else ""
                elif suffix in ("-TgtPosCmd",".VAL","-TgtVelCmd",".VELO"):
                    color = "#26804b" if value is not None else ""
                style = f"color: {color};" if color else ""
                if label.styleSheet() != style:
                    label.setStyleSheet(style)
                if label.text() != text:
                    label.setText(text)
        self.enable_button.set_value(samples.get("-EnaCmd-RB" if self.interface.currentData()=="native" else ".CNEN"))
        for button,suffix in self._shortcut_readbacks.items():
            value=None
            if not self.demo:
                try:
                    pv=self._pvs.get(self.base+suffix)
                    if pv is not None and pv.connected and pv.severity!=3:
                        value=pv.get(use_monitor=True,timeout=.001)
                except Exception: pass
            style="QPushButton:pressed { background-color: #26804b; color: white; }"
            if value==1: style+=" QPushButton { background-color: #26804b; color: white; }"
            if button.styleSheet()!=style: button.setStyleSheet(style)
        if self.home_indicators is not None: self.home_indicators.update_samples(samples)
        if self.home_status is not None:
            proc=samples.get("-movHomCmd")
            self.home_status.setText(f"IOC procedure: {home_procedure_label(proc)} · Homed: {samples.get('-Homed')} · Busy: {samples.get('-Busy')} · Sequence state: {samples.get('-SeqState')}")
        if self._encoder_homing is not None:
            self._encoder_homing.set_axis_state(samples.get("-EncCnt"),samples.get("-SetEncIdCmd-RB"))
        if self._encoder_overview is not None:
            self._encoder_overview.set_axis_state(samples.get("-EncCnt"),samples.get("-SetEncIdCmd-RB"),samples.get("-EncMode-RB"),self.arm.isChecked() and not self._writes and self._jog_native is None and not self._jog_stops and all(samples.get(k)==0 for k in ("-EnaAct","-EnaCmd-RB","-Busy","-Moving")))
        self._readiness.update_samples(samples)
        self.alarm_summary.set_summary(*axis_alarm_summary(samples, self.interface.currentData() == 'native'))
        self._overview.set_values(samples)
        for suffix,label in self._summary_values.items():
            text = format_axis_value(samples.get(suffix),self._precision)
            if label.text()!=text:
                label.setText(text)
        samples.update({"msta:"+label: bit for label,bit in decode_msta(samples.get(".MSTA")).items()})
        for icon in self._status_icons + self._msta_icons:
            icon.set_values(samples)
        self.status.setText(f"{'DEMO — ' if self.demo else ''}{count}/{len(self._labels)} signals available")
        status_style = "color: #26804b;" if count == len(self._labels) else "color: #b7791f;" if count else "color: #c43c3c;"
        if self.status.styleSheet() != status_style:
            self.status.setStyleSheet(status_style)

        self._update_controls()

    def showEvent(self,event):
        super().showEvent(event)
        self.arm.setChecked(not self._write_protected)
        QtCore.QTimer.singleShot(0,self._start)

    def hideEvent(self,event):
        self._release_jog()
        for model in self._demo_motion.values(): model.stop()
        if self._position_task is not None:
            self._position_task.cancelled.set()
        self._generation += 1
        self._stop()
        super().hideEvent(event)

    def closeEvent(self,event):
        self._release_jog()
        if self._position_task is not None:
            self._position_task.cancelled.set()
        self._closed = True
        self._stop()
        super().closeEvent(event)
