"""On-demand compile metadata; never reads arbitrary live PLC variables."""
import secrets
import threading
import time

_lock = threading.Lock()
_next_request = {}


def parse_metadata(payload, token):
    raw = payload.encode('utf-8') if isinstance(payload, str) else bytes(payload)
    if not 0 < len(raw) <= 65536:
        raise ValueError('Invalid PLC metadata size')
    header, length, body = raw.split(b'\n', 2)
    if header != f'ECMCPLC1 {token}'.encode():
        raise ValueError('PLC response replaced by another request; refresh again')
    size = int(length)
    if size < 0 or size >= len(body) or body[size:size+1] != b'\n':
        raise ValueError('Truncated PLC source')
    source = body[:size].decode('utf-8')
    variables = []
    for line in body[size+1:].splitlines():
        scope, name = line.decode('utf-8').split('\t', 1)
        if scope not in ('L', 'G') or not name:
            raise ValueError('Invalid PLC variable metadata')
        variables.append(('Local' if scope == 'L' else 'Global', name))
    return source, variables


def fetch_metadata(prefix, index, timeout=2.0, epics_module=None):
    """Run on a worker thread. Reads only passive diagnostic records."""
    prefix = prefix.rstrip(':')
    with _lock:
        now = time.monotonic()
        if now < _next_request.get(prefix, 0):
            raise RuntimeError('Wait one second between requests to this IOC')
        _next_request[prefix] = now + 1.0
    if epics_module is None:
        import epics as epics_module
    base = f'{prefix}:PLC{int(index):02d}-'
    token = secrets.randbelow(2147483647) + 1
    def put(suffix, value):
        if epics_module.caput(base+suffix, value, wait=True, timeout=timeout) != 1:
            raise RuntimeError('PLC request failed or rate limited; retry after one second')
    put('DiagCmd', token)
    put('DiagStat.PROC', 1)
    status = epics_module.caget(base+'DiagStat', use_monitor=False, timeout=timeout)
    if status != token:
        raise RuntimeError('PLC metadata unavailable or response replaced; refresh again')
    put('DiagData.PROC', 1)
    payload = epics_module.caget(base+'DiagData', as_string=False, use_monitor=False, timeout=timeout)
    if payload is None:
        raise RuntimeError('PLC waveform unavailable')
    return parse_metadata(payload, token)


def fetch_values(prefix, index, page, timeout=3.0, epics_module=None):
    if not 0 <= page < 64:
        raise ValueError('Invalid PLC variable page')
    prefix = prefix.rstrip(':')
    with _lock:
        now = time.monotonic()
        if now < _next_request.get(prefix, 0):
            raise RuntimeError('Wait one second between requests to this IOC')
        _next_request[prefix] = now + 1.0
    if epics_module is None:
        import epics as epics_module
    base = f'{prefix}:PLC{int(index):02d}-'
    token = -((secrets.randbelow((1<<25)-1)+1)*64+page)
    def put(suffix, value):
        if epics_module.caput(base+suffix, value, wait=True, timeout=timeout) != 1:
            raise RuntimeError('PLC snapshot request failed or rate limited')
    put('DiagCmd', token)
    deadline = time.monotonic()+timeout
    while time.monotonic()<deadline:
        put('DiagStat.PROC',1)
        if epics_module.caget(base+'DiagStat',use_monitor=False,timeout=timeout)==token:
            put('DiagData.PROC',1)
            raw = epics_module.caget(base+'DiagData',as_string=False,use_monitor=False,timeout=timeout)
            return parse_values(raw,token,page)
        time.sleep(.05)
    raise RuntimeError('PLC snapshot timed out: PLC may be disabled or IOC may require an update')

def parse_values(payload, token, page):
    raw = payload.encode() if isinstance(payload,str) else bytes(payload)
    header,*lines = raw.splitlines()
    magic, received, start, size = header.decode().split()
    size=int(size)
    if magic!='ECMCPLCV1' or int(received)!=token or int(start)!=page*32 or not 0<size<=32 or len(lines)!=size:
        raise ValueError('Invalid or replaced PLC snapshot')
    return {page*32+i:float(line) for i,line in enumerate(lines)}


def registered_index_at(line, offset, variables):
    """Resolve an exact registered name at a Python character offset."""
    import re
    # Do not interpret names in comments.
    code = re.split(r'//|#', line, maxsplit=1)[0]
    matches = []
    for index, (_, name) in enumerate(variables):
        for match in re.finditer(r'(?<![\w.])'+re.escape(name)+r'(?![\w.])', code):
            if match.start() <= offset < match.end():
                matches.append((len(name), index))
    if not matches:
        return None
    matches.sort(reverse=True)
    if len(matches)>1 and matches[0][0]==matches[1][0]:
        return None
    return matches[0][1]
