"""Shared native ECMC motion command contract (no Qt dependencies).

Subclasses supply _value(key) and a mapping of connected pyepics PVs.
Writes are ordered and fail closed when the execute mode is unknown.
"""
import math


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
        if self._value('ErrId')!=0 or self._value('Enabled')!=1 or self._value('EnableCmd')!=1:
            raise RuntimeError('Axis error, disabled, or status unavailable')
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
            self._put('ExeCmd',1)

    def native_kill(self):
        errors=[]
        for field,value in (('EnaCmd',0),('StpCmd',1)):
            try:
                self._put(field,value)
            except Exception as ex:
                errors.append(str(ex))
        return errors
