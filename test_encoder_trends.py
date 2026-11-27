import unittest
from ecmc_encoder_overview import EncoderOverview

class EncoderTrendTest(unittest.TestCase):
    def test_combined(self):
        calls = []
        class Fake:
            count = 2
            demo = False
            on_plot = staticmethod(lambda names, **kw: calls.append(names))
            _name = lambda self, i, field: f"IOC:AX-Enc0{i}-{field}"
        EncoderOverview._trend_all(Fake(), "PosAct")
        EncoderOverview._trend_all(Fake(), "VelAct")
        self.assertEqual(calls, [["IOC:AX-Enc01-PosAct", "IOC:AX-Enc02-PosAct"],
                                 ["IOC:AX-Enc01-VelAct", "IOC:AX-Enc02-VelAct"]])
