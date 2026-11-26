import weakref
"""Saved, read-only PV dashboards spanning any number of IOC prefixes."""
import csv
import json
from datetime import datetime, timezone
from qt_compat import QtCore, QtWidgets
from ecmc_pv_subscriptions import shared_subscriptions


def validate_dashboard(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Expected dashboard version 1')
    rows = data.get('signals')
    if not isinstance(rows, list) or len(rows) > 256:
        raise ValueError('Expected at most 256 signals')
    result = []
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid signal')
        name = row.get('pv', '')
        if not isinstance(name,str) or not name or any(c.isspace() for c in name):
            raise ValueError('Invalid PV name')
        label = row.get('label', name)
        if not isinstance(label,str): raise ValueError('Invalid label')
        if name not in [r['pv'] for r in result]: result.append(dict(pv=name,label=label))
    return result


class CustomDashboard(QtWidgets.QWidget):
    def __init__(self, on_plot=None, demo=False):
        super().__init__()
        self.on_plot, self.demo = on_plot, demo
        self.rows, self.leases = [], {}
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel('Read-only dashboard · full PV names may span multiple IOCs' + (' · DEMO (no CA connections)' if demo else '')))
        buttons = QtWidgets.QHBoxLayout()
        for label, action in (('Add PV',self._add),('Remove selected',self._remove),
                              ('Load',self._load),('Save',self._save),
                              ('Trend selected',self._trend),('Export report CSV',self._report)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(action)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table = QtWidgets.QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(('Label / PV','Value','Connection','Severity','Last event UTC'))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0,QtWidgets.QHeaderView.Stretch)
        layout.addWidget(self.table)
        self.status = QtWidgets.QLabel('No signals configured')
        layout.addWidget(self.status)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self._tick)
        leases = self.leases
        weakref.finalize(self, lambda: [lease.close() for lease in list(leases.values())])

    def set_rows(self, rows):
        rows = validate_dashboard(dict(version=1, signals=rows))
        self._stop()
        self.rows = rows
        self.table.setRowCount(len(rows))
        for i,row in enumerate(rows):
            for j,text in enumerate((row['label']+'\n'+row['pv'],'—','Disconnected','—','—')):
                self.table.setItem(i,j,QtWidgets.QTableWidgetItem(text))
        if self.isVisible(): self._start()

    def _start(self):
        errors = []
        if not self.demo:
            for row in self.rows:
                name = row['pv']
                if name not in self.leases:
                    try: self.leases[name] = shared_subscriptions.acquire(name)
                    except Exception as ex: errors.append(str(ex))
        self.status.setText('; '.join(errors) if errors else f'{len(self.rows)} signals')
        self.timer.start()
        self._tick()

    def _stop(self):
        self.timer.stop()
        for lease in self.leases.values(): lease.close()
        self.leases.clear()

    def _tick(self):
        for i,row in enumerate(self.rows):
            lease = self.leases.get(row['pv'])
            state = lease.snapshot() if lease else {}
            valid = state.get('connected') and state.get('severity') != 3
            stamp = state.get('timestamp')
            try: stamp = datetime.fromtimestamp(stamp,timezone.utc).isoformat() if stamp else '—'
            except (ValueError,TypeError,OverflowError): stamp = '—'
            values = (str(state.get('value')) if valid and state.get('value') is not None else '—',
                      'Connected' if state.get('connected') else 'Disconnected',str(state.get('severity','—')),stamp)
            for j,value in enumerate(values,1): self.table.item(i,j).setText(value)

    def _selected(self):
        return sorted({i.row() for i in self.table.selectionModel().selectedRows()})
    def _add(self):
        name,ok = QtWidgets.QInputDialog.getText(self,'Add signal','Full PV name (including IOC prefix):')
        if ok and name.strip():
            try: self.set_rows(self.rows+[dict(pv=name.strip(),label=name.strip())])
            except ValueError as ex: self.status.setText(str(ex))
    def _remove(self):
        selected = self._selected()
        self.set_rows([r for i,r in enumerate(self.rows) if i not in selected])
    def _trend(self):
        names = [self.rows[i]['pv'] for i in self._selected()]
        if names and self.on_plot: self.on_plot(names, demo_value=(lambda pv: None) if self.demo else None)
    def _load(self):
        path,_ = QtWidgets.QFileDialog.getOpenFileName(self,'Load dashboard','','JSON (*.json)')
        if path:
            try:
                with open(path,encoding='utf-8') as f: rows=validate_dashboard(json.load(f))
                self.set_rows(rows)
            except Exception as ex: self.status.setText(str(ex))
    def _save(self):
        path,_ = QtWidgets.QFileDialog.getSaveFileName(self,'Save dashboard','','JSON (*.json)')
        if path:
            try:
                with open(path,'w',encoding='utf-8') as f: json.dump(dict(version=1,signals=self.rows),f,indent=2)
            except OSError as ex: self.status.setText(str(ex))
    def _report(self):
        path,_ = QtWidgets.QFileDialog.getSaveFileName(self,'Export visible values','','CSV (*.csv)')
        if path:
            try:
                with open(path,'w',newline='',encoding='utf-8') as f:
                    writer=csv.writer(f)
                    writer.writerow(('Report UTC','Label / PV','Value','Connection','Severity','Last event UTC'))
                    now=datetime.now(timezone.utc).isoformat()
                    for i in range(self.table.rowCount()):
                        writer.writerow([now]+[self.table.item(i,j).text() for j in range(5)])
            except OSError as ex: self.status.setText(str(ex))
    def showEvent(self,event):
        super().showEvent(event)
        self._start()
    def hideEvent(self,event):
        self._stop()
        super().hideEvent(event)
