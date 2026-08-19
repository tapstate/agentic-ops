from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_work.work_cli import main


class CliTest(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), mock.patch(
            "ao_work.work_cli.validate_install_root",
            return_value=Path("/synthetic-developer-install"),
        ):
            exit_code = main(arguments)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return exit_code, json.loads(lines[0]), stderr.getvalue()

    def test_project_task_init_and_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            common = ("--workspace-root", str(root))
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

    def test_source_repo_blocks_work_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / ".agentic-ops-source"
            marker.write_text("maintainer\n", encoding="utf-8")
            exit_code, result, stderr = self.run_cli(
                "--workspace-root",
                str(root),
                "task",
                "inspect",
                "--issue-key",
                "AO-11",
            )
            self.assertEqual(2, exit_code)
            self.assertEqual("workplane_mismatch", result["code"])
            self.assertIn("AgenticOps：", stderr)

    def test_report_write_uses_runtime_managed_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            common = ("--workspace-root", str(root))
            initialized = self.run_cli(
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
            self.assertEqual(0, initialized[0])
            content = root / "analysis-input.md"
            content.write_text("# 分析\n\n确认问题根因。\n", encoding="utf-8")
            exit_code, result, _ = self.run_cli(
                *common,
                "report",
                "write",
                "--issue-key",
                "TAP-123",
                "--agentic-run-id",
                "run-1",
                "--kind",
                "analysis",
                "--content-file",
                "analysis-input.md",
            )
            self.assertEqual(0, exit_code)
            self.assertTrue(Path(result["report_path"]).is_file())

    def test_cli_rejects_task_path_escape_without_outside_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            config = workspace / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            outside = root / "outside"
            outside.mkdir()
            exit_code, result, _ = self.run_cli(
                "--workspace-root",
                str(workspace),
                "task",
                "inspect",
                "--issue-key",
                "../../outside",
            )
            self.assertEqual(2, exit_code)
            self.assertEqual("invalid_task_identity", result["code"])
            self.assertEqual([], list(outside.iterdir()))

    def test_invalid_arguments_still_return_json(self) -> None:
        exit_code, result, stderr = self.run_cli("task", "inspect")
        self.assertEqual(2, exit_code)
        self.assertEqual("invalid_arguments", result["code"])
        self.assertIn("命令参数无效", stderr)

    def test_help_still_returns_one_json_object(self) -> None:
        exit_code, result, stderr = self.run_cli("--help")
        self.assertEqual(0, exit_code)
        self.assertEqual("help", result["operation"])
        self.assertIn("ao-work", result["usage"])
        self.assertEqual("", stderr)

    def test_subcommand_help_returns_its_own_usage(self) -> None:
        """-h/--help 必须透传给子解析器：task -h 显示 task 组帮助。

        2026-08-19 实踩（AO-26）：work_cli._run 曾对参数列表任意位置的
        -h/--help 直接输出顶层 usage，导致 task -h、jira -h 拿不到子命令帮助。
        """
        for group in ("task", "jira", "workspace", "task-run"):
            exit_code, result, stderr = self.run_cli(group, "-h")
            self.assertEqual(0, exit_code, group)
            self.assertEqual("help", result["operation"], group)
            self.assertIn(f"usage: ao-work {group}", str(result["usage"]), group)
            self.assertEqual("", stderr, group)

    def test_help_anywhere_in_arguments_targets_that_parser(self) -> None:
        """--help 出现在子命令之后时，应显示该子命令层级帮助而非顶层。"""
        exit_code, result, _ = self.run_cli("jira", "comment", "--help")
        self.assertEqual(0, exit_code)
        self.assertEqual("help", result["operation"])
        self.assertIn("usage: ao-work jira comment", str(result["usage"]))

    def test_complete_fake_git_install_cannot_be_selected_by_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            config = workspace / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            fabricated = root / "fabricated-install"
            for path in (
                fabricated / "developer/AGENTS.md",
                fabricated / "developer/bootstrap/ao-work",
                fabricated / "developer/pyproject.toml",
                fabricated / "developer/rules/ai-execution.md",
                fabricated / "developer/runtime/src/ao_work/__init__.py",
                fabricated / "developer/skills/example/SKILL.md",
                fabricated / "developer/standards/README.md",
                fabricated / "developer/uv.lock",
                fabricated / "shared/README.md",
                fabricated / "shared/integration/README.md",
                fabricated / "shared/integration/task-to-pr-manifest.schema.json",
                fabricated / "shared/integration/task-to-pr-event.schema.json",
                fabricated / "shared/integration/task-to-pr-result.schema.json",
                fabricated / ".python-version",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture\n", encoding="utf-8")
            subprocess.run(
                ["git", "init", "-b", "main", str(fabricated)],
                check=True,
                capture_output=True,
            )
            for arguments in (
                ("config", "user.email", "fake@example.test"),
                ("config", "user.name", "Fake Install"),
                ("remote", "add", "origin", "git@github.com:tapstate/agentic-ops.git"),
                ("add", "."),
                ("commit", "-m", "complete fake managed clone"),
                (
                    "sparse-checkout",
                    "set",
                    "--no-cone",
                    "/developer/AGENTS.md",
                    "/developer/bootstrap/",
                    "/developer/pyproject.toml",
                    "/developer/rules/",
                    "/developer/runtime/",
                    "/developer/skills/",
                    "/developer/standards/",
                    "/developer/uv.lock",
                    "/shared/integration/",
                    "/.python-version",
                ),
            ):
                subprocess.run(
                    ["git", "-C", str(fabricated), *arguments],
                    check=True,
                    capture_output=True,
                )
            head = subprocess.run(
                ["git", "-C", str(fabricated), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", str(fabricated), "update-ref", "refs/remotes/origin/main", head],
                check=True,
                capture_output=True,
            )
            (fabricated / ".local").mkdir()
            (fabricated / ".local/current-ref").write_text(head + "\n", encoding="utf-8")
            exit_code, result, _ = self.run_cli(
                "--workspace-root",
                str(workspace),
                "--install-root",
                str(fabricated),
                "task",
                "inspect",
                "--issue-key",
                "TAP-123",
            )
            self.assertEqual(2, exit_code)
            self.assertEqual("invalid_arguments", result["code"])
