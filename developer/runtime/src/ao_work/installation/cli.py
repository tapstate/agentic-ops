from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Any

from ao_work.config import load_jira_connection
from ao_work.installation import (
    load_install_credentials,
    load_install_identity,
    save_install_credentials,
    save_install_identity,
    install_user_dir,
)
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult


def _workspace_init_helpers() -> tuple[Any, Any, Any]:
    """延迟导入避免与 workspace_init.service 循环依赖。"""
    from ao_work.workspace_init.service import (
        build_execution_identity,
        mask_email,
        validate_agent_id,
    )

    return build_execution_identity, mask_email, validate_agent_id


def configure_install_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    install = subparsers.add_parser("install")
    install_commands = install.add_subparsers(dest="command", required=True)

    identity = install_commands.add_parser("identity")
    identity_actions = identity.add_subparsers(dest="action", required=True)
    identity_actions.add_parser("show")
    identity_set = identity_actions.add_parser("set")
    identity_set.add_argument("--agent-id")
    identity_set.add_argument("--git-name")
    identity_set.add_argument("--git-email")
    identity_set.add_argument("--github-login")
    identity_set.add_argument("--jira-email")
    identity_set.add_argument("--jira-token-stdin", action="store_true")
    identity_set.add_argument("--non-interactive", action="store_true")
    identity_actions.add_parser("remove")

    auth = install_commands.add_parser("auth")
    auth_actions = auth.add_subparsers(dest="action", required=True)
    auth_actions.add_parser("show")
    auth_set = auth_actions.add_parser("set")
    auth_set.add_argument("--jira-email")
    auth_set.add_argument("--token-stdin", action="store_true")
    auth_set.add_argument("--non-interactive", action="store_true")
    auth_actions.add_parser("remove")


def execute_install(
    args: argparse.Namespace,
    install_root: Path,
) -> dict[str, Any]:
    if args.command == "identity":
        return _execute_identity(args, install_root)
    if args.command == "auth":
        return _execute_auth(args, install_root)
    raise _blocked(
        "install_command_unknown",
        f"未知 install 子命令：{args.command}",
        "请使用 ao-work install identity|auth",
    )


def _execute_identity(args: argparse.Namespace, install_root: Path) -> dict[str, Any]:
    _, mask_email, _ = _workspace_init_helpers()
    install_user_dir(install_root)
    if args.action == "show":
        try:
            identity = load_install_identity(install_root)
        except RuntimeErrorResult as error:
            if error.code == "install_identity_missing":
                return {
                    "configured": False,
                    "user_dir": str(install_user_dir(install_root)),
                }
            raise
        credentials = load_install_credentials(install_root)
        return {
            "configured": True,
            "agent_id": identity["agent_id"],
            "jira_email": mask_email(identity["jira_email"]),
            "execution_identity": {
                "git_author_name": identity["execution_identity"]["git_author_name"],
                "git_author_email": mask_email(identity["execution_identity"]["git_author_email"]),
                "github_actor_login": identity["execution_identity"]["github_actor_login"],
            },
            "jira_credentials_configured": credentials is not None,
        }
    if args.action == "remove":
        user_dir = install_user_dir(install_root)
        identity_path = user_dir / "identity.yaml"
        env_path = user_dir / ".env"
        if identity_path.exists():
            identity_path.unlink()
        if env_path.exists():
            env_path.unlink()
        return {"removed": True, "user_dir": str(user_dir)}
    if args.action == "set":
        return _set_identity(args, install_root)
    raise _blocked(
        "install_command_unknown",
        f"未知 identity 动作：{args.action}",
        "请使用 ao-work install identity show|set|remove",
    )


