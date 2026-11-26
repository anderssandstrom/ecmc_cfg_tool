import unittest
from ecmc_hardware_demo import HardwareDemo

class DemoIOTest(unittest.TestCase):
    def test_output_readback(self):
        model = HardwareDemo()
        for name, value in [('BO01', 1), ('AO02', 1234)]:
            pv = 'DEMO:IOC:m0s005-' + name
            self.assertEqual(model.value(pv+'-RB'), 0)
            model.write(pv, value)
            self.assertEqual(model.value(pv), value)
            self.assertEqual(model.value(pv+'-RB'), value)
