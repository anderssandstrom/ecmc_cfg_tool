"""Qt-independent sample helpers shared by ECMC acquisition tools.

Callers retain their timestamp domain (IOC wall clock or local monotonic).
Missing values can be retained as gaps; no synchronization is fabricated.
"""
from collections import deque
import csv
import math


def finite_value(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def bounded_series(keys, max_samples):
    limit = int(max_samples)
    if limit < 1:
        raise ValueError("Sample capacity must be positive")
    return {key: deque(maxlen=limit) for key in keys}


def append_sample(buffer, timestamp, value, *, keep_gap=False, cutoff=None):
    stamp = finite_value(timestamp)
    number = finite_value(value)
    if stamp is None or (number is None and not keep_gap):
        return False
    buffer.append((stamp, number))
    if cutoff is not None:
        while buffer and buffer[0][0] < cutoff:
            buffer.popleft()
    return True


def sanitize_samples(samples):
    valid = []
    for stamp, value in samples or ():
        stamp, value = finite_value(stamp), finite_value(value)
        if stamp is not None and value is not None:
            valid.append((stamp, value))
    clean = []
    for stamp, value in sorted(valid, key=lambda item: item[0]):
        if clean and stamp == clean[-1][0]:
            clean[-1] = (stamp, value)
        else:
            clean.append((stamp, value))
    return clean


def export_event_csv(path, series, *, pv_name=str):
    """Export independent sample streams as elapsed-time/PV/value rows."""
    rows = [(stamp, key, value) for key, samples in series.items()
            for stamp, value in samples]
    rows.sort(key=lambda row: (row[0], str(row[1])))
    origin = rows[0][0] if rows else 0.0
    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["elapsed_s", "pv", "value"])
        writer.writerows((f"{stamp-origin:.6f}", pv_name(key), value)
                         for stamp, key, value in rows)
    return len(rows)
