from __future__ import annotations

import argparse
from typing import Any

from agentic_ops.story_gate.service import StoryGateService
from agentic_ops.workspace import Workspace


def configure_story_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    story_parser = subparsers.add_parser("story")
    commands = story_parser.add_subparsers(dest="command", required=True)

    impact = commands.add_parser("impact")
    _change_arguments(impact)

    approve = commands.add_parser("approve")
    _change_arguments(approve)
    approve.add_argument("--impact-id", required=True)
    approve.add_argument("--authorization-reference", required=True)

    verify = commands.add_parser("verify")
    _change_arguments(verify)


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
