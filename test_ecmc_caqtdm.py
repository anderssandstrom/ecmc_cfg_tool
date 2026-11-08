import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ecmc_caqtdm as launcher


class CaqtdmTests(unittest.TestCase):
    def test_mac_application_fallback(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(launcher.sys, 'platform', 'darwin'), \
                patch.object(launcher.shutil, 'which', return_value=None), \
                patch.object(Path, 'is_file', return_value=True), patch.object(os, 'access', return_value=True):
            self.assertEqual(launcher.caqtdm_executable(), '/Applications/caQtDM.app/Contents/MacOS/caQtDM')

    def test_path_installation_and_explicit_executable(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(launcher.shutil, 'which', return_value='/usr/bin/caqtdm'):
            self.assertEqual(launcher.caqtdm_executable(), '/usr/bin/caqtdm')
        with patch.dict(os.environ, {'CAQTDM_EXECUTABLE': '/custom/caQtDM'}, clear=True), \
                patch.object(launcher.shutil, 'which', return_value='/custom/caQtDM') as which:
            self.assertEqual(launcher.caqtdm_executable(), '/custom/caQtDM')
            which.assert_called_once_with('/custom/caQtDM')

    def test_installed_local_and_override_panels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            local, installed, override = [root / name for name in ('local qt', 'installed', 'override')]
            for directory in (local, installed, override):
                directory.mkdir()
                (directory / 'ecmcMain.ui').write_text('<ui/>')
            with patch.object(launcher, 'REPOSITORY_PANELS', local), \
                    patch.object(launcher, 'caqtdm_executable', return_value='/Applications/caQtDM'), \
                    patch.object(launcher.subprocess, 'Popen') as popen, \
                    patch.dict(os.environ, {'EPICS_CA_ADDR_LIST': '127.0.0.1'}, clear=True):
                launcher.launch_caqtdm('ecmcMain.ui', 'IOC=SIMIOC')
                self.assertEqual(popen.call_args.args[0][-1], str(local / 'ecmcMain.ui'))
                os.environ['EPICS_DISPLAY_PATH'] = str(installed)
                launcher.launch_caqtdm('ecmcMain.ui', 'IOC=SIMIOC')
                self.assertEqual(popen.call_args.args[0][-1], str(installed / 'ecmcMain.ui'))
                launcher.launch_caqtdm('ecmcMain.ui', 'IOC=SIMIOC', override)
                self.assertEqual(popen.call_args.args[0], ['/Applications/caQtDM', '-macro', 'IOC=SIMIOC', str(override / 'ecmcMain.ui')])
                environment = popen.call_args.kwargs['env']
                self.assertIn(str(local), environment['EPICS_DISPLAY_PATH'])
                self.assertIn(str(installed), environment['EPICS_DISPLAY_PATH'])
                self.assertEqual(environment['CAQTDM_DISPLAY_PATH'], environment['EPICS_DISPLAY_PATH'])
                self.assertEqual(environment['EPICS_CA_ADDR_LIST'], '127.0.0.1')
                os.environ['CAQTDM_DISPLAY_PATH'] = str(override)
                launcher.launch_caqtdm('ecmcMain.ui', 'IOC=SIMIOC')
                self.assertEqual(popen.call_args.args[0][-1], str(override / 'ecmcMain.ui'))
                with self.assertRaisesRegex(FileNotFoundError, 'Panel missing.ui not found'):
                    launcher.launch_caqtdm('missing.ui', '')


if __name__ == '__main__':
    unittest.main()
