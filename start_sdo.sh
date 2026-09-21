#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/qt_runtime.sh"

if [ "${ECMC_SDO_ASKPASS:-}" = "1" ]; then
  cd "${SCRIPT_DIR}"
  PYTHON_BIN="$(find_qt_python)" || exit 1
  exec "${PYTHON_BIN}" ecmc_sdo_askpass.py "${1:-SSH authentication}"
fi

if [ "${1:-}" = "--demo" ]; then
  cd "${SCRIPT_DIR}"
  PYTHON_BIN="$(find_qt_python)" || {
    print_qt_python_error
    exit 1
  }
  exec "${PYTHON_BIN}" ecmc_sdo_qt.py --demo
fi

HOST="${1:-${ETHERCAT_HOST:-}}"
MASTER_ID="${2:-${ETHERCAT_MASTER:-0}}"
SLAVE_ID="${3:-${ETHERCAT_SLAVE:-0}}"

cd "${SCRIPT_DIR}"

PYTHON_BIN="$(find_qt_python)" || {
  print_qt_python_error
  exit 1
}

exec "${PYTHON_BIN}" ecmc_sdo_qt.py "${HOST}" --master "${MASTER_ID}" --slave "${SLAVE_ID}"
