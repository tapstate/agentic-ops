"""AgenticOps 平台无关门禁判定内核。

输入必须是 AgenticOps 标准操作和可信执行上下文。本模块不理解任何平台事件、
原生工具名或 Hook 协议。零第三方依赖，兼容 Python 3.9+。
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import time
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "operations.json"

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


def load_policy(path=None):
    policy_path = Path(path) if path else POLICY_PATH
    with open(policy_path, "r", encoding="utf-8") as stream:
        return json.load(stream)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except (json.JSONDecodeError, OSError):
        return None


def find_gate_root(cwd):
    """返回最近的项目工作空间根。"""
    current = Path(cwd).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / ".agenticops" / "workspace.json").is_file() or (
            candidate / ".agenticops" / "tasks" / "index.json"
        ).is_file():
            return candidate
    return current


def _active_task_directories(root):
    state = Path(root) / ".agenticops"
    registry = _read_json(state / "tasks" / "index.json")
    if isinstance(registry, dict) and isinstance(registry.get("tasks"), dict):
        return [
            (issue, state / "tasks" / issue)
            for issue, entry in sorted(registry["tasks"].items())
            if isinstance(entry, dict) and entry.get("status") == "active"
        ]
    return []


def _repositories_match(document, context):
    repository = context.get("origin", "")
    branch = context.get("branch", "")
    if not repository:
        return False
    for item in document.get("repositories", []):
        if not isinstance(item, dict) or item.get("repository") != repository:
            continue
        if not context.get("branch_relevant", True):
            return True
        if branch and item.get("work_branch") == branch:
            return True
    return False


def find_task_directory(cwd, context=None, issue_key=None):
    """按 Jira 任务号或仓库+分支唯一解析 active 任务目录。"""
    root = find_gate_root(cwd)
    candidates = _active_task_directories(root)
    if issue_key:
        matches = [path for issue, path in candidates if issue == issue_key]
    elif context and context.get("origin"):
        matches = []
        for _, path in candidates:
            task = _read_json(path / "state.json")
            if task and _repositories_match(task, context):
                matches.append(path)
    else:
        matches = [path for _, path in candidates] if len(candidates) == 1 else []
    return matches[0] if len(matches) == 1 else None


def load_authorization_for_issue(cwd, issue_key):
    directory = find_task_directory(cwd, issue_key=issue_key)
    if directory is None:
        return None, None
    path = directory / "authorization.json"
    return (_read_json(path), str(path)) if path.is_file() else (None, str(path))


def find_authorization(cwd, context=None, issue_key=None):
    """从项目工作空间的 active 任务中唯一解析当前操作授权。"""
    directory = find_task_directory(cwd, context=context, issue_key=issue_key)
    if directory is None:
        return None, None
    path = directory / "authorization.json"
    return (_read_json(path), str(path)) if path.is_file() else (None, str(path))


def _git(cwd, *args):
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def normalize_repo(url):
    """把 Git URL 归一为 owner/repository。"""
    if not url:
        return ""
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else url.strip()


def git_context(cwd):
    return {
        "branch": _git(cwd, "rev-parse", "--abbrev-ref", "HEAD"),
        "origin": normalize_repo(_git(cwd, "remote", "get-url", "origin")),
    }


def _branch_protected(branch, patterns):
    return any(fnmatch.fnmatch(branch, pattern) for pattern in patterns)


def check_authorization(auth, context, policy, now=None):
    """校验任务授权与仓库、分支执行上下文的稳定绑定。"""
    scope = policy["authorization_scopes"]["task_execution"]
    reasons = []
    if not auth:
        return False, ["不存在可唯一匹配的 active 任务执行授权"]
    if auth.get("scope") != "task_execution":
        reasons.append("授权 scope 不是 task_execution")
    if auth.get("status") != "active":
        reasons.append("授权状态不是 active：%s" % auth.get("status"))
    missing = [binding for binding in scope["required_bindings"] if not auth.get(binding)]
    if missing:
        reasons.append("授权缺少绑定字段：%s" % ", ".join(missing))
    now = now if now is not None else time.time()
    expires_at = auth.get("expires_at_epoch")
    if isinstance(expires_at, (int, float)) and now > expires_at:
        reasons.append("授权已过期")
    target_issue = context.get("issue_key")
    if target_issue and auth.get("issue_key") != target_issue:
        reasons.append(
            "Jira 任务不匹配：当前 %s，授权 issue_key=%s"
            % (target_issue, auth.get("issue_key"))
        )

    repositories = auth.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        reasons.append("授权 repositories 必须是非空列表")
        repositories = []
    required_repository_bindings = scope.get("repository_bindings", [])
    seen_repositories = set()
    for index, repository in enumerate(repositories):
        if not isinstance(repository, dict):
            reasons.append("授权 repositories[%d] 不是对象" % index)
            continue
        repository_missing = [
            key for key in required_repository_bindings if not repository.get(key)
        ]
        if repository_missing:
            reasons.append(
                "授权 repositories[%d] 缺少绑定字段：%s"
                % (index, ", ".join(repository_missing))
            )
        name = repository.get("repository")
        if name in seen_repositories:
            reasons.append("授权仓库重复：%s" % name)
        seen_repositories.add(name)

    current_repository = context.get("origin", "")
    current_branch = context.get("branch", "")
    if current_repository:
        matched = next(
            (
                item
                for item in repositories
                if isinstance(item, dict) and item.get("repository") == current_repository
            ),
            None,
        )
        if matched is None:
            reasons.append(
                "仓库不匹配：当前 origin=%s，不在授权仓库集合中" % current_repository
            )
        elif context.get("branch_relevant", True):
            if not current_branch:
                reasons.append("无法确定当前分支，门禁按保守处理")
            elif current_branch != matched.get("work_branch"):
                reasons.append(
                    "分支不匹配：当前 %s，授权 work_branch=%s"
                    % (current_branch, matched.get("work_branch"))
                )
    return not reasons, reasons


def evaluate(operation, context, auth, policy, now=None):
    """对一个标准操作执行三态判定。"""
    operations = policy["operations"]
    scope = policy["authorization_scopes"]["task_execution"]

    if operation == "unknown_external_write":
        return _result(
            ASK,
            operation,
            "未识别的外部写操作，不在操作契约内，需人工确认并补充 Tool Adapter 映射",
        )

    metadata = operations.get(operation)
    if metadata is None:
        return _result(ASK, operation, "未知标准操作，需人工确认并补充操作契约")

    level = metadata["level"]
    if level == "free":
        return _result(ALLOW, operation, "自由操作，无需门禁")
    if level == "forbidden":
        return _result(
            DENY,
            operation,
            "禁止 Agent 执行的不可逆操作（%s）；如确有必要由人工在自己的终端执行"
            % operation,
        )

    if operation == "git_push":
        branch = context.get("push_target_branch") or context.get("branch", "")
        if branch and _branch_protected(branch, policy.get("protected_branches", [])):
            return _result(
                DENY,
                "protected_branch_push",
                "目标分支 %s 是保护分支，禁止 Agent 直接推送" % branch,
            )

    if level == "excluded":
        return _result(
            ASK,
            operation,
            "高风险操作永不被任务授权覆盖，每次都需要人工单独确认",
        )

    valid, reasons = check_authorization(auth, context, policy, now=now)
    if valid and operation in scope["covered_operations"]:
        return _result(
            ALLOW,
            operation,
            "已由任务授权覆盖：%s，仓库 %s（计划 %s）"
            % (
                auth.get("issue_key"),
                context.get("origin") or "由任务绑定",
                auth.get("approved_plan_version"),
            ),
        )
    if valid:
        return _result(ASK, operation, "操作有效但不在任务授权覆盖清单内，需人工确认")
    return _result(ASK, operation, "需要人工确认。授权检查：%s" % "；".join(reasons))


def evaluate_all(operations, context, auth, policy, now=None):
    """复合操作取最严格结果：deny > ask > allow。"""
    if not operations:
        return _result(ASK, "invalid_gate_request", "标准请求没有提供操作")
    results = [evaluate(operation, context, auth, policy, now=now) for operation in operations]
    order = {DENY: 2, ASK: 1, ALLOW: 0}
    results.sort(key=lambda result: order[result["decision"]], reverse=True)
    return results[0]


def _result(decision, operation, reason):
    return {"decision": decision, "operation": operation, "reason": reason}
