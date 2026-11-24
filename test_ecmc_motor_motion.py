"""Offline Motor Record operation contract tests."""
import unittest
from ecmc_motor_motion import set_move_params, set_jog_params, request_stop, request_kill


class MotorMotionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.messages = []
        self.fail = set()

    def put(self, field, value, **kwargs):
        self.calls.append((field, value))
        if field in self.fail:
            raise RuntimeError(field)

    def test_move_order(self):
        set_move_params(self.put, self.messages.append, 4, 2, 3, 5)
        self.assertEqual(self.calls, [('VMAX',5),('ACCS',3),('VELO',4),('ACCL',2)])

    def test_optional_failure(self):
        self.fail.add('VMAX')
        set_move_params(self.put, self.messages.append, 4, 2, vmax=5)
        self.assertEqual(self.calls, [('VMAX',5),('VELO',4),('ACCL',2)])
        self.assertTrue(self.messages)

    def test_required_failure(self):
        self.fail.add('VELO')
        with self.assertRaises(RuntimeError):
            set_move_params(self.put, self.messages.append, 4, 2)
        self.assertEqual(self.calls, [('VELO',4)])

    def test_jog_fallback(self):
        self.fail.add('JVEL')
        set_jog_params(self.put, self.messages.append, 4, 2)
        self.assertEqual(self.calls, [('ACCL',2),('JVEL',4),('VELO',4)])

    def test_stop(self):
        request_stop(self.put, self.messages.append)
        self.assertEqual(self.calls, [('JOGF',0),('JOGR',0),('STOP',1)])

    def test_stop_fallback(self):
        self.fail.update(('JOGF','STOP'))
        request_stop(self.put, self.messages.append)
        self.assertEqual(self.calls, [('JOGF',0),('JOGR',0),('STOP',1),('SPMG',0)])

    def test_stop_failure(self):
        self.fail.update(('STOP','SPMG'))
        with self.assertRaises(RuntimeError):
            request_stop(self.put, self.messages.append)

    def test_kill(self):
        self.fail.add('STOP')
        request_kill(self.put)
        self.assertEqual(self.calls, [('JOGF',0),('JOGR',0),('STOP',1),('CNEN',0)])


if __name__ == '__main__':
    unittest.main()
