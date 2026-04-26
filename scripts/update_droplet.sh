#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="stock-research-assistant"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -d "${PROJECT_ROOT}/.git" ]]; then
  echo "Project root not found: ${PROJECT_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Virtualenv Python not found: ${VENV_PYTHON}" >&2
  echo "Create the virtual environment first." >&2
  exit 1
fi

cd "${PROJECT_ROOT}"

echo "Updating repository..."
git pull --ff-only

echo "Refreshing editable install..."
"${VENV_PYTHON}" -m pip install -e .

echo "Reloading service configuration..."
systemctl daemon-reload

echo "Restarting ${SERVICE_NAME}..."
systemctl restart "${SERVICE_NAME}"

echo
echo "Service status:"
systemctl --no-pager --full status "${SERVICE_NAME}"
