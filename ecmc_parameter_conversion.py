"""Conversions between ecmccfg YAML units and ECMC runtime units."""
import math

PID = {p+'.'+g: p+'.'+t for p in ('controller','controller.inner')
       for g,t in (('Ki','TiMs'),('Kd','TdMs'))}
TIMING = {
    'monitoring.lag.time':'monitoring.lag.timeMs',
    'monitoring.target.time':'monitoring.target.timeMs',
    'monitoring.target.slaveControlDeadband.time':'monitoring.target.slaveControlDeadband.timeMs',
    'monitoring.stall.time.timeout':'monitoring.stall.time.timeoutMs',
    'controller.deadband.time':'controller.deadband.timeMs',
    'drive.brake.openDelay':'drive.brake.openDelayMs',
    'drive.brake.closeAhead':'drive.brake.closeAheadMs',
    'encoder.delayComp.time':'encoder.delayComp.timeMs',
}
for group in ('monitoring.velocity','monitoring.velocityDifference'):
    for leaf in ('trajectory','drive'):
        TIMING[group+'.time.'+leaf] = group+'.timeMs.'+leaf
for group, leaves in (
    ('encoder.filter',('velocity','position','trajectory')),
    ('plc.velocity_filter',('encoder','trajectory')),
    ('plc.filter',('velocity','position','trajectory')),
):
    for leaf in leaves:
        TIMING[group+'.'+leaf+'.size'] = group+'.'+leaf+'.timeMs'
ALIASES = {**PID, **TIMING}
REVERSE = {v:k for k,v in ALIASES.items()}

def number(value):
    v = float(value)
    if not math.isfinite(v):
        raise ValueError('Non-finite parameter')
    return v

def rate(value):
    v = number(value)
    if v <= 0:
        raise ValueError('IOC sample rate unavailable')
    return v

def fmt(value):
    return format(number(value), '.15g')

def to_runtime(path, value, hz, kp=None):
    v = number(value)
    hz = rate(hz)
    old = REVERSE[path]
    if old in PID:
        p = number(kp)
        if p < 0 or v < 0:
            raise ValueError('Negative PID parameter')
        if p == 0 and v != 0:
            raise ValueError('Time constants require nonzero Kp; use direct gains')
        return fmt(0 if v == 0 else (1000*p/(hz*v) if old.endswith('.Ki') else p*v*hz/1000))
    if old != 'encoder.delayComp.time' and v < 0:
        raise ValueError('Negative timing parameter')
    cycles = v*hz/1000
    if old == 'encoder.delayComp.time':
        return fmt(cycles)
    # Avoid rounding an exact integer upward due solely to floating-point noise.
    rounded = math.ceil(cycles - 4*math.ulp(cycles)) if cycles else 0
    return str(max(1, rounded) if old.endswith('.size') else max(0,rounded))

def from_runtime(path, value, hz, kp=None):
    v = number(value)
    hz = rate(hz)
    old = REVERSE[path]
    if old in PID:
        p = number(kp)
        if v == 0:
            return '0'
        if p <= 0 or v < 0:
            raise ValueError('Gain cannot be represented as a time constant')
        return fmt(1000*p/(hz*v) if old.endswith('.Ki') else 1000*v/(p*hz))
    return fmt(v*1000/hz)

def display_path(old, pid_ms, timing_ms):
    return ALIASES.get(old,old) if (old in PID and pid_ms or old in TIMING and timing_ms) else old

def export_values(values, current, hz, pid_ms, timing_ms, changed=()):
    """Convert complete or changed-only YAML; include Kp for converted PID terms."""
    result = {}
    marked = set()
    changed = set(changed)
    for old,value in values.items():
        path = display_path(old,pid_ms,timing_ms)
        if path != old:
            kp_key = old.rsplit('.',1)[0]+'.Kp'
            kp = values.get(kp_key,current.get(kp_key))
            try:
                value = from_runtime(path,value,hz,kp)
                if old in PID:
                    result[kp_key] = kp
            except (ValueError,TypeError,OverflowError):
                path = old
        result[path] = value
        if old in changed:
            marked.add(path)
    return result,marked
