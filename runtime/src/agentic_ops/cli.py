from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from agentic_ops.output import (
    EXIT_BLOCKED,
    RuntimeErrorResult,
    failure,
    success,
    write_diagnostic,
    write_json,
)
from agentic_ops.jira.cli import configure_jira_parser, execute_jira
from agentic_ops.task_state import TaskIdentity, TaskStore
from agentic_ops.workspace import PROJECT_EXECUTION, VALID_MODES, require_mode, resolve_workspace


class ArgumentParserError(Exception):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentParserError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="agentic-cli", add_help=True)
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--mode", choices=sorted(VALID_MODES))
    parser.add_argument("--install-root")
    parser.add_argument("--lock-timeout", type=float, default=5.0)
    subparsers = parser.add_subparsers(dest="group", required=True)

    workspace_parser = subparsers.add_parser("workspace")
    workspace_commands = workspace_parser.add_subparsers(dest="command", required=True)
    workspace_commands.add_parser("inspect")

    task_parser = subparsers.add_parser("task")
    task_commands = task_parser.add_subparsers(dest="command", required=True)
    task_init = task_commands.add_parser("init")
    task_init.add_argument("--connection-id", required=True)
    task_init.add_argument("--jira-issue-id", required=True)
    task_init.add_argument("--issue-key", required=True)
    task_init.add_argument("--project-key", required=True)
    task_init.add_argument("--agentic-run-id", required=True)
    task_inspect = task_commands.add_parser("inspect")
    task_inspect.add_argument("--issue-key", required=True)

    report_parser = subparsers.add_parser("report")
    report_commands = report_parser.add_subparsers(dest="command", required=True)
    report_write = report_commands.add_parser("write")
    report_write.add_argument("--issue-key", required=True)
    report_write.add_argument("--agentic-run-id", required=True)
    report_write.add_argument("--kind", choices=("analysis", "plan"), required=True)
    report_write.add_argument("--content-file", required=True)
    configure_jira_parser(subparsers)
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
    workspace = resolve_workspace(args.workspace_root, args.mode)
    operation = operation_name(args)
    if args.group == "workspace" and args.command == "inspect":
        return success(
            operation,
            workspace_root=str(workspace.root),
            workspace_mode=workspace.mode,
            config_path=str(workspace.config_path) if workspace.config_path else None,
        )

    require_mode(workspace, frozenset({PROJECT_EXECUTION}))
    store = TaskStore(Path(workspace.root), lock_timeout=args.lock_timeout)
    if args.group == "jira":
        state = execute_jira(args, workspace, args.install_root, store)
        return success(operation, workspace_mode=workspace.mode, **state)
    if args.group == "report" and args.command == "write":
        content_path = _workspace_content_file(workspace.root, args.content_file)
        state = store.write_report(
            args.issue_key,
            args.agentic_run_id,
            args.kind,
            content_path.read_text(encoding="utf-8"),
        )
        return success(operation, workspace_mode=workspace.mode, **state)
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
        return success(operation, workspace_mode=workspace.mode, **state)
    if args.group == "task" and args.command == "inspect":
        state = store.inspect(args.issue_key)
        return success(operation, workspace_mode=workspace.mode, **state)
    raise RuntimeErrorResult(
        code="capability_gap",
        message="当前 Python Runtime 尚未提供该操作",
        status="capability_gap",
        exit_code=3,
        required_human_action="请在任务完成后提交 AgenticOps 能力改进建议",
    )


def main(argv: Sequence[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    try:
        arguments = list(argv) if argv is not None else sys.argv[1:]
        parser = build_parser()
        if "--help" in arguments or "-h" in arguments:
            write_json(success("help", usage=parser.format_help()))
            return 0
        args = parser.parse_args(arguments)
        write_json(execute(args))
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


def _workspace_content_file(root: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RuntimeErrorResult(
            code="workspace_path_escape",
            message=f"内容文件越出项目工作空间：{value}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请把报告内容文件放在项目 AI 工作空间内",
        ) from error
    if not resolved.is_file():
        raise RuntimeErrorResult(
            code="workspace_file_not_found",
            message=f"内容文件不存在：{value}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请检查内容文件路径后重试",
        )
    return resolved


if __name__ == "__main__":
    sys.exit(main())
