"""Explicit, access-checked scalar hardware writes via Channel Access."""
import math
import re
from qt_compat import QtCore


def parse_write_value(text, kind):
    text = str(text).strip()
    try:
        value = int(text, 16) if re.fullmatch(r"[+-]?0[xX][0-9a-fA-F]+", text) else (
            int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text))
    except (ValueError, OverflowError):
        raise ValueError("Enter a finite decimal or hexadecimal number.") from None
    if not math.isfinite(value):
        raise ValueError("Enter a finite number.")
    if kind == "caToggleButton" and value not in (0, 1):
        raise ValueError("Toggle values must be 0 or 1.")
    if kind in ("caMenu", "caByteController") and (not isinstance(value, int) or value < 0):
        raise ValueError("Enter a non-negative integer.")
    return value


class WriteSignals(QtCore.QObject):
    finished = QtCore.Signal(str) if hasattr(QtCore, "Signal") else QtCore.pyqtSignal(str)


class WriteTask(QtCore.QRunnable):
    def __init__(self, name, value, timeout, kind, expected=None):
        super().__init__()
        self.name, self.value, self.timeout, self.kind = name, value, timeout, kind
        self.expected = expected
        self.signals = WriteSignals()

    def run(self):
        pv = None
        error = ""
        try:
            import epics
            pv = epics.PV(self.name, auto_monitor=False)
            if not pv.wait_for_connection(timeout=self.timeout):
                raise RuntimeError("PV is disconnected.")
            if not pv.write_access:
                raise RuntimeError("IOC denies write access.")
            if pv.count != 1:
                raise RuntimeError("Only scalar writes are supported.")
            if self.kind == "caMenu":
                pv.get_ctrlvars(timeout=self.timeout)
                choices = pv.enum_strs
                if not choices or not 0 <= self.value < len(choices):
                    raise RuntimeError("Invalid enum index.")
            if self.expected is not None:
                current = pv.get(use_monitor=False, timeout=self.timeout)
                if current is None or current != self.expected:
                    raise RuntimeError("PV changed since the displayed value. No write issued; try again after refresh.")
            result = pv.put(self.value, wait=True, timeout=self.timeout)
            if result is None or result <= 0:
                raise RuntimeError("CA put did not confirm completion; check the IOC before retrying.")
        except Exception as ex:
            error = str(ex)
        finally:
            if pv is not None:
                try:
                    pv.disconnect()
                except Exception:
                    pass
            self.signals.finished.emit(error)

class EnumSignals(QtCore.QObject):
    finished = QtCore.Signal(object, str) if hasattr(QtCore, "Signal") else QtCore.pyqtSignal(object, str)

class EnumTask(QtCore.QRunnable):
    def __init__(self, name, timeout):
        super().__init__()
        self.name, self.timeout = name, timeout
        self.signals = EnumSignals()

    def run(self):
        pv = None
        choices, error = [], ""
        try:
            import epics
            pv = epics.PV(self.name, auto_monitor=False)
            if not pv.wait_for_connection(timeout=self.timeout) or not pv.write_access:
                raise RuntimeError("PV disconnected or write access denied.")
            pv.get_ctrlvars(timeout=self.timeout)
            choices = list(pv.enum_strs or [])
            if not choices:
                raise RuntimeError("IOC did not provide enum labels.")
        except Exception as ex:
            error = str(ex)
        finally:
            if pv is not None:
                try:
                    pv.disconnect()
                except Exception:
                    pass
            self.signals.finished.emit(choices, error)
