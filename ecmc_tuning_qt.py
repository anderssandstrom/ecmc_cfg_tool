"""Modeless controller tuning window, using motor-record moves and CA monitors."""
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

    def __init__(self,parent,client,base,demo=False):
        super().__init__(parent)
        self.setWindowTitle('Controller tuning — '+base+(' [DEMO]' if demo else ''))
        self.setModal(False)
        self.resize(760,510)
        self.client,self.base,self.demo=client,base,demo
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
            ('dwell','Dwell [s]','0.2'),('velo','VELO',''),('accl','ACCL [s]',''))):
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
        for i,w in enumerate((self.relative,self.snake,self.start_btn,self.stop_btn)):
            controls.addWidget(w,2,i)
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
            self.inputs['accl'].setText('0.2')
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
                'ErrId':'-ErrId','Enabled':'-EnaAct','DMOV':'.DMOV','RBV':'.RBV',
                'LLM':'.LLM','HLM':'.HLM','VELO':'.VELO','ACCL':'.ACCL',
                'VMAX':'.VMAX','SPMG':'.SPMG','LVIO':'.LVIO','HLS':'.HLS',
                'LLS':'.LLS','MOVN':'.MOVN'}.items():
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
        if key in self.position.samples:
            self.position.append(key,stamp,value)
        if key in self.error.samples:
            self.error.append(key,stamp,value)
        if key in ('VELO','ACCL') and value is not None:
            edit=self.inputs[key.lower()]
            if not edit.text().strip():
                edit.setText(f'{value:g}')

    def _value(self,key):
        if self.demo:
            return {'DMOV':int(abs(self.sim_target-self.sim_pos)<1e-9),'RBV':self.sim_pos,
                    'LLM':-100,'HLM':100,'VMAX':10,'ErrId':0,'Enabled':1,
                    'SPMG':3,'LVIO':0,'HLS':0,'LLS':0,'MOVN':int(self.sim_target!=self.sim_pos)}.get(key)
        pv=self.pvs.get(key)
        if pv is None or not pv.connected:
            return None
        return self.latest.get(key)

    def _put(self,field,value):
        if self.demo:
            if field=='VAL':
                self.sim_target=value
            elif field=='STOP':
                self.sim_target=self.sim_pos
            return
        self.client.put(self.base+'.'+field,value,wait=False)

    def _ready(self):
        if self._value('ErrId')!=0 or self._value('Enabled')!=1:
            raise RuntimeError('Axis error, disabled, or status unavailable')
        if self._value('SPMG')!=3:
            raise RuntimeError('Motor record must be in Go mode')
        if any(self._value(k)!=0 for k in ('LVIO','HLS','LLS')):
            raise RuntimeError('Limit active or limit status unavailable')
        if self._value('DMOV') is None or self._value('RBV') is None:
            raise RuntimeError('Motor record status unavailable')

    def start_scan(self):
        if self.active or self.stop_pending:
            return
        try:
            self._ready()
            if self._value('DMOV')!=1 or self._value('MOVN')!=0:
                raise RuntimeError('Axis must be stationary before starting')
            a,b,dwell,velo,accl=(float(self.inputs[k].text()) for k in ('a','b','dwell','velo','accl'))
            steps=int(self.inputs['steps'].text())
            if not all(math.isfinite(v) for v in (a,b,dwell,velo,accl)) or dwell<0 or velo<=0 or accl<=0:
                raise ValueError('Invalid scan parameters')
            if self.relative.isChecked():
                rbv=self._value('RBV')
                a,b=a+rbv,b+rbv
            points=scan_points(a,b,steps)
            lo,hi,vmax=(self._value(k) for k in ('LLM','HLM','VMAX'))
            if lo is None or hi is None or not lo<hi or min(a,b)<lo or max(a,b)>hi:
                raise RuntimeError('Scan outside known motor-record soft limits')
            if vmax is None or (vmax>0 and velo>vmax):
                raise RuntimeError('Velocity exceeds VMAX or VMAX unavailable')
            if not self.demo:
                answer=QtWidgets.QMessageBox.question(self,'Confirm repetitive motion',
                    f'{self.base}: repeat {a:g} ↔ {b:g} at VELO={velo:g}, ACCL={accl:g}?\nSTOP or close the popup to stop. Motion parameters remain changed.',
                    QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)
                if answer!=QtWidgets.QMessageBox.Yes:
                    return
                self._ready()
                if self._value('DMOV')!=1 or self._value('MOVN')!=0:
                    raise RuntimeError('Axis is no longer stationary')
            self.points,self.index,self.direction=points,0,1
            self.dwell,self.velo,self.accl,self.use_snake=dwell,velo,accl,self.snake.isChecked()
            self._put('VELO',velo)
            self._put('ACCL',accl)
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
        self.sent=time.monotonic()
        self.seen_busy=False
        self.idle_until=None
        self.target=self.points[self.index]
        self._put('VAL',self.target)
        self.status.setText(f'Step {self.index+1}/{len(self.points)} → {self.target:g}')

    def stop_scan(self):
        was_active=self.active or self.stop_pending
        self.active=False
        self.stop_pending=False
        if was_active:
            try:
                self._put('STOP',1)
                self.stop_pending=True
                self.status.setText('STOP requested — waiting for stationary axis')
            except Exception as ex:
                self.stop_pending=True
                self.status.setText(f'STOP failed: {ex}. Retry STOP or use local controls.')
        if not self.stop_pending:
            self._unlock()

    def _unlock(self):
        for w in [*self.inputs.values(),self.relative,self.snake]:
            w.setEnabled(True)
        self.start_btn.setEnabled(True)

    def _tick(self):
        now=time.monotonic()
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
            if self._value('DMOV')==1 and self._value('MOVN')==0:
                self.stop_pending=False
                self._unlock()
                self.status.setText('Stopped')
            return
        if not self.active:
            return
        try:
            self._ready()
            dmov=self._value('DMOV')
            if dmov==0:
                self.seen_busy=True
            if self.idle_until is not None:
                if now>=self.idle_until:
                    self.index,self.direction=next_step(self.index,self.direction,len(self.points),self.use_snake)
                    self._move()
                return
            # Require a fresh busy/done transition, or a settled readback at
            # the commanded target for a no-op move. Never advance on old DMOV.
            rbv=self._value('RBV')
            reached=abs(rbv-self.target)<=max(1e-8,abs(self.target)*1e-10)
            if dmov==1 and self._value('MOVN')==0 and (self.seen_busy or (now-self.sent>=0.5 and reached)):
                self.idle_until=now+self.dwell
                self.status.setText(f'Dwell at {self.target:g}')
            elif now-self.sent>max(10,4*abs(self.target-rbv)/self.velo+4*self.accl) and not self.seen_busy:
                raise RuntimeError('Move did not start or target was not accepted')
        except Exception as ex:
            self.stop_scan()
            self.status.setText(f'Scan aborted: {ex}; STOP requested if connected')

    def closeEvent(self,event):
        if self.active or self.stop_pending:
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
