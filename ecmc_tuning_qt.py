"""Modeless controller tuning window, using native ECMC moves and CA monitors."""
import math
import time
from collections import deque
from qt_compat import QtCore, QtGui, QtWidgets


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
        self.samples = {key: deque(maxlen=40000) for key, _, _ in series}
        self.history = 15
        self.setMinimumHeight(145)

    def append(self, key, stamp, value):
        self.samples[key].append((stamp,value))

    def clear(self):
        for q in self.samples.values():
            q.clear()
        self.update()

    def paintEvent(self,event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.fillRect(self.rect(),QtGui.QColor('#f7f9fb'))
        r = QtCore.QRectF(76,24,self.width()-90,self.height()-49)
        if r.width() < 10 or r.height() < 10:
            return
        now = time.monotonic()
        start = now-self.history
        data = {}
        for key,q in self.samples.items():
            while len(q)>1 and q[1][0]<start:
                q.popleft()
            data[key] = [(t,v) for t,v in q if t>=start and v is not None]
        values = [v for a in data.values() for _,v in a]
        p.setPen(QtGui.QColor('#465565'))
        x = 78
        for key,title,color in self.series:
            p.setPen(QtGui.QColor(color))
            p.drawText(int(x),16,title)
            x += 125
        if not values:
            p.drawText(r,QtCore.Qt.AlignCenter,'Waiting for PV samples')
            return
        lo,hi = min(values),max(values)
        if len(self.series)==1:
            lo,hi = min(0,lo),max(0,hi)
        pad = (hi-lo)*.05 or max(abs(hi)*1e-6,1e-9)
        lo,hi = lo-pad,hi+pad
        for i in range(5):
            y = r.top()+r.height()*i/4
            x = r.left()+r.width()*i/4
            p.setPen(QtGui.QColor('#dce3e9'))
            p.drawLine(QtCore.QPointF(r.left(),y),QtCore.QPointF(r.right(),y))
            p.drawLine(QtCore.QPointF(x,r.top()),QtCore.QPointF(x,r.bottom()))
            p.setPen(QtGui.QColor('#465565'))
            p.drawText(QtCore.QRectF(0,y-9,70,18),QtCore.Qt.AlignRight|QtCore.Qt.AlignVCenter,f'{hi-(hi-lo)*i/4:.5g}')
            p.drawText(QtCore.QRectF(x-28,r.bottom()+3,56,18),QtCore.Qt.AlignCenter,f'{-self.history*(1-i/4):g}s')
        p.save()
        p.setClipRect(r)
        for key,_,color in self.series:
            path = QtGui.QPainterPath()
            started = False
            for stamp,v in self.samples[key]:
                if v is None:
                    started=False
                    continue
                if stamp<start:
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


class TuningWindow(QtWidgets.QDialog):
    sample = QtCore.Signal(str,float,object) if hasattr(QtCore,'Signal') else QtCore.pyqtSignal(str,float,object)

    def __init__(self,parent,client,base,demo=False,read_config=None):
        super().__init__(parent)
        self.setWindowTitle('Controller tuning — '+base+(' [DEMO]' if demo else ''))
        self.setModal(False)
        self.resize(760,510)
        self.client,self.base,self.demo=client,base,demo
        self.read_config=read_config
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
        layout.addLayout(controls)
        self.status=QtWidgets.QLabel('Idle — enter scan endpoints explicitly')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.position=Trend([('PosSet','PosSet','#287bc0'),('PosAct','PosAct','#d06d22')])
        self.error=Trend([('PosErr','PosErr','#a64070')])
        layout.addWidget(self.position,1)
        layout.addWidget(self.error,1)
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
        self.readout=QtWidgets.QLabel('Waiting for PVs')
        row.addWidget(self.readout,1)
        layout.addLayout(row)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._tick)
        if demo:
            self.inputs['a'].setText('-1')
            self.inputs['b'].setText('1')
            self.inputs['velo'].setText('1')
            self.inputs['acc'].setText('5')
            self.inputs['dec'].setText('5')
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
        for key,suffix in {'PosSet':'-PosSet','PosAct':'-PosAct','PosErr':'-PosErr',
                'ErrId':'-ErrId','Enabled':'-EnaAct','EnableCmd':'-EnaCmd-RB',
                'Stat':'-Stat','Auto':'-AutoExecuteOnTargetPos-RB',
                'VELO':'-TgtVelCmd','ACC':'-AccCmd','DEC':'-DecCmd',
                'Command':'-MtnCmd','CmdData':'-MtnCmdData','Target':'-TgtPosCmd',
                'Execute':'-ExeCmd','Stop':'-StpCmd','EnableWrite':'-EnaCmd'}.items():
            def cb(pvname=None,value=None,severity=None,_key=key,**kw):
                try:
                    v=float(value)
                    if not math.isfinite(v) or severity==3:
                        v=None
                except (TypeError,ValueError):
                    v=None
                self.sample.emit(_key,time.monotonic(),v)
            try:
                self.pvs[key]=ep.PV(self.base+suffix,auto_monitor=True,callback=cb)
            except Exception:
                pass

    def _sample(self,key,stamp,value):
        self.latest[key]=value
        if key=='Stat' and value is not None and int(value)&(1<<3):
            self.busy_seen_at=stamp
        if key in self.position.samples:
            self.position.append(key,stamp,value)
        if key in self.error.samples:
            self.error.append(key,stamp,value)
        if key in ('VELO','ACC','DEC') and value is not None:
            edit=self.inputs[key.lower()]
            if not edit.text().strip():
                edit.setText(f'{value:g}')

    def _value(self,key):
        if self.demo:
            busy=int(abs(self.sim_target-self.sim_pos)>1e-9)
            stat=(self.sim_enabled<<1)|(busy<<3)|(int(not busy)<<4)|(1<<6)|(1<<7)|(1<<10)|(self.sim_auto<<24)
            return {'Stat':stat,'Auto':self.sim_auto,'PosAct':self.sim_pos,
                    'PosSet':self.sim_pos,'Enabled':self.sim_enabled,'EnableCmd':self.sim_enabled,
                    'ErrId':0,'Command':3,'CmdData':0,'Target':self.sim_target}.get(key)
        pv=self.pvs.get(key)
        if pv is None or not pv.connected:
            return None
        return self.latest.get(key)

    def _bit(self,bit):
        stat=self._value('Stat')
        return None if stat is None else (int(stat)>>bit)&1

    def _mode(self):
        # Dedicated PV indicates this IOC supports the status-bit contract.
        # Do not interpret a reserved bit on older firmware as explicit mode.
        auto=self._value('Auto')
        bit=self._bit(24)
        return int(auto) if auto in (0,1) and bit==auto else None

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
            elif field=='EnaCmd' and value==0:
                self.sim_enabled=0
                self.sim_target=self.sim_pos
            return
        # Wait for each write so command/data/target/execute are ordered.
        # Use a dedicated CA PV and a bounded timeout; STOP/KILL are independent.
        keys={'TgtPosCmd':'Target','ExeCmd':'Execute','StpCmd':'Stop',
              'EnaCmd':'EnableWrite','MtnCmd':'Command','MtnCmdData':'CmdData',
              'TgtVelCmd':'VELO','AccCmd':'ACC','DecCmd':'DEC'}
        pv=self.pvs.get(keys[field])
        if pv is None or not pv.connected or not pv.write_access:
            raise RuntimeError(field+' unavailable or not writable')
        result=pv.put(value,wait=True,timeout=0.7)
        if result!=1:
            raise RuntimeError(field+' write not confirmed')

    def _ready(self):
        if self._value('ErrId')!=0 or self._value('Enabled')!=1 or self._value('EnableCmd')!=1:
            raise RuntimeError('Axis error, disabled, or status unavailable')
        if self._bit(10)!=1 or self._bit(11)!=0 or self._bit(23)!=0:
            raise RuntimeError('Axis not in RT, external trajectory, or blocked')
        if any(self._bit(k)!=1 for k in (6,7)) or any(self._bit(k)!=0 for k in (17,18)):
            raise RuntimeError('Limit/interlock active or status unavailable')
        if self._mode() is None:
            raise RuntimeError('Native execute mode unknown; scan blocked')
        if self._bit(3) is None or self._value('PosAct') is None:
            raise RuntimeError('Native axis status unavailable')

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
        try:
            self._ready()
            if self._bit(3)!=0 or self._bit(5)!=0:
                raise RuntimeError('Axis must be stationary before starting')
            a,b,dwell,velo,acc,dec=(float(self.inputs[k].text()) for k in ('a','b','dwell','velo','acc','dec'))
            steps=int(self.inputs['steps'].text())
            if not all(math.isfinite(v) for v in (a,b,dwell,velo,acc,dec)) or dwell<0 or min(velo,acc,dec)<=0:
                raise ValueError('Invalid scan parameters')
            if self.relative.isChecked():
                actual=self._value('PosAct')
                a,b=a+actual,b+actual
            points=scan_points(a,b,steps)
            lo,hi,vmax=self._scan_limits()
            if min(a,b)<lo or max(a,b)>hi:
                raise RuntimeError('Scan outside native ECMC soft limits')
            if vmax is not None and (vmax<=0 or velo>vmax):
                raise RuntimeError('Velocity exceeds native monitoring limit')
            mode=self._mode()
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
            self._put('MtnCmd',3)
            self._put('MtnCmdData',0)
            self._put('TgtVelCmd',velo)
            self._put('AccCmd',acc)
            self._put('DecCmd',dec)
            self.active=True
            self.start_btn.setEnabled(False)
            for w in [*self.inputs.values(),self.relative,self.snake]:
                w.setEnabled(False)
            self._move()
        except Exception as ex:
            if self.active:
                self.stop_scan()
            self.status.setText(str(ex))

    def _move(self):
        self._ready()
        if self._mode()!=self.scan_mode or self._bit(3)!=0 or self._bit(5)!=0:
            raise RuntimeError('Axis busy or execute mode changed')
        self.sent=time.monotonic()
        self.start_pos=self._value('PosAct')
        self.idle_until=None
        self.target=self.points[self.index]
        if not self.bounds[0]<=self.target<=self.bounds[1]:
            raise RuntimeError('Target outside native limits')
        self._put('MtnCmd',3)
        self._put('MtnCmdData',0)
        self._put('TgtPosCmd',self.target)
        if self.scan_mode==0:
            if self._mode()!=0:
                raise RuntimeError('Execute mode changed after target write')
            self._put('ExeCmd',1)
        self.status.setText(f'Step {self.index+1}/{len(self.points)} → {self.target:g}')

    def stop_scan(self):
        self.active=False
        self.stop_pending=True
        self.killing=False
        try:
            self._put('StpCmd',1)
            self.status.setText('STOP requested — waiting for stationary axis')
        except Exception as ex:
            self.status.setText(f'STOP failed: {ex}. Retry or use local controls.')

    def kill_scan(self):
        self.active=False
        self.stop_pending=True
        self.killing=True
        errors=[]
        for field,value in (('StpCmd',1),('EnaCmd',0)):
            try:
                self._put(field,value)
            except Exception as ex:
                errors.append(str(ex))
        self.status.setText('KILL requested — waiting for disable readback' if not errors else
                            'KILL write failure: '+'; '.join(errors)+' — verify drive locally')

    def _unlock(self):
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
        self.position.update()
        self.error.update()
        self.readout.setText('   '.join(k+': '+('—' if self.latest.get(k) is None else f'{self.latest[k]:.6g}') for k in ('PosSet','PosAct','PosErr')))
        if self.stop_pending:
            if self._bit(3)==0 and self._bit(5)==0 and (not self.killing or (self._value('Enabled')==0 and self._value('EnableCmd')==0)):
                self.stop_pending=False
                self._unlock()
                self.status.setText('Drive disabled' if self.killing else 'Stopped')
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
                if now>=self.idle_until:
                    self.index,self.direction=next_step(self.index,self.direction,len(self.points),self.use_snake)
                    self._move()
                return
            # Verify completion using native busy/moving and trajectory position,
            # not motor-record DMOV. The setpoint must reach the intended point.
            pos=self._value('PosSet')
            reached=pos is not None and abs(pos-self.target)<=max(1e-8,abs(self.target)*1e-10)
            if self._bit(3)==0 and self._bit(5)==0 and reached and now-self.sent>=0.25:
                self.idle_until=now+self.dwell
                self.status.setText(f'Dwell at {self.target:g}')
            elif now-self.sent>max(10,4*abs(self.target-self.start_pos)/self.velo+4*self.velo/min(self.acc,self.dec)):
                raise RuntimeError('Native move timed out or target was not accepted')
        except Exception as ex:
            self.stop_scan()
            self.status.setText(f'Scan aborted: {ex}; STOP requested if connected')

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
