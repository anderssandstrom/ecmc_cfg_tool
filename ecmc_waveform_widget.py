"""Bounded, read-only waveform summaries; sample index, never assumed time or voltage."""
import math
from qt_compat import QtCore, QtGui, QtWidgets

def waveform_summary(value):
    if value is None:
        return None
    try:
        try:
            count=len(value)
            if not count:
                return None
            values=value
        except TypeError:
            values=(value,)
            count=1
        if isinstance(values,(str,bytes)):
            return None
        lo,hi,total,n=math.inf,-math.inf,0.,0
        for sample in values:
            v=float(sample)
            if math.isfinite(v):
                lo,hi=min(lo,v),max(hi,v)
                total+=v
                n+=1
        if not n:
            return None
        size=min(count,512)
        points=tuple(float(values[i*(count-1)//max(1,size-1)]) for i in range(size))
        return count,float(values[-1]),lo,hi,total/n,points
    except (ValueError,TypeError,OverflowError):
        return None

class WaveformValue(QtWidgets.QLabel):
    def __init__(self,parent=None):
        super().__init__('—',parent)
        self.summary=None
        self._editable=False
        self.setMinimumHeight(92)
        self.setMinimumWidth(160)
        self.setAlignment(QtCore.Qt.AlignTop|QtCore.Qt.AlignLeft)

    def set_editable(self,enabled):
        pass

    def set_value(self,value):
        self.summary=waveform_summary(value)
        if self.summary is None:
            self.setText('Waveform unavailable')
        else:
            n,last,lo,hi,mean,_=self.summary
            self.setText(f'Last {last:.6g} · N={n}\nMin {lo:.6g}  Max {hi:.6g}\nMean {mean:.6g} · raw units')
        self.update()

    def paintEvent(self,event):
        super().paintEvent(event)
        if not self.summary:
            return
        _,_,lo,hi,_,points=self.summary
        top= max(self.fontMetrics().height()*3+4, self.height()-45)
        bottom=self.height()-4
        if bottom<=top:
            return
        painter=QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(QtGui.QColor('#287bc0'),1.4))
        path=QtGui.QPainterPath()
        started=False
        for i,v in enumerate(points):
            if not math.isfinite(v):
                started=False
                continue
            x=3+(self.width()-6)*i/max(1,len(points)-1)
            y=(top+bottom)/2 if hi==lo else bottom-(bottom-top)*(v-lo)/(hi-lo)
            if started: path.lineTo(x,y)
            else: path.moveTo(x,y); started=True
        if len(points)==1:
            painter.drawEllipse(QtCore.QPointF(3,(top+bottom)/2),2,2)
        else:
            painter.drawPath(path)
        painter.end()
