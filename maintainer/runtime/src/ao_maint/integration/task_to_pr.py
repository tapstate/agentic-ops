from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Mapping, Sequence
from urllib.parse import urlsplit

from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult


SCHEMA_VERSION: Final = 1
PROTOCOL: Final = "task_to_pr_review"
ISSUE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
ID_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
AGENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
PROFILE_PATTERN = re.compile(r"^[0-9a-z][0-9a-z_-]*$")
REMOTE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
REPOSITORY_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")

ALLOWED_EXTERNAL_ACTIONS: Final = frozenset(
    {
        "jira_read",
        "jira_comment",
        "jira_worklog",
        "git_commit",
        "git_remote_read",
        "git_push_task_branch",
        "github_pr_create_or_update",
        "github_pr_read",
        "github_ci_read",
        "github_artifact_read",
    }
)
PROHIBITED_ACTIONS: Final = (
    "merge_pr",
    "jira_done",
    "release",
    "create_tag",
    "push_protected_branch",
)
QUALITY_CATEGORIES: Final = (
    "automation_gap",
    "manual_friction",
    "output_quality",
    "unreasonable_process",
)
EVENT_ACTIONS: Final = frozenset(
    {
        "step",
        "external_action",
        "jira_readback",
        "jira_write_readback",
        "prohibition_baseline",
        "remote_branch_readback",
        "pr_readback",
        "verification",
        "human_intervention",
        "failure",
        "retry",
        "quality_finding",
        "waiting",
        "retrospective",
        "prohibition_check",
    }
)
EVENT_ACTORS: Final = frozenset(
    {"skill", "runtime", "project_tool", "ai", "human"}
)
EVENT_STATUSES: Final = frozenset({"started", "completed", "blocked"})
RESULT_STATUSES: Final = frozenset(
    {"ready_for_pr_review", "blocked", "failed"}
)
SENSITIVE_VALUE_PATTERNS: Final = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
TERMINAL_JIRA_STATUSES: Final = frozenset(
    {"done", "closed", "resolved", "完成", "已完成"}
)
VERIFICATION_PYTHON_MODULES: Final = frozenset(
    {"bandit", "black", "flake8", "isort", "mypy", "pylint", "pyright", "pytest", "ruff", "unittest"}
)
VERIFICATION_DIRECT_COMMANDS: Final = frozenset(
    {
        "bandit", "black", "cargo", "dotnet", "eslint", "flake8", "go", "gradle",
        "isort", "mvn", "mypy", "node", "npm", "pnpm", "prettier", "py.test",
        "pylint", "pyright", "pytest", "ruff", "tsc", "yarn",
    }
)
VERIFICATION_PROJECT_WRAPPERS: Final = frozenset({"./gradlew", "./mvnw"})
VERIFICATION_FORBIDDEN_EXECUTABLES: Final = frozenset(
    {
        "ao-maint", "ao-work", "ansible", "apt", "apt-get", "bash", "brew", "cmd",
        "composer", "curl", "dash", "dnf", "docker", "fish", "ftp", "gem", "gh", "git",
        "helm", "http", "httpie", "ksh", "kubectl", "nc", "ncat", "netcat", "pip",
        "pip3", "podman", "powershell", "pwsh", "rsync", "scp", "sftp", "sh", "socat",
        "ssh", "telnet", "terraform", "uv", "wget", "yum", "zsh",
    }
)
VERIFICATION_FORBIDDEN_ARGUMENTS: Final = frozenset(
    {
        "-c", "-w", "--command", "--deploy", "--eval", "--fix", "--global", "--install",
        "--interactive", "--package", "--print", "--publish", "--require",
        "--update-snapshots", "--watch", "--write",
    }
)


def task_to_pr_manifest_template(issue_key: str) -> dict[str, Any]:
    _require_issue_key(issue_key, "命令 issue_key")
    return {
        "schema_version": SCHEMA_VERSION,
        "protocol": PROTOCOL,
        "workspace": {"root": "REQUIRED"},
        "issue": {
            "key": issue_key,
            "id": "REQUIRED",
            "project_key": issue_key.partition("-")[0],
        },
        "jira": {
            "base_url": "REQUIRED",
            "account_id": "REQUIRED",
            "assignee_account_id": "REQUIRED",
            "status_mapping": {"REQUIRED": "REQUIRED"},
            "allowed_status_categories": ["REQUIRED"],
        },
        "agent": {
            "agent_id": "REQUIRED",
            "project_profile": "REQUIRED",
            "agentic_run_id": "REQUIRED",
        },
        "task_binding": {
            "issue_content_sha256": "REQUIRED",
            "approved_plan_file": "REQUIRED",
            "approved_plan_sha256": "REQUIRED",
        },
        "execution_identity": {
            "git_author_name": "REQUIRED",
            "git_author_email": "REQUIRED",
            "git_committer_name": "REQUIRED",
            "git_committer_email": "REQUIRED",
            "github_actor_login": "REQUIRED",
        },
        "repository": {
            "root": "REQUIRED",
            "slug": "REQUIRED",
            "remote_name": "REQUIRED",
            "base_branch": "REQUIRED",
            "task_branch": "REQUIRED",
            "target_branch": "REQUIRED",
            "protected_branches": ["REQUIRED"],
        },
        "scope": {
            "included": ["REQUIRED"],
            "excluded": ["REQUIRED"],
        },
        "verification": [
            {
                "id": "REQUIRED",
                "command": ["REQUIRED"],
                "working_directory": "REQUIRED",
                "timeout_seconds": "REQUIRED",
            }
        ],
        "pr_endpoint": {
            "provider": "github",
            "repository_slug": "REQUIRED",
            "target_branch": "REQUIRED",
            "ci_policy": "REQUIRED",
        },
        "permitted_external_actions": ["REQUIRED"],
        "authorization": {
            "reference": "REQUIRED",
            "confirmed_by": "REQUIRED",
            "confirmed_at": "REQUIRED",
            "confirmed_manifest_sha256": "REQUIRED",
        },
    }


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def manifest_digest(payload: Mapping[str, Any]) -> str:
    candidate = copy.deepcopy(dict(payload))
    authorization = candidate.get("authorization")
    if not isinstance(authorization, dict):
        return ""
    authorization["confirmed_manifest_sha256"] = ""
    return digest(candidate)


def result_digest(payload: Mapping[str, Any]) -> str:
    candidate = copy.deepcopy(dict(payload))
    candidate["result_sha256"] = ""
    return digest(candidate)


def verification_digest(item: Mapping[str, Any]) -> str:
    return digest(
        {
            "command": item.get("command"),
            "working_directory": item.get("working_directory"),
            "timeout_seconds": item.get("timeout_seconds"),
        }
    )


def load_json_object(path_value: str, label: str) -> dict[str, Any]:
    """Read exactly one explicitly supplied file; never discover adjacent state."""

    path = Path(path_value)
    try:
        with path.open("rb") as stream:
            encoded = stream.read(1_048_577)
        if len(encoded) > 1_048_576:
            raise _blocked(
                "integration_protocol_json_too_large",
                f"{label} 超过 1 MiB 协议输入上限",
                f"请只保留最小、脱敏且符合 Schema 的 {label}",
            )
        content = encoded.decode("utf-8")
    except RuntimeErrorResult:
        raise
    except (OSError, UnicodeError) as error:
        raise _blocked(
            "integration_protocol_json_invalid",
            f"{label} 无法读取：{error}",
            f"请修复显式提供的 {label} 文件后重试",
        ) from error
    try:
        payload = json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except ValueError as error:
        raise _blocked(
            "integration_protocol_json_invalid",
            f"{label} 不是可读取的 JSON：{error}",
            f"请修复显式提供的 {label} 文件后重试",
        ) from error
    if not isinstance(payload, dict):
        raise _blocked(
            "integration_protocol_json_invalid",
            f"{label} 必须是 JSON object",
            f"请按 shared/integration 的 Schema 修复 {label}",
        )
    _reject_sensitive_content(payload)
    return payload


def validate_manifest(
    payload: Mapping[str, Any], expected_issue_key: str
) -> dict[str, Any]:
    expected_issue_key = _require_issue_key(expected_issue_key, "命令 issue_key")
    value = dict(payload)
    required_manifest_keys = {
            "schema_version",
            "protocol",
            "workspace",
            "issue",
            "jira",
            "agent",
            "task_binding",
            "execution_identity",
            "repository",
            "scope",
            "verification",
            "pr_endpoint",
            "permitted_external_actions",
            "authorization",
    }
    allowed_manifest_keys = required_manifest_keys | {"process_id"}
    if not required_manifest_keys <= set(value) or not set(value) <= allowed_manifest_keys:
        _invalid(
            "manifest",
            "字段不闭合；"
            f"missing={sorted(required_manifest_keys - set(value))}, "
            f"extra={sorted(set(value) - allowed_manifest_keys)}",
        )
    _require_protocol(value, "manifest")
    _reject_required_placeholders(value)
    process_id = value.get("process_id", "development_change_v1")
    if process_id not in {"development_change_v1", "development_change_v2"}:
        _invalid("process_id", "必须是 development_change_v1 或 development_change_v2")

    workspace = _require_mapping(value["workspace"], "workspace")
    _require_exact_keys(workspace, {"root"}, "workspace")
    _require_absolute_path(workspace["root"], "workspace.root")

    issue = _require_mapping(value["issue"], "issue")
    _require_exact_keys(issue, {"key", "id", "project_key"}, "issue")
    issue_key = _require_issue_key(issue["key"], "issue.key")
    if issue_key != expected_issue_key:
        raise _blocked(
            "integration_issue_mismatch",
            f"命令 issue_key={expected_issue_key} 与 manifest issue.key={issue_key} 不一致",
            "请提供与命令 Jira key 完全一致的已确认 manifest",
        )
    _require_id(issue["id"], "issue.id")
    project_key = _require_string(issue["project_key"], "issue.project_key")
    if issue_key.partition("-")[0] != project_key:
        _invalid("issue.project_key", "必须与 issue.key 的项目部分一致")

    jira = _require_mapping(value["jira"], "jira")
    _require_exact_keys(
        jira,
        {
            "base_url",
            "account_id",
            "assignee_account_id",
            "status_mapping",
            "allowed_status_categories",
        },
        "jira",
    )
    _require_url(jira["base_url"], "jira.base_url")
    _require_string(jira["account_id"], "jira.account_id", maximum=2048)
    _require_string(
        jira["assignee_account_id"], "jira.assignee_account_id", maximum=2048
    )
    status_mapping = _require_mapping(jira["status_mapping"], "jira.status_mapping")
    if not status_mapping:
        _invalid("jira.status_mapping", "必须明确绑定 Project Profile 状态映射")
    for status_name, internal_status in status_mapping.items():
        _require_string(status_name, "jira.status_mapping key")
        _require_id(internal_status, f"jira.status_mapping.{status_name}")
    categories = _require_unique_string_list(
        jira["allowed_status_categories"],
        "jira.allowed_status_categories",
        nonempty=True,
    )
    if any(category.casefold() == "done" for category in categories):
        _invalid("jira.allowed_status_categories", "不能允许 Done")
    agent = _require_mapping(value["agent"], "agent")
    _require_exact_keys(
        agent, {"agent_id", "project_profile", "agentic_run_id"}, "agent"
    )
    agent_id = _require_string(agent["agent_id"], "agent.agent_id")
    if len(agent_id) > 128 or not AGENT_ID_PATTERN.fullmatch(agent_id):
        _invalid("agent.agent_id", "只能包含 [0-9A-Za-z_-]，且最长 128 字符")
    profile = _require_string(agent["project_profile"], "agent.project_profile")
    if len(profile) > 128 or not PROFILE_PATTERN.fullmatch(profile):
        _invalid("agent.project_profile", "必须是 lowercase 的安全 Profile 标识")
    _require_id(agent["agentic_run_id"], "agent.agentic_run_id")

    task_binding = _require_mapping(value["task_binding"], "task_binding")
    _require_exact_keys(
        task_binding,
        {"issue_content_sha256", "approved_plan_file", "approved_plan_sha256"},
        "task_binding",
    )
    _require_digest(
        task_binding["issue_content_sha256"], "task_binding.issue_content_sha256"
    )
    _require_digest(
        task_binding["approved_plan_sha256"], "task_binding.approved_plan_sha256"
    )
    plan_file = _require_relative_path(
        task_binding["approved_plan_file"], "task_binding.approved_plan_file"
    )
    plan_parts = Path(plan_file).parts
    if (
        len(plan_parts) < 2
        or plan_parts[0] != "inputs"
        or any(part.startswith(".") for part in plan_parts)
    ):
        _invalid(
            "task_binding.approved_plan_file",
            "必须位于工作空间 inputs/ 下且不得包含隐藏路径",
        )

    identity = _require_mapping(value["execution_identity"], "execution_identity")
    _require_exact_keys(
        identity,
        {
            "git_author_name",
            "git_author_email",
            "git_committer_name",
            "git_committer_email",
            "github_actor_login",
        },
        "execution_identity",
    )
    for field in ("git_author_name", "git_committer_name"):
        _require_string(identity[field], f"execution_identity.{field}")
    for field in ("git_author_email", "git_committer_email"):
        email = _require_string(
            identity[field], f"execution_identity.{field}", maximum=320
        )
        if re.fullmatch(r"[^\s@]+@[^\s@]+", email) is None:
            _invalid(f"execution_identity.{field}", "必须是明确 Git email")
    github_login = _require_string(
        identity["github_actor_login"],
        "execution_identity.github_actor_login",
        maximum=39,
    )
    if re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", github_login
    ) is None:
        _invalid("execution_identity.github_actor_login", "必须是明确 GitHub login")

    repository = _require_mapping(value["repository"], "repository")
    _require_exact_keys(
        repository,
        {
            "root",
            "slug",
            "remote_name",
            "base_branch",
            "task_branch",
            "target_branch",
            "protected_branches",
        },
        "repository",
    )
    _require_absolute_path(repository["root"], "repository.root")
    _require_repository_slug(repository["slug"], "repository.slug")
    remote_name = _require_string(repository["remote_name"], "repository.remote_name")
    if len(remote_name) > 128 or not REMOTE_PATTERN.fullmatch(remote_name):
        _invalid("repository.remote_name", "不是安全的 Git remote 名称")
    for field in ("base_branch", "task_branch", "target_branch"):
        _require_branch(repository[field], f"repository.{field}")
    protected = _require_unique_string_list(
        repository["protected_branches"],
        "repository.protected_branches",
        nonempty=True,
    )
    for branch in protected:
        _require_branch(branch, "repository.protected_branches")
    if repository["target_branch"] not in protected:
        _invalid("repository.protected_branches", "必须包含 target_branch")
    if repository["base_branch"] != repository["target_branch"]:
        _invalid(
            "repository.base_branch",
            "当前协议要求 base_branch 与 target_branch 相同，确保范围 diff 与 PR diff 使用同一基线",
        )
    if repository["task_branch"] in protected:
        _invalid("repository.task_branch", "任务分支不能是保护分支")
    if repository["task_branch"] in {
        repository["base_branch"],
        repository["target_branch"],
    }:
        _invalid("repository.task_branch", "必须与基线和目标分支不同")

    scope = _require_mapping(value["scope"], "scope")
    _require_exact_keys(scope, {"included", "excluded"}, "scope")
    included = _require_unique_string_list(
        scope["included"], "scope.included", nonempty=True
    )
    excluded = _require_unique_string_list(scope["excluded"], "scope.excluded")
    for item in included:
        _require_relative_path(item, "scope.included")
    for item in excluded:
        _require_relative_path(item, "scope.excluded")
    overlap = sorted(set(included) & set(excluded))
    if overlap:
        _invalid("scope", f"included 与 excluded 不能包含同一项：{overlap}")

    verification = value["verification"]
    if not isinstance(verification, list) or not verification:
        _invalid("verification", "必须是非空数组")
    verification_ids: set[str] = set()
    canonical_verifications: set[bytes] = set()
    for index, raw in enumerate(verification):
        item = _require_mapping(raw, f"verification[{index}]")
        _require_exact_keys(
            item,
            {"id", "command", "working_directory", "timeout_seconds"},
            f"verification[{index}]",
        )
        verification_id = _require_id(item["id"], f"verification[{index}].id")
        if verification_id in verification_ids:
            _invalid("verification", "验证 id 不能重复")
        verification_ids.add(verification_id)
        command = _require_string_list(
            item["command"], f"verification[{index}].command", nonempty=True
        )
        for argument in command:
            if len(argument) > 4096 or "\x00" in argument:
                _invalid(f"verification[{index}].command", "argv 过长或包含 NUL")
        working_directory = _require_relative_path(
            item["working_directory"], f"verification[{index}].working_directory"
        )
        _validate_verification_command(
            command,
            working_directory,
            label=f"verification[{index}]",
        )
        timeout = item["timeout_seconds"]
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            _invalid(
                f"verification[{index}].timeout_seconds",
                "必须是 1..3600 秒整数",
            )
        encoded = canonical_bytes(item)
        if encoded in canonical_verifications:
            _invalid("verification", "验证项不能重复")
        canonical_verifications.add(encoded)

    endpoint = _require_mapping(value["pr_endpoint"], "pr_endpoint")
    endpoint_keys = {"provider", "repository_slug", "target_branch", "ci_policy"}
    _require_exact_keys(
        endpoint,
        endpoint_keys | ({"ci"} if process_id == "development_change_v2" else set()),
        "pr_endpoint",
    )
    if endpoint["provider"] != "github":
        _invalid("pr_endpoint.provider", "当前协议只接受 github")
    _require_repository_slug(endpoint["repository_slug"], "pr_endpoint.repository_slug")
    _require_branch(endpoint["target_branch"], "pr_endpoint.target_branch")
    if endpoint["repository_slug"] != repository["slug"]:
        _invalid("pr_endpoint.repository_slug", "必须与 repository.slug 一致")
    if endpoint["target_branch"] != repository["target_branch"]:
        _invalid("pr_endpoint.target_branch", "必须与 repository.target_branch 一致")
    if endpoint["ci_policy"] not in {
        "require_passed",
        "allow_pending",
        "not_required",
    }:
        _invalid("pr_endpoint.ci_policy", "不是受支持的 CI 策略")
    if process_id == "development_change_v2":
        if endpoint["ci_policy"] != "require_passed":
            _invalid("pr_endpoint.ci_policy", "development_change_v2 必须要求 CI passed")
        _validate_ci_config(endpoint["ci"])

    permissions = _require_unique_string_list(
        value["permitted_external_actions"],
        "permitted_external_actions",
        nonempty=True,
    )
    if not set(permissions) <= ALLOWED_EXTERNAL_ACTIONS:
        _invalid("permitted_external_actions", "包含未知外部动作")
    if process_id == "development_change_v2" and not {
        "github_ci_read",
        "github_artifact_read",
    } <= set(permissions):
        _invalid(
            "permitted_external_actions",
            "development_change_v2 必须显式允许 github_ci_read 和 github_artifact_read",
        )

    authorization = _require_mapping(value["authorization"], "authorization")
    _require_exact_keys(
        authorization,
        {"reference", "confirmed_by", "confirmed_at", "confirmed_manifest_sha256"},
        "authorization",
    )
    authorization_reference = _require_reference(
        authorization["reference"], "authorization.reference"
    )
    expected_authorization_reference = (
        f"user-confirmation:{issue_key}:{agent['agentic_run_id']}:"
        f"{task_binding['approved_plan_sha256']}"
    )
    if authorization_reference != expected_authorization_reference:
        _invalid(
            "authorization.reference",
            "必须精确绑定 user-confirmation、当前 issue、agentic_run_id 和批准计划摘要",
        )
    _require_string(authorization["confirmed_by"], "authorization.confirmed_by")
    _require_timestamp(authorization["confirmed_at"], "authorization.confirmed_at")
    confirmed_digest = _require_string(
        authorization["confirmed_manifest_sha256"],
        "authorization.confirmed_manifest_sha256",
    )
    if not DIGEST_PATTERN.fullmatch(confirmed_digest):
        _invalid(
            "authorization.confirmed_manifest_sha256",
            "必须是 64 位小写 SHA-256",
        )
    calculated = manifest_digest(value)
    if confirmed_digest != calculated:
        raise _blocked(
            "integration_manifest_confirmation_mismatch",
            "manifest 内容与人工确认的 canonical SHA-256 不一致",
            "请重新审阅完整 manifest，置空确认摘要计算 canonical SHA-256 后再确认",
            expected_manifest_sha256=calculated,
        )
    _reject_sensitive_content(value)
    return value


