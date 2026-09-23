#!/usr/bin/env python3
"""单工位占用与操作恢复合同；仅使用临时目录。"""
import json
import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import task_store as store, station_operation as operation
from workflow import engineering_baseline, quality_contract
from gate import engine


class StationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.product = self.base / "product"
        self.product.mkdir()
        (self.base / ".agenticops").mkdir()
        store.initialize_current(self.base)
        store._write_json_atomic(self.base / ".agenticops/station.json", {
            "schema_version": 4, "product_root": str(self.product), "source_pool": str(self.base / "pool"), "station_id": "a" * 32,
            "project": "tapdata", "agents": ["codex"],
            "branch_identity": {"schema_version": 1, "git_name": "Test", "source": "git_global_user_name"},
        })

    def task(self):
        return {"issue_key": "TAP-123", "run_id": "run-abc12345", "task_class": "technical_task",
                "stage": "waiting_takeover", "outcome": "in_progress", "facts": {}, "history": [],
                "pending": None, "engineering_baseline": {"status": "resolving"},
                "task_repositories": {}, "terminal_proof": None, "archive_ref": None}

    def test_invalid_current_is_rejected_on_read_and_before_write(self):
        valid = self.task()
        invalid = [dict(valid, **{key: value}) for key, value in (
            ("stage", "done"), ("stage", []), ("outcome", "unknown"), ("task_class", ""),
            ("facts", []), ("history", {}), ("history", ["bad"]), ("pending", []),
            ("engineering_baseline", []), ("engineering_baseline", {"status": "unknown"}),
            ("task_repositories", []), ("task_repositories", {"owner/repo": "bad"}),
            ("terminal_proof", False), ("archive_ref", {}), ("source_prepared", 1),
            ("initial_runtime", {}), ("reset_baseline", []), ("extra", True),
            ("run_id", "TAP-124-69d8a1f0"), ("issue_key", "tap-123"), ("issue_key", "TAP-123\n"))]
        invalid += [{key: value for key, value in valid.items() if key != missing} for missing in valid]
        original = store.current_path(self.base).read_bytes()
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    store.compare_and_set(self.base, 0, value)
                self.assertEqual(original, store.current_path(self.base).read_bytes())
                envelope = {"schema_version": 1, "revision": 0, "current": value}
                store._write_json_atomic(store.current_path(self.base), envelope)
                corrupted = store.current_path(self.base).read_bytes()
                with self.assertRaises(ValueError):
                    store.read_current(self.base)
                self.assertIsNone(engine.current_task(self.base / ".agenticops"))
                self.assertEqual(corrupted, store.current_path(self.base).read_bytes())
                store.current_path(self.base).write_bytes(original)

    def test_envelope_and_revision_types_are_strict(self):
        original = store.read_current(self.base)
        for field, value in (("schema_version", True), ("schema_version", 1.0),
                             ("revision", True), ("revision", -1), ("revision", 0.0)):
            with self.subTest(field=field, value=value):
                store._write_json_atomic(store.current_path(self.base), dict(original, **{field: value}))
                with self.assertRaises(ValueError):
                    store.read_current(self.base)
        store._write_json_atomic(store.current_path(self.base), original)
        for revision in (False, 0.0, "0", -1):
            with self.assertRaises(ValueError):
                store.compare_and_set(self.base, revision, self.task())
        self.assertEqual(original, store.read_current(self.base))

    def test_compatible_states_and_old_run_ids_are_not_rewritten(self):
        for run in ("run-abc12345", "TAP-123-69d8a1f0", "TAP-123-69d8a1f0-01234567"):
            for stage, outcome in (("waiting_takeover", "in_progress"), ("completed", "completed"),
                                   ("design_review", "interrupted"), ("completed", "in_progress")):
                value = dict(self.task(), run_id=run, stage=stage, outcome=outcome)
                envelope = {"schema_version": 1, "revision": 7, "current": value}
                store._write_json_atomic(store.current_path(self.base), envelope)
                before = store.current_path(self.base).read_bytes()
                self.assertEqual(envelope, store.read_current(self.base))
                self.assertEqual(value, engine.current_task(self.base / ".agenticops"))
                self.assertEqual(before, store.current_path(self.base).read_bytes())

    def test_frozen_baseline_and_repository_references_are_validated(self):
        name = "owner/repo"
        baseline = engineering_baseline.freeze({"id": "fixture", "revision": 1, "repositories": [name]},
            {name: {"origin": "https://example.test/owner/repo"}},
            {name: {"verification": "verified", "ref_kind": "branch", "ref_name": "develop",
                    "commit_sha": "a" * 40, "resolution_source": "fixture", "rule_version": "1"}}, {"fixture": True})
        binding = engineering_baseline.task_repository(baseline, name, "fix/test", "develop", ["src"], "unit")
        valid = dict(self.task(), engineering_baseline=baseline, task_repositories={name: binding})
        envelope = {"schema_version": 1, "revision": 0, "current": valid}
        self.assertEqual(envelope, store.validate_current(envelope))
        for field, value in (("approved_scope", []), ("deliveries", ["bad"]), ("observation", []),
                             ("repository_id", "other/repo"), ("baseline_entry_digest", "b" * 64)):
            broken = copy.deepcopy(envelope)
            broken["current"]["task_repositories"][name][field] = value
            with self.assertRaises(ValueError):
                store.validate_current(broken)
        broken = copy.deepcopy(envelope)
        broken["current"]["engineering_baseline"]["digest"] = "b" * 64
        with self.assertRaises(ValueError):
            store.validate_current(broken)

    def test_shared_schema_validator_handles_nullable_boolean_and_min_items(self):
        for value in (None, {}):
            quality_contract.validate(value, {"type": ["object", "null"]})
        for value in (True, False):
            quality_contract.validate(value, {"type": "boolean", "enum": [True, False]})
        for value, schema in ((1, {"enum": [True, False]}), (True, {"type": "integer"}),
                              (0, {"type": "boolean"}), ([], {"type": "array", "minItems": 1}),
                              (None, {"type": "unknown"}), (None, {"type": []})):
            with self.assertRaises(ValueError):
                quality_contract.validate(value, schema)

    def test_single_current_and_revision(self):
        task = self.task()
        store.write_task(self.base, task)
        stale = store.read_task(self.base)
        task["stage"] = "design_review"
        store.write_task(self.base, task)
        with self.assertRaisesRegex(ValueError, "revision"):
            store.write_task(self.base, stale)
        with self.assertRaisesRegex(ValueError, "占用"):
            store.write_task(self.base, dict(task, issue_key="TAP-124"))
        self.assertFalse((self.base / ".agenticops/tasks").exists())
        self.assertFalse((self.base / ".agenticops/tasks.lock").exists())

    def test_gate_never_redirects_another_state_directory_to_current_station(self):
        store.write_task(self.base, self.task())
        self.assertIsNotNone(engine.current_task(self.base / ".agenticops"))
        self.assertIsNone(engine.current_task(self.base / "unrelated-state"))

    def test_run_guard_and_archive_guard(self):
        task = self.task()
        store.write_task(self.base, task)
        with self.assertRaises(ValueError):
            store.check_expected_run(self.base, "TAP-123", "run-stale")
        task["archive_ref"] = {"scope": "product", "run_id": task["run_id"], "digest": "a" * 64}
        store.write_task(self.base, task)
        with self.assertRaisesRegex(ValueError, "归档"):
            store.require_development(self.base, task)

    def test_legacy_state_never_adopted(self):
        (self.base / ".agenticops/tasks").mkdir()
        with self.assertRaisesRegex(ValueError, "原版本"):
            store.read_current(self.base)

    def test_operation_retries_keep_identity_and_intent(self):
        request = {"issue_key": "TAP-123"}
        op = operation.begin(self.base, "takeover", "op-12345678", 0, request)
        retry = operation.begin(self.base, "takeover", "op-12345678", 0, request)
        self.assertEqual(op, retry)
        with self.assertRaisesRegex(ValueError, "不同请求"):
            operation.begin(self.base, "takeover", "op-12345678", 0, {"issue_key": "TAP-124"})
        with self.assertRaisesRegex(ValueError, "未完成"):
            operation.begin(self.base, "takeover", "op-87654321", 0, {})
        operation.intent(self.base, op, "prepare", {"exists": False}, {"sha": "a" * 40})
        with self.assertRaisesRegex(ValueError, "未核验"):
            operation.finish(self.base, op)
        operation.receipt(self.base, op, "prepare", {"sha": "a" * 40})
        operation.finish(self.base, op)
        self.assertEqual("done", operation.read(self.base)["status"])


if __name__ == "__main__":
    unittest.main()
