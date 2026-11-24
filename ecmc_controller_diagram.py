"""Native Qt controller schematic. All editable cells use existing app callbacks."""
import math
from qt_compat import QtCore, QtGui, QtWidgets


class ControllerDiagram(QtWidgets.QWidget):
    def __init__(self, make_cell, row_def):
        super().__init__()
        self.setMinimumSize(1160, 720)
        self._links = []
        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setHorizontalSpacing(34)
        grid.setVerticalSpacing(30)

        def panel(title, fields=(), monitoring=False):
            box = QtWidgets.QGroupBox(title)
            border = '#49866e' if monitoring else '#5685aa'
            box.setStyleSheet('QGroupBox { border: 1px solid '+border+'; border-radius: 5px; margin-top: 12px; padding-top: 6px; font-weight: bold; } QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 3px; }')
            form = QtWidgets.QFormLayout(box)
            form.setContentsMargins(8, 14, 8, 8)
            form.setSpacing(4)
            for label, name in fields:
                form.addRow(label, make_cell(row_def(name)))
            return box

        pid = panel('PID parameters — outer / inner')
        form = pid.layout()
        for label, suffix in (('Kp','Kp'), ('Ki','Ki'), ('Kd','Kd')):
            pair = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(pair)
            h.setContentsMargins(0,0,0,0)
            h.addWidget(make_cell(row_def('AxisCntrl'+suffix)))
            h.addWidget(make_cell(row_def('AxisCntrlInner'+suffix)))
            form.addRow(label, pair)
        form.addRow('Inner tolerance', make_cell(row_def('AxisCntrlInnerTol')))
        ff = panel('Velocity feedforward', [('Kff','AxisCntrlKff')])
        traj = panel('Trajectory / position')
        traj.layout().addRow(QtWidgets.QLabel('Position setpoint'))
        error = panel('Error  (+ −)')
        error.layout().addRow(QtWidgets.QLabel('Position error'))
        p = panel('P — proportional')
        integ = panel('I — accumulator', [('Min','AxisCntrlIPartLL'),('Max','AxisCntrlIPartHL'),('Reset at ramp','AxisCntrlResetIAtRmp'),('Freeze at ramp','AxisCntrlFreezeIAtRmp')])
        d = panel('D — difference')
        summ = panel('Σ  P + I + D + FF')
        out = panel('Output limits', [('Min','AxisCntrlOutLL'),('Max','AxisCntrlOutHL')])
        drive = panel('Drive scaling', [('Numerator','AxisDrvScaleNum'),('Denominator','AxisDrvScaleDenom')])
        process = panel('Physical process')
        enc = panel('Encoder scaling', [('Numerator','AxisEncScaleNum'),('Denominator','AxisEncScaleDenom')])
        target = panel('At-target monitoring', [('Enable','AxisMonEnableAtTargetMon'),('Tolerance','AxisMonAtTargetTol'),('Time [cycles]','AxisMonAtTargetTime')], True)
        deadband = panel('Controller deadband', [('Tolerance','AxisCntrlDeadband'),('Time [cycles]','AxisCntrlDeadbandTime')], True)
        slave = panel('Slave control deadband', [('Tolerance','AxisMonSlvCtrlDbTol'),('Time [cycles]','AxisMonSlvCtrlDbTime')], True)

        grid.addWidget(pid,0,2,1,2)
        grid.addWidget(ff,0,4)
        grid.addWidget(traj,2,0)
        grid.addWidget(error,2,1)
        grid.addWidget(p,1,2)
        grid.addWidget(integ,2,2)
        grid.addWidget(d,3,2)
        grid.addWidget(summ,2,3)
        grid.addWidget(out,2,4)
        grid.addWidget(drive,2,5)
        grid.addWidget(process,3,5)
        grid.addWidget(enc,4,4,1,2)
        grid.addWidget(target,5,0,1,2)
        grid.addWidget(deadband,5,2,1,2)
        grid.addWidget(slave,5,4,1,2)
        for a,b in ((traj,error),(error,p),(error,integ),(error,d),(p,summ),(integ,summ),(d,summ),(summ,out),(out,drive)):
            self._links.append((a,b,'right','left',False))
        self._links.extend([(ff,summ,'bottom','top',False),(drive,process,'bottom','top',False),
                            (process,enc,'bottom','right',False),(enc,error,'left','bottom',False),
                            (pid,p,'bottom','top',True)])
        self._grid = grid

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        def anchor(widget, side):
            r = widget.geometry()
            return {'left': QtCore.QPointF(r.left(),r.center().y()),
                    'right': QtCore.QPointF(r.right(),r.center().y()),
                    'top': QtCore.QPointF(r.center().x(),r.top()),
                    'bottom': QtCore.QPointF(r.center().x(),r.bottom())}[side]
        for a,b,sa,sb,dashed in self._links:
            start,end=anchor(a,sa),anchor(b,sb)
            pen=QtGui.QPen(self.palette().color(QtGui.QPalette.WindowText),1.5)
            if dashed:
                pen.setStyle(QtCore.Qt.DashLine)
            painter.setPen(pen)
            if sa=='right' and sb=='left':
                mid=(start.x()+end.x())/2
                points=[start,QtCore.QPointF(mid,start.y()),QtCore.QPointF(mid,end.y()),end]
            elif sa=='left' and sb=='bottom':
                y=start.y()
                points=[start,QtCore.QPointF(end.x(),y),end]
            elif sa=='bottom' and sb=='right':
                x=end.x()+12
                points=[start,QtCore.QPointF(start.x(),end.y()),QtCore.QPointF(x,end.y()),end]
            else:
                mid=(start.y()+end.y())/2
                points=[start,QtCore.QPointF(start.x(),mid),QtCore.QPointF(end.x(),mid),end]
            painter.drawPolyline(QtGui.QPolygonF(points))
            if not dashed:
                prev=next((p for p in reversed(points[:-1]) if p!=end),start)
                angle=math.atan2(end.y()-prev.y(),end.x()-prev.x())
                head=[end]+[QtCore.QPointF(end.x()-8*math.cos(angle+t),end.y()-8*math.sin(angle+t)) for t in (-.45,.45)]
                painter.setBrush(pen.color())
                painter.drawPolygon(QtGui.QPolygonF(head))
        painter.end()
