#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="/opt/X_Download"
PYTHON_BIN="${BASE_DIR}/.venv/bin/python"
if [ ! -x "${PYTHON_BIN}" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

export XDL_HOME="${XDL_HOME:-${BASE_DIR}/data}"

exec "${PYTHON_BIN}" "${BASE_DIR}/xdl_gui.py" --set-auth "$@"
