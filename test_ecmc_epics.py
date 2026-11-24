"""Offline EPICS backend security and failure handling tests."""
import unittest
from unittest.mock import Mock, patch
from ecmc_epics import EpicsClient


class EpicsClientTests(unittest.TestCase):
    def client(self, backend="pyepics"):
        client = EpicsClient.__new__(EpicsClient)
        client.timeout = 1.0
        client.backend = backend
        client._epics = Mock()
        client._caput_bin = "caput"
        client._caget_bin = "caget"
        return client

    def test_failed_put(self):
        client = self.client()
        for value in (None, False, 0, -1):
            client._epics.caput.return_value = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                client.put("IOC:X", 1)

    def test_successful_put(self):
        client = self.client()
        client._epics.caput.return_value = 1
        client.put("IOC:X", 1)
        client._epics.caput.assert_called_once_with("IOC:X", 1, wait=True, timeout=1.0)

    def test_invalid_pvs(self):
        client = self.client()
        for pv in ("", "-h", "IOC: X", "IOC:\nX"):
            with self.subTest(pv=pv), self.assertRaises(ValueError):
                client.get(pv)
            with self.assertRaises(ValueError):
                client.put(pv, 1)
        client._epics.caput.assert_not_called()

    def test_invalid_timeout(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                EpicsClient(timeout)

    @patch("ecmc_epics.subprocess.run")
    def test_cli_timeout(self, run):
        client = self.client("cli")
        run.return_value = Mock(returncode=0, stdout="1", stderr="")
        client.put("IOC:X", 1)
        self.assertEqual(run.call_args.kwargs["timeout"], 3.0)
        client.get("IOC:X")
        self.assertEqual(run.call_args.kwargs["timeout"], 3.0)


if __name__ == "__main__":
    unittest.main()
