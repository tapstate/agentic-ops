#!/usr/bin/env python3
"""Codex Hook 与 AgenticOps 标准协议的薄转换器。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from adapters.runtime import decision_reason, evaluate_tool_call  # noqa: E402

ADAPTER_VERSION = 1


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, TypeError):
        print(json.dumps({"decision": "deny", "reason": "Codex Hook 输入不是有效 JSON"}, ensure_ascii=False))
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
        print(json.dumps({"decision": "allow", "passthrough": True}))
        return 0

    native_decision = "deny" if decision["decision"] == "ask" else decision["decision"]
    reason = decision_reason(decision)
    if decision["decision"] == "ask":
        reason += "（当前 Codex Adapter 为二态模式：请签发授权或由人工执行。）"
    print(
        json.dumps(
            {
                "decision": native_decision,
                "original_decision": decision["decision"],
                "operation": decision["operation"],
                "reason": reason,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
