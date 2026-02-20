#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
TIMEOUT="${TIMEOUT:-2.0}"

cd "${SCRIPT_DIR}"

exec python3 ecmc_stream_qt.py \
  --catalog "${SCRIPT_DIR}/ecmc_commands.json" \
  --favorites "${SCRIPT_DIR}/ecmc_favorites.json" \
  --prefix "${PREFIX}" \
  --timeout "${TIMEOUT}"
