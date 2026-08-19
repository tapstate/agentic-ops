from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ao_work.config import load_jira_context, validate_workspace_jira_binding
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.service import (
    JiraService,
    WriteAttempt,
    WritePlan,
    build_write_attempt,
)
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state import TaskStore
from ao_work.workspace import Workspace
from ao_work.workspace_security import (
    read_workspace_outbound_file,
    validate_workspace_managed_path,
    validate_workspace_state_root,
)

PLAN_FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json$")


def configure_jira_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    jira_parser = subparsers.add_parser("jira")
    jira_commands = jira_parser.add_subparsers(dest="command", required=True)

    inspect_parser = jira_commands.add_parser("inspect")
    inspect_parser.add_argument("--issue-key", required=True)

    list_parser = jira_commands.add_parser("list")
    list_parser.add_argument("--max-results", type=int, default=10)

    comment_parser = jira_commands.add_parser("comment")
    _configure_write_actions(comment_parser, "comment")

    worklog_parser = jira_commands.add_parser("worklog")
    _configure_write_actions(worklog_parser, "worklog")

    description_parser = jira_commands.add_parser("description")
    _configure_write_actions(description_parser, "description", readback=False)

    transition_parser = jira_commands.add_parser("transition")
    _configure_write_actions(transition_parser, "transition")


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
    elif kind == "transition":
        target = plan.add_mutually_exclusive_group(required=True)
        target.add_argument("--target-status")
        target.add_argument("--target-transition")
        plan.add_argument("--comment-content-file")
    elif kind == "worklog":
        plan.add_argument("--title", required=True)
        plan.add_argument("--details-file", required=True)
        plan.add_argument("--included-work-file", required=True)
        plan.add_argument(
            "--excluded-waiting-category",
            action="append",
            required=True,
        )
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
        readback_parser.add_argument("--plan-file", required=True)
        readback_parser.add_argument("--confirm-plan-id", required=True)


