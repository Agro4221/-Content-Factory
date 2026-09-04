#!/usr/bin/env bash
# Idempotent dependency setup for the MaryJane Parser Cloud Agent environment.
# Creates a local .venv next to the app (matching deps.py behaviour) and installs
# runtime + dev dependencies. Safe to run repeatedly.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# The default image ships Python 3.12 but not the stdlib venv/ensurepip module.
if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
  echo "--> Installing python3-venv system package"
  sudo apt-get update -y
  sudo apt-get install -y python3-venv
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "--> Creating .venv"
  python3 -m venv .venv
fi

echo "--> Upgrading pip"
.venv/bin/python -m pip install --upgrade pip

echo "--> Installing dependencies"
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

echo "--> Done. Run the app with: .venv/bin/python main.py"
