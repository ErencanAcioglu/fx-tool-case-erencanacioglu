#!/usr/bin/env bash
# Starts the service. It listens on $PORT (default 8080) and reads the upstream
# base URL from $FX_UPSTREAM_BASE, so the real host is never hardcoded here.
set -euo pipefail
cd "$(dirname "$0")"

# First run installs into .venv. `python3 -m venv` is the portable path; `uv` is
# a fallback for machines whose bundled ensurepip is broken.
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || uv venv --seed .venv
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

exec .venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port "${PORT:-8080}"
