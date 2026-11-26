"""Modeless controller tuning window, using native ECMC moves and CA monitors."""
import math
import time
from ecmc_acquisition import bounded_series, append_sample, export_event_csv
from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_native_motion import NativeMotionMixin, NATIVE_PV_SUFFIXES, numeric_readback


def scan_points(a, b, steps):
    if not all(math.isfinite(v) for v in (a, b)) or a == b:
        raise ValueError('Enter two different finite endpoints')
    if not 1 <= steps <= 10000:
        raise ValueError('Steps must be 1–10000')
    return [a+(b-a)*i/steps for i in range(steps+1)]


def next_step(index, direction, count, snake):
    if direction > 0 and index < count-1:
        return index+1, 1
    if direction > 0:
        return (index-1 if snake else 0), -1
    if snake and index > 0:
        return index-1, -1
    return 1, 1


class Trend(QtWidgets.QWidget):
    def __init__(self, series):
        super().__init__()
        self.series = series
        self.samples = bounded_series((key for key, _, _ in series), 40000)
        self.history = 15
        self.frozen = None
        self.fixed_range = None
        self.include_zero = len(series) == 1
        self.axis_precision = 5
        self.limit = None
        self.setMinimumHeight(145)

    def append(self, key, stamp, value):
        append_sample(self.samples[key], stamp, value, keep_gap=True)

    def freeze(self,checked):
        self.frozen = (time.monotonic(), {k:list(q) for k,q in self.samples.items()}) if checked else None
        self.update()

    def clear(self):
        if self.frozen is not None:
            self.frozen = (self.frozen[0], {k:[] for k in self.samples})
        for q in self.samples.values():
            q.clear()
        self.update()

    def paintEvent(self,event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(),QtGui.QColor('#f7f9fb'))
        margin = max(76, self.fontMetrics().horizontalAdvance('−0.' + '0' * self.axis_precision + 'e+00') + 8) if self.axis_precision > 5 else 76
        r = QtCore.QRectF(margin,24,self.width()-margin-14,self.height()-49)
        if r.width() < 10 or r.height() < 10:
            return
        now, source = self.frozen if self.frozen is not None else (time.monotonic(),self.samples)
        start = now-self.history
        data = {}
        for key,q in source.items():
            # Preserve the last sample before the visible window for held values.
            data[key] = [(t,v) for t,v in q if start<=t<=now and v is not None]
        values = [v for a in data.values() for _,v in a]
        p.setPen(QtGui.QColor('#465565'))
        x = margin + 2
        for key,title,color in self.series:
            p.setPen(QtGui.QColor(color))
            p.drawText(int(x),16,title)
            x += 125
        if not values:
            p.drawText(r,QtCore.Qt.AlignCenter,'Waiting for PV samples')
            return
        lo,hi = min(values),max(values)
        if self.include_zero:
            lo,hi = min(0,lo),max(0,hi)
        pad = (hi-lo)*.05 or max(abs(hi)*1e-6,1e-9)
        lo,hi = lo-pad,hi+pad
        if self.fixed_range is not None:
            lo,hi = self.fixed_range
        for i in range(5):
            y = r.top()+r.height()*i/4
            x = r.left()+r.width()*i/4
            p.setPen(QtGui.QColor('#dce3e9'))
            p.drawLine(QtCore.QPointF(r.left(),y),QtCore.QPointF(r.right(),y))
            p.drawLine(QtCore.QPointF(x,r.top()),QtCore.QPointF(x,r.bottom()))
            p.setPen(QtGui.QColor('#465565'))
            p.drawText(QtCore.QRectF(0,y-9,margin-6,18),QtCore.Qt.AlignRight|QtCore.Qt.AlignVCenter,f'{hi-(hi-lo)*i/4:.{self.axis_precision}g}')
            p.drawText(QtCore.QRectF(x-28,r.bottom()+3,56,18),QtCore.Qt.AlignCenter,f'{-self.history*(1-i/4):g}s')
        p.save()
        p.setClipRect(r)
        if self.limit is not None:
            p.setPen(QtGui.QPen(QtGui.QColor('#c43b3b'),1.2,QtCore.Qt.DashLine))
            for v in (-self.limit,self.limit):
                if lo<=v<=hi:
                    y=r.bottom()-(v-lo)/(hi-lo)*r.height()
                    p.drawLine(QtCore.QPointF(r.left(),y),QtCore.QPointF(r.right(),y))
        for key,_,color in self.series:
            path = QtGui.QPainterPath()
            started = False
            for stamp,v in source[key]:
                if v is None:
                    started=False
                    continue
                if stamp<start or stamp>now:
                    continue
                x=r.left()+(stamp-start)/self.history*r.width()
                y=r.bottom()-(v-lo)/(hi-lo)*r.height()
                if started:
                    path.lineTo(x,y)
                else:
                    path.moveTo(x,y)
                    started=True
            p.setPen(QtGui.QPen(QtGui.QColor(color),1.5))
            p.drawPath(path)
        p.restore()


