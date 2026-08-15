from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path
from typing import Any

from ao_work.config import (
    list_jira_connections,
    load_jira_connection,
    resolve_workspace_connection_id,
    validate_workspace_jira_binding,
)
from ao_work.config.env import (
    read_env_file,
    resolve_secret_pair_with_source,
    update_workspace_env_file,
)
from ao_work.config.model import ProjectProfile
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.locking import TaskLock
from ao_work.workspace import Workspace

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def configure_authorization_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    auth_parser = subparsers.add_parser("auth")
    auth_systems = auth_parser.add_subparsers(dest="command", required=True)
    jira_parser = auth_systems.add_parser("jira")
    actions = jira_parser.add_subparsers(dest="action", required=True)

    actions.add_parser("list")

    show = actions.add_parser("show")
    _connection_argument(show)

    set_parser = actions.add_parser("set")
    _connection_argument(set_parser)
    set_parser.add_argument("--email")
    set_parser.add_argument("--token-stdin", action="store_true")
    set_parser.add_argument("--interactive", action="store_true")

    remove = actions.add_parser("remove")
    _connection_argument(remove)
    remove.add_argument("--field", choices=("email", "token", "all"), required=True)

    verify = actions.add_parser("verify")
    _connection_argument(verify)


def execute_authorization(
    args: argparse.Namespace,
    workspace: Workspace,
    install_root: Path,
) -> dict[str, Any]:
    if args.action == "list":
        connections = list_jira_connections(install_root)
        return {"connections": connections, "count": len(connections)}

    connection_id = resolve_workspace_connection_id(
        workspace,
        install_root,
        getattr(args, "connection_id", None),
    )
    connection = load_jira_connection(
        install_root,
        connection_id,
        workspace_root=workspace.root,
    )
    validate_workspace_jira_binding(workspace, connection)
    if args.action == "show":
        return _show(connection, workspace)
    if args.action == "set":
        return _set(connection, workspace, args)
    if args.action == "remove":
        return _remove(connection, workspace, args.field)
    if args.action == "verify":
        status = _effective_status(connection, workspace)
        email, token = _require_effective_credentials(status, connection)
        client = JiraClient(
            ProjectProfile(
                profile_id="authorization-verification",
                connection_id=connection.connection_id,
                project_key="AUTH",
                task_query="",
            ),
            UrllibJiraTransport(connection, email, token),
        )
        current_user = client.current_user()
        validate_workspace_jira_binding(
            workspace,
            connection,
            account_id=current_user,
        )
        fields = client.field_metadata()
        return {
            "connection_id": connection.connection_id,
            "base_url": connection.base_url,
            "verified": True,
            "jira_user": current_user,
            "field_count": len(fields),
            "account_scope": "workspace",
            "credential_source": status["credential_source"],
        }
    raise RuntimeErrorResult(
        code="authorization_action_unsupported",
        message="不支持的授权操作",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请使用 auth jira list、show、set、remove 或 verify",
    )


def _connection_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--connection-id", help=argparse.SUPPRESS)


def _show(connection: Any, workspace: Workspace) -> dict[str, Any]:
    status = _effective_status(connection, workspace)
    return {
        "connection_id": connection.connection_id,
        "base_url": connection.base_url,
        "account_scope": "workspace",
        "email_configured": status["email"] is not None,
        "token_configured": status["token"] is not None,
        "credential_source": status["credential_source"],
        "email_hint": _mask_email(status["email"]),
        "ready": status["email"] is not None and status["token"] is not None,
    }


def _set(
    connection: Any,
    workspace: Workspace,
    args: argparse.Namespace,
) -> dict[str, Any]:
    env_path = workspace.root / ".agentic-ops" / ".env"
    current = read_env_file(env_path)
    interactive = args.interactive or (
        args.email is None and not args.token_stdin and sys.stdin.isatty()
    )
    email = args.email
    token: str | None = None
    if interactive:
        current_email = current.get(connection.email_env, "")
        sys.stderr.write(
            f"AgenticOps：Jira email [{_mask_email(current_email) or '未设置'}]，留空表示保留： "
        )
        sys.stderr.flush()
        entered_email = sys.stdin.readline().strip()
        email = entered_email or None
        entered_token = getpass.getpass(
            "AgenticOps：Jira API token（留空表示保留）： ", stream=sys.stderr
        )
        token = entered_token or None
    elif args.token_stdin:
        token = sys.stdin.readline().rstrip("\r\n")
        if not token:
            raise _input_error("authorization_token_empty", "标准输入中的 Jira token 为空")

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
            "authorization_no_change",
            "没有提供需要设置或修改的授权字段",
        )

    with TaskLock(env_path.parent / ".authorization.lock", timeout=5):
        credential_protection = update_workspace_env_file(workspace.root, updates)
    status = _show(connection, workspace)
    return {
        **status,
        "credential_protection": credential_protection,
        "updated_fields": sorted(
            "email" if name == connection.email_env else "token" for name in updates
        ),
        "next_action": "auth_jira_verify" if status["ready"] else "auth_jira_set",
    }


def _remove(
    connection: Any,
    workspace: Workspace,
    field: str,
) -> dict[str, Any]:
    env_path = workspace.root / ".agentic-ops" / ".env"
    updates: dict[str, str | None] = {}
    if field in {"email", "all"}:
        updates[connection.email_env] = None
    if field in {"token", "all"}:
        updates[connection.token_env] = None
    with TaskLock(env_path.parent / ".authorization.lock", timeout=5):
        credential_protection = update_workspace_env_file(workspace.root, updates)
    status = _show(connection, workspace)
    return {
        **status,
        "credential_protection": credential_protection,
        "removed_fields": [field] if field != "all" else ["email", "token"],
    }


def _effective_status(connection: Any, workspace: Workspace) -> dict[str, Any]:
    email, token, credential_source = resolve_secret_pair_with_source(
        connection.email_env,
        connection.token_env,
        workspace.root / ".agentic-ops" / ".env",
    )
    return {
        "email": email,
        "token": token,
        "credential_source": credential_source,
    }


def _require_effective_credentials(
    status: dict[str, Any], connection: Any
) -> tuple[str, str]:
    missing = []
    if not status["email"]:
        missing.append(connection.email_env)
    if not status["token"]:
        missing.append(connection.token_env)
    if missing:
        raise RuntimeErrorResult(
            code="jira_credentials_missing",
            message=f"Jira 授权尚未配置完整：{', '.join(missing)}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请在当前工作空间执行 ao-work auth jira set",
        )
    return str(status["email"]), str(status["token"])


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
        required_human_action="请修正授权输入后重试",
    )
