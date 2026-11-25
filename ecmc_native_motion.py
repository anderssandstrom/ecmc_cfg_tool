"""Shared native ECMC motion command contract (no Qt dependencies).

Subclasses supply _value(key) and a mapping of connected pyepics PVs.
Writes are ordered and fail closed when the execute mode is unknown.
"""
import math

NATIVE_PV_SUFFIXES = {
    'PosSet': 'PosSet', 'PosAct': 'PosAct', 'PosErr': 'PosErr',
    'ErrId': 'ErrId', 'Enabled': 'EnaAct', 'EnableCmd': 'EnaCmd-RB',
    'Stat': 'Stat', 'Auto': 'AutoExecuteOnTargetPos-RB', 'TrajType': 'TrajType',
    'VELO': 'TgtVelCmd', 'ACC': 'AccCmd', 'DEC': 'DecCmd',
    'Command': 'MtnCmd', 'CmdData': 'MtnCmdData', 'Target': 'TgtPosCmd',
    'Execute': 'ExeCmd', 'Stop': 'StpCmd', 'EnableWrite': 'EnaCmd',
}


def numeric_readback(value, severity=None):
    try:
        value = float(value)
        return value if math.isfinite(value) and severity != 3 else None
    except (TypeError, ValueError, OverflowError):
        return None


class NativeMotionMixin:
    def _bit(self,bit):
        stat=self._value('Stat')
        return None if stat is None else (int(stat)>>bit)&1

    def _mode(self):
        # Dedicated PV indicates this IOC supports the status-bit contract.
        # Do not interpret a reserved bit on older firmware as explicit mode.
        auto=self._value('Auto')
        bit=self._bit(24)
        return int(auto) if auto in (0,1) and bit==auto else None

    def _put(self,field,value):
        # Wait for each write so command/data/target/execute are ordered.
        # Use a dedicated CA PV and a bounded timeout; STOP/KILL are independent.
        keys={'TgtPosCmd':'Target','ExeCmd':'Execute','StpCmd':'Stop',
              'EnaCmd':'EnableWrite','MtnCmd':'Command','MtnCmdData':'CmdData',
              'TgtVelCmd':'VELO','AccCmd':'ACC','DecCmd':'DEC'}
        pv=self.pvs.get(keys[field])
        if pv is None or not pv.connected or not pv.write_access:
            raise RuntimeError(field+' unavailable or not writable')
        result=pv.put(value,wait=True,timeout=0.7)
        if result!=1:
            raise RuntimeError(field+' write not confirmed')

    def _ready(self):
        err, enabled, requested = (self._value(k) for k in ('ErrId','Enabled','EnableCmd'))
        if err is None or enabled not in (0,1) or requested not in (0,1):
            raise RuntimeError(f'Axis status unavailable: ErrId={err}, EnaAct={enabled}, EnaCmd-RB={requested}')
        if err != 0:
            raise RuntimeError(f'Axis error 0x{int(err):x}')
        if (enabled != 1 or requested != 1) and not getattr(self, 'auto_enable_timeout', 0) > 0:
            raise RuntimeError(f'Axis disabled (EnaAct={enabled:g}, EnaCmd-RB={requested:g}); auto-enable not confirmed')
        if self._bit(10)!=1 or self._bit(11)!=0 or self._bit(23)!=0:
            raise RuntimeError('Axis not in RT, external trajectory, or blocked')
        if any(self._bit(k)!=1 for k in (6,7)) or any(self._bit(k)!=0 for k in (17,18)):
            raise RuntimeError('Limit/interlock active or status unavailable')
        if self._mode() is None:
            raise RuntimeError('Native execute mode unknown; scan blocked')
        if self._bit(3) is None or self._value('PosAct') is None:
            raise RuntimeError('Native axis status unavailable')

    def native_move_absolute(self,target,mode,bounds):
        self._ready()
        if self._mode()!=mode or self._bit(3)!=0 or self._bit(5)!=0:
            raise RuntimeError('Axis busy or execute mode changed')
        if not math.isfinite(target) or not bounds[0]<=target<=bounds[1]:
            raise RuntimeError('Target outside native limits')
        self._put('MtnCmd',3)
        self._put('MtnCmdData',0)
        if self._mode()!=mode:
            raise RuntimeError('Execute mode changed before target write')
        self._put('TgtPosCmd',target)
        if mode==0:
            if self._mode()!=0:
                raise RuntimeError('Execute mode changed after target write')
            # Match the native caQtDM EXEC button: press=0, release=1.
            self._put('ExeCmd',0)
            if self._mode()!=0:
                raise RuntimeError('Execute mode changed before execute edge')
            self._put('ExeCmd',1)

    def native_kill(self):
        errors=[]
        for field,value in (('EnaCmd',0),('StpCmd',1)):
            try:
                self._put(field,value)
            except Exception as ex:
                errors.append(str(ex))
        return errors


class NativeAxisConnection(NativeMotionMixin):
    """Direct, non-monitored native PV access independent of the GUI."""
    def __init__(self, client, base, timeout=0.7):
        ep = getattr(client, '_epics', None)
        if ep is None:
            raise RuntimeError('Native motion requires pyepics')
        self.base = str(base).strip()
        if not self.base:
            raise ValueError('Native axis PV base is required')
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('Invalid connection timeout')
        self.pvs = {}
        try:
            for key, suffix in NATIVE_PV_SUFFIXES.items():
                if key != 'PosErr':
                    self.pvs[key] = ep.PV(self.base + '-' + suffix, auto_monitor=False)
            for pv in self.pvs.values():
                pv.wait_for_connection(timeout=self.timeout)
        except Exception:
            self.close()
            raise

    def _value(self, key):
        pv = self.pvs.get(key)
        if pv is None or not pv.connected:
            return None
        try:
            return numeric_readback(pv.get(use_monitor=False, timeout=self.timeout), pv.severity)
        except Exception:
            return None

    def close(self):
        for pv in self.pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self.pvs.clear()
