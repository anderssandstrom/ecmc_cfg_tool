#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/qt_runtime.sh"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
AXIS_ID="${2:-${AXIS_ID:-1}}"
TIMEOUT="${TIMEOUT:-2.0}"
POLL_MS="${POLL_MS:-250}"
HISTORY_LIMIT="${HISTORY_LIMIT:-200}"

cd "${SCRIPT_DIR}"

PYTHON_BIN="$(find_qt_python)" || {
  print_qt_python_error
  exit 1
}

exec "${PYTHON_BIN}" ecmc_rtlog_qt.py \
  --prefix "${PREFIX}" \
  --axis-id "${AXIS_ID}" \
  --timeout "${TIMEOUT}" \
  --poll-ms "${POLL_MS}" \
  --history-limit "${HISTORY_LIMIT}"
