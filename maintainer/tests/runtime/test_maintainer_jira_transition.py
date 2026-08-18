from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from typing import Any

from ao_maint.jira.client import JiraClient, JiraConnection, TransportResponse
from ao_maint.jira.cli import configure_jira_parser
from ao_maint.jira.config import load_maintainer_workflow
from ao_maint.jira.service import MaintainerJiraService
from ao_maint.output import RuntimeErrorResult

CONNECTION = JiraConnection(
    connection_id="tapdata-cloud",
    base_url="https://tapdata.atlassian.net",
    email_env="TAPDATA_JIRA_EMAIL",
    token_env="TAPDATA_JIRA_API_TOKEN",
    timeout_seconds=20.0,
)

WORKFLOW = {
    "projects": {
        "AO": {
            "statuses": {
                "待办": "waiting_takeover",
                "正在进行": "implementation",
                "已完成": "completed",
            },
            "transitions": {
                "start_progress": {
                    "name": "In Progress",
                    "id": "31",
                    "from": ["待办"],
                    "to": "正在进行",
                },
                "complete": {
                    "name": "Done",
                    "id": "41",
                    "from": ["正在进行"],
                    "to": "已完成",
                },
            },
        }
    }
}

WORKFLOW_YAML = """schema_version: 1
connection_id: tapdata-cloud
projects:
  AO:
    statuses:
      待办: waiting_takeover
      正在进行: implementation
    transitions:
      start_progress:
        name: In Progress
        id: "31"
        from: [待办]
        to: 正在进行
"""


