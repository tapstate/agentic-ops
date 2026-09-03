#!/usr/bin/env bash
set -euo pipefail

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)"
product_python="${AGENTIC_OPS_TEST_PYTHON:-$(command -v python3)}"
internal_python="${AGENTIC_OPS_INTERNAL_TEST_PYTHON:-$repo_root/.local/venv/internal/bin/python}"

test -x "$product_python" || { printf 'AgenticOps：缺少 Python 3.9+\n' >&2; exit 1; }
if [ ! -x "$internal_python" ]; then internal_python="$product_python"; fi
"$internal_python" -c 'import yaml' >/dev/null 2>&1 || {
  printf 'AgenticOps：内部测试依赖尚未准备，请执行 agenticops setup\n' >&2
  exit 1
}

PYTHONDONTWRITEBYTECODE=1 "$product_python" "$repo_root/tests/test_gate.py"
PYTHONDONTWRITEBYTECODE=1 "$product_python" "$repo_root/tests/test_contracts.py"
PYTHONDONTWRITEBYTECODE=1 "$product_python" "$repo_root/tests/test_adapter_boundary.py"
PYTHONDONTWRITEBYTECODE=1 "$product_python" "$repo_root/tests/test_workflow.py"
PYTHONDONTWRITEBYTECODE=1 "$product_python" "$repo_root/tests/test_quality.py"
PYTHONDONTWRITEBYTECODE=1 "$product_python" "$repo_root/tests/test_issue_versions.py"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root" \
  "$internal_python" -m unittest discover \
    -s "$repo_root/internal/tests" -p 'test_story_gate.py' -v
