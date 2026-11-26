import unittest
from ecmc_hardware_demo import HardwareDemo

class DemoDS402Test(unittest.TestCase):
    def test_drive_commands(self):
        model = HardwareDemo()
        drive = 'DEMO:IOC:m0s002-Drv01'
        def read(suffix):
            return model.value(drive+'-'+suffix, 'EL7062', 'EL7062')
        self.assertEqual(read('Cmd-RB'), 15)
        self.assertEqual(read('Stat'), 39)
        model.write(drive+'-TrgDS402Dis', 1, ds402=True)
        self.assertEqual(read('Stat'), 64)
        self.assertEqual(read('Spd-RB'), 0)
        model.write(drive+'-TrgDS402Ena', 1, ds402=True)
        self.assertEqual(read('Stat'), 39)
        model.write(drive+'-Cmd', 6, ds402=True)
        self.assertEqual(read('Cmd-RB'), 6)
        self.assertEqual(read('Stat'), 33)
        self.assertEqual(model.value('DEMO:IOC:m0s002-Drv02-Stat', 'EL7062', 'EL7062'), 39)
