from __future__ import annotations

import argparse
import unittest
from typing import Any

from ao_work.config.model import JiraConnection, ProjectProfile
from ao_work.jira.cli import configure_jira_parser
from ao_work.jira.client import JiraClient, TransportResponse
from ao_work.jira.service import JiraService, WritePlan
from ao_work.output import RuntimeErrorResult

CONNECTION = JiraConnection(
    connection_id="tapdata-cloud",
    base_url="https://tapdata.atlassian.net",
    email_env="TAPDATA_JIRA_EMAIL",
    token_env="TAPDATA_JIRA_TOKEN",
    timeout_seconds=20.0,
)

PROFILE = ProjectProfile(
    profile_id="tapdata",
    connection_id="tapdata-cloud",
    project_key="TAP",
    task_query="",
    issue_types=("任务",),
    status_mapping={
        "打开": "waiting_takeover",
        "正在进行": "implementation",
        "Pull Request Submitted": "implementation",
        "完成": "completed",
        "Done": "completed",
    },
    transition_mapping={
        "start_progress": {
            "name": "Implementation started",
            "id": "91",
            "from": ["打开"],
            "to": "正在进行",
        },
    },
)


class FakeTransitionTransport:
    def __init__(
        self,
        initial_status: str = "打开",
        assignee: str = "jira-account-1",
        transitions: list[dict[str, Any]] | None = None,
        post_result: str | None = None,
    ) -> None:
        self.requests: list[tuple[str, str]] = []
        self.issue_status = initial_status
        self.assignee = assignee
        self.transitions = transitions or [
            {"id": "91", "name": "Implementation started", "to": "正在进行"},
            {"id": "291", "name": "PR Approved", "to": "Merged"},
        ]
        self.post_result = post_result

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> TransportResponse:
        self.requests.append((method, path))
        if path == "/rest/api/3/myself":
            return TransportResponse(
                200, {"accountId": "jira-account-1", "displayName": "Harsen Test Bot"}
            )
        if path == "/rest/api/3/field":
            return TransportResponse(200, [{"id": "summary", "name": "Summary"}])
        if path == "/rest/api/3/issue/TAP-100" and method == "GET":
            return TransportResponse(
                200,
                {
                    "id": "100",
                    "key": "TAP-100",
                    "fields": {
                        "project": {"key": "TAP"},
                        "summary": "任务",
                        "status": {"name": self.issue_status},
                        "issuetype": {"name": "任务"},
                        "assignee": {"accountId": self.assignee},
                        "description": None,
                    },
                },
            )
        if path == "/rest/api/3/issue/TAP-100/transitions":
            if method == "GET":
                return TransportResponse(
                    200,
                    {
                        "transitions": [
                            {
                                "id": item["id"],
                                "name": item["name"],
                                "to": {"name": item["to"]},
                            }
                            for item in self.transitions
                        ]
                    },
                )
            if method == "POST":
                transition_id = (body or {}).get("transition", {}).get("id")
                target = next(
                    (item for item in self.transitions if item["id"] == transition_id),
                    None,
                )
                if self.post_result is not None:
                    self.issue_status = self.post_result
                elif target:
                    self.issue_status = target["to"]
                return TransportResponse(204, None)
        raise AssertionError(f"unexpected request: {method} {path}")


def _service(transport: FakeTransitionTransport) -> JiraService:
    client = JiraClient(PROFILE, transport)
    return JiraService(PROFILE, client)


