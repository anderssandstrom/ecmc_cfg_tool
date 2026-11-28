"""Inventory explicit ECMCCFG hardware definitions; unresolved macros remain reported."""
import re
from pathlib import Path

SETTING = re.compile(r'\bepicsEnvSet\s*\(\s*"(?P<key>ECMC_EC_HWTYPE|ECMC_HW_PANEL)"\s*,?\s*"(?P<value>[^"]+)"\s*\)')
LITERAL = re.compile(r'[A-Za-z0-9_.+\-]+')

INCLUDE = re.compile(r'\$\{SCRIPTEXEC\}\s+\$\{ecmccfg_DIR\}(ecmc[A-Za-z0-9_.+\-]+\.cmd)\b')

def inventory(root):
    root=Path(root)
    paths=sorted((root/'hardware').rglob('ecmc*.cmd'))
    by_name={}
    for path in paths:
        by_name.setdefault(path.name,[]).append(path)
    def local(path):
        values={}
        includes=[]
        for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
            if line.lstrip().startswith('#'):
                continue
            for m in SETTING.finditer(line):
                values.setdefault(m['key'],set()).add(m['value'])
            for m in INCLUDE.finditer(line):
                matches=by_name.get(m[1],[])
                if len(matches)==1:
                    includes.append(matches[0])
        return values,includes
    parsed={path:local(path) for path in paths}
    def inherited(path,trail=()):
        if path in trail or len(trail)>=20:
            return set()
        values,includes=parsed[path]
        result=set(values.get('ECMC_HW_PANEL',()))
        for child in includes:
            result.update(inherited(child,trail+(path,)))
        return result
    entries=[]
    for path in paths:
        values,_=parsed[path]
        if not values:
            continue
        types=sorted(values.get('ECMC_EC_HWTYPE',()))
        panels=sorted(inherited(path))
        entries.append({'file':str(path.relative_to(root)), 'definition':path.stem.removeprefix('ecmc'),
                        'types':types,'panels':panels,
                        'panel_source':'explicit' if values.get('ECMC_HW_PANEL') else 'inherited' if panels else 'unknown'})
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
