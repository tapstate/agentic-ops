from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ao_maint.install.identity import load_maintainer_identity
from ao_maint.jira.adf import adf_to_markdown
from ao_maint.jira.client import JiraClient, UrllibJiraTransport
from ao_maint.jira.config import (
    credential_status,
    env_file_path,
    load_comment_template_schema,
    load_maintainer_jira_config,
    load_maintainer_workflow,
    plans_dir,
    remove_credentials,
    set_credentials,
)
from ao_maint.jira.service import (
    MaintainerJiraService,
    WritePlan,
    _description_sha256,
)
from ao_maint.jira.scope import (
    MAINTAINER_JIRA_PROJECT_KEY,
    validate_maintainer_issue_key,
    validate_maintainer_project_key,
    validate_write_plan_scope,
)
from ao_maint.locking import TaskLock
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

PLAN_FILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json$")
DECISIONS_FILE = "decisions.ndjson"


def configure_jira_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    jira_parser = subparsers.add_parser("jira")
    jira_commands = jira_parser.add_subparsers(dest="command", required=True)

    auth_parser = jira_commands.add_parser("auth")
    auth_actions = auth_parser.add_subparsers(dest="action", required=True)
    auth_actions.add_parser("show")
    auth_actions.add_parser("verify")
    set_parser = auth_actions.add_parser("set")
    set_parser.add_argument("--email")
    set_parser.add_argument("--token-stdin", action="store_true")
    set_parser.add_argument("--interactive", action="store_true")
    remove = auth_actions.add_parser("remove")
    remove.add_argument("--field", choices=("email", "token", "all"), required=True)

    inspect_parser = jira_commands.add_parser("inspect")
    inspect_parser.add_argument("--issue-key", required=True)

    create_parser = jira_commands.add_parser("create")
    create_actions = create_parser.add_subparsers(dest="action", required=True)
    create_plan = create_actions.add_parser("plan")
    create_plan.add_argument("--project-key", required=True)
    create_plan.add_argument("--issuetype", required=True)
    create_plan.add_argument("--summary", required=True)
    create_plan.add_argument("--description-file")
    create_plan.add_argument("--assignee")
    create_plan.add_argument("--parent")
    create_plan.add_argument("--field", action="append", default=[])
    create_plan.add_argument("--idempotency-key", required=True)
    create_plan.add_argument("--run-id")
    create_plan.add_argument("--plan-file", required=True)
    create_apply = create_actions.add_parser("apply")
    create_apply.add_argument("--plan-file", required=True)
    create_apply.add_argument("--confirm-plan-id", required=True)
    create_apply.add_argument("--authorization-reference", required=True)
    create_apply.add_argument(
        "--decision-summary",
        default="项目维护者确认 Jira 写入计划",
    )
    create_readback = create_actions.add_parser("readback")
    create_readback.add_argument("--issue-key", required=True)
    create_readback.add_argument("--idempotency-key", required=True)
    create_readback.add_argument("--plan-file", required=True)
    create_readback.add_argument("--confirm-plan-id", required=True)

    comment_parser = jira_commands.add_parser("comment")
    _configure_write_actions(comment_parser, "comment")

    description_parser = jira_commands.add_parser("description")
    _configure_write_actions(description_parser, "description")

    worklog_parser = jira_commands.add_parser("worklog")
    _configure_write_actions(worklog_parser, "worklog")

    transition_parser = jira_commands.add_parser("transition")
    _configure_write_actions(transition_parser, "transition")


def _configure_write_actions(
    parser: argparse.ArgumentParser, kind: str, *, readback: bool = True
) -> None:
    actions = parser.add_subparsers(dest="action", required=True)
    plan = actions.add_parser("plan")
    plan.add_argument("--issue-key", required=True)
    plan.add_argument("--idempotency-key", required=True)
    plan.add_argument("--run-id")
    plan.add_argument("--plan-file", required=True)
    if kind == "comment":
        plan.add_argument("--category", required=True)
        plan.add_argument("--content-file", required=True)
    elif kind == "description":
        plan.add_argument("--content-file", required=True)
    elif kind == "transition":
        target = plan.add_mutually_exclusive_group(required=True)
        target.add_argument("--target-status")
        target.add_argument("--target-transition")
        target.add_argument("--transition-id")
        plan.add_argument("--comment-content-file")
    else:
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

    apply = actions.add_parser("apply")
    apply.add_argument("--plan-file", required=True)
    apply.add_argument("--confirm-plan-id", required=True)
    apply.add_argument("--authorization-reference", required=True)
    apply.add_argument(
        "--decision-summary",
        default="项目维护者确认 Jira 写入计划",
    )

    if readback:
        readback_parser = actions.add_parser("readback")
        readback_parser.add_argument("--issue-key", required=True)
        readback_parser.add_argument("--idempotency-key", required=True)
        readback_parser.add_argument("--plan-file", required=True)
        readback_parser.add_argument("--confirm-plan-id", required=True)


