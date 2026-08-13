from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from agentic_ops.cli import main
from test_jira_service import FakeTransport


class JiraCliTest(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(arguments)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return exit_code, json.loads(lines[0]), stderr.getvalue()

    def prepare(self, root: Path) -> tuple[Path, Path]:
        install = root / "install"
        workspace = root / "workspace"
        connection = install / "standards" / "connections" / "tap-cloud.yaml"
        profile = install / "standards" / "projects" / "demo" / "profile.yaml"
        connection.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        connection.write_text(
            """\
connection_id: tap-cloud
base_url: https://jira.example.test
auth:
  email_env: TEST_JIRA_EMAIL
  token_env: TEST_JIRA_TOKEN
""",
            encoding="utf-8",
        )
        profile.write_text(
            """\
profile_id: demo
connection_id: tap-cloud
jira:
  project_key: TAP
  issue_types: [任务]
fields:
  issue_analysis:
    source: jira_field
    jira_field: customfield_10092
    state: active
  fix_details:
    source: jira_field
    jira_field: customfield_10093
    state: active
statuses: {}
transitions: {}
""",
            encoding="utf-8",
        )
        agent = workspace / ".agentic-ops" / "agent.json"
        agent.parent.mkdir(parents=True)
        agent.write_text(
            json.dumps(
                {
                    "mode": "project_execution",
                    "project_profile": "demo",
                    "connection_id": "tap-cloud",
                }
            ),
            encoding="utf-8",
        )
        return install, workspace

    def test_cli_comment_plan_apply_readback_and_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = (
                "--workspace-root",
                str(workspace),
                "--mode",
                "project_execution",
                "--install-root",
                str(install),
            )
            initialized = self.run_cli(
                *common,
                "task",
                "init",
                "--connection-id",
                "tap-cloud",
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
            content = workspace / "comment.md"
            content.write_text("## 分析\n\n确认需要补充 Jira 写入回读。\n", encoding="utf-8")
            plan_file = ".agentic-ops/tasks/TAP-123/runs/run-1/comment-plan.json"
            transport = FakeTransport()
            environment = {"TEST_JIRA_EMAIL": "owner@example.test", "TEST_JIRA_TOKEN": "secret"}
            with mock.patch.dict(os.environ, environment, clear=False), mock.patch(
                "agentic_ops.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                planned = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "plan",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-analysis",
                    "--category",
                    "analysis",
                    "--content-file",
                    "comment.md",
                    "--plan-file",
                    plan_file,
                )
                self.assertEqual(0, planned[0])
                plan_id = str(planned[1]["plan_id"])
                applied = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                    "--authorization-reference",
                    "jira-comment-approval-1",
                )
                self.assertEqual(0, applied[0])
                self.assertEqual(True, applied[1]["created"])
                self.assertEqual(True, applied[1]["decision_recorded"])
                readback = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "readback",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-analysis",
                )
                self.assertEqual(0, readback[0])
            sync_path = workspace / ".agentic-ops" / "tasks" / "TAP-123" / "sync.json"
            sync = json.loads(sync_path.read_text(encoding="utf-8"))
            self.assertIn("jira_comment:run-1-analysis", sync["external_writes"])
            decisions_path = workspace / ".agentic-ops" / "tasks" / "TAP-123" / "decisions.ndjson"
            decisions = decisions_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(decisions))
            self.assertEqual(
                "jira-comment-approval-1", json.loads(decisions[0])["reference"]
            )

    def test_plan_file_outside_runtime_state_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            content = workspace / "comment.md"
            content.write_text("分析内容使用中文。", encoding="utf-8")
            common = (
                "--workspace-root",
                str(workspace),
                "--mode",
                "project_execution",
                "--install-root",
                str(install),
            )
            with mock.patch.dict(
                os.environ,
                {"TEST_JIRA_EMAIL": "owner@example.test", "TEST_JIRA_TOKEN": "secret"},
                clear=False,
            ), mock.patch("agentic_ops.jira.cli.UrllibJiraTransport", return_value=FakeTransport()):
                blocked = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "plan",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-analysis",
                    "--category",
                    "analysis",
                    "--content-file",
                    "comment.md",
                    "--plan-file",
                    "tracked-plan.json",
                )
            self.assertEqual(2, blocked[0])
            self.assertEqual("jira_plan_path_not_managed", blocked[1]["code"])