def validate_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    _require_exact_keys(
        value,
        {
            "schema_version",
            "protocol",
            "event_id",
            "agentic_run_id",
            "step_id",
            "recorded_at",
            "status",
            "actor",
            "action",
            "duration_seconds",
            "summary",
            "authorization_reference",
            "action_data",
            "evidence_origin",
        },
        "event",
    )
    _require_protocol(value, "event")
    _require_id(value["event_id"], "event.event_id")
    _require_id(value["agentic_run_id"], "event.agentic_run_id")
    _require_id(value["step_id"], "event.step_id")
    _require_timestamp(value["recorded_at"], "event.recorded_at")
    event_status = _require_string(value["status"], "event.status")
    if event_status not in EVENT_STATUSES:
        _invalid("event.status", "必须是 started、completed 或 blocked")
    event_actor = _require_string(value["actor"], "event.actor")
    if event_actor not in EVENT_ACTORS:
        _invalid("event.actor", "不在协议枚举中")
    event_action = _require_string(value["action"], "event.action")
    if event_action not in EVENT_ACTIONS:
        _invalid("event.action", "不在协议枚举中")
    evidence_origin = _require_string(
        value["evidence_origin"], "event.evidence_origin"
    )
    if evidence_origin not in {"imported", "runtime_probe"}:
        _invalid("event.evidence_origin", "必须是 imported 或 runtime_probe")
    if evidence_origin == "runtime_probe" and event_actor != "runtime":
        _invalid("event", "runtime_probe 事件只能由 runtime 生成")
    if event_action in {
        "jira_readback",
        "jira_write_readback",
        "remote_branch_readback",
        "pr_readback",
        "verification",
        "prohibition_check",
    } and (event_actor != "runtime" or evidence_origin != "runtime_probe"):
        _invalid(
            "event",
            f"关键事实 {event_action} 必须由 runtime + runtime_probe 生成",
        )
    duration = value["duration_seconds"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        _invalid("event.duration_seconds", "必须是大于等于 0 的数值")
    _require_string(value["summary"], "event.summary")
    if value["authorization_reference"] is not None:
        _require_reference(
            value["authorization_reference"], "event.authorization_reference"
        )
    data = _require_mapping(value["action_data"], "event.action_data")
    if event_status == "started":
        if event_action != "step" or data:
            _invalid("event", "started 事件只能使用 action=step 和空 action_data")
    else:
        _validate_action_data(event_action, data)
    _reject_sensitive_content(value)
    return value


def _validate_ci_config(value: object) -> dict[str, Any]:
    config = _require_mapping(value, "pr_endpoint.ci")
    _require_exact_keys(
        config,
        {
            "provider", "start_timeout_seconds", "completion_timeout_seconds", "poll_interval_seconds",
            "max_remediation_attempts", "required_checks", "workflows",
            "artifact_name_patterns", "report_parser", "limits", "completion",
        },
        "pr_endpoint.ci",
    )
    if config["provider"] != "github-actions":
        _invalid("pr_endpoint.ci.provider", "第一阶段只支持 github-actions")
    if config["report_parser"] != "maven-failsafe-v1":
        _invalid("pr_endpoint.ci.report_parser", "第一阶段只支持 maven-failsafe-v1")

    def integer(field: str, minimum: int, maximum: int) -> int:
        item = config[field]
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            _invalid(f"pr_endpoint.ci.{field}", f"必须是 {minimum}..{maximum} 的整数")
        return item

    start_timeout = integer("start_timeout_seconds", 1, 3_600)
    completion_timeout = integer("completion_timeout_seconds", 1, 3_600)
    if start_timeout != 300:
        _invalid("pr_endpoint.ci.start_timeout_seconds", "必须固定为 300 秒")
    if completion_timeout != 600:
        _invalid("pr_endpoint.ci.completion_timeout_seconds", "必须固定为 600 秒")
    if integer("poll_interval_seconds", 1, 300) > min(start_timeout, completion_timeout):
        _invalid("pr_endpoint.ci.poll_interval_seconds", "不能超过两个 CI timeout")
    integer("max_remediation_attempts", 1, 3)
    for field in ("required_checks", "workflows", "artifact_name_patterns"):
        _require_unique_string_list(config[field], f"pr_endpoint.ci.{field}", nonempty=True)

    limits = _require_mapping(config["limits"], "pr_endpoint.ci.limits")
    _require_exact_keys(
        limits,
        {"max_archive_bytes", "max_extracted_bytes", "max_file_bytes", "max_files", "max_depth"},
        "pr_endpoint.ci.limits",
    )

    def limit(field: str, minimum: int, maximum: int) -> int:
        item = limits[field]
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            _invalid(f"pr_endpoint.ci.limits.{field}", f"必须是 {minimum}..{maximum} 的整数")
        return item

    max_archive = limit("max_archive_bytes", 1_024, 524_288_000)
    max_extracted = limit("max_extracted_bytes", 1_024, 1_073_741_824)
    max_file = limit("max_file_bytes", 1_024, 1_073_741_824)
    limit("max_files", 1, 20_000)
    limit("max_depth", 1, 100)
    if max_archive > max_extracted or max_file > max_extracted:
        _invalid("pr_endpoint.ci.limits", "archive 和单文件上限不能超过展开总量上限")
    completion = _require_mapping(config["completion"], "pr_endpoint.ci.completion")
    _require_exact_keys(
        completion,
        {"finish_agent_run_on_pass", "transition_jira_done"},
        "pr_endpoint.ci.completion",
    )
    if completion["finish_agent_run_on_pass"] is not True:
        _invalid("pr_endpoint.ci.completion.finish_agent_run_on_pass", "v2 必须在 CI 通过后结束运行")
    if not isinstance(completion["transition_jira_done"], bool):
        _invalid("pr_endpoint.ci.completion.transition_jira_done", "必须是布尔值")
    return config


def validate_result_package(
    manifest: Mapping[str, Any], payload: Mapping[str, Any]
) -> dict[str, Any]:
    value = dict(payload)
    _require_exact_keys(
        value,
        {
            "schema_version",
            "protocol",
            "status",
            "delivery_passed",
            "manifest_sha256",
            "generated_at",
            "facts",
            "timeline",
            "human_interventions",
            "waitings",
            "failures",
            "quality_findings",
            "retrospective",
            "prohibitions",
            "next_action",
            "result_sha256",
        },
        "result",
    )
    _require_protocol(value, "result")
    result_status = _require_string(value["status"], "result.status")
    if result_status not in RESULT_STATUSES:
        _invalid("result.status", "必须明确为 ready_for_pr_review、blocked 或 failed")
    delivery_passed = value["delivery_passed"]
    if not isinstance(delivery_passed, bool):
        _invalid("result.delivery_passed", "必须是 boolean")
    if delivery_passed != (result_status == "ready_for_pr_review"):
        _evidence_invalid(
            "delivery_passed 必须且只能在 status=ready_for_pr_review 时为 true"
        )
    supplied_manifest_digest = _require_digest(
        value["manifest_sha256"], "result.manifest_sha256"
    )
    confirmed_manifest_digest = manifest_digest(manifest)
    if supplied_manifest_digest != confirmed_manifest_digest:
        raise _blocked(
            "integration_result_manifest_mismatch",
            "result.manifest_sha256 与已确认 manifest 不一致",
            "请使用同一 agentic_run_id 的原始 manifest 和结果包，不得拼接不同运行",
            expected_manifest_sha256=confirmed_manifest_digest,
        )
    _require_timestamp(value["generated_at"], "result.generated_at")
    _require_string(value["next_action"], "result.next_action")
    supplied_result_digest = _require_digest(
        value["result_sha256"], "result.result_sha256"
    )
    calculated_result_digest = result_digest(value)
    if supplied_result_digest != calculated_result_digest:
        raise _blocked(
            "integration_result_digest_mismatch",
            "结果包内容与 result_sha256 不一致",
            "请从 developer 原始审计重新生成脱敏结果包，不得手工改写结果",
            expected_result_sha256=calculated_result_digest,
        )

    timeline_raw = value["timeline"]
    if not isinstance(timeline_raw, list) or not timeline_raw:
        _invalid("result.timeline", "必须是非空数组")
    timeline: list[dict[str, Any]] = []
    previous: str | None = None
    event_ids: set[str] = set()
    expected_run_id = manifest["agent"]["agentic_run_id"]
    expected_authorization = manifest["authorization"]["reference"]
    for sequence, raw in enumerate(timeline_raw, start=1):
        envelope = _validate_envelope(raw, sequence, previous, f"result.timeline[{sequence - 1}]")
        event = envelope["event"]
        event_id = event["event_id"]
        if event_id in event_ids:
            _invalid("result.timeline", f"event_id 重复：{event_id}")
        event_ids.add(event_id)
        if event["agentic_run_id"] != expected_run_id:
            raise _blocked(
                "integration_result_run_mismatch",
                f"事件 {event_id} 的 agentic_run_id 与 manifest 不一致",
                "请只验收同一 agentic_run_id 的完整结果包",
            )
        if event["authorization_reference"] != expected_authorization:
            raise _blocked(
                "integration_result_authorization_mismatch",
                f"事件 {event_id} 没有引用当前 manifest 的明确授权",
                "请只验收逐事件绑定同一 authorization.reference 的完整结果包",
            )
        timeline.append(envelope)
        previous = envelope["event_sha256"]

    events = [envelope["event"] for envelope in timeline]
    event_index = {event["event_id"]: event for event in events}
    sequence_index = {
        envelope["event"]["event_id"]: envelope["sequence"] for envelope in timeline
    }
    _validate_closed_steps(events)
    completed_by_action = {
        action: [
            event
            for event in events
            if event["status"] == "completed" and event["action"] == action
        ]
        for action in EVENT_ACTIONS
    }
    terminal_by_action = {
        action: [
            event
            for event in events
            if event["status"] in {"completed", "blocked"}
            and event["action"] == action
        ]
        for action in EVENT_ACTIONS
    }

    facts = _validate_facts(value["facts"])
    _validate_fact_projection(facts, completed_by_action)
    _validate_specialized_envelopes(value, timeline, completed_by_action)
    _validate_retry_references(completed_by_action, event_index, sequence_index)
    _validate_retrospective(completed_by_action, event_index, sequence_index)
    _validate_external_actions(
        manifest,
        terminal_by_action["external_action"],
        event_index,
        sequence_index,
        str(value["status"]),
    )
    observed_prohibitions = _validate_prohibitions(
        completed_by_action["prohibition_check"], str(value["status"])
    )
    _validate_prohibition_baseline(
        completed_by_action,
        sequence_index,
        str(value["status"]),
    )
    _validate_all_readback_bindings(
        manifest,
        terminal_by_action,
        event_index,
        sequence_index,
    )
    _validate_fact_bindings(manifest, facts, observed_prohibitions)
    _validate_verification_bindings(
        manifest,
        facts,
        str(value["status"]),
        completed_by_action["failure"],
        completed_by_action["retry"],
    )
    _validate_result_outcome(
        manifest, value, facts, completed_by_action, events, observed_prohibitions
    )
    _reject_sensitive_content(value)
    return value


def acceptance_summary(
    expected_issue_key: str,
    manifest_payload: Mapping[str, Any],
    result_payload: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = validate_manifest(manifest_payload, expected_issue_key)
    result = validate_result_package(manifest, result_payload)
    facts = result["facts"]
    prohibitions = result["prohibitions"]
    observed = [
        envelope["event"]["action_data"]["action"]
        for envelope in prohibitions
        if envelope["event"]["action_data"]["observed"]
    ]
    reported_result_status = str(result["status"])
    delivery_passed = bool(result["delivery_passed"])
    return {
        "issue_key": expected_issue_key,
        "agent_id": manifest["agent"]["agent_id"],
        "project_profile": manifest["agent"]["project_profile"],
        "agentic_run_id": manifest["agent"]["agentic_run_id"],
        "repository_slug": manifest["repository"]["slug"],
        "task_branch": manifest["repository"]["task_branch"],
        "target_branch": manifest["repository"]["target_branch"],
        "package_status": "accepted",
        "evidence_basis": "developer_runtime_probe_result_package",
        "trust_scope": "validated_runtime_probe_chain_not_independent_maintainer_readback",
        "authorization_basis": "conversation_user_confirmation_manifest_attestation",
        "independent_human_approval_verified": False,
        "github_actor_scope": (
            "probe_pr_authenticated_gh_session_only_not_remote_push_actor_attestation"
        ),
        "independent_external_readback": False,
        "cryptographic_remote_attestation": False,
        "reported_result_status": reported_result_status,
        "delivery_status": reported_result_status,
        "delivery_passed": delivery_passed,
        "formal_takeover_verified": (
            bool(facts["jira_readback"]["formal_takeover_verified"])
            if facts["jira_readback"] is not None
            else False
        ),
        "pr_url": facts["pr_readback"]["url"] if facts["pr_readback"] else None,
        "manifest_sha256": result["manifest_sha256"],
        "result_sha256": result["result_sha256"],
        "observed_prohibitions": observed,
        "evidence_counts": {
            "timeline": len(result["timeline"]),
            "verifications": len(facts["verifications"]),
            "human_interventions": len(result["human_interventions"]),
            "waitings": len(result["waitings"]),
            "failures": len(result["failures"]),
            "quality_findings": len(result["quality_findings"]),
            "prohibitions": len(result["prohibitions"]),
        },
        "result_next_action": result["next_action"],
        "acceptance_next_action": (
            "结果包已通过 canonical 摘要、事件链、Runtime probe 来源和事实绑定校验；"
            "maintainer 未独立访问 Jira/Git/GitHub，也不提供密码学远程证明。"
            "user-confirmation 只表示清单内会话确认声明，未独立回读确认人的 Jira 身份。"
            "github_actor_login 仅证明 probe-pr 时 gh 会话身份，不证明远端 push actor。"
            f"下一步：{result['next_action']}"
        ),
        "external_execution": False,
        "source_files_modified": False,
    }


def _validate_action_data(action: str, data: Mapping[str, Any]) -> None:
    if action == "step":
        _require_exact_keys(data, set(), "event.action_data")
        return
    if action == "external_action":
        _require_exact_keys(
            data, {"action", "target", "status", "readback_event_id"}, "event.action_data"
        )
        external_action = _require_string(
            data["action"], "event.action_data.action"
        )
        if external_action not in ALLOWED_EXTERNAL_ACTIONS:
            _invalid("event.action_data.action", "不是协议允许的外部动作")
        _require_reference(data["target"], "event.action_data.target")
        external_status = _require_string(
            data["status"], "event.action_data.status"
        )
        if external_status not in {"applied", "unknown", "not_applied"}:
            _invalid("event.action_data.status", "不是协议允许的外部动作状态")
        if data["readback_event_id"] is not None:
            _require_id(data["readback_event_id"], "event.action_data.readback_event_id")
        return
    if action == "jira_readback":
        _require_exact_keys(
            data,
            {
                "provider",
                "issue_key",
                "issue_id",
                "project_key",
                "url",
                "status",
                "assignee",
                "account_id",
                "assignee_account_id",
                "status_category",
                "mapped_status",
                "takeover_comment_id",
                "formal_takeover_verified",
                "issue_content_sha256",
                "approved_plan_sha256",
                "observed_at",
                "reference",
            },
            "event.action_data",
        )
        if data["provider"] != "jira":
            _invalid("event.action_data.provider", "Jira 回读 provider 必须是 jira")
        _require_issue_key(data["issue_key"], "event.action_data.issue_key")
        _require_id(data["issue_id"], "event.action_data.issue_id")
        _require_string(data["project_key"], "event.action_data.project_key")
        _require_url(data["url"], "event.action_data.url")
        _require_string(data["status"], "event.action_data.status")
        if data["assignee"] is not None:
            _require_string(data["assignee"], "event.action_data.assignee")
        _require_string(data["account_id"], "event.action_data.account_id")
        _require_string(
            data["assignee_account_id"], "event.action_data.assignee_account_id"
        )
        _require_string(data["status_category"], "event.action_data.status_category")
        _require_id(data["mapped_status"], "event.action_data.mapped_status")
        if data["takeover_comment_id"] is not None:
            _require_string(
                data["takeover_comment_id"], "event.action_data.takeover_comment_id"
            )
        if not isinstance(data["formal_takeover_verified"], bool):
            _invalid("event.action_data.formal_takeover_verified", "必须是 boolean")
        _require_digest(
            data["issue_content_sha256"],
            "event.action_data.issue_content_sha256",
        )
        _require_digest(
            data["approved_plan_sha256"],
            "event.action_data.approved_plan_sha256",
        )
        _require_timestamp(data["observed_at"], "event.action_data.observed_at")
        _require_reference(data["reference"], "event.action_data.reference")
        return
    if action == "jira_write_readback":
        _require_exact_keys(
            data,
            {
                "provider",
                "issue_key",
                "agentic_run_id",
                "operation",
                "plan_file",
                "attempt_file",
                "plan_id",
                "idempotency_key",
                "external_id",
                "created",
                "write_precondition",
                "write_attempt_id",
                "write_attempt_started_at",
                "content_sha256",
                "body_sha256",
                "title",
                "details_sha256",
                "time_spent_seconds",
                "started",
                "excludes_waiting",
                "included_work",
                "excluded_waiting_categories",
                "observed_at",
                "reference",
            },
            "event.action_data",
        )
        if data["provider"] != "jira":
            _invalid(
                "event.action_data.provider", "Jira 写后回读 provider 必须是 jira"
            )
        _require_issue_key(data["issue_key"], "event.action_data.issue_key")
        _require_id(data["agentic_run_id"], "event.action_data.agentic_run_id")
        operation = _require_string(
            data["operation"], "event.action_data.operation"
        )
        if operation not in {"jira_comment", "jira_worklog"}:
            _invalid(
                "event.action_data.operation", "必须是 jira_comment 或 jira_worklog"
            )
        _require_relative_path(data["plan_file"], "event.action_data.plan_file")
        if data["attempt_file"] is not None:
            _require_relative_path(
                data["attempt_file"], "event.action_data.attempt_file"
            )
        _require_id(data["plan_id"], "event.action_data.plan_id")
        idempotency_key = _require_string(
            data["idempotency_key"], "event.action_data.idempotency_key"
        )
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", idempotency_key) is None:
            _invalid("event.action_data.idempotency_key", "不是安全的 Jira 幂等键")
        _require_string(data["external_id"], "event.action_data.external_id")
        if not isinstance(data["created"], bool):
            _invalid("event.action_data.created", "必须是 boolean")
        if data["write_precondition"] not in {"absent", "preexisting"}:
            _invalid(
                "event.action_data.write_precondition",
                "必须是 absent 或 preexisting",
            )
        if data["write_attempt_id"] is not None:
            _require_id(
                data["write_attempt_id"], "event.action_data.write_attempt_id"
            )
        if data["write_attempt_started_at"] is not None:
            _require_timestamp(
                data["write_attempt_started_at"],
                "event.action_data.write_attempt_started_at",
            )
        if data["created"]:
            if (
                data["write_precondition"] != "absent"
                or data["attempt_file"] is None
                or data["write_attempt_id"] is None
                or data["write_attempt_started_at"] is None
            ):
                _invalid(
                    "event.action_data",
                    "created=true 必须绑定首次 marker_absent 计划和真实 create 尝试",
                )
        elif any(
            data[field] is not None
            for field in (
                "attempt_file",
                "write_attempt_id",
                "write_attempt_started_at",
            )
        ):
            _invalid(
                "event.action_data",
                "created=false 不能携带 create 尝试归因",
            )
        _require_digest(data["content_sha256"], "event.action_data.content_sha256")
        _require_digest(data["body_sha256"], "event.action_data.body_sha256")
        if operation == "jira_comment":
            if any(
                data[field] is not None
                for field in (
                    "title",
                    "details_sha256",
                    "time_spent_seconds",
                    "started",
                    "excludes_waiting",
                    "included_work",
                    "excluded_waiting_categories",
                )
            ):
                _invalid(
                    "event.action_data", "Jira 评论回读不能伪装 Worklog 字段"
                )
        else:
            _require_string(data["title"], "event.action_data.title")
            _require_digest(
                data["details_sha256"], "event.action_data.details_sha256"
            )
            if (
                type(data["time_spent_seconds"]) is not int
                or data["time_spent_seconds"] < 1
            ):
                _invalid(
                    "event.action_data.time_spent_seconds", "必须是正整数"
                )
            _require_timestamp(data["started"], "event.action_data.started")
            if data["excludes_waiting"] is not True:
                _invalid(
                    "event.action_data.excludes_waiting", "必须明确为 true"
                )
            included_work = data["included_work"]
            if not isinstance(included_work, list) or not included_work:
                _invalid(
                    "event.action_data.included_work", "必须是非空耗时组成数组"
                )
            included_seconds = 0
            for index, raw in enumerate(included_work):
                item = _require_mapping(
                    raw, f"event.action_data.included_work[{index}]"
                )
                _require_exact_keys(
                    item,
                    {"description", "seconds"},
                    f"event.action_data.included_work[{index}]",
                )
                description = _require_string(
                    item["description"],
                    f"event.action_data.included_work[{index}].description",
                )
                if re.search(r"[\u3400-\u9fff]", description) is None:
                    _invalid(
                        f"event.action_data.included_work[{index}].description",
                        "必须包含中文说明",
                    )
                seconds = item["seconds"]
                if type(seconds) is not int or seconds < 1:
                    _invalid(
                        f"event.action_data.included_work[{index}].seconds",
                        "必须是正整数",
                    )
                included_seconds += seconds
            if included_seconds != data["time_spent_seconds"]:
                _invalid(
                    "event.action_data.included_work",
                    "各项 seconds 之和必须等于 time_spent_seconds",
                )
            excluded = _require_unique_string_list(
                data["excluded_waiting_categories"],
                "event.action_data.excluded_waiting_categories",
                nonempty=True,
            )
            for index, category in enumerate(excluded):
                if re.search(r"[\u3400-\u9fff]", category) is None:
                    _invalid(
                        f"event.action_data.excluded_waiting_categories[{index}]",
                        "必须包含中文类别说明",
                    )
        _require_timestamp(data["observed_at"], "event.action_data.observed_at")
        _require_reference(data["reference"], "event.action_data.reference")
        return
    if action == "prohibition_baseline":
        _require_exact_keys(
            data,
            {
                "issue_key",
                "repository_slug",
                "remote_name",
                "jira_status",
                "jira_status_category",
                "tag_refs",
                "release_records",
                "protected_heads",
                "local_head_sha",
                "task_branch_remote_sha",
                "task_open_pr",
                "observed_at",
                "reference",
            },
            "event.action_data",
        )
        _require_issue_key(data["issue_key"], "event.action_data.issue_key")
        _require_repository_slug(
            data["repository_slug"], "event.action_data.repository_slug"
        )
        remote_name = _require_string(
            data["remote_name"], "event.action_data.remote_name"
        )
        if len(remote_name) > 128 or not REMOTE_PATTERN.fullmatch(remote_name):
            _invalid("event.action_data.remote_name", "不是安全的 Git remote 名称")
        _require_string(data["jira_status"], "event.action_data.jira_status")
        _require_string(
            data["jira_status_category"],
            "event.action_data.jira_status_category",
        )
        _require_snapshot_records(
            data["tag_refs"], "event.action_data.tag_refs", "name", "sha"
        )
        _require_snapshot_records(
            data["release_records"],
            "event.action_data.release_records",
            "tag_name",
            "published_at",
            nullable_value=True,
            timestamp_value=True,
        )
        _require_snapshot_records(
            data["protected_heads"],
            "event.action_data.protected_heads",
            "branch",
            "sha",
            nullable_value=True,
        )
        if not isinstance(data["local_head_sha"], str) or not GIT_SHA_PATTERN.fullmatch(
            data["local_head_sha"]
        ):
            _invalid("event.action_data.local_head_sha", "必须是 Git SHA")
        task_remote_sha = data["task_branch_remote_sha"]
        if task_remote_sha is not None and (
            not isinstance(task_remote_sha, str)
            or not GIT_SHA_PATTERN.fullmatch(task_remote_sha)
        ):
            _invalid("event.action_data.task_branch_remote_sha", "必须是 Git SHA 或 null")
        task_open_pr = data["task_open_pr"]
        if task_open_pr is not None:
            task_open_pr = _require_mapping(
                task_open_pr, "event.action_data.task_open_pr"
            )
            _require_exact_keys(
                task_open_pr,
                {"number", "url", "head_sha", "base_branch"},
                "event.action_data.task_open_pr",
            )
            number = task_open_pr["number"]
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                _invalid("event.action_data.task_open_pr.number", "必须是正整数")
            _require_url(
                task_open_pr["url"],
                "event.action_data.task_open_pr.url",
                hosts={"github.com"},
            )
            if not isinstance(task_open_pr["head_sha"], str) or not GIT_SHA_PATTERN.fullmatch(
                task_open_pr["head_sha"]
            ):
                _invalid("event.action_data.task_open_pr.head_sha", "必须是 Git SHA")
            _require_branch(
                task_open_pr["base_branch"],
                "event.action_data.task_open_pr.base_branch",
            )
        _require_timestamp(data["observed_at"], "event.action_data.observed_at")
        _require_reference(data["reference"], "event.action_data.reference")
        return
    if action == "remote_branch_readback":
        _require_exact_keys(
            data,
            {
                "provider",
                "url",
                "repository_slug",
                "remote_name",
                "branch",
                "sha",
                "status",
                "protected",
                "observed_at",
                "reference",
                "origin_url",
                "base_sha",
                "head_sha",
                "baseline_event_id",
                "baseline_local_head_sha",
                "baseline_remote_sha",
                "baseline_local_is_ancestor",
                "baseline_remote_is_ancestor",
                "attributed_actions",
                "verification_event_ids",
                "changed_paths",
                "worktree_clean",
                "git_author_name",
                "git_author_email",
                "git_committer_name",
                "git_committer_email",
                "commit_count",
                "commit_identity_sha256",
                "approved_plan_sha256",
            },
            "event.action_data",
        )
        if data["provider"] != "git":
            _invalid("event.action_data.provider", "远端分支回读 provider 必须是 git")
        _require_url(data["url"], "event.action_data.url")
        _require_repository_slug(
            data["repository_slug"], "event.action_data.repository_slug"
        )
        remote = _require_string(data["remote_name"], "event.action_data.remote_name")
        if len(remote) > 128 or not REMOTE_PATTERN.fullmatch(remote):
            _invalid("event.action_data.remote_name", "不是安全的 remote 名称")
        _require_branch(data["branch"], "event.action_data.branch")
        if not isinstance(data["sha"], str) or not GIT_SHA_PATTERN.fullmatch(data["sha"]):
            _invalid("event.action_data.sha", "必须是 40 到 64 位小写提交摘要")
        if data["status"] != "exists":
            _invalid("event.action_data.status", "必须为 exists")
        if not isinstance(data["protected"], bool):
            _invalid("event.action_data.protected", "必须是 boolean")
        _require_timestamp(data["observed_at"], "event.action_data.observed_at")
        _require_reference(data["reference"], "event.action_data.reference")
        _require_string(data["origin_url"], "event.action_data.origin_url")
        for field in ("base_sha", "head_sha"):
            if not isinstance(data[field], str) or not GIT_SHA_PATTERN.fullmatch(data[field]):
                _invalid(f"event.action_data.{field}", "必须是 Git SHA")
        _require_id(data["baseline_event_id"], "event.action_data.baseline_event_id")
        if not isinstance(data["baseline_local_head_sha"], str) or not GIT_SHA_PATTERN.fullmatch(
            data["baseline_local_head_sha"]
        ):
            _invalid("event.action_data.baseline_local_head_sha", "必须是 Git SHA")
        baseline_remote_sha = data["baseline_remote_sha"]
        if baseline_remote_sha is not None and (
            not isinstance(baseline_remote_sha, str)
            or not GIT_SHA_PATTERN.fullmatch(baseline_remote_sha)
        ):
            _invalid("event.action_data.baseline_remote_sha", "必须是 Git SHA 或 null")
        if not isinstance(data["baseline_local_is_ancestor"], bool):
            _invalid("event.action_data.baseline_local_is_ancestor", "必须是 boolean")
        baseline_remote_is_ancestor = data["baseline_remote_is_ancestor"]
        if baseline_remote_is_ancestor is not None and not isinstance(
            baseline_remote_is_ancestor, bool
        ):
            _invalid("event.action_data.baseline_remote_is_ancestor", "必须是 boolean 或 null")
        if (baseline_remote_sha is None) != (baseline_remote_is_ancestor is None):
            _invalid(
                "event.action_data.baseline_remote_is_ancestor",
                "必须与 baseline_remote_sha 是否存在保持一致",
            )
        attributed_actions = _require_unique_string_list(
            data["attributed_actions"],
            "event.action_data.attributed_actions",
        )
        if not set(attributed_actions) <= {"git_commit", "git_push_task_branch"}:
            _invalid("event.action_data.attributed_actions", "包含非 Git 归因动作")
        for event_id in _require_unique_string_list(
            data["verification_event_ids"],
            "event.action_data.verification_event_ids",
        ):
            _require_id(event_id, "event.action_data.verification_event_ids")
        changed_paths = _require_unique_string_list(
            data["changed_paths"], "event.action_data.changed_paths", nonempty=True
        )
        for path in changed_paths:
            _require_relative_path(path, "event.action_data.changed_paths")
        if data["worktree_clean"] is not True:
            _invalid("event.action_data.worktree_clean", "可信 Git probe 要求干净工作树")
        for field in (
            "git_author_name",
            "git_author_email",
            "git_committer_name",
            "git_committer_email",
        ):
            _require_string(data[field], f"event.action_data.{field}")
        if (
            isinstance(data["commit_count"], bool)
            or not isinstance(data["commit_count"], int)
            or data["commit_count"] < 1
        ):
            _invalid("event.action_data.commit_count", "必须是正整数")
        _require_digest(
            data["commit_identity_sha256"],
            "event.action_data.commit_identity_sha256",
        )
        _require_digest(
            data["approved_plan_sha256"],
            "event.action_data.approved_plan_sha256",
        )
        return
    if action == "pr_readback":
        _require_exact_keys(
            data,
            {
                "provider",
                "repository_slug",
                "number",
                "url",
                "status",
                "merged",
                "draft",
                "head_branch",
                "head_sha",
                "base_branch",
                "review_state",
                "ci_status",
                "github_actor_login",
                "approved_plan_sha256",
                "baseline_event_id",
                "git_readback_event_id",
                "attributed_actions",
                "creation_proof",
                "observed_at",
                "reference",
            },
            "event.action_data",
        )
        if data["provider"] != "github":
            _invalid("event.action_data.provider", "PR 回读 provider 必须是 github")
        _require_repository_slug(
            data["repository_slug"], "event.action_data.repository_slug"
        )
        number = data["number"]
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            _invalid("event.action_data.number", "必须是正整数")
        _require_url(data["url"], "event.action_data.url", hosts={"github.com"})
        pr_status = _require_string(data["status"], "event.action_data.status")
        if pr_status != "open":
            _invalid("event.action_data.status", "必须是 open")
        if not isinstance(data["merged"], bool):
            _invalid("event.action_data.merged", "必须是 boolean")
        if not isinstance(data["draft"], bool):
            _invalid("event.action_data.draft", "必须是 boolean")
        _require_branch(data["head_branch"], "event.action_data.head_branch")
        if not isinstance(data["head_sha"], str) or not GIT_SHA_PATTERN.fullmatch(
            data["head_sha"]
        ):
            _invalid("event.action_data.head_sha", "必须是 40 到 64 位小写提交摘要")
        _require_branch(data["base_branch"], "event.action_data.base_branch")
        review_state = _require_string(
            data["review_state"], "event.action_data.review_state"
        )
        if review_state not in {
            "awaiting_review",
            "changes_requested",
            "approved",
        }:
            _invalid("event.action_data.review_state", "不是协议允许的审查状态")
        ci_status = _require_string(data["ci_status"], "event.action_data.ci_status")
        if ci_status not in {"pending", "passed", "failed", "not_configured"}:
            _invalid("event.action_data.ci_status", "不是协议允许的 CI 状态")
        github_login = _require_string(
            data["github_actor_login"],
            "event.action_data.github_actor_login",
            maximum=39,
        )
        if re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", github_login
        ) is None:
            _invalid(
                "event.action_data.github_actor_login", "必须是明确 GitHub login"
            )
        _require_digest(
            data["approved_plan_sha256"],
            "event.action_data.approved_plan_sha256",
        )
        _require_id(data["baseline_event_id"], "event.action_data.baseline_event_id")
        _require_id(
            data["git_readback_event_id"],
            "event.action_data.git_readback_event_id",
        )
        attributed_actions = _require_unique_string_list(
            data["attributed_actions"],
            "event.action_data.attributed_actions",
        )
        if not set(attributed_actions) <= {"github_pr_create_or_update"}:
            _invalid("event.action_data.attributed_actions", "包含非 PR 归因动作")
        if not isinstance(data["creation_proof"], bool):
            _invalid("event.action_data.creation_proof", "必须是 boolean")
        _require_timestamp(data["observed_at"], "event.action_data.observed_at")
        _require_reference(data["reference"], "event.action_data.reference")
        return
    if action == "verification":
        _require_exact_keys(
            data,
            {
                "id",
                "status",
                "command_sha256",
                "evidence_reference",
                "exit_code",
                "duration_seconds",
                "stdout_sha256",
                "stderr_sha256",
                "output_summary",
                "head_sha",
            },
            "event.action_data",
        )
        _require_id(data["id"], "event.action_data.id")
        verification_status = _require_string(
            data["status"], "event.action_data.status"
        )
        if verification_status not in {"passed", "failed", "blocked"}:
            _invalid("event.action_data.status", "不是协议允许的验证状态")
        _require_digest(data["command_sha256"], "event.action_data.command_sha256")
        _require_reference(
            data["evidence_reference"], "event.action_data.evidence_reference"
        )
        if type(data["exit_code"]) is not int:
            _invalid("event.action_data.exit_code", "必须是整数")
        duration = data["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            _invalid("event.action_data.duration_seconds", "必须是非负有限数")
        _require_digest(data["stdout_sha256"], "event.action_data.stdout_sha256")
        _require_digest(data["stderr_sha256"], "event.action_data.stderr_sha256")
        _require_string(data["output_summary"], "event.action_data.output_summary")
        if not isinstance(data["head_sha"], str) or not GIT_SHA_PATTERN.fullmatch(
            data["head_sha"]
        ):
            _invalid("event.action_data.head_sha", "必须是 Git HEAD SHA")
        return
    if action == "waiting":
        _require_exact_keys(
            data,
            {"reason", "started_at", "ended_at", "duration_seconds"},
            "event.action_data",
        )
        _require_string(data["reason"], "event.action_data.reason")
        started = _require_timestamp(data["started_at"], "event.action_data.started_at")
        ended = _require_timestamp(data["ended_at"], "event.action_data.ended_at")
        if _parse_timestamp(ended) < _parse_timestamp(started):
            _invalid("event.action_data", "waiting ended_at 不能早于 started_at")
        duration = data["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            _invalid("event.action_data.duration_seconds", "必须是非负有限数")
        return
    if action == "human_intervention":
        _require_exact_keys(
            data, {"reason", "action", "impact_seconds"}, "event.action_data"
        )
        _require_string(data["reason"], "event.action_data.reason")
        _require_string(data["action"], "event.action_data.action")
        impact = data["impact_seconds"]
        if (
            isinstance(impact, bool)
            or not isinstance(impact, (int, float))
            or not math.isfinite(impact)
            or impact < 0
        ):
            _invalid("event.action_data.impact_seconds", "必须是非负数值")
        return
    if action == "failure":
        _require_exact_keys(data, {"code", "detail", "retry_safe"}, "event.action_data")
        _require_id(data["code"], "event.action_data.code")
        _require_string(data["detail"], "event.action_data.detail")
        if not isinstance(data["retry_safe"], bool):
            _invalid("event.action_data.retry_safe", "必须是 boolean")
        return
    if action == "retry":
        _require_exact_keys(
            data, {"failure_event_id", "attempt", "outcome"}, "event.action_data"
        )
        _require_id(data["failure_event_id"], "event.action_data.failure_event_id")
        attempt = data["attempt"]
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            _invalid("event.action_data.attempt", "必须是正整数")
        outcome = _require_string(data["outcome"], "event.action_data.outcome")
        if outcome not in {"succeeded", "failed", "blocked"}:
            _invalid("event.action_data.outcome", "不是协议允许的重试结果")
        return
    if action == "quality_finding":
        _require_exact_keys(
            data,
            {
                "category",
                "detail",
                "evidence_reference",
                "impact",
                "root_cause_hypothesis",
                "reproduction",
                "sanitized_example",
                "improvement_candidate",
                "suggested_asset",
                "benefit",
                "risk",
                "frequency",
            },
            "event.action_data",
        )
        category = _require_string(data["category"], "event.action_data.category")
        if category not in QUALITY_CATEGORIES:
            _invalid("event.action_data.category", "不是协议允许的复盘分类")
        for field in (
            "detail",
            "impact",
            "root_cause_hypothesis",
            "reproduction",
            "sanitized_example",
            "improvement_candidate",
            "benefit",
            "risk",
            "frequency",
        ):
            _require_string(data[field], f"event.action_data.{field}")
        _require_reference(
            data["evidence_reference"], "event.action_data.evidence_reference"
        )
        suggested_asset = _require_string(
            data["suggested_asset"], "event.action_data.suggested_asset"
        )
        if suggested_asset not in {
            "skill",
            "python_runtime",
            "rule",
            "template",
            "profile",
            "test",
        }:
            _invalid("event.action_data.suggested_asset", "不是协议允许的改进载体")
        return
    if action == "retrospective":
        _require_exact_keys(
            data,
            {
                "reviewed_categories",
                "category_reviews",
                "quality_finding_event_ids",
                "human_intervention_event_ids",
                "failure_event_ids",
                "retry_event_ids",
                "waiting_event_ids",
                "ordered_improvement_event_ids",
                "residual_risks",
                "summary",
            },
            "event.action_data",
        )
        categories = _require_unique_string_list(
            data["reviewed_categories"],
            "event.action_data.reviewed_categories",
            nonempty=True,
        )
        if set(categories) != set(QUALITY_CATEGORIES):
            _invalid("event.action_data.reviewed_categories", "必须逐项审查四类质量问题")
        category_reviews = data["category_reviews"]
        if not isinstance(category_reviews, list) or len(category_reviews) != len(
            QUALITY_CATEGORIES
        ):
            _invalid(
                "event.action_data.category_reviews", "必须为四类问题各提供一条结论"
            )
        reviewed: list[str] = []
        for index, raw in enumerate(category_reviews):
            review = _require_mapping(
                raw, f"event.action_data.category_reviews[{index}]"
            )
            _require_exact_keys(
                review,
                {
                    "category",
                    "outcome",
                    "rationale",
                    "evidence_references",
                    "source_event_ids",
                },
                f"event.action_data.category_reviews[{index}]",
            )
            category = _require_string(
                review["category"],
                f"event.action_data.category_reviews[{index}].category",
            )
            if category not in QUALITY_CATEGORIES:
                _invalid(
                    f"event.action_data.category_reviews[{index}].category",
                    "不是受支持的复盘分类",
                )
            reviewed.append(category)
            if review["outcome"] not in {"finding", "no_finding"}:
                _invalid(
                    f"event.action_data.category_reviews[{index}].outcome",
                    "必须是 finding 或 no_finding",
                )
            _require_string(
                review["rationale"],
                f"event.action_data.category_reviews[{index}].rationale",
            )
            _require_unique_string_list(
                review["evidence_references"],
                f"event.action_data.category_reviews[{index}].evidence_references",
                nonempty=True,
            )
            for event_id in _require_unique_string_list(
                review["source_event_ids"],
                f"event.action_data.category_reviews[{index}].source_event_ids",
            ):
                _require_id(
                    event_id,
                    f"event.action_data.category_reviews[{index}].source_event_ids",
                )
        if set(reviewed) != set(QUALITY_CATEGORIES) or len(set(reviewed)) != len(reviewed):
            _invalid(
                "event.action_data.category_reviews", "必须唯一覆盖四类质量问题"
            )
        for field in (
            "quality_finding_event_ids",
            "human_intervention_event_ids",
            "failure_event_ids",
            "retry_event_ids",
            "waiting_event_ids",
            "ordered_improvement_event_ids",
        ):
            for event_id in _require_unique_string_list(
                data[field], f"event.action_data.{field}"
            ):
                _require_id(event_id, f"event.action_data.{field}")
        _require_string_list(data["residual_risks"], "event.action_data.residual_risks")
        _require_string(data["summary"], "event.action_data.summary")
        return
    if action == "prohibition_check":
        _require_exact_keys(
            data, {"action", "observed", "evidence_reference"}, "event.action_data"
        )
        prohibited_action = _require_string(
            data["action"], "event.action_data.action"
        )
        if prohibited_action not in PROHIBITED_ACTIONS:
            _invalid("event.action_data.action", "不是协议定义的禁止动作")
        if not isinstance(data["observed"], bool) and data["observed"] != "not_verified":
            _invalid(
                "event.action_data.observed", "必须是 boolean 或 not_verified"
            )
        _require_reference(
            data["evidence_reference"], "event.action_data.evidence_reference"
        )
        return
    _invalid("event.action", "没有对应 action_data 合同")


def _validate_facts(value: object) -> dict[str, Any]:
    facts = _require_mapping(value, "result.facts")
    base_keys = {
            "jira_readback",
            "remote_branch_readback",
            "pr_readback",
            "verifications",
            "external_actions",
    }
    if set(facts) not in {frozenset(base_keys), frozenset(base_keys | {"ci_completion"})}:
        _invalid("result.facts", "字段不闭合；只允许可选 ci_completion")
    for field, action in (
        ("jira_readback", "jira_readback"),
        ("remote_branch_readback", "remote_branch_readback"),
        ("pr_readback", "pr_readback"),
    ):
        item = facts[field]
        if item is not None:
            _validate_action_data(action, _require_mapping(item, f"result.facts.{field}"))
    for field, action in (
        ("verifications", "verification"),
        ("external_actions", "external_action"),
    ):
        items = facts[field]
        if not isinstance(items, list):
            _invalid(f"result.facts.{field}", "必须是数组")
        for index, item in enumerate(items):
            _validate_action_data(
                action, _require_mapping(item, f"result.facts.{field}[{index}]")
            )
    if "ci_completion" in facts:
        _validate_ci_completion(facts["ci_completion"])
    return facts


def _validate_ci_completion(value: object) -> dict[str, Any]:
    completion = _require_mapping(value, "result.facts.ci_completion")
    _require_exact_keys(
        completion,
        {
            "provider", "head_sha", "attempt_id", "ci_status", "started_at",
            "execution_started_at", "finished_at", "start_deadline_at", "completion_deadline_at", "required_checks", "workflow_runs",
            "artifact", "report", "remediations", "remediation_attempts_used",
            "remediation_attempts_remaining",
        },
        "result.facts.ci_completion",
    )
    if completion["provider"] != "github-actions" or completion["ci_status"] != "passed":
        _invalid("result.facts.ci_completion", "必须是 github-actions 的 passed 终态")
    if not isinstance(completion["head_sha"], str) or not GIT_SHA_PATTERN.fullmatch(completion["head_sha"]):
        _invalid("result.facts.ci_completion.head_sha", "必须是 Git SHA")
    if re.fullmatch(r"[0-9a-f]{24}", _require_string(completion["attempt_id"], "result.facts.ci_completion.attempt_id")) is None:
        _invalid("result.facts.ci_completion.attempt_id", "必须是 24 位小写十六进制")
    for field in (
        "started_at", "execution_started_at", "finished_at",
        "start_deadline_at", "completion_deadline_at",
    ):
        _require_timestamp(completion[field], f"result.facts.ci_completion.{field}")
    checks = completion["required_checks"]
    if not isinstance(checks, list) or not checks:
        _invalid("result.facts.ci_completion.required_checks", "必须是非空数组")
    names: list[str] = []
    for index, raw in enumerate(checks):
        item = _require_mapping(raw, f"result.facts.ci_completion.required_checks[{index}]")
        _require_exact_keys(item, {"name", "status", "conclusion"}, f"result.facts.ci_completion.required_checks[{index}]")
        names.append(_require_string(item["name"], f"result.facts.ci_completion.required_checks[{index}].name"))
        if item["conclusion"] != "SUCCESS":
            _evidence_invalid("CI 完成证据包含非 SUCCESS 必需检查")
    if len(names) != len(set(names)):
        _invalid("result.facts.ci_completion.required_checks", "检查名必须唯一")
    for field in ("workflow_runs", "remediations"):
        if not isinstance(completion[field], list):
            _invalid(f"result.facts.ci_completion.{field}", "必须是数组")
    for field in ("artifact", "report"):
        if completion[field] is not None and not isinstance(completion[field], dict):
            _invalid(f"result.facts.ci_completion.{field}", "必须是对象或 null")
    for field in ("remediation_attempts_used", "remediation_attempts_remaining"):
        item = completion[field]
        if isinstance(item, bool) or not isinstance(item, int) or not 0 <= item <= 3:
            _invalid(f"result.facts.ci_completion.{field}", "必须是 0..3 的整数")
    return completion


def _validate_envelope(
    raw: object, expected_sequence: int, previous: str | None, label: str
) -> dict[str, Any]:
    envelope = _require_mapping(raw, label)
    _require_exact_keys(
        envelope,
        {"sequence", "previous_event_sha256", "event_sha256", "event"},
        label,
    )
    if (
        type(envelope["sequence"]) is not int
        or envelope["sequence"] != expected_sequence
    ):
        _invalid(label, f"sequence 必须连续且当前应为 {expected_sequence}")
    if envelope["previous_event_sha256"] != previous:
        _invalid(label, "previous_event_sha256 与前一事件不一致")
    event = validate_event(_require_mapping(envelope["event"], f"{label}.event"))
    supplied_digest = _require_digest(envelope["event_sha256"], f"{label}.event_sha256")
    expected_digest = digest(
        {
            "sequence": expected_sequence,
            "previous_event_sha256": previous,
            "event": event,
        }
    )
    if supplied_digest != expected_digest:
        raise _blocked(
            "integration_event_chain_invalid",
            f"{label} 的 canonical hash chain 校验失败",
            "请从 developer 原始不可变事件日志重新生成结果包",
        )
    return envelope


def _validate_closed_steps(events: Sequence[Mapping[str, Any]]) -> None:
    by_step: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, event in enumerate(events):
        by_step.setdefault(str(event["step_id"]), []).append((index, event))
    for step_id, values in by_step.items():
        started = [(index, event) for index, event in values if event["status"] == "started"]
        terminal = [
            (index, event)
            for index, event in values
            if event["status"] in {"completed", "blocked"}
        ]
        if len(started) != 1 or len(terminal) != 1 or started[0][0] >= terminal[0][0]:
            _evidence_invalid(
                f"步骤 {step_id} 未形成唯一且有序的 started -> completed|blocked 闭环"
            )


def _validate_fact_projection(
    facts: Mapping[str, Any], by_action: Mapping[str, list[dict[str, Any]]]
) -> None:
    for field, action in (
        ("jira_readback", "jira_readback"),
        ("remote_branch_readback", "remote_branch_readback"),
        ("pr_readback", "pr_readback"),
    ):
        events = by_action[action]
        expected = events[-1]["action_data"] if events else None
        if facts[field] != expected:
            _evidence_invalid(f"facts.{field} 不是 timeline 中最后一条完成回读")
    for field, action in (
        ("verifications", "verification"),
        ("external_actions", "external_action"),
    ):
        expected = [event["action_data"] for event in by_action[action]]
        if facts[field] != expected:
            _evidence_invalid(f"facts.{field} 未完整、按序投影 timeline 完成事件")


def _validate_specialized_envelopes(
    result: Mapping[str, Any],
    timeline: Sequence[dict[str, Any]],
    by_action: Mapping[str, list[dict[str, Any]]],
) -> None:
    envelope_by_id = {
        envelope["event"]["event_id"]: envelope for envelope in timeline
    }
    for field, action in (
        ("human_interventions", "human_intervention"),
        ("waitings", "waiting"),
        ("failures", "failure"),
        ("quality_findings", "quality_finding"),
        ("prohibitions", "prohibition_check"),
    ):
        supplied = result[field]
        if not isinstance(supplied, list):
            _invalid(f"result.{field}", "必须是数组")
        expected = [envelope_by_id[event["event_id"]] for event in by_action[action]]
        if supplied != expected:
            _evidence_invalid(f"result.{field} 未完整、按序引用 timeline 的 {action} 事件")
    retrospective_events = by_action["retrospective"]
    if len(retrospective_events) != 1:
        _evidence_invalid("必须且只能有一条 completed retrospective 事件")
    expected_retrospective = envelope_by_id[retrospective_events[0]["event_id"]]
    if result["retrospective"] != expected_retrospective:
        _evidence_invalid("result.retrospective 未引用 timeline 中的完整复盘事件")


def _validate_retry_references(
    by_action: Mapping[str, list[dict[str, Any]]],
    event_index: Mapping[str, Mapping[str, Any]],
    sequence_index: Mapping[str, int],
) -> None:
    for retry in by_action["retry"]:
        failure_id = retry["action_data"]["failure_event_id"]
        referenced = event_index.get(failure_id)
        if (
            referenced is None
            or referenced["action"] != "failure"
            or sequence_index[failure_id] >= sequence_index[retry["event_id"]]
        ):
            _evidence_invalid(
                f"retry 事件必须引用更早的 failure：{failure_id}"
            )


def _validate_retrospective(
    by_action: Mapping[str, list[dict[str, Any]]],
    event_index: Mapping[str, Mapping[str, Any]],
    sequence_index: Mapping[str, int],
) -> None:
    retrospective_events = by_action["retrospective"]
    if len(retrospective_events) != 1:
        _evidence_invalid("必须且只能有一条 completed retrospective 事件")
    retrospective_event = retrospective_events[0]
    data = retrospective_event["action_data"]
    expected = {
        "quality_finding_event_ids": {
            event["event_id"] for event in by_action["quality_finding"]
        },
        "human_intervention_event_ids": {
            event["event_id"] for event in by_action["human_intervention"]
        },
        "failure_event_ids": {event["event_id"] for event in by_action["failure"]},
        "retry_event_ids": {event["event_id"] for event in by_action["retry"]},
        "waiting_event_ids": {event["event_id"] for event in by_action["waiting"]},
    }
    for field, expected_ids in expected.items():
        supplied = data[field]
        if set(supplied) != expected_ids or len(supplied) != len(expected_ids):
            _evidence_invalid(f"retrospective.{field} 未完整引用对应审计事件")
    quality_ids = expected["quality_finding_event_ids"]
    ordered = data["ordered_improvement_event_ids"]
    if set(ordered) != quality_ids or len(ordered) != len(quality_ids):
        _evidence_invalid("retrospective 未对全部改进候选给出唯一排序")
    if set(data["reviewed_categories"]) != set(QUALITY_CATEGORIES):
        _evidence_invalid("retrospective 未逐项审查四类质量问题")
    reviews = data["category_reviews"]
    review_by_category = {item["category"]: item for item in reviews}
    if len(review_by_category) != len(QUALITY_CATEGORIES):
        _evidence_invalid("retrospective.category_reviews 未唯一覆盖四类质量问题")
    process_event_ids = {
        event["event_id"]
        for action in ("failure", "retry", "human_intervention", "waiting")
        for event in by_action[action]
    }
    reviewed_process_ids: set[str] = set()
    for category in QUALITY_CATEGORIES:
        findings = [
            event
            for event in by_action["quality_finding"]
            if event["action_data"]["category"] == category
        ]
        review = review_by_category.get(category)
        if review is None:
            _evidence_invalid(f"retrospective 缺少 {category} 分类结论")
        source_ids = set(review["source_event_ids"])
        for event_id in source_ids:
            source = event_index.get(event_id)
            if source is None or source["action"] not in {
                "quality_finding",
                "failure",
                "retry",
                "human_intervention",
                "waiting",
            }:
                _evidence_invalid(
                    f"retrospective {category} 引用了不允许的来源事件：{event_id}"
                )
            if (
                source["action"] == "quality_finding"
                and source["action_data"]["category"] != category
            ):
                _evidence_invalid(
                    f"retrospective {category} 引用了其它分类的 finding：{event_id}"
                )
            if sequence_index[event_id] >= sequence_index[retrospective_event["event_id"]]:
                _evidence_invalid(
                    f"retrospective 来源事件必须早于复盘：{event_id}"
                )
        expected_outcome = "finding" if findings or source_ids else "no_finding"
        if review["outcome"] != expected_outcome:
            _evidence_invalid(
                f"retrospective {category} 的 finding/no_finding 与实际发现不一致"
            )
        finding_ids = {event["event_id"] for event in findings}
        if finding_ids and not finding_ids <= source_ids:
            _evidence_invalid(
                f"retrospective {category} 未把全部 finding 事件列为来源"
            )
        if source_ids and not source_ids <= set(review["evidence_references"]):
            _evidence_invalid(
                f"retrospective {category} 的证据未覆盖全部来源事件"
            )
        if review["outcome"] == "finding":
            reviewed_process_ids.update(source_ids & process_event_ids)
    uncovered = process_event_ids - reviewed_process_ids
    if uncovered:
        _evidence_invalid(
            "failure/retry/human_intervention/waiting 必须逐事件被 finding 分类复盘引用："
            f"{sorted(uncovered)}"
        )
    for field in (
        "quality_finding_event_ids",
        "human_intervention_event_ids",
        "failure_event_ids",
        "retry_event_ids",
        "waiting_event_ids",
        "ordered_improvement_event_ids",
    ):
        for event_id in data[field]:
            if event_id not in event_index:
                _evidence_invalid(f"retrospective 引用了不存在的事件：{event_id}")
            if sequence_index[event_id] >= sequence_index[retrospective_event["event_id"]]:
                _evidence_invalid(
                    f"retrospective 引用的事件必须早于复盘：{event_id}"
                )


def _validate_external_actions(
    manifest: Mapping[str, Any],
    external_actions: Sequence[Mapping[str, Any]],
    event_index: Mapping[str, Mapping[str, Any]],
    sequence_index: Mapping[str, int],
    result_status: str,
) -> None:
    permissions = set(manifest["permitted_external_actions"])
    authorization = manifest["authorization"]["reference"]
    allowed_readbacks = {
        "jira_read": {"jira_readback", "prohibition_baseline"},
        "jira_comment": {"jira_write_readback"},
        "jira_worklog": {"jira_write_readback"},
        "git_commit": {"remote_branch_readback"},
        "git_remote_read": {"remote_branch_readback", "prohibition_baseline"},
        "git_push_task_branch": {"remote_branch_readback"},
        "github_pr_create_or_update": {"pr_readback"},
        "github_pr_read": {"pr_readback", "prohibition_baseline"},
    }
    for event in external_actions:
        data = event["action_data"]
        action = data["action"]
        if action not in permissions:
            raise _blocked(
                "integration_external_action_not_authorized",
                f"外部动作 {action} 不在 manifest 授权范围内",
                "请停止使用结果包；范围变化必须重新确认 manifest",
            )
        if data["status"] in {"applied", "unknown"} and event[
            "authorization_reference"
        ] != authorization:
            raise _blocked(
                "integration_external_action_authorization_mismatch",
                f"外部动作 {action} 没有引用 manifest 授权",
                "请补充与该运行绑定的真实授权引用，不得从聊天或隐式配置推断",
            )
        reference = data["readback_event_id"]
        if data["status"] == "applied":
            if not reference or reference not in event_index:
                _evidence_invalid(f"已执行外部动作 {action} 缺少真实回读事件")
            readback = event_index[reference]
            if readback["status"] != "completed" or readback["action"] not in allowed_readbacks[action]:
                _evidence_invalid(f"外部动作 {action} 引用的回读事件类型不匹配")
            if (
                readback["actor"] != "runtime"
                or readback["evidence_origin"] != "runtime_probe"
            ):
                _evidence_invalid(f"外部动作 {action} 未引用 Runtime 可信回读")
            if sequence_index[reference] <= sequence_index[event["event_id"]]:
                _evidence_invalid(f"外部动作 {action} 的回读事件没有发生在动作之后")
            expected_target = _external_action_target(action, readback)
            if expected_target is None or data["target"] != expected_target:
                _evidence_invalid(f"外部动作 {action} 的目标未与回读事实绑定")
            if (
                action in {"git_commit", "git_push_task_branch"}
                and action not in readback["action_data"]["attributed_actions"]
            ):
                _evidence_invalid(
                    f"外部动作 {action} 的 Git 回读没有声明本运行归因证明"
                )
            if (
                action == "github_pr_create_or_update"
                and action not in readback["action_data"]["attributed_actions"]
            ):
                _evidence_invalid("PR 动作回读没有声明本运行新建归因证明")
        if data["status"] == "unknown" and result_status == "ready_for_pr_review":
            _evidence_invalid(f"外部动作 {action} 的结果仍为 unknown")


def _validate_prohibitions(
    events: Sequence[Mapping[str, Any]], result_status: str
) -> list[str]:
    actions = [event["action_data"]["action"] for event in events]
    if len(actions) != len(PROHIBITED_ACTIONS) or set(actions) != set(PROHIBITED_ACTIONS):
        _evidence_invalid("五项禁止动作必须各有且只有一条完成审计")
    if result_status == "ready_for_pr_review" and any(
        event["action_data"]["observed"] == "not_verified" for event in events
    ):
        _evidence_invalid("ready_for_pr_review 的五项禁止动作必须全部实时核验")
    observed = [
        event["action_data"]["action"]
        for event in events
        if event["action_data"]["observed"] is True
    ]
    if observed and result_status != "failed":
        raise _blocked(
            "integration_prohibited_action_requires_failed_result",
            f"观察到禁止动作但结果状态不是 failed：{', '.join(observed)}",
            "请停止自动化并显式生成 failed 事故结果包，保留越权证据",
        )
    return observed


def _validate_prohibition_baseline(
    by_action: Mapping[str, list[dict[str, Any]]],
    sequence_index: Mapping[str, int],
    result_status: str,
) -> None:
    baselines = by_action["prohibition_baseline"]
    if result_status == "ready_for_pr_review" and len(baselines) != 1:
        _evidence_invalid(
            "ready_for_pr_review 必须包含且只包含一条外部写入前禁止动作基线"
        )
    if not baselines:
        return
    if len(baselines) != 1:
        _evidence_invalid("禁止动作基线不能重复")
    baseline = baselines[0]
    baseline_sequence = sequence_index[baseline["event_id"]]
    write_sequences = [
        sequence_index[event["event_id"]]
        for event in by_action["external_action"]
        if event["action_data"]["action"]
        not in {"jira_read", "git_remote_read", "github_pr_read"}
        and event["action_data"]["status"] in {"applied", "unknown"}
    ]
    if write_sequences and baseline_sequence >= min(write_sequences):
        _evidence_invalid("禁止动作基线必须早于任何外部写入")
    if result_status == "ready_for_pr_review":
        marker = f"baseline={baseline['event_id']}"
        for event in by_action["prohibition_check"]:
            if marker not in event["action_data"]["evidence_reference"]:
                _evidence_invalid(
                    f"禁止动作 {event['action_data']['action']} 没有绑定运行前基线"
                )


def _validate_fact_bindings(
    manifest: Mapping[str, Any],
    facts: Mapping[str, Any],
    observed_prohibitions: Sequence[str],
) -> None:
    issue = manifest["issue"]
    repository = manifest["repository"]
    endpoint = manifest["pr_endpoint"]
    expected_jira = manifest["jira"]
    task_binding = manifest["task_binding"]
    execution_identity = manifest["execution_identity"]
    jira = facts["jira_readback"]
    branch = facts["remote_branch_readback"]
    pr = facts["pr_readback"]

    if jira is not None:
        if (
            jira["issue_key"] != issue["key"]
            or jira["issue_id"] != issue["id"]
            or jira["project_key"] != issue["project_key"]
        ):
            _evidence_invalid("Jira 回读身份与 manifest issue 不一致")
        expected_jira_url = (
            f"{str(expected_jira['base_url']).rstrip('/')}/browse/{issue['key']}"
        )
        if jira["url"] != expected_jira_url:
            _evidence_invalid("Jira 回读 URL 与 manifest jira.base_url/issue 不一致")
        if (
            jira["account_id"] != expected_jira["account_id"]
            or jira["assignee_account_id"] != expected_jira["assignee_account_id"]
            or jira["account_id"] != jira["assignee_account_id"]
            or (
                jira["assignee"] is not None
                and jira["assignee"] != jira["assignee_account_id"]
            )
            or jira["status_category"]
            not in expected_jira["allowed_status_categories"]
            or jira["status_category"].casefold() == "done"
            or jira["mapped_status"]
            != expected_jira["status_mapping"].get(jira["status"])
            or jira["mapped_status"] == "completed"
        ):
            _evidence_invalid("Jira 账户、负责人、状态分类或 Profile 映射不一致")
        if (
            jira["issue_content_sha256"]
            != task_binding["issue_content_sha256"]
            or jira["approved_plan_sha256"]
            != task_binding["approved_plan_sha256"]
        ):
            _evidence_invalid("Jira 任务内容或批准计划摘要与 manifest 不一致")
        if jira["formal_takeover_verified"] != (
            jira["takeover_comment_id"] is not None
        ):
            _evidence_invalid("Jira 接管评论引用与正式接管结论不一致")
        terminal = jira["status"].strip().casefold() in TERMINAL_JIRA_STATUSES
        if terminal and "jira_done" not in observed_prohibitions:
            _evidence_invalid("Jira 终态回读与 jira_done 禁止动作审计不一致")

    if branch is not None:
        if (
            branch["repository_slug"] != repository["slug"]
            or branch["remote_name"] != repository["remote_name"]
            or branch["branch"] != repository["task_branch"]
        ):
            _evidence_invalid("远端分支事实与 manifest 任务分支不一致")
        if _repository_slug_from_origin(branch["origin_url"]) != repository["slug"]:
            _evidence_invalid("Git origin_url 与 manifest repository.slug 不一致")
        expected_branch_url = (
            f"https://github.com/{repository['slug']}/tree/{repository['task_branch']}"
        )
        if branch["url"] != expected_branch_url:
            _evidence_invalid("远端分支 URL 与 manifest 仓库/任务分支不一致")
        if branch["sha"] != branch["head_sha"] or branch["base_sha"] == branch["head_sha"]:
            _evidence_invalid("Git 远端 SHA、本地 HEAD 或基线 SHA 关系不一致")
        for field in (
            "git_author_name",
            "git_author_email",
            "git_committer_name",
            "git_committer_email",
        ):
            if branch[field] != execution_identity[field]:
                _evidence_invalid(f"Git 提交身份 {field} 与 manifest 不一致")
        if branch["approved_plan_sha256"] != task_binding["approved_plan_sha256"]:
            _evidence_invalid("Git probe 未绑定已确认批准计划摘要")
        outside = [
            path
            for path in branch["changed_paths"]
            if not any(
                fnmatch.fnmatchcase(path, pattern)
                for pattern in manifest["scope"]["included"]
            )
            or any(
                fnmatch.fnmatchcase(path, pattern)
                for pattern in manifest["scope"]["excluded"]
            )
        ]
        if outside:
            _evidence_invalid(f"Git 变更路径越出 manifest scope：{outside[:10]}")
        if branch["protected"] and "push_protected_branch" not in observed_prohibitions:
            _evidence_invalid("任务分支保护事实与 push_protected_branch 审计不一致")

    if pr is not None:
        if (
            pr["repository_slug"] != endpoint["repository_slug"]
            or pr["head_branch"] != repository["task_branch"]
            or pr["base_branch"] != endpoint["target_branch"]
        ):
            _evidence_invalid("PR 回读与 manifest 仓库或分支绑定不一致")
        if branch is not None and pr["head_sha"] != branch["sha"]:
            _evidence_invalid("PR head SHA 与远端任务分支 SHA 不一致")
        expected_pr_path = f"/{endpoint['repository_slug']}/pull/{pr['number']}"
        if urlsplit(pr["url"]).path.rstrip("/") != expected_pr_path:
            _evidence_invalid("PR URL 与 manifest 仓库或 PR number 不一致")
        if pr["merged"] and "merge_pr" not in observed_prohibitions:
            _evidence_invalid("PR merged 事实与 merge_pr 禁止动作审计不一致")
        if (
            pr["github_actor_login"]
            != execution_identity["github_actor_login"]
            or pr["approved_plan_sha256"]
            != task_binding["approved_plan_sha256"]
        ):
            _evidence_invalid("GitHub actor 或批准计划摘要与 manifest 不一致")


def _validate_all_readback_bindings(
    manifest: Mapping[str, Any],
    by_action: Mapping[str, list[dict[str, Any]]],
    event_index: Mapping[str, Mapping[str, Any]],
    sequence_index: Mapping[str, int],
) -> None:
    issue = manifest["issue"]
    jira_manifest = manifest["jira"]
    repository = manifest["repository"]
    endpoint = manifest["pr_endpoint"]
    task_binding = manifest["task_binding"]
    execution_identity = manifest["execution_identity"]
    for event in by_action["prohibition_baseline"]:
        data = event["action_data"]
        protected_head_map = {
            item["branch"]: item["sha"] for item in data["protected_heads"]
        }
        expected_local_head = (
            data["task_branch_remote_sha"]
            or protected_head_map.get(repository["target_branch"])
        )
        if (
            data["issue_key"] != issue["key"]
            or data["repository_slug"] != repository["slug"]
            or data["remote_name"] != repository["remote_name"]
            or data["jira_status_category"].casefold() == "done"
            or [item["branch"] for item in data["protected_heads"]]
            != sorted(repository["protected_branches"])
            or any(item["sha"] is None for item in data["protected_heads"])
            or data["local_head_sha"] != expected_local_head
        ):
            _evidence_invalid(
                f"禁止动作基线 {event['event_id']} 未绑定 manifest、包含预置提交或已观察到越权终态"
            )
        task_open_pr = data["task_open_pr"]
        if task_open_pr is not None:
            expected_path = (
                f"/{repository['slug']}/pull/{task_open_pr['number']}"
            )
            if (
                data["task_branch_remote_sha"] is None
                or task_open_pr["head_sha"] != data["task_branch_remote_sha"]
                or task_open_pr["base_branch"] != repository["target_branch"]
                or urlsplit(task_open_pr["url"]).path.rstrip("/") != expected_path
            ):
                _evidence_invalid(
                    f"禁止动作基线 {event['event_id']} 的既有 open PR 未绑定任务分支远端事实"
                )
    for event in by_action["jira_readback"]:
        data = event["action_data"]
        if (
            data["issue_key"] != issue["key"]
            or data["issue_id"] != issue["id"]
            or data["project_key"] != issue["project_key"]
            or data["url"]
            != f"{str(jira_manifest['base_url']).rstrip('/')}/browse/{issue['key']}"
        ):
            _evidence_invalid(
                f"Jira 回读事件 {event['event_id']} 与 manifest issue 不一致"
            )
        if (
            data["account_id"] != jira_manifest["account_id"]
            or data["assignee_account_id"] != jira_manifest["assignee_account_id"]
            or data["account_id"] != data["assignee_account_id"]
            or data["status_category"]
            not in jira_manifest["allowed_status_categories"]
            or data["status_category"].casefold() == "done"
            or data["mapped_status"]
            != jira_manifest["status_mapping"].get(data["status"])
            or data["mapped_status"] == "completed"
        ):
            _evidence_invalid(
                f"Jira 回读事件 {event['event_id']} 的账户或状态映射不一致"
            )
        if data["formal_takeover_verified"] != (
            data["takeover_comment_id"] is not None
        ):
            _evidence_invalid(
                f"Jira 回读事件 {event['event_id']} 的接管评论结论不一致"
            )
        if (
            data["issue_content_sha256"]
            != task_binding["issue_content_sha256"]
            or data["approved_plan_sha256"]
            != task_binding["approved_plan_sha256"]
        ):
            _evidence_invalid(
                f"Jira 回读事件 {event['event_id']} 未绑定任务内容或批准计划摘要"
            )
    expected_plan_prefix = (
        f".agentic-ops/tasks/{issue['key']}/runs/"
        f"{manifest['agent']['agentic_run_id']}/jira-plans/"
    )
    for event in by_action["jira_write_readback"]:
        data = event["action_data"]
        if (
            data["issue_key"] != issue["key"]
            or data["agentic_run_id"] != manifest["agent"]["agentic_run_id"]
            or not data["plan_file"].startswith(expected_plan_prefix)
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json",
                data["plan_file"][len(expected_plan_prefix) :],
            )
            is None
        ):
            _evidence_invalid(
                f"Jira 写后回读事件 {event['event_id']} 未绑定当前 issue/run 受管计划"
            )
        if data["attempt_file"] is not None and (
            not data["attempt_file"].startswith(expected_plan_prefix)
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json",
                data["attempt_file"][len(expected_plan_prefix) :],
            )
            is None
        ):
            _evidence_invalid(
                f"Jira 写后回读事件 {event['event_id']} 的 create 尝试文件未绑定当前 issue/run"
            )
        if data["operation"] == "jira_worklog" and re.search(
            r"[\u3400-\u9fff]", data["title"]
        ) is None:
            _evidence_invalid(
                f"Jira Worklog 回读事件 {event['event_id']} 的标题不是中文"
            )
        if data["operation"] == "jira_worklog" and sum(
            item["seconds"] for item in data["included_work"]
        ) != data["time_spent_seconds"]:
            _evidence_invalid(
                f"Jira Worklog 回读事件 {event['event_id']} 的耗时组成与总耗时不一致"
            )
    allowed_branches = {
        repository["base_branch"],
        repository["task_branch"],
        repository["target_branch"],
        *repository["protected_branches"],
    }
    for event in by_action["remote_branch_readback"]:
        data = event["action_data"]
        if (
            data["repository_slug"] != repository["slug"]
            or data["remote_name"] != repository["remote_name"]
            or data["branch"] not in allowed_branches
        ):
            _evidence_invalid(
                f"远端分支回读事件 {event['event_id']} 越出 manifest 仓库或分支边界"
            )
        if (
            _repository_slug_from_origin(data["origin_url"]) != repository["slug"]
            or data["url"]
            != f"https://github.com/{repository['slug']}/tree/{data['branch']}"
            or data["sha"] != data["head_sha"]
            or data["base_sha"] == data["head_sha"]
        ):
            _evidence_invalid(
                f"远端分支回读事件 {event['event_id']} 的 origin 或 SHA 绑定不一致"
            )
        baseline_event = event_index.get(data["baseline_event_id"])
        if (
            baseline_event is None
            or baseline_event["action"] != "prohibition_baseline"
            or baseline_event["status"] != "completed"
            or baseline_event["action_data"]["local_head_sha"]
            != data["baseline_local_head_sha"]
            or baseline_event["action_data"]["task_branch_remote_sha"]
            != data["baseline_remote_sha"]
            or sequence_index[baseline_event["event_id"]]
            >= sequence_index[event["event_id"]]
        ):
            _evidence_invalid(
                f"远端分支回读事件 {event['event_id']} 没有绑定更早的同运行基线"
            )
        attributed = set(data["attributed_actions"])
        linked_actions = {
            candidate["action_data"]["action"]
            for candidate in by_action["external_action"]
            if candidate["action_data"]["status"] == "applied"
            and candidate["action_data"]["readback_event_id"] == event["event_id"]
            and candidate["action_data"]["action"]
            in {"git_commit", "git_push_task_branch"}
        }
        if attributed != linked_actions:
            _evidence_invalid(
                f"远端分支回读事件 {event['event_id']} 的归因动作与外部动作引用不一致"
            )
        if "git_commit" in attributed:
            if (
                data["baseline_local_head_sha"] == data["head_sha"]
                or data["baseline_local_is_ancestor"] is not True
            ):
                _evidence_invalid(
                    f"远端分支回读事件 {event['event_id']} 不能证明本运行增量提交"
                )
            expected_verification_ids: list[str] = []
            for verification in manifest["verification"]:
                attempts = [
                    candidate
                    for candidate in by_action["verification"]
                    if candidate["action_data"]["id"] == verification["id"]
                ]
                if not attempts:
                    _evidence_invalid(
                        f"Git 提交归因缺少验证：{verification['id']}"
                    )
                latest = attempts[-1]
                if (
                    latest["action_data"]["status"] != "passed"
                    or latest["action_data"]["head_sha"] != data["head_sha"]
                ):
                    _evidence_invalid(
                        f"Git 提交归因的最新验证未绑定最终 HEAD：{verification['id']}"
                    )
                expected_verification_ids.append(latest["event_id"])
            if data["verification_event_ids"] != expected_verification_ids:
                _evidence_invalid(
                    f"远端分支回读事件 {event['event_id']} 未绑定全部最终 HEAD 验证"
                )
            if any(
                sequence_index[baseline_event["event_id"]]
                >= sequence_index[verification_id]
                or sequence_index[verification_id]
                >= sequence_index[event["event_id"]]
                for verification_id in expected_verification_ids
            ):
                _evidence_invalid(
                    "Git 提交归因区间必须由写前基线、最终 HEAD 验证和后置回读闭合"
                )
        if "git_push_task_branch" in attributed:
            if data["baseline_remote_sha"] == data["head_sha"]:
                _evidence_invalid(
                    f"远端分支回读事件 {event['event_id']} 未观察到本运行远端变化"
                )
            if (
                data["baseline_remote_sha"] is not None
                and data["baseline_remote_is_ancestor"] is not True
            ):
                _evidence_invalid(
                    f"远端分支回读事件 {event['event_id']} 的任务分支变化不是快进"
                )
        if any(
            data[field] != execution_identity[field]
            for field in (
                "git_author_name",
                "git_author_email",
                "git_committer_name",
                "git_committer_email",
            )
        ) or data["approved_plan_sha256"] != task_binding["approved_plan_sha256"]:
            _evidence_invalid(
                f"远端分支回读事件 {event['event_id']} 未绑定提交身份或批准计划"
            )
        outside = [
            path
            for path in data["changed_paths"]
            if not any(
                fnmatch.fnmatchcase(path, pattern)
                for pattern in manifest["scope"]["included"]
            )
            or any(
                fnmatch.fnmatchcase(path, pattern)
                for pattern in manifest["scope"]["excluded"]
            )
        ]
        if outside:
            _evidence_invalid(
                f"远端分支回读事件 {event['event_id']} 越出 manifest scope"
            )
    for event in by_action["pr_readback"]:
        data = event["action_data"]
        if (
            data["repository_slug"] != endpoint["repository_slug"]
            or data["head_branch"] != repository["task_branch"]
            or data["base_branch"] != endpoint["target_branch"]
        ):
            _evidence_invalid(
                f"PR 回读事件 {event['event_id']} 与 manifest 仓库或分支不一致"
            )
        expected_path = f"/{endpoint['repository_slug']}/pull/{data['number']}"
        if urlsplit(data["url"]).path.rstrip("/") != expected_path:
            _evidence_invalid(
                f"PR 回读事件 {event['event_id']} 的 URL 与仓库/number 不一致"
            )
        if (
            data["github_actor_login"]
            != execution_identity["github_actor_login"]
            or data["approved_plan_sha256"]
            != task_binding["approved_plan_sha256"]
        ):
            _evidence_invalid(
                f"PR 回读事件 {event['event_id']} 未绑定 GitHub actor 或批准计划"
            )
        baseline_event = event_index.get(data["baseline_event_id"])
        git_event = event_index.get(data["git_readback_event_id"])
        if (
            baseline_event is None
            or baseline_event["action"] != "prohibition_baseline"
            or git_event is None
            or git_event["action"] != "remote_branch_readback"
            or git_event["action_data"]["head_sha"] != data["head_sha"]
            or sequence_index[baseline_event["event_id"]]
            >= sequence_index[git_event["event_id"]]
            or sequence_index[git_event["event_id"]]
            >= sequence_index[event["event_id"]]
        ):
            _evidence_invalid(
                f"PR 回读事件 {event['event_id']} 未绑定写前基线和最终 Git 回读"
            )
        attributed = set(data["attributed_actions"])
        linked_actions = {
            candidate["action_data"]["action"]
            for candidate in by_action["external_action"]
            if candidate["action_data"]["status"] == "applied"
            and candidate["action_data"]["readback_event_id"] == event["event_id"]
            and candidate["action_data"]["action"]
            == "github_pr_create_or_update"
        }
        if attributed != linked_actions:
            _evidence_invalid(
                f"PR 回读事件 {event['event_id']} 的归因动作与外部动作引用不一致"
            )
        if data["creation_proof"] != bool(attributed):
            _evidence_invalid(
                f"PR 回读事件 {event['event_id']} 的 creation_proof 与归因动作不一致"
            )
        if attributed and baseline_event["action_data"]["task_open_pr"] is not None:
            _evidence_invalid(
                "写入前已有 open PR，当前协议不能证明本运行更新动作；必须 fail closed"
            )


def _external_action_target(
    action: str, readback: Mapping[str, Any]
) -> str | None:
    data = readback["action_data"]
    if action == "jira_read" and readback["action"] == "jira_readback":
        return f"jira:{data['issue_key']}"
    if action == "jira_read" and readback["action"] == "prohibition_baseline":
        return f"jira:{data['issue_key']}:prohibition-baseline"
    if (
        action in {"jira_comment", "jira_worklog"}
        and readback["action"] == "jira_write_readback"
        and data["operation"] == action
    ):
        return f"jira:{data['issue_key']}:{action}:{data['external_id']}"
    if (
        action in {"git_commit", "git_remote_read", "git_push_task_branch"}
        and readback["action"] == "remote_branch_readback"
    ):
        return f"git:{data['repository_slug']}:{data['branch']}@{data['head_sha']}"
    if action == "git_remote_read" and readback["action"] == "prohibition_baseline":
        return f"git:{data['repository_slug']}:prohibition-baseline"
    if (
        action in {"github_pr_create_or_update", "github_pr_read"}
        and readback["action"] == "pr_readback"
    ):
        return str(data["url"])
    if action == "github_pr_read" and readback["action"] == "prohibition_baseline":
        return f"github:{data['repository_slug']}:prohibition-baseline"
    return None


def _validate_verification_bindings(
    manifest: Mapping[str, Any],
    facts: Mapping[str, Any],
    result_status: str,
    failure_events: Sequence[Mapping[str, Any]],
    retry_events: Sequence[Mapping[str, Any]],
) -> None:
    expected = {
        item["id"]: verification_digest(item) for item in manifest["verification"]
    }
    observed: dict[str, list[Mapping[str, Any]]] = {}
    final_head = (
        facts["remote_branch_readback"]["head_sha"]
        if facts["remote_branch_readback"] is not None
        else None
    )
    for item in facts["verifications"]:
        verification_id = item["id"]
        if verification_id not in expected:
            _evidence_invalid(f"验证 {verification_id} 不在 manifest 中")
        if item["command_sha256"] != expected[verification_id]:
            _evidence_invalid(f"验证 {verification_id} 的命令摘要与 manifest 不一致")
        if item["status"] == "passed" and item["exit_code"] != 0:
            _evidence_invalid(f"验证 {verification_id} 声称 passed 但 exit_code 非 0")
        if item["status"] != "passed" and item["exit_code"] == 0:
            _evidence_invalid(f"验证 {verification_id} 状态与 exit_code 不一致")
        observed.setdefault(verification_id, []).append(item)
    if result_status == "ready_for_pr_review":
        if set(observed) != set(expected):
            _evidence_invalid("验证事实未完整覆盖 manifest 中的全部命令")
        failed = [
            key for key, attempts in observed.items() if attempts[-1]["status"] != "passed"
        ]
        if failed:
            _evidence_invalid(f"ready_for_pr_review 的最新验证仍未通过：{failed}")
        stale_heads = [
            key
            for key, attempts in observed.items()
            if attempts[-1]["head_sha"] != final_head
        ]
        if stale_heads:
            _evidence_invalid(
                f"最新通过验证未绑定最终 Git/PR head_sha：{stale_heads}"
            )
        superseded_failures = sum(
            item["status"] != "passed"
            for attempts in observed.values()
            for item in attempts[:-1]
        )
        successful_retries = sum(
            event["action_data"]["outcome"] == "succeeded"
            for event in retry_events
        )
        if superseded_failures and (
            len(failure_events) < superseded_failures
            or successful_retries < superseded_failures
        ):
            _evidence_invalid(
                "验证失败后重测虽已通过，但缺少逐次 failure/retry(succeeded) 审计"
            )


def _validate_result_outcome(
    manifest: Mapping[str, Any],
    result: Mapping[str, Any],
    facts: Mapping[str, Any],
    by_action: Mapping[str, list[dict[str, Any]]],
    events: Sequence[Mapping[str, Any]],
    observed_prohibitions: Sequence[str],
) -> None:
    status = result["status"]
    if status == "ready_for_pr_review":
        jira = facts["jira_readback"]
        branch = facts["remote_branch_readback"]
        pr = facts["pr_readback"]
        if jira is None or branch is None or pr is None:
            _evidence_invalid("ready_for_pr_review 缺少 Jira、远端分支或 PR 回读")
        if observed_prohibitions:
            _evidence_invalid("ready_for_pr_review 不能包含已观察到的禁止动作")
        if branch["protected"] or not branch["worktree_clean"]:
            _evidence_invalid("ready_for_pr_review 的任务分支必须未受保护且工作树干净")
        if pr["merged"] or pr["draft"] or pr["status"] != "open":
            _evidence_invalid("ready_for_pr_review 的 PR 必须 open、非 draft、未合并")
        jira_writes = by_action["jira_write_readback"]
        if (
            len(jira_writes) != 2
            or {event["action_data"]["operation"] for event in jira_writes}
            != {"jira_comment", "jira_worklog"}
            or any(
                event["action_data"]["created"] is not True
                or event["action_data"]["write_precondition"] != "absent"
                or event["action_data"]["attempt_file"] is None
                or event["action_data"]["write_attempt_id"] is None
                or event["action_data"]["write_attempt_started_at"] is None
                for event in jira_writes
            )
        ):
            _evidence_invalid(
                "ready_for_pr_review 必须由 Runtime 证明本运行各创建一条 Jira Comment 与 Worklog"
            )
        ci_policy = manifest["pr_endpoint"]["ci_policy"]
        if (
            (ci_policy == "require_passed" and pr["ci_status"] != "passed")
            or (ci_policy == "allow_pending" and pr["ci_status"] == "failed")
        ):
            _evidence_invalid("PR CI 事实不满足 manifest ci_policy")
        if manifest.get("process_id", "development_change_v1") == "development_change_v2":
            ci_completion = facts.get("ci_completion")
            if not isinstance(ci_completion, dict):
                _evidence_invalid("development_change_v2 缺少 CI 完成证据")
            if ci_completion["head_sha"] != pr["head_sha"]:
                _evidence_invalid("CI 完成证据未绑定最终 PR Head")
            expected_checks = manifest["pr_endpoint"]["ci"]["required_checks"]
            observed_checks = [item["name"] for item in ci_completion["required_checks"]]
            if observed_checks != expected_checks:
                _evidence_invalid("CI 完成证据未按 Profile 顺序完整覆盖必需检查")
            maximum = manifest["pr_endpoint"]["ci"]["max_remediation_attempts"]
            if (
                ci_completion["remediation_attempts_used"]
                + ci_completion["remediation_attempts_remaining"]
                != maximum
            ):
                _evidence_invalid("CI 完成证据的修复预算与 Profile 不一致")
        if not jira["formal_takeover_verified"]:
            jira_events = by_action["jira_readback"]
            latest_jira_event_id = jira_events[-1]["event_id"] if jira_events else ""
            automation_gaps = [
                event
                for event in by_action["quality_finding"]
                if event["action_data"]["category"] == "automation_gap"
                and event["action_data"]["evidence_reference"]
                == latest_jira_event_id
            ]
            retrospective = by_action["retrospective"][0]["action_data"]
            event_order = {
                event["event_id"]: index for index, event in enumerate(events)
            }
            gap_order_valid = all(
                event_order[latest_jira_event_id]
                < event_order[event["event_id"]]
                < event_order[by_action["retrospective"][0]["event_id"]]
                for event in automation_gaps
            )
            if (
                not automation_gaps
                or not gap_order_valid
                or not retrospective["residual_risks"]
            ):
                _evidence_invalid(
                    "受管接管 Comment 未正式核对时，必须记录绑定 Jira probe 的 automation_gap 和残留风险"
                )
        applied = {
            item["action"]
            for item in facts["external_actions"]
            if item["status"] == "applied"
        }
        required = {
            "jira_read",
            "jira_comment",
            "jira_worklog",
            "git_commit",
            "git_push_task_branch",
            "github_pr_create_or_update",
        }
        if not required <= applied:
            _evidence_invalid(
                "缺少真实 Jira 读取、Comment、Worklog、Git 提交、任务分支推送或 PR 创建/更新动作"
            )
        return
    if result["delivery_passed"]:
        _evidence_invalid("blocked/failed 结果不得声明 delivery_passed=true")
    if status == "failed" and not by_action["failure"]:
        _evidence_invalid("failed 结果缺少完成的 failure 事件")
    if status == "blocked":
        blocked_events = [event for event in events if event["status"] == "blocked"]
        # by_action only indexes completed events; inspect projection-independent failures too.
        if not by_action["failure"] and not blocked_events:
            _evidence_invalid("blocked 结果缺少 blocked 步骤或完成的 failure 事件")


def _require_protocol(value: Mapping[str, Any], label: str) -> None:
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("protocol") != PROTOCOL
    ):
        _invalid(label, "schema_version 或 protocol 不受支持")


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        _invalid(
            label,
            f"字段不闭合；missing={sorted(expected - actual)}, extra={sorted(actual - expected)}",
        )


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _invalid(label, "必须是 object")
    return value


