import unittest
from ecmc_plc_metadata import registered_index_at

class TestSourceWatch(unittest.TestCase):
    def test_exact(self):
        variables=[('Local','counter'),('Global','ax1.posact')]
        self.assertEqual(registered_index_at('if ax1.posact > 0',8,variables),1)
        self.assertEqual(registered_index_at('counter := 1',2,variables),0)
        self.assertIsNone(registered_index_at('counter2 := 1',2,variables))
        self.assertIsNone(registered_index_at('// counter',5,variables))
    def test_ambiguous(self):
        self.assertIsNone(registered_index_at('x := 1',0,[('Local','x'),('Global','x')]))
