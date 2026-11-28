"""Offline hardware demonstration: no EPICS imports or network access."""
import math
import re
import time

class HardwareDemo:
    def __init__(self):
        self.overrides = {}
        self.ds402 = {}
        self.started = time.monotonic()

    def write(self, pv, value, ds402=False):
        match = re.fullmatch(r"(.+-Drv\d+)-(Cmd|TrgDS402Ena|TrgDS402Dis)", pv)
        if ds402 and match:
            drive, command = match.groups()
            control, status = self.ds402.get(drive, (15, 39))
            if command == "TrgDS402Ena" and value == 1:
                if status != 8:
                    control, status = 15, 39
            elif command == "TrgDS402Dis" and value == 1:
                control, status = 0, 64
            elif command == "Cmd":
                previous = control
                control = int(value) & 0xffff
                if status == 8:
                    if control & 128 and not previous & 128:
                        status = 64
                elif control & 0x80:
                    status = 64
                elif control & 0x0f == 2:
                    status = 7 if status == 39 else 64
                elif not control & 2:
                    status = 64
                elif not control & 4:
                    status = 7 if status == 39 else 64
                elif control & 15 == 15:
                    status = 39
                elif control & 7 == 7:
                    status = 35
                elif control & 7 == 6:
                    status = 33
                else:
                    status = 64
            self.ds402[drive] = (control, status)
            return
        self.overrides[pv] = value

    def value(self, pv, hardware="", panel=""):
        drive_match = re.fullmatch(r"(.+-Drv\d+)-(Cmd|Cmd-RB|Stat|ErrAlrm|Spd-RB|TrgDS402Ena|TrgDS402Dis)", pv)
        if drive_match and (panel in ("EL7062", "EL7062_CSP", "Ex72x1_STD") or panel in ("EX7062_OPT", "EL7062_OPT") or drive_match[1] in self.ds402):
            drive, signal = drive_match.groups()
            control, status = self.ds402.get(drive, (15, 39))
            if signal in ("Cmd", "Cmd-RB"):
                return control
            if signal == "Stat":
                return status
            if signal == "ErrAlrm":
                return int(status == 8)
            if signal.startswith("TrgDS402"):
                return 0
            if signal == "Spd-RB" and status != 39:
                return 0
        if pv in self.overrides:
            return self.overrides[pv]
        name = pv.split("-", 1)[-1]
        t = time.monotonic() - self.started
        if name.endswith("-Arr") and re.match(r"(AI|AO|Enc)\d+", name):
            channel=int(re.search(r"\d+",name).group())
            return [int(12000*math.sin(2*math.pi*i/32+t+channel)) for i in range(64)]
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
        if re.fullmatch(r'(BO|AO)\d+-RB', name):
            return self.overrides.get(pv[:-3], 0)
        if re.fullmatch(r'AO\d+', name):
            return 0
        if re.search(r"(BI|BO)\d+(-RB)?$", name):
            return int(t % 4 < 2) if "BI" in name else 0
        if re.search(r"AI\d+", name):
            return round(2.5 + 1.8 * math.sin(t), 3)
        return 0
