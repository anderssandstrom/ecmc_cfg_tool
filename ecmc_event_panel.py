"""Session event log with CSV export."""
import csv
from qt_compat import QtCore,QtWidgets

class EventPanel(QtWidgets.QWidget):
    def __init__(self,history):
        super().__init__()
        self.history=history
        self.revision=-1
        layout=QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Observed health transitions · session only · UTC · sampled, not lossless"))
        self.table=QtWidgets.QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(("UTC","IOC","Object / PV","Previous","Current"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2,QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
        button=QtWidgets.QPushButton("Export CSV")
        button.clicked.connect(self._export)
        layout.addWidget(button)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
    def _tick(self):
        if self.revision==self.history.revision:return
        self.revision=self.history.revision
        rows=list(reversed(self.history.events))
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,value in enumerate(row):
                self.table.setItem(i,j,QtWidgets.QTableWidgetItem(value))
    def _export(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,"Export events","events.csv","CSV (*.csv)")
        if path:
            try:
                with open(path,"w",newline="",encoding="utf-8") as stream:
                    writer=csv.writer(stream)
                    writer.writerow(("UTC","IOC","Object / PV","Previous","Current"))
                    writer.writerows(self.history.events)
            except OSError as ex: QtWidgets.QMessageBox.warning(self,"Export failed",str(ex))
    def showEvent(self,event):
        super().showEvent(event)
        self._tick()
        self.timer.start()
    def hideEvent(self,event):
        self.timer.stop()
        super().hideEvent(event)
