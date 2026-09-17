#!/usr/bin/env python3
"""单工位占用与操作恢复合同；仅使用临时目录。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import task_store as store, station_operation as operation


class StationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / ".agenticops").mkdir()
        store.initialize_current(self.base)
        store._write_json_atomic(self.base / ".agenticops/station.json", {
            "schema_version": 4, "product_root": str(ROOT), "source_pool": str(self.base / "pool"), "station_id": "a" * 32,
            "project": "tapdata", "agents": ["codex"],
            "branch_identity": {"schema_version": 1, "git_name": "Test", "source": "git_global_user_name"},
        })

    def task(self):
        return {"issue_key": "TAP-123", "run_id": "run-abc12345", "outcome": "in_progress"}

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

    def test_run_guard_and_archive_guard(self):
        task = self.task()
        store.write_task(self.base, task)
        with self.assertRaises(ValueError):
            store.check_expected_run(self.base, "TAP-123", "run-stale")
        task["archive_ref"] = {"digest": "a" * 64}
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
