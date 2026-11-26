"""Ordered access-checked axis command writes."""
from qt_compat import QtCore
from ecmc_hardware_write import WriteSignals

class AxisWriteTask(QtCore.QRunnable):
    def __init__(self,pv,values,timeout=1.0):
        super().__init__()
        self.pv,self.values,self.timeout = pv,tuple(values),timeout
        self.signals = WriteSignals()

    def run(self):
        pv = None
        error = ""
        try:
            import epics
            pv = epics.PV(self.pv,auto_monitor=False)
            if not pv.wait_for_connection(timeout=self.timeout) or not pv.write_access or pv.count != 1:
                raise RuntimeError("PV unavailable, non-scalar or write access denied.")
            for value in self.values:
                result = pv.put(value,wait=True,timeout=self.timeout)
                if result is None or result <= 0:
                    raise RuntimeError("Write not confirmed; check IOC before retrying.")
        except Exception as ex:
            error = str(ex)
        finally:
            if pv is not None:
                try:
                    pv.disconnect()
                except Exception:
                    pass
            self.signals.finished.emit(error)
