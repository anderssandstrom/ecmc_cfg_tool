"""Conservative integration of generated hardware metadata."""
import re

SAFE = re.compile(r'[A-Za-z0-9_.:-]+')
WRITE_KINDS = {'caToggleButton','caTextEntry','caMenu','caByteController','caNumeric','caSpinbox'}

def valid_signal(s):
    return isinstance(s,str) and bool(SAFE.fullmatch(s))

def merge_generated(curated,generated):
    panels=curated['panels']
    overview=curated.setdefault('overview',{})
    writes=curated.setdefault('writable',{})
    commands=curated.setdefault('commands',{})
    experts=curated.setdefault('experts',{})
    existing=set(panels)
    database=generated.get('database',{})
    generated_keys=[]
    output_records=generated.get('database_outputs',{})
    for key,signals in sorted(generated.get('panels',{}).items()):
        if not valid_signal(key):
            continue
        signals={s for s in signals if valid_signal(s)}
        if not signals:
            continue
        if key not in existing:
            generated_keys.append(key)
            panels[key]=sorted(signals)
        db={s for s in database.get(key,()) if valid_signal(s)}
        proposed=set(generated.get('overview',{}).get(key,signals)) & signals
        overview.setdefault(key,sorted((proposed & db) or proposed or signals))
        # Verified output records may be missing from the curated overview.
        # Add only those exact records and matching database-backed readbacks.
        if '_OPT' not in key:
            outputs={s:kind for s,kind in output_records.get(key,{}).items()
                     if s in db and ((re.fullmatch(r'BO\d+',s) and kind=='caToggleButton')
                                  or (re.fullmatch(r'BO\d+-Arr',s) and kind=='caByteController'))}
            if outputs:
                visible=set(outputs)|{s+'-RB' for s in outputs if s+'-RB' in db}
                panels[key]=sorted(set(panels[key])|visible)
                overview[key]=sorted(set(overview[key])|visible)
                writes.setdefault(key,{}).update(outputs)
        # Explicit UI controls backed by the exact device database only.
        # Preserve specialized controls and never generate raw drive-word writes.
        if '_OPT' not in key:
            permitted={s:kind for s,kind in generated.get('writable',{}).get(key,{}).items()
                       if s in db and s in panels[key] and (
                           (re.fullmatch(r'BO\d+',s) and kind=='caToggleButton') or
                           (re.fullmatch(r'AO\d+',s) and kind in ('caTextEntry','caNumeric','caSpinbox')) or
                           (re.fullmatch(r'BO\d+-Arr',s) and kind=='caByteController'))}
            for s,kind in permitted.items():
                writes.setdefault(key,{}).setdefault(s,kind)
            # Only explicit DS402 trigger records, not arbitrary controlword
            # writes or tuning / reset commands with device-specific semantics.
            for s,cmd in generated.get('commands',{}).get(key,{}).items():
                if (s in db and s in panels[key] and
                    re.fullmatch(r'Drv\d+-TrgDS402(?:Ena|Dis)',s) and
                    type(cmd.get('release')) is int and cmd['release']==1):
                    commands.setdefault(key,{}).setdefault(s,cmd)
        writes.setdefault(key,{})
        commands.setdefault(key,{})
        experts.setdefault(key,[e for e in generated.get('experts',{}).get(key,[]) if isinstance(e,dict) and 'file' in e])
    curated['generated_keys']=generated_keys
    curated['generated_source_commit']=generated.get('source_commit')
    curated['hardware_inventory']=generated.get('hardware_inventory',[])
    curated['generated_database']=database
    return curated

def demo_definitions(catalog):
    seen=set()
    for entry in catalog.get('hardware_inventory',[]):
        key=entry.get('definition')
        if not entry.get('types') or not valid_signal(key) or key in seen:
            continue
        seen.add(key)
        if catalog['panels'].get(key):
            yield key
