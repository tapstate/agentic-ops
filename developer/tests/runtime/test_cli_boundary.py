from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_work.work_cli import build_parser, main
from ao_work.output import RuntimeErrorResult


class DeveloperCliBoundaryTest(unittest.TestCase):
    MAINTAINER_COMMANDS = frozenset({"story", "release", "hotfix", "integration"})
    SWITCH_FLAGS = frozenset({"--mode", "--role", "--workplane"})

    def test_ao_work_exposes_only_developer_commands(self) -> None:
        parser = build_parser()
        commands = self._subcommands(parser)
        for expected in ("workspace", "auth", "jira", "task", "task-run", "report", "capability", "version"):
            self.assertIn(expected, commands)
        self.assertEqual(set(), commands & self.MAINTAINER_COMMANDS)

    def test_no_parser_level_can_switch_workplane(self) -> None:
        parser = build_parser()
        self.assertEqual(set(), self._all_option_strings(parser) & self.SWITCH_FLAGS)

    def test_ao_work_rejects_switch_flags(self) -> None:
        for flag in self.SWITCH_FLAGS:
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main([flag, "maintainer", "workspace", "inspect"])
            self.assertEqual(2, exit_code)
            self.assertEqual("invalid_arguments", json.loads(stdout.getvalue())["code"])

    def test_source_workspace_is_blocked_before_developer_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / ".agentic-ops-source"
            marker.write_text("maintainer\n", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                mock.patch(
                    "ao_work.work_cli.validate_install_root",
                    return_value=Path("/synthetic-developer-install"),
                ),
                mock.patch("ao_work.work_cli.execute_workspace_preflight") as preflight,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                exit_code = main(
                    [
                        "--workspace-root",
                        str(root),
                        "workspace",
                        "preflight",
                    ]
                )

            self.assertEqual(2, exit_code)
            self.assertEqual("workplane_mismatch", json.loads(stdout.getvalue())["code"])
            preflight.assert_not_called()
            self.assertFalse((root / ".agentic-ops").exists())

    def test_every_operation_validates_default_install_identity(self) -> None:
        cases = (
            ["capability", "list"],
            ["version"],
            ["workspace", "inspect"],
            ["task", "inspect", "--issue-key", "TAP-1"],
            ["report", "write", "--issue-key", "TAP-1", "--agentic-run-id", "run-1", "--kind", "analysis", "--content-file", "analysis.md"],
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with (
                    mock.patch(
                        "ao_work.work_cli.validate_install_root",
                        side_effect=RuntimeErrorResult(
                            code="install_origin_mismatch",
                            message="测试安装身份不可信",
                            status="blocked",
                            exit_code=2,
                            required_human_action="重新安装",
                        ),
                    ) as validate,
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    exit_code = main(arguments)
                self.assertEqual(2, exit_code)
                self.assertEqual("install_origin_mismatch", json.loads(stdout.getvalue())["code"])
                validate.assert_called_once_with()

    def test_install_root_is_not_a_public_option(self) -> None:
        self.assertNotIn("--install-root", self._all_option_strings(build_parser()))

    def test_repository_confirmation_uses_id_and_domain_not_a_managed_path(self) -> None:
        parsed = build_parser().parse_args(
            [
                "task",
                "repositories",
                "confirm",
                "--issue-key",
                "TAP-123",
                "--confirmation-id",
                "rc_" + "a" * 32,
                "--task-domain",
                "product",
                "--confirm",
            ]
        )

        self.assertEqual("rc_" + "a" * 32, parsed.confirmation_id)
        self.assertEqual("product", parsed.task_domain)
        self.assertIsNone(parsed.mapping_file)

    def test_auth_routes_to_workspace_setup_without_preflight(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch(
                "ao_work.work_cli.validate_install_root",
                return_value=Path("/synthetic-developer-install"),
            ),
            mock.patch(
                "ao_work.work_cli.execute_authorization",
                return_value={"configured": True},
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["auth", "--show"])

        self.assertEqual(0, exit_code, stderr.getvalue())
        payload = json.loads(stdout.getvalue())
        self.assertEqual("auth", payload["operation"])
        self.assertEqual(
            "initialize_or_inspect_workspace",
            payload["agentic_next_action"]["action"],
        )
        self.assertEqual(
            ["workspace_init", "workspace_inspect"],
            payload["agentic_next_action"]["allowed_operations"],
        )

    def _subcommands(self, parser: argparse.ArgumentParser) -> set[str]:
        actions = [
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        ]
        self.assertEqual(1, len(actions), "CLI 顶层必须只有一个 subparser 注册表")
        return set(actions[0].choices)

    def _all_option_strings(self, parser: argparse.ArgumentParser) -> set[str]:
        options: set[str] = set()
        pending = [parser]
        visited: set[int] = set()
        while pending:
            current = pending.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))
            for action in current._actions:
                options.update(action.option_strings)
                if isinstance(action, argparse._SubParsersAction):
                    pending.extend(action.choices.values())
        return options


if __name__ == "__main__":
    unittest.main()
