"""Visibility-scoped axis commissioning controls."""
import math
import time
from qt_compat import QtCore, QtWidgets
from ecmc_axis_diagnostics import axis_signals, NATIVE_EDIT, MOTOR_EDIT, NATIVE_ACTIONS, MOTOR_ACTIONS, BOOL_COMMANDS, BOOL_STATUS, MOTION_ENUMS, format_axis_value, axis_status_value, decode_msta, MSTA_BITS
from ecmc_hardware_widgets import SignalValue, BooleanValue, BooleanStatus
from ecmc_axis_write import AxisWriteTask
from ecmc_axis_position import PositionTask, HomeTask, VelocityTask, MotorStopTask, KillTask
from ecmc_axis_overview import AxisPositionBar, AxisStatusIcon
from ecmc_hardware_write import parse_write_value, EnumTask, WriteTask

class AxisExpertPanel(QtWidgets.QWidget):
    def __init__(self, base, axis_id, demo=False, on_plot=None, actions=(), interface="native"):
        super().__init__()
        self.base,self.demo,self.on_plot = base,demo,on_plot
        self._closed = False
        self._pvs = {}
        self._labels = {}
        self._started = time.monotonic()
        self._overrides = {}
        self._writes = set()
        self._enum_task = None
        self._precision = 8
        self._generation = 0
        self._edit_widgets = {}
        self._action_buttons = []
        self._position_task = None
        self._motion_widgets = []
        self._overview = None
        self._status_icons = []
        self._msta_icons = []
        self._stop_pool = QtCore.QThreadPool(self)
        self._stop_pool.setMaxThreadCount(1)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6,6,6,6)
        layout.setSpacing(5)
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
        controls = QtWidgets.QHBoxLayout()
        self.arm = QtWidgets.QCheckBox("Enable writes")
        self.arm.setToolTip("Arms motion commands; target writes may initiate motion.")
        self.arm.toggled.connect(self._update_controls)
        controls.addWidget(self.arm)
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

    def _rebuild(self,*_):
        self._generation += 1
        self._stop()
        self.arm.setChecked(False)
        self._labels = {}
        self._edit_widgets = {}
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
        layout.addWidget(self._overview)
        command = QtWidgets.QGroupBox("Move")
        command_grid = QtWidgets.QGridLayout(command)
        command_grid.setContentsMargins(5,4,5,4)
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
        available = {caption:(suffix,values) for caption,suffix,values in
                     (NATIVE_ACTIONS if native else MOTOR_ACTIONS)}
        # Reserve identical positions even when a command is unavailable.
        for caption,short in (("Tweak −","◀"),("Tweak +","▶"),
                              ("Home reverse","⌂−"),("Home forward","⌂+"),
                              ("Reset error","↻")):
            button = QtWidgets.QPushButton(short)
            button.setFixedWidth(42)
            button.setAccessibleName(caption)
            button.setSizePolicy(QtWidgets.QSizePolicy.Fixed,QtWidgets.QSizePolicy.Fixed)
            button.setToolTip(caption)
            if native and caption == "Home reverse":
                button.setText("⌂")
                button.setAccessibleName("Home configured procedure")
                button.setToolTip("Home using configured ECMC procedure")
                button.clicked.connect(self._home)
                self._action_buttons.append(button)
            elif caption in available:
                suffix,values = available[caption]
                button.clicked.connect(lambda checked=False,suffix=suffix,values=values: self._write(suffix,values))
                self._action_buttons.append(button)
            else:
                button.setEnabled(False)
                button.setToolTip(caption+" unavailable in this interface")
            action_row.addWidget(button)
        action_row.addStretch()
        layout.addLayout(action_row)
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
                if suffix in BOOL_COMMANDS:
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
                note = QtWidgets.QLabel("Encoder 1 configuration snapshot (read-only). Procedure selection is writable.")
                note.setWordWrap(True)
                grid.addWidget(note,grid.rowCount(),0,1,4)
            if index == 0 or group in ("Limits","ECMC soft limits"):
                layout.addWidget(box)
            else:
                toggle = QtWidgets.QToolButton()
                box.setTitle("")
                toggle.setText(group)
                toggle.setCheckable(True)
                toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
                toggle.setArrowType(QtCore.Qt.RightArrow)
                toggle.toggled.connect(box.setVisible)
                toggle.toggled.connect(lambda on,b=toggle: b.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow))
                layout.addWidget(toggle)
                layout.addWidget(box)
                box.hide()
            if index == 0:
                pass
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
            layout.addWidget(toggle)
            layout.addWidget(details)
            details.hide()
        layout.addStretch()
        self.scroll.setWidget(root)
        self._update_controls()
        self._start()

    def _update_controls(self,*_):
        armed = self.arm.isChecked() and self.isVisible() and not self._writes and self._enum_task is None and not self._closed
        for b in self._action_buttons + self._motion_widgets:
            b.setEnabled(armed)
        self.interface.setEnabled(not self._writes)
        if self._overview is not None:
            self._overview.set_armed(armed)
        for pv,w in self._edit_widgets.items():
            available = w.value is not None if isinstance(w,BooleanValue) else w.text() != "—"
            w.set_editable(armed and available)
            w.setCursor(QtCore.Qt.PointingHandCursor if armed else QtCore.Qt.ArrowCursor)

    def _refresh_draft(self,*_):
        if self._overview is not None and hasattr(self,"move_target"):
            self._overview.set_pending(self.move_target.text() if self.move_mode.currentData()=="abs" else None)

    def _stage_target(self,value):
        if not self.arm.isChecked() or self._writes or self._enum_task is not None or not self.isVisible():
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

    def _home(self):
        if self.interface.currentData()!="native" or not self.arm.isChecked() or self._writes or self._enum_task is not None or not self.isVisible():
            return
        if self.demo:
            self._overrides["-Homed"]=1
            self._tick()
            return
        if QtWidgets.QMessageBox.question(self,"Home axis",
                "Execute configured ECMC homing procedure?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if not self.arm.isChecked() or self._writes or not self.isVisible() or self.interface.currentData()!="native":
            return
        self._launch_position_task(HomeTask(self.base))

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
            if native:
                self._overrides.update({"-PosAct":target,"-PosSet":target,"-TgtPosCmd":target,"-TgtVelCmd":velocity,"-PosErr":0})
            else:
                self._overrides.update({".RBV":target,".VAL":target,".VELO":velocity})
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
            QtWidgets.QMessageBox.information(self,"Demo","Continuous velocity simulation is not yet available.")
            return
        if QtWidgets.QMessageBox.question(self,"Continuous motion",
                "Start continuous motion? Use STOP to end it.",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if not self.arm.isChecked() or self._writes or not self.isVisible() or native != (self.interface.currentData()=="native"):
            return
        self._launch_position_task(VelocityTask(self.base,native,speed))

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
        if self._position_task is not None:
            self._position_task.cancelled.set()
        self.arm.setChecked(False)
        self._write("-StpCmd" if self.interface.currentData()=="native" else ".STOP",(1,),stop=True)

    def _write(self,suffix,values,stop=False,enum=False):
        if self._closed or not self.isVisible() or (not stop and (not self.arm.isChecked() or self._writes or self._enum_task is not None)):
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
            if suffix==".VAL":
                self._overrides[".RBV"]=values[-1]
            if suffix=="-ExeCmd":
                target=self._demo_value(self.base+"-TgtPosCmd")
                self._overrides.update({"-PosAct":target,"-PosSet":target,"-PosErr":0})
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

    def _menu(self,widget,pos,pv):
        menu = QtWidgets.QMenu(self)
        if self.on_plot:
            action = menu.addAction("Trend PV")
            action.triggered.connect(lambda: self.on_plot([pv],demo_value=self._demo_value if self.demo else None))
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
                  "-Enc01-HmeProc":11,"-Enc01-HmeVelToCam":1,"-Enc01-HmeVelFrmCam":0.2,"-Enc01-HmeAcc":2,"-Enc01-HmeDec":2,"-Enc01-Homed":1,"-movHomCmd":14,"-AtTarget":1,"-InRT":1,"-EncCnt":1,"-SetEncIdCmd-RB":1,
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
                for pv in self._labels:
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
        for icon in self._status_icons + self._msta_icons:
            icon.set_values({})
        self.status.setText("Paused")
        self.status.setStyleSheet("")

    def _tick(self):
        if not self.isVisible():
            self._stop()
            return
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
                text = format_axis_value(value,self._precision)
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
        self._overview.set_values(samples)
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
        QtCore.QTimer.singleShot(0,self._start)

    def hideEvent(self,event):
        if self._position_task is not None:
            self._position_task.cancelled.set()
        self._generation += 1
        self.arm.setChecked(False)
        self._stop()
        super().hideEvent(event)

    def closeEvent(self,event):
        if self._position_task is not None:
            self._position_task.cancelled.set()
        self._closed = True
        self._stop()
        super().closeEvent(event)
