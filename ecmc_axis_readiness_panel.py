"""Live explanation of axis readiness from existing monitor samples."""
from qt_compat import QtWidgets
from ecmc_axis_readiness import diagnose

class AxisReadiness(QtWidgets.QWidget):
    def __init__(self,base,native,on_plot=None):
        super().__init__()
        self.base,self.native,self.on_plot=base,native,on_plot
        self.rows=[]
        layout=QtWidgets.QVBoxLayout(self)
        self.summary=QtWidgets.QLabel("Waiting for samples")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table=QtWidgets.QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels(("Status","Check","Explanation"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2,QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table,1)
        button=QtWidgets.QPushButton("Trend selected check")
        button.setEnabled(on_plot is not None)
        button.clicked.connect(self._trend)
        layout.addWidget(button)
        note=QtWidgets.QLabel("Observed conditions only, not a safety assessment or proof that motion is permitted.")
        note.setWordWrap(True)
        layout.addWidget(note)
    def _trend(self):
        row=self.table.currentRow()
        if self.on_plot and 0<=row<len(self.rows):
            self.on_plot([self.base+k for k in self.rows[row][3] if k!="-MsgTxt"])
    def update_samples(self,samples):
        rows=diagnose(samples,self.native)
        if rows==self.rows:return
        self.rows=rows
        self.summary.setText(" · ".join(f"{sum(r[0]==s for r in rows)} {s.lower()}" for s in ("Error","Blocked","Warning","Unknown")))
        self.table.setRowCount(len(rows))
        for i,(level,title,detail,keys) in enumerate(rows):
            for j,value in enumerate((level,title,detail)):
                self.table.setItem(i,j,QtWidgets.QTableWidgetItem(value))
