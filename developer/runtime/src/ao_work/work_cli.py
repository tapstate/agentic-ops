from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from ao_work.authorization.cli import configure_authorization_parser, execute_authorization
from ao_work.capabilities import configure_capability_parser, execute_capability
from ao_work.cli_common import ArgumentParserError, HelpRequested, JsonArgumentParser
from ao_work.jira.cli import configure_jira_parser, execute_jira
from ao_work.installation import validate_install_root
from ao_work.output import (
    EXIT_BLOCKED,
    RuntimeErrorResult,
    failure,
    success,
    write_diagnostic,
    write_json,
)
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.task_gate import execute_task_gate
from ao_work.task_start import execute_task_start
from ao_work.task_takeover import execute_task_takeover
from ao_work.task_resume import execute_task_resume
from ao_work.task_repository_scope import (
    execute_repository_assess,
    execute_repository_confirm,
    execute_worktree_cleanup,
    execute_worktree_prepare,
)
from ao_work.task_run import configure_task_run_parser, execute_task_run
from ao_work.workspace import DEVELOPER, resolve_developer_workspace
from ao_work.workspace_security import read_workspace_outbound_file
from ao_work.workspace_init import (
    configure_workspace_init_parser,
    execute_workspace_init,
    execute_workspace_preflight,
)
from ao_work.version import inspect_version


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="ao-work", add_help=True)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--lock-timeout", type=float, default=5.0)
    subparsers = parser.add_subparsers(dest="group", required=True)

    workspace_parser = subparsers.add_parser("workspace")
    workspace_commands = workspace_parser.add_subparsers(dest="command", required=True)
    workspace_commands.add_parser("inspect")
    configure_workspace_init_parser(workspace_commands)

    takeover = subparsers.add_parser("takeover")
    _configure_takeover_parser(takeover)

    task_parser = subparsers.add_parser("task")
    task_commands = task_parser.add_subparsers(
        dest="command",
        required=True,
        metavar="{start,intake,solution,init,resume,inspect,repositories,worktrees}",
    )
    task_start = task_commands.add_parser("start")
    task_start.add_argument("issue_key")
    task_intake = task_commands.add_parser("intake")
    task_intake_actions = task_intake.add_subparsers(dest="action", required=True)
    task_intake_assess = task_intake_actions.add_parser("assess")
    task_intake_assess.add_argument("--issue-key", required=True)
    task_intake_assess.add_argument("--agentic-run-id", required=True)
    task_intake_assess.add_argument("--input-file", required=True)
    task_solution = task_commands.add_parser("solution")
    task_solution_actions = task_solution.add_subparsers(dest="action", required=True)
    task_solution_classify = task_solution_actions.add_parser("classify")
    task_solution_classify.add_argument("--issue-key", required=True)
    task_solution_classify.add_argument("--agentic-run-id", required=True)
    task_solution_classify.add_argument("--input-file", required=True)
    task_init = task_commands.add_parser("init")
    task_init.add_argument("--connection-id", required=True)
    task_init.add_argument("--jira-issue-id", required=True)
    task_init.add_argument("--issue-key", required=True)
    task_init.add_argument("--project-key", required=True)
    task_init.add_argument("--agentic-run-id", required=True)
    task_resume = task_commands.add_parser("resume")
    resume_target = task_resume.add_mutually_exclusive_group()
    resume_target.add_argument("--issue-key")
    resume_target.add_argument("--agentic-run-id")
    task_inspect = task_commands.add_parser("inspect")
    task_inspect.add_argument("--issue-key", required=True)
    task_repositories = task_commands.add_parser("repositories")
    repository_actions = task_repositories.add_subparsers(dest="action", required=True)
    repository_assess = repository_actions.add_parser("assess")
    repository_assess.add_argument("--issue-key", required=True)
    repository_confirm = repository_actions.add_parser("confirm")
    repository_confirm.add_argument("--issue-key", required=True)
    repository_confirm.add_argument("--mapping-file", required=True)
    repository_confirm.add_argument("--confirm", action="store_true")
    task_worktrees = task_commands.add_parser("worktrees")
    worktree_actions = task_worktrees.add_subparsers(dest="action", required=True)
    worktree_prepare = worktree_actions.add_parser("prepare")
    worktree_prepare.add_argument("--issue-key", required=True)
    worktree_prepare.add_argument("--repository", required=True)
    worktree_cleanup = worktree_actions.add_parser("cleanup")
    worktree_cleanup.add_argument("--issue-key", required=True)

    report_parser = subparsers.add_parser("report")
    report_commands = report_parser.add_subparsers(dest="command", required=True)
    report_write = report_commands.add_parser("write")
    report_write.add_argument("--issue-key", required=True)
    report_write.add_argument("--agentic-run-id", required=True)
    report_write.add_argument("--kind", choices=("analysis", "plan"), required=True)
    report_write.add_argument("--content-file", required=True)
    configure_capability_parser(subparsers)
    configure_authorization_parser(subparsers)
    configure_jira_parser(subparsers)
    configure_task_run_parser(subparsers)
    subparsers.add_parser("version")
    return parser


def operation_name(args: argparse.Namespace | None) -> str:
    if args is None:
        return "cli"
    parts = [
        getattr(args, "group", None),
        getattr(args, "command", None),
        getattr(args, "action", None),
    ]
    return "_".join(part for part in parts if part) or "cli"


