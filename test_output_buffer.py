import unittest
from ecmc_output_buffer import make_buffer

class OutputBufferTest(unittest.TestCase):
    def test_constant(self):
        self.assertEqual(make_buffer(4,'Constant',10),[32767]*4)
        self.assertEqual(make_buffer(4,'Zero'),[0]*4)
    def test_sine(self):
        self.assertEqual(make_buffer(4,'Sine',0,10),[0,32767,0,-32767])
    def test_custom_and_limits(self):
        self.assertEqual(make_buffer(3,'Custom',custom='-10,0,10'),[-32767,0,32767])
        for args in [(101,'Zero'),(4,'Constant',11),(4,'Sine',9,2),(4,'Custom')]:
            with self.assertRaises(ValueError): make_buffer(*args)
