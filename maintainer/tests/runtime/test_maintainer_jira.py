from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_maint.cli import build_parser as build_maintainer_parser
from ao_maint.cli import main as maintainer_main
from ao_maint.jira.client import JiraClient, JiraConnection, TransportResponse
from ao_maint.jira.config import (
    env_file_path,
    load_maintainer_connection,
    load_maintainer_jira_config,
    plans_dir,
    set_credentials,
)
from ao_maint.jira.service import MaintainerJiraService
from ao_maint.output import RuntimeErrorResult


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.comments: list[dict[str, object]] = []
        self.worklogs: list[dict[str, object]] = []
        self.issue: dict[str, object] | None = None

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
            return TransportResponse(200, {"accountId": "user-1", "displayName": "维护者"})
        if path == "/rest/api/3/field":
            return TransportResponse(200, [{"id": "summary", "name": "Summary"}])
        if path.startswith("/rest/api/3/issue/") and path.endswith("/comment"):
            if method == "GET":
                return TransportResponse(200, {"comments": self.comments, "total": len(self.comments)})
            comment_id = f"c{len(self.comments) + 1}"
            self.comments.append({"id": comment_id, "body": body.get("body") if isinstance(body, dict) else None})
            return TransportResponse(201, {"id": comment_id})
        if path.startswith("/rest/api/3/issue/") and path.endswith("/worklog"):
            if method == "GET":
                return TransportResponse(200, {"worklogs": self.worklogs, "total": len(self.worklogs)})
            worklog_id = f"w{len(self.worklogs) + 1}"
            self.worklogs.append(
                {
                    "id": worklog_id,
                    "comment": body.get("comment") if isinstance(body, dict) else None,
                    "timeSpentSeconds": body.get("timeSpentSeconds") if isinstance(body, dict) else 0,
                    "started": body.get("started") if isinstance(body, dict) else "",
                }
            )
            return TransportResponse(201, {"id": worklog_id})
        if path.startswith("/rest/api/3/issue/"):
            return TransportResponse(200, self.issue or {})
        raise AssertionError(f"unexpected request: {method} {path}")


