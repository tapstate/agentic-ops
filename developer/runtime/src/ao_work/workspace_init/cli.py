from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from ao_work.config import list_project_profiles
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.io import read_json
from ao_work.workspace import Workspace
from ao_work.workspace_init.service import (
    WorkspaceCandidate,
    WorkspaceInitializer,
    mask_email,
    normalize_agent_id,
)


def configure_workspace_init_parser(
    workspace_commands: argparse._SubParsersAction[Any],
) -> None:
    init = workspace_commands.add_parser("init")
    init.add_argument("--project", dest="project_profile")
    init.add_argument("--agent-id")
    init.add_argument("--source-root")
    init.add_argument("--jira-email")
    init.add_argument("--token-stdin", action="store_true")
    init.add_argument("--non-interactive", action="store_true")
    init.add_argument("--confirm", action="store_true")
    init.add_argument("--confirm-existing-config", action="store_true")

    workspace_commands.add_parser("preflight")


def execute_workspace_init(
    args: argparse.Namespace,
    workspace_root: str,
    install_root: Path,
) -> dict[str, Any]:
    root = Path(workspace_root).expanduser().resolve()
    initializer = WorkspaceInitializer(root, install_root)
    interactive = not args.non_interactive
    if interactive and not sys.stdin.isatty():
        raise _blocked(
            "interactive_terminal_required",
            "零参数工作空间初始化需要终端输入",
            "请在终端运行 ao-work workspace init，或使用完整非交互参数",
        )

    profiles = list_project_profiles(install_root)
    profile_id = args.project_profile
    if interactive:
        default_profile = _default_profile(root, profiles, profile_id)
        profile_id = _prompt_required(
            f"Project Profile（可选：{', '.join(profiles)}）",
            default_profile,
        )
    elif not profile_id:
        raise _blocked(
            "missing_project_profile",
            "非交互初始化缺少 --project",
            "请明确提供 --project 和 --agent-id，并使用 --confirm",
        )
    assert profile_id is not None

    agent_id = args.agent_id
    if interactive:
        agent_id = _prompt_required("agent_id", agent_id or normalize_agent_id())
    elif not agent_id:
        raise _blocked(
            "missing_agent_id",
            "非交互初始化缺少 --agent-id",
            "请明确提供 --agent-id",
        )
    assert agent_id is not None

    credentials = _stdin_credentials(args)
    candidate = initializer.prepare(
        profile_id,
        agent_id,
        source_root=args.source_root,
        credentials=credentials,
        persist_credentials=credentials is not None,
        allow_rebind=True,
    )
    if interactive and (not candidate.email or not candidate.token):
        credentials = _prompt_credentials(candidate)
        candidate = initializer.prepare(
            profile_id,
            agent_id,
            source_root=args.source_root,
            credentials=credentials,
            persist_credentials=True,
            allow_rebind=True,
        )

    confirmed = args.confirm
    if interactive:
        _write_summary(candidate)
        confirmed = _prompt_confirmation("确认使用以上信息初始化业务项目工作空间")
    if not confirmed:
        raise _blocked(
            "workspace_init_confirmation_required",
            "工作空间初始化摘要尚未确认",
            "请核对 agent_id、Jira 项目空间、授权账户和源码仓库后确认",
        )

    confirm_existing = args.confirm_existing_config
    try:
        preflight = initializer.preflight(
            candidate,
            confirm_existing_config=confirm_existing,
            check_remote=True,
        )
    except RuntimeErrorResult as error:
        if (
            interactive
            and error.code == "existing_config_confirmation_required"
            and _prompt_confirmation("已有不同完整配置，确认覆盖")
        ):
            preflight = initializer.preflight(
                candidate,
                confirm_existing_config=True,
                check_remote=True,
            )
        else:
            raise
    result = initializer.apply(candidate, preflight)
    post_preflight = initializer.preflight(
        candidate,
        confirm_existing_config=True,
        check_remote=False,
    )
    return {**result, "post_preflight_status": post_preflight["status"]}


def execute_workspace_preflight(
    workspace: Workspace,
    install_root: Path,
) -> dict[str, Any]:
    if workspace.config_path is None:
        raise _blocked(
            "workspace_config_missing",
            "业务项目工作空间尚未初始化",
            "请先运行 ao-work workspace init",
        )
    agent = read_json(workspace.config_path)
    profile_id = _required_agent_value(agent, "project_profile")
    agent_id = _required_agent_value(agent, "agent_id")
    source_root = _required_agent_value(agent, "source_root")
    initializer = WorkspaceInitializer(workspace.root, install_root)
    candidate = initializer.prepare(profile_id, agent_id, source_root=source_root)
    result = initializer.preflight(
        candidate,
        confirm_existing_config=False,
        check_remote=True,
    )
    return {**candidate.summary(), **result}


def _stdin_credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    if not args.token_stdin and args.jira_email is None:
        return None
    if not args.token_stdin or not args.jira_email:
        raise _blocked(
            "jira_credential_pair_required",
            "非交互授权必须同时提供 --jira-email 和 --token-stdin",
            "请从标准输入传入 token，并明确提供同一账户的 Jira email",
        )
    token = sys.stdin.readline().rstrip("\r\n")
    if not token:
        raise _blocked(
            "authorization_token_empty",
            "标准输入中的 Jira token 为空",
            "请重新从安全标准输入传入 token",
        )
    return args.jira_email, token


def _prompt_credentials(candidate: WorkspaceCandidate) -> tuple[str, str]:
    email = _prompt_required("Jira email", candidate.email or "")
    token = getpass.getpass("AgenticOps：Jira API token：", stream=sys.stderr).strip()
    if not token:
        raise _blocked(
            "authorization_token_empty",
            "Jira API token 不能为空",
            "请重新运行初始化并输入当前 Jira 账户的 API token",
        )
    return email, token


def _default_profile(root: Path, profiles: list[str], explicit: str | None) -> str:
    if explicit:
        return explicit
    if root.name in profiles:
        return root.name
    if len(profiles) == 1:
        return profiles[0]
    return ""


def _prompt_required(label: str, fallback: str) -> str:
    suffix = f" [{fallback}]" if fallback else ""
    sys.stderr.write(f"AgenticOps：{label}{suffix}：")
    sys.stderr.flush()
    value = sys.stdin.readline().strip() or fallback
    if not value:
        raise _blocked(
            "interactive_input_required",
            f"{label} 不能为空",
            "请补齐交互式输入后重试",
        )
    return value


def _prompt_confirmation(label: str) -> bool:
    sys.stderr.write(f"AgenticOps：{label}？[y/N]：")
    sys.stderr.flush()
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _write_summary(candidate: WorkspaceCandidate) -> None:
    summary = candidate.summary()
    summary["jira_account"] = mask_email(candidate.email)
    sys.stderr.write("AgenticOps：初始化摘要\n")
    sys.stderr.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    sys.stderr.flush()


def _required_agent_value(agent: dict[str, Any], key: str) -> str:
    value = agent.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise _blocked(
        "workspace_config_invalid",
        f"agent.json 缺少 {key}",
        "请重新运行 workspace init 修复工作空间配置",
    )


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )
