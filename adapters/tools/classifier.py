"""把 Agent 工具调用编排为标准操作和目标上下文。"""
from __future__ import annotations

import json
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
    operations = [operation]
    if operation in MCP_REPOSITORY_BOUND_OPERATIONS and not target.get("repository"):
        operations.append(UNKNOWN)
    return operations, tool_name, target


def classify_bash(command):
    return classify_bash_call(command)[0]


def github_target(tool_input):
    owner = tool_input.get("owner")
    repository = tool_input.get("repository")
    repo = tool_input.get("repo")
    if owner and repo and "/" not in str(repo):
        repository = "%s/%s" % (owner, repo)
    elif not repository and repo and "/" in str(repo):
        repository = repo
    branch = tool_input.get("branch") or tool_input.get("head") or tool_input.get("head_ref")
    if branch and ":" in str(branch):
        branch = str(branch).split(":", 1)[1]
    target = {"branch_relevant": bool(branch)}
    if repository:
        target["repository"] = str(repository)
    if branch:
        target["branch"] = str(branch)
    return target


def jira_target(tool_input):
    keys = ("issueKey", "issue_key", "issueIdOrKey", "issue_key_or_id", "key")
    value = next((tool_input.get(key) for key in keys if tool_input.get(key)), None)
    if value:
        return {"issue_key": str(value).upper(), "branch_relevant": False}
    return {"branch_relevant": False}
