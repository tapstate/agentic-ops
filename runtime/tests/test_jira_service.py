from __future__ import annotations

import unittest
from typing import Any

from agentic_ops.config.model import FieldMapping, ProjectProfile
from agentic_ops.jira import JiraClient, JiraService
from agentic_ops.jira.adf import extract_description_section, markdown_to_adf
from agentic_ops.jira.client import JiraTransportError, TransportResponse
from agentic_ops.output import RuntimeErrorResult


class FakeTransport:
    def __init__(self) -> None:
        self.fields = [
            {"id": "customfield_10092", "name": "问题分析"},
            {"id": "customfield_10093", "name": "修复详情"},
        ]
        self.description = markdown_to_adf("原始说明")
        self.comments: list[dict[str, Any]] = []
        self.worklogs: list[dict[str, Any]] = []
        self.unknown_after_comment = False

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> TransportResponse:
        if path == "/rest/api/3/field":
            return TransportResponse(200, self.fields)
        if path.endswith("/comment"):
            if method == "GET":
                return TransportResponse(200, {"comments": self.comments})
            comment = {"id": str(len(self.comments) + 1), "body": body["body"]}
            self.comments.append(comment)
            if self.unknown_after_comment:
                raise JiraTransportError("timeout", response_received=False)
            return TransportResponse(201, comment)
        if path.endswith("/worklog"):
            if method == "GET":
                return TransportResponse(200, {"worklogs": self.worklogs})
            worklog = {
                "id": str(len(self.worklogs) + 10),
                "comment": body["comment"],
                "timeSpentSeconds": body["timeSpentSeconds"],
                "started": body["started"],
            }
            self.worklogs.append(worklog)
            return TransportResponse(201, worklog)
        if "/rest/api/3/issue/" in path and method == "PUT":
            self.description = body["fields"]["description"]
            return TransportResponse(204, None)
        if "/rest/api/3/issue/" in path and method == "GET":
            return TransportResponse(
                200,
                {
                    "id": "10001",
                    "key": "TAP-123",
                    "fields": {
                        "project": {"key": "TAP"},
                        "summary": "修复任务",
                        "status": {"name": "正在进行"},
                        "issuetype": {"name": "任务"},
                        "assignee": {"accountId": "owner-1"},
                        "description": self.description,
                        "customfield_10092": "已有分析",
                        "customfield_10093": "",
                    },
                },
            )
        if path == "/rest/api/3/myself":
            return TransportResponse(200, {"accountId": "owner-1"})
        return TransportResponse(404, None)


def profile() -> ProjectProfile:
    return ProjectProfile(
        profile_id="tapdata",
        connection_id="tapdata-cloud",
        project_key="TAP",
        task_query="project = TAP",
        issue_types=("任务",),
        fields={
            "issue_analysis": FieldMapping(
                logical_name="issue_analysis",
                source="jira_field",
                jira_field="customfield_10092",
                state="active",
                writable=True,
            ),
            "fix_details": FieldMapping(
                logical_name="fix_details",
                source="jira_field",
                jira_field="customfield_10093",
                state="active",
                writable=True,
            ),
        },
    )


class JiraServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeTransport()
        self.service = JiraService(profile(), JiraClient(profile(), self.transport))

    def test_comment_plan_apply_readback_and_replan_are_idempotent(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-analysis", "analysis", "## 分析\n\n需要修复状态写入。"
        )
        self.assertEqual("create_or_update", plan.action)
        applied = self.service.apply_comment(plan, plan.plan_id)
        self.assertEqual(True, applied["created"])
        readback = self.service.readback_comment("TAP-123", "run-1-analysis")
        self.assertEqual(applied["external_id"], readback["external_id"])
        repeated = self.service.plan_comment(
            "TAP-123", "run-1-analysis", "analysis", "## 分析\n\n需要修复状态写入。"
        )
        self.assertEqual("no_op", repeated.action)
        self.assertEqual(False, self.service.apply_comment(repeated, repeated.plan_id)["created"])

    def test_unknown_comment_response_requires_readback_not_retry(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-unknown", "analysis", "分析内容已经使用中文记录。"
        )
        self.transport.unknown_after_comment = True
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(plan, plan.plan_id)
        self.assertEqual("jira_write_result_unknown", captured.exception.code)
        self.assertEqual(False, captured.exception.retry_safe)
        readback = self.service.readback_comment("TAP-123", "run-1-unknown")
        self.assertEqual("1", readback["external_id"])

    def test_worklog_requires_real_duration_chinese_and_waiting_exclusion(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.plan_worklog(
                "TAP-123",
                "run-1-work",
                "实现",
                "完成实现与验证。",
                600,
                "2026-08-13T13:00:00+08:00",
                False,
            )
        self.assertEqual("worklog_waiting_exclusion_required", captured.exception.code)
        plan = self.service.plan_worklog(
            "TAP-123",
            "run-1-work",
            "实现 Jira 回读",
            "本次耗时包括编码、单元测试和结果回读，不包括等待时间。",
            900,
            "2026-08-13T13:00:00.000+0800",
            True,
        )
        applied = self.service.apply_worklog(plan, plan.plan_id)
        self.assertEqual(True, applied["created"])
        readback = self.service.readback_worklog("TAP-123", "run-1-work")
        self.assertEqual(900, readback["time_spent_seconds"])

    def test_description_updates_only_managed_sections(self) -> None:
        plan = self.service.plan_description(
            "TAP-123",
            "run-1-description",
            {"问题分析": "确认原子写入缺少回读。", "实施计划": "先补测试，再实现回读。"},
        )
        applied = self.service.apply_description(plan, plan.plan_id)
        self.assertEqual(True, applied["created"])
        self.assertIn("原始说明", str(self.transport.description))
        self.assertEqual(
            "确认原子写入缺少回读。",
            extract_description_section(self.transport.description, "问题分析"),
        )
        repeated = self.service.plan_description(
            "TAP-123",
            "run-1-description",
            {"问题分析": "确认原子写入缺少回读。", "实施计划": "先补测试，再实现回读。"},
        )
        self.assertEqual("no_op", repeated.action)

    def test_missing_active_custom_field_becomes_capability_gap(self) -> None:
        self.transport.fields = []
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.inspect_issue("TAP-123")
        self.assertEqual("jira_field_mapping_missing", captured.exception.code)
        self.assertEqual("capability_gap", captured.exception.status)

    def test_plan_reference_mismatch_blocks_write(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-plan", "plan", "计划内容已经由研发工程师确认。"
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(plan, "plan-wrong")
        self.assertEqual("jira_write_plan_mismatch", captured.exception.code)
        self.assertEqual([], self.transport.comments)

    def test_tampered_plan_blocks_write(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-tamper", "plan", "计划内容已经由研发工程师确认。"
        )
        payload = plan.to_dict()
        payload["payload"]["markdown"] = "篡改后的内容"
        from agentic_ops.jira.service import WritePlan

        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(WritePlan.from_dict(payload), plan.plan_id)
        self.assertEqual("jira_write_plan_tampered", captured.exception.code)

    def test_assignee_mismatch_blocks_write_plan(self) -> None:
        original = self.transport.request

        def mismatch_user(method: str, path: str, **kwargs: Any) -> TransportResponse:
            if path == "/rest/api/3/myself":
                return TransportResponse(200, {"accountId": "another-user"})
            return original(method, path, **kwargs)

        self.transport.request = mismatch_user  # type: ignore[method-assign]
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.plan_comment(
                "TAP-123", "run-1-owner", "analysis", "分析内容已经使用中文。"
            )
        self.assertEqual("jira_assignee_mismatch", captured.exception.code)

    def test_assignee_change_between_plan_and_apply_blocks_write(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-owner-change", "analysis", "分析内容已经使用中文。"
        )
        original = self.transport.request

        def mismatch_user(method: str, path: str, **kwargs: Any) -> TransportResponse:
            if path == "/rest/api/3/myself":
                return TransportResponse(200, {"accountId": "another-user"})
            return original(method, path, **kwargs)

        self.transport.request = mismatch_user  # type: ignore[method-assign]
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(plan, plan.plan_id)
        self.assertEqual("jira_assignee_mismatch", captured.exception.code)
        self.assertEqual([], self.transport.comments)
