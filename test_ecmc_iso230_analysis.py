import unittest
from ecmc_iso230_analysis import compensation_rows, compare_metrics, session_metrics, settling_time

class AnalysisTests(unittest.TestCase):
    def test_compensation_sign(self):
        rows=[dict(target=10.,direction=d,ref_error=e,reference_mean=10+e)
              for d,e in [('forward',.3),('reverse',.1)] for _ in range(2)]
        m=session_metrics({'measurements':rows})
        r=compensation_rows(m,[10.],2)[0]
        self.assertAlmostEqual(r['position_correction'],-.2)
        self.assertAlmostEqual(r['reversal_forward_minus_reverse'],.2)
        with self.assertRaises(ValueError):
            compensation_rows(m,[10.,20.],2)
    def test_saved_adjusted_values(self):
        rows=[dict(target=0.,direction=d,reference_mean=2.,ref_error=2.,
                   adjusted_reference_mean=1.,adjusted_ref_error=1.)
              for d in ('forward','reverse') for _ in range(2)]
        self.assertEqual(session_metrics({'measurements':rows})['mean_bias'],1.)
    def test_settling(self):
        self.assertEqual(settling_time([(0,1),(.5,0),(1,0),(1.5,0)],0,.1,.5),.5)
        self.assertIsNone(settling_time([(0,0),(1,1),(1.1,0)],0,.1,.5))
    def test_missing_comparison(self):
        self.assertIsNone(compare_metrics({}, {})[0][-1])
