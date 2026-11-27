"""Shared read-only PV context actions for diagnostic widgets."""
from qt_compat import QtCore, QtWidgets

def pv_menu(parent, global_pos, pvs, on_plot=None, demo_value=None, value=None, trendable=True):
    pvs=list(dict.fromkeys(pvs))
    if not pvs:
        return
    menu=QtWidgets.QMenu(parent)
    for title,add in (("Trend PV",False),("Add to existing trend…",True)):
        action=menu.addAction(title)
        action.setEnabled(bool(on_plot and trendable))
        action.triggered.connect(lambda checked=False,add=add:on_plot(pvs,add=add,demo_value=demo_value))
    action=menu.addAction("Copy PV name" if len(pvs)==1 else "Copy PV names")
    action.triggered.connect(lambda checked=False:QtWidgets.QApplication.clipboard().setText("\n".join(pvs)))
    if value is not None:
        action=menu.addAction("Copy value")
        action.triggered.connect(lambda checked=False:QtWidgets.QApplication.clipboard().setText(str(value)))
    execute=getattr(menu,"exec",None) or menu.exec_
    execute(global_pos)

def bind_pv_menu(widget,pv,on_plot=None,demo_value=None,trendable=True):
    widget.setToolTip(pv)
    widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
    widget.customContextMenuRequested.connect(lambda pos:pv_menu(widget,widget.mapToGlobal(pos),[pv],on_plot,demo_value,widget.text() if hasattr(widget,"text") else None,trendable))
