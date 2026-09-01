#!/usr/bin/env bash
# Runs the tests. They never reach the network: the upstream is faked in-process
# and one test points FX_UPSTREAM_BASE at a closed port on purpose.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || uv venv --seed .venv
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
fi

exec .venv/bin/python -m pytest -q "$@"