def _require_string(
    value: object, label: str, *, maximum: int = 4096
) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(label, "必须是非空字符串")
    if len(value) > maximum:
        _invalid(label, f"长度不能超过 {maximum}")
    return value


def _require_id(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=128)
    if not ID_PATTERN.fullmatch(text):
        _invalid(label, "必须是安全稳定标识")
    return text


def _require_issue_key(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=128)
    if not ISSUE_PATTERN.fullmatch(text):
        _invalid(label, "必须是形如 TAP-12289 的大写 Jira key")
    return text


def _require_digest(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=64)
    if not DIGEST_PATTERN.fullmatch(text):
        _invalid(label, "必须是 64 位小写 SHA-256")
    return text


def _require_string_list(
    value: object, label: str, *, nonempty: bool = False
) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        _invalid(label, "必须是字符串数组")
    return [
        _require_string(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    ]


def _require_unique_string_list(
    value: object, label: str, *, nonempty: bool = False
) -> list[str]:
    result = _require_string_list(value, label, nonempty=nonempty)
    if len(set(result)) != len(result):
        _invalid(label, "不能包含重复项")
    return result


def _require_snapshot_records(
    value: object,
    label: str,
    key_field: str,
    value_field: str,
    *,
    nullable_value: bool = False,
    timestamp_value: bool = False,
) -> None:
    if not isinstance(value, list):
        _invalid(label, "必须是数组")
    keys: set[str] = set()
    for index, raw in enumerate(value):
        item = _require_mapping(raw, f"{label}[{index}]")
        _require_exact_keys(item, {key_field, value_field}, f"{label}[{index}]")
        key = _require_string(item[key_field], f"{label}[{index}].{key_field}")
        if key in keys:
            _invalid(label, f"{key_field} 不能重复")
        keys.add(key)
        supplied = item[value_field]
        if supplied is None and nullable_value:
            continue
        if timestamp_value:
            _require_timestamp(supplied, f"{label}[{index}].{value_field}")
        else:
            text = _require_string(
                supplied, f"{label}[{index}].{value_field}", maximum=64
            )
            if not GIT_SHA_PATTERN.fullmatch(text):
                _invalid(f"{label}[{index}].{value_field}", "必须是 Git SHA")


def _require_timestamp(value: object, label: str) -> str:
    text = _require_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        _invalid(label, f"必须是 ISO-8601 date-time：{error}")
    if parsed.tzinfo is None:
        _invalid(label, "必须包含时区")
    return text


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _repository_slug_from_origin(value: str) -> str:
    normalized = value.strip().removesuffix(".git")
    if normalized.startswith("git@github.com:"):
        return normalized.split(":", 1)[1]
    parsed = urlsplit(normalized)
    safe_identity = (
        (parsed.scheme == "https" and parsed.username is None)
        or (parsed.scheme == "ssh" and parsed.username == "git")
        or (parsed.scheme == "git" and parsed.username is None)
    )
    if parsed.hostname == "github.com" and safe_identity and not parsed.password:
        return parsed.path.strip("/")
    return ""


def _require_absolute_path(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=4096)
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts or "\x00" in text:
        _invalid(label, "必须是无跳转、无 NUL 的绝对路径")
    return text


def _require_relative_path(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=1024)
    path = Path(text)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\x00" in text
        or re.match(r"^[A-Za-z]:[\\/]", text)
    ):
        _invalid(label, "必须是工作仓库内无跳转的相对路径")
    return text


