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
    directory, _ = resolve_task_directory(cwd, context=context, issue_key=issue_key)
    return directory


def resolve_task_directory(cwd, context=None, issue_key=None):
    """解析 active 任务，并返回可审计的解析状态。"""
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
        matches = [path for _, path in candidates]
    if len(matches) == 1:
        return matches[0], "resolved"
    if len(matches) > 1:
        return None, "ambiguous_active_task"
    return None, "no_active_task"


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


def task_worktree_matches(task_directory, git_cwd, context):
    """确认显式 Git 工作目录就是已解析任务的当前绑定 worktree。"""
    task = _read_json(Path(task_directory) / "state.json")
    if not isinstance(task, dict):
        return False
    try:
        candidate = Path(git_cwd).resolve()
    except OSError:
        return False
    for item in task.get("repositories", []):
        if not isinstance(item, dict):
            continue
        worktree = item.get("worktree")
        if (
            item.get("repository") == context.get("origin")
            and item.get("work_branch") == context.get("branch")
            and isinstance(worktree, dict)
            and worktree.get("status") == "prepared"
            and isinstance(worktree.get("path"), str)
            and Path(worktree["path"]).resolve() == candidate
        ):
            return True
    return False


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


def _git_lines(cwd, *args):
    output = _git(cwd, *args)
    return [line.strip() for line in output.splitlines() if line.strip()]


def normalize_repo(url):
    """把 Git URL 归一为 owner/repository。"""
    if not url:
        return ""
    match = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else url.strip()


