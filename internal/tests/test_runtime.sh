#!/usr/bin/env bash
set -euo pipefail

# 独立测试进程共享一个明确的测试会话；并发用例自行更换或清除。
export AGENTICOPS_EXECUTION_ID=0123456789abcdef0123456789abcdef

repo_root="$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)"
. "$repo_root/internal/python-environment.sh"
product_python="${AGENTIC_OPS_TEST_PYTHON:-$(command -v python3)}"
internal_python="${AGENTIC_OPS_INTERNAL_TEST_PYTHON:-$repo_root/.local/venv/internal/bin/python}"

test -x "$product_python" || { printf 'AgenticOps：缺少 Python 3.12.x\n' >&2; exit 1; }
test -x "$internal_python" || { printf 'AgenticOps：内部 Python 3.12 环境缺失\n' >&2; exit 1; }
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
  run_product tests/test_python_runtime.py
  run_product tests/test_gate.py
  run_product tests/test_contracts.py
  run_product tests/test_adapter_boundary.py
  run_product tests/test_workflow.py
  run_product tests/test_repair_strategy.py
  run_product tests/test_failures.py
  run_product tests/test_git_refs.py
  run_product tests/test_engineering_baseline.py
  run_product tests/test_station_context.py
  run_product tests/test_station_state.py
  run_product tests/test_station_source.py
  run_product tests/test_station_source_update.py
  run_product tests/test_shared_skills.py
  run_product tests/test_shared_repositories.py
  run_product tests/test_repository_recovery.py
  run_product tests/test_task_identity.py
  run_product tests/test_station_compatibility.py
  run_product tests/test_installation_update.py
  run_product tests/test_product_access.py
  run_product tests/test_task_update.py
  run_product tests/test_checkpoints.py
  run_product tests/test_quality.py
  run_product tests/test_it_manual.py
  run_product tests/test_issue_versions.py
  run_product tests/test_jira_status.py
  run_product tests/test_issue_routing.py
  run_product tests/test_jira_watermark.py
  PYTHONDONTWRITEBYTECODE=1 "$product_python" -m unittest discover \
    -s "$repo_root/projects/tapdata/tests" -p 'test_*.py'
  run_internal -m unittest discover -s "$repo_root/internal/tests" -p 'test_story_gate.py' -v
  run_internal -m unittest internal.tests.test_test_selection -v
  run_internal -m unittest internal.tests.test_review_context -v
  run_internal -m unittest internal.tests.test_verification -v
}

run_general_suite
run_product tests/test_station_resources.py
run_product tests/test_station_archive_selection.py
run_product tests/test_station_records.py
run_product tests/test_task_handoff.py
run_product tests/test_execution.py
run_product tests/test_jira_coordination.py
run_product tests/test_station_clean.py
run_product tests/test_cleanup_tree.py
run_product tests/test_source_preservation.py
run_product tests/test_station_development_reset.py
run_product tests/test_station_terminal_redaction.py
run_product tests/test_station_lifecycle.py