def execute_jira(
    args: argparse.Namespace,
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
) -> dict[str, Any]:
    context = load_jira_context(workspace, install_root)
    email, token = context.require_credentials()
    client = JiraClient(
        context.profile,
        UrllibJiraTransport(context.connection, email, token),
    )
    validate_workspace_jira_binding(
        workspace,
        context.connection,
        account_id=client.current_user(),
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

    if args.command == "list":
        # 契约（AO-27 确认）：任务必须分配给当前用户、按优先级+更新时间排序、一页 10 个。
        # 过滤条件来自 profile.task_query（project/statusCategory 等），但排序统一由
        # Runtime 接管：JQL 不能有两个 ORDER BY，先剥离 task_query 尾部排序再追加。
        base = (context.profile.task_query or "").strip() or (
            "assignee = currentUser() AND resolution = Unresolved"
        )
        jql = _with_forced_order(base, "priority DESC, updated ASC")
        result = client.search_jql(jql, max_results=args.max_results)
        tasks = [
            {
                "issue_key": issue.key,
                "summary": issue.summary,
                "status": issue.status,
                "issue_type": issue.issue_type,
                "priority": issue.priority,
                "updated": issue.updated,
            }
            for issue in result.issues
        ]
        return {
            "connection_id": context.connection.connection_id,
            "profile_id": context.profile.profile_id,
            "jql": jql,
            "total": result.total,
            "returned": len(tasks),
            "tasks": tasks,
            "credential_status": context.credential_status(),
        }

    if args.action == "plan":
        task = _validate_task_binding(store, context, service, args.issue_key)
        agentic_run_id = str(task["agentic_run_id"])
        plan_path = _jira_plan_file(
            workspace.root,
            args.plan_file,
            args.issue_key,
            agentic_run_id,
            must_exist=False,
        )
        if args.command == "comment":
            content = read_workspace_outbound_file(
                workspace.root,
                args.content_file,
                label="Jira 评论内容文件",
            )
            plan = service.plan_comment(
                args.issue_key,
                args.idempotency_key,
                args.category,
                content,
                agentic_run_id=agentic_run_id,
            )
        elif args.command == "transition":
            comment = None
            if args.comment_content_file:
                comment = read_workspace_outbound_file(
                    workspace.root,
                    args.comment_content_file,
                    label="Jira 状态流转说明评论文件",
                )
            plan = service.plan_transition(
                args.issue_key,
                args.idempotency_key,
                agentic_run_id=agentic_run_id,
                target_status=args.target_status,
                target_transition=args.target_transition,
                comment=comment,
            )
        elif args.command == "worklog":
            details = read_workspace_outbound_file(
                workspace.root,
                args.details_file,
                label="Jira Worklog 内容文件",
            )
            included_work_content = read_workspace_outbound_file(
                workspace.root,
                args.included_work_file,
                label="Jira Worklog 耗时组成文件",
            )
            plan = service.plan_worklog(
                args.issue_key,
                args.idempotency_key,
                args.title,
                details,
                args.time_spent_seconds,
                args.started,
                args.exclude_waiting,
                agentic_run_id=agentic_run_id,
                included_work=_read_included_work(included_work_content),
                excluded_waiting_categories=args.excluded_waiting_category,
            )
        else:
            sections_content = read_workspace_outbound_file(
                workspace.root,
                args.sections_file,
                label="Jira Description 章节文件",
            )
            sections = _read_sections(sections_content)
            plan = service.plan_description(
                args.issue_key,
                args.idempotency_key,
                sections,
                agentic_run_id=agentic_run_id,
            )
        service.validate_no_credentials(plan, email, token)
        _require_plan_task_binding(plan, task)
        _write_new_plan(plan_path, plan.to_dict())
        authorization = _authorization_guidance(plan, str(task["agentic_run_id"]))
        result: dict[str, Any] = {
            "connection_id": context.connection.connection_id,
            "profile_id": context.profile.profile_id,
            "issue_key": plan.issue_key,
            "plan_id": plan.plan_id,
            "action": plan.action,
            "content_sha256": plan.content_sha256,
            "plan_file": str(plan_path),
            **authorization,
        }
        if args.command == "transition":
            result.update(
                {
                    "project_key": plan.payload.get("project_key"),
                    "from_status": plan.payload.get("from_status"),
                    "target_status": plan.payload.get("target_status"),
                    "transition_id": plan.payload.get("transition_id"),
                    "transition_name": plan.payload.get("transition_name"),
                    "with_comment": bool(plan.payload.get("comment")),
                }
            )
        return result

    if args.action == "apply":
        plan_path, path_issue_key, path_run_id, plan = _read_plan_candidate(
            workspace.root,
            args.plan_file,
        )
        _require_plan_path_binding(plan, path_issue_key, path_run_id)
        task = _validate_task_binding(store, context, service, path_issue_key)
        plan_path = _jira_plan_file(
            workspace.root,
            args.plan_file,
            plan.issue_key,
            plan.agentic_run_id,
        )
        plan = _read_plan(plan_path)
        _require_plan_task_binding(plan, task)
        operation = {
            "comment": "jira_comment",
            "worklog": "jira_worklog",
            "description": "jira_description",
            "transition": "jira_transition",
        }[args.command]
        service.validate_apply(plan, args.confirm_plan_id, operation)
        service.validate_no_credentials(plan, email, token)
        authorization_type = _validate_authorization_reference(
            args.authorization_reference,
            plan,
            str(task["agentic_run_id"]),
            service,
        )
        decision_created = store.append_decision(
            plan.issue_key,
            str(task["agentic_run_id"]),
            "jira_write_plan_confirmed",
            args.decision_summary,
            args.authorization_reference,
        )
        attempt_path = _jira_attempt_file(plan_path)

        def begin_create(current_plan: WritePlan) -> WriteAttempt:
            attempt = build_write_attempt(
                current_plan,
                args.authorization_reference,
            )
            _write_new_plan(attempt_path, attempt.to_dict())
            return attempt

        if args.command == "comment":
            result = service.apply_comment(
                plan,
                args.confirm_plan_id,
                begin_create=begin_create,
            )
        elif args.command == "worklog":
            result = service.apply_worklog(
                plan,
                args.confirm_plan_id,
                begin_create=begin_create,
            )
        elif args.command == "transition":
            result = service.apply_transition(plan, args.confirm_plan_id)
        else:
            result = service.apply_description(plan, args.confirm_plan_id)
        record = store.record_external_readback(
            plan.issue_key,
            plan.operation,
            plan.idempotency_key,
            str(result["external_id"]),
            agentic_run_id=plan.agentic_run_id,
            plan_id=plan.plan_id,
            content_sha256=plan.content_sha256,
            evidence=_sync_evidence(result),
        )
        return {
            "connection_id": context.connection.connection_id,
            "profile_id": context.profile.profile_id,
            "issue_key": plan.issue_key,
            "plan_id": plan.plan_id,
            "decision_recorded": decision_created,
            "authorization_reference": args.authorization_reference,
            "authorization_type": authorization_type,
            "attempt_file": (
                str(attempt_path) if result.get("write_attempt_id") else None
            ),
            **result,
            "readback": record,
        }

    if args.command == "comment":
        plan_path, path_issue_key, path_run_id, plan = _read_plan_candidate(
            workspace.root,
            args.plan_file,
        )
        _require_plan_path_binding(plan, path_issue_key, path_run_id)
        if args.issue_key != plan.issue_key or args.idempotency_key != plan.idempotency_key:
            raise RuntimeErrorResult(
                code="jira_write_plan_mismatch",
                message="Jira 回读输入与写入计划不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请使用 plan 输出绑定的 Issue、幂等键和计划文件",
            )
        task = _validate_task_binding(store, context, service, args.issue_key)
        _require_plan_task_binding(plan, task)
        service.validate_apply(plan, args.confirm_plan_id, "jira_comment")
        attempt = _read_attempt_if_present(_jira_attempt_file(plan_path), plan)
        result = service.readback_comment(plan, attempt=attempt)
        operation = "jira_comment"
    elif args.command == "transition":
        plan_path, path_issue_key, path_run_id, plan = _read_plan_candidate(
            workspace.root,
            args.plan_file,
        )
        _require_plan_path_binding(plan, path_issue_key, path_run_id)
        if args.issue_key != plan.issue_key or args.idempotency_key != plan.idempotency_key:
            raise RuntimeErrorResult(
                code="jira_write_plan_mismatch",
                message="Jira 回读输入与写入计划不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请使用 plan 输出绑定的 Issue、幂等键和计划文件",
            )
        task = _validate_task_binding(store, context, service, args.issue_key)
        _require_plan_task_binding(plan, task)
        service.validate_apply(plan, args.confirm_plan_id, "jira_transition")
        result = service.readback_transition(plan)
        operation = "jira_transition"
    else:
        plan_path, path_issue_key, path_run_id, plan = _read_plan_candidate(
            workspace.root,
            args.plan_file,
        )
        _require_plan_path_binding(plan, path_issue_key, path_run_id)
        if args.issue_key != plan.issue_key or args.idempotency_key != plan.idempotency_key:
            raise RuntimeErrorResult(
                code="jira_write_plan_mismatch",
                message="Jira 回读输入与写入计划不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请使用 plan 输出绑定的 Issue、幂等键和计划文件",
            )
        task = _validate_task_binding(store, context, service, args.issue_key)
        _require_plan_task_binding(plan, task)
        service.validate_apply(plan, args.confirm_plan_id, "jira_worklog")
        attempt = _read_attempt_if_present(_jira_attempt_file(plan_path), plan)
        result = service.readback_worklog(plan, attempt=attempt)
        operation = "jira_worklog"
    record = store.record_external_readback(
        args.issue_key,
        operation,
        args.idempotency_key,
        str(result["external_id"]),
        agentic_run_id=plan.agentic_run_id,
        plan_id=plan.plan_id,
        content_sha256=plan.content_sha256,
        evidence=_sync_evidence(result),
    )
    return {**result, "readback": record}


def _read_plan(path: Path) -> WritePlan:
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size > 1024 * 1024
                or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise ValueError("plan must be a stable single-link regular file")
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                payload = json.load(
                    stream,
                    object_pairs_hook=_reject_duplicate_object_keys,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"invalid JSON constant: {value}")
                    ),
                )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if not isinstance(payload, dict):
            raise ValueError("plan must be a JSON object")
        plan = WritePlan.from_dict(payload)
        plan.validate_integrity()
        return plan
    except (KeyError, OSError, TypeError, UnicodeError, ValueError) as error:
        raise RuntimeErrorResult(
            code="jira_write_plan_invalid",
            message=f"Jira 写入计划文件无效：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 plan，不要人工拼接计划文件",
        ) from error


