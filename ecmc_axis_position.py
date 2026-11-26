"""Compact position commands, using the shared native motion contract."""
import math
from threading import Event
from qt_compat import QtCore
from ecmc_hardware_write import WriteSignals
from ecmc_axis_diagnostics import native_soft_bounds
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
                for key,suffix in (("Low","-CfgDLLM"),("High","-CfgDHLM"),
                                   ("LowEna","-SftLimBwdEna-RB"),("HighEna","-SftLimFwdEna-RB")):
                    self._connect(key,suffix)
                bounds = native_soft_bounds(*(self._value(k) for k in ("Low","High","LowEna","HighEna")))
                if not bounds[0] <= target <= bounds[1]:
                    raise RuntimeError("Target outside enabled ECMC soft limits")
                self._put("TgtVelCmd",self.velocity)
                self.native_move_absolute(target,mode,bounds)
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

class HomeTask(PositionTask):
    """Use configured homing procedure, never substitute a position move."""
    def __init__(self,base,procedure=None):
        super().__init__(base,True,False,0,1)
        self.procedure=procedure

    def run(self):
        error = ""
        try:
            for key in ("Stat","Auto","ErrId","Enabled","EnableCmd","Command","CmdData","Execute"):
                self._connect(key,"-"+NATIVE_PV_SUFFIXES[key])
            self._connect("HomeProcedure","-movHomCmd")
            proc = self._value("HomeProcedure") if self.procedure is None else self.procedure
            if proc not in range(1,15):
                raise RuntimeError("Homing procedure unavailable, disabled or manual position setting")
            if self._value("ErrId") != 0 or self._value("Enabled") != 1 or self._value("EnableCmd") != 1:
                raise RuntimeError("Axis not enabled or has an error")
            if any(self._bit(bit)!=expected for bit,expected in ((10,1),(11,0),(23,0),(3,0),(5,0),(17,0),(18,0))):
                raise RuntimeError("Axis busy, blocked, interlocked or not in local RT")
            # HOME is enum index 10 in the verified ECMCCFG record.
            # Confirm actual IOC enum label before writing.
            pv = self.pvs["Command"]
            pv.get_ctrlvars(timeout=.7)
            labels = list(pv.enum_strs or [])
            if "HOME" not in labels:
                raise RuntimeError("IOC does not expose HOME command")
            if self.procedure is not None:
                self._motor_put("HomeProcedure",int(proc))
                if self._value("HomeProcedure") != proc:
                    raise RuntimeError("Homing procedure write not verified")
            self._put("MtnCmd",labels.index("HOME"))
            self._put("MtnCmdData",int(proc))
            if self._value("HomeProcedure") != proc:
                raise RuntimeError("Homing procedure changed")
            self._put("ExeCmd",0)
            self._put("ExeCmd",1)
        except Exception as ex:
            error = str(ex)
        finally:
            for pv in self.pvs.values():
                try:
                    pv.disconnect()
                except Exception:
                    pass
            self.signals.finished.emit(error)

class VelocityTask(PositionTask):
    """Signed velocity command with directional limit checks."""
    def __init__(self,base,native,velocity):
        super().__init__(base,native,False,0,velocity)

    def run(self):
        error = ""
        try:
            speed = self.velocity
            if not math.isfinite(speed) or speed == 0:
                raise ValueError("Nonzero finite signed velocity required")
            if self.native:
                for key in ("Stat","Auto","ErrId","Enabled","EnableCmd","PosAct","Command","CmdData","Execute","VELO"):
                    self._connect(key,"-"+NATIVE_PV_SUFFIXES[key])
                self._ready()
                if self._bit(3)!=0 or self._bit(5)!=0:
                    raise RuntimeError("Axis busy")
                suffix = "-CfgDHLM" if speed>0 else "-CfgDLLM"
                enable = "-SftLimFwdEna-RB" if speed>0 else "-SftLimBwdEna-RB"
                self._connect("Limit",suffix)
                self._connect("LimitEnabled",enable)
                limit,actual = self._value("Limit"),self._value("PosAct")
                if self._value("LimitEnabled") != 1 or limit is None or actual is None or (speed>0 and actual>=limit) or (speed<0 and actual<=limit):
                    raise RuntimeError("Directional soft limit unavailable, disabled or reached")
                pv = self.pvs["Command"]
                pv.get_ctrlvars(timeout=.7)
                choices = list(pv.enum_strs or [])
                if "VEL" not in choices:
                    raise RuntimeError("IOC does not expose VEL command")
                self._put("MtnCmd",choices.index("VEL"))
                self._put("MtnCmdData",0)
                self._put("TgtVelCmd",speed)
                self._put("ExeCmd",0)
                self._put("ExeCmd",1)
            else:
                direction = "JOGF" if speed>0 else "JOGR"
                opposite = "JOGR" if speed>0 else "JOGF"
                for key in ("DMOV","CNEN","RBV","LLM","HLM","JVEL",direction,opposite):
                    self._connect(key,"."+key)
                actual,limit = self._value("RBV"),self._value("HLM" if speed>0 else "LLM")
                if self._value("DMOV")!=1 or self._value("CNEN")!=1 or actual is None or limit is None or (speed>0 and actual>=limit) or (speed<0 and actual<=limit):
                    raise RuntimeError("Motor not ready or directional limit reached")
                self._motor_put(opposite,0)
                self._motor_put("JVEL",abs(speed))
                self._motor_put(direction,1)
        except Exception as ex:
            error = str(ex)
        finally:
            for pv in self.pvs.values():
                try:
                    pv.disconnect()
                except Exception:
                    pass
            self.signals.finished.emit(error)

class MotorStopTask(PositionTask):
    def __init__(self,base):
        super().__init__(base,False,False,0,1)

    def run(self):
        errors = []
        for key,value in (("STOP",1),("JOGF",0),("JOGR",0),("STOP",1)):
            try:
                self._connect(key,"."+key)
                self._motor_put(key,value)
            except Exception as ex:
                errors.append(str(ex))
        for pv in self.pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self.signals.finished.emit("; ".join(errors))

class KillTask(PositionTask):
    """Disable drive and request stop independently; report every failed write."""
    def __init__(self,base,native):
        super().__init__(base,native,False,0,1)

    def run(self):
        errors = []
        commands = (("-EnaCmd",0),("-StpCmd",1)) if self.native else (
            (".CNEN",0),(".STOP",1),(".JOGF",0),(".JOGR",0))
        for suffix,value in commands:
            try:
                self._connect(suffix,suffix)
                self._motor_put(suffix,value)
            except Exception as ex:
                errors.append(str(ex))
        for pv in self.pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self.signals.finished.emit("; ".join(errors))
