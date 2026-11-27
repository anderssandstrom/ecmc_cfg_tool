"""Inventory explicit ECMCCFG hardware definitions; unresolved macros remain reported."""
import re
from pathlib import Path

SETTING = re.compile(r'\bepicsEnvSet\s*\(\s*"(?P<key>ECMC_EC_HWTYPE|ECMC_HW_PANEL)"\s*,?\s*"(?P<value>[^"]+)"\s*\)')
LITERAL = re.compile(r'[A-Za-z0-9_.+\-]+')

def inventory(root):
    root=Path(root)
    entries=[]
    for path in sorted((root/'hardware').rglob('ecmc*.cmd')):
        values={}
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
            if line.lstrip().startswith('#'):
                continue
            for m in SETTING.finditer(line):
                values.setdefault(m['key'],set()).add(m['value'])
        if not values:
            continue
        types=sorted(values.get('ECMC_EC_HWTYPE',()))
        panels=sorted(values.get('ECMC_HW_PANEL',()))
        entries.append({'file':str(path.relative_to(root)), 'definition':path.stem.removeprefix('ecmc'),
                        'types':types,'panels':panels})
    return entries

def augment_catalog(catalog,root):
    entries=inventory(root)
    panels=catalog['panels']
    overview=catalog['overview']
    writes=catalog['writable']
    commands=catalog['commands']
    experts=catalog['experts']
    audit=[]
    for entry in entries:
        key=entry['definition']
        matches=[p for p in entry['panels'] if LITERAL.fullmatch(p) and p in panels]
        # Never guess which panel is active for conditional definitions.
        panel=matches[0] if len(entry['panels'])==1 and len(matches)==1 else None
        database=set(catalog.get('database',{}).get(key,()))
        if panel:
            # Keep the definition-specific catalogue separate from shared panel variants.
            original=set(panels.get(key,()))
            signals=original|database|set(panels[panel])
            panels[key]=sorted(signals)
            overview[key]=sorted(set(overview.get(panel,()))|database)
            writes[key]=dict(writes.get(panel,{}))
            commands[key]=dict(commands.get(panel,{}))
            experts[key]=list(experts.get(panel,()))
        signals=panels.get(key,())
        audit.append(dict(entry, resolved_panel=panel, signals=len(signals),
                          writable=len(writes.get(key,{})), commands=len(commands.get(key,{})),
                          status='mapped' if signals else 'generic-only'))
    catalog['hardware_inventory']=audit
    return catalog

def coverage_summary(catalog):
    entries=catalog.get('hardware_inventory',[])
    return {'definitions':len(entries),'mapped':sum(e['status']=='mapped' for e in entries),
            'generic_only':sum(e['status']=='generic-only' for e in entries),
            'unresolved':sum(not e['resolved_panel'] for e in entries),
            'database_warnings':len(catalog.get('database_warnings',[]))}
