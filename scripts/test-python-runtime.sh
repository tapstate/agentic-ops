#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
PYTHON_BIN="${AGENTIC_OPS_TEST_PYTHON:-$REPO_ROOT/.venv/bin/python}"

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$(command -v python3)"
fi

PYTHONPATH="$REPO_ROOT/runtime/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$PYTHON_BIN" -m unittest discover -s "$REPO_ROOT/runtime/tests" -p 'test_*.py' -v
