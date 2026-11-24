import unittest
import math
from ecmc_iso230_math import compute_metrics, linear_regression

def dataset(fn):
    return [dict(target=x,direction=d,ref_error=e,reference_mean=x+e)
            for x in (0.,50.,100.) for d in ('forward','reverse')
            for e in fn(x,d)]

class MetricsTest(unittest.TestCase):
    def test_perfect(self):
        m=compute_metrics(dataset(lambda x,d:[0.,0.,0.]))
        self.assertEqual(m['bidirectional_accuracy'],0.)
        self.assertEqual(m['bidirectional_repeatability'],0.)
    def test_offset(self):
        m=compute_metrics(dataset(lambda x,d:[.1]*3))
        self.assertAlmostEqual(m['mean_bias'],.1)
        self.assertAlmostEqual(m['bidirectional_accuracy'],0.)
    def test_backlash(self):
        m=compute_metrics(dataset(lambda x,d:[.02 if d=='forward' else -.02]*3))
        for k in ('mean_reversal_value','bidirectional_accuracy','bidirectional_repeatability','bidirectional_systematic_deviation'):
            self.assertAlmostEqual(m[k],.04)
    def test_scale(self):
        m=compute_metrics(dataset(lambda x,d:[.001*x]*3))
        self.assertAlmostEqual(m['fit_slope'],1.001)
        self.assertAlmostEqual(m['bidirectional_accuracy'],.1)
    def test_unequal_spread(self):
        m=compute_metrics(dataset(lambda x,d:[-.01,0,.01] if d=='forward' else [-.02,0,.02]))
        self.assertAlmostEqual(m['forward_axis_repeatability'],.04)
        self.assertAlmostEqual(m['reverse_axis_repeatability'],.08)
        self.assertAlmostEqual(m['bidirectional_repeatability'],.08)
    def test_incomplete(self):
        m=compute_metrics(dataset(lambda x,d:[0.]))
        self.assertIsNone(m['bidirectional_accuracy'])
        self.assertIsNone(m['bidirectional_repeatability'])
    def test_fit_correction(self):
        r=linear_regression([(x,1.001*x+.125) for x in (0.,50.,100.)])
        self.assertAlmostEqual(r['slope'],1.001)
        self.assertAlmostEqual(r['intercept'],.125)
        for x in (0.,50.,100.):
            self.assertAlmostEqual((1.001*x+.125-r['intercept'])/r['slope'],x)

if __name__=='__main__':
    unittest.main()
