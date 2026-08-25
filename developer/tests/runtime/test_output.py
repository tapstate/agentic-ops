from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from ao_work.output import RuntimeErrorResult, failure, success, write_json


class OutputTest(unittest.TestCase):
    def test_success_has_stable_envelope(self) -> None:
        result = success("workspace_inspect", value="ok")
        self.assertEqual(True, result["ok"])
        self.assertEqual("completed", result["status"])
        self.assertEqual(True, result["retry_safe"])
        self.assertEqual("ok", result["value"])
        next_action = result["agentic_next_action"]
        self.assertEqual("ai", next_action["executor"])
        self.assertEqual("takeover_explicit_jira_task", next_action["action"])
        self.assertEqual("takeover", next_action["operation_id"])
        self.assertEqual(["takeover", "<issue-key>"], next_action["command_argv"])
        self.assertEqual("ao-work takeover <issue-key>", next_action["command_line"])
        self.assertEqual(["issue_key"], next_action["required_inputs"])
        self.assertEqual(["takeover"], next_action["allowed_operations"])
        self.assertEqual(
            [{"kind": "issue_key", "source": "user_input.issue_key"}],
            next_action["input_artifacts"],
        )

    def test_success_promotes_legacy_next_action_to_reason(self) -> None:
        result = success(
            "task_start",
            agentic_next_action="请先审查 AI 提议的实施计划",
        )
        next_action = result["agentic_next_action"]
        self.assertEqual("ai", next_action["executor"])
        self.assertEqual("assess_task_intake", next_action["action"])
        self.assertEqual(False, next_action["requires_authorization"])
        self.assertEqual("请先审查 AI 提议的实施计划", next_action["reason"])

    def test_workspace_preflight_routes_to_top_level_takeover(self) -> None:
        next_action = success("workspace_preflight")["agentic_next_action"]

        self.assertEqual("takeover_explicit_jira_task", next_action["action"])
        self.assertEqual(["issue_key"], next_action["required_inputs"])
        self.assertEqual(["takeover"], next_action["allowed_operations"])

    def test_ci_timeout_routes_to_analysis_and_user_decision(self) -> None:
        next_action = success(
            "task-run_probe-ci",
            ci_status="completion_timeout",
            pr_url="https://github.com/example/repo/pull/1",
            required_checks=[],
            workflow_runs=[],
        )["agentic_next_action"]
        self.assertEqual("ai", next_action["executor"])
        self.assertEqual("analyze_ci_timeout_and_request_user_decision", next_action["action"])
        self.assertTrue(next_action["requires_authorization"])
        self.assertTrue(next_action["stop_workflow"])

    def test_ci_report_routes_to_explicit_remediation_decision(self) -> None:
        next_action = success("task-run_parse-ci-report")["agentic_next_action"]
        self.assertEqual("analyze_ci_failure_and_request_user_decision", next_action["action"])
        self.assertEqual(["task-run_authorize-ci-remediation"], next_action["allowed_operations"])
        self.assertTrue(next_action["requires_authorization"])

    def test_jira_inspect_does_not_route_to_internal_task_start(self) -> None:
        next_action = success("jira_inspect")["agentic_next_action"]

        self.assertEqual("takeover_verified_jira_task", next_action["action"])
        self.assertEqual(["takeover"], next_action["allowed_operations"])
        self.assertNotIn("task_start", next_action["allowed_operations"])

    def test_failure_has_chinese_human_action(self) -> None:
        result = failure(
            "task_init",
            RuntimeErrorResult(
                code="test_blocked",
                message="测试阻断",
                status="blocked",
                exit_code=2,
                required_human_action="请检查测试输入",
            ),
        )
        self.assertEqual("test_blocked", result["code"])
        self.assertEqual("请检查测试输入", result["required_human_action"])
        next_action = result["agentic_next_action"]
        self.assertEqual("human", next_action["executor"])
        self.assertEqual("resolve_runtime_blocker", next_action["action"])
        self.assertEqual("human_decision", next_action["operation_id"])
        self.assertEqual([], next_action["command_argv"])
        self.assertEqual("请检查测试输入", next_action["reason"])
        self.assertEqual("escalate_to_human", next_action["retry_gate"]["on_exhausted"])

    def test_retry_safe_failure_allows_one_changed_input_retry(self) -> None:
        result = failure(
            "workspace_preflight",
            RuntimeErrorResult(
                code="temporary_state_conflict",
                message="状态刚刚发生变化",
                status="blocked",
                exit_code=2,
                retry_safe=True,
                required_human_action="请先回读状态并修正输入",
            ),
        )
        next_action = result["agentic_next_action"]
        self.assertEqual("ai", next_action["executor"])
        self.assertFalse(next_action["stop_workflow"])
        self.assertEqual(["workspace_preflight"], next_action["allowed_operations"])
        self.assertTrue(next_action["retry_gate"]["allowed"])
        self.assertEqual(1, next_action["retry_gate"]["max_additional_attempts"])
        self.assertFalse(next_action["retry_gate"]["same_input_allowed"])

    def test_failure_can_include_structured_gate_details(self) -> None:
        result = failure(
            "story_impact",
            RuntimeErrorResult(
                code="maintenance_story_impacted",
                message="故事受影响",
                status="blocked",
                exit_code=2,
                details={"impact_id": "abc", "impacted_story_ids": ["PM-007"]},
            ),
        )
        self.assertEqual("abc", result["impact_id"])
        self.assertEqual(["PM-007"], result["impacted_story_ids"])

    def test_failure_uses_operation_specific_next_action_when_provided(self) -> None:
        result = failure(
            "task_repositories_assess",
            RuntimeErrorResult(
                code="branch_alignment_failed",
                message="无法对齐",
                status="blocked",
                exit_code=2,
                required_human_action="请确认分支",
                agentic_next_action={
                    "executor": "human",
                    "action": "confirm_repository_branch_override",
                    "required_inputs": ["issue_key", "task_domain"],
                    "allowed_operations": ["jira_description_plan"],
                    "requires_authorization": True,
                    "stop_workflow": True,
                    "ownership_effect": "none",
                },
            ),
        )

        self.assertEqual(
            "confirm_repository_branch_override",
            result["agentic_next_action"]["action"],
        )
        self.assertEqual(
            ["jira_description_plan"],
            result["agentic_next_action"]["allowed_operations"],
        )
        self.assertEqual(
            ["jira", "description", "plan"],
            result["agentic_next_action"]["command_argv"],
        )

    def test_every_success_and_failure_next_action_has_actionable_envelope(self) -> None:
        cases = (
            success("task_start", issue_key="TAP-123", agentic_run_id="run-123"),
            success("task-run_probe-ci", ci_status="completion_timeout"),
            failure(
                "workspace_preflight",
                RuntimeErrorResult(
                    code="temporary", message="暂时失败", retry_safe=True
                ),
            ),
        )
        for result in cases:
            with self.subTest(operation=result["operation"]):
                next_action = result["agentic_next_action"]
                for field in (
                    "operation_id",
                    "command_argv",
                    "command_line",
                    "bound_arguments",
                    "input_artifacts",
                    "reason",
                ):
                    self.assertIn(field, next_action)

    def test_writer_outputs_exactly_one_json_object(self) -> None:
        stream = io.StringIO()
        with redirect_stdout(stream):
            write_json(success("test"))
        lines = stream.getvalue().splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual("test", json.loads(lines[0])["operation"])
