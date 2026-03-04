#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path


DEFINE_RE = re.compile(r"^\s*#define\s+([A-Za-z0-9_]+)\s+(0x[0-9A-Fa-f]+)\b")


def build_error_entries(header_path):
    entries = []
    for line in Path(header_path).read_text(errors="ignore").splitlines():
        m = DEFINE_RE.match(line)
        if not m:
            continue
        name, hex_value = m.groups()
        code_dec = int(hex_value, 16)
        entries.append(
            {
                "code_dec": code_dec,
                "code_hex": f"0x{code_dec:X}",
                "name": name,
            }
        )
    entries.sort(key=lambda x: x["code_dec"])
    return entries


def default_header_path():
    return Path(__file__).resolve().parent / "../ecmc_repos/ECMC/ecmc/devEcmcSup/main/ecmcErrorsList.h"


def main():
    ap = argparse.ArgumentParser(
        description="Build local ECMC error DB JSON from ecmcErrorsList.h"
    )
    ap.add_argument(
        "--header",
        default=str(default_header_path()),
        help="Path to ecmcErrorsList.h",
    )
    ap.add_argument(
        "--out",
        default="ecmc_error_codes.json",
        help="Output JSON file",
    )
    args = ap.parse_args()

    header = Path(args.header).resolve()
    out = Path(args.out).resolve()
    if not header.exists():
        print(f"ERROR: header not found: {header}", file=sys.stderr)
        return 1

    errors = build_error_entries(header)
    payload = {
        "generated_from": str(header),
        "count": len(errors),
        "errors": errors,
    }
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Wrote {out} ({len(errors)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
