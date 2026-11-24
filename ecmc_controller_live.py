"""Read-only live controller diagnostics using existing Channel Access PVs."""
import math
import time

SUFFIXES = {
    'setpoint': 'PosSet', 'actual': 'PosAct', 'error': 'PosErr',
    'velocity': 'VelAct', 'target': 'TgtPosCmd',
    'busy': 'Busy', 'local_busy': 'BusyLocal', 'at_target': 'AtTarget',
    'enabled': 'EnaAct', 'external': 'TrjSrcTyp-RB',
    'error_id': 'ErrId',
}

def numeric(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None

def derive(values, config, mode, elapsed):
    """Diagnostic estimates, not RT state; samples are asynchronous."""
    def n(key):
        return numeric(values.get(key))
    def c(key):
        return numeric(config.get(key))
    actual, target, error, setpoint = (n(k) for k in ('actual','target','error','setpoint'))
    tol = c('AxisCntrlInnerTol')
    busy, local, enabled, external = (n(k) for k in ('busy','local_busy','enabled','external'))
    inner = None
    if tol is not None and actual is not None and target is not None and enabled == 1:
        inner = tol > 0 and abs(target-actual) < tol
    at_tol = c('AxisMonAtTargetTol')
    db_tol = c('AxisCntrlDeadband')
    monitor = c('AxisMonEnableAtTargetMon')
    db_cycles = c('AxisCntrlDeadbandTime')
    hz = c('sample_rate')
    deadband = 'unknown'
    if all(x is not None for x in (actual,target,error,setpoint,busy,local,external,at_tol,db_tol,monitor,db_cycles,hz)) and hz > 0 and external == 0 and monitor == 1 and db_cycles >= 0:
        if local == 0 and abs(target-setpoint) < at_tol and abs(error) < db_tol:
            deadband = 'qualifying'
            if elapsed is not None and elapsed >= (db_cycles+1)/hz and busy == 0:
                deadband = 'likely gated' if mode == 'CSV' else 'likely holding' if mode == 'CSP' else 'within tolerance'
        else:
            deadband = 'not qualified'
    if mode not in ('CSV','CSP'):
        inner = None
    return inner, deadband

class LivePVMonitor:
    """CA monitors are serviced by pyepics; the Qt timer only reads cached data."""
    def __init__(self, client, base, demo=False):
        self.client = client
        self.base = base
        self.demo = demo
        self.pvs = {}
        self.reason = ''
        if demo:
            return
        epics = getattr(client, '_epics', None)
        if epics is None:
            self.reason = 'Live requires pyepics'
            return
        try:
            for key,suffix in SUFFIXES.items():
                self.pvs[key] = epics.PV(base+'-'+suffix, auto_monitor=True)
        except Exception:
            self.close()
            self.reason = 'Live CA unavailable'

    def snapshot(self):
        if self.demo:
            # Static, coherent example values; never connect to an IOC.
            return dict(setpoint=-40.0, actual=-39.998, error=-0.002,
                        velocity=0, target=-40.0, busy=0, local_busy=0,
                        at_target=1, enabled=1, external=0, error_id=0)
        result = {}
        for key,pv in self.pvs.items():
            try:
                if pv.connected:
                    # Cached CA monitor value only. Never wait for a missing PV.
                    if getattr(pv, 'severity', None) != 3:
                        result[key] = numeric(pv.value)
            except Exception:
                pass
        return result

    def close(self):
        for pv in self.pvs.values():
            try:
                pv.disconnect()
            except Exception:
                pass
        self.pvs.clear()