def _parse_cli(*args: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure_jira_parser(subparsers)
    return parser.parse_args(["jira", "transition", *args])


class JiraTransitionServiceTest(unittest.TestCase):
    def test_plan_transition_by_target_transition_key(self) -> None:
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        self.assertEqual("jira_transition", plan.operation)
        self.assertEqual("打开", plan.payload["from_status"])
        self.assertEqual("正在进行", plan.payload["target_status"])
        self.assertEqual("91", plan.payload["transition_id"])
        self.assertEqual("Implementation started", plan.payload["transition_name"])

    def test_plan_transition_by_target_status(self) -> None:
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_status="正在进行",
        )
        self.assertEqual("91", plan.payload["transition_id"])

    def test_plan_transition_requires_exactly_one_target(self) -> None:
        service = _service(FakeTransitionTransport())
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
                target_status="正在进行",
                target_transition="start_progress",
            )
        self.assertEqual("invalid_transition_target", captured.exception.code)
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
            )
        self.assertEqual("invalid_transition_target", captured.exception.code)

    def test_plan_transition_mapping_gap_includes_adaptation_material(self) -> None:
        service = _service(FakeTransitionTransport())
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
                target_status="Pull Request Submitted",
            )
        error = captured.exception
        self.assertEqual("jira_transition_mapping_gap", error.code)
        self.assertEqual("打开", error.details["current_status"])
        self.assertEqual("TAP", error.details["project_key"])
        self.assertIn("start_progress", error.details["configured_transitions"])
        self.assertIn("guidance", error.details)

    def test_plan_transition_completed_forbidden_by_default(self) -> None:
        service = _service(FakeTransitionTransport())
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
                target_status="完成",
            )
        error = captured.exception
        self.assertEqual("jira_transition_completed_forbidden", error.code)
        self.assertEqual("completed", error.details["completed_stage"])
        self.assertEqual("完成", error.details["target_status"])

    def test_plan_transition_comment_requires_chinese(self) -> None:
        service = _service(FakeTransitionTransport())
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
                target_transition="start_progress",
                comment="english only",
            )
        self.assertEqual("jira_visible_content_not_chinese", captured.exception.code)

    def test_plan_transition_owner_mismatch_blocks(self) -> None:
        service = _service(
            FakeTransitionTransport(assignee="someone-else")
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
                target_transition="start_progress",
            )
        self.assertEqual("jira_assignee_mismatch", captured.exception.code)

    def test_d037_id_available_but_from_mismatch_blocks(self) -> None:
        profile = ProjectProfile(
            profile_id="tapdata",
            connection_id="tapdata-cloud",
            project_key="TAP",
            task_query="",
            issue_types=("任务",),
            status_mapping=PROFILE.status_mapping,
            transition_mapping={
                "start_progress": {
                    "name": "Implementation started",
                    "id": "91",
                    "from": ["完成"],
                    "to": "正在进行",
                },
            },
        )
        client = JiraClient(profile, FakeTransitionTransport())
        service = JiraService(profile, client)
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
                target_transition="start_progress",
            )
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)

    def test_d037_name_fallback_requires_unique_name(self) -> None:
        profile = ProjectProfile(
            profile_id="tapdata",
            connection_id="tapdata-cloud",
            project_key="TAP",
            task_query="",
            issue_types=("任务",),
            status_mapping=PROFILE.status_mapping,
            transition_mapping={
                "start_progress": {
                    "name": "Implementation started",
                    "from": ["打开"],
                    "to": "正在进行",
                },
            },
        )
        client = JiraClient(profile, FakeTransitionTransport())
        service = JiraService(profile, client)
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        self.assertEqual("91", plan.payload["transition_id"])
        # 名称重复 → 阻断
        transport = FakeTransitionTransport(
            transitions=[
                {"id": "91", "name": "Implementation started", "to": "正在进行"},
                {"id": "92", "name": "Implementation started", "to": "完成"},
            ]
        )
        client = JiraClient(profile, transport)
        service = JiraService(profile, client)
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "TAP-100",
                "k1",
                agentic_run_id="run-TAP-100-abc123",
                target_transition="start_progress",
            )
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)

    def test_apply_transition_roundtrip(self) -> None:
        transport = FakeTransitionTransport()
        service = _service(transport)
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        result = service.apply_transition(plan, plan.plan_id)
        self.assertTrue(result["created"])
        self.assertTrue(result["status_matched"])
        self.assertEqual("正在进行", result["current_status"])
        self.assertEqual("91", result["external_id"])
        self.assertEqual("正在进行", transport.issue_status)
        readback = service.readback_transition(plan)
        self.assertTrue(readback["status_matched"])

    def test_apply_transition_idempotent_when_target_reached(self) -> None:
        transport = FakeTransitionTransport(initial_status="正在进行")
        service = _service(transport)
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        result = service.apply_transition(plan, plan.plan_id)
        self.assertFalse(result["created"])
        self.assertTrue(result["status_matched"])
        posts = [
            method for method, path in transport.requests
            if method == "POST" and path.endswith("/transitions")
        ]
        self.assertEqual([], posts)

    def test_apply_transition_precondition_changed_blocks(self) -> None:
        transport = FakeTransitionTransport()
        service = _service(transport)
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        transport.issue_status = "完成"
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_transition(plan, plan.plan_id)
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)
        self.assertIn("available_transitions", captured.exception.details)

    def test_apply_transition_available_changed_blocks(self) -> None:
        transport = FakeTransitionTransport()
        service = _service(transport)
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        transport.transitions = [
            {"id": "99", "name": "Implementation started", "to": "正在进行"},
        ]
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_transition(plan, plan.plan_id)
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)

    def test_apply_transition_readback_mismatch_blocks(self) -> None:
        service = _service(FakeTransitionTransport(post_result="完成"))
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_transition(plan, plan.plan_id)
        self.assertEqual("jira_transition_readback_mismatch", captured.exception.code)
        self.assertEqual("完成", captured.exception.details["current_status"])

    def test_validate_apply_rejects_invalid_transition_payload(self) -> None:
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "TAP-100",
            "k1",
            agentic_run_id="run-TAP-100-abc123",
            target_transition="start_progress",
        )
        tampered = plan.to_dict()
        tampered["payload"] = dict(plan.payload)
        del tampered["payload"]["target_status"]
        modified = WritePlan.from_dict(tampered)
        with self.assertRaises(RuntimeErrorResult):
            service.validate_apply(modified, modified.plan_id, "jira_transition")


