"""Tool Adapter：把 Shell、gh 和 MCP 调用转换成标准操作。"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

MAPPING_PATH = Path(__file__).resolve().parent / "mcp-operations.json"


def load_mappings(path=None):
    with open(Path(path) if path else MAPPING_PATH, "r", encoding="utf-8") as stream:
        return json.load(stream)


def classify_tool_call(tool_name, tool_input):
    """返回标准 operations、审计 note 和目标上下文。"""
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        target = {"branch_relevant": True}
        push_target = extract_push_target(command)
        if push_target:
            target["push_target_branch"] = push_target
        return classify_bash(command), command, target

    if tool_name.startswith("mcp__"):
        configuration = load_mappings()
        short_name = tool_name.split("__")[-1].lower()
        operation = configuration["mappings"].get(short_name)
        if operation is None and any(
            short_name.startswith(prefix)
            for prefix in configuration["readonly_prefixes"]
        ):
            return [], tool_name, {}
        operation = operation or "unknown_external_write"
        target = {"branch_relevant": False}
        if operation in configuration["repository_target_operations"]:
            target.update(github_target(tool_input))
        if operation in configuration.get("jira_target_operations", []):
            target.update(jira_target(tool_input))
        return [operation], tool_name, target

    return [], tool_name, {}


def classify_bash(command):
    operations = []
    for segment in re.split(r"(?<!\|)\|(?!\|)|&&|\|\||;|\n", command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        if tokens:
            operations.extend(_classify_tokens(tokens))
    return operations


def _classify_tokens(tokens):
    executable = os.path.basename(tokens[0])
    if executable == "git":
        return _classify_git(tokens[1:])
    if executable == "gh":
        return _classify_gh(tokens[1:])
    return []


def _git_subcommand(arguments):
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return argument, arguments[index + 1 :]
    return "", []


def _classify_git(arguments):
    subcommand, rest = _git_subcommand(arguments)
    if subcommand == "push":
        if any(
            argument in ("--force", "-f", "--force-with-lease")
            or argument.startswith("+")
            for argument in rest
        ):
            return ["force_push"]
        return ["git_push"]
    if subcommand == "commit":
        return ["history_rewrite"] if "--amend" in rest else ["git_commit"]
    if subcommand == "merge":
        return ["git_merge"]
    if subcommand == "rebase":
        return ["git_rebase"]
    if subcommand == "clean":
        return ["git_clean"]
    if subcommand == "tag":
        return [] if not rest or all(item.startswith("-l") or item == "--list" for item in rest) else ["git_tag"]
    if subcommand in ("filter-branch", "filter-repo", "replace"):
        return ["history_rewrite"]
    if subcommand == "reset" and "--hard" in rest:
        return ["history_rewrite"]
    return []


def _classify_gh(arguments):
    if len(arguments) < 2:
        return []
    group, action = arguments[0], arguments[1]
    if group == "pr":
        if action == "create":
            return ["create_pr"]
        if action == "merge":
            return ["pr_merge"]
        if action in ("edit", "ready", "reopen", "update-branch", "close"):
            return ["update_pr"]
        if action in ("comment", "review"):
            return ["fix_pr_comments"]
    if group == "release" and action in ("create", "edit", "delete", "upload"):
        return ["release"]
    if group == "repo" and action in ("create", "fork", "delete"):
        return ["create_repository"]
    if group == "api":
        joined = " ".join(arguments)
        if re.search(r"(-X|--method)\s+(POST|PUT|PATCH|DELETE)", joined, re.I) or "-f " in joined or "--field" in joined:
            return ["unknown_external_write"]
    return []


def extract_push_target(command):
    match = re.search(r"\bgit\b[^|;&]*\bpush\b([^|;&]*)", command)
    if not match:
        return None
    try:
        tokens = shlex.split(match.group(1))
    except ValueError:
        tokens = match.group(1).split()
    positional = [token for token in tokens if not token.startswith("-")]
    if len(positional) < 2:
        return None
    return positional[1].split(":")[-1].lstrip("+")


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
    for key in ("issueKey", "issue_key", "issueIdOrKey", "issue_key_or_id", "key"):
        value = tool_input.get(key)
        if value:
            return {"issue_key": str(value).upper(), "branch_relevant": False}
    return {"branch_relevant": False}
