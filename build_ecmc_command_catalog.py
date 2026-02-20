#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


def _normalize_signature(sig: str) -> str:
    s = sig.strip().replace('\\"', '"')
    s = re.sub(r'%\\"\\s*SCN[u|d]64\\s*\\"', '<i64>', s)
    s = re.sub(r'%\\"\\s*PRI[u|d]64\\s*\\"', '<i64>', s)
    s = s.replace('%[^)]', '<str>')
    s = s.replace('%[^,]', '<str>')
    s = s.replace('%[^\\n]', '<expr>')
    s = s.replace('%lf', '<float>')
    s = s.replace('%d', '<int>')
    s = s.replace('%u', '<uint>')
    s = s.replace('%x', '<hex>')
    s = s.replace('%c', '<char>')
    s = s.replace('%' + '"' + ' PRIx64 "', '<hex64>')
    s = s.replace('%' + '"' + ' PRIu64 "', '<u64>')
    s = s.replace('%' + '"' + ' PRId64 "', '<i64>')
    s = re.sub(r'\s+', ' ', s)
    return s


def _command_name(sig: str) -> str:
    s = sig.strip()
    if s.startswith('Main.M'):
        tail = s.split('.', 2)[-1]
        return tail.split('=')[0]
    if s.startswith('ADSPORT='):
        return 'ADSPORT'
    if '(' in s:
        return s.split('(', 1)[0]
    if '=' in s:
        return s.split('=', 1)[0]
    return s


def _command_key(sig: str) -> str:
    n = _command_name(sig).strip().lower()
    if n.startswith('cfg.'):
        n = n[4:]
    return n


def _category(sig: str) -> str:
    n = _command_name(sig)
    low = sig.lower()
    key = _command_key(sig)
    if key.startswith('ec') or 'ecentry' in key or 'sdo' in key or 'soe' in key:
        return 'EtherCAT'
    if 'plc' in key:
        return 'PLC'
    if 'storage' in key or 'lut' in key:
        return 'Storage/Misc'
    if 'plugin' in key:
        return 'Plugin'
    if key.startswith('setaxis') or key.startswith('getaxis') or key.startswith('move') or key.startswith('stop'):
        return 'Motion'
    if sig.startswith('Main.M') or n in {
        'bBusy?', 'bError?', 'nErrorId?', 'bEnable?', 'bEnabled?', 'bExecute?',
        'bReset?', 'bHomeSensor?', 'bLimitBwd?', 'bLimitFwd?', 'bHomed?',
        'bDone?', 'fActPosition?', 'fActVelocity?', 'fVelocity?', 'fPosition?',
        'nCommand?', 'nCmdData?', 'nMotionAxisID?', 'fAcceleration?',
        'fDeceleration?', 'stAxisStatus?', 'sErrorMessage?'
    }:
        return 'Motion'
    if n.startswith('Cfg.'):
        return 'Configuration'
    if n in {'GetControllerError', 'GetControllerErrorMessage', 'ControllerErrorReset', 'ValidateConfig'}:
        return 'General'
    return 'General'


def _valid_signature(sig: str) -> bool:
    s = sig.strip()
    if not s:
        return False
    if s in {'%d', '%u', '%lf', '%x'}:
        return False
    if s.startswith('%'):
        return False
    if s.startswith('Cfg.') or s.startswith('Main.') or s.startswith('ADSPORT='):
        return True
    if '(' in s or '?' in s or '=' in s:
        return True
    return False


def _runtime_meta(command_template: str, parser_sig: str):
    # Conservative classification based on parser routing and intended use.
    # - Cfg.* routed to handleCfgCommand() => configuration phase (not runtime-safe by default).
    # - Main.M*, ADSPORT=, Move*/Stop*, Get*/Read*/Write* are runtime operations/queries.
    c = command_template.strip()
    n = _command_name(c)

    if c.startswith('Cfg.'):
        return {
            'runtime_safe': False,
            'runtime_class': 'config_only',
            'runtime_note': 'Configuration command; typically intended before entering runtime mode.',
        }

    if c.startswith('Main.M') or c.startswith('ADSPORT='):
        return {
            'runtime_safe': True,
            'runtime_class': 'runtime',
            'runtime_note': 'TwinCAT-style runtime command/status access.',
        }

    if c.endswith('?'):
        return {
            'runtime_safe': True,
            'runtime_class': 'runtime',
            'runtime_note': 'Status/readback query command.',
        }

    if n.startswith('Move') or n.startswith('Stop'):
        return {
            'runtime_safe': True,
            'runtime_class': 'runtime',
            'runtime_note': 'Motion execution command intended for runtime operation.',
        }

    if n.startswith('Get') or n.startswith('Read'):
        return {
            'runtime_safe': True,
            'runtime_class': 'runtime',
            'runtime_note': 'Read/query command usable during runtime.',
        }

    if n.startswith('Write') and not n.startswith('WriteDataStorage'):
        return {
            'runtime_safe': True,
            'runtime_class': 'runtime',
            'runtime_note': 'Direct write command generally usable during runtime.',
        }

    if n in {'WriteDataStorage', 'AppendDataStorage'}:
        return {
            'runtime_safe': True,
            'runtime_class': 'runtime',
            'runtime_note': 'Storage buffer operation; allowed at runtime in parser.',
        }

    return {
        'runtime_safe': False,
        'runtime_class': 'unknown',
        'runtime_note': 'Runtime behavior not clearly guaranteed; verify for your application state.',
    }


