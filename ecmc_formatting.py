"""Shared numerical display formatting, independent of Qt."""
import re

FLOAT_DISPLAY_RE = re.compile(r'^[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+(?:\.\d*)?[eE][+-]?\d+)|(?:\.\d+[eE][+-]?\d+))$')
FLOAT_DISPLAY_COMMA_RE = re.compile(r'^[+-]?(?:(?:\d+,\d*)|(?:,\d+)|(?:\d+(?:,\d*)?[eE][+-]?\d+)|(?:,\d+[eE][+-]?\d+))$')

def compact_float_text(value, sig_digits=15):
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        v = float(value)
    else:
        s = str(value or '').strip()
        if not s:
            return s
        if FLOAT_DISPLAY_COMMA_RE.match(s) and '.' not in s:
            s = s.replace(',', '.')
        if not FLOAT_DISPLAY_RE.match(s):
            return str(value)
        # Preserve plain integer strings exactly as entered/read.
        if '.' not in s and 'e' not in s.lower():
            return s
        try:
            v = float(s)
        except Exception:
            return str(value)

    out = f'{v:.{int(sig_digits)}g}'
    if out in {'-0', '+0'}:
        return '0'
    return out