class MaintainerJiraConfigTest(unittest.TestCase):
    def test_ao_jira_interactive_eof_is_graceful(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_source(root)
            import io
            import subprocess
            from contextlib import redirect_stderr, redirect_stdout

            subprocess.run(["git", "-C", str(root), "init", "-b", "main"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(root), "remote", "add", "origin", "git@github.com:tapstate/agentic-ops.git"],
                check=True,
                capture_output=True,
            )

            with (
                mock.patch("sys.stdin", io.StringIO("")),
                mock.patch("sys.stdin.isatty", return_value=True),
                mock.patch("getpass.getpass", side_effect=EOFError),
            ):
                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = maintainer_main(
                        [
                            "--source-root",
                            str(root),
                            "jira",
                            "auth",
                            "set",
                            "--interactive",
                        ]
                    )
            self.assertEqual(1, exit_code)
            payload = json.loads(stdout.getvalue())
            self.assertEqual("operation_interrupted", payload["code"])

    def _prepare_source(self, root: Path) -> None:
        (root / ".agentic-ops-source").write_text("maintainer\n", encoding="utf-8")
        maintainer_dir = root / "maintainer"
        maintainer_dir.mkdir(parents=True, exist_ok=True)
        (maintainer_dir / "AGENTS.md").write_text("# maintainer\n", encoding="utf-8")
        connections = root / "maintainer" / "standards" / "connections"
        connections.mkdir(parents=True)
        (connections / "tapdata-cloud.yaml").write_text(
            "schema_version: 1\n"
            "connection_id: tapdata-cloud\n"
            "base_url: https://tapdata.atlassian.net\n"
            "timeout_seconds: 20\n"
            "auth:\n"
            "  type: basic_api_token\n"
            "  email_env: TAPDATA_JIRA_EMAIL\n"
            "  token_env: TAPDATA_JIRA_API_TOKEN\n",
            encoding="utf-8",
        )

    def test_load_connection_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_source(root)
            connection = load_maintainer_connection(root, "tapdata-cloud")
            self.assertEqual("https://tapdata.atlassian.net", connection.base_url)
            config = load_maintainer_jira_config(root)
            self.assertEqual("missing", config.credential_source)
            self.assertEqual(False, config.credential_status()["email_configured"])
            with self.assertRaises(RuntimeErrorResult) as captured:
                config.require_credentials()
            self.assertEqual("jira_credentials_missing", captured.exception.code)

    def test_set_credentials_writes_protected_env(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_source(root)
            connection = load_maintainer_connection(root, "tapdata-cloud")
            result = set_credentials(
                root, connection, email="maintainer@example.com", token="token-123456"
            )
            self.assertTrue(result["ready"])
            env_path = env_file_path(root)
            self.assertTrue(env_path.exists())
            self.assertEqual(0o600, env_path.stat().st_mode & 0o777)
            config = load_maintainer_jira_config(root)
            self.assertEqual("maintainer@example.com", config.email)
            self.assertEqual("token-123456", config.token)
            self.assertEqual("maintainer_local", config.credential_source)

    def test_plans_dir_is_under_maintainer_local(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_source(root)
            self.assertEqual(root / "maintainer" / ".local" / "jira-plans", plans_dir(root))


class MaintainerJiraServiceTest(unittest.TestCase):
    def _service(self) -> tuple[MaintainerJiraService, FakeTransport, JiraClient]:
        connection = JiraConnection(
            connection_id="tapdata-cloud",
            base_url="https://tapdata.atlassian.net",
            email_env="TAPDATA_JIRA_EMAIL",
            token_env="TAPDATA_JIRA_API_TOKEN",
        )
        transport = FakeTransport()
        client = JiraClient(connection, transport)
        transport.issue = {
            "id": "1001",
            "key": "AO-11",
            "fields": {
                "project": {"key": "AO"},
                "summary": "测试任务",
                "status": {"name": "执行中"},
                "issuetype": {"name": "任务"},
                "assignee": {"accountId": "user-1"},
            },
        }
        return MaintainerJiraService(client), transport, client

    def test_plan_comment_roundtrip(self) -> None:
        service, transport, _client = self._service()
        plan = service.plan_comment(
            "AO-11",
            "idem-1",
            "progress",
            "已完成设计并进入实现",
            maintainer_run_id="maint-test-1",
        )
        self.assertEqual("create_or_update", plan.action)
        result = service.apply_comment(plan, plan.plan_id)
        self.assertEqual(True, result["created"])
        self.assertEqual("c1", result["external_id"])
        readback = service.readback_comment(plan)
        self.assertEqual("c1", readback["external_id"])
        # 幂等：重复 plan 应识别已有记录
        plan2 = service.plan_comment(
            "AO-11",
            "idem-1",
            "progress",
            "已完成设计并进入实现",
            maintainer_run_id="maint-test-1",
        )
        self.assertEqual("no_op", plan2.action)
        self.assertEqual("c1", plan2.existing_external_id)

    def test_plan_comment_requires_chinese(self) -> None:
        service, _transport, _client = self._service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_comment(
                "AO-11",
                "idem-2",
                "progress",
                "english only",
                maintainer_run_id="maint-test-1",
            )
        self.assertEqual("chinese_content_required", captured.exception.code)

    def test_apply_rejects_mismatched_plan_id(self) -> None:
        service, _transport, _client = self._service()
        plan = service.plan_comment(
            "AO-11",
            "idem-3",
            "progress",
            "计划内容确认",
            maintainer_run_id="maint-test-1",
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_comment(plan, "plan-wrong")
        self.assertEqual("jira_write_plan_mismatch", captured.exception.code)

    def test_worklog_plan_apply_readback(self) -> None:
        service, _transport, _client = self._service()
        plan = service.plan_worklog(
            "AO-11",
            "idem-w1",
            "实现 jira 能力",
            "完成命令组实现与测试",
            1800,
            "2026-08-18T10:00:00+08:00",
            True,
            maintainer_run_id="maint-test-1",
            included_work=[{"description": "编写代码", "seconds": 1200}, {"description": "运行测试", "seconds": 600}],
            excluded_waiting_categories=["等待人工确认", "CI 排队"],
        )
        self.assertEqual("create_or_update", plan.action)
        result = service.apply_worklog(plan, plan.plan_id)
        self.assertEqual(True, result["created"])
        self.assertEqual("w1", result["external_id"])
        readback = service.readback_worklog(plan)
        self.assertEqual("w1", readback["external_id"])

    def test_worklog_rejects_waiting_time(self) -> None:
        service, _transport, _client = self._service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_worklog(
                "AO-11",
                "idem-w2",
                "登记耗时",
                "包含等待时间",
                1800,
                "2026-08-18T10:00:00+08:00",
                False,
                maintainer_run_id="maint-test-1",
                included_work=[{"description": "等待", "seconds": 1800}],
                excluded_waiting_categories=["等待人工确认"],
            )
        self.assertEqual("worklog_waiting_exclusion_required", captured.exception.code)

    def test_validate_no_credentials_blocks_exposure(self) -> None:
        service, _transport, _client = self._service()
        plan = service.plan_comment(
            "AO-11",
            "idem-4",
            "progress",
            "包含凭证内容 super-secret-token-1",
            maintainer_run_id="maint-test-1",
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.validate_no_credentials(plan, "user@example.com", "super-secret-token-1")
        self.assertEqual("jira_credential_exposure_forbidden", captured.exception.code)


class MaintainerJiraCliTest(unittest.TestCase):
    def test_ao_jira_parser_registered_and_disjoint_from_developer(self) -> None:
        parser = build_maintainer_parser()
        actions = [
            action
            for action in parser._actions
            if isinstance(action, __import__("argparse")._SubParsersAction)
        ]
        commands = set(actions[0].choices or {})
        self.assertIn("jira", commands)
        developer_commands = {"workspace", "auth", "task", "report"}
        self.assertEqual(set(), commands & developer_commands)

    def test_apply_does_not_require_issue_key_argument(self) -> None:
        """apply 只从计划文件读取 Issue 绑定，不需要（也没有）--issue-key 参数。"""
        parser = build_maintainer_parser()
        args = parser.parse_args(
            [
                "jira",
                "comment",
                "apply",
                "--plan-file",
                "plan.json",
                "--confirm-plan-id",
                "plan-abc",
                "--authorization-reference",
                "user-confirmation:AO-11:plan-abc",
            ]
        )
        self.assertFalse(hasattr(args, "issue_key"))
        self.assertEqual("apply", args.action)

    def test_cli_requires_source_workspace_for_ao_jira(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "maintainer").mkdir()
            (root / ".agentic-ops-source").write_text("maintainer\n", encoding="utf-8")
            (root / "maintainer" / "AGENTS.md").write_text("# maintainer\n", encoding="utf-8")
            import subprocess

            subprocess.run(["git", "-C", str(root), "init", "-b", "main"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(root), "remote", "add", "origin", "git@github.com:tapstate/agentic-ops.git"],
                check=True,
                capture_output=True,
            )
            with mock.patch(
                "ao_maint.jira.config.load_maintainer_connection"
            ) as load_connection:
                load_connection.side_effect = RuntimeErrorResult(
                    code="jira_connection_not_found",
                    message="缺少 Connection",
                    status="blocked",
                    exit_code=2,
                    required_human_action="请先初始化维护配置",
                )
                import io
                from contextlib import redirect_stderr, redirect_stdout

                stdout = io.StringIO()
                stderr = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = maintainer_main(
                        [
                            "--source-root",
                            str(root),
                            "jira",
                            "auth",
                            "show",
                        ]
                    )
                self.assertEqual(2, exit_code)
                payload = json.loads(stdout.getvalue())
                self.assertEqual("jira_connection_not_found", payload["code"])


if __name__ == "__main__":
    unittest.main()
