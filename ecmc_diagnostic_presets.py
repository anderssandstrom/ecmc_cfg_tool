"""Read-only cross-tool diagnostic PV presets.

Presets contain existing ECMC signal names; optional signals may be absent
on older IOCs. No PV reads or writes are performed by these helpers.
"""
from ecmc_controller_live import SUFFIXES

POSITION_SIGNALS = ("PosAct", "PosSet", "PosErr")
CONTROLLER_SIGNALS = POSITION_SIGNALS + (
    SUFFIXES["velocity"], SUFFIXES["target"], SUFFIXES["busy"],
    SUFFIXES["at_target"], SUFFIXES["error_id"],
)


def unique_pvs(values):
    return list(dict.fromkeys(str(value).strip() for value in values
                             if value is not None and str(value).strip()))


def axis_diagnostic_pvs(base, profile="position", extra=()):
    base = str(base or "").strip()
    if profile not in ("position", "controller"):
        raise ValueError("Unknown diagnostic profile")
    suffixes = CONTROLLER_SIGNALS if profile == "controller" else POSITION_SIGNALS
    return unique_pvs([base + "-" + suffix for suffix in suffixes] + list(extra)
                      if base else extra)
