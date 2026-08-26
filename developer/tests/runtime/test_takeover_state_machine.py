from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work.output import RuntimeErrorResult
from ao_work.task_state import (
    TaskIdentity,
    TaskStore,
    validate_takeover_event,
    validate_takeover_operation,
)
from ao_work.task_state.takeover import normalized_comment_content_sha256


IDENTITY = TaskIdentity(
    connection_id="tapdata-cloud",
    jira_issue_id="123",
    issue_key="TAP-123",
    project_key="TAP",
    agentic_run_id="run-TAP-123-abc123",
)
HASH_A = hashlib.sha256(b"a").hexdigest()
HASH_B = hashlib.sha256(b"b").hexdigest()
AUTHORIZATION_DIGEST = hashlib.sha256(b"user-confirmation:TAP-123").hexdigest()
COMMENT_MARKER = "[agentic-ops-takeover:TAP-123:run-TAP-123-abc123]"


class TakeoverStateMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = TaskStore(self.root)
        self.store.initialize(IDENTITY)

    def persist(self, takeover_kind: str = "new_takeover") -> dict:
        return self.store.persist_takeover_intent(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            agent_id="developer-agent",
            takeover_kind=takeover_kind,
            authorization_digest=AUTHORIZATION_DIGEST,
            preflight_facts_sha256=HASH_A,
            jira_status_before="待办",
            jira_status_target="正在进行",
            transition_id="31",
            comment_marker=COMMENT_MARKER,
            comment_content_sha256=HASH_B,
            planned_at="2026-08-21T00:00:00Z",
        )

    def verify_comment(self, operation_id: str) -> dict:
        return self.store.verify_takeover_comment(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            operation_id,
            comment_id="46799",
            comment_author="jira-account-1",
            expected_author="jira-account-1",
            comment_marker=COMMENT_MARKER,
            comment_content_sha256=HASH_B,
        )

    def verify_status(self, operation_id: str) -> dict:
        return self.store.verify_takeover_status(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            operation_id,
            status_after="正在进行",
            transition_applied=True,
        )

    def test_full_lifecycle_separates_phase_from_business_stage(self) -> None:
        intent = self.persist()
        operation_id = intent["operation"]["operation_id"]
        intent_next_action = intent["operation"]["agentic_next_action"]
        self.assertEqual("takeover_task", intent_next_action["operation_id"])
        self.assertEqual(["takeover", "TAP-123"], intent_next_action["command_argv"])
        self.assertEqual(
            "ao-work takeover TAP-123", intent_next_action["command_line"]
        )
        self.assertEqual({"issue_key": "TAP-123"}, intent_next_action["bound_arguments"])
        self.assertEqual([], intent_next_action["input_artifacts"])
        progress_path = self.task_dir / "progress.json"
        self.assertEqual("initialized", self.read(progress_path)["stage"])

        comment = self.verify_comment(operation_id)
        self.assertEqual("comment_verified", comment["operation"]["phase"])
        self.assertEqual(
            ["takeover", "TAP-123"],
            comment["operation"]["agentic_next_action"]["command_argv"],
        )
        self.assertEqual("initialized", self.read(progress_path)["stage"])

        status = self.verify_status(operation_id)
        self.assertEqual("status_verified", status["operation"]["phase"])
        self.assertEqual(
            ["takeover", "TAP-123"],
            status["operation"]["agentic_next_action"]["command_argv"],
        )
        self.assertEqual("initialized", self.read(progress_path)["stage"])

        completed = self.store.finalize_takeover(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            operation_id,
        )
        self.assertEqual("local_finalized", completed["operation"]["phase"])
        self.assertEqual("completed", completed["operation"]["result"])
        self.assertEqual("takeover_started", self.read(progress_path)["stage"])
        recovery = self.store.read_takeover_recovery(IDENTITY.issue_key)
        self.assertTrue(recovery["state_consistent"])
        self.assertTrue(Path(recovery["state_file"]).is_file())

        next_action = completed["operation"]["agentic_next_action"]
        self.assertEqual("repository_branch_assess", next_action["operation_id"])
        self.assertEqual(
            ["task", "repositories", "assess", "--issue-key", "TAP-123"],
            next_action["command_argv"],
        )
        self.assertEqual(
            "ao-work task repositories assess --issue-key TAP-123",
            next_action["command_line"],
        )
        self.assertEqual({"issue_key": "TAP-123"}, next_action["bound_arguments"])
        self.assertEqual(["repository_branch_assess"], next_action["allowed_operations"])

    def test_completed_takeover_remains_consistent_after_later_gate(self) -> None:
        intent = self.persist()
        operation_id = intent["operation"]["operation_id"]
        self.verify_comment(operation_id)
        self.verify_status(operation_id)
        self.store.finalize_takeover(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            operation_id,
        )
        self.store.record_gate_transition(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            stage="task_run_manifest_review",
            next_action="review_task_run_authorization",
            operation="task_run_prepare",
            status="completed",
            evidence={"draft_digest": HASH_A},
        )

        recovery = self.store.read_takeover_recovery(IDENTITY.issue_key)

        self.assertTrue(recovery["state_consistent"])
        self.assertEqual("local_finalized", recovery["operation"]["phase"])
        self.assertEqual("completed", recovery["operation"]["result"])

    def test_three_takeover_kinds_validate_and_non_new_is_explicit(self) -> None:
        for kind in (
            "new_takeover",
            "accept_existing_task",
            "resume_takeover",
        ):
            with self.subTest(kind=kind):
                with tempfile.TemporaryDirectory() as temporary:
                    store = TaskStore(Path(temporary))
                    store.initialize(IDENTITY)
                    result = store.persist_takeover_intent(
                        IDENTITY.issue_key,
                        IDENTITY.agentic_run_id,
                        agent_id="developer-agent",
                        takeover_kind=kind,
                        authorization_digest=AUTHORIZATION_DIGEST,
                        preflight_facts_sha256=HASH_A,
                        jira_status_before="待办",
                        jira_status_target="正在进行",
                        transition_id="31",
                        comment_marker=COMMENT_MARKER,
                        comment_content_sha256=HASH_B,
                    )
                    operation = validate_takeover_operation(result["operation"])
                    if kind == "new_takeover":
                        self.assertNotIn("不是新接管", operation["human_notice"])
                    else:
                        self.assertIn("不是新接管", operation["human_notice"])

    def test_intent_is_idempotent_and_conflict_fails_closed(self) -> None:
        first = self.persist()
        repeated = self.persist()
        self.assertTrue(first["created"])
        self.assertFalse(repeated["created"])
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.persist_takeover_intent(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                agent_id="developer-agent",
                takeover_kind="new_takeover",
                authorization_digest=AUTHORIZATION_DIGEST,
                preflight_facts_sha256=HASH_A,
                jira_status_before="待办",
                jira_status_target="已完成",
                transition_id="41",
                comment_marker=COMMENT_MARKER,
                comment_content_sha256=HASH_B,
            )
        self.assertEqual("takeover_intent_conflict", captured.exception.code)

    def test_intent_retry_reuses_original_planned_at(self) -> None:
        first = self.store.persist_takeover_intent(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            agent_id="developer-agent",
            takeover_kind="new_takeover",
            authorization_digest=AUTHORIZATION_DIGEST,
            preflight_facts_sha256=HASH_A,
            jira_status_before="待办",
            jira_status_target="正在进行",
            transition_id="31",
            comment_marker=COMMENT_MARKER,
            comment_content_sha256=HASH_B,
        )
        repeated = self.store.persist_takeover_intent(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            agent_id="developer-agent",
            takeover_kind="new_takeover",
            authorization_digest=AUTHORIZATION_DIGEST,
            preflight_facts_sha256=HASH_A,
            jira_status_before="待办",
            jira_status_target="正在进行",
            transition_id="31",
            comment_marker=COMMENT_MARKER,
            comment_content_sha256=HASH_B,
        )
        self.assertFalse(repeated["created"])
        self.assertEqual(
            first["operation"]["planned_at"],
            repeated["operation"]["planned_at"],
        )

    def test_comment_markdown_is_immutable_and_digest_bound(self) -> None:
        markdown = "## 接管\n\n稳定正文"
        digest = normalized_comment_content_sha256(markdown)
        first = self.store.persist_takeover_intent(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            agent_id="developer-agent",
            takeover_kind="new_takeover",
            authorization_digest=AUTHORIZATION_DIGEST,
            preflight_facts_sha256=HASH_A,
            jira_status_before="待办",
            jira_status_target="正在进行",
            transition_id="31",
            comment_marker=COMMENT_MARKER,
            comment_content_sha256=digest,
            comment_markdown=markdown,
        )
        self.assertEqual(markdown, first["operation"]["comment_markdown"])
        changed = "被修改的正文"
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.persist_takeover_intent(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                agent_id="developer-agent",
                takeover_kind="new_takeover",
                authorization_digest=AUTHORIZATION_DIGEST,
                preflight_facts_sha256=HASH_A,
                jira_status_before="待办",
                jira_status_target="正在进行",
                transition_id="31",
                comment_marker=COMMENT_MARKER,
                comment_content_sha256=normalized_comment_content_sha256(changed),
                comment_markdown=changed,
            )
        self.assertEqual("takeover_intent_conflict", captured.exception.code)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.persist_takeover_intent(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                agent_id="developer-agent",
                takeover_kind="new_takeover",
                authorization_digest=AUTHORIZATION_DIGEST,
                preflight_facts_sha256=HASH_A,
                jira_status_before="待办",
                jira_status_target="正在进行",
                transition_id="31",
                comment_marker=COMMENT_MARKER,
                comment_content_sha256=digest,
                comment_markdown=changed,
            )
        self.assertEqual("takeover_schema_invalid", captured.exception.code)

    def test_illegal_phase_skip_and_comment_conflict_are_blocked(self) -> None:
        intent = self.persist()
        operation_id = intent["operation"]["operation_id"]
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.verify_status(operation_id)
        self.assertEqual("takeover_phase_transition_invalid", captured.exception.code)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.verify_takeover_comment(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                operation_id,
                comment_id="46799",
                comment_author="foreign-account",
                expected_author="jira-account-1",
                comment_marker=COMMENT_MARKER,
                comment_content_sha256=HASH_B,
            )
        self.assertEqual("takeover_comment_evidence_conflict", captured.exception.code)

    def test_uncertain_result_is_not_retry_safe_or_completed(self) -> None:
        intent = self.persist()
        result = self.store.mark_takeover_uncertain(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            intent["operation"]["operation_id"],
            failure_code="jira_comment_result_uncertain",
            recovery_action="readback_takeover_comment",
        )
        operation = result["operation"]
        self.assertEqual("uncertain", operation["result"])
        self.assertFalse(operation["retry_safe"])
        self.assertNotEqual("completed", operation["takeover_status"])
        self.assertEqual("initialized", self.read(self.task_dir / "progress.json")["stage"])

    def test_risk_waiting_is_blocked_without_advancing_business_stage(self) -> None:
        intent = self.persist("accept_existing_task")
        result = self.store.block_takeover(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            intent["operation"]["operation_id"],
            failure_code="takeover_external_fact_conflict",
            recovery_action="review_takeover_risk",
        )
        operation = validate_takeover_operation(result["operation"])
        self.assertEqual("blocked", operation["result"])
        self.assertIn("不是新接管", operation["human_notice"])
        self.assertTrue(operation["agentic_next_action"]["stop_workflow"])
        self.assertEqual("initialized", self.read(self.task_dir / "progress.json")["stage"])

    def test_local_finalize_interruption_is_detected_and_recovered(self) -> None:
        intent = self.persist()
        operation_id = intent["operation"]["operation_id"]
        self.verify_comment(operation_id)
        self.verify_status(operation_id)
        with mock.patch(
            "ao_work.task_state.store.append_ndjson",
            side_effect=OSError("simulated journal failure"),
        ):
            with self.assertRaises(OSError):
                self.store.finalize_takeover(
                    IDENTITY.issue_key,
                    IDENTITY.agentic_run_id,
                    operation_id,
                )
        recovery = self.store.read_takeover_recovery(IDENTITY.issue_key)
        self.assertFalse(recovery["state_consistent"])
        self.assertEqual("uncertain", recovery["operation"]["result"])
        self.assertEqual(
            "recover_local_takeover_state",
            recovery["operation"]["recovery_action"],
        )

        recovered = self.store.finalize_takeover(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            operation_id,
        )
        self.assertTrue(recovered["state_consistent"])
        self.assertEqual(
            "takeover_recovered",
            recovered["event"]["operation"],
        )

    def test_checkpoint_event_interruption_is_detected_and_recovered(self) -> None:
        intent = self.persist()
        operation_id = intent["operation"]["operation_id"]
        with mock.patch(
            "ao_work.task_state.store.append_ndjson",
            side_effect=OSError("simulated journal failure"),
        ):
            with self.assertRaises(OSError):
                self.verify_comment(operation_id)
        recovery = self.store.read_takeover_recovery(IDENTITY.issue_key)
        self.assertFalse(recovery["state_consistent"])
        self.assertEqual("comment_verified", recovery["operation"]["phase"])
        self.assertEqual("uncertain", recovery["operation"]["result"])

        repaired = self.verify_comment(operation_id)
        self.assertTrue(repaired["created"])
        self.assertEqual("takeover_recovered", repaired["event"]["operation"])
        recovery = self.store.read_takeover_recovery(IDENTITY.issue_key)
        self.assertTrue(recovery["state_consistent"])

    def test_legacy_migration_succeeds_with_verified_facts(self) -> None:
        self.seed_legacy_state()
        result = self.store.migrate_legacy_takeover(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            self.legacy_evidence(),
        )
        self.assertEqual("local_finalized", result["operation"]["phase"])
        self.assertEqual("takeover_recovered", result["event"]["operation"])
        inspected = self.store.inspect(IDENTITY.issue_key)
        self.assertEqual(result["operation"], inspected["takeover_recovery"]["operation"])

    def test_legacy_migration_failure_preserves_original_files(self) -> None:
        self.seed_legacy_state()
        before = {
            path.name: path.read_bytes()
            for path in (
                self.task_dir / "progress.json",
                self.task_dir / "sync.json",
                self.task_dir / "journal.ndjson",
            )
        }
        evidence = self.legacy_evidence()
        evidence["assignee"] = "other-account"
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.migrate_legacy_takeover(
                IDENTITY.issue_key,
                IDENTITY.agentic_run_id,
                evidence,
            )
        self.assertEqual("takeover_legacy_state_unverified", captured.exception.code)
        after = {
            path.name: path.read_bytes()
            for path in (
                self.task_dir / "progress.json",
                self.task_dir / "sync.json",
                self.task_dir / "journal.ndjson",
            )
        }
        self.assertEqual(before, after)

    def test_legacy_migration_local_event_failure_is_recoverable(self) -> None:
        self.seed_legacy_state()
        evidence = self.legacy_evidence()
        with mock.patch(
            "ao_work.task_state.store.append_ndjson",
            side_effect=OSError("simulated migration event failure"),
        ):
            with self.assertRaises(OSError):
                self.store.migrate_legacy_takeover(
                    IDENTITY.issue_key,
                    IDENTITY.agentic_run_id,
                    evidence,
                )
        recovery = self.store.read_takeover_recovery(IDENTITY.issue_key)
        self.assertFalse(recovery["state_consistent"])
        recovered = self.store.migrate_legacy_takeover(
            IDENTITY.issue_key,
            IDENTITY.agentic_run_id,
            evidence,
        )
        self.assertTrue(recovered["state_consistent"])
        self.assertEqual("takeover_recovered", recovered["event"]["operation"])

    def test_event_schema_rejects_unknown_event(self) -> None:
        intent = self.persist()
        event = dict(intent["event"])
        event["operation"] = "takeover_unknown"
        with self.assertRaises(RuntimeErrorResult) as captured:
            validate_takeover_event(event)
        self.assertEqual("takeover_schema_invalid", captured.exception.code)

    @property
    def task_dir(self) -> Path:
        return self.root / ".agentic-ops" / "tasks" / IDENTITY.issue_key

    @staticmethod
    def read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def seed_legacy_state(self) -> None:
        progress_path = self.task_dir / "progress.json"
        progress = self.read(progress_path)
        progress["stage"] = "takeover_started"
        progress["agentic_next_action"] = "assess_task_intake"
        progress_path.write_text(json.dumps(progress), encoding="utf-8")
        event = {
            "schema_version": "1",
            "issue_key": IDENTITY.issue_key,
            "agentic_run_id": IDENTITY.agentic_run_id,
            "updated_at": "2026-08-20T00:00:00Z",
            "content_version": 1,
            "operation": "takeover_task",
            "status": "completed",
            "code": None,
            "retry_safe": True,
            "evidence": {
                "agent_id": "developer-agent",
                "takeover_kind": "new_takeover",
                "takeover_comment_id": "46799",
                "takeover_comment_marker": COMMENT_MARKER,
                "jira_status_before": "待办",
                "jira_status_after": "正在进行",
            },
        }
        with (self.task_dir / "journal.ndjson").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    @staticmethod
    def legacy_evidence() -> dict:
        return {
            "agent_id": "developer-agent",
            "takeover_kind": "new_takeover",
            "authorization_digest": AUTHORIZATION_DIGEST,
            "preflight_facts_sha256": HASH_A,
            "jira_status_before": "待办",
            "jira_status_target": "正在进行",
            "jira_status_after": "正在进行",
            "transition_id": "31",
            "comment_marker": COMMENT_MARKER,
            "comment_content_sha256": HASH_B,
            "comment_id": "46799",
            "comment_author": "jira-account-1",
            "expected_comment_author": "jira-account-1",
            "assignee": "jira-account-1",
            "expected_assignee": "jira-account-1",
        }


if __name__ == "__main__":
    unittest.main()
