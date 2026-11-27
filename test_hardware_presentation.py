import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from ecmc_hardware_widgets import digital_pair_status, signal_caption

class PresentationTest(unittest.TestCase):
    def test_pair(self):
        self.assertEqual(digital_pair_status(1,'1'),'Match')
        self.assertEqual(digital_pair_status(1,0),'Mismatch')
        self.assertEqual(digital_pair_status(None,0),'Unknown')
    def test_labels(self):
        self.assertEqual(signal_caption('PosAct'),'Actual position')
        self.assertEqual(signal_caption('Drv01-ErrAlrm'),'Drv01 Error')