class JiraTransitionCliTest(unittest.TestCase):
    def test_transition_apply_does_not_require_issue_key_argument(self) -> None:
        args = _parse_cli(
            "apply",
            "--plan-file",
            "x.json",
            "--confirm-plan-id",
            "plan-x",
            "--authorization-reference",
            "user-confirmation:TAP-100:run-1:plan-x",
        )
        self.assertFalse(hasattr(args, "issue_key"))

    def test_transition_plan_requires_exactly_one_target(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_cli(
                "plan",
                "--issue-key",
                "TAP-100",
                "--idempotency-key",
                "k1",
                "--plan-file",
                "x.json",
            )
        with self.assertRaises(SystemExit):
            _parse_cli(
                "plan",
                "--issue-key",
                "TAP-100",
                "--idempotency-key",
                "k1",
                "--plan-file",
                "x.json",
                "--target-status",
                "正在进行",
                "--target-transition",
                "start_progress",
            )

    def test_transition_plan_has_no_transition_id_option(self) -> None:
        # developer 面不允许 --transition-id（D-049：AIAgent 语义层只用状态名/映射 key）
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command", required=True)
        configure_jira_parser(subparsers)
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "jira",
                    "transition",
                    "plan",
                    "--issue-key",
                    "TAP-100",
                    "--idempotency-key",
                    "k1",
                    "--plan-file",
                    "x.json",
                    "--transition-id",
                    "91",
                ]
            )

    def test_transition_plan_accepts_each_target(self) -> None:
        for target in (
            ["--target-status", "正在进行"],
            ["--target-transition", "start_progress"],
        ):
            args = _parse_cli(
                "plan",
                "--issue-key",
                "TAP-100",
                "--idempotency-key",
                "k1",
                "--plan-file",
                "x.json",
                *target,
            )
            self.assertIsNotNone(args.plan_file)


if __name__ == "__main__":
    unittest.main()
