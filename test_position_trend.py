import unittest
from ecmc_axis_diagnostics import position_trend_pvs

class PositionTrendTest(unittest.TestCase):
    def test_native(self):
        self.assertEqual(position_trend_pvs("IOC:AX", "native"), ["IOC:AX-PosAct", "IOC:AX-TgtPosCmd", "IOC:AX-PosSet"])
    def test_motor(self):
        self.assertEqual(position_trend_pvs("IOC:AX", "motor"), ["IOC:AX.RBV", "IOC:AX.VAL"])
