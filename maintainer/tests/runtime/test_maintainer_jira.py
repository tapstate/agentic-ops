from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from ao_maint.cli import build_parser as build_maintainer_parser
from ao_maint.cli import main as maintainer_main
from ao_maint.jira.cli import _execute_auth, execute_jira
from ao_maint.jira.client import JiraClient, JiraConnection, TransportResponse
from ao_maint.jira.config import (
    env_file_path,
    load_maintainer_connection,
    load_maintainer_jira_config,
    plans_dir,
    set_credentials,
)
from ao_maint.jira.service import MaintainerJiraService, _build_plan
from ao_maint.output import RuntimeErrorResult


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.comments: list[dict[str, object]] = []
        self.worklogs: list[dict[str, object]] = []
        self.issues: list[dict[str, object]] = []
        self.issue: dict[str, object] | None = None
        self.next_issue_id = 1000
        self.create_meta_payload: dict[str, object] | None = None
        self.jql_handler: Any | None = None

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
        if path == "/rest/api/3/issue":
            if method == "POST":
                return self._create_issue(body)
            raise AssertionError(f"unexpected method: {method} {path}")
        if path == "/rest/api/3/issue/createmeta":
            if self.create_meta_payload is not None:
                return TransportResponse(200, self.create_meta_payload)
            raise AssertionError("createmeta not configured")
        if path == "/rest/api/3/search/jql":
            if self.jql_handler is not None:
                return TransportResponse(200, self.jql_handler(query or {}))
            raise AssertionError("jql handler not configured")
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
        if path.startswith("/rest/api/3/issue/") and method == "PUT":
            if self.issue is not None and isinstance(self.issue.get("fields"), dict):
                fields = body.get("fields", {}) if isinstance(body, dict) else {}
                if isinstance(fields, dict) and "description" in fields:
                    self.issue["fields"]["description"] = fields["description"]  # type: ignore[index]
            return TransportResponse(204, None)
        if path.startswith("/rest/api/3/issue/"):
            issue_key = path.removeprefix("/rest/api/3/issue/").split("?")[0]
            for item in self.issues:
                if str(item.get("key", "")) == issue_key:
                    return TransportResponse(200, item)
            if self.issue is not None:
                return TransportResponse(200, self.issue)
            raise AssertionError(f"issue not found: {issue_key}")
        raise AssertionError(f"unexpected request: {method} {path}")

    def _create_issue(self, body: dict[str, object] | None) -> TransportResponse:
        fields = body.get("fields", {}) if isinstance(body, dict) else {}
        if not isinstance(fields, dict):
            raise AssertionError("create issue fields must be dict")
        self.next_issue_id += 1
        key = f"AO-{self.next_issue_id}"
        item: dict[str, object] = {
            "id": str(self.next_issue_id),
            "key": key,
            "fields": dict(fields),
        }
        self.issues.append(item)
        return TransportResponse(201, {"id": str(self.next_issue_id), "key": key})


def _ao_createmeta_payload() -> dict[str, object]:
    return {
        "projects": [
            {
                "key": "AO",
                "name": "agentic-ops",
                "issuetypes": [
                    {
                        "id": "10100",
                        "name": "任务",
                        "subtask": False,
                        "fields": {
                            "summary": {"required": True, "name": "摘要"},
                            "project": {"required": True, "name": "项目"},
                            "issuetype": {"required": True, "name": "事务类型"},
                            "reporter": {"required": True, "name": "报告人"},
                            "description": {"required": False, "name": "描述"},
                            "customfield_10353": {
                                "required": True,
                                "name": "执行模式",
                                "schema": {
                                    "type": "option",
                                    "custom": "com.atlassian.jira.plugin.system.customfieldtypes:select",
                                    "customId": 10353,
                                },
                                "allowedValues": [
                                    {"value": "研发模式"},
                                    {"value": "评估模式"},
                                ],
                            },
                        },
                    }
                ],
            }
        ]
    }


