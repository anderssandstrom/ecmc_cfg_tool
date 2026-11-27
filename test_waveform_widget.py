import unittest
from ecmc_waveform_widget import waveform_summary
from ecmc_hardware_catalog import hardware_pvs
from ecmc_hardware_demo import HardwareDemo

class WaveformTests(unittest.TestCase):
    def test_statistics(self):
        s=waveform_summary([1,2,3,4])
        self.assertEqual(s[:5],(4,4,1,4,2.5))
        self.assertIsNone(waveform_summary([]))
        self.assertEqual(len(waveform_summary(list(range(10000)))[5]),512)
    def test_catalog_demo(self):
        pvs=dict(hardware_pvs('IOC',0,1,['EL4732']))
        for key in ('AO01-Arr','AO02-Arr'):
            self.assertIn(key,pvs)
            self.assertEqual(len(HardwareDemo().value(pvs[key],'EL4732','EL4732')),64)
