"""Bounded, read-only waveform summaries; sample index, never assumed time or voltage."""
import numpy as np
from qt_compat import QtCore, QtGui, QtWidgets

def waveform_summary(value):
    if value is None:
        return None
    try:
        a=np.asarray(value,dtype=float)
        if a.ndim==0:
            a=a.reshape(1)
        if a.ndim!=1 or not a.size:
            return None
        finite=np.isfinite(a)
        if not finite.any():
            return None
        valid=a[finite]
        # Preserve the original sample order; limit preview drawing to 512 points.
        idx=np.linspace(0,len(a)-1,min(len(a),512),dtype=int)
        points=tuple(float(v) for v in a[idx])
        return len(a),float(a[-1]),float(valid.min()),float(valid.max()),float(valid.mean()),points
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
            if not np.isfinite(v):
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
