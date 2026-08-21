from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_work.jira.adf import markdown_to_adf, plain_text
from ao_work.jira.client import JiraTransportError, TransportResponse
from ao_work.output import RuntimeErrorResult
from ao_work.task_state import TaskStore
from ao_work.work_cli import main
from install_auth_fixture import configure_install_authorization, v5_agent


class TakeoverTransport:
    def __init__(
        self,
        *,
        assignee: str = "jira-account-1",
        status: str = "打开",
        transitions: list[dict[str, str]] | None = None,
        comment_post_mode: str = "normal",
        transition_post_mode: str = "normal",
        status_on_issue_read: dict[int, str] | None = None,
        fail_comment_reads: set[int] | None = None,
        fail_issue_reads: set[int] | None = None,
    ) -> None:
        self.assignee = assignee
        self.status = status
        self.transitions = (
            transitions
            if transitions is not None
            else [{"id": "11", "name": "Start Progress"}]
        )
        self.requests: list[tuple[str, str]] = []
        self.transition_executed: str | None = None
        self.search_issues: list[dict[str, object]] = []
        self.comments: list[dict[str, object]] = []
        self.comment_post_mode = comment_post_mode
        self.transition_post_mode = transition_post_mode
        self.status_on_issue_read = status_on_issue_read or {}
        self.issue_read_count = 0
        self.comment_read_count = 0
        self.fail_comment_reads = fail_comment_reads or set()
        self.fail_issue_reads = fail_issue_reads or set()

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
        if path == "/rest/api/3/issue/TAP-12289/comment" and method == "GET":
            self.comment_read_count += 1
            if self.comment_read_count in self.fail_comment_reads:
                raise JiraTransportError("simulated comment read failure")
            return TransportResponse(
                200,
                {
                    "comments": self.comments,
                    "total": len(self.comments),
                    "startAt": 0,
                    "maxResults": 50,
                },
            )
        if path == "/rest/api/3/issue/TAP-12289/comment" and method == "POST":
            if self.comment_post_mode == "raise_without_write":
                raise JiraTransportError("simulated comment response loss")
            comment = {
                "id": str(9000 + len(self.comments) + 1),
                "body": body["body"],
                "author": {"accountId": "jira-account-1"},
                "created": "2026-08-20T08:00:00.000+0800",
            }
            self.comments.append(comment)
            if self.comment_post_mode == "write_then_raise":
                raise JiraTransportError("simulated comment response loss")
            return TransportResponse(201, comment)
        if path.startswith("/rest/api/3/issue/TAP-12289/comment/") and method == "GET":
            comment_id = path.rsplit("/", 1)[-1]
            for comment in self.comments:
                if comment["id"] == comment_id:
                    return TransportResponse(200, comment)
            return TransportResponse(404, None)
        if path == "/rest/api/3/issue/TAP-12289" and method == "GET":
            self.issue_read_count += 1
            if self.issue_read_count in self.fail_issue_reads:
                raise JiraTransportError("simulated issue read failure")
            self.status = self.status_on_issue_read.get(
                self.issue_read_count, self.status
            )
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
        if path == "/rest/api/3/issue/TAP-12289/transitions":
            if method == "GET":
                return TransportResponse(
                    200, {"transitions": self.transitions}
                )
            if method == "POST":
                self.transition_executed = str(body["transition"]["id"])
                if self.transition_post_mode == "raise_without_write":
                    raise JiraTransportError("simulated transition response loss")
                if self.transition_post_mode == "third_then_raise":
                    self.status = "代码评审"
                    raise JiraTransportError("simulated transition response loss")
                if self.transition_post_mode == "known_failure":
                    return TransportResponse(409, {"errorMessages": ["blocked"]})
                self.status = "正在进行"
                if self.transition_post_mode == "write_then_raise":
                    raise JiraTransportError("simulated transition response loss")
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
        install_identity_ref = configure_install_authorization(self.install)
        state = self.workspace / ".agentic-ops"
        (state / "profiles").mkdir(parents=True)
        (state / "agent.json").write_text(
            json.dumps(
                v5_agent(
                    self.install,
                    project_profile="tapdata",
                    jira_project="TAP",
                    connection_id="tapdata-cloud",
                    jira_base_url="https://tapdata.atlassian.net",
                    jira_site="tapdata.atlassian.net",
                    source_root=str(self.source.resolve()),
                    repository="tapdata/tapdata",
                )
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
        with_identity: bool = True,
        issue_key: str | None = "TAP-12289",
    ) -> tuple[int, dict[str, object], str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        identity = self.install / "user" / "identity.yaml"
        if with_identity:
            identity.parent.mkdir(parents=True, exist_ok=True)
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
        elif identity.exists():
            identity.unlink()
        arguments: list[str] = [
            "--workspace-root",
            str(self.workspace),
            "takeover",
        ]
        if issue_key is not None:
            arguments.append(issue_key)
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

    def test_top_level_takeover_generates_stable_internal_authorization(self) -> None:
        transport = TakeoverTransport(status="打开")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("takeover", payload["operation"])
        self.assertEqual("new_takeover", payload["takeover_kind"])
        self.assertNotIn("authorization_reference", payload)
        first_run = payload["agentic_run_id"]
        first_comment = payload["takeover_comment_id"]

        code, repeated, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (repeated, stderr))
        self.assertEqual(first_run, repeated["agentic_run_id"])
        self.assertEqual(first_comment, repeated["takeover_comment_id"])
        self.assertEqual(1, len(transport.comments))
        self.assertEqual(
            1,
            transport.requests.count(
                ("POST", "/rest/api/3/issue/TAP-12289/transitions")
            ),
        )

    def test_top_level_takeover_without_key_is_read_only(self) -> None:
        transport = TakeoverTransport()
        transport.search_issues = [
            self._candidate_issue("TAP-101", summary="候选任务", priority="Highest")
        ]
        code, payload, stderr = self.run_cli(
            transport,
            issue_key=None,
        )
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("selection_required", payload["takeover_status"])
        self.assertEqual(1, payload["candidate_count"])
        self.assertIn("未执行接管", payload["human_notice"])
        self.assertEqual([], transport.comments)
        self.assertFalse((self.workspace / ".agentic-ops" / "tasks").exists())

    def test_removed_takeover_alias_and_hidden_inputs_are_rejected(self) -> None:
        for arguments in (
            ("task", "takeover", "TAP-12289"),
            ("takeover", "TAP-12289", "--agent-id", "legacy-agent"),
            (
                "takeover",
                "TAP-12289",
                "--authorization-reference",
                "legacy-reference",
            ),
        ):
            with self.subTest(arguments=arguments):
                stdout, stderr = io.StringIO(), io.StringIO()
                with (
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                    mock.patch(
                        "ao_work.work_cli.validate_install_root",
                        return_value=self.install,
                    ),
                ):
                    code = main(
                        ("--workspace-root", str(self.workspace), *arguments)
                    )
                self.assertEqual(2, code)
                self.assertEqual(
                    "invalid_arguments", json.loads(stdout.getvalue())["code"]
                )

    def test_developer_takeover_rejects_ao_issue_before_jira_access(self) -> None:
        transport = TakeoverTransport()
        code, payload, stderr = self.run_cli(transport, issue_key="AO-51")
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("jira_workspace_mismatch", payload["code"])
        self.assertEqual([], transport.requests)

    def test_takeover_comments_then_transitions_without_custom_fields(self) -> None:
        transport = TakeoverTransport(status="打开")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("takeover_started", payload["current_stage"])
        self.assertEqual("TAP-12289", payload["issue_key"])
        self.assertEqual("11", transport.transition_executed)
        self.assertEqual("正在进行", payload["jira_status_after"])
        self.assertTrue(payload["transition_applied"])
        self.assertEqual("new_takeover", payload["takeover_kind"])
        self.assertEqual("completed", payload["takeover_status"])
        self.assertEqual("local_finalized", payload["takeover_phase"])
        self.assertEqual("completed", payload["takeover_result"])
        self.assertEqual("verified", payload["external_result_certainty"])
        self.assertTrue(payload["retry_safe"])
        self.assertEqual("none", payload["recovery_action"])
        self.assertEqual("已完成新接管。", payload["human_notice"])
        inspected = TaskStore(Path(self.workspace)).inspect("TAP-12289")
        self.assertEqual(
            payload["human_notice"],
            inspected["takeover_recovery"]["operation"]["human_notice"],
        )
        self.assertTrue(payload["takeover_comment_verified"])
        self.assertEqual("jira-account-1", payload["takeover_comment_author"])
        self.assertTrue(payload["takeover_comment_author_verified"])
        self.assertEqual("正在进行", payload["jira_status_target"])
        self.assertTrue(payload["state_consistent"])
        self.assertTrue(payload["agentic_takeover_at"])
        self.assertRegex(
            payload["intake_source"]["context_digest"], r"^[0-9a-f]{64}$"
        )
        self.assertTrue(
            Path(payload["intake_source"]["source_context_path"]).is_file()
        )
        self.assertEqual(
            "assess_task_intake", payload["agentic_next_action"]["action"]
        )
        self.assertFalse(
            payload["agentic_next_action"]["requires_authorization"]
        )
        self.assertEqual(1, len(transport.comments))
        comment = plain_text(transport.comments[0]["body"])
        self.assertIn("操作类型: 新接管", comment)
        self.assertIn("[agentic-ops-takeover:TAP-12289:", comment)
        self.assertNotIn(("GET", "/rest/api/3/field"), transport.requests)
        self.assertNotIn(("PUT", "/rest/api/3/issue/TAP-12289"), transport.requests)
        comment_write = transport.requests.index(
            ("POST", "/rest/api/3/issue/TAP-12289/comment")
        )
        transition_write = transport.requests.index(
            ("POST", "/rest/api/3/issue/TAP-12289/transitions")
        )
        self.assertLess(comment_write, transition_write)
        first_run = payload["agentic_run_id"]
        transition_writes = transport.requests.count(
            ("POST", "/rest/api/3/issue/TAP-12289/transitions")
        )
        code, repeated, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (repeated, stderr))
        self.assertEqual(first_run, repeated["agentic_run_id"])
        self.assertEqual("new_takeover", repeated["takeover_kind"])
        self.assertEqual(1, len(transport.comments))
        self.assertEqual(
            transition_writes,
            transport.requests.count(
                ("POST", "/rest/api/3/issue/TAP-12289/transitions")
            ),
        )

    def test_takeover_checks_transition_before_writing_comment(self) -> None:
        transport = TakeoverTransport(status="打开", transitions=[])
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("jira_transition_mapping_gap", payload["code"])
        self.assertEqual([], transport.comments)

    def test_takeover_skips_transition_when_already_implementation(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertIsNone(transport.transition_executed)
        self.assertFalse(payload["transition_applied"])
        self.assertEqual("正在进行", payload["jira_status_after"])
        self.assertEqual("accept_existing_task", payload["takeover_kind"])
        self.assertIn("不是新接管", payload["human_notice"])
        self.assertIn(
            "接纳存量任务（不是新接管）",
            plain_text(transport.comments[0]["body"]),
        )

    def test_takeover_blocks_owner_mismatch(self) -> None:
        transport = TakeoverTransport(assignee="someone-else")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("owner_mismatch", payload["code"])
        self.assertEqual([], transport.comments)
        self.assertIsNone(transport.transition_executed)
        self.assertFalse((self.workspace / ".agentic-ops" / "tasks").exists())

    def test_takeover_blocks_unassigned_issue_before_side_effects(self) -> None:
        transport = TakeoverTransport(assignee="")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("assignee_unassigned", payload["code"])
        self.assertEqual("Jira /myself", payload["identity_source"])
        self.assertEqual([], transport.comments)
        self.assertIsNone(transport.transition_executed)
        self.assertFalse((self.workspace / ".agentic-ops" / "tasks").exists())

    def test_accept_existing_task_blocks_owner_mismatch_before_side_effects(self) -> None:
        transport = TakeoverTransport(assignee="someone-else", status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("owner_mismatch", payload["code"])
        self.assertEqual([], transport.comments)
        self.assertIsNone(transport.transition_executed)
        self.assertFalse((self.workspace / ".agentic-ops" / "tasks").exists())

    def test_resume_takeover_blocks_owner_change_without_new_side_effects(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        first_code, first_payload, first_stderr = self.run_cli(transport)
        self.assertEqual(0, first_code, (first_payload, first_stderr))
        comment_count = len(transport.comments)
        transition_count = transport.requests.count(
            ("POST", "/rest/api/3/issue/TAP-12289/transitions")
        )
        task_state = self.workspace / ".agentic-ops" / "tasks" / "TAP-12289"
        before = {
            path.relative_to(task_state).as_posix(): path.read_text(encoding="utf-8")
            for path in task_state.rglob("*")
            if path.is_file()
        }

        transport.assignee = "someone-else"
        code, payload, stderr = self.run_cli(transport)

        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("owner_mismatch", payload["code"])
        self.assertEqual(comment_count, len(transport.comments))
        self.assertEqual(
            transition_count,
            transport.requests.count(("POST", "/rest/api/3/issue/TAP-12289/transitions")),
        )
        after = {
            path.relative_to(task_state).as_posix(): path.read_text(encoding="utf-8")
            for path in task_state.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)

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
        self.assertEqual("selection_required", payload["takeover_status"])
        self.assertEqual(
            "select_takeover_candidate",
            payload["agentic_next_action"]["action"],
        )
        self.assertTrue(payload["agentic_next_action"]["stop_workflow"])
        self.assertEqual(3, payload["candidate_count"])
        candidates: list[dict[str, object]] = payload["candidates"]  # type: ignore[assignment]
        keys = [str(task["issue_key"]) for task in candidates]
        # 优先级 Highest > Medium > Low，同级按 updated 倒序。
        self.assertEqual(["TAP-101", "TAP-102", "TAP-100"], keys)
        # 无 key 路径必须只读：不执行 transition、不写评论。
        self.assertIsNone(transport.transition_executed)
        self.assertEqual([], transport.comments)
        self.assertNotIn(
            ("POST", "/rest/api/3/issue/TAP-12289/transitions"),
            transport.requests,
        )

    def test_takeover_without_issue_key_empty_candidates(self) -> None:
        transport = TakeoverTransport()
        code, payload, stderr = self.run_cli(transport, issue_key=None)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(True, payload["selection_required"])
        self.assertEqual("selection_required", payload["takeover_status"])
        self.assertEqual(0, payload["candidate_count"])
        self.assertEqual([], payload["candidates"])

    def test_takeover_reads_agent_id_from_install_identity(self) -> None:
        transport = TakeoverTransport(status="打开")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("harsen-mini-test-bot", payload["agent_id"])

    def test_schema_v4_takeover_revalidates_matching_install_identity(self) -> None:
        identity = {
            "agent_id": "harsen-mini-test-bot",
            "jira_email": "harsen@example.test",
            "execution_identity": {
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen@example.test",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen@example.test",
                "github_actor_login": "harsen-mini-test-bot",
            },
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        agent_path = self.workspace / ".agentic-ops" / "agent.json"
        agent = json.loads(agent_path.read_text(encoding="utf-8"))
        for field in ("agent_id", "jira_account_id", "execution_identity"):
            agent.pop(field, None)
        agent.update(
            {
                "schema_version": 5,
                "install_identity_ref": f"install:{fingerprint}",
            }
        )
        agent_path.write_text(json.dumps(agent), encoding="utf-8")
        (self.install / "user").mkdir(parents=True, exist_ok=True)
        (self.install / "user" / ".env").write_text(
            "TAPDATA_JIRA_EMAIL=harsen@example.test\n"
            "TAPDATA_JIRA_API_TOKEN=test-token-secret\n",
            encoding="utf-8",
        )

        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)

        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("accept_existing_task", payload["takeover_kind"])
        self.assertEqual("harsen-mini-test-bot", payload["agent_id"])
        self.assertEqual(1, len(transport.comments))
        self.assertIsNone(transport.transition_executed)

    def test_takeover_blocks_when_install_identity_missing(self) -> None:
        transport = TakeoverTransport(status="打开")
        code, payload, stderr = self.run_cli(transport, with_identity=False)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("install_identity_missing", payload["code"])

    def test_takeover_blocks_missing_transition(self) -> None:
        transport = TakeoverTransport(
            status="打开",
            transitions=[{"id": "99", "name": "Other"}],
        )
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("jira_transition_mapping_gap", payload["code"])

    def test_takeover_does_not_read_agentic_field_metadata(self) -> None:
        transport = TakeoverTransport()
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertNotIn(("GET", "/rest/api/3/field"), transport.requests)

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

    def test_takeover_resume_is_explicit_and_idempotent(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("accept_existing_task", payload["takeover_kind"])
        self.assertIn("不是新接管", payload["human_notice"])
        self.assertIn(
            "接纳存量任务（不是新接管）",
            plain_text(transport.comments[0]["body"]),
        )
        first_run = payload["agentic_run_id"]
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("accept_existing_task", payload["takeover_kind"])
        self.assertEqual(first_run, payload["agentic_run_id"])
        self.assertEqual(1, len(transport.comments))

    def test_takeover_does_not_reuse_foreign_marker_comment(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        transport.comments[0]["author"] = {"accountId": "someone-else"}
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_comment_evidence_conflict", payload["code"])
        self.assertEqual(1, len(transport.comments))

    def test_takeover_blocks_foreign_run_comment_before_writing(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        transport.comments.append(
            {
                "id": "8801",
                "body": markdown_to_adf(
                    "外来运行\n\n"
                    "[agentic-ops-takeover:TAP-12289:run-foreign:"
                    "accept_existing_task:foreign]"
                ),
                "author": {"accountId": "jira-account-1"},
                "created": "2026-08-20T08:00:00.000+0800",
            }
        )
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("external_task_state_conflict", payload["code"])
        self.assertEqual(1, len(transport.comments))
        self.assertIsNone(transport.transition_executed)

    def test_takeover_blocks_foreign_agent_against_persisted_intent(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        with mock.patch(
            "ao_work.task_takeover._default_agent_id",
            return_value="foreign-agent",
        ):
            code, conflict, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (conflict, stderr))
        self.assertEqual("takeover_intent_conflict", conflict["code"])
        self.assertEqual(1, len(transport.comments))

    def test_comment_response_loss_recovers_from_verified_readback(self) -> None:
        transport = TakeoverTransport(comment_post_mode="write_then_raise")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("local_finalized", payload["takeover_phase"])
        self.assertEqual(1, len(transport.comments))
        self.assertEqual("11", transport.transition_executed)

    def test_comment_response_loss_with_verified_absence_is_retryable(self) -> None:
        transport = TakeoverTransport(comment_post_mode="raise_without_write")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_comment_retryable_absent", payload["code"])
        self.assertTrue(payload["retry_safe"])
        self.assertIsNone(transport.transition_executed)
        operation = TaskStore(Path(self.workspace)).inspect("TAP-12289")[
            "takeover_recovery"
        ]["operation"]
        self.assertEqual("intent_persisted", operation["phase"])
        self.assertEqual("in_progress", operation["result"])

        transport.comment_post_mode = "normal"
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(1, len(transport.comments))

    def test_comment_write_without_reliable_readback_becomes_uncertain(self) -> None:
        transport = TakeoverTransport(fail_comment_reads={3})
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_comment_result_uncertain", payload["code"])
        self.assertFalse(payload["retry_safe"])
        self.assertEqual(1, len(transport.comments))
        self.assertIsNone(transport.transition_executed)
        operation = TaskStore(Path(self.workspace)).inspect("TAP-12289")[
            "takeover_recovery"
        ]["operation"]
        self.assertEqual("intent_persisted", operation["phase"])
        self.assertEqual("uncertain", operation["result"])

        transport.fail_comment_reads.clear()
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(1, len(transport.comments))

    def test_transition_response_loss_recovers_from_target_status(self) -> None:
        transport = TakeoverTransport(transition_post_mode="write_then_raise")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("正在进行", payload["jira_status_after"])
        self.assertTrue(payload["transition_applied"])
        self.assertEqual(1, len(transport.comments))

    def test_transition_response_loss_at_original_status_is_recoverable(self) -> None:
        transport = TakeoverTransport(transition_post_mode="raise_without_write")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_transition_retryable_original", payload["code"])
        self.assertTrue(payload["retry_safe"])
        self.assertEqual(1, len(transport.comments))
        operation = TaskStore(Path(self.workspace)).inspect("TAP-12289")[
            "takeover_recovery"
        ]["operation"]
        self.assertEqual("comment_verified", operation["phase"])

        transport.transition_post_mode = "normal"
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(1, len(transport.comments))

    def test_confirmed_comment_and_known_transition_failure_is_recoverable(self) -> None:
        transport = TakeoverTransport(transition_post_mode="known_failure")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_transition_retryable_original", payload["code"])
        self.assertTrue(payload["retry_safe"])
        self.assertEqual(1, len(transport.comments))
        operation = TaskStore(Path(self.workspace)).inspect("TAP-12289")[
            "takeover_recovery"
        ]["operation"]
        self.assertEqual("comment_verified", operation["phase"])

        transport.transition_post_mode = "normal"
        code, recovered, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (recovered, stderr))
        self.assertEqual(1, len(transport.comments))

    def test_transition_readback_failure_becomes_uncertain(self) -> None:
        transport = TakeoverTransport(fail_issue_reads={4})
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_transition_result_uncertain", payload["code"])
        self.assertFalse(payload["retry_safe"])
        self.assertEqual(1, len(transport.comments))
        operation = TaskStore(Path(self.workspace)).inspect("TAP-12289")[
            "takeover_recovery"
        ]["operation"]
        self.assertEqual("comment_verified", operation["phase"])
        self.assertEqual("uncertain", operation["result"])

        transport.fail_issue_reads.clear()
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(1, len(transport.comments))

    def test_transition_third_party_status_stops_for_risk_review(self) -> None:
        transport = TakeoverTransport(transition_post_mode="third_then_raise")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_status_external_conflict", payload["code"])
        self.assertFalse(payload["retry_safe"])
        self.assertEqual(1, len(transport.comments))
        transition_writes = transport.requests.count(
            ("POST", "/rest/api/3/issue/TAP-12289/transitions")
        )

        transport.status = "正在进行"
        transport.transition_post_mode = "normal"
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_status_external_conflict", payload["code"])
        self.assertEqual(
            transition_writes,
            transport.requests.count(
                ("POST", "/rest/api/3/issue/TAP-12289/transitions")
            ),
        )

    def test_pre_comment_fact_drift_blocks_without_comment_or_transition(self) -> None:
        transport = TakeoverTransport(status_on_issue_read={2: "正在进行"})
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_preflight_facts_changed", payload["code"])
        self.assertEqual([], transport.comments)
        self.assertIsNone(transport.transition_executed)

    def test_jira_success_local_failure_recovers_without_external_rewrite(self) -> None:
        transport = TakeoverTransport()
        with mock.patch(
            "ao_work.task_takeover.record_current_task_source_context",
            side_effect=OSError("simulated local source failure"),
        ):
            code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_local_finalize_failed", payload["code"])
        self.assertTrue(payload["retry_safe"])
        self.assertEqual(1, len(transport.comments))
        transition_writes = transport.requests.count(
            ("POST", "/rest/api/3/issue/TAP-12289/transitions")
        )
        self.assertEqual(1, transition_writes)

        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(1, len(transport.comments))
        self.assertEqual(
            transition_writes,
            transport.requests.count(
                ("POST", "/rest/api/3/issue/TAP-12289/transitions")
            ),
        )

    def test_structured_local_finalize_failure_uses_stable_saga_code(self) -> None:
        transport = TakeoverTransport()
        local_error = RuntimeErrorResult(
            code="task_source_context_invalid",
            message="simulated structured local failure",
            status="blocked",
            exit_code=2,
            retry_safe=False,
            required_human_action="检查本地状态",
        )
        with mock.patch(
            "ao_work.task_takeover.record_current_task_source_context",
            side_effect=local_error,
        ):
            code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_local_finalize_failed", payload["code"])
        self.assertFalse(payload["retry_safe"])
        self.assertEqual(1, len(transport.comments))
        self.assertEqual(
            1,
            transport.requests.count(
                ("POST", "/rest/api/3/issue/TAP-12289/transitions")
            ),
        )

    def test_duplicate_exact_marker_is_blocked_on_completed_replay(self) -> None:
        transport = TakeoverTransport(status="正在进行")
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        duplicate = dict(transport.comments[0])
        duplicate["id"] = "9999"
        transport.comments.append(duplicate)

        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_comment_duplicate", payload["code"])
        self.assertEqual(2, len(transport.comments))

    def test_verified_legacy_takeover_migrates_without_jira_rewrite(self) -> None:
        from ao_work.task_state import TaskIdentity

        store = TaskStore(Path(self.workspace))
        run_id = "run-TAP-12289-legacy"
        store.initialize(
            TaskIdentity(
                connection_id="tapdata-cloud",
                jira_issue_id="12289",
                issue_key="TAP-12289",
                project_key="TAP",
                agentic_run_id=run_id,
            )
        )
        marker = f"[agentic-ops-takeover:TAP-12289:{run_id}:legacy]"
        store.record_gate_transition(
            "TAP-12289",
            run_id,
            stage="takeover_started",
            next_action="assess_task_intake",
            operation="takeover_task",
            status="completed",
            evidence={
                "agent_id": "harsen-mini-test-bot",
                "takeover_kind": "accept_existing_task",
                "takeover_comment_id": "9001",
                "takeover_comment_marker": marker,
                "agentic_takeover_at": "2026-08-20T08:00:00Z",
                "jira_status_before": "正在进行",
                "jira_status_after": "正在进行",
                "authorization_reference": (
                    "user-confirmation:TAP-12289:takeover-test"
                ),
            },
        )
        transport = TakeoverTransport(status="正在进行")
        transport.comments.append(
            {
                "id": "9001",
                "body": markdown_to_adf(f"旧接管记录\n\n{marker}"),
                "author": {"accountId": "jira-account-1"},
                "created": "2026-08-20T08:00:00.000+0800",
            }
        )
        code, payload, stderr = self.run_cli(transport)
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual(run_id, payload["agentic_run_id"])
        self.assertEqual("accept_existing_task", payload["takeover_kind"])
        self.assertEqual("local_finalized", payload["takeover_phase"])
        self.assertEqual(1, len(transport.comments))
        self.assertIsNone(transport.transition_executed)

    def test_unverified_legacy_takeover_fails_closed_without_state_rewrite(self) -> None:
        from ao_work.task_state import TaskIdentity

        store = TaskStore(Path(self.workspace))
        run_id = "run-TAP-12289-legacy-unverified"
        store.initialize(
            TaskIdentity(
                connection_id="tapdata-cloud",
                jira_issue_id="12289",
                issue_key="TAP-12289",
                project_key="TAP",
                agentic_run_id=run_id,
            )
        )
        marker = f"[agentic-ops-takeover:TAP-12289:{run_id}:legacy]"
        store.record_gate_transition(
            "TAP-12289",
            run_id,
            stage="takeover_started",
            next_action="assess_task_intake",
            operation="takeover_task",
            status="completed",
            evidence={
                "agent_id": "harsen-mini-test-bot",
                "takeover_kind": "accept_existing_task",
                "takeover_comment_id": "9001",
                "takeover_comment_marker": marker,
                "agentic_takeover_at": "2026-08-20T08:00:00Z",
                "jira_status_before": "正在进行",
                "jira_status_after": "正在进行",
                "authorization_reference": "legacy-authorization",
            },
        )
        task_root = self.workspace / ".agentic-ops" / "tasks" / "TAP-12289"
        before = {
            path.relative_to(task_root): path.read_bytes()
            for path in task_root.rglob("*")
            if path.is_file()
        }
        transport = TakeoverTransport(status="正在进行")
        transport.comments.append(
            {
                "id": "9001",
                "body": markdown_to_adf(f"旧接管记录\n\n{marker}"),
                "author": {"accountId": "foreign-account"},
                "created": "2026-08-20T08:00:00.000+0800",
            }
        )

        code, payload, stderr = self.run_cli(transport)

        self.assertEqual(2, code, (payload, stderr))
        self.assertEqual("takeover_legacy_state_unverified", payload["code"])
        after = {
            path.relative_to(task_root): path.read_bytes()
            for path in task_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(1, len(transport.comments))
        self.assertIsNone(transport.transition_executed)


if __name__ == "__main__":
    unittest.main()
