#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/qt_runtime.sh"

PREFIX="${1:-${PREFIX:-IOC:ECMC}}"
AXIS_ID="${2-}"
YAML_FILE="${3:-${YAML_FILE:-${SCRIPT_DIR}/axis_template.yaml}}"
TIMEOUT="${TIMEOUT:-2.0}"

cd "${SCRIPT_DIR}"

PYTHON_BIN="$(find_qt_python)" || {
  print_qt_python_error
  exit 1
}

exec "${PYTHON_BIN}" ecmc_axis_cfg.py \
  --catalog "${SCRIPT_DIR}/ecmc_commands.json" \
  --yaml "${YAML_FILE}" \
  --prefix "${PREFIX}" \
  --axis-id "${AXIS_ID}" \
  --timeout "${TIMEOUT}"