def _validate_verification_command(
    command: object,
    working_directory: object,
    *,
    label: str,
) -> list[str]:
    """Mirror the developer fail-closed test/static-check argv policy."""

    argv = _require_string_list(command, f"{label}.command", nonempty=True)
    if len(argv) > 128:
        _verification_forbidden(label, "argv 参数过多")
    workdir = _require_relative_path(working_directory, f"{label}.working_directory")
    _reject_verification_path_reference(workdir, f"{label}.working_directory")
    raw_executable = argv[0].replace("\\", "/")
    normalized_executable = raw_executable.casefold()
    executable_name = normalized_executable.rsplit("/", 1)[-1]
    if executable_name.endswith(".exe"):
        executable_name = executable_name[:-4]
    if (
        executable_name in VERIFICATION_FORBIDDEN_EXECUTABLES
        or executable_name.startswith(("ao-work", "ao-maint"))
    ):
        _verification_forbidden(label, f"禁止执行高副作用或网络命令 {argv[0]}")

    python_command = re.fullmatch(r"python(?:3(?:\.[0-9]+)?)?", executable_name)
    wrapper = normalized_executable in VERIFICATION_PROJECT_WRAPPERS
    if "/" in normalized_executable and not wrapper:
        _verification_forbidden(
            label,
            "可执行文件必须使用隔离 PATH 中的固定名称；只接受 ./mvnw 与 ./gradlew 项目包装器",
        )
    if (
        python_command is None
        and executable_name not in VERIFICATION_DIRECT_COMMANDS
        and not wrapper
    ):
        _verification_forbidden(label, f"命令 {argv[0]} 不在测试/静态检查白名单")

    for index, argument in enumerate(argv[1:], start=1):
        lowered = argument.casefold()
        if (
            lowered in VERIFICATION_FORBIDDEN_ARGUMENTS
            or (
                lowered.startswith("-")
                and any(
                    term in lowered
                    for term in ("deploy", "fix", "install", "publish", "snapshot", "update", "write")
                )
            )
            or any(
                lowered.startswith(prefix)
                for prefix in (
                    "--command=", "--eval=", "--fix=", "--init-hook=",
                    "--load-plugins=", "--publish=", "--require=", "--write=",
                )
            )
        ):
            _verification_forbidden(label, f"参数 {argument} 可进入解释器、交互或修改模式")
        _reject_verification_path_reference(argument, f"{label}.command[{index}]")

    if python_command is not None:
        if len(argv) < 3 or argv[1] != "-m" or argv[2] not in VERIFICATION_PYTHON_MODULES:
            _verification_forbidden(
                label,
                "Python 只允许以 -m 调用固定测试或静态检查模块；禁止 -c、脚本和标准输入",
            )
        _validate_verification_tool(argv[2], argv[3:], label)
    else:
        tool = "mvn" if normalized_executable == "./mvnw" else (
            "gradle" if normalized_executable == "./gradlew" else executable_name
        )
        _validate_verification_tool(tool, argv[1:], label)
    return argv


