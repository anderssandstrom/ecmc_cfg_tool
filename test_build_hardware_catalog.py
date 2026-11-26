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

if __name__ == "__main__":
    unittest.main()
