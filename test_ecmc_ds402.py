import unittest
from ecmc_ds402 import decode_state, edit_controlword, bit_label

class DS402Test(unittest.TestCase):
    def test_states(self):
        for word, state in [(0, "Not ready to switch on"), (0x40, "Switch on disabled"),
                            (0x21, "Ready to switch on"), (0x23, "Switched on"),
                            (0x27, "Operation enabled"), (7, "Quick stop active"),
                            (15, "Fault reaction active"), (8, "Fault"),
                            (0x1227, "Operation enabled")]:
            self.assertEqual(decode_state(word), state)
    def test_invalid(self):
        self.assertEqual(decode_state(None), "Unavailable")
        self.assertEqual(decode_state(-1), "Unavailable")
        self.assertEqual(decode_state(0x006F), "Unknown state")

    def test_controlword_edit(self):
        self.assertEqual(edit_controlword(0x8006, {0: True, 3: True}), 0x800F)
        self.assertEqual(edit_controlword(0x008F, {7: False}), 0x000F)
        with self.assertRaises(ValueError):
            edit_controlword(0, {15: True})
        with self.assertRaises(ValueError):
            edit_controlword(None, {0: True})
        self.assertEqual(bit_label(3, True), "Fault")
