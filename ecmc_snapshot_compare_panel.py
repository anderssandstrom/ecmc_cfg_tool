"""Compare exported diagnostic discovery snapshots against current IOC."""
import json
from qt_compat import QtWidgets
from ecmc_engineering_compare import compare_snapshots

class SnapshotCompare(QtWidgets.QWidget):
    def __init__(self,current):
        super().__init__()
        self.current=current
        layout=QtWidgets.QVBoxLayout(self)
        note=QtWidgets.QLabel("Compare a saved diagnostic snapshot with current discovery. This is not a full axis YAML or SDO parameter comparison.")
        note.setWordWrap(True)
        layout.addWidget(note)
        button=QtWidgets.QPushButton("Load baseline JSON")
        button.clicked.connect(self._load)
        layout.addWidget(button)
        self.status=QtWidgets.QLabel("No baseline loaded")
        layout.addWidget(self.status)
        self.table=QtWidgets.QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(("Group","Object / field","Baseline","Current","Change"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(3,QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
    def _load(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,"Baseline snapshot","","JSON (*.json)")
        if not path:return
        try:
            with open(path,encoding="utf-8") as f: old=json.load(f)
            if not isinstance(old,dict):raise ValueError("Expected snapshot object")
            now=self.current()
            if not now.get("prefix"):raise ValueError("Discover current IOC first")
            rows=compare_snapshots(old,now)
            self.table.setRowCount(len(rows))
            for i,(key,a,b,status) in enumerate(rows):
                for j,value in enumerate((key[0],key[1]+" / "+key[2],a,b,status)):
                    self.table.setItem(i,j,QtWidgets.QTableWidgetItem("—" if value is None else str(value)))
            self.status.setText(f"{len(rows)} differences · current IOC {now['prefix']}")
        except Exception as ex:QtWidgets.QMessageBox.warning(self,"Comparison failed",str(ex))