def _read_attempt(path: Path, plan: WritePlan) -> WriteAttempt:
    try:
        payload = _read_single_link_json(path)
        attempt = WriteAttempt.from_dict(payload)
        attempt.validate_integrity(plan)
        return attempt
    except RuntimeErrorResult:
        raise
    except (KeyError, OSError, TypeError, UnicodeError, ValueError) as error:
        raise RuntimeErrorResult(
            code="jira_write_attempt_invalid",
            message=f"Jira 写入尝试文件无效：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请保留原文件并停止写入，交给 AgenticOps 维护者核对",
        ) from error


def _read_attempt_if_present(path: Path, plan: WritePlan) -> WriteAttempt | None:
    if not path.exists() and not path.is_symlink():
        return None
    return _read_attempt(path, plan)


def _read_single_link_json(path: Path) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size > 1024 * 1024
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise ValueError("managed JSON must be a stable single-link regular file")
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            payload = json.load(
                stream,
                object_pairs_hook=_reject_duplicate_object_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(payload, dict):
        raise ValueError("managed JSON must be an object")
    return payload


def _read_sections(content: str) -> dict[str, str]:
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as error:
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


def _read_included_work(content: str) -> list[dict[str, object]]:
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise RuntimeErrorResult(
            code="worklog_included_work_invalid",
            message=f"Worklog 耗时组成文件无法读取：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供 description 和 seconds 组成的 YAML 或 JSON 数组",
        ) from error
    if not isinstance(payload, list):
        raise RuntimeErrorResult(
            code="worklog_included_work_invalid",
            message="Worklog 耗时组成文件必须是数组",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请逐项列出实际处理说明和对应秒数",
        )
    return payload


def _with_forced_order(jql: str, order: str) -> str:
    """剥离 JQL 尾部 ORDER BY 子句并追加统一排序。

    JQL 不允许两个 ORDER BY；Runtime 需要接管排序（如优先级+更新时间），
    而过滤条件仍来自 profile.task_query。仅剥离最后一个顶层 ORDER BY，
    不解析 JQL 内部结构（ORDER BY 只能出现在 JQL 末尾）。
    """
    cleaned = jql.strip()
    marker = cleaned.upper().rfind(" ORDER BY ")
    if marker != -1:
        cleaned = cleaned[:marker].rstrip()
    if not cleaned:
        raise RuntimeErrorResult(
            code="task_query_failed",
            message="Project Profile 的 task_query 为空或仅含排序子句",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请在 Project Profile 配置有效的 task_query（JQL）后重试",
        )
    return f"{cleaned} ORDER BY {order}"


def _jira_plan_file(
    root: Path,
    value: str,
    issue_key: str,
    agentic_run_id: str,
    *,
    must_exist: bool = True,
) -> Path:
    state_root = validate_workspace_state_root(root)
    expected_directory = (
        state_root / "tasks" / issue_key / "runs" / agentic_run_id / "jira-plans"
    )
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = candidate.absolute()
    try:
        relative = candidate.relative_to(expected_directory)
    except ValueError as error:
        raise RuntimeErrorResult(
            code="jira_plan_path_not_bound",
            message="Jira 写入计划路径未绑定当前 Issue 与 agentic_run_id",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                "请把 --plan-file 指向 .agentic-ops/tasks/<ISSUE>/runs/"
                "<agentic-run-id>/jira-plans/<name>.json"
            ),
        ) from error
    if (
        len(relative.parts) != 1
        or relative.name != candidate.name
        or not PLAN_FILE_PATTERN.fullmatch(relative.name)
    ):
        raise RuntimeErrorResult(
            code="jira_plan_path_not_bound",
            message="Jira 写入计划必须是当前运行 jira-plans 目录下的单层 JSON 文件",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请使用安全的 <name>.json 计划文件名后重试",
        )
    try:
        validate_workspace_managed_path(root, candidate)
    except RuntimeErrorResult as error:
        raise RuntimeErrorResult(
            code="jira_plan_path_unsafe",
            message="Jira 写入计划路径含符号链接或越出受管目录",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请移除异常路径并核对任务状态完整性",
        ) from error
    if candidate.exists() and candidate.is_symlink():
        raise RuntimeErrorResult(
            code="jira_plan_path_unsafe",
            message="Jira 写入计划不能是符号链接",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请移除异常路径并重新执行 plan",
        )
    if must_exist and not candidate.is_file():
        raise RuntimeErrorResult(
            code="jira_write_plan_invalid",
            message="Jira 写入计划文件不存在或不是普通文件",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 plan，不要人工拼接计划文件",
        )
    if not must_exist:
        if candidate.exists():
            raise RuntimeErrorResult(
                code="jira_plan_file_exists",
                message="Jira 写入计划文件已存在，禁止覆盖现有计划或受管状态",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请使用新的计划文件名，或人工确认后移除旧计划",
            )
        expected_directory.mkdir(parents=True, exist_ok=True)
        try:
            validate_workspace_managed_path(root, expected_directory)
        except RuntimeErrorResult as error:
            raise RuntimeErrorResult(
                code="jira_plan_path_unsafe",
                message="Jira 写入计划目录包含不安全路径",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请移除异常路径并核对任务状态完整性",
            ) from error
    return candidate


