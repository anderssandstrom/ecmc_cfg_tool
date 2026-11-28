import unittest
from ecmc_hardware_widgets import is_bit_array
from ecmc_hardware_demo import HardwareDemo

class BitArrayTest(unittest.TestCase):
    def test_classification(self):
        for name in ('BI01-Arr','BO01-Arr','IOC:m0s003-BO02-Arr-RB'):
            self.assertTrue(is_bit_array(name))
        for name in ('AI01-Arr','AO02-Arr','Enc01-PosAct-Arr'):
            self.assertFalse(is_bit_array(name))
    def test_demo(self):
        m=HardwareDemo()
        pv='IOC:ECMC:m0s003-BO01-Arr'
        m.write(pv,32769)
        self.assertEqual(m.value(pv),32769)
        self.assertEqual(m.value(pv+'-RB'),32769)
        self.assertIsInstance(m.value('IOC:ECMC:m0s003-BI01-Arr'),int)
