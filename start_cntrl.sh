#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
AXIS_ID="${2:-${AXIS_ID:-1}}"
SKETCH_IMAGE_ARG="${3:-}"
TIMEOUT="${TIMEOUT:-2.0}"
SKETCH_IMAGE="${SKETCH_IMAGE_ARG:-${SKETCH_IMAGE:-}}"

BASE_CATALOG="${BASE_CATALOG:-${SCRIPT_DIR}/ecmc_commands.json}"
CNTRL_CATALOG="${CNTRL_CATALOG:-${SCRIPT_DIR}/ecmc_commands_cntrl.json}"
TMP_CNTRL_CATALOG_DEFAULT="/tmp/ecmc_commands_cntrl_${USER:-user}.json"

cd "${SCRIPT_DIR}"

CNTRL_CATALOG_OUT="${CNTRL_CATALOG}"
if [ ! -w "$(dirname "${CNTRL_CATALOG_OUT}")" ]; then
  CNTRL_CATALOG_OUT="${TMP_CNTRL_CATALOG_DEFAULT}"
fi

NEED_REBUILD=0
if [ ! -f "${CNTRL_CATALOG_OUT}" ]; then
  NEED_REBUILD=1
elif [ "${BASE_CATALOG}" -nt "${CNTRL_CATALOG_OUT}" ]; then
  NEED_REBUILD=1
elif [ "${SCRIPT_DIR}/build_cntrl_command_catalog.py" -nt "${CNTRL_CATALOG_OUT}" ]; then
  NEED_REBUILD=1
fi

if [ "${NEED_REBUILD}" -eq 1 ]; then
  python3 "${SCRIPT_DIR}/build_cntrl_command_catalog.py" \
    --in "${BASE_CATALOG}" \
    --out "${CNTRL_CATALOG_OUT}"
fi

exec python3 ecmc_cntrl_qt.py \
  --catalog "${CNTRL_CATALOG_OUT}" \
  --prefix "${PREFIX}" \
  --axis-id "${AXIS_ID}" \
  --sketch-image "${SKETCH_IMAGE}" \
  --timeout "${TIMEOUT}"
