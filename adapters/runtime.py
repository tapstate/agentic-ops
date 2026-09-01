"""Agent Adapter 共用的标准请求组装桥；不得承载策略和任务状态。"""
from __future__ import annotations

import re

from adapters.tools.classifier import classify_tool_call
from gate.runner import evaluate_request


SENSITIVE_COMMAND_VALUE = re.compile(
    r"(?i)(\b[A-Z_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|AUTHORIZATION|CREDENTIAL|PRIVATE[_-]?KEY|ACCESS[_-]?KEY)[A-Z_]*=)(?:bearer\s+)?([^\s;]+)"
)
SENSITIVE_COMMAND_OPTION = re.compile(
    r"(?i)((?:--?)(?:token|secret|password|passwd|api[-_]?key|authorization|credential|private[-_]?key|access[-_]?key|user|username|u|p)(?:=|\s+))([^\s;]+)"
)
SENSITIVE_COMPACT_OPTION = re.compile(r"(?i)(\s-(?:u|p))([^\s;]+)")
AUTHORIZATION_HEADER = re.compile(r"(?i)(authorization:\s*(?:bearer\s+)?)([^\s'\";]+)")


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


def _command_preview(command):
    preview = "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character for character in str(command)
    )
    preview = " ".join(preview.split()) or "（空命令）"
    preview = SENSITIVE_COMMAND_VALUE.sub(r"\1<已隐藏>", preview)
    preview = SENSITIVE_COMMAND_OPTION.sub(r"\1<已隐藏>", preview)
    preview = SENSITIVE_COMPACT_OPTION.sub(r"\1<已隐藏>", preview)
    preview = AUTHORIZATION_HEADER.sub(r"\1<已隐藏>", preview)
    return preview if len(preview) <= 600 else preview[:600] + " …（已截断）"


def _tool_summary(tool_name, tool_input):
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        return "触发工具：Bash\n命令摘要（凭据片段已隐藏）：%s" % _command_preview(tool_input.get("command", ""))
    if tool_name.startswith("mcp__"):
        target = next(
            (
                "%s=%s" % (key, tool_input[key])
                for key in ("issueKey", "issue_key", "repository", "repo", "branch")
                if tool_input.get(key)
            ),
            "未提供可展示的目标参数",
        )
        return "触发工具：%s\n目标参数：%s" % (_command_preview(tool_name), _command_preview(target))
    return "触发工具：%s" % (tool_name or "（未提供工具名）")


def decision_reason(decision, tool_name=None, tool_input=None, codex_manual_unknown=False):
    parts = ["门禁原因：%s" % decision["reason"], "判定：%s" % decision["reason_code"]]
    if tool_name is not None:
        parts.append(_tool_summary(tool_name, tool_input or {}))
    required_action = decision.get("required_action")
    if codex_manual_unknown and decision.get("reason_code") == "unknown_external_write":
        if tool_name.startswith("mcp__"):
            required_action = (
                "请研发工程师在已登录的对应 MCP 服务中，按上述工具和目标参数完成该写入；"
                "回读外部结果后回复“继续”。Agent 不得改写参数或换工具重试；Adapter 更新后可明确原样重放一次，再次拒绝则停止。"
            )
        else:
            required_action = (
                "请研发工程师在自己的终端核对并执行上述命令；回读结果后回复“继续”。"
                "Agent 不得拆分、改写或换工具重试。Tool Adapter 更新后，研发工程师可明确要求原样重放一次；再次拒绝则停止。"
            )
    if required_action:
        parts.append("下一步：%s" % required_action)
    warnings = decision.get("warnings") or []
    if warnings:
        parts.extend(warnings)
    return "\n".join(parts)
