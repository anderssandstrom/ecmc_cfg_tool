"""Read-only domain monitor and ordered slave overview."""
import csv
import time
from collections import deque
from qt_compat import QtCore,QtWidgets
from ecmc_engineering_compare import domain_signals,counter_change

class DomainMonitor(QtWidgets.QWidget):
    def __init__(self,prefix,master,count,demo=False,on_plot=None):
        super().__init__()
        self.demo,self.on_plot=demo,on_plot
        self.rows=domain_signals(prefix,master,count)
        self.pvs={}
        self.last={}
        self.baseline={}
        self.history=deque(maxlen=500)
        self.states={}
        self.failure_started={}
        layout=QtWidgets.QVBoxLayout(self)
        self.note=QtWidgets.QLabel("Domain working counters and cumulative failure counters. Delta is since panel opened or baseline reset; unavailable/reset counters are not interpreted as zero.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.table=QtWidgets.QTableWidget(len(self.rows)//6,6)
        self.table.setHorizontalHeaderLabels(("Domain","WC","State","Failures total","Delta","Redundancy"))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for i in range(self.table.rowCount()):
            for j in range(6): self.table.setItem(i,j,QtWidgets.QTableWidgetItem(str(i) if j==0 else "—"))
        layout.addWidget(self.table)
        row=QtWidgets.QHBoxLayout()
        reset=QtWidgets.QPushButton("Reset local baseline")
        reset.clicked.connect(self.baseline.clear)
        row.addWidget(reset)
        trend=QtWidgets.QPushButton("Trend selected domain")
        trend.setEnabled(on_plot is not None and not demo)
        trend.clicked.connect(self._trend)
        row.addWidget(trend)
        export=QtWidgets.QPushButton('Export history…')
        export.clicked.connect(self._export)
        row.addWidget(export)
        layout.addLayout(row)
        self.events=QtWidgets.QTableWidget(0,5)
        self.events.setHorizontalHeaderLabels(('Time','Domain','Event','Failures added','Duration (s)'))
        self.events.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.events)
        self.timer=QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
    def _event(self,domain,event,delta=None,duration=None):
        entry=(time.strftime('%Y-%m-%d %H:%M:%S'),domain,event,delta,duration)
        self.history.append(entry)
        self.events.insertRow(0)
        for j,value in enumerate(entry):
            self.events.setItem(0,j,QtWidgets.QTableWidgetItem('—' if value is None else str(value)))
        if self.events.rowCount()>self.history.maxlen:
            self.events.removeRow(self.events.rowCount()-1)

    def _observe(self,domain,state,total):
        previous=self.states.get(domain)
        now=time.monotonic()
        bad=('Zero','Incomplete')
        if state!=previous:
            if state in bad and previous not in bad:
                self.failure_started[domain]=now
            if state=='Complete' and previous in bad:
                start=self.failure_started.pop(domain,None)
                self._event(domain,'Recovered',duration=round(now-start,2) if start is not None else None)
            elif state=='Unknown':
                self.failure_started.pop(domain,None)
                if previous is not None: self._event(domain,'Monitoring unavailable')
            elif state in bad:
                self._event(domain,state)
            self.states[domain]=state
        old=self.last.get(domain)
        change=counter_change(old,total)
        if change is not None and change>0:
            self._event(domain,'Failure counter increased',change)
        elif old is not None and total is not None and change is None:
            self._event(domain,'Counter reset or discontinuity')

    def _export(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'Export domain history','domain_history.csv','CSV (*.csv)')
        if not path: return
        try:
            with open(path,'w',newline='',encoding='utf-8') as stream:
                writer=csv.writer(stream)
                writer.writerow(('Time','Domain','Event','Failures added','Duration (s)'))
                writer.writerows(self.history)
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self,'Export failed',str(exc))

    def _trend(self):
        row=self.table.currentRow()
        if row>=0 and self.on_plot: self.on_plot([p for i,p in self.rows if i==row])
    def _read(self,name):
        if self.demo: return 1 if name.endswith("-WC-Complete") else 42 if name.endswith("-WC") else 0
        try:
            pv=self.pvs.get(name)
            if pv is not None and pv.connected and pv.severity!=3:
                return pv.get(use_monitor=True,timeout=.001)
        except Exception: pass
        return None
    def _tick(self):
        for i in range(self.table.rowCount()):
            wc,complete,incomplete,zero,total,red=[self._read(p) for j,p in self.rows if i==j]
            state="Zero" if zero==1 else "Incomplete" if incomplete==1 else "Complete" if complete==1 else "Unknown"
            self._observe(i,state,total)
            if total is None: self.baseline.pop(i,None)
            elif i not in self.baseline or counter_change(self.last.get(i),total) is None: self.baseline[i]=total
            delta=counter_change(self.baseline.get(i),total)
            self.last[i]=total
            for j,v in enumerate((wc,state,total,delta,red),1):
                self.table.item(i,j).setText("—" if v is None else str(v))
    def showEvent(self,event):
        super().showEvent(event)
        if not self.demo:
            try:
                import epics
                for _,name in self.rows: self.pvs[name]=epics.PV(name,auto_monitor=True)
            except Exception: pass
        self.baseline.clear()
        self.last.clear()
        self.states.clear()
        self.failure_started.clear()
        self.timer.start()
        self._tick()
    def hideEvent(self,event):
        self.timer.stop()
        for pv in self.pvs.values():
            try: pv.disconnect()
            except Exception: pass
        self.pvs.clear()
        super().hideEvent(event)
