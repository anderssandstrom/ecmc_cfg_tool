import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from qt_compat import QtWidgets
from ecmc_axis_troubleshooting import AxisTroubleshooting, display_sample, selected_axes

class TroubleshootingTest(unittest.TestCase):
    def test_limits(self):
        self.assertEqual(display_sample('-LimFwd',1)[0],'No')
        self.assertEqual(display_sample('-LimFwd',0),('Yes','#c43c3c'))
        self.assertEqual(display_sample('-ErrId',None)[0],'—')
    def test_demo(self):
        app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        axes=[dict(id='1',name='A',motor='DEMO:A'),dict(id='2',name='B',motor='DEMO:B')]
        self.assertEqual(len(selected_axes(axes+axes)),2)
        pane=AxisTroubleshooting(axes,axes,demo=True)
        pane.show()
        app.processEvents()
        self.assertEqual(len(pane.cards),2)
        self.assertEqual(pane.cards[0][1]['-PosAct'].text(),'10')
        self.assertEqual(pane.cards[1][1]['-PosAct'].text(),'15')
        self.assertFalse(pane.leases)
        pane.hide()
        self.assertFalse(pane.timer.isActive())
        pane.close()
