from __future__ import annotations

import argparse
import sys
from typing import Any

from ao_maint.output import write_json
from ao_maint.story_gate.service import StoryGateService
from ao_maint.workspace import Workspace


def configure_story_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    story_parser = subparsers.add_parser("story")
    commands = story_parser.add_subparsers(dest="command", required=True)

    impact = commands.add_parser("impact")
    _change_arguments(impact)

    approve = commands.add_parser("approve")
    _change_arguments(approve)
    approve.add_argument("--impact-id", required=True)
    approve.add_argument(
        "--authorization-reference",
        required=True,
        help=(
            "Agent 根据已审阅 commit 或 GitHub PR Review 构造的内部审计引用；"
            "用户无需查看或复制 impact_id"
        ),
    )

    verify = commands.add_parser("verify")
    _change_arguments(verify)
    verify.add_argument("--progress", action="store_true", help="以 NDJSON 输出检查进度事件")


def execute_story(args: argparse.Namespace, workspace: Workspace) -> dict[str, Any]:
    service = StoryGateService(workspace.root)
    if args.command == "impact":
        return service.inspect(
            args.change_source,
            base=args.base,
            head=args.head,
        )
    if args.command == "approve":
        return service.approve(
            args.change_source,
            args.impact_id,
            args.authorization_reference,
            base=args.base,
            head=args.head,
        )
    if args.command == "verify":
        return service.verify(
            args.change_source,
            base=args.base,
            head=args.head,
            event_sink=write_json if args.progress else _write_progress,
        )
    raise ValueError(f"unsupported story command: {args.command}")


def _change_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--change-source",
        choices=("staged", "worktree", "range"),
        default="worktree",
    )
    parser.add_argument("--base")
    parser.add_argument("--head")


def _write_progress(event: dict[str, Any]) -> None:
    check_id = event.get("check_id", "验收")
    if event.get("event") == "check_progress":
        message = f"AgenticOps：验收 {check_id} 仍在执行（{event.get('elapsed_seconds')} 秒）"
    else:
        message = f"AgenticOps：验收事件 {event.get('event')}：{check_id}"
    sys.stderr.write(message + "\n")
    sys.stderr.flush()
