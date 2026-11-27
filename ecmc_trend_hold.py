"""Display-only zero-order hold for asynchronous PV events."""

def held_points(events, start, end):
    """Clip to window, retaining the last valid event before its start.

    None marks a disconnect/invalid sample. Never bridge such gaps or
    invent a value before the first event. Raw acquisition remains unchanged.
    """
    previous = None
    points = []
    for stamp, value in events:
        if stamp < start:
            previous = value
            continue
        if stamp > end:
            break
        if not points and previous is not None:
            points.append((start, previous))
        if previous is not None:
            points.append((stamp, previous))
        points.append((stamp, value))
        previous = value
    if not points and previous is not None:
        points.append((start, previous))
    if previous is not None:
        points.append((end, previous))
    return points
