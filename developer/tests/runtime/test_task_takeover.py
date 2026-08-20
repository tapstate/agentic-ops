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
from ao_work.task_takeover import _find_agentic_id_field
from ao_work.work_cli import main


class TakeoverTransport:
    def __init__(
        self,
        *,
        assignee: str = "jira-account-1",
        status: str = "打开",
        agentic_value: str | None = None,
        transitions: list[dict[str, str]] | None = None,
        field_id: str = "customfield_10042",
    ) -> None:
        self.assignee = assignee
        self.status = status
        self.agentic_value = agentic_value
        self.field_id = field_id
        self.transitions = transitions or [{"id": "11", "name": "Start Progress"}]
        self.requests: list[tuple[str, str]] = []
        self.transition_executed: str | None = None
        self.field_written: dict[str, str] | None = None
        self.search_issues: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> TransportResponse:
        self.requests.append((method, path))
        if path == "/rest/api/3/search/jql":
            return TransportResponse(
                200,
                {
                    "issues": self.search_issues,
                    "total": len(self.search_issues),
                    "startAt": 0,
                    "maxResults": 50,
                },
            )
        if path == "/rest/api/3/myself":
            return TransportResponse(
                200,
                {"accountId": "jira-account-1", "displayName": "Harsen Test Bot"},
            )
        if path == "/rest/api/3/field":
            return TransportResponse(
                200,
                [
                    {"id": "summary", "name": "Summary"},
                    {"id": self.field_id, "name": "Agentic ID"},
                ],
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
            if self.field_id:
                fields[self.field_id] = self.agentic_value or ""
            return TransportResponse(
                200,
                {
                    "id": "12289",
                    "key": "TAP-12289",
                    "fields": fields,
                },
            )
        if path == "/rest/api/3/issue/TAP-12289/transitions":
            if method == "GET":
                return TransportResponse(
                    200, {"transitions": self.transitions}
                )
            if method == "POST":
                self.transition_executed = str(body["transition"]["id"])
                self.status = "正在进行"
                return TransportResponse(204, None)
        if path == "/rest/api/3/issue/TAP-12289" and method == "PUT":
            self.field_written = {
                str(key): str(value) for key, value in body["fields"].items()
            }
            self.agentic_value = str(body["fields"][self.field_id])
            return TransportResponse(204, None)
        return TransportResponse(404, None)


class TaskTakeoverTest(unittest.TestCase):
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
        self,
        transport: TakeoverTransport,
        *extra: str,
        agent_id: str | None = "harsen-mini-test-bot",
        issue_key: str | None = "TAP-12289",
    ) -> tuple[int, dict[str, object], str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        arguments: list[str] = [
            "--workspace-root",
            str(self.workspace),
            "task",
            "takeover",
        ]
        if issue_key is not None:
            arguments.append(issue_key)
        if agent_id is not None:
            arguments.extend(["--agent-id", agent_id])
        arguments.extend(
            [
                "--authorization-reference",
                "user-confirmation:TAP-12289:takeover-test",
            ]
        )
        arguments.extend(extra)
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch("ao_work.work_cli.validate_install_root", return_value=self.install),
            mock.patch(
                "ao_work.task_takeover.UrllibJiraTransport",
                return_value=transport,
            ),
        ):
            code = main(tuple(arguments))
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_takeover_transitions_and_writes_agentic_id(self) -> None:
        transport = TakeoverTransport(status="打开")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("takeover_started", payload["current_stage"])
        self.assertEqual("TAP-12289", payload["issue_key"])
        self.assertEqual("11", transport.transition_executed)
        self.assertEqual(
            {"customfield_10042": "harsen-mini-test-bot"},
            transport.field_written,
        )
        self.assertEqual("正在进行", payload["jira_status_after"])
        self.assertTrue(payload["transition_applied"])
        self.assertTrue(payload["agentic_id_written"])

    def test_takeover_skips_transition_when_already_implementation(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertIsNone(transport.transition_executed)
        self.assertFalse(payload["transition_applied"])
        self.assertEqual("正在进行", payload["jira_status_after"])

    def test_takeover_blocks_owner_mismatch(self) -> None:
        transport = TakeoverTransport(assignee="someone-else")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("owner_mismatch", payload["code"])

    def test_takeover_blocks_agent_ownership_conflict(self) -> None:
        transport = TakeoverTransport(agentic_value="other-agent")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("agent_ownership_conflict", payload["code"])

    def _candidate_issue(
        self,
        key: str,
        *,
        summary: str,
        status: str = "打开",
        priority: str = "Medium",
        updated: str = "2026-08-19T01:00:00.000+0800",
    ) -> dict[str, object]:
        return {
            "id": "1000",
            "key": key,
            "fields": {
                "project": {"key": "TAP"},
                "summary": summary,
                "status": {"name": status},
                "issuetype": {"name": "任务"},
                "assignee": {"accountId": "jira-account-1"},
                "priority": {"name": priority},
                "updated": updated,
            },
        }

    def test_takeover_without_issue_key_lists_candidates_sorted_by_priority(self) -> None:
        transport = TakeoverTransport()
        transport.search_issues = [
            self._candidate_issue(
                "TAP-100",
                summary="低优先任务",
                priority="Low",
                updated="2026-08-19T03:00:00.000+0800",
            ),
            self._candidate_issue(
                "TAP-101",
                summary="最高优先任务",
                priority="Highest",
                updated="2026-08-19T01:00:00.000+0800",
            ),
            self._candidate_issue(
                "TAP-102",
                summary="中等优先更新新",
                priority="Medium",
                updated="2026-08-19T02:00:00.000+0800",
            ),
        ]
        code, payload, stderr = self.run_cli(transport, issue_key=None)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(True, payload["selection_required"])
        self.assertEqual(3, payload["candidate_count"])
        candidates: list[dict[str, object]] = payload["candidates"]  # type: ignore[assignment]
        keys = [str(task["issue_key"]) for task in candidates]
        # 优先级 Highest > Medium > Low，同级按 updated 倒序。
        self.assertEqual(["TAP-101", "TAP-102", "TAP-100"], keys)
        # 无 key 路径必须只读：不执行 transition、不写字段。
        self.assertIsNone(transport.transition_executed)
        self.assertIsNone(transport.field_written)
        self.assertNotIn(
            ("POST", "/rest/api/3/issue/TAP-12289/transitions"),
            transport.requests,
        )

    def test_takeover_without_issue_key_empty_candidates(self) -> None:
        transport = TakeoverTransport()
        code, payload, stderr = self.run_cli(transport, issue_key=None)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(True, payload["selection_required"])
        self.assertEqual(0, payload["candidate_count"])
        self.assertEqual([], payload["candidates"])

    def test_takeover_reads_agent_id_from_install_identity(self) -> None:
        identity = self.install / "user" / "identity.yaml"
        identity.parent.mkdir(parents=True)
        identity.write_text(
            "agent_id: harsen-mini-test-bot\n"
            "jira_email: harsen@example.test\n"
            "execution_identity:\n"
            "  git_author_name: Harsen Test Bot\n"
            "  git_author_email: harsen@example.test\n"
            "  git_committer_name: Harsen Test Bot\n"
            "  git_committer_email: harsen@example.test\n"
            "  github_actor_login: harsen-mini-test-bot\n",
            encoding="utf-8",
        )
        transport = TakeoverTransport(status="打开")
        code, payload, stderr = self.run_cli(transport, agent_id=None)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("harsen-mini-test-bot", payload["agent_id"])
        self.assertEqual(
            {"customfield_10042": "harsen-mini-test-bot"},
            transport.field_written,
        )

    def test_takeover_blocks_when_agent_id_missing(self) -> None:
        transport = TakeoverTransport(status="打开")
        code, payload, stderr = self.run_cli(transport, agent_id=None)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("agent_identity_missing", payload["code"])

    def test_takeover_blocks_when_authorization_reference_missing(self) -> None:
        transport = TakeoverTransport(status="打开")
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch("ao_work.work_cli.validate_install_root", return_value=self.install),
            mock.patch(
                "ao_work.task_takeover.UrllibJiraTransport",
                return_value=transport,
            ),
        ):
            code = main(
                (
                    "--workspace-root",
                    str(self.workspace),
                    "task",
                    "takeover",
                    "TAP-12289",
                    "--agent-id",
                    "harsen-mini-test-bot",
                )
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(2, code)
        self.assertEqual("authorization_reference_required", payload["code"])

    def test_takeover_blocks_missing_transition(self) -> None:
        transport = TakeoverTransport(
            status="打开",
            transitions=[{"id": "99", "name": "Other"}],
        )
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("jira_transition_mapping_gap", payload["code"])

    def test_takeover_works_without_agentic_field(self) -> None:
        transport = TakeoverTransport(field_id="")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertFalse(payload["agentic_id_written"])

    def test_takeover_reuses_existing_task_state(self) -> None:
        """任务已初始化（task.json 存在）后再接管：复用 agentic_run_id，不 KeyError。"""
        from ao_work.task_state import TaskIdentity, TaskStore

        store = TaskStore(Path(self.workspace))
        store.initialize(
            TaskIdentity(
                connection_id="tapdata-cloud",
                jira_issue_id="12289",
                issue_key="TAP-12289",
                project_key="TAP",
                agentic_run_id="run-TAP-12289-previous",
            )
        )
        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("takeover_started", payload["current_stage"])
        self.assertEqual("run-TAP-12289-previous", payload["agentic_run_id"])
        self.assertFalse(payload["task_state_created"])


class AgenticFieldProbeTest(unittest.TestCase):
    def test_finds_agentic_id_field_by_common_names(self) -> None:
        self.assertEqual(
            "customfield_10042",
            _find_agentic_id_field(
                [
                    {"id": "summary", "name": "Summary"},
                    {"id": "customfield_10042", "name": "Agentic ID"},
                ]
            ),
        )
        self.assertEqual(
            "customfield_10043",
            _find_agentic_id_field(
                [
                    {"id": "customfield_10043", "name": "AI Agent ID"},
                ]
            ),
        )

    def test_returns_none_when_no_agentic_field(self) -> None:
        self.assertIsNone(
            _find_agentic_id_field(
                [{"id": "summary", "name": "Summary"}]
            )
        )


if __name__ == "__main__":
    unittest.main()
