"""Build read-only hardware PV candidates from a local ecmccfg/qt checkout."""
import argparse
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

def extract_catalog(directory, source):
    roots = {p.name: ET.parse(p).getroot() for p in sorted(Path(directory).glob("*.ui"))}
    def collect(name, env, trail, out):
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
            if node.get("class") == "caInclude":
                collect(Path(expand(props.get("filename", ""), local)).name, local, trail + (name,), out)
            if node.get("class") == "caRelatedDisplay":
                args = props.get("args", "").split(";")
                for i, file in enumerate(props.get("files", "").split(";")):
                    target = Path(expand(file, local)).name
                    collect(target, macros(args[i] if i < len(args) else "", local), trail + (name,), out)
            for child in node:
                if child.tag != "property":
                    visit(child, local)
        visit(roots[name], env)
    panels = {}
    for name in sorted(roots):
        if not HARDWARE.fullmatch(name):
            continue
        out = set()
        collect(name, BASE, (), out)
        panels[name.removeprefix("ecmc").removesuffix(".ui")] = sorted(out)
    return {"source": source, "panels": panels}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("ecmc_hardware_catalog.json"))
    args = parser.parse_args()
    args.output.write_text(json.dumps(extract_catalog(args.directory, args.source), indent=2) + "\n", encoding="utf-8")
