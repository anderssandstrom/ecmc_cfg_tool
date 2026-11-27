import unittest
from ecmc_axis_diagnostics import axis_alarm_summary

class AlarmSummaryTest(unittest.TestCase):
    def test_native(self):
        values={'-ErrId':0,'-WrnId':0,'-Blocked':0,'-SumIlockFwd':0,'-SumIlockBwd':0,'-LimFwd':1,'-LimBwd':1,'-SftLimFwdIlock':0,'-SftLimBwdIlock':0,'-InRT':1}
        self.assertEqual(axis_alarm_summary(values)[0],'ok')
        values['-LimFwd']=0
        self.assertEqual(axis_alarm_summary(values)[0],'warning')
        values['-ErrId']=42
        self.assertEqual(axis_alarm_summary(values)[0],'fault')
        self.assertIn('0x2A',axis_alarm_summary(values)[1])
    def test_unknown(self):
        self.assertEqual(axis_alarm_summary({})[0],'unknown')
    def test_motor(self):
        self.assertEqual(axis_alarm_summary({'.MSTA':512,'.LVIO':0,'.HLS':0,'.LLS':0},False)[0],'fault')
