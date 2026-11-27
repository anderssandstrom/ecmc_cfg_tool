import tempfile
import unittest
from pathlib import Path
from ecmc_hardware_inventory import augment_catalog, coverage_summary

class InventoryTests(unittest.TestCase):
    def test_explicit_alias_and_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            hw=root/'hardware'
            hw.mkdir()
            (hw/'ecmcEL1234.cmd').write_text('epicsEnvSet("ECMC_EC_HWTYPE" "EL1234")\nepicsEnvSet("ECMC_HW_PANEL" "Ex1234")')
            (hw/'ecmcEL9999.cmd').write_text('epicsEnvSet("ECMC_EC_HWTYPE" "EL9999")\nepicsEnvSet("ECMC_HW_PANEL" "${unknown}")')
            c={'panels':{'Ex1234':['BO01']},'overview':{'Ex1234':['BO01']},'writable':{'Ex1234':{'BO01':'caToggleButton'}},'commands':{},'experts':{},'database':{'EL1234':['BO01-RB']}}
            augment_catalog(c,root)
            self.assertEqual(c['panels']['EL1234'],['BO01','BO01-RB'])
            self.assertEqual(c['writable']['EL1234'],{'BO01':'caToggleButton'})
            self.assertEqual(coverage_summary(c)['generic_only'],1)