def parse_parser_commands(parser_file: Path):
    text = parser_file.read_text(errors='ignore')
    cfg_start = text.find('static int handleCfgCommand')
    main_start = text.find('int motorHandleOneArg')

    patterns = [
        r'sscanf\s*\(\s*myarg_1\s*,\s*"([^"]+)"',
        r'strcmp\s*\(\s*myarg_1\s*,\s*"([^"]+)"\s*\)',
        r'!strcmp\s*\(\s*myarg_1\s*,\s*"([^"]+)"\s*\)',
    ]

    found = []
    for pat in patterns:
        for m in re.finditer(pat, text):
            raw = m.group(1)
            sig = _normalize_signature(raw)
            if _valid_signature(sig):
                line = text[: m.start()].count('\n') + 1
                found.append((sig, line))

    out = {}
    for sig, line in found:
        if sig not in out:
            # Commands parsed in handleCfgCommand are configuration commands.
            # They typically require "Cfg." prefix in external command strings.
            pos = text.find(sig.replace('<int>', '%d').replace('<float>', '%lf'))
            in_cfg_scope = False
            if cfg_start >= 0 and main_start > cfg_start:
                # Use line number as robust proxy for scope.
                cfg_line_start = text[:cfg_start].count('\n') + 1
                main_line_start = text[:main_start].count('\n') + 1
                in_cfg_scope = cfg_line_start <= line < main_line_start
            out[sig] = {'line': line, 'cfg_scope': in_cfg_scope}
    return out


def _clean_comment_block(block: str):
    lines = []
    for raw in block.splitlines():
        line = re.sub(r'^\s*\*\s?', '', raw).strip()
        if line:
            lines.append(line)
    return lines


def _extract_summary(lines):
    for ln in lines:
        if '\\brief' in ln:
            s = ln.replace('\\brief', '').strip()
            if s:
                return s
    for ln in lines:
        low = ln.lower()
        if 'command string to ecmccmdparser.c' in low:
            continue
        if ln.startswith('"') or ln.startswith('- '):
            continue
        if ln.startswith('\\param') or ln.startswith('\\note') or ln.startswith('\\return'):
            continue
        if len(ln) < 8:
            continue
        return ln
    return ''


def _extract_param_names(lines):
    names = []
    seen = set()
    for ln in lines:
        m = re.search(r'\\param(?:\s*\[[^\]]+\])?\s+([A-Za-z_][A-Za-z0-9_]*)', ln)
        if not m:
            continue
        name = m.group(1).strip().strip(',:.;')
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _extract_commands_from_lines(lines):
    cmds = []
    for ln in lines:
        for q in re.findall(r'"([^"\n]+)"', ln):
            if '(' in q or q.startswith('Main.') or q.startswith('ADSPORT=') or q.endswith('?'):
                cmd = _normalize_signature(q)
                if _valid_signature(cmd):
                    cmds.append(cmd)
    return cmds


def _extract_following_function_name(text: str, block_end: int):
    # Look shortly after the doc block for the associated C/C++ function name.
    tail = text[block_end:block_end + 600]
    m = re.search(
        r'\b(?:int|void|double|float|size_t|uint64_t|uint32_t|int64_t|char\s*\*|const\s+char\s*\*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(',
        tail,
        re.MULTILINE,
    )
    if not m:
        return ''
    return m.group(1)