class TuningWindow(NativeMotionMixin,QtWidgets.QDialog):
    sample = QtCore.Signal(str,float,object) if hasattr(QtCore,'Signal') else QtCore.pyqtSignal(str,float,object)

    def __init__(self,parent,client,base,demo=False,read_config=None,read_lag=None,read_jerk=None,write_jerk=None,read_auto_enable=None):
        super().__init__(parent)
        self.setWindowTitle('Controller tuning — '+base+(' [DEMO]' if demo else ''))
        self.setModal(False)
        self.resize(800,570)
        self.client,self.base,self.demo=client,base,demo
        self.read_config=read_config
        self.read_lag=read_lag
        self.read_jerk=read_jerk
        self.write_jerk=write_jerk
        self.read_auto_enable=read_auto_enable
        self.auto_enable_timeout=0.0
        self.scan_failure=''
        self.traj_type=None
        self.paused=False
        self.pause_requested=False
        self.stats_count=0
        self.stats_sum_sq=0.
        self.stats_min=None
        self.stats_max=None
        self.stats_start=time.monotonic()
        self.stop_confirm_after=0.
        self.stop_sent=False
        self.killing=False
        self.sim_enabled=1
        self.sim_auto=1
        self.scan_mode=None
        self.bounds=None
        self.busy_seen_at=0.
        self.active=False
        self.stop_pending=False
        self.pvs={}
        self.latest={}
        self.points=[]
        self.sim_pos=0.
        self.sim_target=0.
        self.last_tick=time.monotonic()
        self.sample.connect(self._sample)
        layout=QtWidgets.QVBoxLayout(self)
        controls=QtWidgets.QGridLayout()
        self.inputs={}
        for i,(key,title,default) in enumerate((
            ('a','A',''),('b','B',''),('steps','Steps','1'),
            ('dwell','Dwell [s]','0.2'),('velo','Velocity',''),('acc','Acc [EGU/s²]',''),('dec','Dec [EGU/s²]',''))):
            edit=QtWidgets.QLineEdit(default)
            self.inputs[key]=edit
            if key in ('a', 'b'):
                edit.setPlaceholderText('Required')
            controls.addWidget(QtWidgets.QLabel(title),0,i)
            controls.addWidget(edit,1,i)
        self.relative=QtWidgets.QCheckBox('Relative')
        self.relative.setChecked(True)
        self.snake=QtWidgets.QCheckBox('Snake')
        self.snake.setChecked(True)
        self.start_btn=QtWidgets.QPushButton('Start scan')
        self.stop_btn=QtWidgets.QPushButton('STOP')
        self.stop_btn.setStyleSheet('font-weight:bold;color:white;background:#a52e2e')
        self.start_btn.clicked.connect(self.start_scan)
        self.stop_btn.clicked.connect(self.stop_scan)
        self.kill_btn=QtWidgets.QPushButton('KILL')
        self.kill_btn.setStyleSheet('font-weight:bold;color:white;background:#711414')
        self.kill_btn.setToolTip('Cancel scan and disable drive. Not a safety-rated emergency stop.')
        self.kill_btn.clicked.connect(self.kill_scan)
        self.mode_label=QtWidgets.QLabel('Mode: unknown')
        for i,w in enumerate((self.relative,self.snake,self.start_btn,self.stop_btn,self.kill_btn)):
            controls.addWidget(w,2,i)
        controls.addWidget(self.mode_label,2,5,1,2)
        self.pause_btn=QtWidgets.QPushButton('Pause')
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self.toggle_pause)
        controls.addWidget(self.pause_btn,3,2)
        self.enable_btn=QtWidgets.QPushButton('Enable drive')
        self.enable_btn.clicked.connect(self.enable_drive)
        controls.addWidget(self.enable_btn,3,3,1,2)
        self.jerk_label=QtWidgets.QLabel('Jerk [EGU/s³]')
        self.jerk_edit=QtWidgets.QLineEdit('')
        self.jerk_edit.setEnabled(False)
        self.jerk_edit.setToolTip('Ruckig only. Applied before scan start through Cfg.SetAxisJerk; blank retains the IOC setting.')
        controls.addWidget(self.jerk_label,3,5)
        controls.addWidget(self.jerk_edit,3,6)
        layout.addLayout(controls)
        self.status=QtWidgets.QLabel('Idle — enter scan endpoints explicitly')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.position=Trend([('PosSet','PosSet','#287bc0'),('PosAct','PosAct','#d06d22')])
        self.error=Trend([('PosErr','PosErr','#a64070')])
        layout.addWidget(self.position,1)
        layout.addWidget(self.error,1)
        stats=QtWidgets.QHBoxLayout()
        self.stats_label=QtWidgets.QLabel('RMS: —    Peak: —    P–P: —')
        stats.addWidget(self.stats_label,1)
        self.lag_label=QtWidgets.QLabel('Lag: unknown')
        stats.addWidget(self.lag_label)
        reset=QtWidgets.QPushButton('Reset stats')
        reset.clicked.connect(self.reset_stats)
        stats.addWidget(reset)
        layout.addLayout(stats)
        row=QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel('History [s]'))
        history=QtWidgets.QSpinBox()
        history.setRange(2,120)
        history.setValue(15)
        history.valueChanged.connect(self._history)
        row.addWidget(history)
        clear=QtWidgets.QPushButton('Clear plots')
        clear.clicked.connect(self.position.clear)
        clear.clicked.connect(self.error.clear)
        row.addWidget(clear)
        freeze=QtWidgets.QCheckBox('Freeze')
        freeze.toggled.connect(self.position.freeze)
        freeze.toggled.connect(self.error.freeze)
        row.addWidget(freeze)
        fixed=QtWidgets.QCheckBox('Fixed ±error')
        self.error_range=QtWidgets.QLineEdit('0.01')
        self.error_range.setMaximumWidth(74)
        self.error_range.setEnabled(False)
        def scale():
            self.error_range.setEnabled(fixed.isChecked())
            try:
                v=float(self.error_range.text())
                if not math.isfinite(v) or v<=0: raise ValueError()
                self.error.fixed_range=(-v,v) if fixed.isChecked() else None
                self.error_range.setStyleSheet('')
            except ValueError:
                self.error_range.setStyleSheet('background:#ffd7d7')
                if not fixed.isChecked(): self.error.fixed_range=None
        fixed.toggled.connect(scale)
        self.error_range.editingFinished.connect(scale)
        row.addWidget(fixed)
        row.addWidget(self.error_range)
        export=QtWidgets.QPushButton('Export CSV')
        export.clicked.connect(self.export_csv)
        row.addWidget(export)
        layout.addLayout(row)
        readouts=QtWidgets.QGridLayout()
        self.readouts={}
        for column,(key,title) in enumerate((
                ('PosAct','Actual position'), ('Target','Target command'),
                ('PosSet','Trajectory setpoint'), ('PosErr','Position error'),
                ('Enabled','Drive state'), ('EnableCmd','Enable request'))):
            readouts.addWidget(QtWidgets.QLabel(title),0,column)
            label=QtWidgets.QLabel('—')
            readouts.addWidget(label,1,column)
            self.readouts[key]=label
        layout.addLayout(readouts)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        if demo:
            self.inputs['a'].setText('-1')
            self.inputs['b'].setText('1')
            self.inputs['velo'].setText('1')
            self.inputs['acc'].setText('5')
            self.inputs['dec'].setText('5')
            self.traj_type=1
            self.jerk_edit.setText('50')
            self.jerk_edit.setEnabled(True)
        else:
            self._connect()
        self.timer.start()

    def _history(self,value):
        self.position.history=self.error.history=value

    def _connect(self):
        ep=getattr(self.client,'_epics',None)
        if ep is None:
            self.status.setText('Tuning popup requires pyepics for CA monitors')
            self.start_btn.setEnabled(False)
            return
        # Keep all graph samples, not just the most recent value at repaint.
        for key,suffix in NATIVE_PV_SUFFIXES.items():
            def cb(pvname=None,value=None,severity=None,_key=key,**kw):
                self.sample.emit(_key,time.monotonic(),numeric_readback(value,severity))
            try:
                self.pvs[key]=ep.PV(self.base+'-'+suffix,auto_monitor=True,callback=cb)
            except Exception:
                pass

    def _sample(self,key,stamp,value):
        self.latest[key]=value
        if key=='TrajType':
            previous=self.traj_type
            self.traj_type=int(value) if value in (0,1) else None
            if self.traj_type!=previous and not self.active:
                self.jerk_edit.clear()
                self.jerk_edit.setEnabled(self.traj_type==1 and not self.stop_pending)
                if self.traj_type==1 and self.read_jerk is not None:
                    # Defer the one-time command read; do not poll the command interface.
                    QtCore.QTimer.singleShot(0,self._load_jerk)
        if key=='Stat' and value is not None and int(value)&(1<<3):
            self.busy_seen_at=stamp
        if key in self.position.samples:
            self.position.append(key,stamp,value)
        if key in self.error.samples:
            self.error.append(key,stamp,value)
            if value is not None and stamp>=self.stats_start:
                self.stats_count+=1
                self.stats_sum_sq+=value*value
                self.stats_min=value if self.stats_min is None else min(value,self.stats_min)
                self.stats_max=value if self.stats_max is None else max(value,self.stats_max)
        if key in ('VELO','ACC','DEC') and value is not None:
            edit=self.inputs[key.lower()]
            if not edit.text().strip():
                edit.setText(f'{value:g}')

    def _load_jerk(self):
        if self.active or self.stop_pending or self.traj_type!=1 or self.jerk_edit.text().strip():
            return
        try:
            value=self.read_jerk()
            if math.isfinite(value) and value>0:
                self.jerk_edit.setText(f'{value:.12g}')
        except Exception as ex:
            self.jerk_edit.setToolTip(f'Jerk read unavailable: {ex}. Blank keeps current setting.')

    def _value(self,key):
        if self.demo:
            busy=int(abs(self.sim_target-self.sim_pos)>1e-9)
            stat=(self.sim_enabled<<1)|(busy<<3)|(int(not busy)<<4)|(1<<6)|(1<<7)|(1<<10)|(self.sim_auto<<24)
            return {'Stat':stat,'Auto':self.sim_auto,'PosAct':self.latest.get('PosAct',self.sim_pos),
                    'PosSet':self.sim_pos,'PosErr':self.latest.get('PosErr'),
                    'Enabled':self.sim_enabled,'EnableCmd':self.sim_enabled,
                    'ErrId':0,'Command':3,'CmdData':0,'Target':self.sim_target}.get(key)
        pv=self.pvs.get(key)
        if pv is None or not pv.connected:
            return None
        return self.latest.get(key)

    def _put(self,field,value):
        if self.demo:
            if field=='TgtPosCmd':
                self.sim_staged=value
                if self.sim_auto:
                    self.sim_target=value
            elif field=='ExeCmd':
                self.sim_target=self.sim_staged
            elif field=='StpCmd':
                self.sim_target=self.sim_pos
            elif field=='EnaCmd':
                self.sim_enabled=int(value)
                if value==0:
                    self.sim_target=self.sim_pos
            return
        self._scan_stage=f'Write {self.base}-{field} = {value:g}'
        print(f'Tuning: {self._scan_stage}', flush=True)
        return super()._put(field,value)

    def _scan_limits(self):
        if self.demo:
            return -100.,100.,10.
        if self.read_config is None:
            raise RuntimeError('Native axis limit configuration unavailable')
        c=self.read_config()
        lo,hi=c.get('lo'),c.get('hi')
        if c.get('lo_enabled')!=1 or c.get('hi_enabled')!=1 or lo is None or hi is None or not lo<hi:
            raise RuntimeError('Both native soft limits must be enabled and valid for tuning scans')
        vmax=c.get('vmax') if c.get('vmax_enabled')==1 else None
        return lo,hi,vmax

    def start_scan(self):
        if self.active or self.stop_pending:
            return
        self.scan_failure=''
        self._scan_stage='Read auto-enable configuration'
        try:
            self.auto_enable_timeout=0.0
            if self.read_auto_enable is not None and not self.demo:
                timeout=float(self.read_auto_enable())
                if math.isfinite(timeout) and timeout>0:
                    self.auto_enable_timeout=timeout
            self._scan_stage='Check native axis readiness'
            if not self.demo:
                for key in ('ErrId','Enabled','EnableCmd','Stat','Auto','PosAct'):
                    pv=self.pvs.get(key)
                    if pv is not None and pv.connected:
                        self.latest[key]=numeric_readback(pv.get(use_monitor=False, timeout=0.7), pv.severity)
            if self.auto_enable_timeout <= 0 and self._value('ErrId') == 0 and self._needs_enable():
                self.enable_btn.setFocus()
                raise RuntimeError('Use Enable drive in this window, then start the scan once the drive is enabled')
            self._ready()
            if self._bit(3)!=0 or self._bit(5)!=0:
                raise RuntimeError('Axis must be stationary before starting')
            self._scan_stage='Validate scan inputs'
            values = {}
            for key, label in (('a', 'Endpoint A'), ('b', 'Endpoint B'),
                               ('dwell', 'Dwell'), ('velo', 'Velocity'),
                               ('acc', 'Acceleration'), ('dec', 'Deceleration'),
                               ('steps', 'Steps')):
                edit = self.inputs[key]
                raw = edit.text().strip()
                try:
                    if not raw:
                        raise ValueError(f'{label}: enter a value')
                    try:
                        value = int(raw) if key == 'steps' else float(raw)
                    except ValueError:
                        raise ValueError(f'{label}: enter a valid number' if key != 'steps'
                                         else 'Steps: enter an integer') from None
                    if not math.isfinite(value):
                        raise ValueError(f'{label}: enter a finite value')
                    if key in ('velo', 'acc', 'dec') and value <= 0:
                        raise ValueError(f'{label}: must be positive')
                    if key == 'dwell' and value < 0:
                        raise ValueError('Dwell: must not be negative')
                    if key == 'steps' and not 1 <= value <= 10000:
                        raise ValueError('Steps: must be 1–10000')
                except ValueError:
                    edit.setFocus()
                    edit.selectAll()
                    raise
                values[key] = value
            a,b,dwell,velo,acc,dec=(values[k] for k in ('a','b','dwell','velo','acc','dec'))
            steps=values['steps']
            jerk=None
            if self.traj_type==1 and self.jerk_edit.text().strip():
                try:
                    jerk=float(self.jerk_edit.text())
                except ValueError:
                    self.jerk_edit.setFocus()
                    self.jerk_edit.selectAll()
                    raise ValueError('Jerk: enter a valid number or leave blank') from None
                if not math.isfinite(jerk) or jerk<=0:
                    raise ValueError('Ruckig jerk must be finite and positive')
                if not self.demo and self.write_jerk is None:
                    raise RuntimeError('Jerk configuration unavailable')
            if self.relative.isChecked():
                actual=self._value('PosAct')
                a,b=a+actual,b+actual
            points=scan_points(a,b,steps)
            self._scan_stage='Read native scan limits'
            lo,hi,vmax=self._scan_limits()
            if min(a,b)<lo or max(a,b)>hi:
                raise RuntimeError('Scan outside native ECMC soft limits')
            if vmax is not None and (vmax<=0 or velo>vmax):
                raise RuntimeError('Velocity exceeds native monitoring limit')
            mode=self._mode()
            self._scan_stage='Confirm motion'
            if not self.demo:
                answer=QtWidgets.QMessageBox.question(self,'Confirm repetitive native motion',
                    f'{self.base}: repeat {a:g} ↔ {b:g} in native coordinates at velocity={velo:g}, acc={acc:g}, dec={dec:g}?\\nSTOP cancels motion; KILL disables drive. Motion parameters remain changed.',
                    QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)
                if answer!=QtWidgets.QMessageBox.Yes:
                    return
                self._ready()
                if self._bit(3)!=0 or self._bit(5)!=0 or self._mode()!=mode:
                    raise RuntimeError('Axis state or execute mode changed')
            self.points,self.index,self.direction=points,0,1
            self.dwell,self.velo,self.acc,self.dec,self.use_snake=dwell,velo,acc,dec,self.snake.isChecked()
            self.scan_mode=mode
            self.bounds=(lo,hi)
            self._put('TgtVelCmd',velo)
            self._put('AccCmd',acc)
            self._put('DecCmd',dec)
            if jerk is not None:
                if self.traj_type!=1:
                    raise RuntimeError('Trajectory type changed')
                if not self.demo:
                    self.write_jerk(jerk)
            self.active=True
            self.jerk_edit.setEnabled(False)
            self.paused=False
            self.pause_requested=False
            self.pause_btn.setEnabled(True)
            self.pause_btn.setText('Pause')
            self.start_btn.setEnabled(False)
            for w in [*self.inputs.values(),self.relative,self.snake]:
                w.setEnabled(False)
            self._move()
        except Exception as ex:
            stage=self._scan_stage
            if self.active:
                self.stop_scan()
            self.scan_failure=f'{stage}: {ex}'
            self.status.setText(self.scan_failure)
            print(f'Tuning: {self.scan_failure}; Stat={self._value("Stat")}, Auto={self._value("Auto")}, EnaAct={self._value("Enabled")}, EnaCmd-RB={self._value("EnableCmd")}, auto-enable timeout={self.auto_enable_timeout:g}', flush=True)

    def _move(self):
        self._ready()
        if self._mode()!=self.scan_mode or self._bit(3)!=0 or self._bit(5)!=0:
            raise RuntimeError('Axis busy or execute mode changed')
        self.sent=time.monotonic()
        self.start_pos=self._value('PosAct')
        self.idle_until=None
        self.target=self.points[self.index]
        self.native_move_absolute(self.target,self.scan_mode,self.bounds)
        self.status.setText(f'Step {self.index+1}/{len(self.points)} → {self.target:g}')

    def stop_scan(self):
        self.active=False
        self.paused=self.pause_requested=False
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText('Pause')
        self.stop_pending=True
        self.killing=False
        self.stop_sent=False
        self.stop_confirm_after=time.monotonic()+0.25
        try:
            self._put('StpCmd',1)
            self.stop_sent=True
            self.status.setText('STOP requested — waiting for stationary axis')
        except Exception as ex:
            self.status.setText(f'STOP failed: {ex}. Retry or use local controls.')

    def kill_scan(self):
        self.active=False
        self.paused=self.pause_requested=False
        self.pause_btn.setEnabled(False)
        self.pause_btn.setText('Pause')
        self.stop_pending=True
        self.killing=True
        self.stop_sent=False
        self.stop_confirm_after=time.monotonic()+0.25
        errors=self.native_kill()
        self.stop_sent=not errors
        self.status.setText('KILL requested — waiting for disable readback' if not errors else
                            'KILL write failure: '+'; '.join(errors)+' — verify drive locally')

    def reset_stats(self):
        self.stats_count=0
        self.stats_sum_sq=0.
        self.stats_min=self.stats_max=None
        self.stats_start=time.monotonic()

    def export_csv(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'Export buffered PV samples','tuning.csv','CSV (*.csv)')
        if not path:
            return
        try:
            # Independent CA streams: no fabricated synchronized samples.
            series = {key: samples for trend in (self.position, self.error)
                      for key, samples in trend.samples.items()}
            count = export_event_csv(path, series, pv_name=lambda key: self.base+'-'+key)
            self.status.setText(f'Exported {count} buffered samples')
        except Exception as ex:
            self.status.setText(f'Export failed: {ex}')

    def toggle_pause(self):
        if not self.active or self.stop_pending:
            return
        if self.paused or self.pause_requested:
            self.paused=self.pause_requested=False
            self.pause_btn.setText('Pause')
            self.status.setText('Scan resumed')
        else:
            self.pause_requested=True
            self.pause_btn.setText('Resume')
            self.status.setText('Pause requested — finish current move')

    def _needs_enable(self):
        return self._value('Enabled') == 0 or self._value('EnableCmd') == 0

    def _can_enable(self):
        return (self._value('ErrId') == 0 and self._bit(10) == 1 and
                all(self._bit(bit) == 0 for bit in (3, 5, 11, 16, 23)))

    def enable_drive(self):
        if self.active or self.stop_pending:
            return
        if not self._can_enable():
            self.status.setText('Cannot enable: axis error, busy, blocked or unavailable')
            return
        if not self.demo and QtWidgets.QMessageBox.question(self,'Enable drive',f'Enable {self.base}?',QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:
            return
        if not self._can_enable():
            self.status.setText('Enable cancelled — axis state changed')
            return
        try:
            self._put('EnaCmd',1)
            self.status.setText('Enable requested — see drive readback')
        except Exception as ex:
            self.status.setText(f'Enable failed: {ex}')

    def _unlock(self):
        self.jerk_edit.setEnabled(self.traj_type==1)
        for w in [*self.inputs.values(),self.relative,self.snake]:
            w.setEnabled(True)
        self.start_btn.setEnabled(True)

    def _tick(self):
        now=time.monotonic()
        mode=self._mode()
        self.mode_label.setText('Mode: '+('auto execute' if mode==1 else 'execute required' if mode==0 else 'unknown'))
        if self.demo:
            diff=self.sim_target-self.sim_pos
            step=min(abs(diff),max(0,now-self.last_tick)*getattr(self,'velo',1.))
            self.sim_pos+=math.copysign(step,diff) if diff else 0
            err=0.003*math.sin(now*12) if self.active else 0.
            for key,v in (('PosSet',self.sim_pos),('PosAct',self.sim_pos-err),('PosErr',err)):
                self._sample(key,now,v)
        self.last_tick=now
        if self.stats_count:
            rms=math.sqrt(self.stats_sum_sq/self.stats_count)
            peak=max(abs(self.stats_min),abs(self.stats_max))
            self.stats_label.setText(f'RMS: {rms:.5g}    Peak: {peak:.5g}    P–P: {self.stats_max-self.stats_min:.5g}    N: {self.stats_count}')
        else:
            self.stats_label.setText('RMS: —    Peak: —    P–P: —')
        lag=self.read_lag() if self.read_lag is not None else (0.01 if self.demo else None)
        self.error.limit=lag
        self.lag_label.setText('Lag: '+(f'±{lag:g}' if lag is not None else 'off / unknown'))
        self.enable_btn.setEnabled(not self.active and not self.stop_pending and self._needs_enable())
        self.position.update()
        self.error.update()
        for key,label in self.readouts.items():
            value=self._value(key)
            if key in ('Enabled','EnableCmd'):
                states=('Disabled','Enabled') if key=='Enabled' else ('Off','On')
                label.setText(states[int(value)] if value in (0,1) else 'Unknown')
            else:
                label.setText('—' if value is None else f'{value:.9g}')
        if self.stop_pending:
            if now>=self.stop_confirm_after and self.stop_sent and self._bit(3)==0 and self._bit(5)==0 and (not self.killing or (self._value('Enabled')==0 and self._value('EnableCmd')==0)):
                self.stop_pending=False
                self._unlock()
                self.status.setText(self.scan_failure or ('Drive disabled' if self.killing else 'Stopped'))
            return
        if not self.active:
            return
        try:
            self._ready()
            if self._mode()!=self.scan_mode:
                raise RuntimeError('Native execute mode changed')
            if self.idle_until is not None:
                if self._bit(3)!=0 or self._bit(5)!=0:
                    raise RuntimeError('Unexpected motion during dwell')
                if self.pause_requested or self.paused:
                    if not self.paused:
                        self.paused=True
                        self.status.setText(f'Paused at {self.target:g}')
                    return
                if now>=self.idle_until:
                    self.index,self.direction=next_step(self.index,self.direction,len(self.points),self.use_snake)
                    self._move()
                return
            # Verify completion using native busy/moving and trajectory position,
            # not motor-record DMOV. The setpoint must reach the intended point.
            pos=self._value('PosSet')
            reached=pos is not None and abs(pos-self.target)<=max(1e-8,abs(self.target)*1e-10)
            if self._bit(3)==0 and self._bit(5)==0 and reached and now-self.sent>=0.25 and (self.auto_enable_timeout<=0 or self._value('Enabled')==1 or self.busy_seen_at>=self.sent):
                self.idle_until=now+self.dwell
                self.status.setText(f'Dwell at {self.target:g}')
            elif now-self.sent>max(10,4*abs(self.target-self.start_pos)/self.velo+4*self.velo/min(self.acc,self.dec))+self.auto_enable_timeout:
                raise RuntimeError('Native move timed out or target was not accepted')
        except Exception as ex:
            self.stop_scan()
            self.scan_failure=f'Scan aborted: {ex}; STOP requested if connected'
            self.status.setText(self.scan_failure)
            print(f'Tuning: {self.scan_failure}', flush=True)

    def closeEvent(self,event):
        if self.active or self.stop_pending:
            if self.active:
                self.stop_scan()
            if self.stop_pending and not self.demo:
                event.ignore()
                self.status.setText('Waiting for STOP confirmation before closing')
                return
        self.timer.stop()
        for pv in self.pvs.values():
            try:
                pv.clear_callbacks()
                pv.disconnect()
            except Exception:
                pass
        self.pvs.clear()
        super().closeEvent(event)
