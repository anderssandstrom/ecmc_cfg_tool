"""Offline Qt startup and report-export smoke tests.

All EPICS clients are replaced before constructing any live-facing window.
No SSH connection is made; the SDO browser uses its bundled demo.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from qt_compat import QtWidgets, QtGui
from ecmc_demo import DemoClient

ROOT = Path(__file__).resolve().parent

class QtSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_windows(self):
        cases = [
            ("ecmc_axis_cfg", "AxisYamlConfigWindow", dict(catalog_path=str(ROOT/"ecmc_commands.json"), yaml_path=str(ROOT/"axis_template.yaml"), mapping_path="", default_cmd_pv="DEMO:MCU-Cmd.AOUT", default_qry_pv="DEMO:MCU-Cmd.AINP", timeout=.1, axis_id="1", title_prefix="DEMO", demo=True)),
            ("ecmc_cntrl_qt", "CntrlWindow", dict(catalog_path=str(ROOT/"ecmc_commands_cntrl.json"), default_cmd_pv="DEMO:MCU-Cmd.AOUT", default_qry_pv="DEMO:MCU-Cmd.AINP", timeout=.1, default_axis_id="1", title_prefix="DEMO", demo=True)),
            ("ecmc_mtn_qt", "MotionWindow", dict(prefix="DEMO", axis_id="1", timeout=.1)),
            ("ecmc_iso230_qt", "Iso230Window", dict(prefix="DEMO", axis_id="1", timeout=.1, standalone=True)),
            ("ecmc_daq_qt", "DaqWindow", dict(default_prefix="DEMO", timeout=.1)),
            ("ecmc_rtlog_qt", "RtLogWindow", dict(prefix="DEMO", timeout=.1)),
            ("ecmc_sdo_qt", "SdoBrowserWindow", dict(demo=True)),
            ("ecmc_ioc_navigator", "IocNavigator", dict(prefix="DEMO", ssh_host="", ssh_host_pv="", timeout=.1, caqtdm_dir="", demo=True)),
            ("ecmc_stream_qt", "MainWindow", dict(catalog_path=str(ROOT/"ecmc_commands.json"), blocklist_path=str(ROOT/"ecmc_commands_blocklist_all.json"), default_cmd_pv="DEMO:MCU-Cmd.AOUT", default_qry_pv="DEMO:MCU-Cmd.AINP", timeout=.1)),
            ("ecmc_tuning_qt", "TuningWindow", dict(parent=None, client=DemoClient(), base="DEMO:X", demo=True)),
        ]
        with ExitStack() as stack:
            # Patch the class itself before modules import it, as well as
            # aliases in modules already imported by other tests.
            modules = {name: importlib.import_module(name) for name, _, _ in cases}
            modules["ecmc_ioc_tree"] = importlib.import_module("ecmc_ioc_tree")
            modules["ecmc_controller_live"] = importlib.import_module("ecmc_controller_live")
            for module in modules.values():
                if hasattr(module, "EpicsClient"):
                    stack.enter_context(patch.object(module, "EpicsClient", DemoClient))
            # Never permit CLI fallback or external program launches.
            stack.enter_context(patch("subprocess.Popen", side_effect=AssertionError("External process in offline test")))
            for name, klass, kwargs in cases:
                with self.subTest(app=name):
                    window = getattr(modules[name], klass)(**kwargs)
                    try:
                        window.show()
                        self.app.processEvents()
                        self.assertTrue(window.isVisible())
                    finally:
                        window.close()
                        self.app.processEvents()

    def test_report_png(self):
        from ecmc_iso230_qt import build_demo_report_files
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"iso230 report.md"
            build_demo_report_files(path)
            png = path.with_name("iso230 report_graph.png")
            self.assertTrue(png.is_file())
            image = QtGui.QImage(str(png))
            self.assertFalse(image.isNull())
            self.assertGreaterEqual(image.width(), 2000)
            report = path.read_text(encoding="utf-8")
            self.assertIn("![ISO230 positioning accuracy](iso230%20report_graph.png)", report)
            self.assertNotIn("<svg", report)

if __name__ == "__main__":
    unittest.main()
