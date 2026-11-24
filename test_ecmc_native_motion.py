import unittest
from ecmc_native_motion import NativeMotionMixin


class Fake(NativeMotionMixin):
    def __init__(self,mode):
        self.mode=mode
        self.writes=[]
    def _value(self,key):
        stat=(1<<6)|(1<<7)|(1<<10)|(self.mode<<24)
        return dict(Stat=stat,Auto=self.mode,ErrId=0,Enabled=1,EnableCmd=1,PosAct=0).get(key)
    def _put(self,field,value):
        self.writes.append((field,value))


class NativeMotionTests(unittest.TestCase):
    def test_explicit(self):
        axis=Fake(0)
        axis.native_move_absolute(2,0,(-3,3))
        self.assertEqual(axis.writes,[('MtnCmd',3),('MtnCmdData',0),('TgtPosCmd',2),('ExeCmd',1)])
    def test_auto(self):
        axis=Fake(1)
        axis.native_move_absolute(2,1,(-3,3))
        self.assertEqual(axis.writes[-1],('TgtPosCmd',2))
        self.assertNotIn(('ExeCmd',1),axis.writes)
    def test_changed_mode(self):
        axis=Fake(1)
        with self.assertRaises(RuntimeError):
            axis.native_move_absolute(2,0,(-3,3))
        self.assertEqual(axis.writes,[])
    def test_limits(self):
        axis=Fake(1)
        with self.assertRaises(RuntimeError):
            axis.native_move_absolute(4,1,(-3,3))
        self.assertEqual(axis.writes,[])
    def test_kill(self):
        axis=Fake(1)
        self.assertEqual(axis.native_kill(),[])
        self.assertEqual(axis.writes,[('EnaCmd',0),('StpCmd',1)])


if __name__=='__main__':
    unittest.main()
