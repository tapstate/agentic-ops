#!/usr/bin/env python3
"""Codex Hook 与 AgenticOps 标准协议的薄转换器。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from adapters.runtime import decision_reason, evaluate_tool_call  # noqa: E402

ADAPTER_VERSION = 6


def deny(reason_code, reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "[agenticops:%s] %s" % (reason_code, reason),
        }
    }


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, TypeError):
        print(json.dumps(deny("invalid_hook_input", "Codex Hook 输入不是有效 JSON"), ensure_ascii=False))
        return 0
    try:
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
        tool_input = payload.get("tool_input") or payload.get("arguments") or {}
        if tool_name in ("exec_command", "shell", "shell_command"):
            tool_name = "Bash"
            tool_input = {"command": tool_input.get("command") or tool_input.get("cmd") or ""}
        decision = evaluate_tool_call(
            "codex", ADAPTER_VERSION, tool_name, tool_input, str(payload.get("cwd") or os.getcwd())
        )
        if decision is None or decision["decision"] == "allow":
            return 0
        reason = decision_reason(decision, tool_name, tool_input, codex_manual_unknown=True)
        if decision["decision"] == "ask":
            reason = "操作已暂停，Agent 必须立即向研发工程师展示本消息并停止依赖步骤。%s" % reason
        print(json.dumps(deny(decision["reason_code"], reason), ensure_ascii=False))
    except Exception:
        print(
            json.dumps(
                deny("adapter_failure", "AgenticOps Hook 执行异常，已拒绝本次操作；请检查本地 Adapter/Gate 配置。"),
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
