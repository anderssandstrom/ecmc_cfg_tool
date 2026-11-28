"""Read-only hardware PV catalog, independent of caQtDM at runtime."""
import json
from ecmc_generated_catalog import merge_generated, demo_definitions
import os
from functools import lru_cache
from pathlib import Path
import re

CATALOG = Path(__file__).with_name("ecmc_hardware_catalog.json")
GENERIC = ("HWType", "PnlTyp", "Online", "Operational", "Alstate-Init",
           "Alstate-Preop", "Alstate-Safeop", "Alstate-Op")

@lru_cache(maxsize=2)
def _load_catalog(path):
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    if Path(path).resolve() == CATALOG.resolve():
        snapshot=CATALOG.with_name("ecmc_hardware_generated.json")
        if snapshot.is_file():
            merge_generated(catalog,json.loads(snapshot.read_text(encoding="utf-8")))
        extra = json.loads(CATALOG.with_name("ecmc_hardware_variants.json").read_text(encoding="utf-8"))
        for key, signals in extra["panels"].items():
            catalog["panels"][key] = sorted(set(catalog["panels"].get(key, [])) | set(signals))
            catalog.setdefault("overview", {}).setdefault(key, signals)
    return catalog

def load_catalog():
    return _load_catalog(os.environ.get("ECMC_HARDWARE_CATALOG", str(CATALOG)))

def hardware_pvs(prefix, master, slave, candidates, catalog=None):
    if catalog is None:
        catalog = load_catalog()
    panels = catalog["panels"]
    suffixes = list(GENERIC)
    for candidate in candidates:
        key = str(candidate or "").removesuffix(".ui").removeprefix("ecmc")
        if key in panels:
            suffixes.extend(panels[key])
    base = f"{prefix.rstrip(':')}:m{int(master)}s{int(slave):03d}"
    return [(s, f"{base}-{s}") for s in dict.fromkeys(suffixes)
            if re.fullmatch(r"[A-Za-z0-9_.:-]+", s)]

def _ordered_keys(candidates,catalog):
    known=list(dict.fromkeys(str(c or "").removesuffix(".ui").removeprefix("ecmc") for c in candidates))
    known=[k for k in known if k in catalog["panels"]]
    if known:
        # Use an exact device mapping only where inventory confirms that its
        # panel is the one reported by the IOC. Never guess a variant.
        first=known[0]
        exact={e["definition"] for e in catalog.get("hardware_inventory",[])
               if e.get("resolved_panel")==first}
        matches=[k for k in known[1:] if k in exact and k in catalog.get("generated_database",{})]
        if matches:
            return matches+[k for k in known if k not in matches]
    return known

def hardware_writable_pvs(prefix, master, slave, candidates, catalog=None):
    """Only channels explicitly associated with supported write widgets."""
    if catalog is None:
        catalog = load_catalog()
    base = f"{prefix.rstrip(':')}:m{int(master)}s{int(slave):03d}"
    for key in _ordered_keys(candidates,catalog):
        if key in catalog["panels"]:
            return {f"{base}-{suffix}": kind for suffix, kind in
                    catalog.get("writable", {}).get(key, {}).items()
                    if suffix in catalog["panels"][key] and
                    re.fullmatch(r"[A-Za-z0-9_.:-]+", suffix)}
    return {}

def hardware_views(prefix, master, slave, candidates, catalog=None):
    """Overview and expert groups with resolved PVs; legacy catalogs still work."""
    if catalog is None:
        catalog = load_catalog()
    pairs = hardware_pvs(prefix, master, slave, candidates, catalog)
    by_name = dict(pairs)
    for key in _ordered_keys(candidates,catalog):
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

def hardware_commands(prefix, master, slave, candidates, catalog=None):
    if catalog is None:
        catalog = load_catalog()
    base = f"{prefix.rstrip(':')}:m{int(master)}s{int(slave):03d}"
    for key in _ordered_keys(candidates,catalog):
        if key in catalog["panels"]:
            return {f"{base}-{suffix}": cmd for suffix, cmd in catalog.get("commands", {}).get(key, {}).items()
                    if suffix in catalog["panels"][key] and re.fullmatch(r"[A-Za-z0-9_.:-]+", suffix)}
    return {}
