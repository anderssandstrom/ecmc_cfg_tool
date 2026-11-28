import unittest
from ecmc_waveform_inspector import capture_samples,sample_statistics,MAX_SAMPLES

class InspectorTest(unittest.TestCase):
    def test_samples(self):
        self.assertEqual(capture_samples([1,2,3]),(1.,2.,3.))
        self.assertEqual(capture_samples(None),())
        self.assertEqual(capture_samples(range(MAX_SAMPLES+1)),())
    def test_stats(self):
        lo,hi,mean,rms,std=sample_statistics([-1,1])
        self.assertEqual((lo,hi,mean),(-1,1,0))
        self.assertAlmostEqual(rms,1)
        self.assertAlmostEqual(std,1)
        self.assertIsNone(sample_statistics([float('nan')]))
