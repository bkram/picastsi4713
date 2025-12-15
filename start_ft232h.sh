#!/usr/bin/env bash
# Start PiCastSI4713 using an FT232H via the Blinka backend.
# Adjust RESET_PIN or CFG path as needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
CFG_PATH="${SCRIPT_DIR}/cfg/picastsi4713.yml"

# Configurable defaults
: "${RESET_PIN:=5}"
: "${BACKEND:=ft232h_blinka}"
: "${FTDI_URL:=ftdi://ftdi:232h/1}"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Virtualenv not found at $VENV_DIR. Create it with: python3 -m venv .venv" >&2
  exit 1
fi

source "${VENV_DIR}/bin/activate"

export BLINKA_FT232H=1

exec python3 "${SCRIPT_DIR}/picast4713.py" \
  --cfg "$CFG_PATH" \
  --backend "$BACKEND" \
  --ftdi-url "$FTDI_URL" \
  --ftdi-reset-pin "$RESET_PIN"
