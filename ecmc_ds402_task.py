"""Non-RT, bounded, cancellable CiA402 state request."""
import time
from threading import Event
from qt_compat import QtCore
from ecmc_ds402 import decode_state, word_number, transition_word

class StateSignals(QtCore.QObject):
    finished=QtCore.Signal(str) if hasattr(QtCore,'Signal') else QtCore.pyqtSignal(str)

class StateTask(QtCore.QRunnable):
    def __init__(self, status, control, trigger, target, timeout):
        super().__init__()
        self.status,self.control,self.trigger,self.target=status,control,trigger,target
        self.timeout=max(.2,min(float(timeout),2.0))
        self.cancelled=Event()
        self.signals=StateSignals()

    def run(self):
        pvs=[]
        error=''
        try:
            import epics
            def connect(name,write=False):
                pv=epics.PV(name,auto_monitor=False)
                pvs.append(pv)
                if not pv.wait_for_connection(timeout=self.timeout) or (write and not pv.write_access):
                    raise RuntimeError("PV unavailable or write denied: "+name)
                if pv.count!=1:
                    raise RuntimeError("Expected scalar PV: "+name)
                return pv
            def read(pv):
                if self.cancelled.is_set():
                    raise RuntimeError("State request cancelled")
                value=pv.get(use_monitor=False,timeout=self.timeout)
                if value is None or pv.severity==3:
                    raise RuntimeError("Invalid drive value: "+pv.pvname)
                return value
            def put(pv,value):
                if self.cancelled.is_set() or not pv.write_access:
                    raise RuntimeError("State request cancelled or write denied")
                if (pv.put(value,wait=True,timeout=self.timeout) or 0)<=0:
                    raise RuntimeError("Drive write not confirmed")
            status=connect(self.status)
            target=self.target
            state=decode_state(read(status))
            if target=="Reset fault" and state!="Fault":
                raise RuntimeError("Drive is not in fault")
            if self.trigger and state!=target:
                if state in ("Fault","Fault reaction active","Not ready to switch on","Unavailable","Unknown state") and target=="Operation enabled":
                    raise RuntimeError("Drive is not ready to enable")
                put(connect(self.trigger,True),1)
            control=connect(self.control,True) if self.control and not self.trigger else None
            deadline=time.monotonic()+5
            last=None
            while time.monotonic()<deadline:
                state=decode_state(read(status))
                if state==target or (target=="Reset fault" and state not in ("Fault","Fault reaction active","Unavailable","Unknown state")):
                    break
                if control:
                    old=word_number(read(control))
                    value=transition_word(state,target,old)
                    if value is not None and (state,value)!=last:
                        put(control,value)
                        last=(state,value)
                elif not self.trigger:
                    raise RuntimeError("No verified command mapping")
                if self.cancelled.wait(.1):
                    raise RuntimeError("State request cancelled")
            else:
                raise RuntimeError("Target not reached: "+state)
        except Exception as ex:
            error=str(ex)
        finally:
            for pv in pvs:
                try: pv.disconnect()
                except Exception: pass
            self.signals.finished.emit(error)