def _choose_best_doc(sig, doc_candidates):
    if not doc_candidates:
        return None

    name = _command_key(sig)
    best = None
    best_score = -1
    for doc in doc_candidates:
        doc_cmd = doc.get('example', '')
        doc_name = _command_key(doc_cmd)
        score = 0
        if doc_name == name:
            score += 10
        if doc_cmd == sig:
            score += 20
        if sig.startswith('Main.M') and doc_cmd.startswith('Main.M'):
            score += 5
        if sig.startswith('Cfg.') and doc_cmd.startswith('Cfg.'):
            score += 5
        if len(doc.get('summary', '')) > 40:
            score += 1
        if score > best_score:
            best_score = score
            best = doc
    return best


def _command_template(sig, info, cfg_scope=False):
    if sig.startswith('Cfg.') or sig.startswith('Main.') or sig.startswith('ADSPORT='):
        return sig
    if cfg_scope:
        return f'Cfg.{sig}'
    if info:
        ex = str(info.get('example', '')).strip()
        if ex.lower().startswith('cfg.'):
            return f'Cfg.{sig}'
    return sig


def _apply_param_names(template, param_names):
    if not param_names:
        return template
    i = 0

    def repl(match):
        nonlocal i
        if i < len(param_names):
            name = param_names[i]
            i += 1
            return f'<{name}>'
        return match.group(0)

    return re.sub(r'<[^>]+>', repl, template)


def parse_header_explanations(repo_root: Path):
    explanations_by_name = {}
    header_files = sorted((repo_root / 'devEcmcSup').rglob('*.h'))

    for path in header_files:
        rel = str(path.relative_to(repo_root))
        text = path.read_text(errors='ignore')
        for m in re.finditer(r'/\*\*(.*?)\*/', text, re.DOTALL):
            block = m.group(1)
            lines = _clean_comment_block(block)
            if not lines:
                continue
            summary = _extract_summary(lines)
            if not summary:
                continue
            param_names = _extract_param_names(lines)
            cmds = _extract_commands_from_lines(lines)
            func_name = _extract_following_function_name(text, m.end())
            payload = {
                'summary': summary,
                'header': rel,
                'example': cmds[0] if cmds else '',
                'param_names': param_names,
            }

            # Primary mapping from explicit command examples in docs.
            for cmd in cmds:
                key = _command_key(cmd)
                explanations_by_name.setdefault(key, []).append(payload)

            # Fallback mapping from function name in header declaration.
            if func_name:
                func_key = func_name.strip().lower()
                explanations_by_name.setdefault(func_key, []).append(payload)

    return explanations_by_name, [str(p.relative_to(repo_root)) for p in header_files]


def build_catalog(repo_root: Path):
    parser_file = repo_root / 'devEcmcSup/com/ecmcCmdParser.c'
    parser_cmds = parse_parser_commands(parser_file)
    headers_by_name, scanned_headers = parse_header_explanations(repo_root)

    items = []
    for sig in sorted(parser_cmds.keys()):
        name = _command_name(sig)
        key = _command_key(name)
        info = _choose_best_doc(sig, headers_by_name.get(key, []))
        cfg_scope = bool(parser_cmds[sig].get('cfg_scope', False))
        template = _command_template(sig, info, cfg_scope=cfg_scope)
        param_names = info.get('param_names', []) if info else []
        named_template = _apply_param_names(template, param_names)
        rt = _runtime_meta(template, sig)

        items.append(
            {
                'command': template,
                'command_named': named_template,
                'param_names': param_names,
                'parser_command': sig,
                'name': name,
                'category': _category(sig),
                'description': info['summary'] if info else '',
                'header_source': info['header'] if info else '',
                'header_example': info['example'] if info else '',
                'parser_source': f"devEcmcSup/com/ecmcCmdParser.c:{parser_cmds[sig]['line']}",
                'runtime_safe': rt['runtime_safe'],
                'runtime_class': rt['runtime_class'],
                'runtime_note': rt['runtime_note'],
            }
        )

    return {
        'generated_from': {
            'parser': 'devEcmcSup/com/ecmcCmdParser.c',
            'headers_root': 'devEcmcSup',
            'header_count_scanned': len(scanned_headers),
        },
        'command_count': len(items),
        'commands': items,
    }


def main():
    ap = argparse.ArgumentParser(description='Build ecmc command catalog from parser + headers')
    ap.add_argument('--repo-root', default='.', help='Path to ecmc repository root')
    ap.add_argument('--out', default='ecmc_commands.json', help='Output JSON file')
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    out_path = Path(args.out).resolve()

    catalog = build_catalog(repo_root)
    out_path.write_text(json.dumps(catalog, indent=2) + '\n')
    print(
        f"Wrote {out_path} "
        f"({catalog['command_count']} commands, scanned {catalog['generated_from']['header_count_scanned']} headers)"
    )


if __name__ == '__main__':
    main()
