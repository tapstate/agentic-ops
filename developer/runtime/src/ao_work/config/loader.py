from __future__ import annotations

import json
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

import yaml

from ao_work.managed_io import read_managed_json, read_managed_text
from ao_work.config.model import (
    FIELD_STATES,
    AnalysisMount,
    BranchDerivation,
    CiProfile,
    FieldMapping,
    JiraConnection,
    ProjectProfile,
    RepositoryBranchRule,
    WorktreeDomain,
    require_mapping,
)
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.workspace import Workspace
from ao_work.workspace_security import validate_workspace_managed_path

CONFIG_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True)
class JiraContext:
    connection: JiraConnection
    profile: ProjectProfile
    email: str | None
    token: str | None

    def credential_status(self) -> dict[str, bool]:
        return {"email_configured": bool(self.email), "token_configured": bool(self.token)}

    def require_credentials(self) -> tuple[str, str]:
        if self.email and self.token:
            return self.email, self.token
        missing = []
        if not self.email:
            missing.append(self.connection.email_env)
        if not self.token:
            missing.append(self.connection.token_env)
        raise RuntimeErrorResult(
            code="jira_credentials_missing",
            message=f"Jira 凭证未配置：{', '.join(missing)}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请执行 ao-work auth 配置当前 developer 安装的研发员账户",
        )


def load_jira_context(workspace: Workspace, install_root: Path) -> JiraContext:
    agent = _load_agent_config(workspace)
    profile_id = _validated_config_id(
        _required_text(agent, "project_profile", "workspace_project_profile_missing"),
        "Project Profile",
    )

    profile_payload = _load_layered_yaml(
        [
            install_root / "developer" / "standards" / "projects" / profile_id / "profile.yaml",
            install_root / "user" / "projects" / profile_id / "profile.local.yaml",
            _workspace_config_path(
                workspace.root, "profiles", f"{profile_id}.local.yaml"
            ),
        ],
        "project_profile_not_found",
    )
    try:
        profile = _parse_profile(profile_payload, profile_id)
    except (TypeError, ValueError) as error:
        raise _configuration_error("Project Profile", error) from error
    configured_connection_id = _optional_text(agent.get("connection_id"))
    if configured_connection_id and profile.connection_id != configured_connection_id:
        raise RuntimeErrorResult(
            code="jira_workspace_mismatch",
            message=(
                f"工作空间 connection_id={configured_connection_id} 与项目 Profile "
                f"connection_id={profile.connection_id} 不一致"
            ),
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请核对项目工作空间与 Jira Profile 绑定后重试",
        )

    connection = load_jira_connection(
        install_root,
        profile.connection_id,
        workspace_root=workspace.root,
    )
    validate_workspace_jira_binding(
        workspace,
        connection,
        install_root=install_root,
    )
    validate_workspace_project_binding(workspace, profile)
    from ao_work.installation import load_install_credentials, load_install_identity

    install_identity = load_install_identity(install_root)
    install_credentials = load_install_credentials(install_root)
    if install_credentials is not None:
        email, token = install_credentials
        if email != str(install_identity["jira_email"]).strip():
            raise RuntimeErrorResult(
                code="install_identity_drift",
                message="安装身份中的 Jira email 与安装凭据不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请运行 ao-work auth 重新配置同一 Jira 账户",
            )
    else:
        email, token = None, None
    return JiraContext(
        connection=connection,
        profile=profile,
        email=email,
        token=token,
    )


def list_project_profiles(install_root: Path) -> list[str]:
    profile_ids: set[str] = set()
    for directory in (
        install_root / "developer" / "standards" / "projects",
        install_root / "user" / "projects",
    ):
        if not directory.is_dir():
            continue
        for path in directory.glob("*/profile.yaml"):
            if path.parent.name:
                profile_ids.add(path.parent.name)
        for path in directory.glob("*/profile.local.yaml"):
            if path.parent.name:
                profile_ids.add(path.parent.name)
    return sorted(profile_ids)


def load_project_profile(
    install_root: Path,
    profile_id: str,
    *,
    workspace_root: Path | None = None,
) -> ProjectProfile:
    profile_id = _validated_config_id(profile_id, "Project Profile")
    paths = [
        install_root / "developer" / "standards" / "projects" / profile_id / "profile.yaml",
        install_root / "user" / "projects" / profile_id / "profile.local.yaml",
    ]
    if workspace_root is not None:
        paths.append(_workspace_config_path(workspace_root, "profiles", f"{profile_id}.local.yaml"))
    payload = _load_layered_yaml(paths, "project_profile_not_found")
    try:
        return _parse_profile(payload, profile_id)
    except (TypeError, ValueError) as error:
        raise _configuration_error("Project Profile", error) from error


