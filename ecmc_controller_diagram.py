"""Fit-to-window controller diagram with native interactive Qt controls."""
import math
from qt_compat import QtCore, QtGui, QtWidgets


class ControllerDiagram(QtWidgets.QGraphicsView):
    WIDTH = 1000
    HEIGHT = 438

    def __init__(self, make_cell, row_def):
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

        def box(x, y, w, h, title, monitoring=False):
            item = scene.addRect(x,y,w,h,QtGui.QPen(QtGui.QColor(green if monitoring else blue)),
                                 QtGui.QBrush(QtGui.QColor('#f1f7f3' if monitoring else '#f7f9fb')))
            item.setZValue(1)
            label(x+w/2,y+4,title,12,True,True)

        def cell(x,y,name):
            widget = make_cell(row_def(name))
            proxy = scene.addWidget(widget)
            proxy.setPos(x,y)
            proxy.setZValue(3)

        def field(x,y,title,name,dx=70):
            label(x,y+4,title,11)
            cell(x+dx,y,name)

        def line(points, dashed=False, arrow=True):
            pen=QtGui.QPen(QtGui.QColor(ink),1.5)
            if dashed:
                pen.setStyle(QtCore.Qt.DashLine)
            path=QtGui.QPainterPath(QtCore.QPointF(*points[0]))
            for p in points[1:]:
                path.lineTo(QtCore.QPointF(*p))
            item=scene.addPath(path,pen)
            item.setZValue(0)
            if arrow and not dashed:
                x,y=points[-1]
                px,py=points[-2]
                a=math.atan2(y-py,x-px)
                poly=QtGui.QPolygonF([QtCore.QPointF(x,y)]+[
                    QtCore.QPointF(x-7*math.cos(a+t),y-7*math.sin(a+t))
                    for t in (-.45,.45)])
                scene.addPolygon(poly,QtGui.QPen(QtCore.Qt.NoPen),QtGui.QBrush(QtGui.QColor(ink)))

        def summing(x,y,sign):
            item=scene.addEllipse(x-12,y-12,24,24,QtGui.QPen(QtGui.QColor(ink),1.5),
                                  QtGui.QBrush(QtGui.QColor('#f7f9fb')))
            item.setZValue(1)
            label(x,y-11,sign,16,True,True)

        # Explicit logical coordinates, independent of font metrics or layout
        # compression. Native widgets remain editable inside graphics proxies.
        box(205,8,300,116,'PID parameters')
        label(357,25,'Outer',11,True,True)
        label(455,25,'Inner',11,True,True)
        for i,key in enumerate(('Kp','Ki','Kd')):
            y=39+22*i
            label(215,y+4,key,11)
            cell(311,y,'AxisCntrl'+key)
            cell(409,y,'AxisCntrlInner'+key)
        field(215,102,'Inner tolerance','AxisCntrlInnerTol',96)

        box(570,22,178,53,'Velocity feedforward')
        field(582,47,'Kff','AxisCntrlKff',62)
        box(8,190,91,58,'Trajectory')
        label(53,211,'Position',11,False,True)
        label(53,226,'setpoint',11,False,True)
        summing(130,219,'−')
        box(182,141,96,38,'P')
        label(230,158,'Proportional',11,False,True)
        box(182,190,210,106,'I — accumulator')
        field(192,211,'Min','AxisCntrlIPartLL',110)
        field(192,232,'Max','AxisCntrlIPartHL',110)
        field(192,253,'Reset at ramp','AxisCntrlResetIAtRmp',110)
        field(192,274,'Freeze at ramp','AxisCntrlFreezeIAtRmp',110)
        box(182,310,96,35,'D')
        label(230,326,'Difference',11,False,True)
        summing(432,219,'+')
        summing(544,219,'+')
        box(578,181,172,76,'Output limits')
        field(588,205,'Min','AxisCntrlOutLL',66)
        field(588,228,'Max','AxisCntrlOutHL',66)
        box(776,181,216,76,'Drive scaling')
        field(786,205,'Numerator','AxisDrvScaleNum',108)
        field(786,228,'Denominator','AxisDrvScaleDenom',108)
        box(850,280,142,39,'Physical process')
        label(921,298,'Motor / mechanics',11,False,True)
        box(578,279,216,76,'Encoder scaling')
        field(588,302,'Numerator','AxisEncScaleNum',108)
        field(588,325,'Denominator','AxisEncScaleDenom',108)

        line([(99,219),(118,219)])
        line([(142,219),(158,219),(158,160),(182,160)])
        line([(158,219),(182,219)])
        line([(158,219),(158,327),(182,327)])
        line([(278,160),(432,160),(432,207)])
        line([(392,219),(420,219)])
        line([(278,327),(432,327),(432,231)])
        line([(444,219),(532,219)])
        line([(659,75),(659,116),(544,116),(544,207)])
        # Separate velocity signal from the same trajectory generator.
        line([(53,190),(53,130),(557,130),(557,48),(570,48)])
        label(60,115,'Trajectory velocity',11)
        line([(556,219),(578,219)])
        line([(750,219),(776,219)])
        line([(884,257),(884,269),(921,269),(921,280)])
        line([(850,300),(822,300),(822,317),(794,317)])
        line([(578,317),(475,317),(475,359),(130,359),(130,231)])
        label(285,344,'Actual position feedback',11)
        line([(230,122),(230,141)],True,False)

        # Monitoring is shown separately from the arithmetic signal path.
        box(8,372,330,64,'At-target monitoring',True)
        field(17,394,'Enable','AxisMonEnableAtTargetMon',48)
        field(166,394,'Tolerance','AxisMonAtTargetTol',72)
        # Use remaining lower row; compact cells keep the whole scene visible.
        field(17,413,'Time [cycles]','AxisMonAtTargetTime',96)
        box(350,372,310,64,'Controller deadband',True)
        field(359,394,'Tolerance','AxisCntrlDeadband',92)
        field(359,413,'Time [cycles]','AxisCntrlDeadbandTime',92)
        box(672,372,320,64,'Slave control deadband',True)
        field(681,394,'Tolerance','AxisMonSlvCtrlDbTol',92)
        field(681,413,'Time [cycles]','AxisMonSlvCtrlDbTime',92)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(),QtCore.Qt.KeepAspectRatio)

    def showEvent(self,event):
        super().showEvent(event)
        self.fitInView(self.sceneRect(),QtCore.Qt.KeepAspectRatio)
