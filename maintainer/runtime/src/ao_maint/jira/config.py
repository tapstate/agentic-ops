from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ao_maint.jira.client import JiraConnection
from ao_maint.locking import TaskLock
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

CONFIG_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

MAINTAINER_CONNECTIONS_DIR = "standards/connections"
MAINTAINER_LOCAL_DIR = ".local"
MAINTAINER_ENV_FILE = ".env"
MAINTAINER_PLANS_DIR = "jira-plans"


@dataclass(frozen=True)
class MaintainerJiraConfig:
    connection: JiraConnection
    email: str | None
    token: str | None
    credential_source: str

    def credential_status(self) -> dict[str, bool]:
        return {
            "email_configured": bool(self.email),
            "token_configured": bool(self.token),
        }

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
            message=f"维护工作区 Jira 凭证未配置：{', '.join(missing)}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                "请先运行 maintainer/bin/init-maintainer-config.sh "
                "或 ao-maint jira auth set 配置维护者 Jira 账户"
            ),
        )


def local_root(source_root: Path) -> Path:
    return source_root / "maintainer" / MAINTAINER_LOCAL_DIR


def env_file_path(source_root: Path) -> Path:
    return local_root(source_root) / MAINTAINER_ENV_FILE


def plans_dir(source_root: Path) -> Path:
    return local_root(source_root) / MAINTAINER_PLANS_DIR


def load_maintainer_jira_config(
    source_root: Path,
    connection_id: str = "tapdata-cloud",
) -> MaintainerJiraConfig:
    connection = load_maintainer_connection(source_root, connection_id)
    email, token, credential_source = _resolve_credential_pair(
        connection.email_env,
        connection.token_env,
        env_file_path(source_root),
    )
    return MaintainerJiraConfig(
        connection=connection,
        email=email,
        token=token,
        credential_source=credential_source,
    )


def load_maintainer_connection(
    source_root: Path,
    connection_id: str,
) -> JiraConnection:
    connection_id = _validated_config_id(connection_id, "Jira Connection")
    path = (
        source_root / "maintainer" / MAINTAINER_CONNECTIONS_DIR / f"{connection_id}.yaml"
    )
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeErrorResult(
            code="jira_connection_not_found",
            message=f"maintainer Jira Connection 无法读取：{path}（{error}）",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请先运行 maintainer/bin/init-maintainer-config.sh 初始化维护配置",
        ) from error
    if not isinstance(content, dict):
        raise _invalid_connection("Connection 必须是映射")
    return _parse_connection(content, connection_id)


