from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
AGENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
REPOSITORY_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_PATTERN = re.compile(r"^(?!-)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]+$")
ALLOWED_ADAPTERS = frozenset({"offline_fake"})
ALLOWED_WRITES = frozenset({"comment", "description", "worklog", "transition"})
ALLOWED_READS = frozenset({"identity", "project", "fields", "issue", "comment", "worklog"})
ALLOWED_CAPABILITIES = frozenset(
    {
        "local_filesystem",
        "local_git",
        "loopback_http",
        "subprocess",
        "network_git",
        "network_jira",
        "ssh_agent",
        "github_cli_session",
    }
)
ALLOWED_CREDENTIAL_CHANNELS = frozenset(
    {
        "jira_api_token_stdin",
        "jira_api_token_hidden_prompt",
        "git_ssh_agent",
        "github_cli_session",
    }
)
ALLOWED_VERIFICATION_RECIPES = frozenset(
    {
        "python_unittest",
        "shell_script",
        "executable",
    }
)
OFFLINE_REQUIRED_CAPABILITIES = frozenset(
    {"local_filesystem", "local_git", "loopback_http", "subprocess"}
)


@dataclass(frozen=True)
class RepositoryInput:
    repository: Path
    ref: str
    slug: str | None = None


@dataclass(frozen=True)
class IntegrationManifest:
    path: Path
    issue_key: str
    adapter: str
    agentic_ops: RepositoryInput
    task_repository: RepositoryInput
    agent_id: str
    project_profile: str
    jira_project_key: str
    allowed_reads: tuple[str, ...]
    allowed_writes: tuple[str, ...]
    verification_commands: tuple[tuple[str, tuple[str, ...]], ...]
    cleanup_strategy: str
    credential_channels: tuple[str, ...]
    allowed_external_capabilities: tuple[str, ...]
    authorization_reference: str
    confirmed_manifest_sha256: str