def resolve_workspace_connection_id(
    workspace: Workspace,
    install_root: Path,
    explicit_connection_id: str | None = None,
) -> str:
    agent = _load_agent_config(workspace, required=False)
    profile_id = _optional_text(agent.get("project_profile"))
    configured_connection_id = _optional_text(agent.get("connection_id"))

    profile_connection_id: str | None = None
    if profile_id:
        profile_id = _validated_config_id(profile_id, "Project Profile")
        profile_payload = _load_layered_yaml(
            [
                install_root / "developer" / "standards" / "projects" / profile_id / "profile.yaml",
                install_root / "user" / "projects" / profile_id / "profile.local.yaml",
                _workspace_config_path(
                    workspace.root, "profiles", f"{profile_id}.local.yaml"
                ),
            ],
            "project_profile_not_found",
        )
        try:
            profile_connection_id = _parse_profile(profile_payload, profile_id).connection_id
        except (TypeError, ValueError) as error:
            raise _configuration_error("Project Profile", error) from error

    candidates = {
        value
        for value in (explicit_connection_id, configured_connection_id, profile_connection_id)
        if value
    }
    if len(candidates) > 1:
        raise RuntimeErrorResult(
            code="jira_workspace_mismatch",
            message="显式 Jira Connection、工作空间绑定与 Project Profile 不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复工作空间绑定；不得在一个工作空间混用多个 Jira 站点",
        )
    if candidates:
        return candidates.pop()

    connections = list_jira_connections(install_root)
    if len(connections) == 1:
        return connections[0]
    if not connections:
        raise RuntimeErrorResult(
            code="jira_connection_not_found",
            message="当前安装没有可用的 Jira Connection",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请先安装或配置 Jira Connection",
        )
    raise RuntimeErrorResult(
        code="jira_connection_selection_required",
        message="当前安装包含多个 Jira 站点，工作空间尚未绑定默认站点",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="首次设置时请使用高级参数 --connection-id，之后可省略",
    )


def load_jira_connection(
    install_root: Path,
    connection_id: str,
    *,
    workspace_root: Path | None = None,
) -> JiraConnection:
    connection_id = _validated_config_id(connection_id, "Jira Connection")
    paths = [
        install_root / "developer" / "standards" / "connections" / f"{connection_id}.yaml",
        install_root / "user" / "connections" / f"{connection_id}.local.yaml",
    ]
    if workspace_root is not None:
        paths.append(_workspace_config_path(workspace_root, "connections", f"{connection_id}.local.yaml"))
    connection_payload = _load_layered_yaml(paths, "jira_connection_not_found")
    try:
        return _parse_connection(connection_payload, connection_id)
    except (TypeError, ValueError) as error:
        raise _configuration_error("Jira Connection", error) from error


def list_jira_connections(install_root: Path) -> list[str]:
    connection_ids: set[str] = set()
    for directory in (
        install_root / "developer" / "standards" / "connections",
        install_root / "user" / "connections",
    ):
        if not directory.is_dir():
            continue
        for path in directory.glob("*.yaml"):
            name = path.name
            if name.endswith(".local.yaml"):
                name = name[: -len(".local.yaml")]
            else:
                name = path.stem
            if name:
                connection_ids.add(name)
    return sorted(connection_ids)


def _validated_config_id(value: str, label: str) -> str:
    normalized = value.strip()
    if not CONFIG_ID_PATTERN.fullmatch(normalized):
        raise RuntimeErrorResult(
            code="configuration_id_invalid",
            message=f"{label} 标识只能包含 [0-9A-Za-z_-]",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=f"请修正 {label} 标识，不能使用路径或相对目录",
        )
    return normalized


