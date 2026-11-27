import unittest
from ecmc_plc_demo import demo_program, demo_snapshot

class PlcDemoTest(unittest.TestCase):
    def test_pages(self):
        source,variables=demo_program()
        self.assertIn('global.interlock',source)
        self.assertGreater(len(variables),32)
        first=demo_snapshot(variables,0,4)
        second=demo_snapshot(variables,1,4)
        self.assertEqual(len(first),32)
        self.assertIn(32,second)
        self.assertEqual(first[8],30)
        self.assertEqual(demo_snapshot(variables,0,9)[1],20)
