"""Read-only, bounded inspection of existing hardware waveform buffers."""
import math
from qt_compat import QtCore, QtGui, QtWidgets

MAX_SAMPLES=65536

def capture_samples(value):
    if value is None or isinstance(value,(str,bytes)):
        return ()
    try:
        n=len(value)
    except TypeError:
        value=(value,)
        n=1
    if not 0<n<=MAX_SAMPLES:
        return ()
    try:
        return tuple(float(v) for v in value)
    except (TypeError,ValueError,OverflowError):
        return ()

def sample_statistics(samples):
    finite=[v for v in samples if math.isfinite(v)]
    if not finite:
        return None
    n=len(finite)
    mean=math.fsum(v/n for v in finite)
    rms=math.hypot(*finite)/math.sqrt(n)
    std=math.hypot(*(v-mean for v in finite))/math.sqrt(n)
    return min(finite),max(finite),mean,rms,std

class SampleModel(QtCore.QAbstractTableModel):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.samples=()
    def set_samples(self,samples):
        self.beginResetModel()
        self.samples=samples
        self.endResetModel()
    def rowCount(self,parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.samples)
    def columnCount(self,parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else 2
    def headerData(self,section,orientation,role=QtCore.Qt.DisplayRole):
        if role==QtCore.Qt.DisplayRole and orientation==QtCore.Qt.Horizontal:
            return ('Index','Raw value')[section]
        return None
    def data(self,index,role=QtCore.Qt.DisplayRole):
        if not index.isValid() or role!=QtCore.Qt.DisplayRole:
            return None
        return str(index.row()) if index.column()==0 else f'{self.samples[index.row()]:.10g}'

class SamplePlot(QtWidgets.QWidget):
    def __init__(self,selected,parent=None):
        super().__init__(parent)
        self.samples=()
        self.start=0
        self.end=0
        self.cursor=None
        self.selected=selected
        self.setMinimumHeight(220)
    def set_data(self,samples,start,end,cursor=None):
        self.samples,self.start,self.end,self.cursor=samples,start,end,cursor
        self.update()
    def paintEvent(self,event):
        p=QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect=QtCore.QRectF(65,12,max(1,self.width()-80),max(1,self.height()-45))
        p.setPen(self.palette().color(QtGui.QPalette.Text))
        p.drawRect(rect)
        if not self.samples:
            p.drawText(rect,QtCore.Qt.AlignCenter,'No available waveform')
            p.end();return
        vals=self.samples[self.start:self.end+1]
        finite=[v for v in vals if math.isfinite(v)]
        if not finite:
            p.end();return
        lo,hi=min(finite),max(finite)
        if lo==hi:
            lo-=1;hi+=1
        def y(v): return rect.bottom()-(v-lo)/(hi-lo)*rect.height()
        def x(i): return rect.left()+i/max(1,len(vals)-1)*rect.width()
        for k in range(5):
            value=lo+(hi-lo)*k/4
            yy=y(value)
            p.setPen(QtGui.QPen(QtGui.QColor('#89939e'),.5))
            p.drawLine(QtCore.QPointF(rect.left(),yy),QtCore.QPointF(rect.right(),yy))
            p.setPen(self.palette().color(QtGui.QPalette.Text))
            p.drawText(QtCore.QRectF(0,yy-8,62,16),QtCore.Qt.AlignRight,f'{value:.5g}')
        # Envelope per pixel preserves narrow peaks even at large sample counts.
        p.setPen(QtGui.QPen(QtGui.QColor('#287bc0'),1.3))
        stride=max(1,math.ceil(len(vals)/max(1,int(rect.width()))))
        previous=None
        for i in range(0,len(vals),stride):
            bucket=[v for v in vals[i:i+stride] if math.isfinite(v)]
            if not bucket:
                previous=None
                continue
            xx=x(i)
            p.drawLine(QtCore.QPointF(xx,y(min(bucket))),QtCore.QPointF(xx,y(max(bucket))))
            point=QtCore.QPointF(xx,y(bucket[0]))
            if previous is not None: p.drawLine(previous,point)
            elif stride==1: p.drawEllipse(point,1.5,1.5)
            previous=QtCore.QPointF(xx,y(bucket[-1]))
        if self.cursor is not None and self.start<=self.cursor<=self.end:
            xx=x(self.cursor-self.start)
            p.setPen(QtGui.QPen(QtGui.QColor('#8661c5'),1.5))
            p.drawLine(QtCore.QPointF(xx,rect.top()),QtCore.QPointF(xx,rect.bottom()))
        p.setPen(self.palette().color(QtGui.QPalette.Text))
        p.drawText(QtCore.QRectF(rect.left(),rect.bottom()+3,100,18),QtCore.Qt.AlignLeft,str(self.start))
        p.drawText(QtCore.QRectF(rect.right()-100,rect.bottom()+3,100,18),QtCore.Qt.AlignRight,str(self.end))
        p.drawText(QtCore.QRectF(rect.left(),rect.bottom()+17,rect.width(),18),QtCore.Qt.AlignCenter,'Sample index')
        p.end()
    def mouseReleaseEvent(self,event):
        if event.button()==QtCore.Qt.LeftButton and self.samples:
            x=max(0.,min(1.,(event.pos().x()-65)/max(1,self.width()-80)))
            self.selected(self.start+round(x*(self.end-self.start)))
        super().mouseReleaseEvent(event)

class WaveformInspector(QtWidgets.QDialog):
    def __init__(self,pv,parent=None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle('Waveform inspector')
        self.resize(760,540)
        self.samples=()
        self.cursor=None
        layout=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel(pv)
        title.setWordWrap(True)
        layout.addWidget(title)
        self.status=QtWidgets.QLabel('Waiting for waveform')
        layout.addWidget(self.status)
        controls=QtWidgets.QHBoxLayout()
        self.freeze=QtWidgets.QCheckBox('Freeze')
        controls.addWidget(self.freeze)
        self.start=QtWidgets.QSpinBox()
        self.end=QtWidgets.QSpinBox()
        controls.addWidget(QtWidgets.QLabel('From'))
        controls.addWidget(self.start)
        controls.addWidget(QtWidgets.QLabel('To'))
        controls.addWidget(self.end)
        reset=QtWidgets.QPushButton('Full range')
        reset.clicked.connect(self.full_range)
        controls.addWidget(reset)
        controls.addStretch()
        copy=QtWidgets.QPushButton('Copy samples')
        copy.clicked.connect(self.copy_samples)
        controls.addWidget(copy)
        layout.addLayout(controls)
        self.stats=QtWidgets.QLabel('—')
        self.stats.setWordWrap(True)
        layout.addWidget(self.stats)
        self.plot=SamplePlot(self.select_sample)
        layout.addWidget(self.plot,2)
        self.model=SampleModel(self)
        self.table=QtWidgets.QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.clicked.connect(lambda index:self.select_sample(index.row()))
        layout.addWidget(self.table,1)
        self.start.valueChanged.connect(self.redraw)
        self.end.valueChanged.connect(self.redraw)
        self.freeze.toggled.connect(lambda on:self.status.setText('Frozen buffer' if on else 'Waiting for next buffer'))
    def feed(self,value,error=''):
        if self.freeze.isChecked():
            return
        samples=capture_samples(value)
        old_n=len(self.samples)
        self.samples=samples
        self.model.set_samples(samples)
        n=len(samples)
        for widget in (self.start,self.end):
            widget.blockSignals(True)
            widget.setRange(0,max(0,n-1))
        if n!=old_n:
            self.start.setValue(0)
            self.end.setValue(max(0,n-1))
            self.cursor=None
        for widget in (self.start,self.end): widget.blockSignals(False)
        self.status.setText(f'{n} samples · raw PV values' if n else (error or 'Unavailable or exceeds inspection limit'))
        self.redraw()
    def full_range(self):
        self.start.setValue(0)
        self.end.setValue(max(0,len(self.samples)-1))
    def redraw(self,*_):
        start,end=sorted((self.start.value(),self.end.value()))
        self.plot.set_data(self.samples,start,end,self.cursor)
        stats=sample_statistics(self.samples[start:end+1])
        if stats:
            lo,hi,mean,rms,std=stats
            self.stats.setText(f'Min {lo:.6g} · Max {hi:.6g} · P-P {hi-lo:.6g} · Mean {mean:.6g} · RMS {rms:.6g} · σ {std:.6g}')
        else: self.stats.setText('—')
    def select_sample(self,index):
        if not 0<=index<len(self.samples): return
        self.cursor=index
        self.table.selectRow(index)
        self.table.scrollTo(self.model.index(index,0))
        self.redraw()
    def copy_samples(self):
        start,end=sorted((self.start.value(),self.end.value()))
        text='Index,Value\n'+'\n'.join(f'{i},{self.samples[i]:.17g}' for i in range(start,min(end+1,len(self.samples))))
        QtWidgets.QApplication.clipboard().setText(text)
