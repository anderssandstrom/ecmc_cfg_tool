import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from qt_compat import QtWidgets, QtCore
from ecmc_custom_dashboard import CustomDashboard,validate_dashboard
from ecmc_axis_compare import AxisCompare,compare_values
from ecmc_physical_topology import PhysicalTopology,validate_topology
from ecmc_plc_browser import PlcBrowser

class EngineeringToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def tearDown(self):
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        self.app.processEvents()
    def test_dashboard_validation_and_demo_lifecycle(self):
        rows=validate_dashboard(dict(version=1,signals=[dict(pv='IOC:A'),dict(pv='OTHER:B')]))
        w=CustomDashboard(demo=True);w.set_rows(rows);w.show();self.app.processEvents()
        self.assertEqual(w.table.rowCount(),2);self.assertEqual(w.leases,{})
        w.hide();self.assertFalse(w.timer.isActive());w.deleteLater()
        with self.assertRaises(ValueError):validate_dashboard(dict(version=1,signals=[dict(pv='bad name')]))
    def test_comparison_unknown_and_tolerance(self):
        self.assertEqual(compare_values(None,None)[0],'Unavailable')
        self.assertEqual(compare_values(float('nan'),1)[0],'Unavailable')
        self.assertEqual(compare_values(1,1.001,.01)[0],'Equal')
        self.assertEqual(compare_values(1,2),('Changed','1'))
        w=AxisCompare(demo=True);w.deleteLater()
    def test_topology_explicit_ports(self):
        data=dict(version=1,nodes=[dict(id='master'),dict(id='slave')],links=[{'from':'master','from_port':0,'to':'slave','to_port':0}])
        w=PhysicalTopology();w.set_topology(data)
        self.assertIn('1 explicit cables',w.status.text());w.deleteLater()
        data['links']*=2
        with self.assertRaises(ValueError):validate_topology(data)
    def test_two_trends_share_monitor(self):
        from unittest.mock import patch
        from ecmc_pv_subscriptions import SubscriptionPool
        from test_ecmc_pv_subscriptions import FakePV
        from ecmc_hardware_trend import HardwareTrendWindow
        pool=SubscriptionPool(FakePV)
        with patch('ecmc_pv_subscriptions.shared_subscriptions',pool):
            a=HardwareTrendWindow(['IOC:X']);b=HardwareTrendWindow(['IOC:X'])
            self.assertEqual(len(pool.entries),1)
            pv=pool.entries['IOC:X']['pv']
            pv.callback(value=2,severity=0)
            a.close();self.assertFalse(pv.closed)
            b.close();self.assertTrue(pv.closed)
    def test_navigator_tools_and_plc_tabs(self):
        from ecmc_ioc_navigator import IocNavigator
        w=IocNavigator(prefix='DEMO',demo=True)
        for kind in ('dashboard','axis_compare','topology'):w._open_engineering_tool(kind)
        w._open_native_diagnostic('plc',dict(id='0'))
        pane=w.tabs.currentWidget()
        self.assertEqual(pane.count(),2)
        self.assertIsInstance(pane.widget(1),PlcBrowser)
        w.close();w.deleteLater()
    def test_plc_search(self):
        w=PlcBrowser('DEMO',0,demo=True);w._load()
        self.assertIn('counter',w.source.toPlainText());self.assertEqual(w.table.rowCount(),1)
        w.search.setText('absent');self.assertEqual(w.table.rowCount(),0);w.deleteLater()
if __name__=='__main__':unittest.main()
