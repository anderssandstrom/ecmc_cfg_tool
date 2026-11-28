"""Compact side-by-side native axis troubleshooting and control."""
import math
from ecmc_compact_layout import column_count, visible_width
import time
from ecmc_demo_model import DemoMotion
from ecmc_pv_menu import bind_pv_menu
from ecmc_axis_position import PositionTask, HomeTask, KillTask
from ecmc_axis_write import AxisWriteTask
from qt_compat import QtCore, QtWidgets
from ecmc_axis_diagnostics import axis_alarm_summary, axis_status_value, format_axis_value
from ecmc_hardware_widgets import AlarmSummary
from ecmc_axis_overview import AxisPositionBar, AxisStatusIcon

FIELDS = (
    ('Actual position','-PosAct'),('Target position','-TgtPosCmd'),
    ('Following error','-PosErr'),('Velocity','-VelAct'),('Units','-TgtPosCmd.EGU'),
    ('Enabled','-EnaAct'),('Homed','-Homed'),('Moving','-Moving'),
    ('At target','-AtTarget'),('Busy','-Busy'),('RT','-InRT'),
    ('Forward limit','-LimFwd'),('Backward limit','-LimBwd'),
    ('Forward interlock','-SumIlockFwd'),('Backward interlock','-SumIlockBwd'),
    ('Blocked','-Blocked'),('Error','-ErrId'),('Warning','-WrnId'),
)
EXTRA = ('-MsgTxt','-IlockTxt','-SftLimFwdIlock','-SftLimBwdIlock',
         '-CfgDLLM','-CfgDHLM','-SftLimFwdEna-RB','-SftLimBwdEna-RB','-TgtVelCmd')
SUFFIXES = tuple(s for _,s in FIELDS)+EXTRA
FLAGS = {s for _,s in FIELDS[5:16]}
BAD_FLAGS = {'-LimFwd','-LimBwd','-SumIlockFwd','-SumIlockBwd','-Blocked'}
MAX_AXES = 8

def selected_axes(axes):
    result = {}
    for axis in axes:
        base=axis.get('motor')
        if base and base not in result:
            result[base]=axis
    return list(result.values())

def display_sample(suffix,value):
    if value is None:
        return '—','#89939e'
    if suffix in FLAGS:
        flag=axis_status_value(suffix,value)
        if flag not in (0,1):
            return '—','#89939e'
        bad=(flag==1 if suffix in BAD_FLAGS else flag==0 if suffix=='-InRT' else False)
        return ('Yes' if flag else 'No'), '#c43c3c' if bad else '#26804b' if flag and suffix not in ('-Moving','-Busy') else ''
    if suffix in ('-ErrId','-WrnId'):
        try:
            code=int(value)
            if float(value)!=code:
                raise ValueError()
        except (TypeError,ValueError,OverflowError):
            return '—','#89939e'
        return f'0x{code:X}', ('#c43c3c' if suffix=='-ErrId' else '#b7791f') if code else '#26804b'
    return format_axis_value(value,7), '#287bc0' if suffix=='-PosAct' else '#26804b' if suffix=='-TgtPosCmd' else ''