def _jira_attempt_file(plan_path: Path) -> Path:
    candidate = plan_path.with_name(f"{plan_path.name}.attempt.json")
    if not PLAN_FILE_PATTERN.fullmatch(candidate.name):
        raise RuntimeErrorResult(
            code="jira_write_attempt_path_invalid",
            message="Jira 写入尝试文件名超出受管路径约束",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请使用更短的 Jira plan 文件名重新生成计划",
        )
    return candidate


def _read_plan_candidate(root: Path, value: str) -> tuple[Path, str, str, WritePlan]:
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else root / supplied
    candidate = candidate.absolute()
    state_root = validate_workspace_state_root(root)
    try:
        relative = candidate.relative_to(state_root / "tasks")
    except ValueError as error:
        raise RuntimeErrorResult(
            code="jira_plan_path_not_bound",
            message="Jira 写入计划路径未绑定任务运行",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请使用 plan 输出的 plan_file 原样执行 apply",
        ) from error
    if (
        len(relative.parts) != 5
        or relative.parts[1] != "runs"
        or relative.parts[3] != "jira-plans"
        or not PLAN_FILE_PATTERN.fullmatch(relative.parts[4])
    ):
        raise RuntimeErrorResult(
            code="jira_plan_path_not_bound",
            message="Jira 写入计划路径结构无效",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请使用 plan 输出的 plan_file 原样执行 apply",
        )
    try:
        validate_workspace_managed_path(root, candidate)
    except RuntimeErrorResult as error:
        raise RuntimeErrorResult(
            code="jira_plan_path_unsafe",
            message="Jira 写入计划路径含符号链接或越界",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请移除异常路径并核对任务状态",
        ) from error
    return candidate, relative.parts[0], relative.parts[2], _read_plan(candidate)


