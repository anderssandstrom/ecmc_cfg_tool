import unittest
from ecmc_daq_trigger import TriggerCapture


class TriggerTests(unittest.TestCase):
    def test_rising_and_bounds(self):
        c=TriggerCapture(['x'],'err',0,1,2,3,100)
        c.add('err',0,0)
        for t in range(5): c.add('x',t,t)
        self.assertTrue(c.add('err',4,1))
        c.add('x',6,6)
        c.add('x',8,8)
        self.assertEqual(list(c.buffers['x']),[(2,2),(3,3),(4,4),(6,6)])
    def test_no_initial_trigger(self):
        c=TriggerCapture(['x'],'x',0,1,2,3,100)
        self.assertFalse(c.add('x',0,2))
        c.add('x',1,0)
        self.assertTrue(c.add('x',2,2))
    def test_falling(self):
        c=TriggerCapture(['x'],'x',1,0,2,3,100)
        c.add('x',0,1)
        self.assertTrue(c.add('x',1,0))


if __name__=='__main__':
    unittest.main()