def execute_jira(args: argparse.Namespace, source_root: Path) -> dict[str, Any]:
    if args.command != "auth":
        _validate_jira_scope_preflight(args, source_root)
    config = load_maintainer_jira_config(source_root)
    if args.command == "auth":
        return _execute_auth(args, source_root, config)

    email, token = config.require_credentials()
    client = JiraClient(
        config.connection,
        UrllibJiraTransport(config.connection, email, token),
    )
    service = MaintainerJiraService(client)

    if args.command == "inspect":
        issue = service.inspect_issue(args.issue_key)
        description = adf_to_markdown(issue.description)
        return {
            "connection_id": config.connection.connection_id,
            "issue": {
                "jira_issue_id": issue.issue_id,
                "issue_key": issue.key,
                "project_key": issue.project_key,
                "summary": issue.summary,
                "status": issue.status,
                "issue_type": issue.issue_type,
                "assignee": issue.assignee,
                "description": {
                    "format": "atlassian_adf",
                    "adf": issue.description,
                    "markdown": description.markdown,
                    "plain_text": description.plain_text,
                    "complete": description.complete,
                    "unsupported_node_types": list(
                        description.unsupported_node_types
                    ),
                    "sha256": _description_sha256(issue.description),
                },
            },
            "credential_status": config.credential_status(),
        }

    if args.action == "plan":
        plan_path = _plan_file(
            source_root, args.plan_file, must_exist=False
        )
        run_id = args.run_id or _generate_run_id()
        if args.command == "create":
            description = ""
            if args.description_file:
                description = _read_input_file(
                    source_root, args.description_file, "Jira 任务描述文件"
                )
            extra_fields = _parse_extra_fields(args.field)
            plan = service.plan_create_issue(
                args.project_key,
                args.idempotency_key,
                maintainer_run_id=run_id,
                issuetype_name=args.issuetype,
                summary=args.summary,
                description=description,
                assignee=args.assignee,
                parent_key=args.parent,
                extra_fields=extra_fields,
            )
        elif args.command == "comment":
            content = _read_input_file(source_root, args.content_file, "Jira 评论内容文件")
            plan = service.plan_comment(
                args.issue_key,
                args.idempotency_key,
                args.category,
                content,
                maintainer_run_id=run_id,
                comment_template_schema=load_comment_template_schema(source_root),
            )
        elif args.command == "description":
            content = _read_input_file(source_root, args.content_file, "Jira 任务描述内容文件")
            plan = service.plan_description(
                args.issue_key,
                args.idempotency_key,
                content,
                maintainer_run_id=run_id,
            )
        elif args.command == "transition":
            workflow = load_maintainer_workflow(
                source_root, config.connection.connection_id
            )
            comment = None
            if args.comment_content_file:
                comment = _read_input_file(
                    source_root,
                    args.comment_content_file,
                    "Jira 状态流转说明评论文件",
                )
            plan = service.plan_transition(
                args.issue_key,
                args.idempotency_key,
                maintainer_run_id=run_id,
                workflow=workflow,
                target_status=args.target_status,
                target_transition=args.target_transition,
                transition_id=args.transition_id,
                comment=comment,
            )
        else:
            details = _read_input_file(source_root, args.details_file, "Jira Worklog 内容文件")
            included_work_content = _read_input_file(
                source_root, args.included_work_file, "Jira Worklog 耗时组成文件"
            )
            plan = service.plan_worklog(
                args.issue_key,
                args.idempotency_key,
                args.title,
                details,
                args.time_spent_seconds,
                args.started,
                args.exclude_waiting,
                maintainer_run_id=run_id,
                included_work=_read_included_work(included_work_content),
                excluded_waiting_categories=args.excluded_waiting_category,
            )
        service.validate_no_credentials(plan, email, token)
        _write_new_plan(plan_path, plan.to_dict())
        try:
            maintainer_identity = load_maintainer_identity(source_root)
            agent_id = maintainer_identity["agent_id"]
            agent_type = maintainer_identity.get("agent_type", "")
            model = maintainer_identity.get("model", "")
            environment = maintainer_identity.get("environment", "")
        except RuntimeErrorResult:
            agent_id = ""
            agent_type = ""
            model = ""
            environment = ""
        result: dict[str, Any] = {
            "connection_id": config.connection.connection_id,
            "issue_key": plan.issue_key,
            "plan_id": plan.plan_id,
            "action": plan.action,
            "content_sha256": plan.content_sha256,
            "plan_file": str(plan_path),
            "maintainer_run_id": plan.maintainer_run_id,
            "agent_id": agent_id,
            "agent_type": agent_type,
            "model": model,
            "environment": environment,
            "authorization_guidance": (
                "请人工审查计划后执行 apply，并显式提供确认引用 "
                "（例如 user-confirmation:AO-11:<plan_id>）"
            ),
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
        elif args.command == "create":
            result.update(
                {
                    "project_key": plan.payload.get("project_key"),
                    "issuetype_name": plan.payload.get("issuetype_name"),
                    "summary": plan.payload.get("summary"),
                    "assignee": plan.payload.get("assignee"),
                    "parent": plan.payload.get("parent"),
                }
            )
        return result

    plan_path, path_issue_key, plan = _read_plan_candidate(source_root, args.plan_file)
    if args.action == "apply":
        plan = _read_plan(plan_path)
        if plan.issue_key != path_issue_key:
            raise RuntimeErrorResult(
                code="jira_write_plan_mismatch",
                message="Jira 计划文件与路径绑定不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请重新生成计划文件后 apply",
            )
        if args.confirm_plan_id != plan.plan_id:
            raise RuntimeErrorResult(
                code="jira_write_plan_mismatch",
                message="Jira 计划确认 ID 与计划文件不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请核对 plan 输出并重新确认",
            )
        _validate_authorization_reference(
            args.authorization_reference, plan, source_root
        )
        decision_created = _append_decision(
            source_root,
            plan,
            args.decision_summary,
            args.authorization_reference,
        )
        if args.command == "comment":
            result = service.apply_comment(plan, args.confirm_plan_id)
        elif args.command == "description":
            result = service.apply_description(plan, args.confirm_plan_id)
        elif args.command == "transition":
            result = service.apply_transition(plan, args.confirm_plan_id)
        elif args.command == "create":
            result = service.apply_create_issue(plan, args.confirm_plan_id)
        else:
            result = service.apply_worklog(plan, args.confirm_plan_id)
        return {
            "connection_id": config.connection.connection_id,
            "issue_key": plan.issue_key,
            "plan_id": plan.plan_id,
            "decision_recorded": decision_created,
            "authorization_reference": args.authorization_reference,
            **result,
        }

    # readback
    if args.command == "create":
        if (
            not args.issue_key.startswith(str(plan.issue_key) + "-")
            or args.idempotency_key != plan.idempotency_key
        ):
            raise RuntimeErrorResult(
                code="jira_write_plan_mismatch",
                message="Jira 回读输入与写入计划不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请使用 apply 输出绑定的 Issue、幂等键和计划文件",
            )
    elif (
        args.issue_key != plan.issue_key
        or args.idempotency_key != plan.idempotency_key
    ):
        raise RuntimeErrorResult(
            code="jira_write_plan_mismatch",
            message="Jira 回读输入与写入计划不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请使用 plan 输出绑定的 Issue、幂等键和计划文件",
        )
    if args.confirm_plan_id != plan.plan_id:
        raise RuntimeErrorResult(
            code="jira_write_plan_mismatch",
            message="Jira 回读确认 ID 与计划文件不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请核对 plan 输出并重新确认",
        )
    if args.command == "comment":
        result = service.readback_comment(plan)
    elif args.command == "description":
        result = service.readback_description(plan)
    elif args.command == "transition":
        result = service.readback_transition(plan)
    elif args.command == "create":
        result = service.readback_create_issue(plan, args.issue_key)
    else:
        result = service.readback_worklog(plan)
    return {
        "connection_id": config.connection.connection_id,
        "issue_key": plan.issue_key,
        "plan_id": plan.plan_id,
        **result,
    }


def _execute_auth(
    args: argparse.Namespace,
    source_root: Path,
    config: Any,
) -> dict[str, Any]:
    if args.action == "show":
        status = credential_status(source_root, config.connection)
        return _with_maintainer_auth_scope(status)
    if args.action == "set":
        return _set_auth(args, source_root, config)
    if args.action == "remove":
        return _with_maintainer_auth_scope(
            remove_credentials(source_root, config.connection, args.field)
        )
    if args.action == "verify":
        status = credential_status(source_root, config.connection)
        email, token = config.require_credentials()
        client = JiraClient(
            config.connection,
            UrllibJiraTransport(config.connection, email, token),
        )
        current_user = client.current_user()
        return {
            "connection_id": config.connection.connection_id,
            "base_url": config.connection.base_url,
            "verified": True,
            "jira_user": current_user,
            "account_scope": "maintainer",
            "allowed_project_keys": [MAINTAINER_JIRA_PROJECT_KEY],
            "credential_source": status["credential_source"],
        }
    raise RuntimeErrorResult(
        code="authorization_action_unsupported",
        message="不支持的授权操作",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请使用 jira auth show、set、remove 或 verify",
    )


def _set_auth(
    args: argparse.Namespace,
    source_root: Path,
    config: Any,
) -> dict[str, Any]:
    interactive = args.interactive or (
        args.email is None and not args.token_stdin and sys.stdin.isatty()
    )
    email = args.email
    token: str | None = None
    if interactive:
        status = credential_status(source_root, config.connection)
        current_email = status.get("email_hint")
        sys.stderr.write(
            f"AgenticOps：maintainer Jira email [{current_email or '未设置'}]，留空表示保留： "
        )
        sys.stderr.flush()
        entered_email = sys.stdin.readline().strip()
        email = entered_email or None
        entered_token = getpass.getpass(
            "AgenticOps：maintainer Jira API token（留空表示保留）： ", stream=sys.stderr
        )
        token = entered_token or None
    elif args.token_stdin:
        token = sys.stdin.readline().rstrip("\r\n")
        if not token:
            raise _input_error("authorization_token_empty", "标准输入中的 Jira token 为空")
    result = set_credentials(
        source_root,
        config.connection,
        email=email,
        token=token,
    )
    return _with_maintainer_auth_scope(result)


def _with_maintainer_auth_scope(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **result,
        "account_scope": "maintainer",
        "allowed_project_keys": [MAINTAINER_JIRA_PROJECT_KEY],
    }


def _validate_jira_scope_preflight(
    args: argparse.Namespace, source_root: Path
) -> None:
    """在加载 maintainer 凭证、联网或写审计前固定 AO 项目边界。"""
    if args.command == "inspect":
        validate_maintainer_issue_key(args.issue_key)
        return

    if args.action == "plan":
        if args.command == "create":
            validate_maintainer_project_key(args.project_key)
            if args.parent:
                validate_maintainer_issue_key(args.parent)
        else:
            validate_maintainer_issue_key(args.issue_key)
        return

    _, _, plan = _read_plan_candidate(source_root, args.plan_file)
    validate_write_plan_scope(plan)
    if args.action == "readback":
        validate_maintainer_issue_key(args.issue_key)


def _plan_file(
    source_root: Path, value: str, *, must_exist: bool = True
) -> Path:
    if not PLAN_FILE_PATTERN.fullmatch(value):
        raise RuntimeErrorResult(
            code="jira_plan_file_invalid",
            message="plan 文件必须是受管目录内的合法文件名",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请使用合法 plan 文件名（[A-Za-z0-9._-].json）",
        )
    directory = plans_dir(source_root)
    path = directory / value
    if must_exist and (not path.exists() or path.is_symlink()):
        raise RuntimeErrorResult(
            code="jira_plan_file_missing",
            message="Jira 计划文件不存在",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action="请先执行 plan 生成计划文件",
        )
    return path


def _read_plan_candidate(
    source_root: Path, value: str
) -> tuple[Path, str, WritePlan]:
    path = _plan_file(source_root, value)
    plan = _read_plan(path)
    return path, plan.issue_key, plan


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
    except RuntimeErrorResult:
        raise
    except (KeyError, OSError, TypeError, UnicodeError, ValueError) as error:
        raise RuntimeErrorResult(
            code="jira_write_plan_invalid",
            message=f"Jira 写入计划文件无效：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 plan，不要人工拼接计划文件",
        ) from error


def _write_new_plan(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = _mkstemp(path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _mkstemp(path: Path) -> tuple[int, str]:
    import tempfile

    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    return descriptor, temporary


def _append_decision(
    source_root: Path,
    plan: WritePlan,
    decision_summary: str,
    authorization_reference: str,
) -> bool:
    decisions_path = plans_dir(source_root).parent / DECISIONS_FILE
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "operation": plan.operation,
        "issue_key": plan.issue_key,
        "maintainer_run_id": plan.maintainer_run_id,
        "plan_id": plan.plan_id,
        "content_sha256": plan.content_sha256,
        "decision_summary": decision_summary,
        "authorization_reference": authorization_reference,
    }
    with TaskLock(decisions_path.parent / ".decisions.lock", timeout=5):
        decisions_path.parent.mkdir(parents=True, exist_ok=True)
        with decisions_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return True


def _validate_authorization_reference(
    value: str, plan: WritePlan, source_root: Path
) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeErrorResult(
            code="jira_write_authorization_required",
            message="Jira 写入缺少显式人工确认引用",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供当前交互中的明确确认引用（user-confirmation:<KEY>:<plan_id>）",
        )
    if value.startswith("work-authorization:"):
        from ao_maint.takeover.state import validate_work_authorization

        validate_work_authorization(
            source_root,
            value,
            issue_key=plan.issue_key,
            operation=plan.operation,
        )
        return
    if not value.startswith("user-confirmation:"):
        raise RuntimeErrorResult(
            code="jira_write_authorization_required",
            message="Jira 写入确认引用必须使用 user-confirmation 格式",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供 user-confirmation:<KEY>:<plan_id> 格式的确认引用",
        )
    if not value.endswith(plan.plan_id):
        raise RuntimeErrorResult(
            code="jira_write_authorization_required",
            message="Jira 写入确认引用必须绑定当前 plan_id",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请使用当前 plan 输出的 plan_id 作为确认引用末段",
        )


def _read_input_file(source_root: Path, value: str, label: str) -> str:
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else source_root / supplied
    candidate = candidate.absolute()
    try:
        info = candidate.lstat()
    except OSError as error:
        raise RuntimeErrorResult(
            code="jira_input_file_missing",
            message=f"{label}无法读取：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供可读的内容文件",
        ) from error
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or candidate.is_symlink():
        raise RuntimeErrorResult(
            code="jira_input_file_invalid",
            message=f"{label}必须是单链接普通文件",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供普通文件路径",
        )
    if info.st_size > 1024 * 1024:
        raise RuntimeErrorResult(
            code="jira_input_file_invalid",
            message=f"{label}超过大小限制",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请缩小内容后重试",
        )
    try:
        return candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RuntimeErrorResult(
            code="jira_input_file_invalid",
            message=f"{label}不是有效 UTF-8 文本：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供 UTF-8 文本文件",
        ) from error


def _parse_extra_fields(values: list[str]) -> dict[str, Any]:
    """解析 --field KEY=VALUE 参数为字典。

    值为合法 JSON 时按 JSON 解析（如 '{"value": "研发模式"}'），
    否则作为普通字符串透传（如 customfield_10353=研发模式）。
    """
    result: dict[str, Any] = {}
    for raw in values:
        key, separator, value = raw.partition("=")
        if not separator or not key.strip():
            raise _input_error(
                "invalid_extra_field", "--field 必须是 KEY=VALUE 格式"
            )
        parsed: Any = value
        if value.strip().startswith(("{", "[", '"')):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = value
        if key.strip() in result:
            raise _input_error(
                "invalid_extra_field", f"--field 重复指定字段 {key.strip()}"
            )
        result[key.strip()] = parsed
    return result


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


def _reject_duplicate_object_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _generate_run_id() -> str:
    import hashlib

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    random_part = hashlib.sha256(os.urandom(16)).hexdigest()[:8]
    return f"maint-{stamp}-{random_part}"


def _input_error(code: str, message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action="请修正输入后重试",
    )
