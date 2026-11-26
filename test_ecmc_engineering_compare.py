import unittest
from ecmc_engineering_compare import domain_signals,counter_change,compare_snapshots
class EngineeringCompareTests(unittest.TestCase):
    def test_domains(self):
        self.assertIn((1,"IOC:m2-Dom1-WC"),domain_signals("IOC",2,2))
        self.assertEqual(domain_signals("IOC",0,None),())
    def test_counter(self):
        self.assertEqual(counter_change(5,9),4)
        self.assertIsNone(counter_change(9,2))
        self.assertIsNone(counter_change(None,2))
    def test_compare(self):
        before={"axes":[{"id":"1","name":"A"}]}
        after={"axes":[{"id":"1","name":"B"}]}
        self.assertEqual(compare_snapshots(before,after),[(("axes","1","name"),"A","B","Changed")])
