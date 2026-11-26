import unittest
from ecmc_axis_diagnostics import axis_signals, native_soft_bounds, NATIVE_EDIT

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

    def test_native_soft_limits(self):
        native = {pv for _,rows in axis_signals("IOC:AX","native") for _,pv in rows}
        for suffix in ("-CfgDLLM","-CfgDHLM"):
            self.assertIn("IOC:AX"+suffix,native)
            self.assertIn(suffix,NATIVE_EDIT)
        self.assertEqual(native_soft_bounds(-5,10,1,1),(-5,10))
        self.assertEqual(native_soft_bounds(None,10,0,1),(float("-inf"),10))
        self.assertEqual(native_soft_bounds(-5,None,1,0),(-5,float("inf")))
        for args in ((None,10,1,1),(-5,None,1,1),(-5,10,None,1),(10,-5,1,1)):
            with self.assertRaises(ValueError):
                native_soft_bounds(*args)
