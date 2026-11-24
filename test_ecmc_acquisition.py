"""Offline regression tests for shared acquisition and diagnostic presets."""
import csv
import tempfile
import unittest
from pathlib import Path
from ecmc_acquisition import bounded_series, append_sample, sanitize_samples, export_event_csv, finite_value
from ecmc_diagnostic_presets import axis_diagnostic_pvs


class AcquisitionTests(unittest.TestCase):
    def test_bounded_buffer(self):
        q = bounded_series(["x"], 2)["x"]
        for i in range(3):
            append_sample(q, i, i * 2)
        self.assertEqual(list(q), [(1., 2.), (2., 4.)])

    def test_invalid_and_gap(self):
        q = bounded_series(["x"], 5)["x"]
        self.assertFalse(append_sample(q, float("nan"), 1))
        self.assertFalse(append_sample(q, 1, float("inf")))
        self.assertTrue(append_sample(q, 2, None, keep_gap=True))
        self.assertEqual(list(q), [(2., None)])

    def test_time_window(self):
        q = bounded_series(["x"], 10)["x"]
        for i in range(5):
            append_sample(q, i, i, cutoff=i-2)
        self.assertEqual(list(q), [(2., 2.), (3., 3.), (4., 4.)])

    def test_sanitize(self):
        self.assertEqual(sanitize_samples([(2, 3), (1, 2), (2, 4),
                                          ("bad", 5), (3, float("nan"))]),
                         [(1., 2.), (2., 4.)])

    def test_event_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.csv"
            count = export_event_csv(path, {"A": [(10., 2.), (12., None)],
                                           "B": [(11., 3.)]})
            self.assertEqual(count, 3)
            with path.open(newline="") as file:
                self.assertEqual(list(csv.reader(file)), [
                    ["elapsed_s", "pv", "value"],
                    ["0.000000", "A", "2.0"],
                    ["1.000000", "B", "3.0"],
                    ["2.000000", "A", ""]])

    def test_finite(self):
        for value in (None, "bad", float("inf"), float("nan")):
            self.assertIsNone(finite_value(value))
        self.assertEqual(finite_value("1.25"), 1.25)


class DiagnosticPresetTests(unittest.TestCase):
    def test_position(self):
        self.assertEqual(axis_diagnostic_pvs("IOC:X"),
                         ["IOC:X-PosAct", "IOC:X-PosSet", "IOC:X-PosErr"])

    def test_controller(self):
        pvs = axis_diagnostic_pvs("IOC:X", "controller")
        self.assertIn("IOC:X-Busy", pvs)
        self.assertIn("IOC:X-ErrId", pvs)
        self.assertIn("IOC:X-VelAct", pvs)
        self.assertEqual(len(pvs), len(set(pvs)))

    def test_reference_and_duplicate(self):
        self.assertEqual(axis_diagnostic_pvs("IOC:X", extra=["REF:PV", "IOC:X-PosAct", ""]),
                         ["IOC:X-PosAct", "IOC:X-PosSet", "IOC:X-PosErr", "REF:PV"])

    def test_empty_base(self):
        self.assertEqual(axis_diagnostic_pvs(""), [])
        self.assertEqual(axis_diagnostic_pvs("", extra=["REF:PV"]), ["REF:PV"])

    def test_invalid_profile(self):
        with self.assertRaises(ValueError):
            axis_diagnostic_pvs("IOC:X", "unknown")


if __name__ == "__main__":
    unittest.main()
