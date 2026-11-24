"""Offline tests for the shared ECMC command transaction contract."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from ecmc_command_client import CommandClient, command_transaction


class FakeTransport:
    def __init__(self):
        self.commands = {}
        self.calls = []
        self.fail = False

    def put(self, pv, value, wait=True):
        self.calls.append((pv, value, wait))
        if self.fail:
            raise RuntimeError("transport failure")
        if pv.endswith(".AOUT"):
            self.commands[pv.rsplit(".", 1)[0]] = value

    def get(self, pv, as_string=True):
        self.calls.append((pv, "get", as_string))
        return self.commands.get(pv.rsplit(".", 1)[0])


class CommandTests(unittest.TestCase):
    def test_order(self):
        transport = FakeTransport()
        client = CommandClient.for_ioc(transport, "TEST")
        self.assertEqual(client.query("GetAxisJerk(1)"), "GetAxisJerk(1)")
        self.assertEqual(transport.calls, [
            ("TEST:MCU-Cmd.AOUT", "GetAxisJerk(1)", True),
            ("TEST:MCU-Cmd.PROC", 1, True),
            ("TEST:MCU-Cmd.AINP", "get", True),
        ])

    def test_without_process(self):
        transport = FakeTransport()
        client = CommandClient.for_ioc(transport, "TEST")
        self.assertEqual(client.query("read", process=False), "read")
        self.assertEqual(len(transport.calls), 2)

    def test_release_after_failure(self):
        transport = FakeTransport()
        client = CommandClient.for_ioc(transport, "TEST")
        transport.fail = True
        with self.assertRaises(RuntimeError):
            client.query("failed")
        transport.fail = False
        self.assertEqual(client.query("next"), "next")

    def test_same_record_serializes_distinct_clients(self):
        transport = FakeTransport()
        entered = threading.Event()
        release = threading.Event()
        attempting = threading.Event()
        original = transport.put

        def blocked_put(pv, value, wait=True):
            original(pv, value, wait)
            if value == "first":
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("test synchronization timeout")
        transport.put = blocked_put
        first = CommandClient.for_ioc(transport, "TEST")
        second = CommandClient.for_ioc(transport, "TEST")

        def run_second():
            attempting.set()
            return second.query("second")

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(first.query, "first")
            try:
                self.assertTrue(entered.wait(2))
                b = pool.submit(run_second)
                self.assertTrue(attempting.wait(2))
                self.assertFalse(b.done())
                self.assertEqual(transport.calls, [
                    ("TEST:MCU-Cmd.AOUT", "first", True)])
            finally:
                release.set()
            self.assertEqual(a.result(timeout=2), "first")
            self.assertEqual(b.result(timeout=2), "second")
        self.assertEqual([c[0].rsplit(".", 1)[1] for c in transport.calls],
                         ["AOUT", "PROC", "AINP", "AOUT", "PROC", "AINP"])

    def test_independent_records(self):
        transport = FakeTransport()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with command_transaction("FIRST:MCU-Cmd.AOUT"):
                future = pool.submit(CommandClient.for_ioc(transport, "SECOND").query, "second")
                self.assertEqual(future.result(timeout=2), "second")

    def test_reentrant_record_lock(self):
        transport = FakeTransport()
        with command_transaction("TEST:MCU-Cmd.PROC"):
            self.assertEqual(CommandClient.for_ioc(transport, "TEST").query("nested"), "nested")


if __name__ == "__main__":
    unittest.main()