def _ao_subtask_createmeta_payload() -> dict[str, object]:
    return {
        "projects": [
            {
                "key": "AO",
                "name": "agentic-ops",
                "issuetypes": [
                    {
                        "id": "10101",
                        "name": "子任务",
                        "subtask": True,
                        "fields": {
                            "summary": {"required": True, "name": "摘要"},
                            "project": {"required": True, "name": "项目"},
                            "issuetype": {"required": True, "name": "事务类型"},
                            "reporter": {"required": True, "name": "报告人"},
                            "description": {"required": False, "name": "描述"},
                            "parent": {
                                "required": True,
                                "name": "父级",
                                "schema": {"type": "issuelink", "system": "parent"},
                            },
                        },
                    }
                ],
            }
        ]
    }


def _parent_issue(
    *, key: str = "AO-43", issue_id: str = "41703", project_key: str = "AO"
) -> dict[str, object]:
    return {
        "id": issue_id,
        "key": key,
        "fields": {
            "summary": "使用测试任务验证接管流程",
            "status": {"name": "待办"},
            "issuetype": {"name": "任务", "subtask": False},
            "assignee": {"accountId": "user-1"},
            "project": {"key": project_key},
            "description": None,
        },
    }


def _adf_text(value: Any) -> str:
    """提取 ADF 文档的纯文本，用于测试断言。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_adf_text(item) for item in value)))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        return _adf_text(value.get("content", []))
    return ""


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

    def test_non_ao_issue_is_blocked_before_transport(self) -> None:
        service, transport, _client = self._service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.inspect_issue("TAP-12289")
        self.assertEqual(
            "maintainer_jira_project_scope_mismatch", captured.exception.code
        )
        self.assertEqual([], transport.requests)

    def test_remote_issue_project_mismatch_is_blocked(self) -> None:
        service, transport, _client = self._service()
        assert transport.issue is not None
        fields = transport.issue["fields"]
        assert isinstance(fields, dict)
        fields["project"] = {"key": "TAP"}
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.inspect_issue("AO-11")
        self.assertEqual(
            "maintainer_jira_project_scope_mismatch", captured.exception.code
        )
        self.assertEqual([("GET", "/rest/api/3/issue/AO-11")], transport.requests)

    def test_recomputed_non_ao_plan_is_blocked_before_transport(self) -> None:
        service, transport, _client = self._service()
        plan = _build_plan(
            "jira_comment",
            "TAP-12289",
            "maint-test-1",
            "idem-recomputed-scope",
            {
                "category": "analysis",
                "content": "越界计划",
                "markdown": "越界计划\n",
                "body_sha256": "0" * 64,
            },
            "",
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_comment(plan, plan.plan_id)
        self.assertEqual(
            "maintainer_jira_project_scope_mismatch", captured.exception.code
        )
        self.assertEqual([], transport.requests)

    def test_plan_description_roundtrip(self) -> None:
        service, transport, _client = self._service()
        plan = service.plan_description(
            "AO-11",
            "idem-desc-1",
            "## 背景\n\n这是新的任务描述。",
            maintainer_run_id="maint-test-1",
        )
        self.assertEqual("create_or_update", plan.action)
        result = service.apply_description(plan, plan.plan_id)
        self.assertEqual(True, result["created"])
        self.assertEqual("description", result["external_id"])
        # 幂等：重复 apply 应识别已一致
        plan2 = service.plan_description(
            "AO-11",
            "idem-desc-1",
            "## 背景\n\n这是新的任务描述。",
            maintainer_run_id="maint-test-1",
        )
        self.assertEqual("no_op", plan2.action)
        result2 = service.apply_description(plan2, plan2.plan_id)
        self.assertEqual(False, result2["created"])

    def test_plan_description_requires_chinese(self) -> None:
        service, _transport, _client = self._service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_description(
                "AO-11",
                "idem-desc-2",
                "english only description",
                maintainer_run_id="maint-test-1",
            )
        self.assertEqual("chinese_content_required", captured.exception.code)

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

    def _template_schema(self) -> dict[str, Any]:
        return {
            "templates": {
                "progress": {
                    "required_fields": [
                        "run_id",
                        "current_stage",
                        "completed_actions",
                        "execution_plan",
                        "risk",
                    ],
                    "field_keys": {
                        "run_id": "运行 ID",
                        "current_stage": "当前阶段",
                        "completed_actions": "已完成动作",
                        "execution_plan": "执行计划",
                        "risk": "风险",
                    },
                },
                "evidence": {
                    "required_fields": [
                        "run_id",
                        "completed_content",
                        "verification_result",
                        "residual_risk",
                        "output_fields",
                    ],
                    "field_keys": {
                        "run_id": "运行 ID",
                        "completed_content": "完成内容",
                        "verification_result": "验证结果",
                        "residual_risk": "残留风险",
                        "output_fields": "已输出表单字段",
                    },
                },
            }
        }

    def test_progress_comment_requires_template_fields(self) -> None:
        service, _transport, _client = self._service()
        schema = self._template_schema()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_comment(
                "AO-11",
                "idem-t1",
                "progress",
                "开始处理任务。",
                maintainer_run_id="maint-test-1",
                comment_template_schema=schema,
            )
        self.assertEqual("jira_comment_template_fields_missing", captured.exception.code)
        self.assertIn("run_id(运行 ID)", captured.exception.details["missing_fields"])

    def test_progress_comment_passes_with_complete_template(self) -> None:
        service, _transport, _client = self._service()
        schema = self._template_schema()
        content = (
            "- 运行 ID: maint-test-1\n"
            "- 当前阶段: implementation\n"
            "- 已完成动作: 开始处理\n"
            "- 执行计划: 完成代码与验证\n"
            "- 风险: 无\n"
        )
        plan = service.plan_comment(
            "AO-11",
            "idem-t2",
            "progress",
            content,
            maintainer_run_id="maint-test-1",
            comment_template_schema=schema,
        )
        self.assertEqual("create_or_update", plan.action)

    def test_evidence_comment_requires_template_fields(self) -> None:
        service, _transport, _client = self._service()
        schema = self._template_schema()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_comment(
                "AO-11",
                "idem-t3",
                "evidence",
                "任务完成。",
                maintainer_run_id="maint-test-1",
                comment_template_schema=schema,
            )
        self.assertEqual("jira_comment_template_fields_missing", captured.exception.code)
        self.assertIn("run_id(运行 ID)", captured.exception.details["missing_fields"])

    def test_non_template_category_skips_validation(self) -> None:
        service, _transport, _client = self._service()
        schema = self._template_schema()
        plan = service.plan_comment(
            "AO-11",
            "idem-t4",
            "analysis",
            "分析结论。",
            maintainer_run_id="maint-test-1",
            comment_template_schema=schema,
        )
        self.assertEqual("create_or_update", plan.action)

    def _create_service(self) -> tuple[MaintainerJiraService, FakeTransport]:
        connection = JiraConnection(
            connection_id="tapdata-cloud",
            base_url="https://tapdata.atlassian.net",
            email_env="TAPDATA_JIRA_EMAIL",
            token_env="TAPDATA_JIRA_API_TOKEN",
        )
        transport = FakeTransport()
        transport.create_meta_payload = _ao_createmeta_payload()
        transport.jql_handler = self._make_jql_handler(transport)
        client = JiraClient(connection, transport)
        return MaintainerJiraService(client), transport

    @staticmethod
    def _make_jql_handler(transport: FakeTransport):
        def handler(query: dict[str, str] | None) -> dict[str, object]:
            # 简化 JQL：返回 issues 中 description 含幂等词的记录
            matches = []
            for item in transport.issues:
                fields = item.get("fields", {})
                description = fields.get("description", {}) if isinstance(fields, dict) else {}
                text = _adf_text(description)
                if "agentic-ops-maintainer-idempotency" in text:
                    matches.append(item)
            return {"issues": matches, "total": len(matches)}

        return handler

    def test_create_issue_roundtrip(self) -> None:
        service, transport = self._create_service()
        plan = service.plan_create_issue(
            "AO",
            "idem-c1",
            maintainer_run_id="maint-test-1",
            issuetype_name="任务",
            summary="新增维护面建卡能力",
            description="为维护面新增 Jira 建卡能力。",
            assignee="712020:b86ae2c3-9527-4dad-9b5c-d1a79a9180f0",
            extra_fields={"customfield_10353": "研发模式"},
        )
        self.assertEqual("create_or_update", plan.action)
        self.assertEqual("AO", plan.issue_key)
        result = service.apply_create_issue(plan, plan.plan_id)
        self.assertEqual(True, result["created"])
        self.assertEqual("AO-1001", result["external_id"])
        # apply 时实际创建了任务
        self.assertEqual(1, len(transport.issues))
        created_fields = transport.issues[0]["fields"]
        assert isinstance(created_fields, dict)
        self.assertEqual("新增维护面建卡能力", created_fields["summary"])
        self.assertEqual({"value": "研发模式"}, created_fields["customfield_10353"])
        readback = service.readback_create_issue(plan, "AO-1001")
        self.assertEqual("AO-1001", readback["external_id"])
        self.assertEqual(True, readback["created"])

    def test_create_subtask_roundtrip_with_typed_parent(self) -> None:
        service, transport = self._create_service()
        transport.create_meta_payload = _ao_subtask_createmeta_payload()
        transport.issues.append(_parent_issue())
        plan = service.plan_create_issue(
            "AO",
            "idem-subtask-1",
            maintainer_run_id="maint-test-1",
            issuetype_name="子任务",
            summary="建立维护面工具路由门禁",
            description="确保 AO Jira 写入只使用维护 Runtime。",
            parent_key="AO-43",
        )
        self.assertEqual(
            {
                "key": "AO-43",
                "issue_id": "41703",
                "project_key": "AO",
                "issue_type": "任务",
            },
            plan.payload["parent"],
        )
        self.assertEqual({"key": "AO-43"}, plan.payload["fields"]["parent"])
        result = service.apply_create_issue(plan, plan.plan_id)
        self.assertEqual(True, result["created"])
        created_fields = transport.issues[-1]["fields"]
        assert isinstance(created_fields, dict)
        self.assertEqual({"key": "AO-43"}, created_fields["parent"])

    def test_create_subtask_rejects_generic_parent_field(self) -> None:
        service, transport = self._create_service()
        transport.create_meta_payload = _ao_subtask_createmeta_payload()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_create_issue(
                "AO",
                "idem-subtask-2",
                maintainer_run_id="maint-test-1",
                issuetype_name="子任务",
                summary="拒绝模糊父任务字段",
                description="",
                extra_fields={"parent": {"key": "AO-43"}},
            )
        self.assertEqual(
            "jira_create_parent_requires_typed_argument", captured.exception.code
        )

    def test_create_subtask_rejects_cross_project_parent(self) -> None:
        service, transport = self._create_service()
        transport.create_meta_payload = _ao_subtask_createmeta_payload()
        transport.issues.append(
            _parent_issue(key="TAP-12289", issue_id="40000", project_key="TAP")
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_create_issue(
                "AO",
                "idem-subtask-3",
                maintainer_run_id="maint-test-1",
                issuetype_name="子任务",
                summary="拒绝跨项目父任务",
                description="",
                parent_key="TAP-12289",
            )
        self.assertEqual(
            "maintainer_jira_project_scope_mismatch", captured.exception.code
        )
        self.assertEqual([], transport.requests)

    def test_create_subtask_parent_change_blocks_apply(self) -> None:
        service, transport = self._create_service()
        transport.create_meta_payload = _ao_subtask_createmeta_payload()
        transport.issues.append(_parent_issue())
        plan = service.plan_create_issue(
            "AO",
            "idem-subtask-4",
            maintainer_run_id="maint-test-1",
            issuetype_name="子任务",
            summary="父任务变化时阻断建卡",
            description="",
            parent_key="AO-43",
        )
        transport.issues[0]["id"] = "99999"
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_create_issue(plan, plan.plan_id)
        self.assertEqual("jira_create_parent_changed", captured.exception.code)

    def test_create_subtask_parent_readback_mismatch_is_blocked(self) -> None:
        service, transport = self._create_service()
        transport.create_meta_payload = _ao_subtask_createmeta_payload()
        transport.issues.append(_parent_issue())
        plan = service.plan_create_issue(
            "AO",
            "idem-subtask-5",
            maintainer_run_id="maint-test-1",
            issuetype_name="子任务",
            summary="回读校验子任务父级",
            description="",
            parent_key="AO-43",
        )
        result = service.apply_create_issue(plan, plan.plan_id)
        created = next(
            item for item in transport.issues if item.get("key") == result["external_id"]
        )
        fields = created["fields"]
        assert isinstance(fields, dict)
        fields["parent"] = {"key": "AO-44"}
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.readback_create_issue(plan, str(result["external_id"]))
        self.assertEqual(
            "jira_create_parent_readback_mismatch", captured.exception.code
        )

    def test_create_issue_idempotent_no_op(self) -> None:
        service, transport = self._create_service()
        plan = service.plan_create_issue(
            "AO",
            "idem-c2",
            maintainer_run_id="maint-test-1",
            issuetype_name="任务",
            summary="新增维护面建卡能力",
            description="为维护面新增 Jira 建卡能力。",
            extra_fields={"customfield_10353": "研发模式"},
        )
        service.apply_create_issue(plan, plan.plan_id)
        # 幂等：相同幂等键重新 plan 应识别已有任务
        plan2 = service.plan_create_issue(
            "AO",
            "idem-c2",
            maintainer_run_id="maint-test-1",
            issuetype_name="任务",
            summary="新增维护面建卡能力",
            description="为维护面新增 Jira 建卡能力。",
            extra_fields={"customfield_10353": "研发模式"},
        )
        self.assertEqual("no_op", plan2.action)
        self.assertEqual("AO-1001", plan2.existing_external_id)
        result = service.apply_create_issue(plan2, plan2.plan_id)
        self.assertEqual(False, result["created"])
        self.assertEqual("AO-1001", result["external_id"])
        # 没有重复建卡
        self.assertEqual(1, len(transport.issues))

    def test_create_issue_requires_chinese_summary(self) -> None:
        service, _transport = self._create_service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_create_issue(
                "AO",
                "idem-c3",
                maintainer_run_id="maint-test-1",
                issuetype_name="任务",
                summary="english only summary",
                description="",
            )
        self.assertEqual("chinese_content_required", captured.exception.code)

    def test_create_issue_missing_required_field(self) -> None:
        service, _transport = self._create_service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_create_issue(
                "AO",
                "idem-c4",
                maintainer_run_id="maint-test-1",
                issuetype_name="任务",
                summary="缺少必填字段",
                description="",
            )
        self.assertEqual("jira_create_required_fields_missing", captured.exception.code)
        self.assertIn("customfield_10353", captured.exception.details["required_fields"])

    def test_create_issue_unknown_field_rejected(self) -> None:
        service, _transport = self._create_service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_create_issue(
                "AO",
                "idem-c5",
                maintainer_run_id="maint-test-1",
                issuetype_name="任务",
                summary="未知字段被拒绝",
                description="",
                extra_fields={
                    "customfield_10353": "研发模式",
                    "customfield_99999": "研发模式",
                },
            )
        self.assertEqual("jira_create_unknown_field", captured.exception.code)

    def test_create_issue_rejects_invalid_project_key(self) -> None:
        service, _transport = self._create_service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_create_issue(
                "AO-1",
                "idem-c6",
                maintainer_run_id="maint-test-1",
                issuetype_name="任务",
                summary="非法项目 Key",
                description="",
                extra_fields={"customfield_10353": "研发模式"},
            )
        self.assertEqual("invalid_project_key", captured.exception.code)

    def test_create_issue_rejects_non_ao_project_before_transport(self) -> None:
        service, transport = self._create_service()
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_create_issue(
                "TAP",
                "idem-c-scope",
                maintainer_run_id="maint-test-1",
                issuetype_name="任务",
                summary="拒绝业务项目建卡",
                description="",
            )
        self.assertEqual(
            "maintainer_jira_project_scope_mismatch", captured.exception.code
        )
        self.assertEqual([], transport.requests)

    def test_create_issue_meta_changed_blocks_apply(self) -> None:
        service, transport = self._create_service()
        plan = service.plan_create_issue(
            "AO",
            "idem-c7",
            maintainer_run_id="maint-test-1",
            issuetype_name="任务",
            summary="createmeta 变化阻断 apply",
            description="",
            extra_fields={"customfield_10353": "研发模式"},
        )
        # 篡改 createmeta 的 issuetype id，模拟 Jira 侧类型变化
        payload = _ao_createmeta_payload()
        payload["projects"][0]["issuetypes"][0]["id"] = "99999"
        transport.create_meta_payload = payload
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_create_issue(plan, plan.plan_id)
        self.assertEqual("jira_create_meta_changed", captured.exception.code)


class MaintainerJiraCliTest(unittest.TestCase):
    def test_non_ao_inputs_are_blocked_before_config_and_credentials(self) -> None:
        parser = build_maintainer_parser()
        cases = (
            ["jira", "inspect", "--issue-key", "TAP-12289"],
            [
                "jira",
                "create",
                "plan",
                "--project-key",
                "TAP",
                "--issuetype",
                "任务",
                "--summary",
                "禁止越界建卡",
                "--idempotency-key",
                "scope-preflight",
                "--plan-file",
                "scope.json",
            ],
        )
        with tempfile.TemporaryDirectory() as temporary:
            for argv in cases:
                with self.subTest(argv=argv), mock.patch(
                    "ao_maint.jira.cli.load_maintainer_jira_config"
                ) as load_config:
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        execute_jira(parser.parse_args(argv), Path(temporary))
                    self.assertEqual(
                        "maintainer_jira_project_scope_mismatch",
                        captured.exception.code,
                    )
                    load_config.assert_not_called()

    def test_auth_verify_reports_ao_scope_without_global_field_probe(self) -> None:
        parser = build_maintainer_parser()
        args = parser.parse_args(["jira", "auth", "verify"])
        connection = JiraConnection(
            connection_id="tapdata-cloud",
            base_url="https://tapdata.atlassian.net",
            email_env="TAPDATA_JIRA_EMAIL",
            token_env="TAPDATA_JIRA_API_TOKEN",
        )
        config = mock.Mock(connection=connection)
        config.require_credentials.return_value = (
            "maintainer@example.com",
            "token-123456",
        )
        transport = FakeTransport()
        with (
            mock.patch(
                "ao_maint.jira.cli.credential_status",
                return_value={"credential_source": "maintainer_local"},
            ),
            mock.patch(
                "ao_maint.jira.cli.UrllibJiraTransport", return_value=transport
            ),
        ):
            result = _execute_auth(args, Path("/unused"), config)
        self.assertEqual(["AO"], result["allowed_project_keys"])
        self.assertNotIn("field_count", result)
        self.assertEqual([("GET", "/rest/api/3/myself")], transport.requests)

    def test_non_ao_plan_file_blocks_before_config_and_decision_audit(self) -> None:
        parser = build_maintainer_parser()
        plan = _build_plan(
            "jira_comment",
            "TAP-12289",
            "maint-test-1",
            "scope-plan-file",
            {
                "category": "analysis",
                "content": "越界计划",
                "markdown": "越界计划\n",
                "body_sha256": "0" * 64,
            },
            "",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_directory = root / "maintainer" / ".local" / "jira-plans"
            plan_directory.mkdir(parents=True)
            (plan_directory / "scope.json").write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
            args = parser.parse_args(
                [
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    "scope.json",
                    "--confirm-plan-id",
                    plan.plan_id,
                    "--authorization-reference",
                    f"user-confirmation:TAP-12289:{plan.plan_id}",
                ]
            )
            with mock.patch(
                "ao_maint.jira.cli.load_maintainer_jira_config"
            ) as load_config, self.assertRaises(RuntimeErrorResult) as captured:
                execute_jira(args, root)
            self.assertEqual(
                "maintainer_jira_project_scope_mismatch", captured.exception.code
            )
            load_config.assert_not_called()
            self.assertFalse((root / "maintainer" / ".local" / "decisions.ndjson").exists())

    def test_ao_jira_parser_registered_and_disjoint_from_developer(self) -> None:
        parser = build_maintainer_parser()
        actions = [
            action
            for action in parser._actions
            if isinstance(action, __import__("argparse")._SubParsersAction)
        ]
        commands = set(actions[0].choices or {})
        self.assertIn("jira", commands)
        self.assertIn("install", commands)
        developer_commands = {"workspace", "auth", "task", "report"}
        self.assertEqual(set(), commands & developer_commands)

    def test_install_identity_parser_registered(self) -> None:
        parser = build_maintainer_parser()
        args = parser.parse_args(
            ["install", "identity", "set", "--agent-id", "hermes-agent"]
        )
        self.assertEqual("install", args.group)
        self.assertEqual("identity", args.command)
        self.assertEqual("set", args.action)
        self.assertEqual("hermes-agent", args.agent_id)
        args_show = parser.parse_args(["install", "identity", "show"])
        self.assertEqual("show", args_show.action)
        args_interactive = parser.parse_args(
            ["install", "identity", "set", "--interactive"]
        )
        self.assertTrue(args_interactive.interactive)

    def test_create_parser_registered_with_plan_apply_readback(self) -> None:
        parser = build_maintainer_parser()
        # jira create plan 参数
        args = parser.parse_args(
            [
                "jira",
                "create",
                "plan",
                "--project-key",
                "AO",
                "--issuetype",
                "任务",
                "--summary",
                "测试建卡",
                "--description-file",
                "desc.md",
                "--assignee",
                "712020:b86ae2c3-9527-4dad-9b5c-d1a79a9180f0",
                "--parent",
                "AO-43",
                "--field",
                "customfield_10353=研发模式",
                "--idempotency-key",
                "idem-x",
                "--plan-file",
                "create.json",
            ]
        )
        self.assertEqual("create", args.command)
        self.assertEqual("plan", args.action)
        self.assertEqual("AO", args.project_key)
        self.assertEqual("任务", args.issuetype)
        self.assertEqual("AO-43", args.parent)
        self.assertEqual(["customfield_10353=研发模式"], args.field)
        # jira create apply 参数
        args_apply = parser.parse_args(
            [
                "jira",
                "create",
                "apply",
                "--plan-file",
                "create.json",
                "--confirm-plan-id",
                "plan-abc",
                "--authorization-reference",
                "user-confirmation:AO-11:plan-abc",
            ]
        )
        self.assertEqual("apply", args_apply.action)
        self.assertFalse(hasattr(args_apply, "issue_key"))
        # jira create readback 需要真实 issue key
        args_readback = parser.parse_args(
            [
                "jira",
                "create",
                "readback",
                "--issue-key",
                "AO-99",
                "--idempotency-key",
                "idem-x",
                "--plan-file",
                "create.json",
                "--confirm-plan-id",
                "plan-abc",
            ]
        )
        self.assertEqual("readback", args_readback.action)
        self.assertEqual("AO-99", args_readback.issue_key)

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
