"""Qt-independent EPICS client and PV naming helpers.

Preserves the legacy epicsPV, pyepics and CLI backend selection.
"""
import re
import math
import shutil
import subprocess

FLOAT_DISPLAY_COMMA_RE = re.compile(r'^[+-]?(?:(?:\d+,\d*)|(?:,\d+)|(?:\d+(?:,\d*)?[eE][+-]?\d+)|(?:,\d+[eE][+-]?\d+))$')

class EpicsClient:
    def __init__(self, timeout=2.0):
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('EPICS timeout must be finite and positive')
        self.backend = None
        self._epics = None
        self._epicspv_mod = None
        self._epicspv_cache = {}
        self._caput_bin = shutil.which('caput')
        self._caget_bin = shutil.which('caget')
        self._cli_available = bool(self._caput_bin and self._caget_bin)

        try:
            import epicsPV  # type: ignore

            self._epicspv_mod = epicsPV
        except Exception:
            self._epicspv_mod = None

        try:
            import epics  # type: ignore

            self._epics = epics
        except Exception:
            self._epics = None

        if self._epicspv_mod is not None:
            self.backend = 'epicsPV'
            return
        if self._epics is not None:
            self.backend = 'pyepics'
            return

        if self._cli_available:
            self.backend = 'cli'
            return

        raise RuntimeError('No EPICS client available. Install pyepics or ensure caget/caput are in PATH.')

    def _is_missing_ca_dll_error(self, ex):
        msg = str(ex).lower()
        return ('cannot find epics ca dll' in msg) or ('cannot load ca dll' in msg)

    def _fallback_if_backend_unavailable(self, ex):
        """Change backend only when the Channel Access library cannot load."""
        if self.backend not in {'pyepics', 'epicsPV'} or not self._is_missing_ca_dll_error(ex):
            return False

        if self.backend == 'epicsPV':
            self._epicspv_mod = None
            self._epicspv_cache = {}
            if self._epics is not None:
                self.backend = 'pyepics'
                return True

        if self._cli_available:
            self.backend = 'cli'
            self._epics = None
            self._epicspv_mod = None
            self._epicspv_cache = {}
            return True
        return False

    def _epicspv(self, pv):
        cached = self._epicspv_cache.get(pv)
        if cached is not None:
            try:
                cached.setTimeout(float(self.timeout))
            except Exception:
                pass
            return cached
        obj = self._epicspv_mod.epicsPV(str(pv))
        obj.setTimeout(float(self.timeout))
        self._epicspv_cache[pv] = obj
        return obj

    def _parse_cli_caget_value(self, pv, raw_out):
        s = str(raw_out or '').strip()
        if not s:
            return s
        # caget output can include PV name and alarm trailer:
        #   "<pv> <value> SEVR:... STAT:..."
        # Keep only the value payload.
        if s.startswith(str(pv)):
            s = s[len(str(pv)):].strip()
        if ' SEVR:' in s:
            s = s.split(' SEVR:', 1)[0].rstrip()
        if ' STAT:' in s:
            s = s.split(' STAT:', 1)[0].rstrip()
        # Some sites prepend a leading timestamp column, or "<undefined>"
        # when no timestamp is available. Drop those first-column tokens.
        parts = s.split()
        while len(parts) > 1:
            first = parts[0].strip()
            low = first.lower()
            if low in {'<undefined>', 'undefined'}:
                parts.pop(0)
                continue
            # Timestamp-like token (must include separators; plain numeric value
            # like "1" should not be treated as a timestamp).
            if (
                ':' in first
                or 't' in low
                or first.endswith('z')
                or '/' in first
                or '-' in first
                or '.' in first
            ) and any(ch.isdigit() for ch in first):
                parts.pop(0)
                continue
            break
        s = ' '.join(parts)
        # Many caget variants quote string/scalar values.
        if len(s) >= 2 and s[0] == s[-1] and s[0] in {'"', "'"}:
            s = s[1:-1]
        # Locale-dependent caget output may use decimal comma.
        if FLOAT_DISPLAY_COMMA_RE.match(s) and '.' not in s:
            s = s.replace(',', '.')
        return s

    @staticmethod
    def _validate_pv(pv):
        name = str(pv or "").strip()
        if not name or name.startswith("-") or any(ch.isspace() or ord(ch) < 32 for ch in name):
            raise ValueError("Invalid EPICS PV name")
        return name

    def put(self, pv, value, wait=True):
        pv = self._validate_pv(pv)
        if self.backend == 'epicsPV':
            try:
                obj = self._epicspv(str(pv))
                if wait:
                    obj.putWait(value)
                else:
                    obj.putw(value)
                return
            except Exception as ex:
                if self._fallback_if_backend_unavailable(ex):
                    return self.put(pv, value, wait=wait)
                raise

        if self.backend == 'pyepics':
            try:
                ok = self._epics.caput(pv, value, wait=wait, timeout=self.timeout)
                if ok is None or ok <= 0:
                    raise RuntimeError(f'caput failed for {pv}')
                return
            except Exception as ex:
                if self._fallback_if_backend_unavailable(ex):
                    return self.put(pv, value, wait=wait)
                raise

        cmd = [str(self._caput_bin or 'caput'), '-t']
        # In CLI mode, allow non-blocking puts for bulk operations.
        if wait:
            cmd.extend(['-w', str(float(self.timeout))])
        else:
            cmd.extend(['-w', '0'])
        cmd.extend([pv, str(value)])
        proc = subprocess.run(
            cmd,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout + 2.0,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'caput failed for {pv}')

    def get(self, pv, as_string=True):
        pv = self._validate_pv(pv)
        if self.backend == 'epicsPV':
            try:
                obj = self._epicspv(str(pv))
                val = obj.getw()
                if val is None:
                    raise RuntimeError(f'caget failed for {pv}')
                return str(val)
            except Exception as ex:
                if self._fallback_if_backend_unavailable(ex):
                    return self.get(pv, as_string=as_string)
                raise

        if self.backend == 'pyepics':
            try:
                val = self._epics.caget(pv, as_string=as_string, timeout=self.timeout)
                if val is None:
                    raise RuntimeError(f'caget failed for {pv}')
                return str(val)
            except Exception as ex:
                if self._fallback_if_backend_unavailable(ex):
                    return self.get(pv, as_string=as_string)
                raise

        # Prefer a clean value-only output to avoid PV/alarm text mixing
        # into application-level parsing (e.g. axis object-id discovery).
        cmd = [str(self._caget_bin or 'caget'), '-t', '-w', str(float(self.timeout)), '-noname', '-nostat', '-nounit', pv]
        proc = subprocess.run(
            cmd,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.timeout + 2.0,
        )
        if proc.returncode != 0:
            # Some caget variants may not support all formatting flags.
            # Fallback to plain terse mode and parse best-effort.
            proc = subprocess.run(
                [str(self._caget_bin or 'caget'), '-t', '-w', str(float(self.timeout)), pv],
                universal_newlines=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout + 2.0,
            )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f'caget failed for {pv}')
        return self._parse_cli_caget_value(pv, proc.stdout)


def _join_prefix_pv(prefix, suffix):
    p = str(prefix or '').strip()
    s = str(suffix or '').strip()
    if not p:
        return s
    if p.endswith(':'):
        return f'{p}{s}'
    return f'{p}:{s}'


def _proc_pv_for_readback(pv):
    p = str(pv or '').strip()
    if not p:
        return ''
    if '.' in p:
        return f"{p.split('.', 1)[0]}.PROC"
    return f'{p}.PROC'