def _load_agent_config(workspace: Workspace, *, required: bool = True) -> dict[str, Any]:
    if workspace.config_path is None:
        if not required:
            return {}
        raise RuntimeErrorResult(
            code="workspace_config_missing",
            message="Jira 操作要求项目工作空间提供 .agentic-ops/agent.json",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请初始化业务项目 AI 工作空间并配置 Jira 绑定",
        )
    config_path = validate_workspace_managed_path(
        workspace.root, workspace.root / ".agentic-ops" / "agent.json"
    )
    try:
        payload = read_managed_json(config_path, label="工作空间 agent.json")
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeErrorResult(
            code="workspace_config_invalid",
            message=f"工作空间配置无法读取：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复 .agentic-ops/agent.json 后重试",
        ) from error
    return require_mapping(payload, "agent.json")


def _load_layered_yaml(paths: list[Path], missing_code: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    used = False
    for path in paths:
        content = read_managed_text(
            path,
            label=f"受管配置 {path.name}",
            allow_missing=True,
        )
        if content is None:
            continue
        used = True
        try:
            payload = yaml.safe_load(content) or {}
            _merge(merged, require_mapping(payload, str(path)))
        except (OSError, ValueError, yaml.YAMLError) as error:
            raise RuntimeErrorResult(
                code="configuration_invalid",
                message=f"配置文件无法读取：{path.name}（{error}）",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请修复配置语法和字段后重试",
            ) from error
    if not used:
        raise RuntimeErrorResult(
            code=missing_code,
            message=f"未找到配置：{paths[0]}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请添加对应 Connection 或 Project Profile 配置",
        )
    return merged


def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _parse_connection(payload: dict[str, Any], expected_id: str) -> JiraConnection:
    actual_id = str(payload.get("connection_id", "")).strip()
    if actual_id != expected_id:
        raise ValueError(f"connection_id {actual_id!r} does not match {expected_id!r}")
    auth = require_mapping(payload.get("auth", {}), "auth")
    base_url = normalize_jira_site_root(
        _required_text(payload, "base_url", "jira_connection_invalid")
    )
    email_env = _required_text(auth, "email_env", "jira_connection_invalid")
    token_env = _required_text(auth, "token_env", "jira_connection_invalid")
    if not ENV_NAME_PATTERN.fullmatch(email_env):
        raise ValueError("auth.email_env must be a valid uppercase environment variable name")
    if not ENV_NAME_PATTERN.fullmatch(token_env):
        raise ValueError("auth.token_env must be a valid uppercase environment variable name")
    if email_env == token_env:
        raise ValueError("auth.email_env and auth.token_env must be different")
    timeout_seconds = float(payload.get("timeout_seconds", 20.0))
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
        raise ValueError("timeout_seconds must be finite and in the range (0, 300]")
    return JiraConnection(
        connection_id=actual_id,
        base_url=base_url,
        email_env=email_env,
        token_env=token_env,
        timeout_seconds=timeout_seconds,
    )


def normalize_jira_site_root(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError(
            "base_url must be an HTTPS site root without userinfo, query, fragment, or path"
        )
    hostname = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("base_url port is invalid") from error
    authority = hostname if port is None else f"{hostname}:{port}"
    return f"https://{authority}"


def jira_site_identity(base_url: str) -> str:
    parsed = urlsplit(normalize_jira_site_root(base_url))
    return parsed.netloc.lower()


def validate_workspace_jira_binding(
    workspace: Workspace,
    connection: JiraConnection,
    *,
    account_id: str | None = None,
    install_root: Path | None = None,
) -> dict[str, Any]:
    agent = _load_agent_config(workspace)
    schema_version = agent.get("schema_version")
    if schema_version != 5:
        raise RuntimeErrorResult(
            code="workspace_jira_identity_upgrade_required",
            message="旧工作空间入口格式已停用，当前工作空间必须升级到 schema v5",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                "请先执行 ao-work auth 配置安装级授权，再显式执行 "
                "<安装目录>/bin/ao-work workspace init --confirm-existing-config"
            ),
        )
    expected = {
        "connection_id": connection.connection_id,
        "jira_base_url": connection.base_url,
        "jira_site": jira_site_identity(connection.base_url),
    }
    for field, value in expected.items():
        if agent.get(field) != value:
            raise RuntimeErrorResult(
                code="jira_workspace_identity_drift",
                message=f"工作空间固化的 {field} 与当前 Connection 不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请停止读取凭证和发送请求，核对 Connection/Profile overlay 漂移",
            )
    # 身份/凭证只在安装目录。工作空间只持 install_identity_ref 指纹。
    if install_root is None:
        raise RuntimeErrorResult(
            code="install_identity_missing",
            message="schema v5 工作空间需要安装目录身份校验，但未提供 install_root",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请通过 ao-work 正确入口操作该工作空间",
        )
    install_identity = _load_install_identity_for_binding(install_root)
    expected_ref = agent.get("install_identity_ref")
    if not isinstance(expected_ref, str) or not expected_ref.strip():
        raise RuntimeErrorResult(
            code="workspace_jira_identity_upgrade_required",
            message="schema v5 工作空间缺少 install_identity_ref",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 ao-work workspace init 完成身份绑定",
        )
    current_ref = _install_identity_ref(install_root, install_identity)
    if current_ref != expected_ref:
        raise RuntimeErrorResult(
            code="install_identity_drift",
            message="工作空间引用的安装目录身份与当前安装目录身份不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请核对是否误用了其它研发员的安装目录或工作空间",
        )
    expected_entry = agent.get("workspace_entry")
    if expected_entry != ".agentic-ops/bin/ao-work":
        raise RuntimeErrorResult(
            code="workspace_local_entry_missing",
            message="工作空间缺少受管的本地 ao-work 入口绑定",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                "请使用绑定安装的 <安装目录>/bin/ao-work workspace init "
                "--confirm-existing-config 重新生成本地入口"
            ),
        )
    expected_entry_hash = agent.get("install_entry_sha256")
    if not isinstance(expected_entry_hash, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_entry_hash
    ):
        raise RuntimeErrorResult(
            code="workspace_local_entry_missing",
            message="工作空间缺少安装入口完整性摘要",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                "请使用绑定安装的 <安装目录>/bin/ao-work workspace init "
                "--confirm-existing-config 重新生成本地入口"
            ),
        )
    if install_entry_sha256(install_root) != expected_entry_hash:
        raise RuntimeErrorResult(
            code="install_entry_drift",
            message="当前安装的 ao-work 入口与工作空间绑定摘要不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                "请停止使用当前入口；使用原绑定安装重新初始化，或核对安装更新后重新绑定"
            ),
        )
    return agent


