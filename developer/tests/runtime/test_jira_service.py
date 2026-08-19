from __future__ import annotations

import io
import unittest
import urllib.error
from typing import Any
from unittest import mock

from ao_work.config.model import FieldMapping, JiraConnection, ProjectProfile
from ao_work.jira import JiraClient, JiraService
from ao_work.jira.adf import extract_description_section, markdown_to_adf
from ao_work.jira.client import JiraTransportError, TransportResponse, UrllibJiraTransport
from ao_work.jira.service import build_write_attempt
from ao_work.output import RuntimeErrorResult


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
        self.requests: list[tuple[str, str]] = []
        self.queries: list[dict[str, str]] = []
        self.search_issues: list[dict[str, Any]] = []
        self.search_total: int | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> TransportResponse:
        self.requests.append((method, path))
        self.queries.append(dict(query or {}))
        if path == "/rest/api/3/search/jql":
            return TransportResponse(
                200,
                {
                    "issues": self.search_issues,
                    "total": (
                        self.search_total
                        if self.search_total is not None
                        else len(self.search_issues)
                    ),
                    "startAt": 0,
                    "maxResults": 50,
                },
            )
        if path == "/rest/api/3/field":
            return TransportResponse(200, self.fields)
        if "/comment/" in path and method == "GET":
            comment_id = path.rsplit("/", 1)[-1]
            for comment in self.comments:
                if str(comment.get("id")) == comment_id:
                    return TransportResponse(200, comment)
            return TransportResponse(404, None)
        if path.endswith("/comment"):
            if method == "GET":
                start = int((query or {}).get("startAt", "0"))
                size = int((query or {}).get("maxResults", "100"))
                return TransportResponse(200, {
                    "comments": self.comments[start : start + size],
                    "startAt": start,
                    "maxResults": size,
                    "total": len(self.comments),
                })
            comment = {"id": str(len(self.comments) + 1), "body": body["body"]}
            self.comments.append(comment)
            if self.unknown_after_comment:
                raise JiraTransportError("timeout", response_received=False)
            return TransportResponse(201, comment)
        if path.endswith("/worklog"):
            if method == "GET":
                start = int((query or {}).get("startAt", "0"))
                size = int((query or {}).get("maxResults", "100"))
                return TransportResponse(200, {
                    "worklogs": self.worklogs[start : start + size],
                    "startAt": start,
                    "maxResults": size,
                    "total": len(self.worklogs),
                })
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
            "problem_analysis": FieldMapping(
                logical_name="problem_analysis",
                source="jira_description_section",
                section="问题分析",
                state="active",
                writable=True,
            ),
            "implementation_plan": FieldMapping(
                logical_name="implementation_plan",
                source="jira_description_section",
                section="实施计划",
                state="active",
                writable=True,
            ),
        },
    )


class JiraServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeTransport()
        self.service = JiraService(profile(), JiraClient(profile(), self.transport))

    @staticmethod
    def _begin_create(plan: Any) -> Any:
        return build_write_attempt(
            plan,
            "user-confirmation:TAP-123:run-1:test-plan",
            request_started_at="2026-08-13T04:00:00+00:00",
        )

    def test_comment_plan_apply_readback_and_replan_are_idempotent(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-analysis", "analysis", "## 分析\n\n需要修复状态写入。",
            agentic_run_id="run-1",
        )
        self.assertEqual("create_or_update", plan.action)
        applied = self.service.apply_comment(
            plan, plan.plan_id, begin_create=self._begin_create
        )
        self.assertEqual(True, applied["created"])
        attempt = self._begin_create(plan)
        readback = self.service.readback_comment(plan, attempt=attempt)
        self.assertEqual(applied["external_id"], readback["external_id"])
        repeated = self.service.plan_comment(
            "TAP-123", "run-1-analysis", "analysis", "## 分析\n\n需要修复状态写入。",
            agentic_run_id="run-1",
        )
        self.assertEqual("no_op", repeated.action)
        repeated_readback = self.service.apply_comment(repeated, repeated.plan_id)
        self.assertFalse(repeated_readback["created"])
        self.assertEqual("preexisting", repeated_readback["write_precondition"])

    def test_comment_idempotency_marker_must_be_exact_top_level_paragraph(self) -> None:
        marker = "[agentic-ops-idempotency:TAP-123:run-1:run-marker-comment]"
        quoted = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "blockquote",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": marker}],
                        }
                    ],
                },
                {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": marker}],
                },
            ],
        }
        self.transport.comments = [
            {"id": "1", "body": markdown_to_adf(f"{marker}-suffix")},
            {"id": "2", "body": markdown_to_adf(f"prefix-{marker}")},
            {"id": "3", "body": quoted},
        ]
        plan = self.service.plan_comment(
            "TAP-123",
            "run-marker-comment",
            "analysis",
            "该评论必须创建自己的精确幂等标记。",
            agentic_run_id="run-1",
        )
        self.assertEqual("create_or_update", plan.action)
        applied = self.service.apply_comment(plan, plan.plan_id, begin_create=self._begin_create)
        self.assertEqual(True, applied["created"])
        self.assertEqual("4", applied["external_id"])
        self.assertEqual(
            "4",
            self.service.readback_comment(
                plan, attempt=self._begin_create(plan)
            )[
                "external_id"
            ],
        )

    def test_comment_idempotency_content_conflict_is_blocked(self) -> None:
        marker = "[agentic-ops-idempotency:TAP-123:run-1:run-comment-conflict]"
        self.transport.comments = [
            {"id": "1", "body": markdown_to_adf(f"旧的中文正文。\n\n{marker}\n")}
        ]
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.plan_comment(
                "TAP-123",
                "run-comment-conflict",
                "analysis",
                "新的中文正文。",
                agentic_run_id="run-1",
            )
        self.assertEqual("jira_idempotency_content_conflict", captured.exception.code)
        self.assertEqual(False, captured.exception.retry_safe)

    def test_comment_preexisting_after_plan_cannot_be_attributed_to_this_run(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123",
            "concurrent-record",
            "evidence",
            "该评论在计划后由其它入口提前出现。",
            agentic_run_id="run-1",
        )
        self.transport.comments = [
            {
                "id": "1",
                "body": markdown_to_adf(str(plan.payload["markdown"])),
            }
        ]
        begin = mock.Mock(side_effect=self._begin_create)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(
                plan,
                plan.plan_id,
                begin_create=begin,
            )
        self.assertEqual("jira_write_precondition_changed", captured.exception.code)
        begin.assert_not_called()

    def test_comment_readback_requires_original_create_attempt(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123",
            "missing-attempt",
            "evidence",
            "该评论只能由原始写入尝试恢复回读。",
            agentic_run_id="run-1",
        )
        self.service.apply_comment(
            plan,
            plan.plan_id,
            begin_create=self._begin_create,
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.readback_comment(plan)
        self.assertEqual("jira_write_attempt_missing", captured.exception.code)

    def test_comment_same_key_and_content_from_old_run_cannot_satisfy_new_run(self) -> None:
        old_plan = self.service.plan_comment(
            "TAP-123",
            "shared-key",
            "evidence",
            "相同正文也必须由当前运行创建独立评论。",
            agentic_run_id="run-old",
        )
        old_applied = self.service.apply_comment(old_plan, old_plan.plan_id, begin_create=self._begin_create)
        new_plan = self.service.plan_comment(
            "TAP-123",
            "shared-key",
            "evidence",
            "相同正文也必须由当前运行创建独立评论。",
            agentic_run_id="run-new",
        )
        self.assertEqual("create_or_update", new_plan.action)
        new_applied = self.service.apply_comment(new_plan, new_plan.plan_id, begin_create=self._begin_create)
        self.assertTrue(new_applied["created"])
        self.assertNotEqual(old_applied["external_id"], new_applied["external_id"])

    def test_unknown_comment_response_requires_readback_not_retry(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-unknown", "analysis", "分析内容已经使用中文记录。",
            agentic_run_id="run-1",
        )
        self.transport.unknown_after_comment = True
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(plan, plan.plan_id, begin_create=self._begin_create)
        self.assertEqual("jira_write_result_unknown", captured.exception.code)
        self.assertEqual(False, captured.exception.retry_safe)
        self.assertEqual(
            self._begin_create(plan).attempt_id,
            captured.exception.details["write_attempt_id"],
        )
        readback = self.service.readback_comment(
            plan, attempt=self._begin_create(plan)
        )
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
                agentic_run_id="run-1",
                included_work=[{"description": "完成实现与验证", "seconds": 600}],
                excluded_waiting_categories=["等待人工确认"],
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
            agentic_run_id="run-1",
            included_work=[
                {"description": "完成编码与单元测试", "seconds": 600},
                {"description": "完成结果回读", "seconds": 300},
            ],
            excluded_waiting_categories=["等待人工确认", "等待外部系统"],
        )
        applied = self.service.apply_worklog(plan, plan.plan_id, begin_create=self._begin_create)
        self.assertEqual(True, applied["created"])
        readback = self.service.readback_worklog(
            plan, attempt=self._begin_create(plan)
        )
        self.assertEqual(900, readback["time_spent_seconds"])

    def test_worklog_started_is_canonical_and_compared_by_instant(self) -> None:
        plan = self.service.plan_worklog(
            "TAP-123",
            "run-started-canonical",
            "验证时间规范化",
            "本次耗时只包含实际验证，不包括等待时间。",
            300,
            "2026-08-13T04:00:00+00:00",
            True,
            agentic_run_id="run-1",
            included_work=[{"description": "执行实际验证", "seconds": 300}],
            excluded_waiting_categories=["等待人工确认"],
        )
        self.assertEqual("2026-08-13T04:00:00.000+0000", plan.payload["started"])
        self.service.apply_worklog(plan, plan.plan_id, begin_create=self._begin_create)
        # Jira 可能用不同 offset/毫秒格式返回同一时刻，不能误判为幂等冲突。
        self.transport.worklogs[0]["started"] = "2026-08-13T12:00:00.000+0800"
        readback = self.service.readback_worklog(
            plan, attempt=self._begin_create(plan)
        )
        self.assertEqual("10", readback["external_id"])

    def test_worklog_idempotency_marker_rejects_prefix_and_suffix_substrings(self) -> None:
        marker = "[agentic-ops-idempotency:TAP-123:run-1:run-marker-worklog]"
        self.transport.worklogs = [
            {
                "id": "10",
                "comment": markdown_to_adf(f"{marker}-suffix"),
                "timeSpentSeconds": 60,
                "started": "2026-08-13T12:00:00.000+0800",
            },
            {
                "id": "11",
                "comment": markdown_to_adf(f"prefix-{marker}"),
                "timeSpentSeconds": 60,
                "started": "2026-08-13T12:00:00.000+0800",
            },
        ]
        plan = self.service.plan_worklog(
            "TAP-123",
            "run-marker-worklog",
            "验证精确幂等标记",
            "完成实现与回归测试，不包括等待时间。",
            120,
            "2026-08-13T13:00:00.000+0800",
            True,
            agentic_run_id="run-1",
            included_work=[{"description": "完成实现与回归测试", "seconds": 120}],
            excluded_waiting_categories=["等待外部系统"],
        )
        self.assertEqual("create_or_update", plan.action)
        applied = self.service.apply_worklog(plan, plan.plan_id, begin_create=self._begin_create)
        self.assertEqual(True, applied["created"])
        self.assertEqual("12", applied["external_id"])

    def test_worklog_idempotency_content_and_time_conflict_is_blocked(self) -> None:
        marker = "[agentic-ops-idempotency:TAP-123:run-1:run-worklog-conflict]"
        body = "## 实现变更\n\n完成旧实现，不包括等待。\n\n" + marker + "\n"
        self.transport.worklogs = [
            {
                "id": "10",
                "comment": markdown_to_adf(body),
                "timeSpentSeconds": 60,
                "started": "2026-08-13T12:00:00.000+0800",
            }
        ]
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.plan_worklog(
                "TAP-123",
                "run-worklog-conflict",
                "实现变更",
                "完成旧实现，不包括等待。",
                120,
                "2026-08-13T12:00:00.000+0800",
                True,
                agentic_run_id="run-1",
                included_work=[{"description": "完成旧实现", "seconds": 120}],
                excluded_waiting_categories=["等待人工确认"],
            )
        self.assertEqual("jira_idempotency_content_conflict", captured.exception.code)

    def test_worklog_same_key_and_content_from_old_run_cannot_satisfy_new_run(self) -> None:
        arguments = {
            "issue_key": "TAP-123",
            "idempotency_key": "shared-worklog-key",
            "title": "完成实现验证",
            "details": "相同内容仍须由当前运行创建独立 Worklog。",
            "time_spent_seconds": 120,
            "started": "2026-08-13T12:00:00.000+0800",
            "excludes_waiting": True,
            "included_work": [{"description": "完成实现验证", "seconds": 120}],
            "excluded_waiting_categories": ["等待人工确认"],
        }
        old_plan = self.service.plan_worklog(
            **arguments,
            agentic_run_id="run-old",
        )
        old_applied = self.service.apply_worklog(old_plan, old_plan.plan_id, begin_create=self._begin_create)
        new_plan = self.service.plan_worklog(
            **arguments,
            agentic_run_id="run-new",
        )
        self.assertEqual("create_or_update", new_plan.action)
        new_applied = self.service.apply_worklog(new_plan, new_plan.plan_id, begin_create=self._begin_create)
        self.assertTrue(new_applied["created"])
        self.assertNotEqual(old_applied["external_id"], new_applied["external_id"])

    def test_description_updates_only_managed_sections(self) -> None:
        plan = self.service.plan_description(
            "TAP-123",
            "run-1-description",
            {"问题分析": "确认原子写入缺少回读。", "实施计划": "先补测试，再实现回读。"},
            agentic_run_id="run-1",
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
            agentic_run_id="run-1",
        )
        self.assertEqual("no_op", repeated.action)

    def test_description_rejects_unknown_read_only_and_normalized_alias_sections(self) -> None:
        self.transport.description = markdown_to_adf(
            "## 原始缺陷日志\n\n客户原始记录不得覆盖。\n\n"
            "## 问题分析\n\n现有分析。"
        )
        original = self.transport.description
        self.service.profile.fields["raw_log"] = FieldMapping(
            logical_name="raw_log",
            source="jira_description_section",
            section="原始缺陷日志",
            state="read_only",
            writable=True,
        )
        for title in ("原始缺陷日志", "未知章节", "问题分析："):
            with self.subTest(title=title), self.assertRaises(RuntimeErrorResult) as captured:
                self.service.plan_description(
                    "TAP-123",
                    f"run-description-{len(title)}",
                    {title: "试图覆盖未授权章节。"},
                    agentic_run_id="run-1",
                )
            self.assertEqual("description_section_not_writable", captured.exception.code)
            self.assertEqual(original, self.transport.description)

    def test_description_apply_rechecks_effective_profile_writable_mapping(self) -> None:
        plan = self.service.plan_description(
            "TAP-123",
            "run-description-profile-change",
            {"问题分析": "计划生成时章节仍然可写。"},
            agentic_run_id="run-1",
        )
        self.service.profile.fields["problem_analysis"] = FieldMapping(
            logical_name="problem_analysis",
            source="jira_description_section",
            section="问题分析",
            state="read_only",
            writable=False,
        )
        before = self.transport.description
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_description(plan, plan.plan_id)
        self.assertEqual("description_section_not_writable", captured.exception.code)
        self.assertEqual(before, self.transport.description)

    def test_missing_active_custom_field_becomes_capability_gap(self) -> None:
        self.transport.fields = []
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.inspect_issue("TAP-123")
        self.assertEqual("jira_field_mapping_missing", captured.exception.code)
        self.assertEqual("capability_gap", captured.exception.status)

    def test_plan_reference_mismatch_blocks_write(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-plan", "plan", "计划内容已经由研发工程师确认。",
            agentic_run_id="run-1",
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(plan, "plan-wrong")
        self.assertEqual("jira_write_plan_mismatch", captured.exception.code)
        self.assertEqual([], self.transport.comments)

    def test_tampered_plan_blocks_write(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-tamper", "plan", "计划内容已经由研发工程师确认。",
            agentic_run_id="run-1",
        )
        payload = plan.to_dict()
        payload["payload"]["markdown"] = "篡改后的内容"
        from ao_work.jira.service import WritePlan

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
                "TAP-123", "run-1-owner", "analysis", "分析内容已经使用中文。",
                agentic_run_id="run-1",
            )
        self.assertEqual("jira_assignee_mismatch", captured.exception.code)

    def test_assignee_change_between_plan_and_apply_blocks_write(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-1-owner-change", "analysis", "分析内容已经使用中文。",
            agentic_run_id="run-1",
        )
        original = self.transport.request

        def mismatch_user(method: str, path: str, **kwargs: Any) -> TransportResponse:
            if path == "/rest/api/3/myself":
                return TransportResponse(200, {"accountId": "another-user"})
            return original(method, path, **kwargs)

        self.transport.request = mismatch_user  # type: ignore[method-assign]
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.apply_comment(plan, plan.plan_id, begin_create=self._begin_create)
        self.assertEqual("jira_assignee_mismatch", captured.exception.code)
        self.assertEqual([], self.transport.comments)

    def test_comment_idempotency_reads_all_pages_beyond_one_hundred(self) -> None:
        plan = self.service.plan_comment(
            "TAP-123", "run-page-comment", "analysis", "分页幂等评论。",
            agentic_run_id="run-1",
        )
        applied = self.service.apply_comment(plan, plan.plan_id, begin_create=self._begin_create)
        marker = self.transport.comments.pop()
        self.transport.comments = [
            {"id": str(index + 1), "body": markdown_to_adf(f"历史评论 {index}")}
            for index in range(120)
        ] + [marker]
        repeated = self.service.plan_comment(
            "TAP-123", "run-page-comment", "analysis", "分页幂等评论。",
            agentic_run_id="run-1",
        )
        self.assertEqual("no_op", repeated.action)
        self.assertEqual(applied["external_id"], repeated.existing_external_id)
        self.assertTrue(any(query.get("startAt") == "100" for query in self.transport.queries))

    def test_worklog_readback_reads_all_pages_beyond_one_hundred(self) -> None:
        plan = self.service.plan_worklog(
            "TAP-123",
            "run-page-worklog",
            "分页回读",
            "耗时包括实现与测试，不包括等待。",
            300,
            "2026-08-13T13:00:00.000+0800",
            True,
            agentic_run_id="run-1",
            included_work=[{"description": "完成实现与测试", "seconds": 300}],
            excluded_waiting_categories=["等待 Jira 回读"],
        )
        applied = self.service.apply_worklog(plan, plan.plan_id, begin_create=self._begin_create)
        marker = self.transport.worklogs.pop()
        self.transport.worklogs = [
            {
                "id": str(index + 1),
                "comment": markdown_to_adf(f"历史工时 {index}"),
                "timeSpentSeconds": 60,
                "started": "2026-08-13T12:00:00.000+0800",
            }
            for index in range(120)
        ] + [marker]
        readback = self.service.readback_worklog(
            plan, attempt=self._begin_create(plan)
        )
        self.assertEqual(applied["external_id"], readback["external_id"])
        self.assertTrue(any(query.get("startAt") == "100" for query in self.transport.queries))


