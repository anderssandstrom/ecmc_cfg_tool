import unittest
from ecmc_hardware_catalog import hardware_pvs, hardware_writable_pvs

class OptionalHardwareTest(unittest.TestCase):
    def test_el7062_opt(self):
        for key in ('EL7062_OPT', 'EX7062_OPT'):
            rows = dict(hardware_pvs('IOC',0,1,[key]))
            for suffix in ('Enc01-PosAct','Enc02-PosAct','Drv02-Stat','CfgMode02','Enc02-PosAct02','Tp02-PosTimestamp01','Drv02-PosDiff'):
                self.assertIn(suffix,rows)
            self.assertEqual(hardware_writable_pvs('IOC',0,1,[key]),{})
