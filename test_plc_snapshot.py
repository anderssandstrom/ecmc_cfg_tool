import unittest
from ecmc_plc_metadata import parse_values

class PlcSnapshotTest(unittest.TestCase):
    def test_page(self):
        self.assertEqual(parse_values(b'ECMCPLCV1 -65 32 2\n1.5\n-2\n',-65,1),{32:1.5,33:-2.0})
    def test_token(self):
        with self.assertRaises(ValueError):
            parse_values(b'ECMCPLCV1 -66 32 1\n1\n',-65,1)
    def test_truncation(self):
        with self.assertRaises(ValueError):
            parse_values(b'ECMCPLCV1 -65 32 2\n1\n',-65,1)
