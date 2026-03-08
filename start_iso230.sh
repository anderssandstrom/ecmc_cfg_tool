#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/qt_runtime.sh"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
AXIS_ID="${2-}"
TIMEOUT="${TIMEOUT:-2.0}"

cd "${SCRIPT_DIR}"

PYTHON_BIN="$(find_qt_python)" || {
  print_qt_python_error
  exit 1
}

exec "${PYTHON_BIN}" ecmc_iso230_qt.py \
  --prefix "${PREFIX}" \
  --axis-id "${AXIS_ID}" \
  --timeout "${TIMEOUT}"
