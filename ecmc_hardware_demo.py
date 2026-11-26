"""Offline hardware demonstration: no EPICS imports or network access."""
import math
import re
import time

class HardwareDemo:
    def __init__(self):
        self.overrides = {}
        self.started = time.monotonic()

    def write(self, pv, value):
        self.overrides[pv] = value

    def value(self, pv, hardware="", panel=""):
        if pv in self.overrides:
            return self.overrides[pv]
        name = pv.split("-", 1)[-1]
        t = time.monotonic() - self.started
        if name == "HWType":
            return hardware
        if name == "PnlTyp":
            return panel
        if name in ("Online", "Operational", "Alstate-Op"):
            return 1
        if name.startswith("Alstate-"):
            return 0
        if name.endswith(("Alrm", "RstCmd", "TrqRed", "TrqRedCmd")):
            return 0
        if name.endswith(("Rdy", "RdyStat", "RdyToEna", "EnaCmd")):
            return 1
        if name.endswith("-Spd-RB"):
            cmd = self.overrides.get(pv[:-3], 1000)
            enabled = self.overrides.get(pv.rsplit("-", 2)[0] + "-EnaCmd", 1)
            return round(cmd + 3 * math.sin(t * 3), 1) if enabled else 0
        if name.endswith("-Spd"):
            return 1000
        if name.endswith(("PosAct", "PosAct02")):
            return int(640000 + 12000 * math.sin(t * .7))
        if name.endswith("-Stat"):
            return 39
        if name.endswith("-Cmd"):
            return 15
        if re.search(r"(BI|BO)\d+(-RB)?$", name):
            return int(t % 4 < 2) if "BI" in name else 0
        if re.search(r"AI\d+", name):
            return round(2.5 + 1.8 * math.sin(t), 3)
        return 0
