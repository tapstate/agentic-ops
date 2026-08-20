from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Mapping
from urllib.parse import urlsplit

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.managed_io import read_managed_text

SCHEMA_VERSION: Final = 1
PROTOCOL: Final = "task_to_pr_review"
ID_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,127}$")
AGENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
PROFILE_ID_PATTERN = re.compile(r"^[0-9a-z][0-9a-z_-]*$")
ISSUE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
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
    }
)
PROHIBITED_ACTIONS: Final = (
    "merge_pr",
    "jira_done",
    "release",
    "create_tag",
    "push_protected_branch",
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
ACTORS: Final = frozenset({"skill", "runtime", "project_tool", "ai", "human"})
EVIDENCE_ORIGINS: Final = frozenset({"imported", "runtime_probe"})
IMPORTED_ACTIONS: Final = frozenset(
    {
        "step",
        "external_action",
        "human_intervention",
        "failure",
        "retry",
        "waiting",
        "quality_finding",
        "retrospective",
    }
)
STATUSES: Final = frozenset({"started", "completed", "blocked"})
QUALITY_CATEGORIES: Final = (
    "automation_gap",
    "manual_friction",
    "output_quality",
    "unreasonable_process",
)

# task-run 验证不是通用命令执行器。这里保留一份很小的、可审计的测试与
# 静态检查入口集合；项目若需要其它命令，必须先把它作为新的确定性能力
# 评审，而不是让 manifest 临时扩大权限。
VERIFICATION_PYTHON_MODULES: Final = frozenset(
    {
        "bandit",
        "black",
        "flake8",
        "isort",
        "mypy",
        "pylint",
        "pyright",
        "pytest",
        "ruff",
        "unittest",
    }
)
VERIFICATION_DIRECT_COMMANDS: Final = frozenset(
    {
        "bandit",
        "black",
        "cargo",
        "dotnet",
        "eslint",
        "flake8",
        "go",
        "gradle",
        "isort",
        "mvn",
        "mypy",
        "node",
        "npm",
        "pnpm",
        "prettier",
        "py.test",
        "pylint",
        "pyright",
        "pytest",
        "ruff",
        "tsc",
        "yarn",
    }
)
VERIFICATION_PROJECT_WRAPPERS: Final = frozenset({"./gradlew", "./mvnw"})
VERIFICATION_FORBIDDEN_EXECUTABLES: Final = frozenset(
    {
        "ao-maint",
        "ao-work",
        "ansible",
        "apt",
        "apt-get",
        "bash",
        "brew",
        "cmd",
        "composer",
        "curl",
        "dash",
        "dnf",
        "docker",
        "fish",
        "ftp",
        "gem",
        "gh",
        "git",
        "helm",
        "http",
        "httpie",
        "ksh",
        "kubectl",
        "nc",
        "ncat",
        "netcat",
        "pip",
        "pip3",
        "podman",
        "powershell",
        "pwsh",
        "rsync",
        "scp",
        "sftp",
        "sh",
        "socat",
        "ssh",
        "telnet",
        "terraform",
        "uv",
        "wget",
        "yum",
        "zsh",
    }
)
VERIFICATION_FORBIDDEN_ARGUMENTS: Final = frozenset(
    {
        "-c",
        "-w",
        "--command",
        "--deploy",
        "--eval",
        "--fix",
        "--global",
        "--install",
        "--interactive",
        "--package",
        "--print",
        "--publish",
        "--require",
        "--update-snapshots",
        "--watch",
        "--write",
    }
)


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


def verification_digest(item: Mapping[str, Any]) -> str:
    return digest(
        {
            "command": item.get("command"),
            "working_directory": item.get("working_directory"),
            "timeout_seconds": item.get("timeout_seconds"),
        }
    )


def event_envelope(
    event: Mapping[str, Any], sequence: int, previous_event_sha256: str | None
) -> dict[str, Any]:
    base = {
        "sequence": sequence,
        "previous_event_sha256": previous_event_sha256,
        "event": dict(event),
    }
    return {**base, "event_sha256": digest(base)}


def result_digest(payload: Mapping[str, Any]) -> str:
    candidate = copy.deepcopy(dict(payload))
    candidate["result_sha256"] = ""
    return digest(candidate)


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        content = read_managed_text(
            path,
            label=f"task-run {label}",
            max_bytes=1_048_576,
        )
        assert content is not None
    except OSError as error:
        raise blocked(
            "protocol_json_invalid",
            f"{label} 无法读取：{error}",
            f"请修复 {label} 后重新确认",
        ) from error
    try:
        payload = parse_json_text(content)
    except (json.JSONDecodeError, ValueError) as error:
        raise blocked(
            "protocol_json_invalid",
            f"{label} 不是可读取的 JSON：{error}",
            f"请修复 {label} 后重新确认",
        ) from error
    if not isinstance(payload, dict):
        raise blocked(
            "protocol_json_invalid",
            f"{label} 必须是 JSON 对象",
            f"请修复 {label} 后重新确认",
        )
    reject_sensitive_content(payload)
    return payload


def parse_json_text(content: str) -> object:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        content,
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )


def validate_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    require_exact_keys(
        value,
        {
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
        },
        "manifest",
    )
    require_protocol(value, "manifest")
    workspace = require_mapping(value["workspace"], "workspace")
    require_exact_keys(workspace, {"root"}, "workspace")
    require_absolute_path(workspace["root"], "workspace.root")

    issue = require_mapping(value["issue"], "issue")
    require_exact_keys(issue, {"key", "id", "project_key"}, "issue")
    key = require_string(issue["key"], "issue.key")
    if not ISSUE_PATTERN.fullmatch(key):
        invalid("issue.key", "必须是大写 Jira issue key")
    require_id(issue["id"], "issue.id")
    project_key = require_string(issue["project_key"], "issue.project_key")
    if key.split("-", 1)[0] != project_key:
        invalid("issue.project_key", "必须与 issue.key 前缀一致")

    agent = require_mapping(value["agent"], "agent")
    require_exact_keys(
        agent, {"agent_id", "project_profile", "agentic_run_id"}, "agent"
    )
    agent_id = require_string(agent["agent_id"], "agent.agent_id")
    if len(agent_id) > 128 or not AGENT_ID_PATTERN.fullmatch(agent_id):
        invalid("agent.agent_id", "只能包含 [0-9A-Za-z_-]，且最长 128 字符")
    profile_id = require_string(agent["project_profile"], "agent.project_profile")
    if len(profile_id) > 128 or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        invalid(
            "agent.project_profile",
            "必须是最长 128 字符的小写 Project Profile 标识",
        )
    require_id(agent["agentic_run_id"], "agent.agentic_run_id")

    task_binding = require_mapping(value["task_binding"], "task_binding")
    require_exact_keys(
        task_binding,
        {"issue_content_sha256", "approved_plan_file", "approved_plan_sha256"},
        "task_binding",
    )
    for field in ("issue_content_sha256", "approved_plan_sha256"):
        if not isinstance(task_binding[field], str) or not DIGEST_PATTERN.fullmatch(
            task_binding[field]
        ):
            invalid(f"task_binding.{field}", "必须是 64 位小写 SHA-256")
    plan_file = require_relative_path(
        task_binding["approved_plan_file"], "task_binding.approved_plan_file"
    )
    plan_parts = Path(plan_file).parts
    if (
        len(plan_parts) < 2
        or plan_parts[0] != "inputs"
        or any(part.startswith(".") for part in plan_parts)
    ):
        invalid(
            "task_binding.approved_plan_file",
            "必须位于工作空间 inputs/ 下且不得包含隐藏路径",
        )

    execution_identity = require_mapping(
        value["execution_identity"], "execution_identity"
    )
    require_exact_keys(
        execution_identity,
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
        require_string(execution_identity[field], f"execution_identity.{field}")
    for field in ("git_author_email", "git_committer_email"):
        email = require_string(
            execution_identity[field], f"execution_identity.{field}"
        )
        if re.fullmatch(r"[^\s@]+@[^\s@]+", email) is None or len(email) > 320:
            invalid(f"execution_identity.{field}", "必须是明确 Git email")
    github_login = require_string(
        execution_identity["github_actor_login"],
        "execution_identity.github_actor_login",
    )
    if re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", github_login
    ) is None:
        invalid(
            "execution_identity.github_actor_login", "必须是明确 GitHub login"
        )

    jira = require_mapping(value["jira"], "jira")
    require_exact_keys(
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
    require_url(jira["base_url"], "jira.base_url", hosts=None)
    require_string(jira["account_id"], "jira.account_id")
    require_string(jira["assignee_account_id"], "jira.assignee_account_id")
    status_mapping = require_mapping(jira["status_mapping"], "jira.status_mapping")
    if not status_mapping:
        invalid("jira.status_mapping", "必须明确绑定 Project Profile 状态映射")
    for status_name, internal_status in status_mapping.items():
        require_string(status_name, "jira.status_mapping key")
        require_id(internal_status, f"jira.status_mapping.{status_name}")
    categories = require_string_list(
        jira["allowed_status_categories"],
        "jira.allowed_status_categories",
        nonempty=True,
    )
    if len(set(categories)) != len(categories) or any(
        category.casefold() == "done" for category in categories
    ):
        invalid("jira.allowed_status_categories", "不能重复或允许 Done")
    repository = require_mapping(value["repository"], "repository")
    require_exact_keys(
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
    require_absolute_path(repository["root"], "repository.root")
    require_repository_slug(repository["slug"], "repository.slug")
    require_id(repository["remote_name"], "repository.remote_name")
    for field in ("base_branch", "task_branch", "target_branch"):
        require_branch(repository[field], f"repository.{field}")
    protected = require_string_list(
        repository["protected_branches"], "repository.protected_branches", nonempty=True
    )
    if len(set(protected)) != len(protected):
        invalid("repository.protected_branches", "不能包含重复分支")
    for branch in protected:
        require_branch(branch, "repository.protected_branches")
    if repository["target_branch"] not in protected:
        invalid("repository.protected_branches", "必须包含 target_branch")
    if repository["base_branch"] != repository["target_branch"]:
        invalid(
            "repository.base_branch",
            "当前协议要求 base_branch 与 target_branch 相同，确保范围 diff 与 PR diff 使用同一基线",
        )
    if repository["task_branch"] in protected:
        invalid("repository.task_branch", "任务分支不能是保护分支")
    if repository["task_branch"] in {
        repository["base_branch"],
        repository["target_branch"],
    }:
        invalid("repository.task_branch", "必须与基线和目标分支不同")

    scope = require_mapping(value["scope"], "scope")
    require_exact_keys(scope, {"included", "excluded"}, "scope")
    included = require_string_list(scope["included"], "scope.included", nonempty=True)
    excluded = require_string_list(scope["excluded"], "scope.excluded")
    if len(set(included)) != len(included):
        invalid("scope.included", "不能包含重复范围")
    if len(set(excluded)) != len(excluded):
        invalid("scope.excluded", "不能包含重复范围")
    overlap = sorted(set(included) & set(excluded))
    if overlap:
        invalid("scope", f"included 与 excluded 不能包含同一项：{overlap}")
    for item in included:
        require_relative_scope(item, "scope.included")
    for item in excluded:
        require_relative_scope(item, "scope.excluded")

    verification = value["verification"]
    if not isinstance(verification, list) or not verification:
        invalid("verification", "必须是非空数组")
    seen_verifications: set[str] = set()
    for index, raw in enumerate(verification):
        item = require_mapping(raw, f"verification[{index}]")
        require_exact_keys(
            item,
            {"id", "command", "working_directory", "timeout_seconds"},
            f"verification[{index}]",
        )
        verification_id = require_id(item["id"], f"verification[{index}].id")
        if verification_id in seen_verifications:
            invalid("verification", "验证 id 不能重复")
        seen_verifications.add(verification_id)
        command = require_string_list(
            item["command"], f"verification[{index}].command", nonempty=True
        )
        if any("\x00" in argument for argument in command):
            invalid(f"verification[{index}].command", "argv 不能包含 NUL")
        working_directory = require_relative_path(
            item["working_directory"], f"verification[{index}].working_directory"
        )
        validate_verification_command(
            command,
            working_directory,
            label=f"verification[{index}]",
        )
        timeout = item["timeout_seconds"]
        if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
            invalid(f"verification[{index}].timeout_seconds", "必须是 1..3600 秒整数")

    endpoint = require_mapping(value["pr_endpoint"], "pr_endpoint")
    require_exact_keys(
        endpoint,
        {"provider", "repository_slug", "target_branch", "ci_policy"},
        "pr_endpoint",
    )
    if endpoint["provider"] != "github":
        invalid("pr_endpoint.provider", "当前协议只接受 github")
    require_repository_slug(endpoint["repository_slug"], "pr_endpoint.repository_slug")
    require_branch(endpoint["target_branch"], "pr_endpoint.target_branch")
    if endpoint["repository_slug"] != repository["slug"]:
        invalid("pr_endpoint.repository_slug", "必须与 repository.slug 一致")
    if endpoint["target_branch"] != repository["target_branch"]:
        invalid("pr_endpoint.target_branch", "必须与 repository.target_branch 一致")
    if endpoint["ci_policy"] not in {"require_passed", "allow_pending", "not_required"}:
        invalid("pr_endpoint.ci_policy", "不是受支持的 CI 策略")

    permissions = require_string_list(
        value["permitted_external_actions"],
        "permitted_external_actions",
        nonempty=True,
    )
    if len(set(permissions)) != len(permissions) or not set(permissions) <= ALLOWED_EXTERNAL_ACTIONS:
        invalid("permitted_external_actions", "包含重复或未知的外部动作")

    authorization = require_mapping(value["authorization"], "authorization")
    require_exact_keys(
        authorization,
        {"reference", "confirmed_by", "confirmed_at", "confirmed_manifest_sha256"},
        "authorization",
    )
    authorization_reference = require_reference(
        authorization["reference"], "authorization.reference"
    )
    expected_authorization_reference = (
        f"user-confirmation:{key}:{agent['agentic_run_id']}:"
        f"{task_binding['approved_plan_sha256']}"
    )
    if authorization_reference != expected_authorization_reference:
        invalid(
            "authorization.reference",
            "必须精确绑定 user-confirmation、当前 issue、agentic_run_id 和批准计划摘要",
        )
    require_string(authorization["confirmed_by"], "authorization.confirmed_by")
    require_timestamp(authorization["confirmed_at"], "authorization.confirmed_at")
    confirmed_digest = require_string(
        authorization["confirmed_manifest_sha256"],
        "authorization.confirmed_manifest_sha256",
        allow_empty=False,
    )
    if not DIGEST_PATTERN.fullmatch(confirmed_digest):
        invalid("authorization.confirmed_manifest_sha256", "必须是 64 位小写 sha256")
    calculated = manifest_digest(value)
    if confirmed_digest != calculated:
        raise blocked(
            "manifest_digest_mismatch",
            "manifest 内容摘要与用户确认摘要不一致",
            "请重新审阅完整 manifest，并写入按协议计算的确认摘要",
        )
    reject_sensitive_content(value)
    return value


def validate_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    require_exact_keys(
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
    require_protocol(value, "event")
    require_id(value["event_id"], "event.event_id")
    require_id(value["agentic_run_id"], "event.agentic_run_id")
    require_id(value["step_id"], "event.step_id")
    require_timestamp(value["recorded_at"], "event.recorded_at")
    if value["status"] not in STATUSES:
        invalid("event.status", "必须是 started、completed 或 blocked")
    if value["actor"] not in ACTORS:
        invalid("event.actor", "执行主体不在协议枚举中")
    if value["action"] not in EVENT_ACTIONS:
        invalid("event.action", "动作不在协议枚举中")
    if value["evidence_origin"] not in EVIDENCE_ORIGINS:
        invalid("event.evidence_origin", "必须是 imported 或 runtime_probe")
    if value["evidence_origin"] == "runtime_probe" and value["actor"] != "runtime":
        invalid("event", "runtime_probe 事件只能由 runtime 生成")
    duration = value["duration_seconds"]
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration < 0
    ):
        invalid("event.duration_seconds", "必须是大于等于 0 的数值")
    require_string(value["summary"], "event.summary")
    if value["authorization_reference"] is not None:
        require_reference(
            value["authorization_reference"], "event.authorization_reference"
        )
    data = require_mapping(value["action_data"], "event.action_data")
    if value["status"] == "started":
        if value["action"] != "step" or data:
            invalid("event", "started 事件只能使用 action=step 和空 action_data")
    else:
        validate_action_data(str(value["action"]), data)
    reject_sensitive_content(value)
    return value


def validate_action_data(action: str, data: Mapping[str, Any]) -> None:
    if action == "step":
        require_exact_keys(data, set(), "action_data")
        return
    if action == "external_action":
        require_exact_keys(
            data, {"action", "target", "status", "readback_event_id"}, "action_data"
        )
        if data["action"] not in ALLOWED_EXTERNAL_ACTIONS:
            invalid("action_data.action", "不是协议允许的外部动作")
        require_string(data["target"], "action_data.target")
        if data["status"] not in {"applied", "unknown", "not_applied"}:
            invalid("action_data.status", "必须是 applied、unknown 或 not_applied")
        if data["readback_event_id"] is not None:
            require_id(data["readback_event_id"], "action_data.readback_event_id")
        return
    if action == "jira_readback":
        require_exact_keys(
            data,
            {
                "provider",
                "reference",
                "url",
                "issue_key",
                "issue_id",
                "project_key",
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
            },
            "action_data",
        )
        if data["provider"] != "jira":
            invalid("action_data.provider", "Jira 回读 provider 必须是 jira")
        require_reference(data["reference"], "action_data.reference")
        require_url(data["url"], "action_data.url", hosts=None)
        require_string(data["issue_key"], "action_data.issue_key")
        require_id(data["issue_id"], "action_data.issue_id")
        require_string(data["project_key"], "action_data.project_key")
        require_string(data["status"], "action_data.status")
        if data["assignee"] is not None:
            require_string(data["assignee"], "action_data.assignee")
        require_string(data["account_id"], "action_data.account_id")
        require_string(data["assignee_account_id"], "action_data.assignee_account_id")
        require_string(data["status_category"], "action_data.status_category")
        require_id(data["mapped_status"], "action_data.mapped_status")
        if data["takeover_comment_id"] is not None:
            require_string(data["takeover_comment_id"], "action_data.takeover_comment_id")
        if not isinstance(data["formal_takeover_verified"], bool):
            invalid("action_data.formal_takeover_verified", "必须是布尔值")
        for field in ("issue_content_sha256", "approved_plan_sha256"):
            if not isinstance(data[field], str) or not DIGEST_PATTERN.fullmatch(
                data[field]
            ):
                invalid(f"action_data.{field}", "必须是 64 位小写 SHA-256")
        require_timestamp(data["observed_at"], "action_data.observed_at")
        return
    if action == "jira_write_readback":
        require_exact_keys(
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
            "action_data",
        )
        if data["provider"] != "jira":
            invalid("action_data.provider", "Jira 写后回读 provider 必须是 jira")
        require_string(data["issue_key"], "action_data.issue_key")
        require_id(data["agentic_run_id"], "action_data.agentic_run_id")
        operation = data["operation"]
        if operation not in {"jira_comment", "jira_worklog"}:
            invalid("action_data.operation", "必须是 jira_comment 或 jira_worklog")
        require_relative_path(data["plan_file"], "action_data.plan_file")
        if data["attempt_file"] is not None:
            require_relative_path(data["attempt_file"], "action_data.attempt_file")
        require_id(data["plan_id"], "action_data.plan_id")
        idempotency_key = require_string(
            data["idempotency_key"], "action_data.idempotency_key"
        )
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", idempotency_key) is None:
            invalid("action_data.idempotency_key", "不是安全的 Jira 幂等键")
        require_string(data["external_id"], "action_data.external_id")
        if not isinstance(data["created"], bool):
            invalid("action_data.created", "必须是布尔值")
        if data["write_precondition"] not in {"absent", "preexisting"}:
            invalid(
                "action_data.write_precondition",
                "必须是 absent 或 preexisting",
            )
        if data["write_attempt_id"] is not None:
            require_id(data["write_attempt_id"], "action_data.write_attempt_id")
        if data["write_attempt_started_at"] is not None:
            require_timestamp(
                data["write_attempt_started_at"],
                "action_data.write_attempt_started_at",
            )
        if data["created"]:
            if (
                data["write_precondition"] != "absent"
                or data["attempt_file"] is None
                or data["write_attempt_id"] is None
                or data["write_attempt_started_at"] is None
            ):
                invalid(
                    "action_data",
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
            invalid(
                "action_data",
                "created=false 不能携带 create 尝试归因",
            )
        for field in ("content_sha256", "body_sha256"):
            if not isinstance(data[field], str) or not DIGEST_PATTERN.fullmatch(data[field]):
                invalid(f"action_data.{field}", "必须是 64 位小写 SHA-256")
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
                invalid("action_data", "Jira 评论回读不能伪装 Worklog 字段")
        else:
            require_string(data["title"], "action_data.title")
            if not isinstance(data["details_sha256"], str) or not DIGEST_PATTERN.fullmatch(
                data["details_sha256"]
            ):
                invalid("action_data.details_sha256", "必须是 64 位小写 SHA-256")
            if (
                isinstance(data["time_spent_seconds"], bool)
                or not isinstance(data["time_spent_seconds"], int)
                or data["time_spent_seconds"] < 1
            ):
                invalid("action_data.time_spent_seconds", "必须是正整数")
            require_timestamp(data["started"], "action_data.started")
            if data["excludes_waiting"] is not True:
                invalid("action_data.excludes_waiting", "必须明确为 true")
            included_work = data["included_work"]
            if not isinstance(included_work, list) or not included_work:
                invalid("action_data.included_work", "必须是非空耗时组成数组")
            included_seconds = 0
            for index, raw in enumerate(included_work):
                item = require_mapping(raw, f"action_data.included_work[{index}]")
                require_exact_keys(
                    item,
                    {"description", "seconds"},
                    f"action_data.included_work[{index}]",
                )
                description = require_string(
                    item["description"],
                    f"action_data.included_work[{index}].description",
                )
                if re.search(r"[\u3400-\u9fff]", description) is None:
                    invalid(
                        f"action_data.included_work[{index}].description",
                        "必须包含中文说明",
                    )
                seconds = item["seconds"]
                if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
                    invalid(
                        f"action_data.included_work[{index}].seconds",
                        "必须是正整数",
                    )
                included_seconds += seconds
            if included_seconds != data["time_spent_seconds"]:
                invalid(
                    "action_data.included_work",
                    "各项 seconds 之和必须等于 time_spent_seconds",
                )
            excluded = require_string_list(
                data["excluded_waiting_categories"],
                "action_data.excluded_waiting_categories",
                nonempty=True,
            )
            if len(set(excluded)) != len(excluded):
                invalid("action_data.excluded_waiting_categories", "不能包含重复项")
            for index, category in enumerate(excluded):
                if re.search(r"[\u3400-\u9fff]", category) is None:
                    invalid(
                        f"action_data.excluded_waiting_categories[{index}]",
                        "必须包含中文类别说明",
                    )
        require_timestamp(data["observed_at"], "action_data.observed_at")
        require_reference(data["reference"], "action_data.reference")
        return
    if action == "prohibition_baseline":
        require_exact_keys(
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
            "action_data",
        )
        require_string(data["issue_key"], "action_data.issue_key")
        require_string(data["repository_slug"], "action_data.repository_slug")
        require_string(data["remote_name"], "action_data.remote_name")
        require_string(data["jira_status"], "action_data.jira_status")
        require_string(
            data["jira_status_category"], "action_data.jira_status_category"
        )
        _require_snapshot_records(
            data["tag_refs"], "action_data.tag_refs", "name", "sha"
        )
        _require_snapshot_records(
            data["release_records"],
            "action_data.release_records",
            "tag_name",
            "published_at",
            nullable_value=True,
            timestamp_value=True,
        )
        _require_snapshot_records(
            data["protected_heads"],
            "action_data.protected_heads",
            "branch",
            "sha",
            nullable_value=True,
        )
        if not isinstance(data["local_head_sha"], str) or not SHA_PATTERN.fullmatch(
            data["local_head_sha"]
        ):
            invalid("action_data.local_head_sha", "必须是 Git SHA")
        remote_sha = data["task_branch_remote_sha"]
        if remote_sha is not None and (
            not isinstance(remote_sha, str) or not SHA_PATTERN.fullmatch(remote_sha)
        ):
            invalid("action_data.task_branch_remote_sha", "必须是 Git SHA 或 null")
        task_open_pr = data["task_open_pr"]
        if task_open_pr is not None:
            task_open_pr = require_mapping(task_open_pr, "action_data.task_open_pr")
            require_exact_keys(
                task_open_pr,
                {"number", "url", "head_sha", "base_branch"},
                "action_data.task_open_pr",
            )
            if isinstance(task_open_pr["number"], bool) or not isinstance(
                task_open_pr["number"], int
            ) or task_open_pr["number"] < 1:
                invalid("action_data.task_open_pr.number", "必须是正整数")
            require_url(task_open_pr["url"], "action_data.task_open_pr.url", hosts={"github.com"})
            if not isinstance(task_open_pr["head_sha"], str) or not SHA_PATTERN.fullmatch(
                task_open_pr["head_sha"]
            ):
                invalid("action_data.task_open_pr.head_sha", "必须是 Git SHA")
            require_branch(task_open_pr["base_branch"], "action_data.task_open_pr.base_branch")
        require_timestamp(data["observed_at"], "action_data.observed_at")
        require_reference(data["reference"], "action_data.reference")
        return
    if action == "remote_branch_readback":
        require_exact_keys(
            data,
            {
                "provider",
                "reference",
                "url",
                "repository_slug",
                "remote_name",
                "branch",
                "sha",
                "status",
                "protected",
                "observed_at",
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
            "action_data",
        )
        if data["provider"] != "git":
            invalid("action_data.provider", "远端分支回读 provider 必须是 git")
        require_reference(data["reference"], "action_data.reference")
        require_url(data["url"], "action_data.url", hosts=None)
        require_repository_slug(data["repository_slug"], "action_data.repository_slug")
        require_id(data["remote_name"], "action_data.remote_name")
        require_branch(data["branch"], "action_data.branch")
        if not isinstance(data["sha"], str) or not SHA_PATTERN.fullmatch(data["sha"]):
            invalid("action_data.sha", "必须是 40 到 64 位小写提交摘要")
        if data["status"] != "exists":
            invalid("action_data.status", "远端任务分支必须回读为 exists")
        if not isinstance(data["protected"], bool):
            invalid("action_data.protected", "必须是布尔值")
        require_timestamp(data["observed_at"], "action_data.observed_at")
        require_string(data["origin_url"], "action_data.origin_url")
        for field in ("base_sha", "head_sha"):
            if not isinstance(data[field], str) or not SHA_PATTERN.fullmatch(data[field]):
                invalid(f"action_data.{field}", "必须是 Git SHA")
        require_id(data["baseline_event_id"], "action_data.baseline_event_id")
        if not isinstance(data["baseline_local_head_sha"], str) or not SHA_PATTERN.fullmatch(
            data["baseline_local_head_sha"]
        ):
            invalid("action_data.baseline_local_head_sha", "必须是 Git SHA")
        baseline_remote = data["baseline_remote_sha"]
        if baseline_remote is not None and (
            not isinstance(baseline_remote, str) or not SHA_PATTERN.fullmatch(baseline_remote)
        ):
            invalid("action_data.baseline_remote_sha", "必须是 Git SHA 或 null")
        if not isinstance(data["baseline_local_is_ancestor"], bool):
            invalid("action_data.baseline_local_is_ancestor", "必须是布尔值")
        remote_ancestor = data["baseline_remote_is_ancestor"]
        if remote_ancestor is not None and not isinstance(remote_ancestor, bool):
            invalid("action_data.baseline_remote_is_ancestor", "必须是布尔值或 null")
        if (baseline_remote is None) != (remote_ancestor is None):
            invalid(
                "action_data.baseline_remote_is_ancestor",
                "必须与 baseline_remote_sha 是否存在保持一致",
            )
        attributed = require_string_list(
            data["attributed_actions"], "action_data.attributed_actions"
        )
        if len(set(attributed)) != len(attributed) or not set(attributed) <= {
            "git_commit",
            "git_push_task_branch",
        }:
            invalid("action_data.attributed_actions", "包含重复或非 Git 归因动作")
        verification_ids = require_string_list(
            data["verification_event_ids"], "action_data.verification_event_ids"
        )
        if len(set(verification_ids)) != len(verification_ids):
            invalid("action_data.verification_event_ids", "不能包含重复项")
        for event_id in verification_ids:
            require_id(event_id, "action_data.verification_event_ids")
        for path in require_string_list(data["changed_paths"], "action_data.changed_paths", nonempty=True):
            require_relative_path(path, "action_data.changed_paths")
        if data["worktree_clean"] is not True:
            invalid("action_data.worktree_clean", "可信 Git probe 要求干净工作树")
        for field in (
            "git_author_name",
            "git_author_email",
            "git_committer_name",
            "git_committer_email",
        ):
            require_string(data[field], f"action_data.{field}")
        if (
            isinstance(data["commit_count"], bool)
            or not isinstance(data["commit_count"], int)
            or data["commit_count"] < 1
        ):
            invalid("action_data.commit_count", "必须是正整数")
        if not isinstance(data["commit_identity_sha256"], str) or not DIGEST_PATTERN.fullmatch(
            data["commit_identity_sha256"]
        ):
            invalid("action_data.commit_identity_sha256", "必须是 64 位小写 SHA-256")
        if not isinstance(data["approved_plan_sha256"], str) or not DIGEST_PATTERN.fullmatch(
            data["approved_plan_sha256"]
        ):
            invalid("action_data.approved_plan_sha256", "必须是 64 位小写 SHA-256")
        return
    if action == "pr_readback":
        require_exact_keys(
            data,
            {
                "provider",
                "reference",
                "url",
                "repository_slug",
                "number",
                "status",
                "merged",
                "head_branch",
                "head_sha",
                "base_branch",
                "review_state",
                "ci_status",
                "draft",
                "github_actor_login",
                "approved_plan_sha256",
                "baseline_event_id",
                "git_readback_event_id",
                "attributed_actions",
                "creation_proof",
                "observed_at",
            },
            "action_data",
        )
        if data["provider"] != "github":
            invalid("action_data.provider", "PR 回读 provider 必须是 github")
        require_reference(data["reference"], "action_data.reference")
        require_url(data["url"], "action_data.url", hosts={"github.com"})
        require_repository_slug(data["repository_slug"], "action_data.repository_slug")
        if isinstance(data["number"], bool) or not isinstance(data["number"], int) or data["number"] < 1:
            invalid("action_data.number", "必须是正整数")
        if data["status"] != "open":
            invalid("action_data.status", "PR 必须是 open")
        if not isinstance(data["merged"], bool):
            invalid("action_data.merged", "必须是布尔值")
        if not isinstance(data["draft"], bool):
            invalid("action_data.draft", "必须是布尔值")
        require_branch(data["head_branch"], "action_data.head_branch")
        if not isinstance(data["head_sha"], str) or not SHA_PATTERN.fullmatch(data["head_sha"]):
            invalid("action_data.head_sha", "必须是 40 到 64 位小写提交摘要")
        require_branch(data["base_branch"], "action_data.base_branch")
        if data["review_state"] not in {
            "awaiting_review",
            "changes_requested",
            "approved",
        }:
            invalid("action_data.review_state", "不是受支持的审查状态")
        if data["ci_status"] not in {"pending", "passed", "failed", "not_configured"}:
            invalid("action_data.ci_status", "不是受支持的 CI 状态")
        github_login = require_string(
            data["github_actor_login"], "action_data.github_actor_login"
        )
        if re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", github_login
        ) is None:
            invalid("action_data.github_actor_login", "必须是明确 GitHub login")
        if not isinstance(data["approved_plan_sha256"], str) or not DIGEST_PATTERN.fullmatch(
            data["approved_plan_sha256"]
        ):
            invalid("action_data.approved_plan_sha256", "必须是 64 位小写 SHA-256")
        require_id(data["baseline_event_id"], "action_data.baseline_event_id")
        require_id(data["git_readback_event_id"], "action_data.git_readback_event_id")
        attributed = require_string_list(
            data["attributed_actions"], "action_data.attributed_actions"
        )
        if len(set(attributed)) != len(attributed) or not set(attributed) <= {
            "github_pr_create_or_update"
        }:
            invalid("action_data.attributed_actions", "包含重复或非 PR 归因动作")
        if not isinstance(data["creation_proof"], bool):
            invalid("action_data.creation_proof", "必须是布尔值")
        require_timestamp(data["observed_at"], "action_data.observed_at")
        return
    if action == "verification":
        require_exact_keys(
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
            "action_data",
        )
        require_id(data["id"], "action_data.id")
        if data["status"] not in {"passed", "failed", "blocked"}:
            invalid("action_data.status", "不是受支持的验证状态")
        if not isinstance(data["command_sha256"], str) or not DIGEST_PATTERN.fullmatch(data["command_sha256"]):
            invalid("action_data.command_sha256", "必须是 64 位小写 sha256")
        require_reference(data["evidence_reference"], "action_data.evidence_reference")
        if isinstance(data["exit_code"], bool) or not isinstance(data["exit_code"], int):
            invalid("action_data.exit_code", "必须是整数")
        duration = data["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
            invalid("action_data.duration_seconds", "必须是非负数")
        for field in ("stdout_sha256", "stderr_sha256"):
            if not isinstance(data[field], str) or not DIGEST_PATTERN.fullmatch(data[field]):
                invalid(f"action_data.{field}", "必须是 sha256")
        require_string(data["output_summary"], "action_data.output_summary")
        if not isinstance(data["head_sha"], str) or not SHA_PATTERN.fullmatch(
            data["head_sha"]
        ):
            invalid("action_data.head_sha", "必须是 Git HEAD SHA")
        return
    if action == "waiting":
        require_exact_keys(
            data,
            {"reason", "started_at", "ended_at", "duration_seconds"},
            "action_data",
        )
        require_string(data["reason"], "action_data.reason")
        require_timestamp(data["started_at"], "action_data.started_at")
        require_timestamp(data["ended_at"], "action_data.ended_at")
        duration = data["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0:
            invalid("action_data.duration_seconds", "必须是非负数")
        return
    if action == "human_intervention":
        require_exact_keys(
            data, {"reason", "action", "impact_seconds"}, "action_data"
        )
        require_string(data["reason"], "action_data.reason")
        require_string(data["action"], "action_data.action")
        impact = data["impact_seconds"]
        if (
            isinstance(impact, bool)
            or not isinstance(impact, (int, float))
            or not math.isfinite(impact)
            or impact < 0
        ):
            invalid("action_data.impact_seconds", "必须是非负数值")
        return
    if action == "failure":
        require_exact_keys(data, {"code", "detail", "retry_safe"}, "action_data")
        require_id(data["code"], "action_data.code")
        require_string(data["detail"], "action_data.detail")
        if not isinstance(data["retry_safe"], bool):
            invalid("action_data.retry_safe", "必须是布尔值")
        return
    if action == "retry":
        require_exact_keys(
            data, {"failure_event_id", "attempt", "outcome"}, "action_data"
        )
        require_id(data["failure_event_id"], "action_data.failure_event_id")
        if isinstance(data["attempt"], bool) or not isinstance(data["attempt"], int) or data["attempt"] < 1:
            invalid("action_data.attempt", "必须是正整数")
        if data["outcome"] not in {"succeeded", "failed", "blocked"}:
            invalid("action_data.outcome", "不是受支持的重试结果")
        return
    if action == "quality_finding":
        require_exact_keys(
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
            "action_data",
        )
        if data["category"] not in QUALITY_CATEGORIES:
            invalid("action_data.category", "不是受支持的复盘分类")
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
            require_string(data[field], f"action_data.{field}")
        require_reference(data["evidence_reference"], "action_data.evidence_reference")
        if data["suggested_asset"] not in {
            "skill",
            "python_runtime",
            "rule",
            "template",
            "profile",
            "test",
        }:
            invalid("action_data.suggested_asset", "不是受支持的改进载体")
        return
    if action == "retrospective":
        require_exact_keys(
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
            "action_data",
        )
        categories = require_string_list(
            data["reviewed_categories"], "action_data.reviewed_categories", nonempty=True
        )
        if set(categories) != set(QUALITY_CATEGORIES) or len(categories) != len(QUALITY_CATEGORIES):
            invalid("action_data.reviewed_categories", "必须逐项审查四类质量问题")
        category_reviews = data["category_reviews"]
        if not isinstance(category_reviews, list) or len(category_reviews) != len(
            QUALITY_CATEGORIES
        ):
            invalid("action_data.category_reviews", "必须为四类问题各提供一条结论")
        reviewed: list[str] = []
        for index, raw in enumerate(category_reviews):
            review = require_mapping(raw, f"action_data.category_reviews[{index}]")
            require_exact_keys(
                review,
                {
                    "category",
                    "outcome",
                    "rationale",
                    "evidence_references",
                    "source_event_ids",
                },
                f"action_data.category_reviews[{index}]",
            )
            category = require_string(
                review["category"], f"action_data.category_reviews[{index}].category"
            )
            if category not in QUALITY_CATEGORIES:
                invalid(
                    f"action_data.category_reviews[{index}].category",
                    "不是受支持的复盘分类",
                )
            reviewed.append(category)
            if review["outcome"] not in {"finding", "no_finding"}:
                invalid(
                    f"action_data.category_reviews[{index}].outcome",
                    "必须是 finding 或 no_finding",
                )
            require_string(
                review["rationale"], f"action_data.category_reviews[{index}].rationale"
            )
            evidence = require_string_list(
                review["evidence_references"],
                f"action_data.category_reviews[{index}].evidence_references",
                nonempty=True,
            )
            if len(set(evidence)) != len(evidence):
                invalid(
                    f"action_data.category_reviews[{index}].evidence_references",
                    "不能包含重复项",
                )
            source_event_ids = require_string_list(
                review["source_event_ids"],
                f"action_data.category_reviews[{index}].source_event_ids",
            )
            if len(set(source_event_ids)) != len(source_event_ids):
                invalid(
                    f"action_data.category_reviews[{index}].source_event_ids",
                    "不能包含重复项",
                )
            for event_id in source_event_ids:
                require_id(
                    event_id,
                    f"action_data.category_reviews[{index}].source_event_ids",
                )
        if set(reviewed) != set(QUALITY_CATEGORIES) or len(set(reviewed)) != len(reviewed):
            invalid("action_data.category_reviews", "必须唯一覆盖四类质量问题")
        for field in (
            "quality_finding_event_ids",
            "human_intervention_event_ids",
            "failure_event_ids",
            "retry_event_ids",
            "waiting_event_ids",
            "ordered_improvement_event_ids",
        ):
            for event_id in require_string_list(data[field], f"action_data.{field}"):
                require_id(event_id, f"action_data.{field}")
        require_string_list(data["residual_risks"], "action_data.residual_risks")
        require_string(data["summary"], "action_data.summary")
        return
    if action == "prohibition_check":
        require_exact_keys(
            data, {"action", "observed", "evidence_reference"}, "action_data"
        )
        if data["action"] not in PROHIBITED_ACTIONS:
            invalid("action_data.action", "不是协议定义的禁止动作")
        if not isinstance(data["observed"], bool) and data["observed"] != "not_verified":
            invalid("action_data.observed", "必须是布尔值或 not_verified")
        require_reference(data["evidence_reference"], "action_data.evidence_reference")
        return
    invalid("event.action", "动作没有对应的数据契约")


def require_protocol(value: Mapping[str, Any], label: str) -> None:
    if value.get("schema_version") != SCHEMA_VERSION or value.get("protocol") != PROTOCOL:
        invalid(label, "schema_version 或 protocol 不受支持")


def require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        invalid(label, f"字段不闭合；missing={missing}, extra={extra}")


def require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        invalid(label, "必须是对象")
    return value


def require_string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        invalid(label, "必须是非空字符串")
    if len(value) > 4096:
        invalid(label, "字符串过长")
    return value


def require_id(value: object, label: str) -> str:
    text = require_string(value, label)
    if not ID_PATTERN.fullmatch(text):
        invalid(label, "必须是安全的稳定标识")
    return text


def require_string_list(value: object, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        invalid(label, "必须是字符串数组")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(require_string(item, f"{label}[{index}]"))
    return result


def validate_verification_command(
    command: object,
    working_directory: object,
    *,
    label: str = "verification",
) -> list[str]:
    """Fail closed unless argv is an explicit non-interactive check/test entry."""

    argv = require_string_list(command, f"{label}.command", nonempty=True)
    if len(argv) > 128:
        _verification_forbidden(label, "argv 参数过多")
    workdir = require_relative_path(working_directory, f"{label}.working_directory")
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
                    "--command=",
                    "--eval=",
                    "--fix=",
                    "--init-hook=",
                    "--load-plugins=",
                    "--publish=",
                    "--require=",
                    "--write=",
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
            "-e",
            "-i",
            "-p",
            "-r",
            "--eval",
            "--interactive",
            "--print",
            "--require",
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
            _verification_forbidden(
                label,
                f"{tool} 只允许离线执行 test/check/lint/typecheck 脚本",
            )
    if tool == "mvn":
        goals = [argument for argument in positional if ":" in argument or argument.isalpha()]
        allowed_goals = {"checkstyle:check", "spotbugs:check", "test", "verify"}
        if not goals or not set(goals) <= allowed_goals:
            _verification_forbidden(label, "Maven 只允许 test/verify 或固定静态检查 goal")
        if not {"-b", "--batch-mode"}.intersection(lowered) or not {
            "-o",
            "--offline",
        }.intersection(lowered):
            _verification_forbidden(label, "Maven 必须显式使用 batch 与 offline 模式")
    if tool == "gradle":
        allowed_tasks = {"check", "lint", "test"}
        if not positional or not set(positional) <= allowed_tasks:
            _verification_forbidden(label, "Gradle 只允许 test/check/lint task")
        if not {"--offline", "--no-daemon"}.issubset(lowered):
            _verification_forbidden(label, "Gradle 必须显式使用 --offline --no-daemon")
    if tool == "dotnet":
        if positional[:1] != ["test"] or "--no-restore" not in lowered:
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
        parts[index : index + 2] in {
            ("developer", ".local"),
            ("maintainer", ".local"),
        }
        for index in range(max(0, len(parts) - 1))
    ):
        _verification_forbidden(label, "验证不能指向 Runtime 受管状态")


def _verification_forbidden(label: str, detail: str) -> None:
    raise blocked(
        "verification_command_forbidden",
        f"协议字段 {label}.command 不是安全验证命令：{detail}",
        "请只声明非交互、离线优先的测试或静态检查 argv；新增入口需先评审 Runtime 白名单",
    )


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
        invalid(label, "必须是数组")
    keys: set[str] = set()
    for index, raw in enumerate(value):
        item = require_mapping(raw, f"{label}[{index}]")
        require_exact_keys(item, {key_field, value_field}, f"{label}[{index}]")
        key = require_string(item[key_field], f"{label}[{index}].{key_field}")
        if key in keys:
            invalid(label, f"{key_field} 不能重复")
        keys.add(key)
        supplied = item[value_field]
        if supplied is None and nullable_value:
            continue
        if timestamp_value:
            require_timestamp(supplied, f"{label}[{index}].{value_field}")
        else:
            text = require_string(supplied, f"{label}[{index}].{value_field}")
            if re.fullmatch(r"[0-9a-f]{40,64}", text) is None:
                invalid(f"{label}[{index}].{value_field}", "必须是 Git SHA")


def require_timestamp(value: object, label: str) -> str:
    text = require_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        invalid(label, f"必须是 ISO-8601 时间：{error}")
    if parsed.tzinfo is None:
        invalid(label, "必须包含时区")
    return text


def require_absolute_path(value: object, label: str) -> str:
    text = require_string(value, label)
    path = Path(text).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        invalid(label, "必须是无跳转的绝对路径")
    return text


def require_relative_path(value: object, label: str) -> str:
    text = require_string(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or "\x00" in text:
        invalid(label, "必须是工作仓库内相对路径")
    return text


def require_relative_scope(value: str, label: str) -> None:
    require_relative_path(value, label)


def require_branch(value: object, label: str) -> str:
    text = require_string(value, label)
    if (
        len(text) > 255
        or text.startswith(("/", "-"))
        or text.endswith(("/", "."))
        or ".." in text
        or "//" in text
        or "@{" in text
        or any(character.isspace() for character in text)
        or any(character in text for character in "~^:?*[]\\")
    ):
        invalid(label, "不是安全的 Git 分支名")
    return text


def require_repository_slug(value: object, label: str) -> str:
    text = require_string(value, label)
    parts = text.split("/")
    if len(parts) != 2 or not all(ID_PATTERN.fullmatch(part) for part in parts):
        invalid(label, "必须是 owner/repository")
    return text


def require_reference(value: object, label: str) -> str:
    text = require_string(value, label)
    if any(character.isspace() for character in text) or "\x00" in text:
        invalid(label, "引用不能包含空白或 NUL")
    if text.startswith(("http://", "https://")):
        require_url(text, label, hosts=None)
    return text


def require_url(value: object, label: str, hosts: set[str] | None) -> str:
    text = require_string(value, label)
    parsed = urlsplit(text)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        invalid(label, "必须是无凭证、query 和 fragment 的 HTTPS URL")
    if hosts is not None and parsed.hostname not in hosts:
        invalid(label, "URL host 不受协议信任")
    return text


def reject_sensitive_content(value: object) -> None:
    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).casefold().replace("-", "_")
                if any(term in normalized for term in ("password", "secret", "token", "private_key", "credential")):
                    raise blocked(
                        "sensitive_content_detected",
                        f"协议输入包含敏感字段：{key}",
                        "请移除凭证和原始敏感内容，仅保留脱敏引用",
                    )
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str):
            if any(pattern.search(item) for pattern in SENSITIVE_VALUE_PATTERNS):
                raise blocked(
                    "sensitive_content_detected",
                    "协议输入疑似包含凭证或私钥",
                    "请移除凭证和原始敏感内容，仅保留脱敏引用",
                )

    visit(value)


def invalid(label: str, detail: str) -> None:
    raise blocked(
        "protocol_schema_invalid",
        f"协议字段 {label} 无效：{detail}",
        "请按 shared/integration 中的版本化 JSON Schema 修复输入",
    )


def blocked(code: str, message: str, action: str, **details: Any) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action=action,
        details=details,
    )
