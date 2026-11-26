import unittest
from unittest.mock import patch
import ecmc_plc_metadata as m

class MetadataTests(unittest.TestCase):
    def test_bytes_and_utf8_length(self):
        source = 'var café := 1;'
        raw = source.encode()
        payload = b'ECMCPLC1 42\n'+str(len(raw)).encode()+b'\n'+raw+b'\nL\tcaf\xc3\xa9\nG\tax1.actpos\n'
        self.assertEqual(m.parse_metadata(payload,42),(source,[('Local','café'),('Global','ax1.actpos')]))
        with self.assertRaises(ValueError): m.parse_metadata(payload,43)
        with self.assertRaises(ValueError): m.parse_metadata(b'ECMCPLC1 42\n999\nabc',42)
    def test_passive_handshake_and_ioc_limit(self):
        class CA:
            calls=[]
            token=0
            def caput(self,pv,value,**kw):
                self.calls.append(pv)
                if pv.endswith('DiagCmd'): self.token=value
                return 1
            def caget(self,pv,**kw):
                self.calls.append(pv)
                if pv.endswith('DiagStat'): return self.token
                return f'ECMCPLC1 {self.token}\n1\nx\nL\tx\n'.encode()
        ca=CA()
        with patch.object(m,'_next_request',{}):
            self.assertEqual(m.fetch_metadata('TEST:',0,epics_module=ca)[0],'x')
            with self.assertRaises(RuntimeError): m.fetch_metadata('TEST',1,epics_module=ca)
        self.assertEqual(ca.calls,['TEST:PLC00-DiagCmd','TEST:PLC00-DiagStat.PROC','TEST:PLC00-DiagStat','TEST:PLC00-DiagData.PROC','TEST:PLC00-DiagData'])
    def test_rejected_write_stops_reads(self):
        class CA:
            def caput(self,*a,**k):return -1
            def caget(self,*a,**k):raise AssertionError('must not read')
        with patch.object(m,'_next_request',{}):
            with self.assertRaises(RuntimeError):m.fetch_metadata('TEST',0,epics_module=CA())

if __name__=='__main__':unittest.main()
