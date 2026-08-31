#!/usr/bin/env python3
"""Codex Hook 与 AgenticOps 标准协议的薄转换器。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from adapters.runtime import decision_reason, evaluate_tool_call  # noqa: E402

ADAPTER_VERSION = 3


def deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, TypeError):
        print(json.dumps(deny("Codex Hook 输入不是有效 JSON"), ensure_ascii=False))
        return 0

    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    tool_input = payload.get("tool_input") or payload.get("arguments") or {}
    if tool_name in ("exec_command", "shell", "shell_command"):
        tool_name = "Bash"
        tool_input = {"command": tool_input.get("command") or tool_input.get("cmd") or ""}

    decision = evaluate_tool_call(
        "codex",
        ADAPTER_VERSION,
        tool_name,
        tool_input,
        str(payload.get("cwd") or os.getcwd()),
    )
    if decision is None:
        return 0

    if decision["decision"] == "allow":
        return 0

    reason = "[agenticops:%s] %s" % (
        decision.get("reason_code") or decision["operation"],
        decision_reason(decision),
    )
    if decision["decision"] == "ask":
        reason = "操作已暂停，Agent 必须立即向研发工程师展示本消息并停止依赖步骤。%s" % reason
    print(json.dumps(deny(reason), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
