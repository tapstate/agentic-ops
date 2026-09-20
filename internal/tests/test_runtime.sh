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

run_product() {
  PYTHONDONTWRITEBYTECODE=1 "$product_python" "$repo_root/$1"
}

run_internal() {
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$repo_root" "$internal_python" "$@"
}

run_general_suite() {
  run_product tests/test_gate.py
  run_product tests/test_contracts.py
  run_product tests/test_adapter_boundary.py
  run_product tests/test_workflow.py
  run_product tests/test_repair_strategy.py
  run_product tests/test_failures.py
  run_product tests/test_git_refs.py
  run_product tests/test_engineering_baseline.py
  run_product tests/test_station_state.py
  run_product tests/test_station_source.py
  run_product tests/test_shared_repositories.py
  run_product tests/test_repository_recovery.py
  run_product tests/test_task_identity.py
  run_product tests/test_station_compatibility.py
  run_product tests/test_checkpoints.py
  run_product tests/test_quality.py
  run_product tests/test_issue_versions.py
  run_product tests/test_jira_status.py
  run_product tests/test_jira_watermark.py
  PYTHONDONTWRITEBYTECODE=1 "$product_python" -m unittest discover \
    -s "$repo_root/projects/tapdata/tests" -p 'test_maven*.py'
  run_internal -m unittest discover -s "$repo_root/internal/tests" -p 'test_story_gate.py' -v
  run_internal -m unittest internal.tests.test_test_selection -v
  run_internal -m unittest internal.tests.test_review_context -v
  run_internal -m unittest internal.tests.test_verification -v
}

run_general_suite
run_product tests/test_station_resources.py
run_product tests/test_station_clean.py
run_product tests/test_station_lifecycle.py