def read_bound_jira_plan(
    root: Path,
    value: str,
    *,
    issue_key: str,
    agentic_run_id: str,
) -> tuple[Path, WritePlan]:
    """Read a plan only from the exact current issue/run namespace."""
    candidate, path_issue_key, path_run_id, plan = _read_plan_candidate(root, value)
    _require_plan_path_binding(plan, path_issue_key, path_run_id)
    if path_issue_key != issue_key or path_run_id != agentic_run_id:
        raise RuntimeErrorResult(
            code="jira_plan_path_not_bound",
            message="Jira 写入计划不属于当前 task-run 的 Issue 与 agentic_run_id",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请使用当前 task-run 中 plan 输出的原始计划文件",
        )
    canonical = _jira_plan_file(
        root,
        str(candidate),
        issue_key,
        agentic_run_id,
    )
    plan = _read_plan(canonical)
    _require_plan_path_binding(plan, issue_key, agentic_run_id)
    return canonical, plan


def read_bound_jira_attempt(
    plan_path: Path,
    plan: WritePlan,
) -> tuple[Path | None, WriteAttempt | None]:
    """Read the immutable create-attempt paired with an already bound plan."""

    attempt_path = _jira_attempt_file(plan_path)
    attempt = _read_attempt_if_present(attempt_path, plan)
    return (attempt_path if attempt is not None else None), attempt


