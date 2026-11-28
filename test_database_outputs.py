import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from ecmc_hardware_database import merge_database
from ecmc_generated_catalog import merge_generated

class DatabaseOutputTests(unittest.TestCase):
    def test_verified_outputs_only(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/'db'/'vendor'
            db.mkdir(parents=True)
            (db/'ecmc_binaryOutput-chX.template').write_text('record(bo,"${ECMC_P}BO${CH_ID}"){}\nrecord(bi,"${ECMC_P}BO${CH_ID}-RB"){}')
            (db/'ecmc_binaryOutputArray-chX.template').write_text('record(mbboDirect,"${ECMC_P}BO${CH_ID}-Arr"){}\nrecord(mbbiDirect,"${ECMC_P}BO${CH_ID}-Arr-RB"){}')
            (db/'other.template').write_text('record(bi,"${ECMC_P}BO99"){}')
            (db/'ecmcTest.substitutions').write_text('file "ecmc_binaryOutput-chX.template" { { CH_ID=01 } }\nfile "ecmc_binaryOutputArray-chX.template" { { CH_ID=02 } }\nfile "other.template" { { CH_ID=99 } }')
            generated=merge_database({'panels':{}},root)
            result=merge_generated({'panels':{'Test':['BO99']}},generated)
            self.assertEqual(result['writable']['Test'],{'BO01':'caToggleButton','BO02-Arr':'caByteController'})
            self.assertIn('BO02-Arr-RB',result['overview']['Test'])
            self.assertNotIn('BO99',result['writable']['Test'])