def _set_identity(args: argparse.Namespace, install_root: Path) -> dict[str, Any]:
    build_execution_identity, mask_email, validate_agent_id = _workspace_init_helpers()
    interactive = not args.non_interactive
    if interactive and not sys.stdin.isatty():
        raise _blocked(
            "interactive_terminal_required",
            "零参数身份配置需要终端输入",
            "请使用完整非交互参数或直接在终端运行",
        )
    existing: dict[str, Any] = {}
    try:
        existing = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        if error.code != "install_identity_missing":
            raise
    agent_id = args.agent_id or existing.get("agent_id") or ""
    jira_email = args.jira_email or existing.get("jira_email") or ""
    git_name = args.git_name or existing.get("execution_identity", {}).get("git_author_name") or ""
    git_email = args.git_email or existing.get("execution_identity", {}).get("git_author_email") or ""
    github_login = args.github_login or existing.get("execution_identity", {}).get("github_actor_login") or ""
    if interactive:
        agent_id = _prompt_required("agent_id（研发员标识，安装级唯一）", agent_id)
        jira_email = _prompt_required("Jira email", jira_email)
        git_name = _prompt_required("Git author/committer name", git_name)
        git_email = _prompt_required("Git email", git_email)
        github_login = _prompt_required("GitHub login", github_login)
    if not all((agent_id, jira_email, git_name, git_email, github_login)):
        raise _blocked(
            "install_identity_incomplete",
            "安装目录身份配置缺少必填字段",
            "请补齐 agent_id、Jira email、Git 身份与 GitHub login",
        )
    validate_agent_id(agent_id)
    execution_identity = build_execution_identity(git_name, git_email, github_login)
    save_install_identity(
        install_root,
        {
            "agent_id": agent_id,
            "jira_email": jira_email,
            "execution_identity": execution_identity,
        },
    )
    token = None
    if args.jira_token_stdin:
        token = sys.stdin.readline().rstrip("\r\n")
    elif interactive:
        token = getpass.getpass("AgenticOps：Jira API token（可留空稍后配置）：", stream=sys.stderr).strip()
    if token:
        save_install_credentials(install_root, jira_email, token)
    return {
        "configured": True,
        "agent_id": agent_id,
        "jira_email": mask_email(jira_email),
        "execution_identity": {
            "git_author_name": execution_identity["git_author_name"],
            "git_author_email": mask_email(execution_identity["git_author_email"]),
            "github_actor_login": execution_identity["github_actor_login"],
        },
        "jira_credentials_configured": bool(token),
    }


def _execute_auth(args: argparse.Namespace, install_root: Path) -> dict[str, Any]:
    _, mask_email, _ = _workspace_init_helpers()
    install_user_dir(install_root)
    if args.action == "show":
        identity = None
        try:
            identity = load_install_identity(install_root)
        except RuntimeErrorResult as error:
            if error.code != "install_identity_missing":
                raise
        credentials = load_install_credentials(install_root)
        return {
            "configured": credentials is not None,
            "jira_email": mask_email(identity["jira_email"]) if identity else None,
            "jira_credentials_configured": credentials is not None,
        }
    if args.action == "remove":
        env_path = install_user_dir(install_root) / ".env"
        if env_path.exists():
            env_path.unlink()
        return {"removed": True}
    if args.action == "set":
        return _set_auth(args, install_root)
    raise _blocked(
        "install_command_unknown",
        f"未知 auth 动作：{args.action}",
        "请使用 ao-work install auth show|set|remove",
    )


def _set_auth(args: argparse.Namespace, install_root: Path) -> dict[str, Any]:
    _, mask_email, _ = _workspace_init_helpers()
    interactive = not args.non_interactive
    identity = None
    try:
        identity = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        if error.code != "install_identity_missing":
            raise
    if identity is None:
        raise _blocked(
            "install_identity_missing",
            "安装目录尚未配置身份，无法配置 Jira 凭证",
            "请先运行 ao-work install identity set 配置研发员身份",
        )
    jira_email = args.jira_email or (identity or {}).get("jira_email") or ""
    if not jira_email:
        raise _blocked(
            "install_identity_incomplete",
            "缺少 Jira email",
            "请提供 --jira-email 或先配置安装目录身份",
        )
    token = None
    if args.token_stdin:
        token = sys.stdin.readline().rstrip("\r\n")
    elif interactive:
        token = getpass.getpass("AgenticOps：Jira API token：", stream=sys.stderr).strip()
    if not token:
        raise _blocked(
            "authorization_token_empty",
            "Jira API token 不能为空",
            "请重新运行并输入当前 Jira 账户的 API token",
        )
    save_install_credentials(install_root, jira_email, token)
    return {"configured": True, "jira_email": mask_email(jira_email)}


def _prompt_required(label: str, default: str = "") -> str:
    if default:
        value = input(f"AgenticOps：{label} [{default}]：").strip() or default
    else:
        value = input(f"AgenticOps：{label}：").strip()
    if not value:
        raise _blocked(
            "install_identity_incomplete",
            f"缺少 {label}",
            "请重新运行并补齐必填字段",
        )
    return value


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )
