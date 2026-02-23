#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
AXIS_ID="${2:-${AXIS_ID:-1}}"
TIMEOUT="${TIMEOUT:-2.0}"

cd "${SCRIPT_DIR}"

exec python3 ecmc_mtn_qt.py \
  --prefix "${PREFIX}" \
  --axis-id "${AXIS_ID}" \
  --timeout "${TIMEOUT}"