class JiraRedirectSecurityTest(unittest.TestCase):
    def test_transport_rejects_cross_origin_redirect_without_second_authorized_request(self) -> None:
        location = "https://attacker.example.test/steal"

        class RedirectingOpener:
            def __init__(self) -> None:
                self.requests: list[Any] = []

            def open(self, request: Any, timeout: float) -> Any:
                self.requests.append(request)
                raise urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "Found",
                    {"Location": location},
                    io.BytesIO(b""),
                )

        opener = RedirectingOpener()
        connection = JiraConnection(
            connection_id="test",
            base_url="https://jira.example.test",
            email_env="EMAIL",
            token_env="TOKEN",
        )
        with mock.patch("urllib.request.build_opener", return_value=opener):
            transport = UrllibJiraTransport(connection, "user@example.test", "secret")
        response = transport.request("GET", "/rest/api/3/myself")
        self.assertEqual(302, response.status)
        self.assertEqual(1, len(opener.requests))
        self.assertEqual("jira.example.test", opener.requests[0].host)
        self.assertIn("Authorization", opener.requests[0].headers)
        self.assertNotEqual(location, opener.requests[0].full_url)


class JiraSearchJqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeTransport()
        self.client = JiraClient(profile(), self.transport)

    def _issue(
        self,
        key: str = "TAP-1",
        *,
        summary: str = "任务一",
        status: str = "打开",
        issue_type: str = "任务",
        priority: str = "Highest",
        updated: str = "2026-08-19T01:00:00.000+0800",
    ) -> dict[str, Any]:
        return {
            "id": "1000",
            "key": key,
            "fields": {
                "project": {"key": "TAP"},
                "summary": summary,
                "status": {"name": status},
                "issuetype": {"name": issue_type},
                "assignee": {"accountId": "owner-1"},
                "priority": {"name": priority},
                "updated": updated,
            },
        }

    def test_search_jql_returns_parsed_issues_with_total(self) -> None:
        self.transport.search_issues = [self._issue("TAP-1"), self._issue("TAP-2", summary="任务二")]
        self.transport.search_total = 7
        result = self.client.search_jql("project = TAP", max_results=50)
        self.assertEqual(7, result.total)
        self.assertEqual(2, len(result.issues))
        first = result.issues[0]
        self.assertEqual("TAP-1", first.key)
        self.assertEqual("任务一", first.summary)
        self.assertEqual("打开", first.status)
        self.assertEqual("Highest", first.priority)
        self.assertEqual("2026-08-19T01:00:00.000+0800", first.updated)
        self.assertEqual("TAP", first.project_key)
        # 走 /rest/api/3/search/jql 端点（tapdata /search 已移除）。
        self.assertEqual(("GET", "/rest/api/3/search/jql"), self.transport.requests[0])

    def test_search_jql_uses_profile_fields_and_jql_query(self) -> None:
        self.transport.search_issues = [self._issue()]
        self.client.search_jql("assignee = currentUser()", max_results=20)
        query = self.transport.queries[-1]
        self.assertEqual("assignee = currentUser()", query["jql"])
        self.assertEqual("20", query["maxResults"])
        self.assertIn("priority", query["fields"])
        self.assertIn("updated", query["fields"])

    def test_search_jql_total_missing_falls_back_to_issue_count(self) -> None:
        self.transport.search_issues = [self._issue("TAP-1"), self._issue("TAP-2")]
        self.transport.search_total = None
        result = self.client.search_jql("project = TAP")
        self.assertEqual(2, result.total)
        self.assertEqual(2, len(result.issues))

    def test_search_jql_empty_result(self) -> None:
        result = self.client.search_jql("project = NOPE")
        self.assertEqual(0, result.total)
        self.assertEqual([], result.issues)


class WithForcedOrderTest(unittest.TestCase):
    def test_strips_profile_order_by_and_appends_forced_order(self) -> None:
        from ao_work.jira.cli import _with_forced_order

        self.assertEqual(
            "project = TAP ORDER BY priority DESC, updated ASC",
            _with_forced_order(
                "project = TAP ORDER BY updated DESC",
                "priority DESC, updated ASC",
            ),
        )

    def test_appends_order_when_profile_has_none(self) -> None:
        from ao_work.jira.cli import _with_forced_order

        self.assertEqual(
            "assignee = currentUser() ORDER BY priority DESC",
            _with_forced_order("assignee = currentUser()", "priority DESC"),
        )
