"""maintainer 面 install 命令组（AO-40）：维护者 Agent 身份配置。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ao_maint.install.identity import (
    identity_file_path,
    load_maintainer_identity,
    save_maintainer_identity,
)
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult


def configure_install_parser(
    subparsers: argparse._SubParsersAction[Any],
) -> None:
    install = subparsers.add_parser("install")
    install_commands = install.add_subparsers(dest="command", required=True)

    identity = install_commands.add_parser("identity")
    identity_actions = identity.add_subparsers(dest="action", required=True)
    identity_actions.add_parser("show")
    identity_set = identity_actions.add_parser("set")
    identity_set.add_argument("--agent-id")
    identity_set.add_argument("--agent-type")
    identity_set.add_argument("--model")
    identity_set.add_argument("--environment")
    identity_set.add_argument("--note")
    identity_set.add_argument("--interactive", action="store_true")
    identity_actions.add_parser("remove")


def _prompt(label: str, default: str = "") -> str:
    if default:
        value = input(f"{label} [{default}]: ").strip()
        return value or default
    return input(f"{label}: ").strip()


def execute_install(
    args: argparse.Namespace,
    source_root: Path,
) -> dict[str, Any]:
    if args.command != "identity":
        raise RuntimeErrorResult(
            code="install_command_unknown",
            message=f"未知 install 子命令：{args.command}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请使用 ao-maint install identity",
        )
    if args.action == "show":
        identity = load_maintainer_identity(source_root)
        return {
            "identity_file": str(identity_file_path(source_root)),
            **identity,
        }
    if args.action == "set":
        return _set_identity(args, source_root)
    if args.action == "remove":
        identity_path = identity_file_path(source_root)
        if identity_path.exists():
            identity_path.unlink()
        return {"removed": True, "identity_file": str(identity_path)}
    raise RuntimeErrorResult(
        code="install_command_unknown",
        message=f"未知 identity 子命令：{args.action}",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请使用 ao-maint install identity set|show|remove",
    )


def _set_identity(
    args: argparse.Namespace,
    source_root: Path,
) -> dict[str, Any]:
    existing: dict[str, str] = {}
    try:
        existing = load_maintainer_identity(source_root)
    except RuntimeErrorResult as error:
        if error.code != "maintainer_identity_missing":
            raise
    if args.interactive:
        if not sys.stdin.isatty():
            raise RuntimeErrorResult(
                code="interactive_terminal_required",
                message="交互式身份配置需要终端输入",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请直接在终端运行 ao-maint install identity set --interactive",
            )
        agent_id = _prompt(
            "agent_id（执行维护任务的 Agent 标识）",
            existing.get("agent_id", ""),
        )
        agent_type = _prompt(
            "Agent 类型（如 hermes-agent / codex）",
            existing.get("agent_type", ""),
        )
        model = _prompt(
            "使用的模型（如 deepseek-v4-flash）",
            existing.get("model", ""),
        )
        environment = _prompt(
            "接管环境（如工作空间路径 / profile / 主机）",
            existing.get("environment", ""),
        )
        note = _prompt("备注（可选）", existing.get("note", ""))
        if not agent_id:
            raise RuntimeErrorResult(
                code="invalid_agent_id",
                message="agent_id 不能为空",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请提供非空 agent_id",
            )
        saved = save_maintainer_identity(
            source_root,
            agent_id,
            note,
            agent_type=agent_type,
            model=model,
            environment=environment,
        )
        return {"configured": True, **saved}
    agent_id = (args.agent_id or "").strip()
    if not agent_id:
        raise RuntimeErrorResult(
            code="invalid_agent_id",
            message="agent_id 不能为空",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供 --agent-id 或使用 --interactive",
        )
    saved = save_maintainer_identity(
        source_root,
        agent_id,
        args.note or "",
        agent_type=args.agent_type or "",
        model=args.model or "",
        environment=args.environment or "",
    )
    return {"configured": True, **saved}
