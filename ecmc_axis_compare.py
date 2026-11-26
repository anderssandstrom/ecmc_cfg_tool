import weakref
"""Read-only comparison of exposed native axis configuration PVs."""
import math
from concurrent.futures import ThreadPoolExecutor
from qt_compat import QtCore, QtWidgets

PARAMETERS = (
    ('Lower dial limit','-CfgDLLM'),('Upper dial limit','-CfgDHLM'),
    ('Forward soft limit enabled','-SftLimFwdEna-RB'),('Backward soft limit enabled','-SftLimBwdEna-RB'),
    ('Target velocity','-TgtVelCmd'),('Acceleration','-AccCmd'),('Deceleration','-DecCmd'),
    ('Tweak distance','-TwkDstCmd'),('Proportional gain','-Ctrl-Kp'),
    ('Integral gain','-Ctrl-Ki'),('Derivative gain','-Ctrl-Kd'),
    ('Encoder source','-EncSrcTyp-RB'),('Trajectory source','-TrjSrcTyp-RB'),
    ('Trajectory type','-TrajType'),('Encoder selection','-SetEncIdCmd-RB'),
    ('Encoder mode','-EncMode-RB'),('Drive type','-DrvType'),('Drive mode','-DrvMode'),
    ('Units','-TgtPosCmd.EGU'))


def compare_values(a,b,atol=0.0):
    if a is None or b is None: return 'Unavailable',''
    try:
        x,y=float(a),float(b)
        if not math.isfinite(x) or not math.isfinite(y): return 'Unavailable',''
        return ('Equal' if abs(y-x)<=atol else 'Changed'),format(y-x,'.12g')
    except (ValueError,TypeError):
        return ('Equal' if str(a)==str(b) else 'Changed'),''


def read_parameters(left,right):
    import epics
    names=[base+suffix for base in (left,right) for _,suffix in PARAMETERS]
    # Batch connection and per-PV reads have finite timeouts, off the UI thread.
    values=epics.caget_many(names,timeout=2.0,connection_timeout=2.0)
    return values[:len(PARAMETERS)],values[len(PARAMETERS):]


class AxisCompare(QtWidgets.QWidget):
    def __init__(self,demo=False):
        super().__init__()
        self.demo=demo
        self.future=None
        self.executor=ThreadPoolExecutor(max_workers=1)
        executor=self.executor
        weakref.finalize(self, executor.shutdown, wait=False, cancel_futures=True)
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel('Compare exposed native axis parameters (dial coordinates). Enter full axis PV bases. Values are sequential reads, not an atomic snapshot.'+(' · DEMO' if demo else ''))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.left,self.right=QtWidgets.QLineEdit(),QtWidgets.QLineEdit()
        for label,edit in (('Baseline axis PV base',self.left),('Comparison axis PV base',self.right)):
            layout.addWidget(QtWidgets.QLabel(label));layout.addWidget(edit)
        self.tolerance=QtWidgets.QDoubleSpinBox();self.tolerance.setDecimals(9);self.tolerance.setRange(0,1e9)
        layout.addWidget(QtWidgets.QLabel('Absolute numeric tolerance'));layout.addWidget(self.tolerance)
        self.button=QtWidgets.QPushButton('Read and compare');self.button.clicked.connect(self._read);layout.addWidget(self.button)
        self.status=QtWidgets.QLabel('No values read');layout.addWidget(self.status)
        self.table=QtWidgets.QTableWidget(0,5);self.table.setHorizontalHeaderLabels(('Parameter / suffix','Baseline','Comparison','Delta','Result'))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QtWidgets.QHeaderView.Stretch);layout.addWidget(self.table)
        self.timer=QtCore.QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self._poll)
    def _read(self):
        bases=[e.text().strip() for e in (self.left,self.right)]
        if not all(b and not any(c.isspace() for c in b) for b in bases):
            self.status.setText('Enter both full axis PV bases');return
        if self.future is not None:return
        self.button.setEnabled(False);self.table.setRowCount(0)
        self.status.setText('Reading '+ ' / '.join(bases))
        self.used_bases=bases;self.used_tolerance=self.tolerance.value()
        self.future=self.executor.submit((lambda *_:([None]*len(PARAMETERS),[None]*len(PARAMETERS))) if self.demo else read_parameters,*bases)
        self.timer.start()
    def _poll(self):
        if not self.future.done():return
        self.timer.stop()
        try:
            a,b=self.future.result();self.table.setRowCount(len(PARAMETERS))
            for i,(label,suffix) in enumerate(PARAMETERS):
                result,delta=compare_values(a[i],b[i],self.used_tolerance)
                for j,value in enumerate((label+' / '+suffix,a[i],b[i],delta,result)):
                    self.table.setItem(i,j,QtWidgets.QTableWidgetItem('—' if value is None else str(value)))
            self.status.setText('Read complete: '+' / '.join(self.used_bases)+(' · DEMO, values unavailable' if self.demo else ''))
        except Exception as ex:self.status.setText(str(ex))
        finally:self.future=None;self.button.setEnabled(True)
