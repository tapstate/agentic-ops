from __future__ import annotations

import json
from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

import yaml

from ao_work.config.env import ENV_NAME_PATTERN, resolve_secret_pair_with_source
from ao_work.managed_io import read_managed_json, read_managed_text
from ao_work.config.model import (
    FIELD_STATES,
    AnalysisMount,
    BranchDerivation,
    FieldMapping,
    JiraConnection,
    ProjectProfile,
    RepositoryBranchRule,
    require_mapping,
)
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.workspace import Workspace
from ao_work.workspace_security import validate_workspace_managed_path

CONFIG_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


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
            required_human_action="请执行 ao-work auth jira set 配置当前 AgenticOps 研发员账户",
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
    validate_workspace_jira_binding(workspace, connection)
    validate_workspace_project_binding(workspace, profile)
    email, token, _ = resolve_secret_pair_with_source(
        connection.email_env,
        connection.token_env,
        workspace.root / ".agentic-ops" / ".env",
    )
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
) -> dict[str, Any]:
    agent = _load_agent_config(workspace)
    if agent.get("schema_version") != 3:
        raise RuntimeErrorResult(
            code="workspace_jira_identity_upgrade_required",
            message="工作空间尚未固化已验证 Jira 站点与账户身份",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 ao-work workspace init，通过 Jira 授权检查后升级工作空间",
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
    configured_account = agent.get("jira_account_id")
    if not isinstance(configured_account, str) or not configured_account.strip():
        raise RuntimeErrorResult(
            code="workspace_jira_identity_upgrade_required",
            message="工作空间缺少已验证 Jira accountId",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 ao-work workspace init 完成账户验证",
        )
    if account_id is not None and configured_account != account_id:
        raise RuntimeErrorResult(
            code="jira_workspace_account_drift",
            message="Jira 当前登录 accountId 与工作空间固化账户不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请停止操作并重新授权该业务项目研发员账户",
        )
    return agent


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
    transition_mapping = {
        str(key): {str(inner_key): str(inner_value) for inner_key, inner_value in require_mapping(value, str(key)).items()}
        for key, value in require_mapping(payload.get("transitions", {}), "transitions").items()
    }
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


def _parse_branch_derivation(
    repositories: dict[str, Any], repository_list: tuple[str, ...]
) -> BranchDerivation:
    raw = repositories.get("branches", {})
    if not raw:
        return BranchDerivation()
    if not isinstance(raw, dict):
        raise ValueError("repositories.branches must be a mapping")
    derive_from = str(raw.get("derive_from", "default"))
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
    return BranchDerivation(
        derive_from=derive_from,
        default_rule=default_rule,
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
