"""Shared, process-local serialization of ECMC command/readback transactions.

All cooperating clients in a Python process use the same lock per command
record. This cannot serialize other processes or external EPICS clients.
"""
from contextlib import contextmanager
import threading
from ecmc_epics import _proc_pv_for_readback, _join_prefix_pv

_registry_guard = threading.Lock()
_locks = {}


@contextmanager
def command_transaction(command_pv):
    record = _proc_pv_for_readback(command_pv)
    if not record:
        raise ValueError("Command PV is required")
    with _registry_guard:
        lock = _locks.setdefault(record, threading.RLock())
    with lock:
        yield


class CommandClient:
    def __init__(self, client, command_pv, query_pv):
        self.client = client
        self.command_pv = str(command_pv).strip()
        self.query_pv = str(query_pv).strip()
        if not self.command_pv:
            raise ValueError("Command PV is required")

    @classmethod
    def for_ioc(cls, client, prefix):
        return cls(client, _join_prefix_pv(prefix, "MCU-Cmd.AOUT"),
                   _join_prefix_pv(prefix, "MCU-Cmd.AINP"))

    def send(self, command, wait=True):
        if not str(command).strip():
            raise ValueError("Empty command")
        with command_transaction(self.command_pv):
            self.client.put(self.command_pv, command, wait=wait)

    def query(self, command, process=True):
        if not self.query_pv:
            raise ValueError("Query PV is required")
        with command_transaction(self.command_pv):
            self.send(command, wait=True)
            if process:
                self.client.put(_proc_pv_for_readback(self.query_pv), 1, wait=True)
            result = self.client.get(self.query_pv, as_string=True)
            if result is None:
                raise RuntimeError("Command readback unavailable")
            return str(result).strip()
