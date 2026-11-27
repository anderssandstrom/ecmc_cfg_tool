import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from qt_compat import QtWidgets
from ecmc_ioc_navigator import IocNavigator

class WidgetProtectionTest(unittest.TestCase):
    def test_axis_protection(self):
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        w=IocNavigator(prefix='DEMO',demo=True)
        w._open_native_axis_expert(w._snapshot['axes'][0])
        panel=w.tabs.currentWidget()
        self.assertTrue(panel.arm.isChecked())
        w.widget_protection.setChecked(True)
        self.assertFalse(panel.arm.isChecked())
        w.widget_protection.setChecked(False)
        self.assertTrue(panel.arm.isChecked())
        w.close()
        w.deleteLater()
        from qt_compat import QtCore
        QtCore.QCoreApplication.sendPostedEvents(None,QtCore.QEvent.DeferredDelete)
        app.processEvents()
