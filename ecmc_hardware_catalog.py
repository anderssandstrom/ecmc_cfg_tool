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

def hardware_writable_pvs(prefix, master, slave, candidates, catalog=None):
    """Only channels explicitly associated with supported write widgets."""
    if catalog is None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    base = f"{prefix.rstrip(':')}:m{int(master)}s{int(slave):03d}"
    for candidate in candidates:
        key = str(candidate or "").removesuffix(".ui").removeprefix("ecmc")
        if key in catalog["panels"]:
            return {f"{base}-{suffix}": kind for suffix, kind in
                    catalog.get("writable", {}).get(key, {}).items()
                    if suffix in catalog["panels"][key] and
                    re.fullmatch(r"[A-Za-z0-9_.:-]+", suffix)}
    return {}

def hardware_views(prefix, master, slave, candidates, catalog=None):
    """Overview and expert groups with resolved PVs; legacy catalogs still work."""
    if catalog is None:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    pairs = hardware_pvs(prefix, master, slave, candidates, catalog)
    by_name = dict(pairs)
    for candidate in candidates:
        key = str(candidate or "").removesuffix(".ui").removeprefix("ecmc")
        if key not in catalog["panels"]:
            continue
        overview = set(catalog.get("overview", {}).get(key, catalog["panels"][key])) | set(GENERIC)
        groups = []
        for expert in catalog.get("experts", {}).get(key, []):
            context = ", ".join(f"{k}={v}" for k,v in expert.get("macros", {}).items()
                                if k not in ("IOC", "SYS", "MasterID", "SlaveID"))
            title = expert.get("label") or expert["file"]
            title += " — " + expert["file"].removesuffix(".ui")
            if context:
                title += " (" + context + ")"
            groups.append((title, [(s, by_name[s]) for s in expert["signals"] if s in by_name]))
        return [(s,pv) for s,pv in pairs if s in overview], groups
    return pairs, []
