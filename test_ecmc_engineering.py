import unittest
from ecmc_axis_readiness import diagnose
from ecmc_event_history import EventHistory

class EngineeringTests(unittest.TestCase):
    def test_unknown(self):
        self.assertTrue(all(r[0]=="Unknown" for r in diagnose({})))
    def test_interlock(self):
        rows=diagnose({"-SumIlockFwd":1,"-LimFwd":0})
        self.assertIn(("Blocked","Forward interlock"),[(r[0],r[1]) for r in rows])
        self.assertIn(("Warning","Forward hard limit"),[(r[0],r[1]) for r in rows])
    def test_events(self):
        h=EventHistory(2)
        h.observe("A",{"x":"OP"})
        self.assertEqual(len(h.events),0)
        h.observe("A",{"x":"Offline"})
        h.observe("A",{"x":"Offline"})
        self.assertEqual(len(h.events),1)
        h.observe("B",{"x":"OP"})
        self.assertEqual(len(h.events),1)
        h.reset_scope("A")
        h.observe("A",{"x":"OP"})
        self.assertEqual(len(h.events),1)