def execute(args: argparse.Namespace) -> dict[str, object]:
    install_root = validate_install_root()
    if args.group == "capability":
        state = execute_capability(args, install_root)
        return success(operation_name(args), **state)
    if args.group == "auth":
        state = execute_authorization(args, install_root)
        return success("auth", workplane=DEVELOPER, **state)
    if args.group == "version":
        return success("version", workplane=DEVELOPER, **inspect_version(install_root))
    if args.group == "workspace" and args.command == "init":
        state = execute_workspace_init(args, args.workspace_root, install_root)
        return success("workspace_init", workplane=DEVELOPER, **state)

    workspace = resolve_developer_workspace(args.workspace_root)
    operation = operation_name(args)
    if args.group == "workspace" and args.command == "inspect":
        return success(
            operation,
            workspace_root=str(workspace.root),
            workplane=workspace.workplane,
            config_path=str(workspace.config_path),
        )
    if args.group == "workspace" and args.command == "preflight":
        state = execute_workspace_preflight(workspace, install_root)
        return success(operation, workplane=workspace.workplane, **state)
    store = TaskStore(Path(workspace.root), lock_timeout=args.lock_timeout)
    if args.group == "takeover":
        state = execute_task_takeover(
            workspace,
            install_root,
            store,
            args.issue_key,
        )
        return success("takeover", workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "start":
        state = execute_task_start(
            workspace,
            install_root,
            store,
            args.issue_key,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "resume":
        state = execute_task_resume(
            workspace,
            install_root,
            store,
            issue_key=args.issue_key,
            agentic_run_id=args.agentic_run_id,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "repositories" and args.action == "assess":
        state = execute_repository_assess(
            workspace,
            install_root,
            store,
            args.issue_key,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "repositories" and args.action == "confirm":
        content = read_workspace_outbound_file(
            workspace.root,
            args.mapping_file,
            label="仓库分支确认文件",
        )
        try:
            mapping = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeErrorResult(
                code="repository_mapping_invalid",
                message=f"仓库分支确认文件不是有效 JSON：{error}",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请修正确认文件后重试",
            ) from error
        if not isinstance(mapping, dict):
            raise RuntimeErrorResult(
                code="repository_mapping_invalid",
                message="仓库分支确认文件必须是 JSON 对象",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请修正确认文件后重试",
            )
        state = execute_repository_confirm(
            workspace,
            install_root,
            store,
            args.issue_key,
            mapping,
            confirm=args.confirm,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "worktrees" and args.action == "prepare":
        state = execute_worktree_prepare(
            workspace,
            install_root,
            store,
            args.issue_key,
            args.repository,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "worktrees" and args.action == "cleanup":
        state = execute_worktree_cleanup(
            workspace,
            install_root,
            store,
            args.issue_key,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command in {"intake", "solution"}:
        state = execute_task_gate(args, workspace, store)
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "jira":
        state = execute_jira(args, workspace, install_root, store)
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task-run":
        state = execute_task_run(
            args,
            workspace,
            install_root,
            store,
            args.lock_timeout,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "report" and args.command == "write":
        content = read_workspace_outbound_file(
            workspace.root,
            args.content_file,
            label="报告内容文件",
        )
        state = store.write_report(
            args.issue_key,
            args.agentic_run_id,
            args.kind,
            content,
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "init":
        state = store.initialize(
            TaskIdentity(
                connection_id=args.connection_id,
                jira_issue_id=args.jira_issue_id,
                issue_key=args.issue_key,
                project_key=args.project_key,
                agentic_run_id=args.agentic_run_id,
            )
        )
        return success(operation, workplane=workspace.workplane, **state)
    if args.group == "task" and args.command == "inspect":
        state = store.inspect(args.issue_key)
        return success(operation, workplane=workspace.workplane, **state)
    raise RuntimeErrorResult(
        code="capability_gap",
        message="当前 developer Runtime 尚未提供该操作",
        status="capability_gap",
        exit_code=3,
        required_human_action="请在任务完成后提交 AgenticOps 能力改进建议",
    )


def _configure_takeover_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("issue_key", nargs="?")


def main(argv: Sequence[str] | None = None) -> int:
    return _run(argv, build_parser(), execute)


def _run(
    argv: Sequence[str] | None,
    parser: argparse.ArgumentParser,
    executor: object,
) -> int:
    args: argparse.Namespace | None = None
    try:
        arguments = list(argv) if argv is not None else sys.argv[1:]
        args = parser.parse_args(arguments)
        write_json(executor(args))  # type: ignore[operator]
        return 0
    except HelpRequested as error:
        write_json(success("help", usage=error.usage))
        return 0
    except ArgumentParserError as error:
        result = RuntimeErrorResult(
            code="invalid_arguments",
            message=f"命令参数无效：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action="请按命令帮助修正参数后重试",
        )
        write_diagnostic(result.message)
        write_json(failure(operation_name(args), result))
        return result.exit_code
    except RuntimeErrorResult as error:
        write_diagnostic(error.message)
        write_json(failure(operation_name(args), error))
        return error.exit_code
    except (OSError, ValueError) as error:
        result = RuntimeErrorResult(
            code="runtime_failed",
            message=f"Runtime 处理失败：{error}",
            required_human_action="请保留脱敏诊断并联系 AgenticOps 维护者",
        )
        write_diagnostic(result.message)
        write_json(failure(operation_name(args), result))
        return result.exit_code
    except Exception as error:
        result = RuntimeErrorResult(
            code="runtime_unexpected_error",
            message=f"Runtime 出现未预期错误（{type(error).__name__}）",
            required_human_action="请保留脱敏诊断并联系 AgenticOps 维护者",
        )
        write_diagnostic(result.message)
        write_json(failure(operation_name(args), result))
        return result.exit_code
    except KeyboardInterrupt:
        result = RuntimeErrorResult(
            code="operation_interrupted",
            message="操作被中断，已有快照未被覆盖",
            retry_safe=True,
            required_human_action="请检查当前任务状态后决定是否重试",
        )
        write_diagnostic(result.message)
        write_json(failure(operation_name(args), result))
        return result.exit_code
