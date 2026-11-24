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
while (( $# )); do
  case "$1" in
    --motion-trigger)
      if (( $# < 2 )); then
        echo "Missing motion trigger PV" >&2
        exit 2
      fi
      ARGS+=("--motion-trigger=$2")
      shift 2
      ;;
    *)
      ARGS+=("--pv=$1")
      shift
      ;;
  esac
done

exec "${PYTHON_BIN}" ecmc_daq_qt.py \
  --prefix "${PREFIX}" \
  --timeout "${TIMEOUT}" \
  "${ARGS[@]+"${ARGS[@]}"}"
