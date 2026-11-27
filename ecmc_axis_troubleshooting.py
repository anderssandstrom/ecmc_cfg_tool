"""Compact, read-only side-by-side native axis troubleshooting."""
import math
from qt_compat import QtCore, QtWidgets
from ecmc_axis_diagnostics import axis_alarm_summary, axis_status_value, format_axis_value
from ecmc_hardware_widgets import AlarmSummary
from ecmc_axis_overview import AxisPositionBar

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
         '-CfgDLLM','-CfgDHLM','-SftLimFwdEna-RB','-SftLimBwdEna-RB')
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
        layout=QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4,4,4,4)
        layout.setSpacing(4)
        bar=QtWidgets.QHBoxLayout()
        choose=QtWidgets.QPushButton('Select axes…')
        choose.clicked.connect(self._choose)
        bar.addWidget(choose)
        self.status=QtWidgets.QLabel('Read-only')
        bar.addWidget(self.status,1)
        layout.addLayout(bar)
        self.scroll=QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
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
        root=QtWidgets.QWidget()
        row=QtWidgets.QHBoxLayout(root)
        row.setContentsMargins(2,2,2,2)
        row.setSpacing(4)
        for axis in selected_axes(axes)[:MAX_AXES]:
            card=QtWidgets.QGroupBox(f"{axis.get('id','')} · {axis.get('name',axis['motor'])}")
            card.setToolTip(axis['motor'])
            card.setMinimumWidth(215)
            card.setMaximumWidth(300)
            col=QtWidgets.QVBoxLayout(card)
            col.setContentsMargins(5,5,5,5)
            col.setSpacing(2)
            alarm=AlarmSummary()
            col.addWidget(alarm)
            grid=QtWidgets.QGridLayout()
            grid.setSpacing(3)
            labels={}
            for i,(caption,suffix) in enumerate(FIELDS):
                label=QtWidgets.QLabel('—')
                label.setAlignment(QtCore.Qt.AlignRight|QtCore.Qt.AlignVCenter)
                label.setToolTip(axis['motor']+suffix)
                if i<2:
                    font=label.font()
                    font.setBold(True)
                    font.setPointSize(max(13,font.pointSize()+2))
                    label.setFont(font)
                grid.addWidget(QtWidgets.QLabel(caption),i,0)
                grid.addWidget(label,i,1)
                labels[suffix]=label
            col.addLayout(grid)
            track=AxisPositionBar(True)
            col.addWidget(track)
            actions=QtWidgets.QHBoxLayout()
            if self.on_axis:
                button=QtWidgets.QPushButton('Expert')
                button.clicked.connect(lambda checked=False,a=axis:self.on_axis(a))
                actions.addWidget(button)
            if self.on_plot:
                button=QtWidgets.QPushButton('Trend')
                button.clicked.connect(lambda checked=False,a=axis:self._trend(a))
                actions.addWidget(button)
            col.addLayout(actions)
            col.addStretch()
            row.addWidget(card,0,QtCore.Qt.AlignTop)
            self.cards.append((axis,labels,alarm,track))
        row.addStretch()
        self.scroll.setWidget(root)
        self.status.setText(f'{len(self.cards)} axes · read-only · live at 2.5 Hz' if self.cards else 'Select axes to begin')
        if self.isVisible():
            self._start()

    def _demo_value(self, name):
        for i,(axis,_,_,_) in enumerate(self.cards):
            base=axis['motor']
            if name.startswith(base+'-'):
                suffix=name[len(base):]
                pos=10+i*5
                values={'-PosAct':pos,'-TgtPosCmd':pos,'-PosErr':0,'-VelAct':0,
                        '-TgtPosCmd.EGU':'mm','-EnaAct':1,'-Homed':1,'-AtTarget':1,'-InRT':1,
                        '-LimFwd':1,'-LimBwd':1,'-CfgDLLM':-50,'-CfgDHLM':50,
                        '-SftLimFwdEna-RB':1,'-SftLimBwdEna-RB':1,'-MsgTxt':'','-IlockTxt':''}
                return values.get(suffix,0)
        return None

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

    def _tick(self):
        for axis,labels,alarm,track in self.cards:
            samples={s:self._read(axis['motor']+s) for s in SUFFIXES}
            for suffix,label in labels.items():
                text,color=display_sample(suffix,samples[suffix])
                if label.text()!=text:
                    label.setText(text)
                style='color: '+color+';' if color else ''
                if label.styleSheet()!=style:
                    label.setStyleSheet(style)
            alarm.set_summary(*axis_alarm_summary(samples))
            track.set_values(samples)

    def _stop(self):
        self.timer.stop()
        for lease in self.leases.values():
            try: lease.close()
            except Exception: pass
        self.leases.clear()
        for _,labels,alarm,track in self.cards:
            for label in labels.values():
                label.setText('—')
            alarm.set_summary('unknown','Monitoring paused')
            track.set_values({})

    def showEvent(self,event):
        super().showEvent(event)
        self._start()

    def hideEvent(self,event):
        self._stop()
        super().hideEvent(event)

    def closeEvent(self,event):
        self.closed=True
        self._stop()
        super().closeEvent(event)
