import unittest
from ecmc_ds402 import transition_word

class StateCommandTest(unittest.TestCase):
    def test_enable_steps(self):
        self.assertEqual(transition_word('Switch on disabled','Operation enabled',0x100),0x106)
        self.assertEqual(transition_word('Ready to switch on','Operation enabled',0x106),0x107)
        self.assertEqual(transition_word('Switched on','Operation enabled',0x107),0x10f)
        self.assertIsNone(transition_word('Operation enabled','Operation enabled',15))
    def test_other_states(self):
        self.assertEqual(transition_word('Operation enabled','Quick stop active',15),2)
        self.assertEqual(transition_word('Operation enabled','Switch on disabled',15),0)
        self.assertEqual(transition_word('Fault','Reset fault',0),128)
        self.assertEqual(transition_word('Fault','Reset fault',128),0)
        with self.assertRaises(ValueError):
            transition_word('Fault','Operation enabled',0)