def normalize_remote_endpoint(url):
    """保留主机身份地归一 Git URL，用于比较 fetch/push 的真实目的地。"""
    text = (url or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("/"):
        return str(Path(text).resolve())
    if text.startswith("file://"):
        return "file://" + str(Path(text[7:]).resolve())
    scp = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", text)
    if scp:
        host, path = scp.groups()
        endpoint = "%s/%s" % (host.lower(), path.lstrip("/"))
    else:
        remote = re.fullmatch(
            r"(?:ssh|https?|git)://(?:[^@/]+@)?([^/]+)/(.+)", text
        )
        if not remote:
            return ""
        host, path = remote.groups()
        endpoint = "%s/%s" % (host.lower(), path.lstrip("/"))
    return endpoint[:-4] if endpoint.endswith(".git") else endpoint


def git_context(cwd, *, for_push=False):
    context = {
        "branch": _git(cwd, "rev-parse", "--abbrev-ref", "HEAD"),
    }
    if not for_push:
        context["origin"] = normalize_repo(
            _git(cwd, "remote", "get-url", "origin")
        )
        return context

    raw_urls = _git_lines(cwd, "config", "--get-all", "remote.origin.url")
    fetch_urls = _git_lines(cwd, "remote", "get-url", "--all", "origin")
    # Git push 优先使用 remote.<name>.pushurl；未配置时 get-url --push 会按
    # Git 自身语义回退到 fetch URL。多 pushurl 会把一次命令扩散到多个目标，不能
    # 用单个任务授权表示，因此必须失败关闭而不是挑选第一项。
    push_urls = _git_lines(cwd, "remote", "get-url", "--all", "--push", "origin")
    counts = (len(raw_urls), len(fetch_urls), len(push_urls))
    if counts != (1, 1, 1):
        context["origin"] = ""
        context["repository_fact_error"] = (
            "无法唯一确定 origin URL 信任链（raw=%d，fetch=%d，push=%d）"
            % counts
        )
        return context

    raw_endpoint = normalize_remote_endpoint(raw_urls[0])
    fetch_endpoint = normalize_remote_endpoint(fetch_urls[0])
    push_endpoint = normalize_remote_endpoint(push_urls[0])
    context["raw_origin_endpoint"] = raw_endpoint
    context["fetch_origin_endpoint"] = fetch_endpoint
    context["push_origin_endpoint"] = push_endpoint
    context["fetch_origin"] = normalize_repo(fetch_urls[0])
    context["origin"] = normalize_repo(push_urls[0])
    if not all((raw_endpoint, fetch_endpoint, push_endpoint, context["origin"])):
        context["repository_fact_error"] = "origin URL 信任链包含无法识别的 endpoint"
    elif len({raw_endpoint, fetch_endpoint, push_endpoint}) != 1:
        context["repository_fact_error"] = (
            "origin raw、fetch 与 push URL 指向不同 endpoint"
        )
    return context


def _push_repository_binding_error(context, repositories):
    """验证实际 origin 信任链与授权中唯一、已固化的 canonical endpoint。"""
    current_repository = context.get("origin", "")
    matches = [
        item
        for item in repositories
        if isinstance(item, dict) and item.get("repository") == current_repository
    ]
    if len(matches) != 1:
        return "Git 实际 push 仓库无法唯一匹配当前任务授权"
    authorized_endpoint = matches[0].get("authorized_endpoint")
    if not isinstance(authorized_endpoint, str) or not authorized_endpoint:
        return "任务授权缺少唯一可信的 authorized_endpoint；请重新签发授权"
    endpoints = (
        context.get("raw_origin_endpoint"),
        context.get("fetch_origin_endpoint"),
        context.get("push_origin_endpoint"),
        authorized_endpoint,
    )
    if not all(isinstance(endpoint, str) and endpoint for endpoint in endpoints):
        return "push 的 raw、fetch、push 或 authorized endpoint 缺失"
    if len(set(endpoints)) != 1:
        return "push 的 raw、fetch、push 与 authorized endpoint 不一致"
    return ""


def _branch_protected(branch, patterns):
    return any(fnmatch.fnmatch(branch, pattern) for pattern in patterns)


def _push_refspec_error(context, repositories):
    if not context.get("push_refspec_required"):
        return ""
    matches = [
        item
        for item in repositories
        if isinstance(item, dict) and item.get("repository") == context.get("origin")
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("work_branch"), str):
        return ""
    work_branch = matches[0]["work_branch"]
    if not work_branch:
        return ""
    destination = "refs/heads/" + work_branch
    allowed_sources = {"HEAD", work_branch, destination}
    if context.get("push_destination_ref") != destination:
        return "push destination 必须严格等于授权分支 %s" % destination
    if context.get("push_source_ref") not in allowed_sources:
        return "push source 只能是 HEAD 或授权工作分支"
    return ""


def check_authorization(auth, context, policy, now=None):
    """校验任务授权与仓库、分支执行上下文的稳定绑定。"""
    scope = policy["authorization_scopes"]["task_execution"]
    reasons = []
    repository_fact_error = context.get("repository_fact_error")
    if isinstance(repository_fact_error, str) and repository_fact_error:
        reasons.append(repository_fact_error)
    if not auth:
        return False, ["不存在可唯一匹配的 active 任务执行授权"]
    if auth.get("scope") != "task_execution":
        reasons.append("授权 scope 不是 task_execution")
    if auth.get("status") != "active":
        reasons.append("授权状态不是 active：%s" % auth.get("status"))
    missing = [binding for binding in scope["required_bindings"] if not auth.get(binding)]
    if missing:
        reasons.append("授权缺少绑定字段：%s" % ", ".join(missing))
    invalid_types = [
        binding
        for binding in scope["required_bindings"]
        if binding != "repositories"
        and auth.get(binding)
        and not isinstance(auth.get(binding), str)
    ]
    if invalid_types:
        reasons.append("授权绑定字段类型错误：%s" % ", ".join(invalid_types))
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
        repository_invalid = [
            key
            for key in required_repository_bindings
            if repository.get(key) and not isinstance(repository.get(key), str)
        ]
        if repository_invalid:
            reasons.append(
                "授权 repositories[%d] 绑定字段类型错误：%s"
                % (index, ", ".join(repository_invalid))
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
            "受控操作存在包装、目标或参数歧义，无法可靠生成标准请求",
            "unknown_external_write",
            "请研发工程师核对并执行原命令；Agent 不得拆分、改写或换工具重试。"
            "Tool Adapter 更新后，研发工程师可明确要求原样重放一次；再次拒绝则停止。",
        )

    metadata = operations.get(operation)
    if metadata is None:
        return _result(
            ASK,
            operation,
            "未知标准操作，需人工确认并补充操作契约",
            "unknown_operation",
            "请研发工程师确认本次操作；维护者应补充标准操作契约后再重试。",
        )

    level = metadata["level"]
    if level == "free":
        return _result(ALLOW, operation, "自由操作，无需门禁", "operation_free")
    if level == "forbidden":
        return _result(
            DENY,
            operation,
            "禁止 Agent 执行的不可逆操作（%s）；如确有必要由人工在自己的终端执行"
            % operation,
            "forbidden_operation",
            "Agent 必须停止；如确有必要，请研发工程师在自己的终端执行。",
        )

    if operation == "git_push":
        repository_fact_error = context.get("repository_fact_error")
        authorized_repositories = (
            auth.get("repositories", []) if isinstance(auth, dict) else []
        )
        authorization_loaded = context.get("authorization_state") in ("loaded", "invalid")
        repository_binding_error = (
            _push_repository_binding_error(context, authorized_repositories)
            if authorization_loaded
            else ""
        )
        if repository_fact_error or repository_binding_error:
            return _result(
                DENY,
                "untrusted_push_repository",
                repository_fact_error
                or repository_binding_error,
                "untrusted_push_repository",
                "Agent 必须停止推送；请修复 origin URL 信任链或重新签发包含可信 endpoint 的授权后重试。",
            )
        refspec_error = _push_refspec_error(context, authorized_repositories)
        if refspec_error:
            return _result(
                DENY,
                "unauthorized_push_refspec",
                refspec_error,
                "unauthorized_push_refspec",
                "Agent 必须停止推送；只允许从 HEAD 或授权工作分支推送到同名 heads ref。",
            )
        branch = context.get("push_target_branch") or context.get("branch", "")
        if branch and _branch_protected(branch, policy.get("protected_branches", [])):
            return _result(
                DENY,
                "protected_branch_push",
                "目标分支 %s 是保护分支，禁止 Agent 直接推送" % branch,
                "protected_branch_push",
                "Agent 必须停止直接推送；请通过受保护的审查与合入流程处理。",
            )

    if level == "controlled":
        issue_key = context.get("issue_key")
        if not issue_key:
            return _result(
                ASK,
                operation,
                "受控仓库准备必须显式指定 Jira 任务号",
                "issue_key_required",
                "请使用 workflow/task.py repository prepare --issue-key <KEY> 重试。",
            )
        resolution = context.get("task_resolution")
        if resolution != "resolved":
            reason = (
                "Jira 任务 %s 不是当前工作空间中的 active 任务" % issue_key
                if resolution == "no_active_task"
                else "当前执行上下文无法唯一解析 active 任务"
            )
            code = "no_active_task" if resolution == "no_active_task" else "ambiguous_active_task"
            return _result(
                ASK,
                operation,
                reason,
                code,
                "请先接管或恢复对应任务，再使用显式 --issue-key 重试；Agent 停止仓库准备及其依赖步骤。",
            )
        return _result(
            ALLOW,
            operation,
            "active 任务 %s 的受控 Source Pool 与 linked worktree 准备可自动执行" % issue_key,
            "controlled_prepare_allowed",
        )

    if level == "excluded":
        return _result(
            ASK,
            operation,
            "高风险操作永不被任务授权覆盖，每次都需要人工单独确认",
            "excluded_operation",
            "请研发工程师在自己的终端执行原命令，完成后回复“继续”；Agent 不得重试该命令。",
        )

    resolution = context.get("task_resolution")
    if resolution != "resolved":
        reason = (
            "当前操作无法匹配 active 任务"
            if resolution == "no_active_task"
            else "当前操作匹配到多个 active 任务"
        )
        code = "no_active_task" if resolution == "no_active_task" else "ambiguous_active_task"
        return _result(
            ASK,
            operation,
            reason,
            code,
            "请先接管任务或消除 active 任务歧义；Agent 在恢复前停止该操作及其依赖步骤。",
        )

    if context.get("authorization_state") == "missing":
        return _result(
            ASK,
            operation,
            "active 任务尚未签发 task_execution 授权",
            "authorization_missing",
            "请完成方案确认并签发 task_execution 授权；Agent 在授权前停止该操作及其依赖步骤。",
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
            "task_authorization_covered",
        )
    if valid:
        return _result(
            ASK,
            operation,
            "操作不在 task_execution 授权覆盖清单内",
            "operation_not_covered",
            "请研发工程师在自己的终端执行原命令，完成后回复“继续”；Agent 不得重试该命令。",
        )
    return _result(
        ASK,
        operation,
        "task_execution 授权无效：%s" % "；".join(reasons),
        "authorization_invalid",
        "请修复或重新签发有效授权；Agent 在授权恢复前停止该操作及其依赖步骤。",
    )


def evaluate_all(operations, context, auth, policy, now=None):
    """复合操作取最严格结果：deny > ask > allow。"""
    if not operations:
        return _result(
            ASK,
            "invalid_gate_request",
            "标准请求没有提供操作",
            "invalid_gate_request",
            "请修复 Adapter 请求后重试。",
        )
    results = [evaluate(operation, context, auth, policy, now=now) for operation in operations]
    order = {DENY: 2, ASK: 1, ALLOW: 0}
    results.sort(key=lambda result: order[result["decision"]], reverse=True)
    return results[0]


def _result(decision, operation, reason, reason_code, required_action=None):
    result = {
        "decision": decision,
        "operation": operation,
        "reason": reason,
        "reason_code": reason_code,
    }
    if required_action:
        result["required_action"] = required_action
    return result
