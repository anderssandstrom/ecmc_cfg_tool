import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
import unittest
from qt_compat import QtWidgets
from ecmc_plugin_monitor import plugin_pv
from ecmc_axis_expert_panel import AxisExpertPanel
from ecmc_ioc_dashboard import IocDashboard
from ecmc_native_diagnostic_panel import NativeDiagnosticPanel

class NativePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def test_plugin_names(self):
        self.assertEqual(plugin_pv("IOC","Signal"),"IOC:Signal")
        with self.assertRaises(ValueError): plugin_pv("IOC","OTHER:Signal")
    def test_axis_demo(self):
        panel=AxisExpertPanel("DEMO:X",1,demo=True)
        try:
            panel.show()
            self.app.processEvents()
            panel.arm.setChecked(True)
            panel.jog_speed.setText("1")
            panel._press_jog(1,panel._jog_buttons[1])
            self.assertTrue(panel._demo_motion[True].active)
            panel._release_jog()
            self.assertFalse(panel._demo_motion[True].active)
            panel.interface.setCurrentIndex(1)
            self.app.processEvents()
            panel.interface.setCurrentIndex(0)
            self.app.processEvents()
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()
    def test_diagnostics(self):
        for kind,obj in (("plugin",0),("state_machine",0),("safety_plugin",0),("thread",None)):
            panel=NativeDiagnosticPanel("DEMO",kind,obj,demo=True)
            try:
                panel.show()
                self.app.processEvents()
                panel._tick()
            finally:
                panel.close()
                panel.deleteLater()
                self.app.processEvents()
    def test_dashboard(self):
        panel=IocDashboard({"prefix":"DEMO"},lambda key:None,lambda kill:None,demo=True)
        try:
            panel.show()
            self.app.processEvents()
            panel._tick()
        finally:
            panel.close()
            panel.deleteLater()
            self.app.processEvents()
