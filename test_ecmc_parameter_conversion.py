import unittest
import ecmc_parameter_conversion as u

class ConversionTests(unittest.TestCase):
    def test_pid(self):
        for hz,ki,kd in [(500,.1,25),(1000,.05,50),(2000,.025,100)]:
            self.assertAlmostEqual(float(u.to_runtime('controller.TiMs',200,hz,10)),ki)
            self.assertAlmostEqual(float(u.to_runtime('controller.TdMs',5,hz,10)),kd)
            self.assertAlmostEqual(float(u.from_runtime('controller.TiMs',ki,hz,10)),200)
            self.assertAlmostEqual(float(u.from_runtime('controller.TdMs',kd,hz,10)),5)
        self.assertEqual(u.to_runtime('controller.inner.TiMs',0,1000,5),'0')
        with self.assertRaises(ValueError):
            u.from_runtime('controller.TiMs',1,1000,0)

    def test_timing(self):
        self.assertEqual(u.to_runtime('monitoring.target.timeMs',5,500),'3')
        self.assertEqual(u.to_runtime('encoder.filter.velocity.timeMs',0,500),'1')
        self.assertEqual(u.to_runtime('encoder.delayComp.timeMs',-1.5,500),'-0.75')
        with self.assertRaises(ValueError):
            u.to_runtime('controller.deadband.timeMs',1,0)

    def test_export(self):
        out,_=u.export_values({'controller.Ki':'.05'},{'controller.Kp':'10'},1000,True,False)
        self.assertEqual(out,{'controller.Kp':'10','controller.TiMs':'200'})

if __name__ == '__main__':
    unittest.main()