def _validate_verification_tool(tool: str, arguments: list[str], label: str) -> None:
    lowered = [argument.casefold() for argument in arguments]
    positional = [argument for argument in lowered if not argument.startswith("-")]
    if tool == "ruff" and positional[:1] == ["format"] and "--check" not in lowered:
        _verification_forbidden(label, "ruff format 必须显式使用 --check")
    if tool == "black" and "--check" not in lowered:
        _verification_forbidden(label, "black 必须显式使用 --check")
    if tool == "isort" and not {"--check", "--check-only"}.intersection(lowered):
        _verification_forbidden(label, "isort 必须显式使用 --check-only")
    if tool == "prettier" and "--check" not in lowered:
        _verification_forbidden(label, "prettier 必须显式使用 --check")
    if tool == "tsc" and "--noemit" not in lowered:
        _verification_forbidden(label, "tsc 必须显式使用 --noEmit")
    if tool == "node":
        if "--test" not in lowered or {
            "-e", "-i", "-p", "-r", "--eval", "--interactive", "--print", "--require",
        }.intersection(lowered):
            _verification_forbidden(label, "node 只允许非交互 --test 测试运行器")
    if tool == "go" and positional[:1] not in (["test"], ["vet"]):
        _verification_forbidden(label, "go 只允许 test 或 vet")
    if tool == "cargo":
        if positional[:1] not in (["test"], ["check"], ["clippy"], ["fmt"]):
            _verification_forbidden(label, "cargo 只允许 test、check、clippy 或 fmt")
        if positional[:1] == ["fmt"]:
            if "--check" not in lowered:
                _verification_forbidden(label, "cargo fmt 必须显式使用 --check")
        elif not {"--offline", "--locked"}.issubset(lowered):
            _verification_forbidden(label, "cargo 验证必须显式使用 --offline --locked")
    if tool in {"npm", "pnpm", "yarn"}:
        allowed_scripts = {"check", "lint", "test", "type-check", "typecheck"}
        if tool == "npm" and positional[:1] == ["run"]:
            script = positional[1] if len(positional) > 1 else ""
        else:
            script = positional[0] if positional else ""
        if script not in allowed_scripts or "--offline" not in lowered:
            _verification_forbidden(label, f"{tool} 只允许离线执行 test/check/lint/typecheck 脚本")
    if tool == "mvn":
        goals = [argument for argument in positional if ":" in argument or argument.isalpha()]
        if not goals or not set(goals) <= {"checkstyle:check", "spotbugs:check", "test", "verify"}:
            _verification_forbidden(label, "Maven 只允许 test/verify 或固定静态检查 goal")
        if not {"-b", "--batch-mode"}.intersection(lowered) or not {
            "-o", "--offline",
        }.intersection(lowered):
            _verification_forbidden(label, "Maven 必须显式使用 batch 与 offline 模式")
    if tool == "gradle":
        if not positional or not set(positional) <= {"check", "lint", "test"}:
            _verification_forbidden(label, "Gradle 只允许 test/check/lint task")
        if not {"--offline", "--no-daemon"}.issubset(lowered):
            _verification_forbidden(label, "Gradle 必须显式使用 --offline --no-daemon")
    if tool == "dotnet" and (
        positional[:1] != ["test"] or "--no-restore" not in lowered
    ):
        _verification_forbidden(label, "dotnet 只允许 test --no-restore")


