import unittest
from ecmc_sdo_compare import compare_snapshots, decode_word

class SdoCompareTest(unittest.TestCase):
    def row(self,value,index='0x8000',typ='uint16'):
        return dict(index=index,subindex='0x01',type=typ,bits='16 bit',value=value)
    def test_integer_formats(self):
        self.assertEqual(compare_snapshots({'entries':[self.row('0x10')]},{'entries':[self.row('16')]}),[])
    def test_changed_and_missing(self):
        a={'entries':[self.row('1'),self.row('2','0x8001')]}
        b={'entries':[self.row('3')]}
        self.assertEqual([x['state'] for x in compare_snapshots(a,b)],['Changed','Not read in current'])
    def test_type_mismatch(self):
        self.assertEqual(compare_snapshots({'entries':[self.row('1')]},{'entries':[self.row('1',typ='int16')]})[0]['state'],'Type mismatch')
    def test_ds402(self):
        self.assertIn('Bit 3: Fault: 1',decode_word('0x6041','0x00','8',16,'uint16'))
