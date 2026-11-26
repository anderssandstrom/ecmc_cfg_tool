import tempfile
import unittest
from pathlib import Path
from ecmc_incident import build_incident,save_incident,load_incident

class IncidentTests(unittest.TestCase):
    def test_capture_roundtrip_and_scope(self):
        health={'IOC:X':{'pvs':['IOC:X','IOC:Y'],'status':'Unknown'}}
        b=build_incident({'prefix':'IOC'},health,
            {'IOC:X':{'connected':True,'value':2,'received':95},
             'IOC:Y':{'connected':False,'value':9}},
            [('t','IOC','x','a','b'),('t','OTHER','x','a','b')],
            {'IOC:X':[(1,0),(99,2)],'OTHER:X':[(99,3)]},now=100)
        self.assertEqual(b['pvs']['IOC:X']['age_s'],5)
        self.assertIsNone(b['pvs']['IOC:Y']['value'])
        self.assertEqual(len(b['events']),1)
        self.assertEqual(b['trends'],{'IOC:X':[[ -99,0],[-1,2]]})
        health.clear()
        self.assertTrue(b['health'])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'incident.json'
            save_incident(p,b)
            self.assertEqual(load_incident(p),b)

    def test_legacy_and_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'incident.json'
            p.write_text('{"discovery":{"prefix":"IOC"}}')
            self.assertEqual(load_incident(p)['discovery']['prefix'],'IOC')
            p.write_text('{"format":"ecmc-incident","version":2,"discovery":{}}')
            with self.assertRaises(ValueError):
                load_incident(p)

if __name__=='__main__':
    unittest.main()
