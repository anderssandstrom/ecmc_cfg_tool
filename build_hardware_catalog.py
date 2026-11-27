"""Build hardware PV and control metadata from a local ecmccfg/qt checkout."""
import argparse
from ecmc_hardware_database import merge_database
from ecmc_hardware_inventory import augment_catalog, coverage_summary
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

BASE = {"IOC": "@IOC@", "SYS": "@IOC@", "MasterID": "@MASTER@", "SlaveID": "@SLAVE@"}
PREFIX = "@IOC@:m@MASTER@s@SLAVE@-"
HARDWARE = re.compile(r"^ecmc(?:E[LKPXxD]|E_slave|M[BOS]|N6|iPOS|CD02|CMMT|Opto|MCS2|GenericSlave|NoProcessData|Passive|PowerSupply|EncTouchProbe).*\.ui$")

def expand(text, env):
    return re.sub(r"\$\(([^)]+)\)", lambda m: env.get(m[1], m[0]), text)

def macros(text, env):
    result = dict(env)
    for part in text.split(","):
        key, sep, value = part.partition("=")
        if sep:
            result[key.strip()] = expand(value.strip(), env)
    return result

WRITE_WIDGETS = {"caTextEntry", "caToggleButton", "caMenu", "caByteController", "caNumeric", "caSpinbox"}

def extract_catalog(directory, source, source_commit=None):
    directory = Path(directory)
    root = directory if (directory / "db").is_dir() else directory.parent
    if (directory / "qt").is_dir():
        directory = directory / "qt"
    roots = {p.name: ET.parse(p).getroot() for p in sorted(Path(directory).glob("*.ui"))}
    def collect(name, env, trail, out, writable, experts, commands, scope=None, ancestry=()):
        if name not in roots or name in trail or len(trail) > 15:
            return
        def visit(node, context):
            props = {p.get("name"): p.findtext("string", p.findtext("cstring", ""))
                     for p in node.findall("property")}
            local = macros(props.get("macro", ""), context)
            for key, value in props.items():
                if re.fullmatch(r"channels?|channel[A-D]", key or ""):
                    for value in re.split(r"[;,\n]", value):
                        pv = expand(value, local)
                        suffix = pv[len(PREFIX):]
                        if pv.startswith(PREFIX) and re.fullmatch(r"[A-Za-z0-9_.:-]+", suffix):
                            out.add(suffix)
                            if scope is not None:
                                scope['signals'].add(suffix)
                            if key == 'channel' and node.get('class') in WRITE_WIDGETS:
                                writable[suffix] = node.get('class')
                                if scope is not None:
                                    scope['writable'][suffix] = node.get('class')
                            if key == 'channel' and node.get('class') == 'caMessageButton':
                                press = expand(props.get('pressMessage', ''), local).strip()
                                release = expand(props.get('releaseMessage', ''), local).strip()
                                # Only release-only integer commands are supported by the native GUI.
                                if not press and re.fullmatch(r'[+-]?\d+', release):
                                    command = {'label': props.get('label', '') or suffix,
                                               'release': int(release)}
                                    commands.setdefault(suffix, []).append(command)
            if node.get("class") == "caInclude":
                collect(Path(expand(props.get("filename", ""), local)).name, local, trail + (name,), out, writable, experts, commands, scope, ancestry)
            if node.get("class") == "caRelatedDisplay":
                args = props.get("args", "").split(";")
                for i, file in enumerate(props.get("files", "").split(";")):
                    target = Path(expand(file, local)).name
                    if not target or target not in roots or target in trail or target == name:
                        continue
                    if re.search(r"graph|trend|plot", target, re.I):
                        continue
                    context = macros(args[i] if i < len(args) else "", local)
                    labels = props.get("labels", "").split(";")
                    label = labels[i].strip() if i < len(labels) else ""
                    scope_key = (target, tuple(sorted(context.items())), ancestry)
                    if scope_key not in experts:
                        expert = {"file": target, "label": label or target,
                                  "macros": context, "parents": list(ancestry),
                                  "signals": set(), "writable": {}}
                        experts[scope_key] = expert
                        collect(target, context, trail + (name,), out, writable,
                                experts, commands, expert, ancestry + (target,))
            for child in node:
                if child.tag != "property":
                    visit(child, local)
        visit(roots[name], env)
    panels = {}
    writes = {}
    overview = {}
    expert_panels = {}
    command_panels = {}
    for name in sorted(roots):
        if not HARDWARE.fullmatch(name):
            continue
        out = set()
        writable = {}
        experts = {}
        commands = {}
        main = {'signals': set(), 'writable': {}}
        collect(name, BASE, (), out, writable, experts, commands, main)
        key = name.removeprefix("ecmc").removesuffix(".ui")
        panels[key] = sorted(out)
        writes[key] = dict(sorted(writable.items()))
        # Conflicting commands on the same PV must not be collapsed to an arbitrary action.
        command_panels[key] = {pv: entries[0] for pv, entries in sorted(commands.items())
                               if all(entry == entries[0] for entry in entries)}
        promoted = set()
        if key == 'EL7062':
            promoted = {f'Drv{ch}-{suffix}' for ch in ('01', '02')
                        for suffix in ('Cmd', 'Stat', 'Spd', 'Cmd-RB', 'TrgDS402Ena', 'TrgDS402Dis')}
        overview[key] = sorted(main['signals'] | (promoted & out))
        expert_panels[key] = [dict(e, signals=sorted(e['signals'])) for e in experts.values() if e['signals']]
    catalog = {"source": source, "panels": panels, "writable": writes,
            "overview": overview, "experts": expert_panels, "commands": command_panels,
            "source_commit": source_commit}
    if (root / "db").is_dir():
        merge_database(catalog, root)
    if (root / "hardware").is_dir():
        augment_catalog(catalog, root)
    return catalog

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-commit")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("ecmc_hardware_catalog.json"))
    args = parser.parse_args()
    catalog = extract_catalog(args.directory, args.source, args.source_commit)
    args.output.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(coverage_summary(catalog), indent=2))
