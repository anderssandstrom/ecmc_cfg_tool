import unittest
from ecmc_hardware_catalog import hardware_pvs, hardware_writable_pvs

class ExpandedHardwareTest(unittest.TestCase):
    def test_exact_database_mappings(self):
        for key,signal in [["EL5001","Enc01-PosAct"],["EL5002","Enc01-PosAct"],["EL5072","Enc01-PosAct"],["EL5101-0011","Enc01-Stat"],["EL4132","AO01"],["EL4732","AO01-CycCnt"]]:
            pvs=dict(hardware_pvs('IOC',0,3,[key]))
            self.assertEqual(pvs[signal],'IOC:m0s003-'+signal)
            self.assertEqual(hardware_writable_pvs('IOC',0,3,[key]),{})
    def test_encoder_channel_counts(self):
        self.assertNotIn('Enc02-PosAct',dict(hardware_pvs('IOC',0,3,['EL5001'])))
        self.assertIn('Enc02-PosAct',dict(hardware_pvs('IOC',0,3,['EL5002'])))