def _write_new_plan(path: Path, payload: dict[str, Any]) -> None:
    temporary_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(temporary_descriptor, 0o600)
        with os.fdopen(temporary_descriptor, "w", encoding="utf-8") as stream:
            temporary_descriptor = -1
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise RuntimeErrorResult(
                code="jira_plan_file_exists",
                message="Jira 写入计划文件在生成期间已存在，禁止覆盖",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请核对计划目录并使用新的计划文件名",
            ) from error
        _fsync_directory(path.parent)
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        temporary.unlink(missing_ok=True)


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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


def _require_plan_path_binding(
    plan: WritePlan,
    path_issue_key: str,
    path_run_id: str,
) -> None:
    if plan.issue_key != path_issue_key or plan.agentic_run_id != path_run_id:
        raise RuntimeErrorResult(
            code="jira_plan_path_binding_mismatch",
            message="Jira 写入计划内容与路径中的 Issue 或 agentic_run_id 不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请丢弃该计划并从当前任务运行重新执行 plan",
        )


def _require_plan_task_binding(plan: WritePlan, task: dict[str, Any]) -> None:
    if (
        plan.issue_key != str(task.get("issue_key", ""))
        or plan.agentic_run_id != str(task.get("agentic_run_id", ""))
    ):
        raise RuntimeErrorResult(
            code="jira_plan_task_binding_mismatch",
            message="Jira 写入计划未绑定当前任务的 Issue 与 agentic_run_id",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请丢弃该计划并从当前任务运行重新执行 plan",
        )


def _sync_evidence(result: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "body_sha256",
        "title",
        "details_sha256",
        "time_spent_seconds",
        "started",
        "excludes_waiting",
        "created",
        "write_precondition",
        "write_attempt_id",
        "write_attempt_started_at",
        "current_status",
        "target_status",
        "status_matched",
    }
    return {key: value for key, value in result.items() if key in allowed}


def _authorization_guidance(plan: WritePlan, agentic_run_id: str) -> dict[str, str]:
    return {
        "authorization_user_confirmation_reference": (
            f"user-confirmation:{plan.issue_key}:{agentic_run_id}:{plan.plan_id}"
        ),
        "authorization_comment_marker": _authorization_comment_marker(
            plan.issue_key,
            agentic_run_id,
            plan.plan_id,
        ),
        "authorization_jira_comment_reference_format": (
            f"jira-comment:{plan.issue_key}:<positive-comment-id>:{plan.plan_id}"
        ),
    }


def _validate_authorization_reference(
    reference: str,
    plan: WritePlan,
    agentic_run_id: str,
    service: JiraService,
) -> str:
    guidance = _authorization_guidance(plan, agentic_run_id)
    if reference == guidance["authorization_user_confirmation_reference"]:
        return "user_confirmation"

    pattern = re.compile(
        rf"jira-comment:{re.escape(plan.issue_key)}:"
        rf"(?P<comment_id>[1-9][0-9]*):{re.escape(plan.plan_id)}"
    )
    matched = pattern.fullmatch(reference)
    if matched is not None:
        service.validate_authorization_comment(
            plan.issue_key,
            matched.group("comment_id"),
            guidance["authorization_comment_marker"],
        )
        return "jira_comment"

    raise RuntimeErrorResult(
        code="jira_authorization_reference_invalid",
        message="Jira 写入授权引用未严格绑定当前 Issue、agentic_run_id 和 plan_id",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=(
            "请使用 plan 输出的 authorization_user_confirmation_reference；或先在当前 Jira 任务"
            "留下包含 authorization_comment_marker 的确认评论，再按"
            " authorization_jira_comment_reference_format 提供引用"
        ),
    )


def _authorization_comment_marker(
    issue_key: str,
    agentic_run_id: str,
    plan_id: str,
) -> str:
    return f"[agentic-ops-authorization:{issue_key}:{agentic_run_id}:{plan_id}]"


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
