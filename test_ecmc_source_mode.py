import unittest
from ecmc_source_mode import SOURCE_COMMANDS,source_label
class SourceTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(source_label(0),"Internal")
        self.assertEqual(source_label(1),"PLC")
        self.assertEqual(source_label(None),"—")
    def test_commands(self):
        self.assertEqual(SOURCE_COMMANDS["-TrjSrcTyp-RB"],"-TrjSrcTyp-Cmd")
        self.assertEqual(SOURCE_COMMANDS["-EncSrcTyp-RB"],"-EncSrcTyp-Cmd")
