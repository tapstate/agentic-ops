from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ao_work.config import (
    list_project_profiles,
    resolve_source_pool_root,
    validate_workspace_jira_binding,
    validate_workspace_project_binding,
)
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult, write_diagnostic
from ao_work.task_state.io import read_json
from ao_work.workspace import Workspace
from ao_work.workspace_init.service import WorkspaceCandidate, WorkspaceInitializer


def configure_workspace_init_parser(
    workspace_commands: argparse._SubParsersAction[Any],
) -> None:
    init = workspace_commands.add_parser("init", allow_abbrev=False)
    init.add_argument("--project", dest="project_profile")
    init.add_argument("--source-root")
    init.add_argument("--source-pool-root")
    init.add_argument("--non-interactive", action="store_true")
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
    if interactive and not profile_id:
        default_profile = _default_profile(root, profiles, profile_id)
        profile_id = _prompt_required(
            f"Project Profile（可选：{', '.join(profiles)}）",
            default_profile,
        )
    elif not profile_id:
        raise _blocked(
            "missing_project_profile",
            "非交互初始化缺少 --project",
            "请明确提供 --project",
        )
    assert profile_id is not None

    candidate = initializer.prepare(
        profile_id,
        source_root=args.source_root,
        source_pool_root=args.source_pool_root,
        allow_rebind=True,
    )

    if interactive and not args.project_profile:
        _write_summary(candidate)

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
            and _prompt_existing_config_overwrite(error)
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
    write_diagnostic("初始化完成：业务项目工作空间已就绪")
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
    source_root = _required_agent_value(agent, "source_root")
    initializer = WorkspaceInitializer(workspace.root, install_root)
    configured_pool_root = resolve_source_pool_root(install_root)
    pool_root_value = (
        str(configured_pool_root) if configured_pool_root is not None else None
    )
    candidate = initializer.prepare(
        profile_id,
        source_root=source_root,
        source_pool_root=pool_root_value,
    )
    validate_workspace_jira_binding(
        workspace,
        candidate.connection,
        install_root=install_root,
    )
    validate_workspace_project_binding(workspace, candidate.profile)
    result = initializer.preflight(
        candidate,
        confirm_existing_config=False,
        check_remote=True,
    )
    return {**candidate.summary(), **result}


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
    sys.stderr.write("AgenticOps：初始化摘要\n")
    sys.stderr.write(json.dumps(candidate.summary(), ensure_ascii=False, indent=2) + "\n")
    sys.stderr.flush()


def _prompt_existing_config_overwrite(error: RuntimeErrorResult) -> bool:
    details = error.details if isinstance(error.details, dict) else {}
    differences = details.get("differences", [])
    sys.stderr.write("AgenticOps：已有完整工作空间配置与本次有效配置不同：\n")
    if isinstance(differences, list):
        for item in differences:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field", ""))
            existing = str(item.get("existing", ""))
            candidate = str(item.get("candidate", ""))
            sys.stderr.write(f"- {field}：{existing} -> {candidate}\n")
    return _prompt_confirmation("确认覆盖以上配置")


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