class AxisTroubleshooting(QtWidgets.QWidget):
    def __init__(self, axes, initial=(), demo=False, on_axis=None, on_plot=None):
        super().__init__()
        self.axes=selected_axes(axes)
        self.demo=demo
        self.on_axis,self.on_plot=on_axis,on_plot
        self.leases={}
        self.cards=[]
        self.closed=False
        self.protected=False
        self.pending={}
        self.controls={}
        self.drafts={}
        self.icons={}
        self.models={}
        self.demo_enabled={}
        self.demo_homed={}
        self.last_tick=time.monotonic()
        self.stop_pool=QtCore.QThreadPool(self)
        self.stop_pool.setMaxThreadCount(8)
        layout=QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4,4,4,4)
        layout.setSpacing(4)
        bar=QtWidgets.QHBoxLayout()
        choose=QtWidgets.QPushButton('Select axes…')
        choose.clicked.connect(self._choose)
        bar.addWidget(choose)
        self.status=QtWidgets.QLabel('Read-only')
        bar.addWidget(self.status,1)
        stop=QtWidgets.QPushButton('STOP selected')
        stop.clicked.connect(self._stop_all)
        bar.addWidget(stop)
        if self.on_plot:
            trend=QtWidgets.QToolButton()
            trend.setText('Trend all')
            trend.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            menu=QtWidgets.QMenu(trend)
            for title,suffix in (('Positions','-PosAct'),('Velocities','-VelAct'),('Following errors','-PosErr')):
                action=menu.addAction(title)
                action.triggered.connect(lambda checked=False,s=suffix:self._trend_selected(s))
            trend.setMenu(menu)
            bar.addWidget(trend)
        layout.addLayout(bar)
        self.scroll=QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.viewport().installEventFilter(self)
        self._axis_grid=None
        self._axis_widgets=[]
        self._axis_columns=0
        layout.addWidget(self.scroll,1)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._tick)
        self.set_axes(initial)

    def _choose(self):
        dialog=QtWidgets.QDialog(self)
        dialog.setWindowTitle('Select up to 8 axes')
        layout=QtWidgets.QVBoxLayout(dialog)
        items=QtWidgets.QListWidget()
        items.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        active={a['motor'] for a,_,_,_ in self.cards}
        for axis in self.axes:
            item=QtWidgets.QListWidgetItem(f"{axis.get('id','')}  {axis.get('name',axis['motor'])}")
            item.setData(QtCore.Qt.UserRole,axis)
            items.addItem(item)
            item.setSelected(axis['motor'] in active)
        layout.addWidget(items)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok|QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        execute=getattr(dialog,'exec',None) or dialog.exec_
        if execute():
            chosen=[i.data(QtCore.Qt.UserRole) for i in items.selectedItems()]
            if len(chosen)>MAX_AXES:
                QtWidgets.QMessageBox.information(self,'Axis selection','Select at most 8 axes per view.')
                return
            self.set_axes(chosen)

    def set_axes(self, axes):
        self._stop()
        self.cards=[]
        self.controls={}
        self.drafts={}
        self.icons={}
        root=QtWidgets.QWidget()
        row=QtWidgets.QGridLayout(root)
        row.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self._axis_grid=row
        self._axis_widgets=[]
        self._axis_columns=0
        row.setContentsMargins(2,2,2,2)
        row.setSpacing(4)
        for axis in selected_axes(axes)[:MAX_AXES]:
            card=QtWidgets.QGroupBox(f"{axis.get('id','')} · {axis.get('name',axis['motor'])}")
            card.setToolTip(axis['motor'])
            card.setMinimumWidth(215)
            card.setMaximumWidth(300)
            card.setSizePolicy(QtWidgets.QSizePolicy.Preferred,QtWidgets.QSizePolicy.Maximum)
            col=QtWidgets.QVBoxLayout(card)
            col.setContentsMargins(5,5,5,5)
            col.setSpacing(2)
            alarm=AlarmSummary()
            col.addWidget(alarm)
            grid=QtWidgets.QGridLayout()
            grid.setSpacing(2)
            labels={}
            detail=QtWidgets.QWidget()
            detail_grid=QtWidgets.QGridLayout(detail)
            detail_grid.setContentsMargins(0,0,0,0)
            detail_grid.setSpacing(1)
            for i,(caption,suffix) in enumerate(FIELDS):
                label=QtWidgets.QLabel('—')
                label.setAlignment(QtCore.Qt.AlignRight|QtCore.Qt.AlignVCenter)
                bind_pv_menu(label,axis['motor']+suffix,self.on_plot,self._demo_value if self.demo else None,suffix!='-TgtPosCmd.EGU')
                if i<2:
                    font=label.font()
                    font.setBold(True)
                    font.setPointSize(max(13,font.pointSize()+2))
                    label.setFont(font)
                if i<4:
                    captions=('Actual','Target','Following err.','Velocity')
                    grid.addWidget(QtWidgets.QLabel(captions[i]),(i//2)*2,(i%2))
                    grid.addWidget(label,(i//2)*2+1,(i%2))
                elif i==4:
                    grid.addWidget(label,4,1)
                else:
                    detail_grid.addWidget(QtWidgets.QLabel(caption),i-5,0)
                    detail_grid.addWidget(label,i-5,1)
                labels[suffix]=label
            col.addLayout(grid)
            icons=QtWidgets.QGridLayout()
            icons.setSpacing(2)
            self.icons[axis['motor']]=[]
            for i,(caption,glyph,suffix) in enumerate((('Enabled','⏻','-EnaAct'),('Homed','⌂','-Homed'),('Moving','↔','-Moving'),('At target','◎','-AtTarget'),('Busy','●','-Busy'),('RT','◷','-InRT'))):
                icon=AxisStatusIcon(caption,glyph,suffix)
                bind_pv_menu(icon,axis['motor']+suffix,self.on_plot,self._demo_value if self.demo else None)
                icons.addWidget(icon,i//3,i%3)
                self.icons[axis['motor']].append(icon)
            col.addLayout(icons)
            track=AxisPositionBar(True)
            col.addWidget(track)
            base=axis['motor']
            if base not in self.models:
                self.models[base]=DemoMotion(10+len(self.models)*5)
                self.demo_enabled[base]=1
                self.demo_homed[base]=1
            control=QtWidgets.QGridLayout()
            control.setSpacing(2)
            target=QtWidgets.QLineEdit()
            target.setPlaceholderText('Position')
            self.drafts[base]=set()
            track.on_target=lambda value,b=base,t=target:self._stage(b,t,value)
            target.textEdited.connect(lambda *_,b=base:self.drafts[b].add('target'))
            target.textChanged.connect(lambda text,b=base,tr=track:tr.set_pending(text if 'target' in self.drafts.get(b,set()) else None))
            speed=QtWidgets.QLineEdit()
            speed.setPlaceholderText('Velocity')
            speed.textEdited.connect(lambda *_,b=base:self.drafts[b].add('speed'))
            bind_pv_menu(target,base+'-TgtPosCmd',self.on_plot,self._demo_value if self.demo else None)
            bind_pv_menu(speed,base+'-TgtVelCmd',self.on_plot,self._demo_value if self.demo else None)
            control.addWidget(target,0,0)
            control.addWidget(speed,0,1)
            buttons=[]
            for i,(caption,action) in enumerate((
                ('Move',lambda b=base,t=target,v=speed:self._move(b,t.text(),v.text())),
                ('Home',lambda b=base:self._command(b,'home')),
                ('Enable',lambda b=base:self._command(b,'enable')),
                ('Disable',lambda b=base:self._command(b,'disable')),
                ('Reset',lambda b=base:self._command(b,'reset')),
                ('STOP',lambda b=base:self._command(b,'stop')),
                ('KILL',lambda b=base:self._command(b,'kill')))):
                button=QtWidgets.QPushButton(caption)
                button.clicked.connect(lambda checked=False,fn=action:fn())
                control.addWidget(button,1+i//2,i%2)
                if caption in ('STOP','KILL'):
                    button.setStyleSheet('color: #c43c3c; font-weight: bold')
                else:
                    buttons.append(button)
            self.controls[base]=(target,speed,buttons)
            col.addLayout(control)
            actions=QtWidgets.QHBoxLayout()
            if self.on_axis:
                button=QtWidgets.QPushButton('Expert')
                button.clicked.connect(lambda checked=False,a=axis:self.on_axis(a))
                actions.addWidget(button)
            if self.on_plot:
                button=QtWidgets.QPushButton('Trend')
                button.clicked.connect(lambda checked=False,a=axis:self._trend(a))
                actions.addWidget(button)
            toggle=QtWidgets.QToolButton()
            toggle.setText('Details')
            toggle.setCheckable(True)
            toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            toggle.setArrowType(QtCore.Qt.RightArrow)
            toggle.toggled.connect(detail.setVisible)
            toggle.toggled.connect(lambda on,t=toggle:t.setArrowType(QtCore.Qt.DownArrow if on else QtCore.Qt.RightArrow))
            actions.addWidget(toggle)
            col.addLayout(actions)
            col.addWidget(detail)
            detail.hide()
            col.addStretch()
            self._axis_widgets.append(card)
            row.addWidget(card,len(self._axis_widgets)-1,0,QtCore.Qt.AlignTop)
            self.cards.append((axis,labels,alarm,track))
        self.scroll.setWidget(root)
        self._reflow_axes()
        QtCore.QTimer.singleShot(0,self._reflow_axes)
        self._update_controls()
        if self.isVisible():
            self._start()

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type()==QtCore.QEvent.Resize:
            QtCore.QTimer.singleShot(0,self._reflow_axes)
        return super().eventFilter(watched,event)

    def _reflow_axes(self):
        if self.closed or self._axis_grid is None or not self._axis_widgets:
            return
        required=max(w.minimumSizeHint().width() for w in self._axis_widgets)
        columns=column_count(visible_width(self.scroll.viewport())-12,len(self._axis_widgets),max(300,required))
        if columns==self._axis_columns:
            return
        self._axis_columns=columns
        for w in self._axis_widgets:
            self._axis_grid.removeWidget(w)
        for i,w in enumerate(self._axis_widgets):
            self._axis_grid.addWidget(w,i//columns,i%columns,QtCore.Qt.AlignTop)

    def set_write_protected(self, protected):
        self.protected=bool(protected)
        if self.protected:
            self.cancel_pending_motion()
        self._update_controls()

    def cancel_pending_motion(self):
        for tasks in self.pending.values():
            for task in tasks:
                if hasattr(task,'cancelled') and not getattr(task,'stop_command',False):
                    task.cancelled.set()

    def _update_controls(self):
        self.status.setText(f"{len(self.cards)} axes · "+('Protected' if self.protected else 'Control'))
        for base,(target,speed,buttons) in self.controls.items():
            enabled=not self.protected and not self.pending.get(base)
            for widget in [target,speed]+buttons:
                widget.setEnabled(enabled)

    def _launch(self,base,task,stop=False):
        if not stop and (self.protected or self.pending.get(base) or not self.isVisible()):
            return
        task.stop_command=stop
        self.pending.setdefault(base,set()).add(task)
        def done(error):
            self.pending.get(base,set()).discard(task)
            if not self.closed:
                self._update_controls()
                if error:
                    QtWidgets.QMessageBox.warning(self,'Command not confirmed',base+': '+error)
                elif type(task) is PositionTask:
                    self.drafts.get(base,set()).clear()
        task.signals.finished.connect(done)
        self._update_controls()
        (self.stop_pool if stop else QtCore.QThreadPool.globalInstance()).start(task)

    def _move(self,base,target,speed):
        if self.protected or self.pending.get(base) or not self.isVisible():
            return
        try:
            target,speed=float(target),float(speed)
            if not math.isfinite(target) or not math.isfinite(speed) or speed<=0:
                raise ValueError()
        except ValueError:
            QtWidgets.QMessageBox.warning(self,'Invalid move','Enter finite position and positive velocity.')
            return
        if self.demo:
            if self.demo_enabled[base] and -50<=target<=50 and not self.models[base].active:
                self.models[base].start(target,speed)
                self.drafts[base].clear()
            self._tick()
            return
        self._launch(base,PositionTask(base,True,False,target,speed))

    def _command(self,base,action):
        stop=action in ('stop','kill')
        if not stop and (self.protected or self.pending.get(base) or not self.isVisible()):
            return
        if stop:
            for task in self.pending.get(base,()):
                if hasattr(task,'cancelled') and not getattr(task,'stop_command',False):
                    task.cancelled.set()
        if self.demo:
            if action=='enable': self.demo_enabled[base]=1
            elif action=='disable': self.demo_enabled[base]=0; self.models[base].stop()
            elif action=='stop': self.models[base].stop()
            elif action=='kill': self.models[base].stop(); self.demo_enabled[base]=0
            elif action=='home' and self.demo_enabled[base]: self.demo_homed[base]=1
            self._tick()
            return
        if not stop:
            if QtWidgets.QMessageBox.question(self,'Confirm axis command',f'{action.capitalize()} {base}?',QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:
                return
        if action=='home':
            task=HomeTask(base)
        elif action=='kill':
            task=KillTask(base,True)
        else:
            suffix,values={'enable':('-EnaCmd',(1,)), 'disable':('-EnaCmd',(0,)), 'reset':('-ErrRst',(1,0)), 'stop':('-StpCmd',(1,))}[action]
            task=AxisWriteTask(base+suffix,values)
        self._launch(base,task,stop)

    def _stop_all(self):
        for axis,_,_,_ in self.cards:
            self._command(axis['motor'],'stop')

    def _demo_value(self, name):
        for i,(axis,_,_,_) in enumerate(self.cards):
            base=axis['motor']
            if name.startswith(base+'-'):
                suffix=name[len(base):]
                model=self.models[base]
                pos=model.position
                values={'-PosAct':pos,'-TgtPosCmd':model.target,'-TgtVelCmd':abs(model.speed) or 1,'-PosErr':0,'-VelAct':model.velocity,
                        '-TgtPosCmd.EGU':'mm','-EnaAct':self.demo_enabled[base],'-Homed':self.demo_homed[base],'-AtTarget':int(not model.active),'-Busy':int(model.active),'-Moving':int(model.active),'-InRT':1,
                        '-LimFwd':1,'-LimBwd':1,'-CfgDLLM':-50,'-CfgDHLM':50,
                        '-SftLimFwdEna-RB':1,'-SftLimBwdEna-RB':1,'-MsgTxt':'','-IlockTxt':''}
                return values.get(suffix,0)
        return None

    def _trend_selected(self,suffix):
        if self.cards and self.on_plot:
            self.on_plot([a['motor']+suffix for a,_,_,_ in self.cards],demo_value=self._demo_value if self.demo else None)

    def _trend(self,axis):
        self.on_plot([axis['motor']+s for s in ('-PosAct','-TgtPosCmd')],
                     demo_value=self._demo_value if self.demo else None)

    def _start(self):
        if self.closed or self.timer.isActive() or not self.cards:
            return
        if not self.demo:
            from ecmc_pv_subscriptions import shared_subscriptions
            for axis,_,_,_ in self.cards:
                for suffix in SUFFIXES:
                    name=axis['motor']+suffix
                    try:
                        self.leases[name]=shared_subscriptions.acquire(name)
                    except Exception:
                        pass
        self.timer.start()
        self._tick()

    def _read(self,name):
        if self.demo:
            return self._demo_value(name)
        lease=self.leases.get(name)
        if lease:
            snap=lease.snapshot()
            if snap.get('connected') and snap.get('received') is not None and snap.get('severity')!=3:
                return snap.get('value')
        return None

    def _stage(self,base,target,value):
        if target.isEnabled():
            self.drafts[base].add('target')
            target.setText(format_axis_value(value,9))

    def _prefill(self,base,samples,track):
        target,speed,_=self.controls[base]
        for key,field,suffix in (('target',target,'-TgtPosCmd'),('speed',speed,'-TgtVelCmd')):
            if key in self.drafts[base] or field.hasFocus():
                continue
            value=samples.get(suffix)
            try: valid=value is not None and math.isfinite(float(value))
            except (TypeError,ValueError,OverflowError): valid=False
            text=format_axis_value(value,9) if valid else ''
            if field.text()!=text: field.setText(text)
        track.set_pending(target.text() if 'target' in self.drafts[base] else None)
        track.set_armed(target.isEnabled() and samples.get('-CfgDLLM') is not None and samples.get('-CfgDHLM') is not None)

    def _tick(self):
        now=time.monotonic()
        if self.demo:
            for model in self.models.values():
                model.step(now-self.last_tick,-50,50)
        self.last_tick=now
        self._update_controls()
        for axis,labels,alarm,track in self.cards:
            samples={s:self._read(axis['motor']+s) for s in SUFFIXES}
            for suffix,label in labels.items():
                text,color=display_sample(suffix,samples[suffix])
                if label.text()!=text:
                    label.setText(text)
                style='color: '+color+';' if color else ''
                if label.styleSheet()!=style:
                    label.setStyleSheet(style)
            severity,message=axis_alarm_summary(samples)
            alarm.set_summary(severity,'No active alarms' if severity=='ok' else message)
            for icon in self.icons[axis['motor']]: icon.set_values(samples)
            self._prefill(axis['motor'],samples,track)
            track.set_values(samples)

    def _stop(self):
        self.cancel_pending_motion()
        self.timer.stop()
        for lease in self.leases.values():
            try: lease.close()
            except Exception: pass
        self.leases.clear()
        for axis,labels,alarm,track in self.cards:
            for label in labels.values():
                label.setText('—')
            alarm.set_summary('unknown','Monitoring paused')
            track.set_values({})
            track.set_armed(False)
            for icon in self.icons.get(axis['motor'],[]): icon.set_values({})

    def showEvent(self,event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(0,self._reflow_axes)
        self._start()

    def hideEvent(self,event):
        self._stop()
        super().hideEvent(event)

    def closeEvent(self,event):
        self.closed=True
        self._stop()
        super().closeEvent(event)
