"""Passive, client-side incident files. No EPICS or Qt dependency."""
import json
import math
import time
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone

MAX_BYTES = 16 * 1024 * 1024

def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _clean(v) for k,v in value.items()}
    if isinstance(value, (tuple,list)):
        return [_clean(v) for v in value]
    if hasattr(value,'item'):
        try:
            return _clean(value.item())
        except (ValueError, TypeError):
            return None
    if value is None or isinstance(value,(str,int,float,bool)):
        return value
    return None

def build_incident(discovery, health, cached, events, trends, *, demo=False, now=None):
    prefix=discovery.get('prefix')
    if not prefix:
        raise ValueError('Missing IOC discovery')
    now=time.monotonic() if now is None else now
    # Only explicitly discovered health PVs; do not mix unrelated IOC data.
    names={name for row in health.values() for name in row.get('pvs',())}
    names.update(obj.get('motor','') for obj in discovery.get('axes',()))
    names.discard('')
    def relevant(name):
        return name in names or name.startswith(prefix+':') or any(
            name.startswith(m+'-') or name.startswith(m+'.')
            for m in names if m)
    pvs={}
    for name in sorted({n for row in health.values() for n in row.get('pvs',())}):
        entry=cached.get(name,{})
        connected=entry.get('connected')
        received=entry.get('received')
        pvs[name]={'connected':connected,'value':entry.get('value') if connected else None,
                   'severity':entry.get('severity') if connected else None,
                   'source_timestamp':entry.get('timestamp') if connected else None,
                   'age_s':max(0,now-received) if connected and received is not None else None}
    selected={}
    remaining=20000
    for name,samples in sorted(trends.items()):
        if not relevant(name) or remaining<=0:
            continue
        points=[(t-now,v) for t,v in samples if now-120<=t<=now]
        points=points[-min(2000,remaining):]
        selected[name]=points
        remaining-=len(points)
    return _clean({'format':'ecmc-incident','version':1,'timestamp':time.time(),
        'captured_utc':datetime.now(timezone.utc).isoformat(),'demo':bool(demo),
        'discovery':discovery,'health':health,'pvs':pvs,
        'events':[list(e) for e in events if len(e)==5 and e[1]==prefix][-5000:],
        'trends':selected,'limitations':['Asynchronous cached values, not an RT snapshot',
        'Discovery and versions reflect the last refresh; age_s is time since last monitor update, not proof of staleness',
        'Trends contain up to 120 seconds of already displayed samples, capped at 2000 per signal and 20000 total',
        'No new SDO reads, PLC source requests or configuration reads']})

def save_incident(path,bundle):
    data=json.dumps(bundle,indent=2,allow_nan=False,ensure_ascii=False).encode('utf-8')
    if len(data)>MAX_BYTES:
        raise ValueError('Incident exceeds file size limit')
    path=Path(path)
    temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as fp:
            temp=fp.name
            fp.write(data)
        os.replace(temp,path)
    finally:
        if temp and os.path.exists(temp):
            os.unlink(temp)

def load_incident(path):
    with open(path,'rb') as fp:
        data=fp.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:
        raise ValueError('Incident exceeds file size limit')
    bundle=json.loads(data)
    if not isinstance(bundle,dict) or not isinstance(bundle.get('discovery'),dict):
        raise ValueError('Not an IOC snapshot')
    if bundle.get('format') not in (None,'ecmc-incident'):
        raise ValueError('Unsupported incident format')
    if bundle.get('format') and bundle.get('version')!=1:
        raise ValueError('Unsupported incident version')
    for key in ('health','pvs','trends'):
        if not isinstance(bundle.get(key,{}),dict):
            raise ValueError('Invalid '+key)
    if not isinstance(bundle.get('events',[]),list):
        raise ValueError('Invalid events')
    return bundle
