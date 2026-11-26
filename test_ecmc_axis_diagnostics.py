import unittest
from ecmc_axis_diagnostics import axis_signals

class AxisDiagnosticTest(unittest.TestCase):
    def test_interfaces(self):
        native = {pv for _,rows in axis_signals("IOC:AX","native") for _,pv in rows}
        motor = {pv for _,rows in axis_signals("IOC:AX","motor") for _,pv in rows}
        self.assertIn("IOC:AX-PosAct",native)
        self.assertIn("IOC:AX.RBV",motor)
        self.assertIn("IOC:AX.DMOV",motor)
        self.assertFalse(native & motor)
        self.assertEqual(axis_signals("", "native"), ())
        with self.assertRaises(ValueError):
            axis_signals("IOC:AX","other")
