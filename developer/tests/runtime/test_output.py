from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from ao_work.output import Step, RuntimeErrorResult, failure, success, workflow_query, write_json


class OutputTest(unittest.TestCase):
    def test_success_has_stable_envelope(self) -> None:
        result = success("workspace_inspect", value="ok")
        self.assertEqual(True, result["ok"])
        self.assertEqual("completed", result["status"])
        self.assertEqual(True, result["retry_safe"])
        self.assertEqual("ok", result["value"])
        next_action = result["next_step"]
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
        self.assertEqual("input", next_action["kind"])
        self.assertEqual("manual", next_action["mode"])
        self.assertEqual("local", next_action["scope"])
        self.assertEqual("takeover", next_action["call"]["operation"])
        self.assertEqual("succeeded", result["result"]["status"])
        self.assertEqual("workspace_inspect 已完成", result["result"]["summary"])
        self.assertEqual("step-result/v2", result["schema_version"])

    def test_manual_decision_has_one_recommended_record_only_choice(self) -> None:
        next_step = success("task-run_parse-ci-report")["next_step"]

        self.assertEqual("decision", next_step["kind"])
        self.assertEqual("manual", next_step["mode"])
        self.assertEqual(1, sum(choice["recommended"] for choice in next_step["choices"]))
        self.assertEqual("record_only", next_step["submit"]["effect"])

    def test_ci_terminal_statuses_return_terminal_step(self) -> None:
        for status in ("passed", "not_required"):
            with self.subTest(status=status):
                next_step = success("task-run_probe-ci", ci_status=status)["next_step"]
                self.assertEqual("none", next_step["kind"])
                self.assertEqual("stop", next_step["executor"])
                self.assertTrue(next_step["stop_workflow"])
                self.assertIsNone(next_step["call"])

    def test_timed_auto_requires_runtime_resolution_facts(self) -> None:
        result = success(
            "test",
            next_step={
                "executor": "ao_work",
                "action": "wait_for_confirmation_window",
                "required_inputs": [],
                "allowed_operations": ["task_inspect"],
                "requires_authorization": False,
                "stop_workflow": False,
                "ownership_effect": "none",
                "kind": "decision",
                "mode": "timed_auto",
                "timed": {
                    "decision_id": "confirmation-window-1",
                    "deadline": "2026-08-26T12:00:00+08:00",
                    "default_choice": "continue",
                    "cancel_if": "fact_binding_changed",
                    "fact_bind": "run:run-123",
                    "policy": "policy:timed-confirmation-v1",
                },
                "transitions": {
                    "continue": {
                        "executor": "ao_work",
                        "action": "inspect_confirmed_task",
                        "required_inputs": [],
                        "allowed_operations": ["task_inspect"],
                        "requires_authorization": False,
                        "stop_workflow": False,
                        "ownership_effect": "none",
                        "kind": "action",
                        "mode": "auto",
                    }
                },
            },
        )

        self.assertEqual("timed_auto", result["next_step"]["mode"])
        self.assertEqual("continue", result["next_step"]["timed"]["default_choice"])
        self.assertTrue(Step.from_mapping(
            result["next_step"], operation="test", payload={}
        ).is_timed_auto)

    def test_step_exposes_common_auto_and_human_properties(self) -> None:
        automatic = Step.from_mapping(
            {
                "executor": "ao_work",
                "action": "refresh_verified_state",
                "required_inputs": [],
                "allowed_operations": ["task_inspect"],
                "requires_authorization": False,
                "stop_workflow": False,
                "ownership_effect": "none",
            },
            operation="task_inspect",
            payload={},
        )
        decision = Step.from_mapping(
            {
                "executor": "human",
                "action": "review_scope",
                "required_inputs": [],
                "allowed_operations": [],
                "requires_authorization": True,
                "stop_workflow": True,
                "ownership_effect": "none",
            },
            operation="task_solution_classify",
            payload={},
        )

        self.assertTrue(automatic.can_auto_execute)
        self.assertFalse(automatic.requires_human_input)
        self.assertEqual("task_inspect", automatic.call["operation"])
        self.assertFalse(decision.can_auto_execute)
        self.assertTrue(decision.requires_human_input)

    def test_uncertain_effect_cannot_auto_advance_flow(self) -> None:
        with self.assertRaisesRegex(ValueError, "不得自动推进业务流程"):
            success(
                "task-run_probe-ci",
                effects=[
                    {
                        "kind": "jira_write",
                        "target": "AO-103",
                        "state": "uncertain",
                        "evidence": "event-1",
                    }
                ],
            )

    def test_workflow_query_is_read_only_and_not_executable(self) -> None:
        query = workflow_query(
            "development-change-v2",
            current_step_id="review",
            steps=[
                {"id": "review", "label": "设计审查", "kind": "decision"},
                {"id": "delivery", "label": "交付", "kind": "action"},
            ],
        )

        self.assertFalse(query["executable"])
        self.assertEqual("workflow-query/v1", query["schema_version"])
        self.assertNotIn("call", query["steps"][0])

    def test_success_rejects_legacy_string_next_step(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是结构化 Step"):
            success(
                "task_start",
                next_step="请先审查 AI 提议的实施计划",
            )

    def test_workspace_preflight_routes_to_top_level_takeover(self) -> None:
        next_action = success("workspace_preflight")["next_step"]

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
        )["next_step"]
        self.assertEqual("ai", next_action["executor"])
        self.assertEqual("analyze_ci_timeout_and_request_user_decision", next_action["action"])
        self.assertTrue(next_action["requires_authorization"])
        self.assertTrue(next_action["stop_workflow"])

    def test_ci_report_routes_to_explicit_remediation_decision(self) -> None:
        next_action = success("task-run_parse-ci-report")["next_step"]
        self.assertEqual("analyze_ci_failure_and_request_user_decision", next_action["action"])
        self.assertEqual(["task-run_authorize-ci-remediation"], next_action["allowed_operations"])
        self.assertTrue(next_action["requires_authorization"])

    def test_jira_inspect_does_not_route_to_internal_task_start(self) -> None:
        next_action = success("jira_inspect")["next_step"]

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
        next_action = result["next_step"]
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
        next_action = result["next_step"]
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
                next_step={
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
            result["next_step"]["action"],
        )
        self.assertEqual(
            ["jira_description_plan"],
            result["next_step"]["allowed_operations"],
        )
        self.assertEqual(
            ["jira", "description", "plan"],
            result["next_step"]["command_argv"],
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
                next_action = result["next_step"]
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
