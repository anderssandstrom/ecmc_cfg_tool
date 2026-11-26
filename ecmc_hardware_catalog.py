"""Read-only hardware PV catalog, independent of caQtDM at runtime."""
import json
from pathlib import Path
import re

CATALOG = Path(__file__).with_name("ecmc_hardware_catalog.json")
GENERIC = ("HWType", "PnlTyp", "Online", "Operational", "Alstate-Init",
           "Alstate-Preop", "Alstate-Safeop", "Alstate-Op")

def hardware_pvs(prefix, master, slave, candidates, catalog=None):
    if catalog is None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    panels = catalog["panels"]
    suffixes = list(GENERIC)
    for candidate in candidates:
        key = str(candidate or "").removesuffix(".ui").removeprefix("ecmc")
        if key in panels:
            suffixes.extend(panels[key])
            break
    base = f"{prefix.rstrip(':')}:m{int(master)}s{int(slave):03d}"
    return [(s, f"{base}-{s}") for s in dict.fromkeys(suffixes)
            if re.fullmatch(r"[A-Za-z0-9_.:-]+", s)]
