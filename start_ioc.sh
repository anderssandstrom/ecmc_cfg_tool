#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/qt_runtime.sh"

if [ "${1:-}" = "--demo" ]; then
  cd "${SCRIPT_DIR}"
  PYTHON_BIN="$(find_qt_python)" || {
    print_qt_python_error
    exit 1
  }
  exec "${PYTHON_BIN}" ecmc_ioc_navigator.py --demo
fi

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
SSH_HOST="${2:-${ETHERCAT_HOST:-}}"
SSH_HOST_PV="${ETHERCAT_HOST_PV:-MCU-Cfg-Hostname}"

cd "${SCRIPT_DIR}"
PYTHON_BIN="$(find_qt_python)" || {
  print_qt_python_error
  exit 1
}

exec "${PYTHON_BIN}" ecmc_ioc_navigator.py "${PREFIX}" \
  --ssh-host "${SSH_HOST}" \
  --ssh-host-pv "${SSH_HOST_PV}"
