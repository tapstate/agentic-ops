from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from agentic_ops.config import load_jira_context
from agentic_ops.config.loader import default_install_root
from agentic_ops.jira.client import JiraClient, UrllibJiraTransport
from agentic_ops.jira.service import JiraService, WritePlan
from agentic_ops.output import EXIT_BLOCKED, RuntimeErrorResult
from agentic_ops.task_state import TaskStore
from agentic_ops.task_state.io import atomic_write_json, read_json
from agentic_ops.workspace import Workspace


def configure_jira_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    jira_parser = subparsers.add_parser("jira")
    jira_commands = jira_parser.add_subparsers(dest="command", required=True)

    inspect_parser = jira_commands.add_parser("inspect")
    inspect_parser.add_argument("--issue-key", required=True)

    comment_parser = jira_commands.add_parser("comment")
    _configure_write_actions(comment_parser, "comment")

    worklog_parser = jira_commands.add_parser("worklog")
    _configure_write_actions(worklog_parser, "worklog")

    description_parser = jira_commands.add_parser("description")
    _configure_write_actions(description_parser, "description", readback=False)


def _configure_write_actions(
    parser: argparse.ArgumentParser, kind: str, *, readback: bool = True
) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    plan = actions.add_parser("plan")
    plan.add_argument("--issue-key", required=True)
    plan.add_argument("--idempotency-key", required=True)
    plan.add_argument("--plan-file", required=True)
    if kind == "comment":
        plan.add_argument("--category", required=True)
        plan.add_argument("--content-file", required=True)
    elif kind == "worklog":
        plan.add_argument("--title", required=True)
        plan.add_argument("--details-file", required=True)
        plan.add_argument("--time-spent-seconds", required=True, type=int)
        plan.add_argument("--started", required=True)
        plan.add_argument("--exclude-waiting", action="store_true")
    else:
        plan.add_argument("--sections-file", required=True)

    apply = actions.add_parser("apply")
    apply.add_argument("--plan-file", required=True)
    apply.add_argument("--confirm-plan-id", required=True)
    apply.add_argument("--authorization-reference", required=True)
    apply.add_argument(
        "--decision-summary",
        default="研发工程师确认 Jira 写入计划",
    )

    if readback:
        readback_parser = actions.add_parser("readback")
        readback_parser.add_argument("--issue-key", required=True)
        readback_parser.add_argument("--idempotency-key", required=True)


def execute_jira(
    args: argparse.Namespace,
    workspace: Workspace,
    install_root_value: str | None,
    store: TaskStore,
) -> dict[str, Any]:
    install_root = (
        _safe_root(Path(install_root_value), "install_root_not_found")
        if install_root_value
        else default_install_root()
    )
    context = load_jira_context(workspace, install_root)
    email, token = context.require_credentials()
    client = JiraClient(
        context.profile,
        UrllibJiraTransport(context.connection, email, token),
    )
    service = JiraService(context.profile, client)
    if args.command == "inspect":
        issue = service.inspect_issue(args.issue_key)
        return {
            "connection_id": context.connection.connection_id,
            "profile_id": context.profile.profile_id,
            "issue": {
                "jira_issue_id": issue.issue_id,
                "issue_key": issue.key,
                "project_key": issue.project_key,
                "summary": issue.summary,
                "status": issue.status,
                "issue_type": issue.issue_type,
                "assignee": issue.assignee,
            },
            "credential_status": context.credential_status(),
        }

    if args.action == "plan":
        plan_path = _managed_state_file(workspace.root, args.plan_file, must_exist=False)
        if args.command == "comment":
            content = _workspace_file(workspace.root, args.content_file).read_text(encoding="utf-8")
            plan = service.plan_comment(
                args.issue_key, args.idempotency_key, args.category, content
            )
        elif args.command == "worklog":
            details = _workspace_file(workspace.root, args.details_file).read_text(encoding="utf-8")
            plan = service.plan_worklog(
                args.issue_key,
                args.idempotency_key,
                args.title,
                details,
                args.time_spent_seconds,
                args.started,
                args.exclude_waiting,
            )
        else:
            sections = _read_sections(_workspace_file(workspace.root, args.sections_file))
            plan = service.plan_description(args.issue_key, args.idempotency_key, sections)
        _validate_task_binding(store, context, service, plan.issue_key)
        atomic_write_json(plan_path, plan.to_dict())
        return {
            "connection_id": context.connection.connection_id,
            "profile_id": context.profile.profile_id,
            "issue_key": plan.issue_key,
            "plan_id": plan.plan_id,
            "action": plan.action,
            "content_sha256": plan.content_sha256,
            "plan_file": str(plan_path),
        }

    if args.action == "apply":
        plan = _read_plan(_managed_state_file(workspace.root, args.plan_file))
        task = _validate_task_binding(store, context, service, plan.issue_key)
        decision_created = store.append_decision(
            plan.issue_key,
            str(task["agentic_run_id"]),
            "jira_write_plan_confirmed",
            args.decision_summary,
            args.authorization_reference,
        )
        if args.command == "comment":
            result = service.apply_comment(plan, args.confirm_plan_id)
        elif args.command == "worklog":
            result = service.apply_worklog(plan, args.confirm_plan_id)
        else:
            result = service.apply_description(plan, args.confirm_plan_id)
        record = store.record_external_readback(
            plan.issue_key,
            plan.operation,
            plan.idempotency_key,
            str(result["external_id"]),
        )
        return {
            "connection_id": context.connection.connection_id,
            "profile_id": context.profile.profile_id,
            "issue_key": plan.issue_key,
            "plan_id": plan.plan_id,
            "decision_recorded": decision_created,
            **result,
            "readback": record,
        }

    if args.command == "comment":
        result = service.readback_comment(args.issue_key, args.idempotency_key)
        operation = "jira_comment"
    else:
        result = service.readback_worklog(args.issue_key, args.idempotency_key)
        operation = "jira_worklog"
    record = store.record_external_readback(
        args.issue_key,
        operation,
        args.idempotency_key,
        str(result["external_id"]),
    )
    return {**result, "readback": record}