def set_credentials(
    source_root: Path,
    connection: JiraConnection,
    *,
    email: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    env_path = env_file_path(source_root)
    updates: dict[str, str | None] = {}
    if email is not None:
        normalized_email = email.strip()
        if not EMAIL_PATTERN.fullmatch(normalized_email):
            raise _input_error("authorization_email_invalid", "Jira email 格式无效")
        updates[connection.email_env] = normalized_email
    if token is not None:
        if len(token.strip()) < 8:
            raise _input_error("authorization_token_invalid", "Jira token 长度明显不合理")
        updates[connection.token_env] = token.strip()
    if not updates:
        raise _input_error(
            "authorization_no_change", "没有提供需要设置或修改的授权字段"
        )
    with TaskLock(env_path.parent / ".authorization.lock", timeout=5):
        _update_env_file(env_path, updates)
    status = _effective_status(connection, env_path)
    return {
        **status,
        "updated_fields": sorted(
            "email" if name == connection.email_env else "token" for name in updates
        ),
    }


def remove_credentials(
    source_root: Path,
    connection: JiraConnection,
    field: str,
) -> dict[str, Any]:
    env_path = env_file_path(source_root)
    updates: dict[str, str | None] = {}
    if field in {"email", "all"}:
        updates[connection.email_env] = None
    if field in {"token", "all"}:
        updates[connection.token_env] = None
    with TaskLock(env_path.parent / ".authorization.lock", timeout=5):
        _update_env_file(env_path, updates)
    status = _effective_status(connection, env_path)
    return {
        **status,
        "removed_fields": [field] if field != "all" else ["email", "token"],
    }


def credential_status(source_root: Path, connection: JiraConnection) -> dict[str, Any]:
    return _effective_status(connection, env_file_path(source_root))


def _effective_status(
    connection: JiraConnection, env_path: Path
) -> dict[str, Any]:
    email, token, credential_source = _resolve_credential_pair(
        connection.email_env,
        connection.token_env,
        env_path,
    )
    return {
        "connection_id": connection.connection_id,
        "base_url": connection.base_url,
        "email_configured": email is not None,
        "token_configured": token is not None,
        "credential_source": credential_source,
        "email_hint": _mask_email(email),
        "ready": email is not None and token is not None,
    }


def _resolve_credential_pair(
    email_env: str, token_env: str, env_path: Path
) -> tuple[str | None, str | None, str]:
    values = _read_env_file(env_path)
    file_email = values.get(email_env, "").strip()
    file_token = values.get(token_env, "").strip()
    if file_email or file_token:
        return file_email or None, file_token or None, "maintainer_local"
    return None, None, "missing"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists() or path.is_symlink():
        return {}
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeErrorResult(
            code="maintainer_env_invalid",
            message="维护凭证文件必须是单链接普通文件",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请删除异常凭证文件后重新配置",
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeErrorResult(
            code="maintainer_env_invalid",
            message=f"维护凭证文件无法读取：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请检查文件权限后重新配置",
        ) from error
    result: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not name.strip():
            continue
        cleaned = value.strip()
        if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
            cleaned = cleaned[1:-1]
        result[name.strip()] = cleaned
    return result


def _update_env_file(path: Path, updates: dict[str, str | None]) -> None:
    for name, value in updates.items():
        if not ENV_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"invalid environment variable name: {name}")
        if value is not None and any(character in value for character in ("\n", "\r", "\x00")):
            raise ValueError(f"environment variable {name} contains an invalid control character")

    existing = _read_env_file(path)
    output: list[str] = []
    for name in sorted(set(existing) | set(updates)):
        if name in updates:
            value = updates[name]
            if value is not None:
                output.append(f"{name}={value}")
        elif name in existing:
            output.append(f"{name}={existing[name]}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = _mkstemp(path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output))
            if output:
                stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _mkstemp(path: Path) -> tuple[int, str]:
    import tempfile

    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    return descriptor, temporary


def _parse_connection(payload: dict[str, Any], expected_id: str) -> JiraConnection:
    actual_id = str(payload.get("connection_id", "")).strip()
    if actual_id != expected_id:
        raise _invalid_connection(
            f"connection_id {actual_id!r} does not match {expected_id!r}"
        )
    auth = payload.get("auth", {})
    if not isinstance(auth, dict):
        raise _invalid_connection("auth 必须是映射")
    base_url = str(payload.get("base_url", "")).strip()
    email_env = str(auth.get("email_env", "")).strip()
    token_env = str(auth.get("token_env", "")).strip()
    if not base_url.startswith("https://"):
        raise _invalid_connection("base_url 必须是 HTTPS 站点")
    if not ENV_NAME_PATTERN.fullmatch(email_env) or not ENV_NAME_PATTERN.fullmatch(token_env):
        raise _invalid_connection("auth env 名称必须是合法大写环境变量名")
    if email_env == token_env:
        raise _invalid_connection("email_env 与 token_env 不能相同")
    try:
        timeout_seconds = float(payload.get("timeout_seconds", 20.0))
    except (TypeError, ValueError) as error:
        raise _invalid_connection("timeout_seconds 无效") from error
    if not 0 < timeout_seconds <= 300:
        raise _invalid_connection("timeout_seconds 必须在 (0, 300] 内")
    return JiraConnection(
        connection_id=actual_id,
        base_url=base_url.rstrip("/"),
        email_env=email_env,
        token_env=token_env,
        timeout_seconds=timeout_seconds,
    )


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


def _invalid_connection(message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_connection_invalid",
        message=f"maintainer Jira Connection 无效：{message}",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请修复 maintainer/standards/connections/ 下的 Connection 定义",
    )


def _mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(len(local) - len(visible), 1)}@{domain}"


def _input_error(code: str, message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action="请修正输入后重试",
    )
