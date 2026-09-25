#!/usr/bin/env bash
# Plumbline on your own computer (macOS / Linux): ./start.sh
# First run: creates a Python environment, installs the app and asks two setup questions. Then opens the dashboard.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Plumbline needs Python 3.11 or newer (python.org/downloads)."; exit 1
fi
if [ ! -d .venv ]; then
  echo "First run: setting up (a minute or two)..."
  "$PY" -m venv .venv
  .venv/bin/pip install -q --upgrade pip
fi
git pull --ff-only -q 2>/dev/null || true      # every start picks up the latest version
.venv/bin/pip install -q -e .
.venv/bin/mt setup --if-needed                   # first run: asks for your email and about phone alerts
exec .venv/bin/mt serve --open "$@"
