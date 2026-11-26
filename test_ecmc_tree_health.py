import unittest
from ecmc_tree_health import object_prefix,health_pvs,health_status,is_problem

class TestTreeHealth(unittest.TestCase):
    def test_prefix(self):
        self.assertEqual(object_prefix("IOC:",{"kind":"plc","id":"2"}),"IOC:PLC02")
        self.assertEqual(health_pvs("IOC",{"kind":"cpp_logic","id":"1"}),("IOC:CPP01-StatWord","IOC:CPP01-RateMsAct"))
    def test_status(self):
        self.assertEqual(health_status("plc",1,1),"Error 1")
        self.assertEqual(health_status("state_machine",1,1),"Timeout")
        self.assertEqual(health_status("hardware",1,1),"OP")
        self.assertEqual(health_status("cpp_logic",0,1),"Connected")
        self.assertEqual(health_status("axis",None,1),"Unknown")
        self.assertTrue(is_problem("Timeout"))