def _reject_verification_path_reference(value: str, label: str) -> None:
    candidate = value.split("=", 1)[1] if value.startswith("-") and "=" in value else value
    normalized = candidate.replace("\\", "/")
    lowered = normalized.casefold()
    if (
        lowered.startswith(("/", "~/", "http://", "https://", "ssh://", "git@"))
        or re.match(r"^[a-z]:/", lowered)
    ):
        _verification_forbidden(label, "不允许绝对路径、用户目录或网络地址")
    parts = tuple(part for part in lowered.split("/") if part not in {"", "."})
    if ".." in parts:
        _verification_forbidden(label, "路径不能越出业务仓库")
    if any(part in {".agentic-ops", ".git"} for part in parts):
        _verification_forbidden(label, "验证不能读取或修改 AgenticOps/Git 受管状态")
    if any(part == ".env" or part.startswith(".env.") for part in parts):
        _verification_forbidden(label, "验证不能指向环境凭证文件")
    if any(
        parts[index : index + 2] in {("developer", ".local"), ("maintainer", ".local")}
        for index in range(max(0, len(parts) - 1))
    ):
        _verification_forbidden(label, "验证不能指向 Runtime 受管状态")


def _verification_forbidden(label: str, detail: str) -> None:
    raise _blocked(
        "integration_verification_command_forbidden",
        f"协议字段 {label}.command 不是安全验证命令：{detail}",
        "请只声明非交互、离线优先的测试或静态检查 argv；新增入口需先评审双方 Runtime 白名单",
    )


