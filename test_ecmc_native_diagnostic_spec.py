import unittest
from ecmc_native_diagnostic_spec import diagnostic_rows

class NativeDiagnosticSpecTest(unittest.TestCase):
    def test_plc(self):
        rows=diagnostic_rows("IOC:", "plc", 3)
        self.assertIn(("Scan time","IOC:PLC03-ScanTime","seconds"),rows)
        self.assertIn(("Enabled","IOC:PLC03-EnaCmd","switch"),rows)

    def test_state_machine(self):
        rows=diagnostic_rows("IOC","state_machine",12)
        self.assertIn(("Current phase","IOC:SM12-StatTxt","text"),rows)
        self.assertIn(("Requested state","IOC:SM12-StateCmd","enum"),rows)

    def test_thread(self):
        rows=diagnostic_rows("IOC","thread")
        self.assertIn(("Latency maximum","IOC:MCU-ThdLatMax","ns"),rows)
        self.assertFalse(any(mode in ("switch","enum") for _,_,mode in rows))

    def test_invalid(self):
        for args in (("", "thread"),("IOC","other"),("IOC","plc",-1)):
            with self.assertRaises(ValueError):
                diagnostic_rows(*args)
