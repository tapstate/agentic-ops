from __future__ import annotations

import argparse
import sys
from typing import Sequence

from ao_maint.cli_common import ArgumentParserError, JsonArgumentParser
from ao_maint.integration.cli import configure_integration_parser, execute_integration
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult, failure, success, write_diagnostic, write_json
from ao_maint.story_gate.cli import configure_story_parser, execute_story
from ao_maint.workspace import resolve_maintainer_workspace


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="ao-maint", add_help=True)
    parser.add_argument("--source-root", default=".")
    subparsers = parser.add_subparsers(dest="group", required=True)
    configure_story_parser(subparsers)
    configure_integration_parser(subparsers)
    return parser


def operation_name(args: argparse.Namespace | None) -> str:
    if args is None:
        return "cli"
    parts = [getattr(args, "group", None), getattr(args, "command", None)]
    return "_".join(part for part in parts if part) or "cli"


def execute(args: argparse.Namespace) -> dict[str, object]:
    workspace = resolve_maintainer_workspace(args.source_root)
    if args.group == "story":
        state = execute_story(args, workspace)
        return success(operation_name(args), workplane=workspace.workplane, **state)
    if args.group == "integration":
        state = execute_integration(args, workspace)
        return success(operation_name(args), workplane=workspace.workplane, **state)
    raise RuntimeErrorResult(
        code="capability_gap",
        message="当前 maintainer Runtime 尚未提供该操作",
        status="capability_gap",
        exit_code=3,
        required_human_action="请在 AO-11 中补充维护能力",
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
            required_human_action="请按 ao-maint 帮助修正参数后重试",
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
            message=f"维护 Runtime 处理失败：{error}",
            required_human_action="请保留脱敏诊断并检查 AgenticOps 源头工作区",
        )
        write_diagnostic(result.message)
        write_json(failure(operation_name(args), result))
        return result.exit_code
    except Exception as error:
        result = RuntimeErrorResult(
            code="runtime_unexpected_error",
            message=f"维护 Runtime 出现未预期错误（{type(error).__name__}）",
            required_human_action="请保留脱敏诊断并检查 AgenticOps 源头工作区",
        )
        write_diagnostic(result.message)
        write_json(failure(operation_name(args), result))
        return result.exit_code
    except KeyboardInterrupt:
        result = RuntimeErrorResult(
            code="operation_interrupted",
            message="维护操作被中断",
            retry_safe=True,
            required_human_action="请检查源头工作区状态后决定是否重试",
        )
        write_diagnostic(result.message)
        write_json(failure(operation_name(args), result))
        return result.exit_code