class FakeTransitionTransport:
    def __init__(
        self,
        initial_status: str = "待办",
        transitions: list[dict[str, str]] | None = None,
        post_result: str | None = None,
    ) -> None:
        self.requests: list[tuple[str, str]] = []
        self.issue_status = initial_status
        self.transitions = transitions or [
            {"id": "31", "name": "In Progress", "to": "正在进行"},
            {"id": "41", "name": "Done", "to": "已完成"},
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
            return TransportResponse(200, {"accountId": "user-1", "displayName": "维护者"})
        if path.startswith("/rest/api/3/issue/") and path.endswith("/transitions"):
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
                transition_id = (
                    (body or {}).get("transition", {}).get("id")
                )
                target = next(
                    (item for item in self.transitions if item["id"] == transition_id),
                    None,
                )
                if self.post_result is not None:
                    self.issue_status = self.post_result
                elif target:
                    self.issue_status = target["to"]
                return TransportResponse(204, None)
        if path.startswith("/rest/api/3/issue/"):
            if method == "GET":
                return TransportResponse(
                    200,
                    {
                        "id": "1",
                        "key": "AO-1",
                        "fields": {
                            "summary": "任务",
                            "status": {"name": self.issue_status},
                            "issuetype": {"name": "任务"},
                            "assignee": {"accountId": "user-1"},
                            "project": {"key": "AO"},
                            "description": None,
                        },
                    },
                )
        raise AssertionError(f"unexpected request: {method} {path}")


def _service(
    transport: FakeTransitionTransport,
) -> MaintainerJiraService:
    client = JiraClient(CONNECTION, transport)
    return MaintainerJiraService(client)


def _parse_cli(*args: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure_jira_parser(subparsers)
    return parser.parse_args(["jira", "transition", *args])


class MaintainerTransitionServiceTest(unittest.TestCase):
    def test_plan_transition_by_target_transition_key(self) -> None:
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_transition="start_progress",
        )
        self.assertEqual("jira_transition", plan.operation)
        self.assertEqual("待办", plan.payload["from_status"])
        self.assertEqual("正在进行", plan.payload["target_status"])
        self.assertEqual("31", plan.payload["transition_id"])
        self.assertEqual("In Progress", plan.payload["transition_name"])
        self.assertEqual("AO", plan.payload["project_key"])
        self.assertEqual("", plan.payload["comment"])

    def test_plan_transition_by_target_status(self) -> None:
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_status="正在进行",
        )
        self.assertEqual("31", plan.payload["transition_id"])

    def test_plan_transition_by_transition_id_without_mapping(self) -> None:
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow={"projects": {}},
            transition_id="31",
        )
        self.assertEqual("31", plan.payload["transition_id"])
        self.assertEqual("正在进行", plan.payload["target_status"])

    def test_plan_transition_requires_exactly_one_target(self) -> None:
        service = _service(FakeTransitionTransport())
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "AO-1",
                "k1",
                maintainer_run_id="maint-AO-1-abc123",
                workflow=WORKFLOW,
                target_status="正在进行",
                target_transition="start_progress",
            )
        self.assertEqual("invalid_transition_target", captured.exception.code)
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "AO-1",
                "k1",
                maintainer_run_id="maint-AO-1-abc123",
                workflow=WORKFLOW,
            )
        self.assertEqual("invalid_transition_target", captured.exception.code)

    def test_plan_transition_mapping_gap_includes_adaptation_material(self) -> None:
        service = _service(FakeTransitionTransport())
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "AO-1",
                "k1",
                maintainer_run_id="maint-AO-1-abc123",
                workflow=WORKFLOW,
                target_status="已完成",
            )
        error = captured.exception
        self.assertEqual("jira_transition_mapping_gap", error.code)
        self.assertEqual("待办", error.details["current_status"])
        self.assertEqual("AO", error.details["project_key"])
        self.assertEqual(
            [
                {"id": "31", "name": "In Progress", "to": "正在进行"},
                {"id": "41", "name": "Done", "to": "已完成"},
            ],
            error.details["available_transitions"],
        )
        self.assertIn("start_progress", error.details["configured_transitions"])
        self.assertIn("guidance", error.details)

    def test_plan_transition_comment_requires_chinese(self) -> None:
        service = _service(FakeTransitionTransport())
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "AO-1",
                "k1",
                maintainer_run_id="maint-AO-1-abc123",
                workflow=WORKFLOW,
                target_transition="start_progress",
                comment="english only comment",
            )
        self.assertEqual("chinese_content_required", captured.exception.code)

    def test_plan_transition_with_comment_computes_sha256(self) -> None:
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_transition="start_progress",
            comment="开始处理任务，进入执行状态",
        )
        self.assertTrue(plan.payload["comment"])
        self.assertRegex(plan.payload["body_sha256"], r"^[0-9a-f]{64}$")

    def test_d037_id_available_but_from_mismatch_blocks(self) -> None:
        # 配置了稳定 id，但当前状态不在 from 列表：阻断，不降级名称兜底
        workflow = {
            "projects": {
                "AO": {
                    "transitions": {
                        "start_progress": {
                            "name": "In Progress",
                            "id": "31",
                            "from": ["已完成"],
                            "to": "正在进行",
                        },
                    },
                }
            }
        }
        service = _service(FakeTransitionTransport(initial_status="待办"))
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "AO-1",
                "k1",
                maintainer_run_id="maint-AO-1-abc123",
                workflow=workflow,
                target_transition="start_progress",
            )
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)

    def test_d037_name_fallback_requires_unique_name(self) -> None:
        # 无 id：名称唯一 + from/to 匹配 → 兜底成功
        workflow = {
            "projects": {
                "AO": {
                    "transitions": {
                        "start_progress": {
                            "name": "In Progress",
                            "from": ["待办"],
                            "to": "正在进行",
                        },
                    },
                }
            }
        }
        service = _service(FakeTransitionTransport())
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=workflow,
            target_transition="start_progress",
        )
        self.assertEqual("31", plan.payload["transition_id"])
        # 名称重复（两个同名 transition）→ 阻断
        transport = FakeTransitionTransport(
            transitions=[
                {"id": "31", "name": "In Progress", "to": "正在进行"},
                {"id": "32", "name": "In Progress", "to": "已完成"},
            ]
        )
        service = _service(transport)
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.plan_transition(
                "AO-1",
                "k1",
                maintainer_run_id="maint-AO-1-abc123",
                workflow=workflow,
                target_transition="start_progress",
            )
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)

    def test_apply_transition_roundtrip(self) -> None:
        transport = FakeTransitionTransport()
        service = _service(transport)
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_transition="start_progress",
        )
        result = service.apply_transition(plan, plan.plan_id)
        self.assertTrue(result["created"])
        self.assertTrue(result["status_matched"])
        self.assertEqual("正在进行", result["current_status"])
        self.assertEqual("正在进行", transport.issue_status)
        readback = service.readback_transition(plan)
        self.assertTrue(readback["status_matched"])
        self.assertEqual("", readback["external_id"])

    def test_apply_transition_idempotent_when_target_reached(self) -> None:
        transport = FakeTransitionTransport(initial_status="正在进行")
        service = _service(transport)
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_transition="start_progress",
        )
        result = service.apply_transition(plan, plan.plan_id)
        self.assertFalse(result["created"])
        self.assertTrue(result["status_matched"])
        # 幂等成功不产生 POST 流转请求
        posts = [
            method for method, path in transport.requests
            if method == "POST" and path.endswith("/transitions")
        ]
        self.assertEqual([], posts)

    def test_apply_transition_precondition_changed_blocks(self) -> None:
        transport = FakeTransitionTransport(initial_status="待办")
        service = _service(transport)
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_transition="start_progress",
        )
        # 计划后、apply 前状态已变化（从 待办 变成 已完成）
        transport.issue_status = "已完成"
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_transition(plan, plan.plan_id)
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)
        self.assertIn("available_transitions", captured.exception.details)

    def test_apply_transition_available_changed_blocks(self) -> None:
        transport = FakeTransitionTransport()
        service = _service(transport)
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_transition="start_progress",
        )
        # apply 前 transition 列表变化（31 不再可用）
        transport.transitions = [
            {"id": "99", "name": "In Progress", "to": "正在进行"},
            {"id": "41", "name": "Done", "to": "已完成"},
        ]
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_transition(plan, plan.plan_id)
        self.assertEqual("jira_transition_mapping_gap", captured.exception.code)

    def test_apply_transition_readback_mismatch_blocks(self) -> None:
        service = _service(FakeTransitionTransport(post_result="已完成"))
        plan = service.plan_transition(
            "AO-1",
            "k1",
            maintainer_run_id="maint-AO-1-abc123",
            workflow=WORKFLOW,
            target_transition="start_progress",
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            service.apply_transition(plan, plan.plan_id)
        self.assertEqual("jira_transition_readback_mismatch", captured.exception.code)
        self.assertEqual("已完成", captured.exception.details["current_status"])


class MaintainerTransitionConfigTest(unittest.TestCase):
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
        (connections / "tapdata-cloud-workflow.yaml").write_text(
            WORKFLOW_YAML, encoding="utf-8"
        )

    def test_load_workflow_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_source(root)
            (root / "maintainer" / "standards" / "connections" / "tapdata-cloud-workflow.yaml").unlink()
            workflow = load_maintainer_workflow(root, "tapdata-cloud")
            self.assertEqual({}, workflow["projects"])

    def test_load_workflow_parses_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_source(root)
            workflow = load_maintainer_workflow(root, "tapdata-cloud")
            ao = workflow["projects"]["AO"]
            self.assertEqual("31", ao["transitions"]["start_progress"]["id"])
            self.assertEqual(["待办"], ao["transitions"]["start_progress"]["from"])
            self.assertEqual("waiting_takeover", ao["statuses"]["待办"])

    def test_load_workflow_invalid_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare_source(root)
            path = root / "maintainer" / "standards" / "connections" / "tapdata-cloud-workflow.yaml"
            path.write_text(
                "connection_id: tapdata-cloud\nprojects: not-a-mapping\n",
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_maintainer_workflow(root, "tapdata-cloud")
            self.assertEqual("jira_workflow_invalid", captured.exception.code)


class MaintainerTransitionCliTest(unittest.TestCase):
    def test_transition_apply_does_not_require_issue_key_argument(self) -> None:
        args = _parse_cli(
            "apply",
            "--plan-file",
            "x.json",
            "--confirm-plan-id",
            "plan-x",
            "--authorization-reference",
            "user-confirmation:AO-1:plan-x",
        )
        self.assertFalse(hasattr(args, "issue_key"))

    def test_transition_plan_requires_exactly_one_target(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_cli(
                "plan",
                "--issue-key",
                "AO-1",
                "--idempotency-key",
                "k1",
                "--plan-file",
                "x.json",
            )
        with self.assertRaises(SystemExit):
            _parse_cli(
                "plan",
                "--issue-key",
                "AO-1",
                "--idempotency-key",
                "k1",
                "--plan-file",
                "x.json",
                "--target-status",
                "正在进行",
                "--target-transition",
                "start_progress",
            )

    def test_transition_plan_accepts_each_target(self) -> None:
        for target in (
            ["--target-status", "正在进行"],
            ["--target-transition", "start_progress"],
            ["--transition-id", "31"],
        ):
            args = _parse_cli(
                "plan",
                "--issue-key",
                "AO-1",
                "--idempotency-key",
                "k1",
                "--plan-file",
                "x.json",
                *target,
            )
            self.assertIsNotNone(args.plan_file)


if __name__ == "__main__":
    unittest.main()
