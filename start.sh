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

exec "${PYTHON_BIN}" ecmc_stream_qt.py \
  --catalog "${SCRIPT_DIR}/ecmc_commands.json" \
  --blocklist "${SCRIPT_DIR}/ecmc_commands_blocklist_all.json" \
  --prefix "${PREFIX}" \
  --timeout "${TIMEOUT}"
