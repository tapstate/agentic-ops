from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ao_work.output import RuntimeErrorResult
from ao_work.task_state import RepositoryConfirmationStore


class RepositoryConfirmationStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        (self.workspace / ".agentic-ops" / "tasks" / "TAP-123").mkdir(parents=True)
        self.store = RepositoryConfirmationStore(self.workspace)
        self.scope = {
            "content_version": 1,
            "issue_key": "TAP-123",
            "agentic_run_id": "run-TAP-123",
            "task_domain": "product",
            "problem_version": "develop",
            "problem_version_repository": "tapdata/tapdata",
            "problem_version_sha": "a" * 40,
            "proposed_repository_branch_map": [],
        }

    def test_confirmation_ids_are_independent_and_idempotently_consumed(self) -> None:
        first = self.store.create("TAP-123", "run-TAP-123", self.scope)
        second = self.store.create("TAP-123", "run-TAP-123", self.scope)

        self.assertNotEqual(first["confirmation_id"], second["confirmation_id"])
        consumed = self.store.consume(
            "TAP-123", "run-TAP-123", first["confirmation_id"], self.scope, "product"
        )
        repeated = self.store.consume(
            "TAP-123", "run-TAP-123", first["confirmation_id"], self.scope, "product"
        )

        self.assertEqual("consumed", consumed["status"])
        self.assertEqual(consumed, repeated)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.consume(
                "TAP-123", "run-TAP-123", first["confirmation_id"], self.scope, "assistant"
            )
        self.assertEqual("repository_confirmation_conflict", captured.exception.code)

    def test_scope_revision_change_supersedes_confirmation(self) -> None:
        confirmation = self.store.create("TAP-123", "run-TAP-123", self.scope)
        changed = {**self.scope, "content_version": 2}

        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.validate(
                "TAP-123", "run-TAP-123", confirmation["confirmation_id"], changed, "product"
            )

        self.assertEqual("repository_confirmation_superseded", captured.exception.code)

    def test_second_confirmation_cannot_consume_the_same_scope(self) -> None:
        first = self.store.create("TAP-123", "run-TAP-123", self.scope)
        second = self.store.create("TAP-123", "run-TAP-123", self.scope)
        self.store.consume(
            "TAP-123", "run-TAP-123", first["confirmation_id"], self.scope, "product"
        )

        with self.assertRaises(RuntimeErrorResult) as captured:
            self.store.consume(
                "TAP-123", "run-TAP-123", second["confirmation_id"], self.scope, "product"
            )

        self.assertEqual("repository_confirmation_already_consumed", captured.exception.code)

    def test_inspect_lists_auditable_records_and_reads_one_by_id(self) -> None:
        first = self.store.create("TAP-123", "run-TAP-123", self.scope)
        second = self.store.create("TAP-123", "run-older", self.scope)
        self.store.consume(
            "TAP-123", "run-TAP-123", first["confirmation_id"], self.scope, "product"
        )

        listed = self.store.inspect("TAP-123")

        self.assertEqual(2, listed["record_count"])
        self.assertEqual(
            {first["confirmation_id"], second["confirmation_id"]},
            {item["confirmation_id"] for item in listed["confirmation_records"]},
        )
        first_summary = next(
            item
            for item in listed["confirmation_records"]
            if item["confirmation_id"] == first["confirmation_id"]
        )
        self.assertEqual("consumed", first_summary["status"])
        self.assertEqual("product", first_summary["task_domain"])
        self.assertIn("状态为 consumed", first_summary["description"])
        self.assertNotIn("path", first_summary)

        inspected = self.store.inspect("TAP-123", second["confirmation_id"])

        self.assertEqual(second["confirmation_id"], inspected["confirmation"]["confirmation_id"])
        self.assertEqual("recorded", inspected["confirmation"]["status"])
        self.assertEqual("run-older", inspected["confirmation"]["agentic_run_id"])


if __name__ == "__main__":
    unittest.main()
