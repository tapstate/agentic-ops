from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ao_maint.cli import build_parser
from ao_maint.cli import main as maintainer_main
from ao_maint.jira.model import JiraIssue
from ao_maint.jira.service import WritePlan
from ao_maint.output import RuntimeErrorResult
from ao_maint.takeover.cli import (
    _confirm_pending_gate,
    _read_design,
    _record_design,
    _takeover,
    execute_takeover,
)
from ao_maint.takeover.state import (
    load_state,
    save_state,
    validate_work_authorization,
)


IDENTITY = {
    "agent_id": "maintainer-agent",
    "agent_type": "codex",
    "model": "test-model",
    "environment": "测试环境",
    "note": "",
}


class FakeTakeoverJira:
    def __init__(self, status: str = "待办", issue_type: str = "子任务") -> None:
        self.issue = JiraIssue(
            issue_id="41711",
            key="AO-45",
            project_key="AO",
            summary="建立 maintainer Jira 工具路由防错门禁",
            status=status,
            issue_type=issue_type,
            assignee="user-1",
            description=None,
            fields={},
        )
        self.actions: list[str] = []

    def inspect_issue(self, _issue_key: str) -> JiraIssue:
        return self.issue

    def plan_comment(
        self,
        issue_key: str,
        idempotency_key: str,
        category: str,
        content: str,
        *,
        maintainer_run_id: str,
        comment_template_schema: dict[str, object] | None = None,
    ) -> WritePlan:
        self.actions.append(f"plan_comment:{category}")
        return _plan(
            "jira_comment", issue_key, maintainer_run_id, idempotency_key, content
        )

    def apply_comment(self, plan: WritePlan, _plan_id: str) -> dict[str, object]:
        self.actions.append("apply_comment")
        return {"external_id": "comment-1", "created": True}

    def readback_comment(self, _plan: WritePlan) -> dict[str, object]:
        self.actions.append("readback_comment")
        return {"external_id": "comment-1", "created": True}

    def plan_transition(
        self,
        issue_key: str,
        idempotency_key: str,
        *,
        maintainer_run_id: str,
        workflow: dict[str, object],
        target_transition: str,
    ) -> WritePlan:
        self.actions.append(f"plan_transition:{target_transition}")
        return _plan(
            "jira_transition",
            issue_key,
            maintainer_run_id,
            idempotency_key,
            "正在进行",
        )

    def apply_transition(self, plan: WritePlan, _plan_id: str) -> dict[str, object]:
        self.actions.append("apply_transition")
        self.issue = JiraIssue(
            **{
                **self.issue.__dict__,
                "status": "执行中"
                if self.issue.issue_type == "Agentic 缺陷"
                else "正在进行",
            }
        )
        return {"current_status": "正在进行", "created": True}

    def readback_transition(self, _plan: WritePlan) -> dict[str, object]:
        self.actions.append("readback_transition")
        return {"current_status": self.issue.status, "created": True}


def _plan(
    operation: str,
    issue_key: str,
    run_id: str,
    idempotency_key: str,
    content: str,
) -> WritePlan:
    suffix = "comment" if operation == "jira_comment" else "transition"
    return WritePlan(
        operation=operation,
        issue_key=issue_key,
        maintainer_run_id=run_id,
        idempotency_key=idempotency_key,
        plan_id=f"plan-{suffix}",
        action="create_or_update",
        content_sha256="a" * 64,
        payload={"content": content},
    )


