"""Compact position commands, using the shared native motion contract."""
import math
from threading import Event
from qt_compat import QtCore
from ecmc_hardware_write import WriteSignals
from ecmc_native_motion import NativeMotionMixin, NATIVE_PV_SUFFIXES, numeric_readback

class PositionTask(QtCore.QRunnable, NativeMotionMixin):
    def __init__(self,base,native,relative,target,velocity):
        super().__init__()
        self.base,self.native,self.relative = base,native,relative
        self.target,self.velocity = target,velocity
        self.cancelled = Event()
        self.pvs = {}
        self.signals = WriteSignals()

    def _connect(self,key,suffix):
        if key not in self.pvs:
            import epics
            pv = epics.PV(self.base+suffix,auto_monitor=False)
            self.pvs[key] = pv
            if not pv.wait_for_connection(timeout=.7):
                raise RuntimeError(suffix+" unavailable")
        return self.pvs[key]

    def _value(self,key):
        pv = self.pvs.get(key)
        if pv is None or not pv.connected:
            return None
        return numeric_readback(pv.get(use_monitor=False,timeout=.7),pv.severity)

    def _put(self,field,value):
        if self.cancelled.is_set():
            raise RuntimeError("Motion command cancelled")
        return super()._put(field,value)

    def _motor_put(self,key,value):
        if self.cancelled.is_set():
            raise RuntimeError("Motion command cancelled")
        pv = self.pvs[key]
        if not pv.connected or not pv.write_access or pv.count != 1:
            raise RuntimeError(key+" unavailable or not writable")
        if pv.put(value,wait=True,timeout=.7) != 1:
            raise RuntimeError(key+" write not confirmed")

    def run(self):
        error = ""
        try:
            if not math.isfinite(self.target) or not math.isfinite(self.velocity) or self.velocity <= 0:
                raise ValueError("Finite target and positive velocity required")
            if self.native:
                for key in ("Stat","Auto","ErrId","Enabled","EnableCmd","PosAct","Command","CmdData","Target","Execute","VELO"):
                    self._connect(key,"-"+NATIVE_PV_SUFFIXES[key])
                self._ready()
                mode = self._mode()
                if self._bit(3)!=0 or self._bit(5)!=0:
                    raise RuntimeError("Axis busy")
                target = self.target + self._value("PosAct") if self.relative else self.target
                # IOC enforces native configured limits; do not substitute motor user limits.
                self._put("TgtVelCmd",self.velocity)
                self.native_move_absolute(target,mode,(-math.inf,math.inf))
            else:
                for key in ("RBV","DMOV","CNEN","LLM","HLM","LVIO","VELO","VAL"):
                    self._connect(key,"."+key)
                if self._value("DMOV") != 1 or self._value("CNEN") != 1:
                    raise RuntimeError("Motor not ready or disabled")
                actual,low,high = (self._value(k) for k in ("RBV","LLM","HLM"))
                if actual is None or low is None or high is None:
                    raise RuntimeError("Motor position or limits unavailable")
                target = actual+self.target if self.relative else self.target
                if not low <= target <= high:
                    raise RuntimeError("Target outside motor record limits")
                self._motor_put("VELO",self.velocity)
                if self._value("DMOV") != 1 or self._value("CNEN") != 1:
                    raise RuntimeError("Motor status changed")
                self._motor_put("VAL",target)
        except Exception as ex:
            error = str(ex)
        finally:
            for pv in self.pvs.values():
                try:
                    pv.disconnect()
                except Exception:
                    pass
            self.signals.finished.emit(error)
