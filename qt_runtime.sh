#!/usr/bin/env bash

_ecmc_qt_python_has_binding() {
  local candidate="$1"
  "${candidate}" -c 'import importlib.util, sys; sys.exit(0 if importlib.util.find_spec("PyQt5") or importlib.util.find_spec("PySide6") else 1)' >/dev/null 2>&1
}

find_qt_python() {
  local candidate=""

  if [ -n "${ECMC_PYTHON:-}" ] && command -v "${ECMC_PYTHON}" >/dev/null 2>&1; then
    if _ecmc_qt_python_has_binding "${ECMC_PYTHON}"; then
      printf '%s\n' "${ECMC_PYTHON}"
      return 0
    fi
  fi

  for candidate in python python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      if _ecmc_qt_python_has_binding "${candidate}"; then
        command -v "${candidate}"
        return 0
      fi
    fi
  done

  return 1
}

print_qt_python_error() {
  cat >&2 <<'EOF'
No usable Qt Python runtime was found.

The Qt tools in this repo require a Python interpreter with either PyQt5 or PySide6 installed.
Tried, in order:
  1. $ECMC_PYTHON (if set)
  2. python
  3. python3

If you are using Conda, activate the environment that has PyQt5/PySide6 before running ./start_*.sh.
You can also override the interpreter explicitly, for example:
  ECMC_PYTHON=/path/to/python ./start_iso230.sh
EOF
}
