#!/usr/bin/env python3
"""Claude PreToolUse 与 AgenticOps 标准协议的薄转换器。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from adapters.runtime import decision_reason, evaluate_tool_call  # noqa: E402

ADAPTER_VERSION = 1


def deny(reason):
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "[agenticops:adapter] %s" % reason,
        }
    }


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, TypeError):
        print(json.dumps(deny("Claude Hook 输入不是有效 JSON"), ensure_ascii=False))
        return 0

    decision = evaluate_tool_call(
        "claude",
        ADAPTER_VERSION,
        str(payload.get("tool_name", "")),
        payload.get("tool_input", {}) or {},
        str(payload.get("cwd") or os.getcwd()),
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
                    % (decision["operation"], decision_reason(decision)),
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
