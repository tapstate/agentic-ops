#!/usr/bin/env bash
# 仅维护/测试子进程使用；不得 source 到业务 Agent 会话。
ao_python_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AGENTIC_OPS_TEST_PYTHON="$(python3 "$ao_python_root/bootstrap/python_runtime.py" test --root "$ao_python_root")" || return 1
AGENTIC_OPS_TEST_PYTHON="$(AGENTIC_OPS_PYTHON="$AGENTIC_OPS_TEST_PYTHON" "$AGENTIC_OPS_TEST_PYTHON" -I "$ao_python_root/bootstrap/python_runtime.py" prepare --root "$ao_python_root")" || return 1
export AGENTIC_OPS_TEST_PYTHON
AGENTIC_OPS_INTERNAL_TEST_PYTHON="${AGENTIC_OPS_INTERNAL_TEST_PYTHON:-$ao_python_root/.local/venv/internal/bin/python}"
"$AGENTIC_OPS_TEST_PYTHON" -I "$ao_python_root/bootstrap/python_runtime.py" select --root "$ao_python_root" >/dev/null || return 1
export AGENTIC_OPS_INTERNAL_TEST_PYTHON
# Shell 安装/资源测试及其 shebang 子进程也必须使用被记录的解释器。
export PATH="$(dirname "$AGENTIC_OPS_TEST_PYTHON"):$PATH"
unset ao_python_root
