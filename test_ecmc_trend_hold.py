import unittest
from ecmc_trend_hold import held_points

class HoldTest(unittest.TestCase):
    def test_steps(self):
        self.assertEqual(held_points([(1,0),(3,1),(5,0)],2,6), [(2,0),(3,0),(3,1),(5,1),(5,0),(6,0)])
    def test_constant(self):
        self.assertEqual(held_points([(1,7)],2,6), [(2,7),(6,7)])
    def test_gap(self):
        self.assertEqual(held_points([(1,0),(3,None),(5,1)],2,6), [(2,0),(3,0),(3,None),(5,1),(6,1)])
    def test_no_initial_value(self):
        self.assertEqual(held_points([(3,1)],2,6), [(3,1),(6,1)])
    def test_disconnected_before_window(self):
        self.assertEqual(held_points([(0,1),(1,None)],2,6), [])
