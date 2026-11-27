"""Compact responsive card grid, shared by hardware and multi-axis panels."""
def column_count(width, count, preferred, gap=6):
    if count<=0:
        return 1
    return max(1,min(count,(max(0,width)+gap)//(max(1,preferred)+gap)))

from qt_compat import QtCore, QtWidgets

class BoundedTabWidget(QtWidgets.QTabWidget):
    """Keep hidden pages and accumulated tab labels from widening the workspace."""
    def minimumSizeHint(self):
        return QtCore.QSize(0,0)

    def sizeHint(self):
        return QtCore.QSize(640,480)

def visible_width(widget):
    """Use the visible intersection with ancestors, not an oversized content widget."""
    left=widget.mapToGlobal(QtCore.QPoint(0,0)).x()
    right=left+widget.width()
    parent=widget.parentWidget()
    while parent is not None:
        x=parent.mapToGlobal(QtCore.QPoint(0,0)).x()
        left=max(left,x)
        right=min(right,x+parent.width())
        parent=parent.parentWidget()
    return max(0,right-left)
