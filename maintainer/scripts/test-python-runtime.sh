#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)"
MAINTAINER_PYTHON="${AGENTIC_OPS_MAINTAINER_TEST_PYTHON:-${AGENTIC_OPS_TEST_PYTHON:-$REPO_ROOT/maintainer/.venv/bin/python}}"
DEVELOPER_PYTHON="${AGENTIC_OPS_DEVELOPER_TEST_PYTHON:-${AGENTIC_OPS_TEST_PYTHON:-$REPO_ROOT/developer/.venv/bin/python}}"

if [ ! -x "$MAINTAINER_PYTHON" ]; then
  printf 'AgenticOps：维护测试环境尚未准备，请先执行 uv sync --locked --project maintainer\n' >&2
  exit 1
fi
if [ ! -x "$DEVELOPER_PYTHON" ]; then
  printf 'AgenticOps：研发测试环境尚未准备，请先执行 uv sync --locked --project developer\n' >&2
  exit 1
fi

PYTHONPYCACHEPREFIX="$REPO_ROOT/.local/pycache" \
PYTHONPATH="$REPO_ROOT/developer/runtime/src" \
  "$DEVELOPER_PYTHON" -m unittest discover -s "$REPO_ROOT/developer/tests/runtime" -p 'test_*.py' -v

PYTHONPYCACHEPREFIX="$REPO_ROOT/.local/pycache" \
PYTHONPATH="$REPO_ROOT/maintainer/runtime/src" \
  "$MAINTAINER_PYTHON" -m unittest discover -s "$REPO_ROOT/maintainer/tests/runtime" -p 'test_*.py' -v
