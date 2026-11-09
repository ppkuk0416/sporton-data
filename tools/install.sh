#!/usr/bin/env bash
set -euo pipefail
cd /workspace/sporton-data
python3 -m venv /workspace/.venvs/sporton
/workspace/.venvs/sporton/bin/python -m pip install -r requirements.lock
/workspace/.venvs/sporton/bin/python -m pip install -r requirements-deep-learning.lock
/workspace/.venvs/sporton/bin/python -m pip install --no-deps --no-build-isolation -e .
/workspace/.venvs/sporton/bin/python -m pip check
/workspace/.venvs/sporton/bin/sporton init
