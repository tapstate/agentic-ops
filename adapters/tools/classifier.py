"""把 Agent 工具调用编排为标准操作和目标上下文。"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

from adapters.tools.shell_classifier import UNKNOWN, classify_bash_call

CONFIG = json.loads(
    (Path(__file__).resolve().parent / "mcp-operations.json").read_text(encoding="utf-8")
)
MCP_REPOSITORY_BOUND_OPERATIONS = {"create_pr", "update_pr", "fix_pr_comments", "git_push"}


def classify_tool_call(tool_name, tool_input):
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        operations, target = classify_bash_call(command)
        return operations, command, target
    if not tool_name.startswith("mcp__"):
        return [], tool_name, {}
    _, service_name, short_name = tool_name.split("__", 2)
    operation = CONFIG["mappings"].get(service_name.lower(), {}).get(short_name.lower())
    if operation is None:
        return [], tool_name, {}
    target = {"branch_relevant": False}
    if operation in CONFIG["repository_target_operations"]:
        target.update(github_target(tool_input))
    if operation in CONFIG.get("jira_target_operations", []):
        target.update(jira_target(tool_input))
    # 只允许明确的 Jira 编辑工具携带接管水印意图。创建任务和通用写入映射
    # 仍是 edit_jira_issue，但绝不能借用这条一次性许可。
    if operation == "edit_jira_issue" and short_name.lower() in {
            "edit_issue", "editjiraissue", "update_issue"}:
        target.update(jira_edit_target(tool_input))
    operations = [operation]
    if operation in MCP_REPOSITORY_BOUND_OPERATIONS and not target.get("repository"):
        operations.append(UNKNOWN)
    return operations, tool_name, target


def classify_bash(command):
    return classify_bash_call(command)[0]


def github_target(tool_input):
    owner = tool_input.get("owner")
    repo = tool_input.get("repo")
    repository = (
        "%s/%s" % (owner, repo) if owner and repo and "/" not in str(repo)
        else tool_input.get("repository") or (repo if repo and "/" in str(repo) else None)
    )
    branch = tool_input.get("branch") or tool_input.get("head") or tool_input.get("head_ref")
    branch = str(branch).split(":", 1)[1] if branch and ":" in str(branch) else branch
    return dict({"branch_relevant": bool(branch)},
                **({"repository": str(repository)} if repository else {}),
                **({"branch": str(branch)} if branch else {}))


def jira_target(tool_input):
    value = next(
        (tool_input.get(key) for key in
         ("issueKey", "issue_key", "issueIdOrKey", "issue_key_or_id", "key") if tool_input.get(key)), None
    )
    transition = next(
        (tool_input.get(key) for key in
         ("transitionId", "transition_id", "transition", "statusId", "status_id") if tool_input.get(key)), None
    )
    transition_id = transition.get("id") if isinstance(transition, dict) else transition
    return dict({"branch_relevant": False},
                **({"issue_key": str(value).upper()} if value else {}),
                **({"jira_transition_id": str(transition_id)} if transition_id else {}))


def jira_edit_target(tool_input):
    """只标准化一个 Jira 字符串字段的精确写入；其余编辑仍保持通用受控操作。"""
    issue_keys = [
        key for key in ("issueKey", "issue_key", "issueIdOrKey", "issue_key_or_id", "key")
        if tool_input.get(key)
    ]
    fields = tool_input.get("fields")
    if (set(tool_input) - {"issueKey", "issue_key", "issueIdOrKey", "issue_key_or_id", "key", "fields"} or
            len(issue_keys) != 1 or not isinstance(fields, dict) or len(fields) != 1):
        return {}
    field_id, value = next(iter(fields.items()))
    return ({
        "jira_watermark_field": field_id,
        "jira_watermark_digest": hashlib.sha256(json.dumps(
            {"field_id": field_id, "value": value}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest(),
    } if isinstance(field_id, str) and field_id.startswith("customfield_") and
    isinstance(value, str) and value else {})
