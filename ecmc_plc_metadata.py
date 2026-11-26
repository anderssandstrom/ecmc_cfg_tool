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
