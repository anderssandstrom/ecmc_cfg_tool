import unittest
from ecmc_hardware_options import parse_options, allowed_signal, variant, legacy_names, read_options

class HardwareOptionsTest(unittest.TestCase):
    def test_parse(self):
        key='EL7062_OPT'
        opts={k:0 for k in legacy_names(key,2)}
        opts['02.Mode']=1
        raw=('v=1;Channels=2;'+ ';'.join(f'{k}={v}' for k,v in opts.items())).encode()+b'\0'
        self.assertEqual(parse_options(raw,key,2),opts)
    def test_filter(self):
        opts={'01.Mode':0,'02.Mode':1,'01.Encoder2':1,'01.TouchProbe':0,'02.TouchProbe':1}
        for s in ('Drv01-Spd','Drv02-PosCmd','Enc01-PosAct02','Tp02-Stat'):
            self.assertTrue(allowed_signal(s,'EL7062_OPT',2,opts),s)
        for s in ('Drv01-PosCmd','Drv02-Spd','Enc02-PosAct02','Tp01-Stat','Tp02-PosTimestamp01'):
            self.assertFalse(allowed_signal(s,'EL7062_OPT',2,opts),s)
    def test_unknown(self):
        self.assertFalse(allowed_signal('Drv01-Spd','EL7062_OPT',2,{}))
        self.assertTrue(allowed_signal('Drv01-Stat','EL7062_OPT',2,{}))
    def test_variant(self):
        self.assertEqual(variant(['EX72XX_OPT_2ch']),('EX72XX_OPT_2ch',2))
    def test_fallback(self):
        class Epics:
            def caget(self,*a,**k): return None
            def caget_many(self,names,**k): return [0]*len(names)
        opts,source=read_options('IOC:', 'EL7062_OPT',2,.1,Epics())
        self.assertEqual(source,'legacy configuration')
        self.assertEqual(opts['01.Mode'],0)
