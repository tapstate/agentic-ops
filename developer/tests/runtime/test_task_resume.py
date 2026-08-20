from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_work.jira.adf import markdown_to_adf
from ao_work.jira.client import TransportResponse
from ao_work.output import RuntimeErrorResult
from ao_work.task_resume import _agent_id_from_journal, _latest_resumable, _resolve_local_context
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.work_cli import main


class ResumeTransport:
    """只读 Jira transport：resume 不写 Jira，只回读 myself/issue。"""

    def __init__(
        self,
        *,
        assignee: str = "jira-account-1",
        status: str = "正在进行",
    ) -> None:
        self.assignee = assignee
        self.status = status
        self.requests: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> TransportResponse:
        self.requests.append((method, path))
        if path == "/rest/api/3/myself":
            return TransportResponse(
                200,
                {"accountId": "jira-account-1", "displayName": "Harsen Test Bot"},
            )
        if path == "/rest/api/3/issue/TAP-12289" and method == "GET":
            fields: dict[str, object] = {
                "project": {"key": "TAP"},
                "summary": "减少 AgenticOps 配置阻塞",
                "status": {"name": self.status},
                "issuetype": {"name": "任务"},
                "assignee": {"accountId": self.assignee},
                "description": markdown_to_adf("从 Jira 自动读取任务信息。"),
            }
            return TransportResponse(
                200,
                {
                    "id": "12289",
                    "key": "TAP-12289",
                    "fields": fields,
                },
            )
        return TransportResponse(404, None)


class TaskResumeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.install = root / "install"
        self.workspace = root / "workspace"
        self.source = root / "source"
        self.workspace.mkdir()
        self.source.mkdir()
        connection = self.install / "developer/standards/connections/tapdata-cloud.yaml"
        profile = self.install / "developer/standards/projects/tapdata/profile.yaml"
        connection.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        connection.write_text(
            "connection_id: tapdata-cloud\n"
            "base_url: https://tapdata.atlassian.net\n"
            "auth:\n"
            "  email_env: TAPDATA_JIRA_EMAIL\n"
            "  token_env: TAPDATA_JIRA_TOKEN\n",
            encoding="utf-8",
        )
        profile.write_text(
            "profile_id: tapdata\n"
            "connection_id: tapdata-cloud\n"
            "jira:\n"
            "  project_key: TAP\n"
            "  issue_types: [任务]\n"
            "statuses:\n"
            "  打开: waiting_takeover\n"
            "  正在进行: implementation\n"
            "  完成: completed\n"
            "transitions:\n"
            "  start_progress:\n"
            "    name: Start Progress\n"
            "repositories:\n"
            "  default: tapdata/tapdata\n",
            encoding="utf-8",
        )
        state = self.workspace / ".agentic-ops"
        (state / "profiles").mkdir(parents=True)
        (state / "agent.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "workplane": "developer",
                    "agent_id": "harsen-mini-test-bot",
                    "project_profile": "tapdata",
                    "jira_project": "TAP",
                    "connection_id": "tapdata-cloud",
                    "jira_base_url": "https://tapdata.atlassian.net",
                    "jira_site": "tapdata.atlassian.net",
                    "jira_account_id": "jira-account-1",
                    "source_root": str(self.source.resolve()),
                    "repository": "tapdata/tapdata",
                    "execution_identity": {
                        "git_author_name": "Harsen Test Bot",
                        "git_author_email": "harsen@example.test",
                        "git_committer_name": "Harsen Test Bot",
                        "git_committer_email": "harsen@example.test",
                        "github_actor_login": "harsen-mini-test-bot",
                    },
                }
            ),
            encoding="utf-8",
        )
        (state / "profiles/tapdata.local.yaml").write_text(
            "workspace:\n"
            f"  source_root: {self.source.resolve()}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )
        (state / ".env").write_text(
            "TAPDATA_JIRA_EMAIL=harsen@example.test\n"
            "TAPDATA_JIRA_TOKEN=test-token-secret\n",
            encoding="utf-8",
        )

    def run_cli(
        self, transport: ResumeTransport, *extra: str
    ) -> tuple[int, dict[str, object], str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch("ao_work.work_cli.validate_install_root", return_value=self.install),
            mock.patch(
                "ao_work.task_resume.UrllibJiraTransport",
                return_value=transport,
            ),
        ):
            code = main(
                (
                    "--workspace-root",
                    str(self.workspace),
                    "task",
                    "resume",
                    *extra,
                )
            )
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return code, json.loads(lines[0]), stderr.getvalue()

    def _seed_takeover_state(
        self,
        *,
        stage: str = "takeover_started",
        agentic_run_id: str = "run-TAP-12289-abc123",
        agent_id: str = "harsen-mini-test-bot",
    ) -> None:
        """构造与 task takeover 落盘一致的本地状态（task.json + journal takeover 事件）。"""
        store = TaskStore(self.workspace)
        store.initialize(
            TaskIdentity(
                connection_id="tapdata-cloud",
                jira_issue_id="12289",
                issue_key="TAP-12289",
                project_key="TAP",
                agentic_run_id=agentic_run_id,
            )
        )
        store.record_gate_transition(
            "TAP-12289",
            agentic_run_id,
            stage=stage,
            next_action="run_development" if stage == "takeover_started" else "resolve_blocker",
            operation="takeover_task",
            status="completed",
            evidence={
                "agent_id": agent_id,
                "jira_status_before": "打开",
                "jira_status_after": "正在进行",
                "transition_used": True,
            },
        )

    def test_resume_by_issue_key_returns_context(self) -> None:
        self._seed_takeover_state()
        transport = ResumeTransport()
        code, result, _ = self.run_cli(transport, "--issue-key", "TAP-12289")
        self.assertEqual(0, code)
        self.assertEqual("task_resume", result["operation"])
        self.assertEqual("TAP-12289", result["issue_key"])
        self.assertEqual("run-TAP-12289-abc123", result["agentic_run_id"])
        self.assertEqual("harsen-mini-test-bot", result["agent_id"])
        self.assertEqual("正在进行", result["jira_status"])
        self.assertEqual("implementation", result["jira_status_stage"])
        self.assertEqual("takeover_started", result["current_stage"])
        self.assertEqual(
            "resume_task_from_recorded_state",
            result["agentic_next_action"]["action"],  # type: ignore[index]
        )
        self.assertNotIn(("GET", "/rest/api/3/field"), transport.requests)

    def test_resume_without_args_picks_latest_resumable(self) -> None:
        self._seed_takeover_state()
        transport = ResumeTransport()
        code, result, _ = self.run_cli(transport)
        self.assertEqual(0, code)
        self.assertEqual("TAP-12289", result["issue_key"])

    def test_resume_by_run_id(self) -> None:
        self._seed_takeover_state()
        transport = ResumeTransport()
        code, result, _ = self.run_cli(
            transport, "--agentic-run-id", "run-TAP-12289-abc123"
        )
        self.assertEqual(0, code)
        self.assertEqual("TAP-12289", result["issue_key"])

    def test_resume_blocks_when_assignee_changed(self) -> None:
        self._seed_takeover_state()
        transport = ResumeTransport(assignee="other-user")
        code, result, _ = self.run_cli(transport, "--issue-key", "TAP-12289")
        self.assertEqual(2, code)
        self.assertEqual("assignee_changed", result["code"])

    def test_resume_blocks_when_stage_not_allowed(self) -> None:
        self._seed_takeover_state(stage="initialized")
        transport = ResumeTransport()
        code, result, _ = self.run_cli(transport, "--issue-key", "TAP-12289")
        self.assertEqual(2, code)
        self.assertEqual("resume_stage_not_allowed", result["code"])

    def test_resume_blocks_when_no_local_record(self) -> None:
        transport = ResumeTransport()
        code, result, _ = self.run_cli(transport)
        self.assertEqual(2, code)
        self.assertEqual("run_not_found", result["code"])

    def test_resume_blocks_when_status_unmapped(self) -> None:
        self._seed_takeover_state()
        transport = ResumeTransport(status="未知状态")
        code, result, _ = self.run_cli(transport, "--issue-key", "TAP-12289")
        self.assertEqual(2, code)
        self.assertEqual("jira_status_mapping_missing", result["code"])


class TaskResumeUnitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        (self.workspace / ".agentic-ops" / "tasks" / "TAP-1").mkdir(parents=True)

    def _write_state(self, task: dict, progress: dict, journal_lines: list[str]) -> None:
        task_dir = self.workspace / ".agentic-ops" / "tasks" / "TAP-1"
        (task_dir / "task.json").write_text(json.dumps(task), encoding="utf-8")
        (task_dir / "progress.json").write_text(json.dumps(progress), encoding="utf-8")
        (task_dir / "journal.ndjson").write_text(
            "\n".join(journal_lines) + "\n", encoding="utf-8"
        )

    def test_agent_id_read_from_takeover_journal_event(self) -> None:
        task = {
            "issue_key": "TAP-1",
            "agentic_run_id": "run-1",
            "project_key": "TAP",
        }
        progress = {"stage": "takeover_started", "agentic_next_action": "run_development"}
        journal = [
            json.dumps(
                {
                    "operation": "task_init",
                    "issue_key": "TAP-1",
                    "agentic_run_id": "run-1",
                    "evidence": {},
                }
            ),
            json.dumps(
                {
                    "operation": "takeover_task",
                    "issue_key": "TAP-1",
                    "agentic_run_id": "run-1",
                    "evidence": {"agent_id": "harsen-mini-test-bot"},
                }
            ),
        ]
        self._write_state(task, progress, journal)
        task_dir = self.workspace / ".agentic-ops" / "tasks" / "TAP-1"
        self.assertEqual("harsen-mini-test-bot", _agent_id_from_journal(task_dir))

    def test_latest_resumable_picks_newest_updated(self) -> None:
        def write(key: str, stage: str, updated: str) -> None:
            task_dir = self.workspace / ".agentic-ops" / "tasks" / key
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / "task.json").write_text(
                json.dumps(
                    {
                        "issue_key": key,
                        "agentic_run_id": f"run-{key}",
                        "project_key": "TAP",
                        "updated_at": updated,
                    }
                ),
                encoding="utf-8",
            )
            (task_dir / "progress.json").write_text(
                json.dumps({"stage": stage, "agentic_next_action": "x", "updated_at": updated}),
                encoding="utf-8",
            )
            (task_dir / "journal.ndjson").write_text("", encoding="utf-8")

        write("TAP-1", "takeover_started", "2026-08-19T01:00:00Z")
        write("TAP-2", "blocked", "2026-08-19T03:00:00Z")
        write("TAP-3", "initialized", "2026-08-19T02:00:00Z")
        store = TaskStore(self.workspace)
        latest = _latest_resumable(store)
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual("TAP-2", latest["task"]["issue_key"])

    def test_resolve_local_context_by_run_id(self) -> None:
        self._write_state(
            {"issue_key": "TAP-1", "agentic_run_id": "run-1", "project_key": "TAP"},
            {"stage": "takeover_started"},
            [],
        )
        store = TaskStore(self.workspace)
        state = _resolve_local_context(store, issue_key=None, agentic_run_id="run-1")
        self.assertEqual("TAP-1", state["task"]["issue_key"])

    def test_resolve_local_context_unknown_run_id_raises(self) -> None:
        store = TaskStore(self.workspace)
        with self.assertRaises(RuntimeErrorResult) as captured:
            _resolve_local_context(store, issue_key=None, agentic_run_id="nope")
        self.assertEqual("run_not_found", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
