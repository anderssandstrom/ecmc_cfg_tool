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
    for key,signals in sorted(generated.get('panels',{}).items()):
        if not valid_signal(key) or key in existing:
            continue
        signals={s for s in signals if valid_signal(s)}
        if not signals:
            continue
        generated_keys.append(key)
        panels[key]=sorted(signals)
        db={s for s in database.get(key,()) if valid_signal(s)}
        proposed=set(generated.get('overview',{}).get(key,signals)) & signals
        overview[key]=sorted((proposed & db) or proposed or signals)
        # Initially enable only exact DB-backed binary output controls.
        # Other write types require review of command semantics.
        writes[key]={s:kind for s,kind in generated.get('writable',{}).get(key,{}).items()
                     if s in db and s in signals and re.fullmatch(r'BO\d+',s)
                     and kind=='caToggleButton'}
        commands[key]={}
        experts[key]=[e for e in generated.get('experts',{}).get(key,[]) if isinstance(e,dict) and 'file' in e]
    curated['generated_keys']=generated_keys
    curated['generated_source_commit']=generated.get('source_commit')
    curated['hardware_inventory']=generated.get('hardware_inventory',[])
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