def load_manifest(path_value: str, expected_issue_key: str) -> IntegrationManifest:
    expected = _issue_key(expected_issue_key, "命令 issue_key")
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise _invalid("integration_manifest_not_found", f"集成测试输入清单不存在：{path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise _invalid("integration_manifest_invalid", f"集成测试输入清单无法解析：{error}") from error
    if not isinstance(payload, dict):
        raise _invalid("integration_manifest_invalid", "集成测试输入清单必须是 JSON object")

    required_top_level = {
        "schema_version",
        "issue_key",
        "adapter",
        "agentic_ops",
        "task_repository",
        "agent",
        "jira",
        "verification",
        "cleanup",
        "credential_channels",
        "allowed_external_capabilities",
        "confirmation",
    }
    _exact_keys(payload, required_top_level, "清单根节点")
    if type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
        raise _invalid("integration_manifest_invalid", "schema_version 必须为 1")
    issue_key = _issue_key(payload.get("issue_key"), "manifest issue_key")
    if issue_key != expected:
        raise _invalid(
            "integration_issue_mismatch",
            f"命令 issue_key={expected} 与清单 issue_key={issue_key} 不一致",
        )

    adapter = _required_text(payload, "adapter", "清单")
    if adapter not in ALLOWED_ADAPTERS:
        raise _invalid("integration_manifest_invalid", f"adapter 不受支持：{adapter}")
    agentic_ops = _repository(payload.get("agentic_ops"), "agentic_ops", slug_required=False)
    task_repository = _repository(
        payload.get("task_repository"), "task_repository", slug_required=True
    )

    agent = _mapping(payload.get("agent"), "agent")
    _exact_keys(agent, {"agent_id", "project_profile"}, "agent")
    agent_id = _required_text(agent, "agent_id", "agent")
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise _invalid("integration_manifest_invalid", "agent.agent_id 只能包含 [0-9A-Za-z_-]")
    project_profile = _required_text(agent, "project_profile", "agent")
    if not AGENT_ID_PATTERN.fullmatch(project_profile):
        raise _invalid(
            "integration_manifest_invalid",
            "agent.project_profile 只能包含 [0-9A-Za-z_-]",
        )

    jira = _mapping(payload.get("jira"), "jira")
    _exact_keys(jira, {"project_key", "allowed_reads", "allowed_writes"}, "jira")
    project_key = _required_text(jira, "project_key", "jira")
    if not PROJECT_KEY_PATTERN.fullmatch(project_key) or issue_key.partition("-")[0] != project_key:
        raise _invalid(
            "integration_issue_mismatch",
            "jira.project_key 必须与 issue_key 的项目部分一致",
        )
    allowed_reads = _enum_list(jira.get("allowed_reads"), ALLOWED_READS, "jira.allowed_reads")
    allowed_writes = _enum_list(
        jira.get("allowed_writes"), ALLOWED_WRITES, "jira.allowed_writes", allow_empty=True
    )

    verification = _mapping(payload.get("verification"), "verification")
    _exact_keys(verification, {"commands"}, "verification")
    commands = _commands(verification.get("commands"))

    cleanup = _mapping(payload.get("cleanup"), "cleanup")
    _exact_keys(cleanup, {"strategy"}, "cleanup")
    cleanup_strategy = _required_text(cleanup, "strategy", "cleanup")
    if cleanup_strategy != "always":
        raise _invalid(
            "integration_manifest_invalid",
            "当前只允许 cleanup.strategy=always，避免隔离环境残留凭据或状态",
        )

    credential_channels = _enum_list(
        payload.get("credential_channels"),
        ALLOWED_CREDENTIAL_CHANNELS,
        "credential_channels",
        allow_empty=True,
    )
    capabilities = _enum_list(
        payload.get("allowed_external_capabilities"),
        ALLOWED_CAPABILITIES,
        "allowed_external_capabilities",
    )

    confirmation = _mapping(payload.get("confirmation"), "confirmation")
    _exact_keys(
        confirmation,
        {
            "confirmed_by",
            "confirmed_at",
            "authorization_reference",
            "confirmed_manifest_sha256",
        },
        "confirmation",
    )
    _required_text(confirmation, "confirmed_by", "confirmation")
    confirmed_at = _required_text(confirmation, "confirmed_at", "confirmation")
    try:
        parsed_confirmation_time = datetime.fromisoformat(
            confirmed_at.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise _invalid(
            "integration_manifest_invalid",
            "confirmation.confirmed_at 必须是带时区的 ISO-8601 时间",
        ) from error
    if parsed_confirmation_time.tzinfo is None:
        raise _invalid(
            "integration_manifest_invalid",
            "confirmation.confirmed_at 必须包含时区",
        )
    authorization_reference = _required_text(
        confirmation, "authorization_reference", "confirmation"
    )
    confirmed_manifest_sha256 = _required_text(
        confirmation, "confirmed_manifest_sha256", "confirmation"
    )
    manifest_sha256 = _confirmation_digest(payload)
    if confirmed_manifest_sha256 != manifest_sha256:
        raise _invalid(
            "integration_manifest_confirmation_mismatch",
            "集成测试清单内容与人工确认的 SHA-256 不一致",
            details={"expected_manifest_sha256": manifest_sha256},
        )

    manifest = IntegrationManifest(
        path=path,
        issue_key=issue_key,
        adapter=adapter,
        agentic_ops=agentic_ops,
        task_repository=task_repository,
        agent_id=agent_id,
        project_profile=project_profile,
        jira_project_key=project_key,
        allowed_reads=allowed_reads,
        allowed_writes=allowed_writes,
        verification_commands=commands,
        cleanup_strategy=cleanup_strategy,
        credential_channels=credential_channels,
        allowed_external_capabilities=capabilities,
        authorization_reference=authorization_reference,
        confirmed_manifest_sha256=confirmed_manifest_sha256,
    )
    _validate_adapter_contract(manifest)
    return manifest


def confirmation_digest(payload: dict[str, Any]) -> str:
    return _confirmation_digest(payload)


def _confirmation_digest(payload: dict[str, Any]) -> str:
    normalized = copy.deepcopy(payload)
    confirmation = normalized.get("confirmation")
    if not isinstance(confirmation, dict):
        raise _invalid("integration_manifest_invalid", "confirmation 必须是 JSON object")
    confirmation["confirmed_manifest_sha256"] = ""
    encoded = json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON object 包含重复字段：{key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 不允许非有限数值：{value}")


def _validate_adapter_contract(manifest: IntegrationManifest) -> None:
    capabilities = set(manifest.allowed_external_capabilities)
    if manifest.adapter == "offline_fake":
        missing = sorted(OFFLINE_REQUIRED_CAPABILITIES - capabilities)
        extra = sorted(capabilities - OFFLINE_REQUIRED_CAPABILITIES)
        if missing or extra:
            raise _invalid(
                "integration_capability_not_authorized",
                "offline_fake 的外部能力必须精确声明 local_filesystem、local_git、"
                "loopback_http、subprocess",
                details={"missing_capabilities": missing, "unexpected_capabilities": extra},
            )
        if manifest.credential_channels:
            raise _invalid(
                "integration_manifest_invalid",
                "offline_fake 不接受任何真实凭据通道",
            )
        if set(manifest.allowed_writes) != {"comment"}:
            raise _invalid(
                "integration_capability_not_authorized",
                "offline_fake 烟测链路只允许写入隔离 Fake Jira 评论",
            )
        required_reads = {"identity", "project", "issue", "comment"}
        if not required_reads.issubset(manifest.allowed_reads):
            raise _invalid(
                "integration_capability_not_authorized",
                "offline_fake 缺少 identity、project、issue 或 comment 读取授权",
            )
        if manifest.agentic_ops.ref != "WORKTREE":
            raise _invalid(
                "integration_manifest_invalid",
                "offline_fake 当前要求 agentic_ops.ref=WORKTREE，以显式测试当前本地代码",
            )


def _repository(value: object, label: str, *, slug_required: bool) -> RepositoryInput:
    raw = _mapping(value, label)
    expected = {"repository", "ref", "slug"} if slug_required else {"repository", "ref"}
    _exact_keys(raw, expected, label)
    repository_text = _required_text(raw, "repository", label)
    repository = Path(repository_text)
    if not repository.is_absolute():
        raise _invalid("integration_manifest_invalid", f"{label}.repository 必须是显式绝对路径")
    repository = repository.resolve()
    ref = _required_text(raw, "ref", label)
    if ref != "WORKTREE" and not REF_PATTERN.fullmatch(ref):
        raise _invalid("integration_manifest_invalid", f"{label}.ref 格式无效")
    slug: str | None = None
    if slug_required:
        slug = _required_text(raw, "slug", label)
        if not REPOSITORY_SLUG_PATTERN.fullmatch(slug):
            raise _invalid("integration_manifest_invalid", f"{label}.slug 必须是 owner/repository")
    return RepositoryInput(repository=repository, ref=ref, slug=slug)


def _commands(value: object) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(value, list) or not value:
        raise _invalid("integration_manifest_invalid", "verification.commands 必须是非空数组")
    result: list[tuple[str, tuple[str, ...]]] = []
    for index, command in enumerate(value):
        if not isinstance(command, dict):
            raise _invalid(
                "integration_manifest_invalid",
                f"verification.commands[{index}] 必须是 recipe 与 args 对象",
            )
        _exact_keys(command, {"recipe", "args"}, f"verification.commands[{index}]")
        recipe = _required_text(command, "recipe", f"verification.commands[{index}]")
        if recipe not in ALLOWED_VERIFICATION_RECIPES:
            raise _invalid(
                "integration_manifest_invalid",
                f"verification.commands[{index}].recipe 不受支持：{recipe}",
            )
        raw_arguments = command.get("args")
        if not isinstance(raw_arguments, list) or not raw_arguments:
            raise _invalid(
                "integration_manifest_invalid",
                f"verification.commands[{index}].args 必须是非空 argv 数组",
            )
        arguments: list[str] = []
        for item in raw_arguments:
            if not isinstance(item, str) or not item or "\x00" in item or "\n" in item:
                raise _invalid(
                    "integration_manifest_invalid",
                    f"verification.commands[{index}] 包含无效参数",
                )
            arguments.append(item)
        result.append((recipe, tuple(arguments)))
    return tuple(result)


def _enum_list(
    value: object,
    allowed: frozenset[str],
    label: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise _invalid("integration_manifest_invalid", f"{label} 必须是{'可空' if allow_empty else '非空'}数组")
    if not all(isinstance(item, str) and item for item in value):
        raise _invalid("integration_manifest_invalid", f"{label} 包含无效值")
    values = tuple(value)
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise _invalid("integration_manifest_invalid", f"{label} 包含未知值：{', '.join(unknown)}")
    if len(values) != len(set(values)):
        raise _invalid("integration_manifest_invalid", f"{label} 不得包含重复值")
    return values


def _issue_key(value: object, label: str) -> str:
    if not isinstance(value, str) or not ISSUE_KEY_PATTERN.fullmatch(value):
        raise _invalid("integration_issue_invalid", f"{label} 格式无效")
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _invalid("integration_manifest_invalid", f"{label} 必须是 JSON object")
    return value


def _required_text(raw: dict[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip() or value.strip() == "REQUIRED":
        raise _invalid("integration_manifest_invalid", f"{label}.{key} 尚未明确填写")
    return value.strip()


def _exact_keys(raw: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(raw))
    unknown = sorted(set(raw) - expected)
    if missing or unknown:
        raise _invalid(
            "integration_manifest_invalid",
            f"{label} 字段不完整或存在未知字段",
            details={"field": label, "missing_fields": missing, "unknown_fields": unknown},
        )


def _invalid(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action="请补齐并人工确认显式输入清单后重新运行",
        details=details or {},
    )
