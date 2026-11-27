"""Conservative offline expansion of ECMCCFG hardware database records."""
import re
from pathlib import Path

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|#[^\n]*|[{},=]|[^\s{},=#]+')
MACRO = re.compile(r'\$\{([A-Za-z_]\w*)(?:=([^{}]*))?\}|\$\(([A-Za-z_]\w*)(?:=([^()]*))?\)')
RECORD = re.compile(r'\brecord\s*\(\s*\w+\s*,\s*"([^"]+)"')
SAFE = re.compile(r'[A-Za-z0-9_.:-]+')

def uncomment(source):
    return re.sub(r'"(?:\\.|[^"\\])*"|#[^\n]*',
                  lambda m: '' if m[0].startswith('#') else m[0], source)

def tokens(source):
    return [m[0][1:-1] if m[0].startswith('"') else m[0]
            for m in TOKEN.finditer(source) if not m[0].startswith('#')]

def substitution_rows(source):
    """Yield template basename and macro mapping for explicit rows."""
    t = tokens(source)
    i = 0
    def group(i):
        if i >= len(t) or t[i] != '{':
            raise ValueError('expected group')
        end = t.index('}', i + 1)
        return t[i+1:end], end+1
    while i < len(t):
        if t[i] != 'file' or i+2 >= len(t) or t[i+2] != '{':
            i += 1
            continue
        name = Path(t[i+1]).name
        i += 3
        pattern = None
        while i < len(t) and t[i] != '}':
            if t[i] == 'pattern':
                pattern, i = group(i+1)
                pattern = [x for x in pattern if x != ',']
            elif t[i] == '{':
                row, i = group(i)
                if pattern is not None:
                    values = [x for x in row if x != ',']
                    if len(values) == len(pattern):
                        yield name, dict(zip(pattern, values))
                else:
                    values = {}
                    for j in range(len(row)-2):
                        if row[j+1] == '=':
                            values[row[j]] = row[j+2]
                    yield name, values
            else:
                i += 1
        i += 1

def record_suffixes(source, env):
    env = dict(env, ECMC_P='@PREFIX@')
    for match in RECORD.finditer(uncomment(source)):
        name = match[1]
        for _ in range(12):
            new = MACRO.sub(lambda m: env.get(m[1] or m[3],
                            (m[2] if m[1] else m[4]) if
                            (m[2] if m[1] else m[4]) is not None else m[0]), name)
            if new == name:
                break
            name = new
        if name.startswith('@PREFIX@') and SAFE.fullmatch(name[8:]):
            yield name[8:]

def merge_database(catalog, root):
    """Add read-only record candidates; do not infer permissions from DB types."""
    root = Path(root)
    db = root / 'db'
    templates = {}
    for path in sorted(db.rglob('*.template')):
        templates.setdefault(path.name, []).append(path)
    panels = catalog['panels']
    overview = catalog.setdefault('overview', {})
    database = {}
    warnings = []
    for path in sorted(db.rglob('ecmc*.substitutions')):
        if path.parent.name in ('core', 'generic', 'legacy', 'sequencer'):
            continue
        key = path.stem.removeprefix('ecmc')
        signals = set()
        try:
            for template, env in substitution_rows(path.read_text(encoding='utf-8')):
                matches = templates.get(template, [])
                if len(matches) != 1:
                    warnings.append(f'{path.relative_to(root)}: missing/ambiguous {template}')
                    continue
                signals.update(record_suffixes(matches[0].read_text(encoding='utf-8'), env))
        except (ValueError, UnicodeError) as exc:
            warnings.append(f'{path.relative_to(root)}: {exc}')
        database.setdefault(key, set()).update(signals)
    for key, signals in database.items():
        existing = set(panels.get(key, []))
        panels[key] = sorted(existing | signals)
        # Preserve deliberately compact specialized overviews.
        overview.setdefault(key, sorted(existing or signals))
    catalog['database'] = {k: sorted(v) for k,v in sorted(database.items())}
    catalog['database_warnings'] = warnings
    return catalog
