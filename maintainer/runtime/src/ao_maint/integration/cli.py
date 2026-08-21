from __future__ import annotations

import argparse
from typing import Any

from ao_maint.integration.service import IntegrationService
from ao_maint.integration.real_e2e import RealTaskToPrE2EPreflight
from ao_maint.workspace import Workspace


def configure_integration_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    integration = subparsers.add_parser("integration")
    commands = integration.add_subparsers(dest="command", required=True)

    prepare_task_to_pr = commands.add_parser("prepare-task-to-pr")
    prepare_task_to_pr.add_argument("issue_key")
    prepare_task_to_pr.add_argument("--output")
    prepare_task_to_pr.add_argument("--agent-id")
    prepare_task_to_pr.add_argument("--confirmed-by")

    prepare_e2e_config = commands.add_parser("prepare-task-to-pr-e2e-config")
    prepare_e2e_config.add_argument("--agent-id", required=True)
    prepare_e2e_config.add_argument("--project-profile", required=True)
    prepare_e2e_config.add_argument("--expected-confirmer", required=True)

    preflight_e2e = commands.add_parser("preflight-task-to-pr-e2e")
    preflight_e2e.add_argument("issue_key")

    accept_task_to_pr = commands.add_parser("accept-task-to-pr")
    accept_task_to_pr.add_argument("issue_key")
    accept_task_to_pr.add_argument("--manifest", required=True)
    accept_task_to_pr.add_argument("--result", required=True)

    prepare_offline = commands.add_parser("prepare-offline")
    prepare_offline.add_argument("issue_key")
    prepare_offline.add_argument("--output")

    run_offline = commands.add_parser("run-offline")
    run_offline.add_argument("issue_key")
    run_offline.add_argument("--manifest", required=True)


def execute_integration(args: argparse.Namespace, workspace: Workspace) -> dict[str, Any]:
    service = IntegrationService(workspace.root)
    if args.command == "prepare-task-to-pr-e2e-config":
        return RealTaskToPrE2EPreflight(workspace.root).prepare_config(
            agent_id=args.agent_id,
            project_profile=args.project_profile,
            expected_confirmer=args.expected_confirmer,
        )
    if args.command == "preflight-task-to-pr-e2e":
        return RealTaskToPrE2EPreflight(workspace.root).run(args.issue_key)
    if args.command == "prepare-task-to-pr":
        return service.prepare_task_to_pr(
            args.issue_key,
            output=args.output,
            agent_id=args.agent_id,
            confirmed_by=args.confirmed_by,
        )
    if args.command == "accept-task-to-pr":
        return service.accept_task_to_pr(
            args.issue_key, args.manifest, args.result
        )
    if args.command == "prepare-offline":
        return service.prepare_offline(args.issue_key, output=args.output)
    if args.command == "run-offline":
        return service.run_offline(args.issue_key, args.manifest)
    raise ValueError(f"unsupported integration command: {args.command}")
