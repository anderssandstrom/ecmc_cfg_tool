import unittest
from pathlib import Path
from ecmc_hardware_catalog import CATALOG,load_catalog,hardware_writable_pvs,hardware_pvs
from ecmc_ioc_tree import demo_ioc

@unittest.skipUnless(CATALOG.with_name('ecmc_hardware_generated.json').is_file(),'Generated snapshot not installed')
class FullCatalogTests(unittest.TestCase):
    def test_generated_coverage(self):
        c=load_catalog()
        self.assertGreaterEqual(len(c['generated_keys']),300)
        self.assertEqual(c['generated_source_commit'],'b5ab819ddea57c08a74c8c74e6dc1eb78384b5a0')
        demos=demo_ioc()['hardware']
        names={s['name'] for s in demos}
        self.assertGreaterEqual(len(names),250)
        self.assertEqual(len({s['id'] for s in demos}),len(demos))
        for s in demos:
            self.assertTrue(hardware_pvs('DEMO',0,int(s['id']),[s['panel'],s['name']]))
    def test_real_device_controls(self):
        for device,panel,count,prefix in [('EL2004','Ex20x4',4,'BO'),('EL2068','Ex2068',8,'BO'),
                                          ('EL2819','Ex2819',16,'BO'),('EL4132','Ex4xx2',2,'AO')]:
            pvs=hardware_writable_pvs('DEMO',0,1,[panel,device])
            self.assertEqual(set(pvs),{f'DEMO:m0s001-{prefix}{i:02d}' for i in range(1,count+1)},device)
