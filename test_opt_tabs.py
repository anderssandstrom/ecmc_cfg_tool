import unittest
from ecmc_hardware_options import option_pages, allowed_signal, demo_options

class OptionTabsTest(unittest.TestCase):
    def test_partition_and_order(self):
        names=['Drv01-Cmd','Enc01-PosAct','Tp01-Stat','Drv01-BI01','Drv01-TuneCmd','Drv01-TrqAct','CfgMode','HWType']
        pages=option_pages([(s,s) for s in names])
        self.assertEqual([k for k,_ in pages],['Drive / encoder','TP','Inputs','Diagnostics','Tune','Config'])
        self.assertEqual(dict(pages)['TP'],[('Tp01-Stat','Tp01-Stat')])
        self.assertEqual(len([s for _,rows in pages for s,_ in rows]),len(names)-1)
    def test_disabled_touch_probe_has_no_tab(self):
        key='EL7062_OPT'
        opts=demo_options(key,2)
        opts['01.TouchProbe']=opts['02.TouchProbe']=0
        names=['Drv01-Cmd','Tp01-Stat','Tp02-Stat']
        pairs=[(s,s) for s in names if allowed_signal(s,key,2,opts)]
        self.assertNotIn('TP',dict(option_pages(pairs)))
