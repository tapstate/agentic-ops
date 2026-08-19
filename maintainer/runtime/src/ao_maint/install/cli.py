"""maintainer 面 install 命令组（AO-40）：维护者 Agent 身份配置。"""

from __future__ import annotations

import argparse
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
    identity_set.add_argument("--agent-id", required=True)
    identity_set.add_argument("--agent-type")
    identity_set.add_argument("--model")
    identity_set.add_argument("--environment")
    identity_set.add_argument("--note")
    identity_actions.add_parser("remove")


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
        saved = save_maintainer_identity(
            source_root,
            args.agent_id,
            args.note or "",
            agent_type=args.agent_type or "",
            model=args.model or "",
            environment=args.environment or "",
        )
        return {"configured": True, **saved}
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
