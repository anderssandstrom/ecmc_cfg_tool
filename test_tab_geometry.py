import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from qt_compat import QtCore, QtWidgets
from ecmc_compact_layout import BoundedTabWidget, visible_width

class TabGeometryTest(unittest.TestCase):
    def test_hidden_wide_tab_does_not_increase_minimum(self):
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        tabs=BoundedTabWidget()
        for i in range(16):
            page=QtWidgets.QWidget()
            page.setMinimumWidth(1600 if i else 100)
            tabs.addTab(page,'Long hardware tab '+str(i))
        tabs.setCurrentIndex(0)
        self.assertEqual(tabs.minimumSizeHint().width(),0)
        tabs.resize(500,300)
        tabs.show()
        app.processEvents()
        self.assertLessEqual(tabs.width(),500)
        self.assertLessEqual(visible_width(tabs.currentWidget()),500)
        tabs.close()
        tabs.deleteLater()
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
        app.processEvents()
