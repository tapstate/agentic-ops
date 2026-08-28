from __future__ import annotations

import argparse
from typing import Any

from ao_maint.diagnose.network import NetworkDiagnoser
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_maint.jira.client import JiraClient, UrllibJiraTransport
from ao_maint.jira.config import load_maintainer_jira_config


def configure_diagnose_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("diagnose")
    parser.add_subparsers(dest="command", required=True).add_parser("network")


def execute_diagnose(args: argparse.Namespace, source_root: Any) -> dict[str, Any]:
    if args.command != "network":
        raise AssertionError(f"unsupported diagnose command: {args.command}")
    config = load_maintainer_jira_config(source_root)
    email, token = config.require_credentials()
    client = JiraClient(config.connection, UrllibJiraTransport(config.connection, email, token))
    result = NetworkDiagnoser.for_jira_client(client).diagnose()
    diagnosis = result["diagnosis"]
    if diagnosis["code"] != "network_diagnosis_passed":
        raise RuntimeErrorResult(
            code=str(diagnosis["code"]),
            message="网络诊断发现阻断或无法确定的连接问题",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action=str(result["next_step"]["question"]),
            details=result,
        )
    return result
