import tempfile
import unittest
from pathlib import Path
from build_hardware_catalog import extract_catalog

class BuilderTest(unittest.TestCase):
    def test_nested_channel_macro(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "ecmcEL7062.ui").write_text('<ui><widget class="caInclude"><property name="filename"><string>ecmcEL7062_child.ui</string></property><property name="macro"><string>CH=2</string></property></widget></ui>')
            (p / "ecmcEL7062_child.ui").write_text('<ui><widget class="caLineEdit"><property name="channel"><string>$(IOC):m$(MasterID)s$(SlaveID)-Enc0$(CH)-PosAct</string></property></widget></ui>')
            self.assertEqual(extract_catalog(p, "test")["panels"]["EL7062"], ["Enc02-PosAct"])

    def test_expert_separated(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "ecmcEL7062.ui").write_text('<ui><widget class="caLineEdit"><property name="channel"><string>$(IOC):m$(MasterID)s$(SlaveID)-Online</string></property><widget class="caRelatedDisplay"><property name="files"><string>expert.ui;expert.ui</string></property><property name="args"><string>ID=01;ID=02</string></property></widget></widget></ui>')
            (p / "expert.ui").write_text('<ui><widget class="caTextEntry"><property name="channel"><string>$(IOC):m$(MasterID)s$(SlaveID)-Drv$(ID)-Cmd</string></property></widget></ui>')
            result = extract_catalog(p, "test")
            self.assertEqual(result["overview"]["EL7062"], ["Online"])
            self.assertEqual([e["signals"] for e in result["experts"]["EL7062"]], [["Drv01-Cmd"], ["Drv02-Cmd"]])
            self.assertEqual(result["writable"]["EL7062"]["Drv02-Cmd"], "caTextEntry")

    def test_release_commands_and_conflicts(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            def button(suffix, label, release, press=""):
                return (f'<widget class="caMessageButton"><property name="channel"><string>$(IOC):m$(MasterID)s$(SlaveID)-{suffix}</string></property>'
                        f'<property name="label"><string>{label}</string></property>'
                        f'<property name="releaseMessage"><string>{release}</string></property>'
                        f'<property name="pressMessage"><string>{press}</string></property></widget>')
            (p / "ecmcEL70x1.ui").write_text('<ui><widget>' +
                button("Drv01-RstCmd", "Reset", "1") +
                button("Drv01-RstCmd", "Reset", "1") +
                button("Pulse", "Pulse", "0", "1") +
                button("Ambiguous", "Enable", "1") +
                button("Ambiguous", "Disable", "0") + '</widget></ui>')
            result = extract_catalog(p, "test", "abc")
            self.assertEqual(result["source_commit"], "abc")
            self.assertEqual(result["commands"]["EL70x1"],
                             {"Drv01-RstCmd": {"label": "Reset", "release": 1}})

if __name__ == "__main__":
    unittest.main()