def _load_install_identity_for_binding(install_root: Path) -> dict[str, Any]:
    from ao_work.installation import load_install_identity

    return load_install_identity(install_root)


def _install_identity_ref(install_root: Path, identity: dict[str, Any]) -> str:
    import hashlib
    import json as _json

    fingerprint = hashlib.sha256(
        _json.dumps(
            {
                "agent_id": identity["agent_id"],
                "jira_email": identity["jira_email"],
                "execution_identity": identity["execution_identity"],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"install:{fingerprint}"


def install_entry_sha256(install_root: Path) -> str:
    """返回 developer 安装入口的内容摘要，拒绝符号链接和异常文件。"""
    import hashlib

    entry = install_root.expanduser().resolve() / "bin" / "ao-work"
    if entry.is_symlink() or not entry.is_file():
        raise RuntimeErrorResult(
            code="install_entry_missing",
            message="developer 安装缺少安全的 bin/ao-work 入口",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新完成 developer 安装后再初始化或使用工作空间",
        )
    try:
        return hashlib.sha256(entry.read_bytes()).hexdigest()
    except OSError as error:
        raise RuntimeErrorResult(
            code="install_entry_missing",
            message="无法读取 developer 安装的 bin/ao-work 入口",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复或重新完成 developer 安装后再试",
        ) from error


def validate_workspace_project_binding(
    workspace: Workspace,
    profile: ProjectProfile,
) -> dict[str, Any]:
    agent = _load_agent_config(workspace)
    expected = {
        "project_profile": profile.profile_id,
        "jira_project": profile.project_key,
        "repository": profile.default_repository,
        "source_root": profile.workspace_source_root,
    }
    if profile.workspace_repository != profile.default_repository:
        raise RuntimeErrorResult(
            code="workspace_project_identity_drift",
            message="effective Project Profile 的 workspace.repository 与 repositories.default 不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请停止执行；仅可通过显式 workspace init --confirm 重新绑定",
        )
    for field, value in expected.items():
        if not isinstance(value, str) or not value.strip() or agent.get(field) != value:
            raise RuntimeErrorResult(
                code="workspace_project_identity_drift",
                message=f"工作空间固化的 {field} 与 effective Project Profile 不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请停止执行；仅可通过显式 workspace init --confirm 重新绑定",
            )
    try:
        if Path(profile.workspace_source_root).expanduser().resolve() != Path(str(agent["source_root"])).expanduser().resolve():
            raise ValueError("source_root mismatch")
    except (OSError, ValueError) as error:
        raise RuntimeErrorResult(
            code="workspace_project_identity_drift",
            message="工作空间 source_root 规范路径与 effective Project Profile 不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请停止执行；仅可通过显式 workspace init --confirm 重新绑定",
        ) from error
    return agent


def _parse_profile(payload: dict[str, Any], expected_id: str) -> ProjectProfile:
    actual_id = str(payload.get("profile_id", "")).strip()
    if actual_id != expected_id:
        raise ValueError(f"profile_id {actual_id!r} does not match {expected_id!r}")
    jira = require_mapping(payload.get("jira", {}), "jira")
    parsed_fields: dict[str, FieldMapping] = {}
    for logical_name, raw_mapping in require_mapping(payload.get("fields", {}), "fields").items():
        mapping = require_mapping(raw_mapping, f"fields.{logical_name}")
        state = str(mapping.get("state", "active"))
        if state not in FIELD_STATES:
            raise ValueError(f"unsupported field state: {state}")
        parsed_fields[str(logical_name)] = FieldMapping(
            logical_name=str(logical_name),
            source=str(mapping.get("source", "jira_field")),
            jira_field=_optional_text(mapping.get("jira_field")),
            section=_optional_text(mapping.get("section")),
            state=state,
            writable=bool(mapping.get("writable", False)),
            required=bool(mapping.get("required", False)),
        )
    transition_mapping = _parse_transition_mapping(payload.get("transitions", {}))
    repositories = require_mapping(payload.get("repositories", {}), "repositories")
    workspace = require_mapping(payload.get("workspace", {}), "workspace")
    default_repository = _optional_text(repositories.get("default"))
    if default_repository and (
        default_repository.count("/") != 1
        or any(part in {"", ".", ".."} for part in default_repository.split("/"))
    ):
        raise ValueError("repositories.default must use owner/repository format")
    repository_list = _parse_repository_list(repositories, default_repository)
    analysis_mount = _parse_analysis_mount(repositories, repository_list)
    branch_derivation = _parse_branch_derivation(repositories, repository_list)
    worktree_domains = _parse_worktree_domains(repositories, repository_list)
    process_id = str(payload.get("process_id", "development_change_v1")).strip()
    if process_id not in {"development_change_v1", "development_change_v2"}:
        raise ValueError("process_id must be development_change_v1 or development_change_v2")
    ci = _parse_ci_profile(payload.get("ci"))
    if process_id == "development_change_v2" and ci is None:
        raise ValueError("development_change_v2 requires ci configuration")
    if process_id == "development_change_v1" and ci is not None:
        raise ValueError("ci configuration requires process_id=development_change_v2")
    return ProjectProfile(
        profile_id=actual_id,
        connection_id=_required_text(payload, "connection_id", "project_profile_invalid"),
        project_key=_required_text(jira, "project_key", "project_profile_invalid"),
        task_query=str(jira.get("task_query", "")),
        issue_types=tuple(str(value) for value in jira.get("issue_types", [])),
        fields=parsed_fields,
        status_mapping={
            str(key): str(value)
            for key, value in require_mapping(payload.get("statuses", {}), "statuses").items()
        },
        transition_mapping=transition_mapping,
        default_repository=default_repository,
        workspace_source_root=_optional_text(workspace.get("source_root")),
        workspace_repository=_optional_text(workspace.get("repository")),
        repository_list=repository_list,
        analysis_mount=analysis_mount,
        branch_derivation=branch_derivation,
        worktree_domains=worktree_domains,
        process_id=process_id,
        ci=ci,
    )


def _parse_ci_profile(value: Any) -> CiProfile | None:
    if value is None:
        return None
    raw = require_mapping(value, "ci")
    expected = {
        "provider",
        "start_timeout_seconds",
        "completion_timeout_seconds",
        "poll_interval_seconds",
        "max_remediation_attempts",
        "required_checks",
        "workflows",
        "artifact_name_patterns",
        "report_parser",
        "limits",
        "completion",
    }
    if set(raw) != expected:
        raise ValueError(
            f"ci fields must be closed; missing={sorted(expected - set(raw))}, "
            f"extra={sorted(set(raw) - expected)}"
        )
    if raw["provider"] != "github-actions":
        raise ValueError("ci.provider must be github-actions")
    if raw["report_parser"] != "maven-failsafe-v1":
        raise ValueError("ci.report_parser must be maven-failsafe-v1")

    def integer(name: str, minimum: int, maximum: int) -> int:
        item = raw[name]
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            raise ValueError(f"ci.{name} must be an integer in [{minimum}, {maximum}]")
        return item

    def strings(name: str) -> tuple[str, ...]:
        item = raw[name]
        if (
            not isinstance(item, list)
            or not item
            or not all(isinstance(entry, str) and entry.strip() for entry in item)
        ):
            raise ValueError(f"ci.{name} must be a non-empty string list")
        normalized = tuple(entry.strip() for entry in item)
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"ci.{name} contains duplicates")
        return normalized

    limits = require_mapping(raw["limits"], "ci.limits")
    expected_limits = {
        "max_archive_bytes",
        "max_extracted_bytes",
        "max_file_bytes",
        "max_files",
        "max_depth",
    }
    if set(limits) != expected_limits:
        raise ValueError("ci.limits fields must be closed")
    completion = require_mapping(raw["completion"], "ci.completion")
    if set(completion) != {"finish_agent_run_on_pass", "transition_jira_done"}:
        raise ValueError("ci.completion fields must be closed")
    if completion["finish_agent_run_on_pass"] is not True:
        raise ValueError("ci.completion.finish_agent_run_on_pass must be true")
    if not isinstance(completion["transition_jira_done"], bool):
        raise ValueError("ci.completion.transition_jira_done must be boolean")

    def limit(name: str, minimum: int, maximum: int) -> int:
        item = limits[name]
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            raise ValueError(f"ci.limits.{name} must be in [{minimum}, {maximum}]")
        return item

    max_archive = limit("max_archive_bytes", 1_024, 524_288_000)
    max_extracted = limit("max_extracted_bytes", 1_024, 1_073_741_824)
    max_file = limit("max_file_bytes", 1_024, max_extracted)
    if max_archive > max_extracted:
        raise ValueError("ci.limits.max_archive_bytes cannot exceed max_extracted_bytes")
    start_timeout_seconds = integer("start_timeout_seconds", 1, 3_600)
    completion_timeout_seconds = integer("completion_timeout_seconds", 1, 3_600)
    if start_timeout_seconds != 300:
        raise ValueError("ci.start_timeout_seconds must equal 300")
    if completion_timeout_seconds != 600:
        raise ValueError("ci.completion_timeout_seconds must equal 600")
    poll_interval_seconds = integer("poll_interval_seconds", 1, 300)
    if poll_interval_seconds > min(start_timeout_seconds, completion_timeout_seconds):
        raise ValueError("ci.poll_interval_seconds cannot exceed CI timeout values")
    return CiProfile(
        provider="github-actions",
        start_timeout_seconds=start_timeout_seconds,
        completion_timeout_seconds=completion_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
        max_remediation_attempts=integer("max_remediation_attempts", 1, 3),
        required_checks=strings("required_checks"),
        workflows=strings("workflows"),
        artifact_name_patterns=strings("artifact_name_patterns"),
        report_parser="maven-failsafe-v1",
        max_archive_bytes=max_archive,
        max_extracted_bytes=max_extracted,
        max_file_bytes=max_file,
        max_files=limit("max_files", 1, 20_000),
        max_depth=limit("max_depth", 1, 100),
        finish_agent_run_on_pass=True,
        transition_jira_done=completion["transition_jira_done"],
    )


def _parse_repository_list(
    repositories: dict[str, Any], default_repository: str | None
) -> tuple[str, ...]:
    raw_list = repositories.get("list", [])
    if not raw_list:
        return ()
    if not isinstance(raw_list, list) or not all(
        isinstance(value, str) for value in raw_list
    ):
        raise ValueError("repositories.list must be a list of owner/repository strings")
    normalized = tuple(value.strip() for value in raw_list if value.strip())
    for repository in normalized:
        if (
            repository.count("/") != 1
            or any(part in {"", ".", ".."} for part in repository.split("/"))
        ):
            raise ValueError(
                f"repositories.list entry must use owner/repository format: {repository}"
            )
    if len(set(normalized)) != len(normalized):
        raise ValueError("repositories.list contains duplicate entries")
    if default_repository and default_repository not in normalized:
        raise ValueError("repositories.default must be included in repositories.list")
    return normalized


def _parse_transition_mapping(value: Any) -> dict[str, dict[str, Any]]:
    raw = require_mapping(value, "transitions")
    result: dict[str, dict[str, Any]] = {}
    for key, entry in raw.items():
        spec = require_mapping(entry, f"transitions.{key}")
        name = _optional_text(spec.get("name"))
        if not name:
            raise ValueError(f"transitions.{key} requires a name")
        transition_id = _optional_text(spec.get("id"))
        from_states = spec.get("from", [])
        if not isinstance(from_states, list) or not all(
            isinstance(item, str) for item in from_states
        ):
            raise ValueError(f"transitions.{key}.from must be a string list")
        to_status = _optional_text(spec.get("to"))
        result[str(key)] = {
            "name": name,
            "id": transition_id or "",
            "from": [item.strip() for item in from_states if item.strip()],
            "to": to_status or "",
        }
    return result


def _parse_analysis_mount(
    repositories: dict[str, Any], repository_list: tuple[str, ...]
) -> AnalysisMount:
    raw = repositories.get("analysis_mount", {})
    if not raw:
        return AnalysisMount()
    if not isinstance(raw, dict):
        raise ValueError("repositories.analysis_mount must be a mapping")
    mode = str(raw.get("mode", "all"))
    if mode not in {"all", "include", "exclude"}:
        raise ValueError("repositories.analysis_mount.mode must be all|include|exclude")
    include = _repository_tuple(raw.get("include"), "analysis_mount.include")
    exclude = _repository_tuple(raw.get("exclude"), "analysis_mount.exclude")
    for repository in (*include, *exclude):
        if repository_list and repository not in repository_list:
            raise ValueError(
                f"analysis_mount references unknown repository: {repository}"
            )
    if mode == "include" and not include:
        raise ValueError("analysis_mount.mode=include requires non-empty include")
    if mode == "exclude" and not exclude:
        raise ValueError("analysis_mount.mode=exclude requires non-empty exclude")
    return AnalysisMount(mode=mode, include=include, exclude=exclude)


def _parse_worktree_domains(
    repositories: dict[str, Any], repository_list: tuple[str, ...]
) -> tuple[WorktreeDomain, ...]:
    raw = repositories.get("worktree_domains", [])
    if not isinstance(raw, list):
        raise ValueError("repositories.worktree_domains must be a list")
    domains: list[WorktreeDomain] = []
    seen_repositories: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("repositories.worktree_domains entries must be mappings")
        domain_id = _optional_text(item.get("id"))
        baseline = _optional_text(item.get("baseline_repository"))
        members = _repository_tuple(item.get("repositories"), "worktree_domains.repositories")
        if not domain_id or not baseline or not members:
            raise ValueError("worktree_domains entry requires id/baseline_repository/repositories")
        if baseline not in members or baseline not in repository_list:
            raise ValueError("worktree_domains baseline_repository must be a listed member")
        if any(repo not in repository_list for repo in members):
            raise ValueError("worktree_domains references unknown repository")
        if seen_repositories.intersection(members):
            raise ValueError("worktree_domains repositories must not overlap")
        problem_version_repository = _optional_text(
            item.get("problem_version_repository")
        ) or baseline
        if problem_version_repository not in repository_list:
            raise ValueError(
                "worktree_domains problem_version_repository must be a listed repository"
            )
        seen_repositories.update(members)
        domains.append(
            WorktreeDomain(
                domain_id,
                baseline,
                members,
                problem_version_repository,
            )
        )
    return tuple(domains)


def _parse_branch_derivation(
    repositories: dict[str, Any], repository_list: tuple[str, ...]
) -> BranchDerivation:
    raw = repositories.get("branches", {})
    if not raw:
        return BranchDerivation()
    if not isinstance(raw, dict):
        raise ValueError("repositories.branches must be a mapping")
    derive_from = str(raw.get("derive_from", "default"))
    default_branch = str(raw.get("default_branch", "main")).strip() or "main"
    default_rule = str(raw.get("default_rule", "same_name"))
    if default_rule != "same_name":
        raise ValueError("repositories.branches.default_rule only supports same_name")
    if derive_from != "default" and (
        repository_list and derive_from not in repository_list
    ):
        raise ValueError(
            f"branches.derive_from references unknown repository: {derive_from}"
        )
    overrides: list[RepositoryBranchRule] = []
    for raw_rule in raw.get("overrides", []):
        if not isinstance(raw_rule, dict):
            raise ValueError("branches.overrides entries must be mappings")
        from_branch = _optional_text(raw_rule.get("from_branch"))
        repo = _optional_text(raw_rule.get("repo"))
        branch = _optional_text(raw_rule.get("branch"))
        if not from_branch or not repo or not branch:
            raise ValueError("branches.overrides entry requires from_branch/repo/branch")
        if repository_list and repo not in repository_list:
            raise ValueError(
                f"branches.overrides references unknown repository: {repo}"
            )
        overrides.append(
            RepositoryBranchRule(from_branch=from_branch, repo=repo, branch=branch)
        )
    dev_branches_raw = raw.get("dev_branches", {})
    dev_branches: list[tuple[str, str]] = []
    if dev_branches_raw:
        if not isinstance(dev_branches_raw, dict):
            raise ValueError("branches.dev_branches must be a mapping")
        for repo, branch in dev_branches_raw.items():
            branch_text = _optional_text(branch)
            if not branch_text:
                raise ValueError(
                    f"branches.dev_branches entry requires a branch: {repo}"
                )
            if repository_list and repo not in repository_list:
                raise ValueError(
                    f"branches.dev_branches references unknown repository: {repo}"
                )
            dev_branches.append((str(repo), branch_text))
    baseline_branches_raw = raw.get("baseline_branches", {})
    baseline_branches: list[tuple[str, str]] = []
    if baseline_branches_raw:
        if not isinstance(baseline_branches_raw, dict):
            raise ValueError("branches.baseline_branches must be a mapping")
        for repo, branch in baseline_branches_raw.items():
            branch_text = _optional_text(branch)
            if not branch_text:
                raise ValueError(f"branches.baseline_branches entry requires a branch: {repo}")
            if repository_list and repo not in repository_list:
                raise ValueError(f"branches.baseline_branches references unknown repository: {repo}")
            baseline_branches.append((str(repo), branch_text))
    return BranchDerivation(
        derive_from=derive_from,
        default_branch=default_branch,
        default_rule=default_rule,
        dev_branches=tuple(dev_branches),
        baseline_branches=tuple(baseline_branches),
        overrides=tuple(overrides),
    )


def _repository_tuple(value: Any, label: str) -> tuple[str, ...]:
    if not value:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"repositories.{label} must be a list of repository strings")
    return tuple(item.strip() for item in value)


def resolve_source_pool_root(install_root: Path) -> Path | None:
    """研发员级配置 source_pool_root：~/.agentic-ops/user/config.yaml。

    未配置返回 None（由调用方按 source_pool_root_invalid 阻断）。
    """
    config_path = install_root / "user" / "config.yaml"
    try:
        content = read_managed_text(
            config_path,
            label="研发员级配置 config.yaml",
            allow_missing=True,
            max_bytes=1024 * 1024,
        )
    except RuntimeErrorResult:
        raise
    except (OSError, ValueError) as error:
        raise RuntimeErrorResult(
            code="configuration_invalid",
            message=f"研发员级配置无法读取：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复 ~/.agentic-ops/user/config.yaml 后重试",
        ) from error
    if content is None:
        return None
    try:
        payload = yaml.safe_load(content) or {}
    except yaml.YAMLError as error:
        raise RuntimeErrorResult(
            code="configuration_invalid",
            message=f"研发员级配置语法无效：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复 ~/.agentic-ops/user/config.yaml 后重试",
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeErrorResult(
            code="configuration_invalid",
            message="研发员级配置根必须是映射",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复 ~/.agentic-ops/user/config.yaml 后重试",
        )
    value = payload.get("source_pool_root")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value.strip()).expanduser()


def _workspace_config_path(workspace_root: Path, directory: str, name: str) -> Path:
    return validate_workspace_managed_path(
        workspace_root,
        workspace_root / ".agentic-ops" / directory / name,
    )


def _required_text(payload: dict[str, Any], key: str, code: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise RuntimeErrorResult(
        code=code,
        message=f"配置缺少必填字段：{key}",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请补齐配置后重试",
    )


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _configuration_error(label: str, error: Exception) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="configuration_invalid",
        message=f"{label} 配置无效：{error}",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请修复配置字段和映射后重试",
    )
