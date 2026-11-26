import unittest
from ecmc_hardware_catalog import hardware_pvs

class HardwareCatalogTest(unittest.TestCase):
    def test_stepper(self):
        rows = dict(hardware_pvs("SIM:", 2, 13, ["EL70x1"]))
        self.assertEqual(rows["Enc01-PosAct"], "SIM:m2s013-Enc01-PosAct")
        self.assertIn("Operational", rows)

    def test_dual_channel(self):
        rows = dict(hardware_pvs("SIM", 0, 1, ["EL7062"]))
        self.assertIn("Enc01-PosAct", rows)
        self.assertIn("Enc02-PosAct", rows)

    def test_unknown(self):
        rows = dict(hardware_pvs("SIM", 0, 0, ["unknown"]))
        self.assertIn("Online", rows)
        self.assertNotIn("Enc01-PosAct", rows)

if __name__ == "__main__":
    unittest.main()
