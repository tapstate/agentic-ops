from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agentic_ops.config.env import resolve_secret
from agentic_ops.config.model import (
    FIELD_STATES,
    FieldMapping,
    JiraConnection,
    ProjectProfile,
    require_mapping,
)
from agentic_ops.output import EXIT_BLOCKED, RuntimeErrorResult
from agentic_ops.workspace import Workspace


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
            required_human_action="请在用户或项目 .env 中配置对应变量，不要把凭证提交到仓库",
        )


def default_install_root() -> Path:
    return Path(__file__).resolve().parents[4]


def load_jira_context(workspace: Workspace, install_root: Path) -> JiraContext:
    agent = _load_agent_config(workspace)
    profile_id = _required_text(agent, "project_profile", "workspace_project_profile_missing")
    bound_connection_id = _required_text(agent, "connection_id", "workspace_connection_missing")

    profile_payload = _load_layered_yaml(
        [
            install_root / "standards" / "projects" / profile_id / "profile.yaml",
            install_root / "user" / "projects" / profile_id / "profile.local.yaml",
            workspace.root / ".agentic-ops" / "profiles" / f"{profile_id}.local.yaml",
        ],
        "project_profile_not_found",
    )
    try:
        profile = _parse_profile(profile_payload, profile_id)
    except (TypeError, ValueError) as error:
        raise _configuration_error("Project Profile", error) from error
    if profile.connection_id != bound_connection_id:
        raise RuntimeErrorResult(
            code="jira_workspace_mismatch",
            message=(
                f"工作空间 connection_id={bound_connection_id} 与项目 Profile "
                f"connection_id={profile.connection_id} 不一致"
            ),
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请核对项目工作空间与 Jira Profile 绑定后重试",
        )

    connection = load_jira_connection(
        install_root,
        bound_connection_id,
        workspace_root=workspace.root,
    )
    env_paths = [workspace.root / ".agentic-ops" / ".env", install_root / "user" / ".env"]
    return JiraContext(
        connection=connection,
        profile=profile,
        email=resolve_secret(connection.email_env, env_paths),
        token=resolve_secret(connection.token_env, env_paths),
    )


def load_jira_connection(
    install_root: Path,
    connection_id: str,
    *,
    workspace_root: Path | None = None,
) -> JiraConnection:
    paths = [
        install_root / "standards" / "connections" / f"{connection_id}.yaml",
        install_root / "user" / "connections" / f"{connection_id}.local.yaml",
    ]
    if workspace_root is not None:
        paths.append(
            workspace_root / ".agentic-ops" / "connections" / f"{connection_id}.local.yaml"
        )
    connection_payload = _load_layered_yaml(paths, "jira_connection_not_found")
    try:
        return _parse_connection(connection_payload, connection_id)
    except (TypeError, ValueError) as error:
        raise _configuration_error("Jira Connection", error) from error


def list_jira_connections(install_root: Path) -> list[str]:
    connection_ids: set[str] = set()
    for directory in (
        install_root / "standards" / "connections",
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


def _load_agent_config(workspace: Workspace) -> dict[str, Any]:
    if workspace.config_path is None:
        raise RuntimeErrorResult(
            code="workspace_config_missing",
            message="Jira 操作要求项目工作空间提供 .agentic-ops/agent.json",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请初始化业务项目 AI 工作空间并配置 Jira 绑定",
        )
    try:
        payload = json.loads(workspace.config_path.read_text(encoding="utf-8"))
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
        if not path.is_file():
            continue
        used = True
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
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
    return JiraConnection(
        connection_id=actual_id,
        base_url=_required_text(payload, "base_url", "jira_connection_invalid").rstrip("/"),
        email_env=_required_text(auth, "email_env", "jira_connection_invalid"),
        token_env=_required_text(auth, "token_env", "jira_connection_invalid"),
        timeout_seconds=float(payload.get("timeout_seconds", 20.0)),
    )


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
