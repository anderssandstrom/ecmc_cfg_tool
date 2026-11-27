import tempfile
import unittest
from pathlib import Path
from build_hardware_catalog import extract_catalog
from ecmc_hardware_catalog import hardware_pvs, hardware_writable_pvs

class DatabaseCatalogTest(unittest.TestCase):
    def test_database_only_and_specialized(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'qt').mkdir()
            db = root / 'db' / 'vendor'
            db.mkdir(parents=True)
            (db / 'input.template').write_text(
                'record(bi,"$(ECMC_P)${KEY=BI}${CH_ID}"){}\n'
                '# record(bi,"${ECMC_P}Hidden"){}\n'
                'record(ai,"${ECMC_P}${UNKNOWN}"){}\n')
            (db / 'ecmcNEW.substitutions').write_text(
                'file "input.template" { pattern { CH_ID } { 01 } { 02 } }')
            (db / 'ecmcEL7062.substitutions').write_text(
                'file "input.template" { { CH_ID="03", KEY="X" } }')
            (root / 'qt' / 'ecmcEL7062.ui').write_text(
                '<ui><widget class="caTextEntry"><property name="channel">'
                '<string>$(IOC):m$(MasterID)s$(SlaveID)-Cmd</string></property></widget></ui>')
            cat = extract_catalog(root, 'fixture')
            self.assertEqual(cat['database']['NEW'], ['BI01', 'BI02'])
            self.assertIn('X03', cat['panels']['EL7062'])
            self.assertEqual(cat['overview']['EL7062'], ['Cmd'])
            self.assertEqual(hardware_writable_pvs('IOC', 0, 1, ['NEW'], cat), {})
            rows = dict(hardware_pvs('IOC', 0, 1, ['EL7062','NEW'], cat))
            self.assertEqual(rows['BI02'], 'IOC:m0s001-BI02')