def _require_branch(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=255)
    if (
        text.startswith(("/", "-"))
        or text.endswith(("/", "."))
        or ".." in text
        or "//" in text
        or "@{" in text
        or any(character.isspace() for character in text)
        or any(character in text for character in "~^:?*[]\\")
    ):
        _invalid(label, "不是安全的 Git 分支名")
    return text


def _require_repository_slug(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=256)
    if not REPOSITORY_SLUG_PATTERN.fullmatch(text):
        _invalid(label, "必须是 owner/repository")
    owner, repository = text.split("/", 1)
    if owner.startswith((".", "-")) or repository.startswith((".", "-")):
        _invalid(label, "owner 和 repository 必须以字母或数字开头")
    return text


def _require_reference(value: object, label: str) -> str:
    text = _require_string(value, label, maximum=2048)
    if any(character.isspace() for character in text) or "\x00" in text:
        _invalid(label, "引用不能包含空白或 NUL")
    if text.startswith(("http://", "https://")):
        _require_url(text, label)
    return text


def _require_url(
    value: object, label: str, hosts: set[str] | None = None
) -> str:
    text = _require_string(value, label, maximum=2048)
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        _invalid(label, "必须是无凭证、query、fragment 的 HTTPS URL")
    if hosts is not None and parsed.hostname not in hosts:
        _invalid(label, "URL host 不受协议允许")
    return text


