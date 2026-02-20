#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
AXIS_ID="${2:-${AXIS_ID:-1}}"
TIMEOUT="${TIMEOUT:-2.0}"

BASE_CATALOG="${BASE_CATALOG:-${SCRIPT_DIR}/ecmc_commands.json}"
CNTRL_CATALOG="${CNTRL_CATALOG:-${SCRIPT_DIR}/ecmc_commands_cntrl.json}"

cd "${SCRIPT_DIR}"

python3 "${SCRIPT_DIR}/build_cntrl_command_catalog.py" \
  --in "${BASE_CATALOG}" \
  --out "${CNTRL_CATALOG}"

exec python3 ecmc_cntrl_qt.py \
  --catalog "${CNTRL_CATALOG}" \
  --prefix "${PREFIX}" \
  --axis-id "${AXIS_ID}" \
  --timeout "${TIMEOUT}"
