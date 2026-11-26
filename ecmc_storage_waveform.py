"""Explicit bounded read-only waveform snapshots."""
import math
from qt_compat import QtCore, QtWidgets
from ecmc_plot_widgets import SeriesPlotWidget
from ecmc_demo_model import waveform_points
class SnapshotSignals(QtCore.QObject):
    finished = QtCore.Signal(object,str) if hasattr(QtCore,"Signal") else QtCore.pyqtSignal(object,str)
class SnapshotTask(QtCore.QRunnable):
    def __init__(self,name,timeout):
        super().__init__()
        self.name,self.timeout=name,timeout
        self.signals=SnapshotSignals()
    def run(self):
        pv=None
        result,error=None,""
        try:
            import epics
            pv=epics.PV(self.name,auto_monitor=False)
            if not pv.wait_for_connection(timeout=self.timeout):
                raise RuntimeError("Waveform unavailable")
            value=pv.get(count=min(200000,int(pv.count)),use_monitor=False,timeout=self.timeout)
            if value is None or pv.severity==3: raise RuntimeError("Invalid waveform")
            if not hasattr(value,"__len__"): value=[value]
            result=(len(value),waveform_points(value))
        except Exception as ex: error=str(ex)
        finally:
            if pv is not None:
                try: pv.disconnect()
                except Exception: pass
            self.signals.finished.emit(result,error)
class StorageWaveformPanel(QtWidgets.QWidget):
    def __init__(self,name,demo=False,timeout=1.0):
        super().__init__()
        self.name,self.demo,self.timeout=name,demo,timeout
        self.task=None
        self.generation=0
        layout=QtWidgets.QVBoxLayout(self)
        row=QtWidgets.QHBoxLayout()
        self.button=QtWidgets.QPushButton("Read waveform")
        self.button.clicked.connect(self._read)
        row.addWidget(self.button)
        self.status=QtWidgets.QLabel("Manual snapshot · sample index")
        row.addWidget(self.status,1)
        layout.addLayout(row)
        self.plot=SeriesPlotWidget()
        self.plot.set_axes(title="Data storage",x_label="Buffer index",y_label="Value")
        layout.addWidget(self.plot,1)
    def _read(self):
        if self.task is not None: return
        if self.demo:
            values=[math.sin(i*.03)+.2*math.sin(i*.17) for i in range(1024)]
            self._display((len(values),waveform_points(values)),"")
            return
        generation=self.generation
        task=SnapshotTask(self.name,self.timeout)
        self.task=task
        self.button.setEnabled(False)
        def done(result,error):
            self.task=None
            self.button.setEnabled(True)
            if generation==self.generation: self._display(result,error)
        task.signals.finished.connect(done)
        QtCore.QThreadPool.globalInstance().start(task)
    def _display(self,result,error):
        if error:
            self.status.setText(error)
            self.plot.set_series({})
            return
        n,points=result
        self.plot.set_series({self.name:points})
        self.status.setText(f"{n} elements read · {len(points)} plotted · max 200000 read")
    def hideEvent(self,event):
        self.generation+=1
        super().hideEvent(event)