def _reject_required_placeholders(value: object) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _reject_required_placeholders(child)
    elif isinstance(value, list):
        for child in value:
            _reject_required_placeholders(child)
    elif value == "REQUIRED":
        _invalid("manifest", "仍包含 REQUIRED 占位符")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON object 包含重复字段：{key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数值：{value}")


def _reject_sensitive_content(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(
                term in normalized
                for term in ("password", "secret", "token", "private_key", "credential")
            ):
                raise _blocked(
                    "integration_sensitive_content_detected",
                    f"协议输入包含敏感字段：{key}",
                    "请移除凭证和原始敏感内容，只保留脱敏引用",
                )
            _reject_sensitive_content(child)
    elif isinstance(value, list):
        for child in value:
            _reject_sensitive_content(child)
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS
    ):
        raise _blocked(
            "integration_sensitive_content_detected",
            "协议输入疑似包含凭证或私钥",
            "请移除凭证和原始敏感内容，只保留脱敏引用",
        )


def _evidence_invalid(detail: str) -> None:
    raise _blocked(
        "integration_result_evidence_invalid",
        f"结果包证据不完整或不一致：{detail}",
        "请从 developer 原始审计补齐真实回读、绑定和复盘后重新生成结果包",
    )


def _invalid(label: str, detail: str) -> None:
    raise _blocked(
        "integration_protocol_schema_invalid",
        f"协议字段 {label} 无效：{detail}",
        "请按 shared/integration 中的版本化 JSON Schema 修复输入",
    )


def _blocked(
    code: str, message: str, action: str, **details: Any
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action=action,
        details=details,
    )
