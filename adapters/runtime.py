"""Agent Adapter 共用的标准请求组装桥；不得承载策略和任务状态。"""
from __future__ import annotations

from adapters.tools.classifier import classify_tool_call
from gate.runner import evaluate_request


def evaluate_tool_call(agent, adapter_version, tool_name, tool_input, cwd):
    operations, note, target = classify_tool_call(tool_name, tool_input or {})
    if not operations:
        return None
    request = {
        "protocol_version": 1,
        "event": "before_operation",
        "source": {
            "agent": agent,
            "adapter": "%s-hook" % agent,
            "adapter_version": adapter_version,
            "tool_kind": "mcp" if tool_name.startswith("mcp__") else "shell",
            "tool_name": tool_name,
        },
        "cwd": cwd,
        "operations": operations,
        "target": target,
        "note": note,
    }
    return evaluate_request(request)


def decision_reason(decision):
    warnings = decision.get("warnings") or []
    if warnings:
        return "%s；%s" % (decision["reason"], "；".join(warnings))
    return decision["reason"]
