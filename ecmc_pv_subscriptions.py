"""Reference-counted, read-only monitors shared by diagnostic consumers.

Callbacks run on the CA thread: consumers must queue data, never touch Qt.
No get/put methods are exposed; snapshots never perform network reads.
"""
from threading import RLock
import time


class Subscription:
    def __init__(self, pool, name, token):
        self.pool, self.name, self.token = pool, name, token
    def snapshot(self):
        return self.pool.snapshot(self.name)
    def close(self):
        self.pool.release(self.name, self.token)
    disconnect = close
    def clear_callbacks(self):
        pass  # This lease must not clear another consumer's callbacks.


class SubscriptionPool:
    def __init__(self, factory=None):
        self.factory = factory
        self.lock = RLock()
        self.entries = {}

    def acquire(self, name, callback=None, connection_callback=None):
        name = str(name).strip()
        if not name or any(c.isspace() for c in name):
            raise ValueError('Expected a full PV name')
        token = object()
        with self.lock:
            entry = self.entries.get(name)
            existing = entry is not None
            if entry is None:
                entry = dict(users={}, pv=None, connected=False, value=None,
                             severity=None, timestamp=None, received=None)
                self.entries[name] = entry
            entry['users'][token] = (callback, connection_callback)
            if entry['pv'] is None:
                try:
                    factory = self.factory
                    if factory is None:
                        import epics
                        factory = epics.PV
                    entry['pv'] = factory(name, auto_monitor=True,
                        callback=lambda **kw: self._event(name, entry, **kw),
                        connection_callback=lambda **kw: self._connection(name, entry, **kw))
                except Exception:
                    self.entries.pop(name, None)
                    raise
            cached = dict(entry)
        if existing and callback and cached['connected'] and cached['received'] is not None:
            callback(pvname=name, value=cached['value'], severity=cached['severity'],
                     timestamp=cached['timestamp'])
        return Subscription(self, name, token)

    def _event(self, name, entry, value=None, severity=None, timestamp=None, **kw):
        with self.lock:
            if self.entries.get(name) is not entry:
                return
            entry.update(value=value, severity=severity, timestamp=timestamp,
                         received=time.monotonic())
            callbacks = [pair[0] for pair in entry['users'].values()]
        for callback in callbacks:
            if callback:
                try: callback(pvname=name, value=value, severity=severity, timestamp=timestamp)
                except Exception: pass  # Isolate consumers on the CA thread.

    def _connection(self, name, entry, conn=False, **kw):
        with self.lock:
            if self.entries.get(name) is not entry:
                return
            entry['connected'] = bool(conn)
            if not conn:
                entry.update(value=None, severity=None, received=None)
            callbacks = [pair[1] for pair in entry['users'].values()]
        for callback in callbacks:
            if callback:
                try: callback(pvname=name, conn=conn)
                except Exception: pass

    def snapshot(self, name):
        with self.lock:
            e = self.entries.get(name, {})
            return {key:e.get(key) for key in ('connected','value','severity','timestamp','received')}

    def release(self, name, token):
        with self.lock:
            e = self.entries.get(name)
            if e is None or token not in e['users']:
                return
            del e['users'][token]
            if e['users']:
                return
            del self.entries[name]
            pv = e['pv']
        if pv is not None:
            pv.clear_callbacks()
            pv.disconnect()


shared_subscriptions = SubscriptionPool()
