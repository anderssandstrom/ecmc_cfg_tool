"""Fit-to-window controller diagram with native interactive Qt controls."""
import math
from qt_compat import QtCore, QtGui, QtWidgets


class ControllerDiagram(QtWidgets.QGraphicsView):
    WIDTH = 1000
    HEIGHT = 458

    def __init__(self, make_cell, row_def, pid_ms=False, timing_ms=False, mode='UNKNOWN'):
        super().__init__()
        scene = QtWidgets.QGraphicsScene(self)
        self.setScene(scene)
        scene.setSceneRect(0, 0, self.WIDTH, self.HEIGHT)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setRenderHint(QtGui.QPainter.Antialiasing)
        scene.setBackgroundBrush(QtGui.QBrush(QtGui.QColor('#e9edf0')))
        self.mode = mode
        self.live_items = {}
        blue = '#527a99'
        green = '#548675'
        ink = '#273b4b'

        def label(x, y, text, size=12, bold=False, center=False):
            item = scene.addSimpleText(text)
            font = QtGui.QFont()
            font.setPixelSize(size)
            font.setBold(bold)
            item.setFont(font)
            item.setBrush(QtGui.QBrush(QtGui.QColor(ink)))
            if center:
                x -= item.boundingRect().width()/2
            item.setPos(x, y)
            item.setZValue(2)
            return item

        def live_label(key,x,y,center=False):
            item = label(x,y,'',10,True,center)
            self.live_items[key] = (item,x,center)
            return item

        def box(x, y, w, h, title, monitoring=False, tooltip=None):
            item = scene.addRect(x,y,w,h,QtGui.QPen(QtGui.QColor(green if monitoring else blue)),
                                 QtGui.QBrush(QtGui.QColor('#f1f7f3' if monitoring else '#f7f9fb')))
            item.setZValue(1)
            heading = label(x+w/2,y+4,title,12,True,True)
            if tooltip:
                item.setToolTip(tooltip)
                heading.setToolTip(tooltip)

        def cell(x,y,name):
            widget = make_cell(row_def(name))
            proxy = scene.addWidget(widget)
            proxy.setPos(x,y)
            proxy.setZValue(3)

        def field(x,y,title,name,dx=70):
            label(x,y+4,title,11)
            cell(x+dx,y,name)

        def line(points, dashed=False, arrow=True, color=None):
            color = color or ink
            pen=QtGui.QPen(QtGui.QColor(color),1.5)
            if dashed:
                pen.setStyle(QtCore.Qt.DashLine)
            path=QtGui.QPainterPath(QtCore.QPointF(*points[0]))
            for p in points[1:]:
                path.lineTo(QtCore.QPointF(*p))
            item=scene.addPath(path,pen)
            item.setZValue(0)
            if arrow:
                x,y=points[-1]
                px,py=points[-2]
                a=math.atan2(y-py,x-px)
                poly=QtGui.QPolygonF([QtCore.QPointF(x,y)]+[
                    QtCore.QPointF(x-7*math.cos(a+t),y-7*math.sin(a+t))
                    for t in (-.45,.45)])
                scene.addPolygon(poly,QtGui.QPen(QtCore.Qt.NoPen),QtGui.QBrush(QtGui.QColor(color)))

        def summing(x,y,sign):
            item=scene.addEllipse(x-12,y-12,24,24,QtGui.QPen(QtGui.QColor(ink),1.5),
                                  QtGui.QBrush(QtGui.QColor('#f7f9fb')))
            item.setZValue(1)
            label(x,y-11,sign,16,True,True)

        # Explicit logical coordinates, independent of font metrics or layout
        # compression. Native widgets remain editable inside graphics proxies.
        box(100,8,300,116,'PID parameters — P / I / D',
            tooltip='Inner gains are selected only if inner tolerance > 0 and the absolute difference between the final target position and actual position is strictly smaller than that tolerance.')
        self.outer_heading = label(252,25,'Outer',11,True,True)
        self.inner_heading = label(350,25,'Inner',11,True,True)
        for i,key in enumerate(('Kp','Ki','Kd')):
            y=39+21*i
            label(110,y+4, {'Ki':'Ti [ms]', 'Kd':'Td [ms]'}.get(key,key) if pid_ms else key,11)
            cell(206,y,'AxisCntrl'+key)
            cell(304,y,'AxisCntrlInner'+key)
        field(110,103,'Inner tolerance','AxisCntrlInnerTol',96)
        label(299,105,'|target−act| < tol',10).setToolTip('Inner tolerance must also be greater than zero.')

        box(425,22,178,53,'Velocity feedforward' if mode != 'CSP' else 'Kff (unused in CSP)',
            tooltip='CSV: trajectory velocity setpoint × Kff. CSP with ECMC correction uses zero feedforward.')
        field(437,47,'Kff','AxisCntrlKff',62)

        label(799,8,{'CSV':'CSV position controller','CSP':'CSP position control'}.get(mode,'Drive mode unknown'),16,True,True)
        box(610,32,382,96,'Control / monitoring conditions',True,
            tooltip='ECMC 11.2.0: enabled at-target monitoring qualifies each tolerance counter when the setpoint is within the at-target tolerance of the final target and the local trajectory is not busy. With external trajectory source, the controller-deadband flag is supplied by PLC instead.')
        label(620,55,'At-target enabled; setpoint near final target',11)
        label(620,71,'Local trajectory not busy; error tolerance + time',11)
        if mode == 'CSP':
            label(620,91,'Optional ECMC PID offset; variant not detected.',11)
            label(620,107,'Direct CSP bypasses PID; correction holds in deadband.',11)
        else:
            label(620,91,'CSP: direct position or PID correction; FF = 0',11)
            label(620,107,'CSP correction holds its last offset in deadband.',11)
        box(8,190,91,58,'Trajectory')
        label(53,211,'Position',11,False,True)
        label(53,226,'setpoint',11,False,True)
        summing(130,219,'−')
        box(182,141,96,38,'P')
        label(230,158,'Proportional',11,False,True)
        box(182,190,210,106,'I — accumulator',
            tooltip='Integral contribution is clamped only if max > min. Internal trajectory busy: reset takes precedence over freeze; otherwise integrate Ki × position error.')
        field(192,211,'Min','AxisCntrlIPartLL',105)
        field(192,232,'Max','AxisCntrlIPartHL',105)
        field(192,253,'Reset on busy','AxisCntrlResetIAtRmp',105)
        field(192,274,'Freeze on busy','AxisCntrlFreezeIAtRmp',105)
        box(182,310,96,35,'D')
        label(230,326,'Difference',11,False,True)
        summing(420,219,'+')
        if mode != 'CSP':
            summing(490,219,'+')
        box(520,181,168,76,'Output limits',
            tooltip='Limits the sum of P + I + D + FF only when max > min.')
        field(528,205,'Min','AxisCntrlOutLL',62)
        field(528,228,'Max','AxisCntrlOutHL',62)
        box(710,190,68,58,'Run / 0' if mode != 'CSP' else 'Hold / +',True,
            tooltip=('CSP with ECMC correction: when not busy and in controller deadband, hold the last PID offset and add it to the position setpoint. Direct CSP bypasses the correction.' if mode == 'CSP' else 'CSV: when not busy and in controller deadband, skip PID calculation, reset controller state and send zero velocity to the drive. Axis disabled, invalid feedback or interlocks can also suppress control.'))
        label(744,212,'PID reset' if mode != 'CSP' else 'PID offset',10,False,True)
        label(744,225,'if gated' if mode != 'CSP' else '+ setpoint',10,False,True)
        box(800,181,192,76,'Drive scaling')
        field(808,205,'Numerator','AxisDrvScaleNum',86)
        field(808,228,'Denominator','AxisDrvScaleDenom',86)
        box(850,289,142,38,'Physical process')
        label(921,307,'Motor / mechanics',11,False,True)
        box(578,270,216,76,'Encoder scaling')
        field(588,293,'Numerator','AxisEncScaleNum',108)
        field(588,316,'Denominator','AxisEncScaleDenom',108)

        line([(99,219),(118,219)])
        line([(142,219),(158,219),(158,160),(182,160)])
        line([(158,219),(182,219)])
        line([(158,219),(158,327),(182,327)])
        line([(278,160),(420,160),(420,207)])
        line([(392,219),(408,219)])
        line([(278,327),(420,327),(420,231)])
        if mode != 'CSP':
            line([(432,219),(478,219)])
            line([(490,75),(490,207)])
            # Separate velocity signal from the same trajectory generator.
            line([(53,190),(53,132),(412,132),(412,48),(425,48)])
            label(60,135,'Trajectory velocity',11)
        else:
            # Optional correction path is illustrated; the direct setpoint
            # bypass runs above the arithmetic blocks, to the final sum.
            line([(53,190),(53,132),(744,132),(744,190)])
            label(60,135,'Position setpoint',11)
            line([(432,219),(520,219)])
            label(490,90,'FF = 0',11,True,True)
        if mode != 'CSP':
            line([(502,219),(520,219)])
        line([(688,219),(710,219)])
        line([(778,219),(800,219)])
        line([(921,257),(921,289)])
        line([(850,308),(794,308)])
        line([(780,346),(780,364),(130,364),(130,231)])
        label(285,344,'Actual position feedback',11)

        # Dashed green control path, not a position or velocity signal. Break
        # the path at the feedback crossing to make clear there is no junction.
        line([(375,372),(375,369),(505,369)],True,False,green)
        line([(505,359),(505,263),(744,263),(744,248)],True,True,green)
        label(515,265,'Deadband + not busy',10).setToolTip('CSV: reset PID and command zero. CSP correction: hold last offset.')

        # Monitoring is shown separately from the arithmetic signal path.
        box(8,372,244,84,'At-target monitor',True,
            tooltip='Determines the at-target status using the common trajectory qualification and its own tolerance/time. Its enable also gates internal deadband monitoring.')
        field(15,394,'Enable','AxisMonEnableAtTargetMon',82)
        field(15,414,'Tolerance','AxisMonAtTargetTol',82)
        field(15,434,('Time [ms]' if timing_ms else 'Time [cycles]'),'AxisMonAtTargetTime',82)
        box(260,372,230,84,'Controller deadband',True,
            tooltip='CSV: controller deadband AND not busy resets the PID and commands zero velocity. CSP with ECMC correction: holds the last correction. External trajectory: PLC supplies the controller-deadband status.')
        field(269,394,'Tolerance','AxisCntrlDeadband',82)
        field(269,413,('Time [ms]' if timing_ms else 'Time [cycles]'),'AxisCntrlDeadbandTime',82)
        box(498,372,234,84,'Slave control deadband',True,
            tooltip='Separate tolerance and time qualification for slave control; defaults to the at-target settings if not configured.')
        field(507,394,'Tolerance','AxisMonSlvCtrlDbTol',82)
        field(507,413,('Time [ms]' if timing_ms else 'Time [cycles]'),'AxisMonSlvCtrlDbTime',82)
        box(740,372,252,84,'Position lag monitor',True,
            tooltip='Independent following-error interlock. While enabled, |controller error| above tolerance for more than the configured cycles interlocks the trajectory; the drive interlock is at twice the configured cycle count.')
        field(748,394,'Enable','AxisMonEnableLagMon',82)
        field(748,414,'Tolerance','AxisMonPosLagTol',82)
        field(748,434,('Time [ms]' if timing_ms else 'Time [cycles]'),'AxisMonPosLagTime',82)

        # Live data occupies unused whitespace; no editable configuration
        # widget is touched by the monitor.
        live_label('setpoint',8,252)
        live_label('target',8,270)
        live_label('error',8,288)
        live_label('status',8,306)
        live_label('actual',666,348,True)
        live_label('velocity',921,332,True)
        live_label('at_target',219,415,True)
        live_label('deadband',375,436,True)
        live_label('pid',620,145)
        live_label('message',620,159)

    def _set_live(self,key,text,color='#273b4b'):
        item,x,center = self.live_items[key]
        item.setText(text)
        item.setBrush(QtGui.QBrush(QtGui.QColor(color)))
        item.setX(x-item.boundingRect().width()/2 if center else x)

    def clear_live(self):
        for key in self.live_items:
            self._set_live(key,'')
        for heading in (self.outer_heading,self.inner_heading):
            heading.setBrush(QtGui.QBrush(QtGui.QColor('#273b4b')))

    def set_live_message(self,message):
        self.clear_live()
        self._set_live('message',message)

    def update_live(self,values,inner,deadband):
        def val(key):
            v = values.get(key)
            return '—' if v is None else f'{v:.5g}'
        self._set_live('setpoint','Set: '+val('setpoint'))
        self._set_live('error','e: '+val('error'))
        self._set_live('actual','Act: '+val('actual'))
        self._set_live('velocity','Vel: '+val('velocity'))
        self._set_live('target','Target: '+val('target'))
        status = 'Disabled' if values.get('enabled') == 0 else 'Busy' if values.get('busy') == 1 else 'Idle' if values.get('busy') == 0 else 'Unknown'
        if values.get('error_id') not in (None,0):
            status = 'Axis error'
        self._set_live('status',status)
        at = values.get('at_target')
        self._set_live('at_target','At tgt ✓' if at == 1 else 'At tgt ×' if at == 0 else 'At tgt —',
                       '#237b4b' if at == 1 else '#273b4b')
        self._set_live('deadband',deadband, '#237b4b' if deadband.startswith('likely') else '#273b4b')
        self._set_live('pid','Gain selection (est.): '+('inner' if inner is True else 'outer' if inner is False else 'unknown'))
        self._set_live('message','Live PVs; deadband estimated' if values else 'Waiting for live PVs')
        self.outer_heading.setBrush(QtGui.QBrush(QtGui.QColor('#237b4b' if inner is False else '#273b4b')))
        self.inner_heading.setBrush(QtGui.QBrush(QtGui.QColor('#237b4b' if inner is True else '#273b4b')))

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(),QtCore.Qt.KeepAspectRatio)

    def showEvent(self,event):
        super().showEvent(event)
        self.fitInView(self.sceneRect(),QtCore.Qt.KeepAspectRatio)
