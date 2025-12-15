#!/usr/bin/env bash
# Start PiCastSI4713 using the Raspberry Pi GPIO/I2C backend.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
CFG_PATH="${SCRIPT_DIR}/cfg/picastsi4713.yml"

# Configurable defaults
: "${RESET_PIN:=5}"
: "${I2C_BUS:=1}"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "Virtualenv not found at $VENV_DIR. Create it with: python3 -m venv .venv" >&2
  exit 1
fi

source "${VENV_DIR}/bin/activate"

exec python3 "${SCRIPT_DIR}/picast4713.py" \
  --cfg "$CFG_PATH" \
  --backend rpi \
  --i2c-bus "$I2C_BUS" \
  --ftdi-reset-pin "$RESET_PIN"
