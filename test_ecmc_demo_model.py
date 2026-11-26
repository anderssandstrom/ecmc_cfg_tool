import unittest
from ecmc_demo_model import DemoMotion,waveform_points
from ecmc_homing_spec import HOME_PROCEDURES
class TestDemoModel(unittest.TestCase):
    def test_move(self):
        m=DemoMotion(0); m.start(1,2); m.step(.25,-2,2)
        self.assertEqual(m.position,.5); self.assertTrue(m.active)
        m.step(.25,-2,2); self.assertEqual(m.position,1); self.assertFalse(m.active)
    def test_continuous(self):
        m=DemoMotion(0); m.start(0,-2,True); m.step(.5,-.5,2)
        self.assertEqual(m.position,-.5); self.assertFalse(m.active)
    def test_stop(self):
        m=DemoMotion(0); m.start(2,1); m.stop(); m.step(.5,-2,2)
        self.assertEqual(m.position,0)
    def test_waveform(self):
        self.assertEqual(waveform_points([0,float("nan"),2]),[(0,0.),(2,2.)])
        self.assertLessEqual(len(waveform_points(range(100),7)),7)
    def test_homing(self):
        self.assertEqual([i for i,_ in HOME_PROCEDURES],list(range(1,15)))
