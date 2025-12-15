#!/usr/bin/env bash
# Run PiCastSI4713 with the web UI/API enabled. Adapter/web defaults come from cfg/config.yaml.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
: "${ADAPTER_CFG:=${SCRIPT_DIR}/cfg/config.yaml}"
# Allow overriding the station config via CFG=/path/to/config.json
: "${CFG:=}"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Virtualenv not found at $VENV_DIR. Create it with: python3 -m venv .venv" >&2
  exit 1
fi

source "${VENV_DIR}/bin/activate"

exec python3 "${SCRIPT_DIR}/picast4713.py" \
  --adapter-config "$ADAPTER_CFG" \
  ${CFG:+--cfg "$CFG"} \
  "$@"
