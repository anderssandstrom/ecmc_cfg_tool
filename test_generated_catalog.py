import tempfile
import unittest
from pathlib import Path
from ecmc_generated_catalog import merge_generated, demo_definitions
from ecmc_hardware_inventory import inventory

class GeneratedCatalogTests(unittest.TestCase):
    def test_merge_preserves_curated_and_restricts_new_writes(self):
        curated={'panels':{'EL2068':['BO01']},'overview':{'EL2068':['BO01']},
                 'writable':{'EL2068':{'BO01':'caToggleButton'}}}
        generated={'source_commit':'test','panels':{'EL2068':['BO01','BO02'],
                   'EL9999':['BO01','BO01-RB','BO02']},
                   'database':{'EL9999':['BO01','BO01-RB']},
                   'writable':{'EL2068':{'BO02':'caToggleButton'},
                               'EL9999':{'BO01':'caToggleButton','BO02':'caToggleButton','BO01-RB':'caToggleButton'}},
                   'hardware_inventory':[{'definition':'EL9999','types':['EL9999'],'resolved_panel':'Ex9999'}]}
        result=merge_generated(curated,generated)
        self.assertEqual(result['panels']['EL2068'],['BO01'])
        self.assertEqual(result['writable']['EL2068'],{'BO01':'caToggleButton'})
        self.assertEqual(result['writable']['EL9999'],{'BO01':'caToggleButton'})
        self.assertEqual(list(demo_definitions(result)),['EL9999'])
        from ecmc_hardware_catalog import hardware_writable_pvs
        result['panels']['Ex9999']=['BO01','BO02']
        self.assertEqual(hardware_writable_pvs('IOC',0,1,['Ex9999','EL9999'],result),
                         {'IOC:m0s001-BO01':'caToggleButton'})

    def test_scalar_analog_and_explicit_trigger(self):
        generated={'panels':{'Device':['AO01','AO01-RB','Drv01-Cmd','Drv01-TrgDS402Ena']},
                   'database':{'Device':['AO01','AO01-RB','Drv01-Cmd','Drv01-TrgDS402Ena']},
                   'writable':{'Device':{'AO01':'caTextEntry','AO01-RB':'caTextEntry','Drv01-Cmd':'caByteController'}},
                   'commands':{'Device':{'Drv01-Cmd':{'release':128},'Drv01-TrgDS402Ena':{'release':1}}}}
        result=merge_generated({'panels':{}},generated)
        self.assertEqual(result['writable']['Device'],{'AO01':'caTextEntry'})
        self.assertEqual(list(result['commands']['Device']),['Drv01-TrgDS402Ena'])

    def test_static_inherited_panel(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            hw=root/'hardware'
            hw.mkdir()
            (hw/'ecmcEL1008.cmd').write_text('epicsEnvSet("ECMC_EC_HWTYPE" "EL1008")\n${SCRIPTEXEC} ${ecmccfg_DIR}ecmcEX1008.cmd')
            (hw/'ecmcEX1008.cmd').write_text('epicsEnvSet("ECMC_HW_PANEL" "Ex10x8")')
            row=next(e for e in inventory(root) if e['definition']=='EL1008')
            self.assertEqual(row['panels'],['Ex10x8'])
            self.assertEqual(row['panel_source'],'inherited')
