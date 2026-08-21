from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path
from typing import Any

from ao_work.installation import (
    build_execution_identity,
    install_user_dir,
    load_install_credentials,
    load_install_identity,
    mask_email,
    save_install_credentials,
    save_install_identity,
    validate_agent_id,
    validate_jira_email,
)
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.locking import TaskLock


def configure_authorization_parser(
    subparsers: argparse._SubParsersAction[Any],
) -> None:
    auth = subparsers.add_parser("auth")
    auth.add_argument("--show", action="store_true")
    auth.add_argument("--agent-id")
    auth.add_argument("--jira-email")
    auth.add_argument("--git-name")
    auth.add_argument("--git-email")
    auth.add_argument("--github-login")
    auth.add_argument("--token-stdin", action="store_true")
    auth.add_argument("--non-interactive", action="store_true")


def execute_authorization(
    args: argparse.Namespace,
    install_root: Path,
) -> dict[str, Any]:
    install_user_dir(install_root)
    if args.show:
        if any(
            (
                args.agent_id,
                args.jira_email,
                args.git_name,
                args.git_email,
                args.github_login,
                args.token_stdin,
                args.non_interactive,
            )
        ):
            raise _blocked(
                "authorization_show_arguments_invalid",
                "--show 不能与授权写入参数同时使用",
                "请单独执行 ao-work auth --show",
            )
        return _show(install_root)
    return _set(args, install_root)


def _show(install_root: Path) -> dict[str, Any]:
    try:
        identity = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        if error.code != "install_identity_missing":
            raise
        return {
            "configured": False,
            "identity_configured": False,
            "jira_credentials_configured": False,
            "user_dir": str(install_user_dir(install_root)),
        }
    credentials = load_install_credentials(install_root)
    return {
        "configured": credentials is not None,
        "identity_configured": True,
        "agent_id": identity["agent_id"],
        "jira_email": mask_email(identity["jira_email"]),
        "execution_identity": {
            "git_author_name": identity["execution_identity"]["git_author_name"],
            "git_author_email": mask_email(
                identity["execution_identity"]["git_author_email"]
            ),
            "github_actor_login": identity["execution_identity"][
                "github_actor_login"
            ],
        },
        "jira_credentials_configured": credentials is not None,
        "authorization_scope": "installation",
    }


def _set(args: argparse.Namespace, install_root: Path) -> dict[str, Any]:
    interactive = not args.non_interactive
    if interactive and not sys.stdin.isatty():
        raise _blocked(
            "interactive_terminal_required",
            "零参数授权配置需要终端输入",
            "请直接在终端运行 ao-work auth，或提供完整非交互参数",
        )

    existing: dict[str, Any] = {}
    try:
        existing = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        if error.code != "install_identity_missing":
            raise

    agent_id = args.agent_id or existing.get("agent_id") or ""
    jira_email = args.jira_email or existing.get("jira_email") or ""
    existing_execution = existing.get("execution_identity", {})
    git_name = args.git_name or existing_execution.get("git_author_name") or ""
    git_email = args.git_email or existing_execution.get("git_author_email") or ""
    github_login = (
        args.github_login or existing_execution.get("github_actor_login") or ""
    )
    if interactive:
        if not agent_id:
            agent_id = _prompt_required("agent_id（研发员标识，安装级唯一）")
        if not jira_email:
            jira_email = _prompt_required("Jira email")
        if not git_name:
            git_name = _prompt_required("Git author/committer name")
        if not git_email:
            git_email = _prompt_required("Git email")
        if not github_login:
            github_login = _prompt_required("GitHub login")
    if not all((agent_id, jira_email, git_name, git_email, github_login)):
        raise _blocked(
            "install_identity_incomplete",
            "安装级授权缺少必填身份字段",
            "请补齐 agent_id、Jira email、Git 身份与 GitHub login",
        )

    normalized_agent_id = validate_agent_id(agent_id)
    normalized_jira_email = validate_jira_email(jira_email)
    execution_identity = build_execution_identity(
        git_name,
        git_email,
        github_login,
    )
    token: str | None = None
    if args.token_stdin:
        token = sys.stdin.readline().rstrip("\r\n")
    elif interactive:
        token = getpass.getpass(
            "AgenticOps：Jira API token：",
            stream=sys.stderr,
        ).strip()
    if not token:
        raise _blocked(
            "authorization_token_empty",
            "Jira API token 不能为空",
            "请重新运行 ao-work auth 并通过隐藏输入或安全标准输入提供 token",
        )
    if len(token.strip()) < 8:
        raise _blocked(
            "authorization_token_invalid",
            "Jira token 长度明显不合理",
            "请重新运行 ao-work auth 并输入当前 Jira 账户的 API token",
        )

    user_dir = install_user_dir(install_root)
    user_dir.mkdir(parents=True, exist_ok=True)
    with TaskLock(user_dir / ".authorization.lock", timeout=5):
        save_install_identity(
            install_root,
            {
                "agent_id": normalized_agent_id,
                "jira_email": normalized_jira_email,
                "execution_identity": execution_identity,
            },
        )
        save_install_credentials(install_root, normalized_jira_email, token.strip())
    return {
        "configured": True,
        "identity_configured": True,
        "agent_id": normalized_agent_id,
        "jira_email": mask_email(normalized_jira_email),
        "execution_identity": {
            "git_author_name": execution_identity["git_author_name"],
            "git_author_email": mask_email(execution_identity["git_author_email"]),
            "github_actor_login": execution_identity["github_actor_login"],
        },
        "jira_credentials_configured": True,
        "authorization_scope": "installation",
    }


def _prompt_required(label: str, default: str = "") -> str:
    if default:
        value = input(f"AgenticOps：{label} [{default}]：").strip() or default
    else:
        value = input(f"AgenticOps：{label}：").strip()
    if not value:
        raise _blocked(
            "install_identity_incomplete",
            f"缺少 {label}",
            "请重新运行 ao-work auth 并补齐必填字段",
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
