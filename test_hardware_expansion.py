import unittest
from ecmc_hardware_catalog import hardware_pvs, hardware_writable_pvs

class ExpandedHardwareTest(unittest.TestCase):
    def test_exact_database_mappings(self):
        for key,signal in [["EL5001","Enc01-PosAct"],["EL5002","Enc01-PosAct"],["EL5072","Enc01-PosAct"],["EL5101-0011","Enc01-Stat"],["EL4132","AO01"],["EL4732","AO01-CycCnt"]]:
            pvs=dict(hardware_pvs('IOC',0,3,[key]))
            self.assertEqual(pvs[signal],'IOC:m0s003-'+signal)
            self.assertEqual(hardware_writable_pvs('IOC',0,3,[key]),{})
    def test_more_hardware(self):
        for key,signal in [["EL2068","BO01"],["EL2819","BO01"],["EL3255","AI01"],["ELM3504_Scalar","AI01"],["ELM3604","AI01-Stat"],["EL7037","Drv01-Cmd-RB"]]:
            self.assertIn(signal,dict(hardware_pvs('IOC',0,3,[key])))
            expected={f'IOC:m0s003-BO{i:02d}':'caToggleButton' for i in range(1,9 if key=='EL2068' else 17)} if key in ('EL2068','EL2819') else {}
            self.assertEqual(hardware_writable_pvs('IOC',0,3,[key]),expected)
    def test_panel_alias(self):
        for hw,panel in [('EL2068','Ex2068'),('EL2819','Ex2819')]:
            self.assertEqual(hardware_writable_pvs('IOC',0,3,[hw]),hardware_writable_pvs('IOC',0,3,[panel]))
    def test_encoder_channel_counts(self):
        self.assertNotIn('Enc02-PosAct',dict(hardware_pvs('IOC',0,3,['EL5001'])))
        self.assertIn('Enc02-PosAct',dict(hardware_pvs('IOC',0,3,['EL5002'])))
