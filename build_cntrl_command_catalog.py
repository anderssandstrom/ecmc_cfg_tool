#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


KEYWORDS = (
    re.compile(r'cntrl', re.IGNORECASE),
    re.compile(r'at\s*target|attarget', re.IGNORECASE),
    re.compile(r'scale\s*num|scalenum', re.IGNORECASE),
    re.compile(r'scale\s*denom|scaledenom', re.IGNORECASE),
)


def _match_command(cmd):
    hay = ' | '.join(
        [
            str(cmd.get('command', '')),
            str(cmd.get('command_named', '')),
            str(cmd.get('name', '')),
            str(cmd.get('description', '')),
            str(cmd.get('category', '')),
            str(cmd.get('parser_command', '')),
        ]
    )
    return any(p.search(hay) for p in KEYWORDS)


def main():
    ap = argparse.ArgumentParser(
        description='Build controller-focused command catalog (cntrl, at-target, scaleNum/scaleDenom).'
    )
    ap.add_argument('--in', dest='in_path', default='ecmc_commands.json', help='Input catalog JSON')
    ap.add_argument('--out', dest='out_path', default='ecmc_commands_cntrl.json', help='Output catalog JSON')
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)

    data = json.loads(in_path.read_text())
    commands = data.get('commands', []) if isinstance(data, dict) else []
    filtered = [c for c in commands if _match_command(c)]

    payload = {
        'generated_for': 'controller_tuning',
        'filter': ['*cntrl*', '*atTarget*', '*scaleNum*', '*scaleDenom*'],
        'commands': filtered,
    }
    out_path.write_text(json.dumps(payload, indent=2) + '\n')
    print(f'Wrote {out_path.resolve()} ({len(filtered)} / {len(commands)} commands)')


if __name__ == '__main__':
    main()