def _read_plan(path: Path) -> WritePlan:
    try:
        plan = WritePlan.from_dict(read_json(path))
        plan.validate_integrity()
        return plan
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeErrorResult(
            code="jira_write_plan_invalid",
            message=f"Jira 写入计划文件无效：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 plan，不要人工拼接计划文件",
        ) from error


def _read_sections(path: Path) -> dict[str, str]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeErrorResult(
            code="description_sections_invalid",
            message=f"Description 章节文件无法读取：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供键值均为中文文本的 YAML 或 JSON 文件",
        ) from error
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise RuntimeErrorResult(
            code="description_sections_invalid",
            message="Description 章节文件必须是字符串到字符串的映射",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修正章节文件后重新执行 plan",
        )
    return payload


def _workspace_file(root: Path, value: str, *, must_exist: bool = True) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RuntimeErrorResult(
            code="workspace_path_escape",
            message=f"文件路径越出项目工作空间：{value}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请把输入和计划文件放在项目 AI 工作空间内",
        ) from error
    if must_exist and not resolved.is_file():
        raise RuntimeErrorResult(
            code="workspace_file_not_found",
            message=f"工作空间文件不存在：{value}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请检查文件路径后重试",
        )
    return resolved


def _managed_state_file(root: Path, value: str, *, must_exist: bool = True) -> Path:
    resolved = _workspace_file(root, value, must_exist=must_exist)
    managed_root = (root / ".agentic-ops").resolve()
    try:
        resolved.relative_to(managed_root)
    except ValueError as error:
        raise RuntimeErrorResult(
            code="jira_plan_path_not_managed",
            message="Jira 写入计划必须保存在 .agentic-ops/ 运行状态目录",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请把 --plan-file 指向 .agentic-ops/tasks/<ISSUE>/runs/ 下的文件",
        ) from error
    return resolved


def _validate_task_binding(
    store: TaskStore,
    context: Any,
    service: JiraService,
    issue_key: str,
) -> dict[str, Any]:
    task = store.inspect(issue_key)["task"]
    issue = service.inspect_issue(issue_key)
    expected = {
        "connection_id": context.connection.connection_id,
        "jira_issue_id": issue.issue_id,
        "issue_key": issue.key,
        "project_key": issue.project_key,
    }
    mismatched = [key for key, value in expected.items() if str(task.get(key, "")) != str(value)]
    if mismatched:
        raise RuntimeErrorResult(
            code="jira_workspace_mismatch",
            message=f"本地任务身份与 Jira 工作空间不一致：{', '.join(mismatched)}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请核对 Connection、Profile 和任务初始化身份，不要覆盖现有状态",
        )
    return task


def _safe_root(path: Path, code: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise RuntimeErrorResult(
            code=code,
            message=f"目录不存在：{resolved}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供有效的 AgenticOps 安装根目录",
        )
    return resolved
