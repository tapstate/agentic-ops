from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from agentic_ops.cli import main


class CliTest(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(arguments)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return exit_code, json.loads(lines[0]), stderr.getvalue()

    def test_project_task_init_and_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"mode":"project_execution"}\n', encoding="utf-8")
            common = ("--workspace-root", str(root), "--mode", "project_execution")
            exit_code, initialized, _ = self.run_cli(
                *common,
                "task",
                "init",
                "--connection-id",
                "tapdata",
                "--jira-issue-id",
                "10001",
                "--issue-key",
                "TAP-123",
                "--project-key",
                "TAP",
                "--agentic-run-id",
                "run-1",
            )
            self.assertEqual(0, exit_code)
            self.assertEqual(True, initialized["created"])
            exit_code, inspected, _ = self.run_cli(
                *common, "task", "inspect", "--issue-key", "TAP-123"
            )
            self.assertEqual(0, exit_code)
            self.assertEqual("TAP-123", inspected["task"]["issue_key"])

    def test_source_mode_blocks_task_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "docs" / "strategy" / "project-goals.md"
            marker.parent.mkdir(parents=True)
            marker.write_text("# 目标\n", encoding="utf-8")
            exit_code, result, stderr = self.run_cli(
                "--workspace-root",
                str(root),
                "--mode",
                "source_maintenance",
                "task",
                "inspect",
                "--issue-key",
                "AO-11",
            )
            self.assertEqual(2, exit_code)
            self.assertEqual("workspace_mode_mismatch", result["code"])
            self.assertIn("AgenticOps：", stderr)

    def test_invalid_arguments_still_return_json(self) -> None:
        exit_code, result, stderr = self.run_cli("task", "inspect")
        self.assertEqual(2, exit_code)
        self.assertEqual("invalid_arguments", result["code"])
        self.assertIn("命令参数无效", stderr)

    def test_help_still_returns_one_json_object(self) -> None:
        exit_code, result, stderr = self.run_cli("--help")
        self.assertEqual(0, exit_code)
        self.assertEqual("help", result["operation"])
        self.assertIn("agentic-cli", result["usage"])
        self.assertEqual("", stderr)
