import unittest
from ecmc_native_motion import NativeMotionMixin


class Fake(NativeMotionMixin):
    def __init__(self,mode):
        self.mode=mode
        self.writes=[]
        self.overrides={}
    def _value(self,key):
        stat=(1<<6)|(1<<7)|(1<<10)|(self.mode<<24)
        return self.overrides.get(key, dict(Stat=stat,Auto=self.mode,ErrId=0,Enabled=1,EnableCmd=1,PosAct=0).get(key))
    def _put(self,field,value):
        self.writes.append((field,value))


class NativeMotionTests(unittest.TestCase):
    def test_explicit(self):
        axis=Fake(0)
        axis.native_move_absolute(2,0,(-3,3))
        self.assertEqual(axis.writes,[('MtnCmd',3),('MtnCmdData',0),('TgtPosCmd',2),('ExeCmd',0),('ExeCmd',1)])
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
    def test_auto_enable_disabled_drive(self):
        for mode in (0,1):
            axis=Fake(mode)
            axis.auto_enable_timeout=5
            axis.overrides.update(Enabled=0, EnableCmd=0)
            axis.native_move_absolute(2,mode,(-3,3))
            self.assertNotIn(('EnaCmd',1),axis.writes)
            self.assertIn(('TgtPosCmd',2),axis.writes)

    def test_limits(self):
        axis=Fake(1)
        with self.assertRaises(RuntimeError):
            axis.native_move_absolute(4,1,(-3,3))
        self.assertEqual(axis.writes,[])
    def test_unavailable_or_disabled(self):
        for key, value in [('Stat', None), ('Auto', None), ('ErrId', None),
                           ('ErrId', 1), ('Enabled', 0), ('EnableCmd', 0), ('PosAct', None)]:
            with self.subTest(key=key, value=value):
                axis=Fake(1)
                axis.overrides[key]=value
                with self.assertRaises(RuntimeError):
                    axis.native_move_absolute(2,1,(-3,3))
                self.assertEqual(axis.writes,[])

    def test_interlocks_and_busy(self):
        ready=(1<<6)|(1<<7)|(1<<10)|(1<<24)
        for stat in (ready | (1<<3), ready | (1<<5), ready | (1<<17),
                     ready | (1<<18), ready | (1<<23), ready | (1<<11),
                     ready & ~(1<<6), ready & ~(1<<7), ready & ~(1<<10)):
            with self.subTest(stat=stat):
                axis=Fake(1)
                axis.overrides['Stat']=stat
                with self.assertRaises(RuntimeError):
                    axis.native_move_absolute(2,1,(-3,3))
                self.assertEqual(axis.writes,[])

    def test_rejected_target_prevents_execute(self):
        axis=Fake(0)
        put=axis._put
        def reject(field,value):
            if field=='TgtPosCmd':
                raise RuntimeError('write rejected')
            put(field,value)
        axis._put=reject
        with self.assertRaises(RuntimeError):
            axis.native_move_absolute(2,0,(-3,3))
        self.assertNotIn(('ExeCmd',1),axis.writes)

    def test_kill(self):
        axis=Fake(1)
        self.assertEqual(axis.native_kill(),[])
        self.assertEqual(axis.writes,[('EnaCmd',0),('StpCmd',1)])


if __name__=='__main__':
    unittest.main()
