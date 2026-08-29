from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from internal.story_gate.errors import StoryGateError
from internal.story_gate.service import StoryGateService


def _change_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--change-source",
        choices=("staged", "worktree", "range"),
        default="worktree",
    )
    parser.add_argument("--base")
    parser.add_argument("--head")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="story-gate")
    parser.add_argument("--source-root", default=".")
    commands = parser.add_subparsers(dest="command", required=True)
    impact = commands.add_parser("impact")
    _change_arguments(impact)
    approve = commands.add_parser("approve")
    _change_arguments(approve)
    approve.add_argument("--impact-id", required=True)
    approve.add_argument("--authorization-reference", required=True)
    verify = commands.add_parser("verify")
    _change_arguments(verify)
    verify.add_argument("--progress", action="store_true")
    return parser


def _write(payload: dict[str, object], stream=sys.stdout) -> None:
    stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    stream.flush()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.source_root).resolve()
    service = StoryGateService(root)
    try:
        if args.command == "impact":
            result = service.inspect(args.change_source, base=args.base, head=args.head)
        elif args.command == "approve":
            result = service.approve(
                args.change_source,
                args.impact_id,
                args.authorization_reference,
                base=args.base,
                head=args.head,
            )
        else:
            result = service.verify(
                args.change_source,
                base=args.base,
                head=args.head,
                event_sink=_write if args.progress else None,
            )
        _write({"ok": True, "operation": "story_%s" % args.command, **result})
        return 0
    except StoryGateError as error:
        _write(
            {
                "ok": False,
                "operation": "story_%s" % args.command,
                "code": error.code,
                "status": error.status,
                "message": error.message,
                "required_human_action": error.required_human_action,
                **error.details,
            },
            sys.stderr,
        )
        return error.exit_code
    except (OSError, ValueError) as error:
        _write(
            {
                "ok": False,
                "operation": "story_%s" % args.command,
                "code": "story_gate_failed",
                "status": "failed",
                "message": str(error),
            },
            sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
