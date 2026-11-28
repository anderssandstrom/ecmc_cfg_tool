"""EL4732 buffer editor: bounded, one-shot, non-RT Channel Access writes."""
import math
import re
from threading import Event
from qt_compat import QtCore, QtWidgets
from ecmc_waveform_widget import WaveformValue
from ecmc_hardware_write import WriteSignals

def make_buffer(n,shape,offset=0.,amplitude=0.,cycles=1,custom=''):
    if not 1<=n<=100:
        raise ValueError('Buffer length must be 1–100 samples')
    if shape=='Custom':
        values=[float(x) for x in re.split(r'[\s,;]+',custom.strip()) if x]
        if len(values)!=n:
            raise ValueError(f'Enter exactly {n} voltage samples')
    elif shape=='Zero':
        values=[0.]*n
    elif shape=='Constant':
        values=[offset]*n
    elif shape in ('Sine','Square'):
        if not 1<=cycles<=n//2 or amplitude<0:
            raise ValueError('Invalid cycles per buffer or amplitude')
        if abs(offset)+amplitude>10:
            raise ValueError('Offset plus amplitude exceeds ±10 V')
        values=[offset+amplitude*(math.sin(2*math.pi*cycles*i/n) if shape=='Sine' else (1 if (i*cycles)%n<n/2 else -1)) for i in range(n)]
    else:
        raise ValueError('Unknown waveform')
    if any(not math.isfinite(v) or abs(v)>10 for v in values):
        raise ValueError('All voltages must be finite and within ±10 V')
    return [round(v*32767/10) for v in values]

def output_dialog(parent,n,current):
    dialog=QtWidgets.QDialog(parent)
    dialog.setWindowTitle('EL4732 output buffer')
    form=QtWidgets.QFormLayout(dialog)
    form.addRow(QtWidgets.QLabel(f'{n} samples per EtherCAT cycle · ±10 V'))
    shape=QtWidgets.QComboBox()
    shape.addItems(['Constant','Sine','Square','Custom','Zero'])
    offset=QtWidgets.QDoubleSpinBox()
    offset.setRange(-10,10);offset.setDecimals(4)
    amplitude=QtWidgets.QDoubleSpinBox()
    amplitude.setRange(0,10);amplitude.setDecimals(4)
    cycles=QtWidgets.QSpinBox()
    cycles.setRange(1,max(1,n//2))
    custom=QtWidgets.QPlainTextEdit()
    custom.setMaximumHeight(75)
    custom.setPlainText(', '.join(f'{v*10/32767:.6g}' for v in current))
    form.addRow('Shape',shape);form.addRow('Offset / constant (V)',offset)
    form.addRow('Amplitude (V)',amplitude);form.addRow('Cycles / buffer',cycles)
    form.addRow('Custom values (V)',custom)
    preview=WaveformValue()
    form.addRow(preview)
    status=QtWidgets.QLabel()
    status.setWordWrap(True)
    form.addRow(status)
    buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok|QtWidgets.QDialogButtonBox.Cancel)
    buttons.button(QtWidgets.QDialogButtonBox.Ok).setText('Apply output')
    form.addRow(buttons)
    result=[]
    def refresh(*_):
        pattern=shape.currentText()
        offset.setEnabled(pattern in ('Constant','Sine','Square'))
        amplitude.setEnabled(pattern in ('Sine','Square'))
        cycles.setEnabled(pattern in ('Sine','Square'))
        custom.setEnabled(pattern=='Custom')
        try:
            result[:]=make_buffer(n,pattern,offset.value(),amplitude.value(),cycles.value(),custom.toPlainText())
            preview.set_value([v*10/32767 for v in result])
            status.setText('Command voltage preview; not measured output.')
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(True)
        except ValueError as ex:
            result.clear();preview.set_value(None)
            status.setText(str(ex))
            buttons.button(QtWidgets.QDialogButtonBox.Ok).setEnabled(False)
    for widget in (offset,amplitude,cycles):
        widget.valueChanged.connect(refresh)
    shape.currentTextChanged.connect(refresh)
    custom.textChanged.connect(refresh)
    buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
    shape.setCurrentText('Custom')
    refresh()
    ok=dialog.exec() if hasattr(dialog,'exec') else dialog.exec_()
    return list(result) if ok and result else None

class BufferTask(QtCore.QRunnable):
    def __init__(self,pv,values,timeout):
        super().__init__()
        self.pv,self.values,self.timeout=pv,values,timeout
        self.cancelled=Event()
        self.signals=WriteSignals()
    def run(self):
        handles=[]
        error=''
        try:
            import epics
            def connect(name):
                pv=epics.PV(name,auto_monitor=False)
                handles.append(pv)
                if not pv.wait_for_connection(timeout=self.timeout):
                    raise RuntimeError('Disconnected: '+name)
                return pv
            def read(pv,as_string=False):
                value=pv.get(use_monitor=False,as_string=as_string,timeout=self.timeout)
                if value is None or pv.severity==3:
                    raise RuntimeError('Invalid PV: '+pv.pvname)
                return value
            n=int(read(connect(self.pv+'.NELM')))
            if not 1<=n<=100 or n!=len(self.values):
                raise RuntimeError('Configured buffer length changed')
            if str(read(connect(self.pv+'.FTVL'),True)).upper()!='SHORT':
                raise RuntimeError('Expected signed 16-bit waveform')
            if str(read(connect(self.pv+'.DTYP'),True))!='asynInt16ArrayOut':
                raise RuntimeError('Expected asynInt16ArrayOut')
            pv=connect(self.pv)
            if not pv.write_access or pv.count!=n:
                raise RuntimeError('Output write denied or array size mismatch')
            if any(type(x) is not int or not -32767<=x<=32767 for x in self.values):
                raise RuntimeError('Invalid signed 16-bit sample')
            if self.cancelled.is_set():
                raise RuntimeError('Output cancelled before write')
            if (pv.put(self.values,wait=True,timeout=self.timeout) or 0)<=0:
                raise RuntimeError('Output put not confirmed')
            actual=read(pv)
            if n==1 and not hasattr(actual,'__len__'):
                actual=[actual]
            if list(actual)!=self.values:
                raise RuntimeError('Output command readback differs')
        except Exception as ex:
            error=str(ex)
        finally:
            for pv in handles:
                try: pv.disconnect()
                except Exception: pass
            self.signals.finished.emit(error)
