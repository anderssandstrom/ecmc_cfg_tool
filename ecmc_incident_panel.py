"""Read-only incident viewer; does not create an EPICS client."""
import json
from qt_compat import QtWidgets

class IncidentPanel(QtWidgets.QWidget):
    def __init__(self,bundle):
        super().__init__()
        layout=QtWidgets.QVBoxLayout(self)
        title=QtWidgets.QLabel('Offline incident — '+str(bundle['discovery'].get('prefix','Unknown'))+
            (' [DEMO]' if bundle.get('demo') else ''))
        layout.addWidget(title)
        note=QtWidgets.QLabel(str(bundle.get('captured_utc',bundle.get('timestamp','Unknown time'))) +
            '\n'+ '\n'.join(bundle.get('limitations',['Legacy snapshot; cached PVs and events may be absent.'])))
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs=QtWidgets.QTabWidget()
        layout.addWidget(tabs)
        for label,key in [('Health','health'),('Events','events'),('PV values','pvs'),
                          ('Trends (seconds before capture)','trends'),('IOC discovery / versions','discovery')]:
            page=QtWidgets.QPlainTextEdit()
            page.setReadOnly(True)
            page.setPlainText(json.dumps(bundle.get(key,{}),indent=2,ensure_ascii=False))
            tabs.addTab(page,label)