class MaintainerTakeoverTest(unittest.TestCase):
    def test_non_ao_takeover_blocks_before_identity_and_config(self) -> None:
        args = build_parser().parse_args(["takeover", "TAP-12289"])
        with (
            mock.patch(
                "ao_maint.takeover.cli.load_maintainer_identity"
            ) as load_identity,
            mock.patch(
                "ao_maint.takeover.cli.load_maintainer_jira_config"
            ) as load_config,
            self.assertRaises(RuntimeErrorResult) as captured,
        ):
            execute_takeover(args, Path("/unused"))
        self.assertEqual(
            "maintainer_jira_project_scope_mismatch", captured.exception.code
        )
        load_identity.assert_not_called()
        load_config.assert_not_called()

    def test_parser_exposes_single_takeover_entry(self) -> None:
        args = build_parser().parse_args(["takeover", "AO-45"])
        self.assertEqual("takeover", args.group)
        self.assertEqual("AO-45", args.issue_key)
        self.assertIsNone(args.design_file)
        self.assertIsNone(args.confirm)

    def test_nested_help_describes_takeover_instead_of_root_commands(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = maintainer_main(["takeover", "--help"])
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertIn("ao-maint takeover", payload["usage"])
        self.assertIn("--design-file", payload["usage"])

    def test_new_takeover_comments_before_transition_and_reads_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira()
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "develop"),
            ):
                result = _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual("new", result["mode"])
            self.assertEqual("正在进行", result["jira_status"])
            self.assertEqual(
                [
                    "plan_comment:progress",
                    "apply_comment",
                    "readback_comment",
                    "plan_transition:start_progress",
                    "apply_transition",
                    "readback_transition",
                ],
                jira.actions,
            )

    def test_new_takeover_uses_agentic_defect_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira("待接管", "Agentic 缺陷")
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "develop"),
            ):
                result = _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual("new", result["mode"])
            self.assertEqual("执行中", result["jira_status"])

    def test_resume_is_explicit_and_does_not_repeat_jira_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira("正在进行")
            state = self._state(root)
            save_state(root, state)
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "develop"),
            ):
                result = _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual("resume", result["mode"])
            self.assertIn("恢复", result["human_notice"])
            self.assertEqual("prepare_design_review", result["agentic_next_action"])
            self.assertEqual([], jira.actions)

    def test_resume_preserves_pending_adopt_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira("正在进行")
            state = self._state(root)
            state["mode"] = "adopt"
            state["pending_gate"] = "adopt"
            state["design_digest"] = "c" * 64
            save_state(root, state)
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "develop"),
            ):
                result = _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual("adopt", result["mode"])
            self.assertEqual("waiting_confirmation", result["takeover_status"])
            self.assertEqual(
                "confirm_takeover:" + "c" * 64,
                result["agentic_next_action"],
            )
            self.assertEqual([], jira.actions)

    def test_resume_active_authorization_continues_until_precommit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira("正在进行")
            state = self._state(root)
            state["authorization_status"] = "active"
            state["pending_gate"] = "precommit"
            state["design_digest"] = "b" * 64
            state["design_content"] = "已确认设计"
            save_state(root, state)
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "develop"),
            ):
                result = _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual(
                "implement_until_precommit_gate", result["agentic_next_action"]
            )
            self.assertEqual(
                "work-authorization:AO-45:maint-AO-45-test:" + "b" * 64,
                result["work_authorization"],
            )
            self.assertEqual([], jira.actions)

    def test_active_without_local_history_requires_adopt_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira("正在进行")
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "develop"),
            ):
                result = _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual("adopt", result["mode"])
            self.assertEqual("waiting_confirmation", result["takeover_status"])
            self.assertIn("接纳存量", result["human_notice"])

    def test_design_confirmation_activates_bounded_work_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            design = root / "design.md"
            design.write_text("实现接管路由，并在提交前停止。\n", encoding="utf-8")
            jira = FakeTakeoverJira("正在进行")
            save_state(root, self._state(root))
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "develop"),
            ):
                planned = _record_design(
                    root, jira, "AO-45", IDENTITY, str(design), "test"
                )
                waiting = _takeover(root, jira, "AO-45", IDENTITY, "test")
                confirmed = _confirm_pending_gate(
                    root,
                    jira,
                    "AO-45",
                    IDENTITY,
                    planned["design_digest"],
                    "test",
                )
            self.assertEqual("waiting_confirmation", waiting["takeover_status"])
            self.assertEqual(
                f"confirm_takeover:{planned['design_digest']}",
                waiting["agentic_next_action"],
            )
            self.assertEqual("active", confirmed["authorization_status"])
            self.assertTrue(
                confirmed["work_authorization"].startswith(
                    "work-authorization:AO-45:maint-AO-45-test:"
                )
            )
            self.assertIn("plan_comment:decision", jira.actions)

    def test_resume_rejects_changed_branch_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira("正在进行")
            save_state(root, self._state(root))
            with mock.patch(
                "ao_maint.takeover.cli.git_binding",
                return_value=(str(root), "different-branch"),
            ):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual(
                "maintainer_takeover_binding_changed", captured.exception.code
            )

    def test_existing_state_rejects_unknown_jira_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            jira = FakeTakeoverJira("已取消")
            save_state(root, self._state(root))
            with self.assertRaises(RuntimeErrorResult) as captured:
                _takeover(root, jira, "AO-45", IDENTITY, "test")
            self.assertEqual(
                "maintainer_takeover_status_unsupported", captured.exception.code
            )

    def test_design_file_must_remain_inside_source_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "source"
            root.mkdir()
            outside = base / "outside.md"
            outside.write_text("不应被读取\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                _read_design(root, str(outside))
            self.assertEqual(
                "maintainer_design_file_outside_source", captured.exception.code
            )

    def test_work_authorization_rejects_non_routine_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            state = self._state(root)
            state["authorization_status"] = "active"
            state["design_digest"] = "b" * 64
            state["design_content"] = "已确认设计"
            save_state(root, state)
            reference = (
                "work-authorization:AO-45:maint-AO-45-test:" + "b" * 64
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                validate_work_authorization(
                    root,
                    reference,
                    issue_key="AO-45",
                    operation="jira_description",
                )
            self.assertEqual(
                "jira_work_authorization_scope_forbidden", captured.exception.code
            )

    def test_work_authorization_accepts_bound_routine_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._prepare(root)
            state = self._state(root)
            state["authorization_status"] = "active"
            state["design_digest"] = "b" * 64
            state["design_content"] = "已确认设计"
            save_state(root, state)
            reference = (
                "work-authorization:AO-45:maint-AO-45-test:" + "b" * 64
            )
            with mock.patch(
                "ao_maint.takeover.state.git_binding",
                return_value=(str(root), "develop"),
            ):
                validate_work_authorization(
                    root,
                    reference,
                    issue_key="AO-45",
                    operation="jira_comment",
                )

    @staticmethod
    def _prepare(root: Path) -> None:
        workflow = root / "maintainer/standards/connections/test-workflow.yaml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text(
            """schema_version: 1
connection_id: test
projects:
  AO:
    statuses:
      待办: waiting_takeover
      正在进行: implementation
      已完成: completed
    transitions:
      start_progress:
        name: In Progress
        id: "31"
        from: [待办]
        to: 正在进行
    issue_types:
      Agentic 缺陷:
        statuses:
          待接管: waiting_takeover
          执行中: implementation
        transitions:
          start_progress:
            name: 接管任务
            id: "2"
            from: [待接管]
            to: 执行中
""",
            encoding="utf-8",
        )
        schema = root / "shared/standards/jira-comment-template.schema.json"
        schema.parent.mkdir(parents=True)
        schema.write_text('{"templates": {}}\n', encoding="utf-8")

    @staticmethod
    def _state(root: Path) -> dict[str, object]:
        return {
            "schema_version": 1,
            "issue_key": "AO-45",
            "jira_issue_id": "41711",
            "agent_id": "maintainer-agent",
            "run_id": "maint-AO-45-test",
            "mode": "new",
            "jira_status": "正在进行",
            "repository_root": str(root),
            "working_branch": "develop",
            "authorization_status": "pending",
            "pending_gate": "design_review",
            "design_digest": "",
            "design_content": "",
            "created_at": "2026-08-20T00:00:00+00:00",
            "updated_at": "2026-08-20T00:00:00+00:00",
        }


if __name__ == "__main__":
    unittest.main()
