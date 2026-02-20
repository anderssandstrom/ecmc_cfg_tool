#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CMD_PV="${CMD_PV:-IOC:ECMC:CMD}"
QRY_PV="${QRY_PV:-IOC:ECMC:QRY}"
TIMEOUT="${TIMEOUT:-2.0}"

cd "${SCRIPT_DIR}"

exec python3 ecmc_stream_qt.py \
  --catalog "${SCRIPT_DIR}/ecmc_commands.json" \
  --favorites "${SCRIPT_DIR}/ecmc_favorites.json" \
  --cmd-pv "${CMD_PV}" \
  --qry-pv "${QRY_PV}" \
  --timeout "${TIMEOUT}"
