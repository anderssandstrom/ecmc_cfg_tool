import unittest
from ecmc_compact_layout import column_count
class CompactLayoutTest(unittest.TestCase):
    def test_wrap(self):
        self.assertEqual(column_count(550,2,300),1)
        self.assertEqual(column_count(650,2,300),2)
        self.assertEqual(column_count(1000,8,300),3)
        self.assertEqual(column_count(0,2,300),1)
