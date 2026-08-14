from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ao_work.task_run.service import TaskRunProtocol
from ao_work.workspace import Workspace


def configure_task_run_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("task-run")
    commands = parser.add_subparsers(dest="command", required=True)
    open_parser = commands.add_parser("open")
    open_parser.add_argument("--manifest", required=True)
    record_parser = commands.add_parser("record")
    record_parser.add_argument("--manifest", required=True)
    record_parser.add_argument("--event", required=True)
    for name in (
        "probe-prohibition-baseline",
        "probe-jira",
        "probe-git",
        "probe-pr",
        "probe-prohibitions",
    ):
        probe = commands.add_parser(name)
        probe.add_argument("--manifest", required=True)
        if name == "probe-git":
            probe.add_argument(
                "--bind-action",
                action="append",
                default=[],
                choices=("git_commit", "git_push_task_branch"),
            )
        if name == "probe-pr":
            probe.add_argument(
                "--bind-action",
                action="append",
                default=[],
                choices=("github_pr_create_or_update",),
            )
    unverified = commands.add_parser("record-unverified-prohibitions")
    unverified.add_argument("--manifest", required=True)
    jira_write = commands.add_parser("probe-jira-write")
    jira_write.add_argument("--manifest", required=True)
    jira_write.add_argument("--plan-file", required=True)
    jira_write.add_argument("--confirm-plan-id", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--manifest", required=True)
    verify_parser.add_argument("--verification-id", required=True)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--manifest", required=True)
    finalize_parser.add_argument(
        "--status",
        choices=("ready_for_pr_review", "blocked", "failed"),
        required=True,
    )
    finalize_parser.add_argument("--next-action", required=True)


def execute_task_run(
    args: argparse.Namespace,
    workspace: Workspace,
    install_root: Path,
    lock_timeout: float,
) -> dict[str, Any]:
    protocol = TaskRunProtocol(
        workspace,
        install_root=install_root,
        lock_timeout=lock_timeout,
    )
    if args.command == "open":
        return protocol.open(args.manifest)
    if args.command == "record":
        return protocol.record(args.manifest, args.event)
    if args.command == "probe-jira":
        return protocol.probe_jira(args.manifest)
    if args.command == "probe-prohibition-baseline":
        return protocol.probe_prohibition_baseline(args.manifest)
    if args.command == "probe-git":
        return protocol.probe_git(args.manifest, args.bind_action)
    if args.command == "probe-pr":
        return protocol.probe_pr(args.manifest, args.bind_action)
    if args.command == "verify":
        return protocol.verify(args.manifest, args.verification_id)
    if args.command == "probe-prohibitions":
        return protocol.probe_prohibitions(args.manifest)
    if args.command == "record-unverified-prohibitions":
        return protocol.record_unverified_prohibitions(args.manifest)
    if args.command == "probe-jira-write":
        return protocol.probe_jira_write(
            args.manifest,
            args.plan_file,
            args.confirm_plan_id,
        )
    if args.command == "finalize":
        return protocol.finalize(args.manifest, args.status, args.next_action)
    raise AssertionError(f"unknown task-run command: {args.command}")
