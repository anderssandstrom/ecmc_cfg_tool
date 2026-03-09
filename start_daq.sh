#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/qt_runtime.sh"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
TIMEOUT="${TIMEOUT:-2.0}"

cd "${SCRIPT_DIR}"

PYTHON_BIN="$(find_qt_python)" || {
  print_qt_python_error
  exit 1
}

shift || true

ARGS=()
for pv in "$@"; do
  ARGS+=(--pv "$pv")
done

exec "${PYTHON_BIN}" ecmc_daq_qt.py \
  --prefix "${PREFIX}" \
  --timeout "${TIMEOUT}" \
  "${ARGS[@]+"${ARGS[@]}"}"
