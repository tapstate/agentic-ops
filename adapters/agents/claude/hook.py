#!/usr/bin/env python3
"""Claude PreToolUse 与 AgenticOps 标准协议的薄转换器。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from adapters.runtime import decision_reason, evaluate_tool_call  # noqa: E402

ADAPTER_VERSION = 5


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
        print(json.dumps(deny("invalid_hook_input", "Claude Hook 输入不是有效 JSON"), ensure_ascii=False))
        return 0
    try:
        tool_name = str(payload.get("tool_name", ""))
        tool_input = payload.get("tool_input", {}) or {}
        decision = evaluate_tool_call(
            "claude", ADAPTER_VERSION, tool_name, tool_input, str(payload.get("cwd") or os.getcwd())
        )
        if decision is None:
            print(json.dumps({}))
            return 0
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": decision["decision"],
                        "permissionDecisionReason": "[agenticops:%s] %s"
                        % (
                            decision["reason_code"],
                            decision_reason(decision, tool_name, tool_input),
                        ),
                    }
                },
                ensure_ascii=False,
            )
        )
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
