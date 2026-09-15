#!/usr/bin/env python3
"""清理仅作用于确认清单，重试不扩大删除范围。"""
import json
import unittest
from unittest import mock
from test_station_source import SourceFixture
from workflow import station_resources as resources, task_store, station_operation


class ResourceTests(SourceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.prepare()
        self.task = {"issue_key": "TAP-123", "run_id": self.op["run_id"], "archive_ref": {"digest": "test"},
                     "facts": {"station_contract": 2},
                     "engineering_baseline": {"status": "frozen", "repositories": {self.name: {"origin": str(self.remote), "commit_sha": self.sha, "path": "source/" + self.name}}},
                     "task_repositories": {self.name: {"target_branch": "develop", "work_branch": "fix/test", "approved_scope": ["file.txt"], "verification_method": "unit", "baseline_entry_digest": "test"}}}
        task_store.write_task(self.ws, self.task)
        (self.ws / "runtime").mkdir()

    def register_files(self, *paths):
        task_store._write_json_atomic(self.ws / ".agenticops/evidence/resources.json", {
            "run_id": self.task["run_id"], "entries": [{"kind": "file", "path": path, "producer": "test"} for path in paths]})

    def test_unknown_runtime_blocks_without_deletion(self):
        path = self.ws / "runtime/unknown"
        path.write_text("keep")
        with self.assertRaisesRegex(ValueError, "未登记"):
            resources.plan(self.ws, self.task)
        self.assertEqual(path.read_text(), "keep")

    def test_confirmed_delete_and_retry(self):
        path = self.ws / "runtime/output"
        path.write_text("generated")
        self.register_files("runtime/output")
        plan = resources.plan(self.ws, self.task)
        resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.assertFalse(path.exists())

    def test_changed_since_confirmation_preserved(self):
        path = self.ws / "runtime/output"
        path.write_text("old")
        self.register_files("runtime/output")
        plan = resources.plan(self.ws, self.task)
        path.write_text("new")
        with self.assertRaisesRegex(ValueError, "变化"):
            resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.assertEqual(path.read_text(), "new")

    def test_restore_crash_before_receipt_can_resume(self):
        path = self.repo / "file.txt"
        path.write_text("unfinished work")
        plan = resources.plan(self.ws, self.task)
        with mock.patch.object(station_operation, "receipt", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.assertEqual(path.read_text(), "base\n")

    def test_tracked_code_not_generated_resource(self):
        self.register_files("source/" + self.name + "/file.txt")
        with self.assertRaisesRegex(ValueError, "已跟踪"):
            resources.plan(self.ws, self.task)
        self.assertTrue((self.repo / "file.txt").exists())

    def test_registration_preserves_previous_ownership(self):
        self.task["archive_ref"] = None
        task_store.write_task(self.ws, self.task)
        station_operation.finish(self.ws, self.op)
        for name in ("one", "two"):
            (self.ws / "runtime" / name).write_text(name)
            resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [
                {"kind": "file", "path": "runtime/" + name, "producer": "test"}])
        self.assertEqual(len(resources.inventory(self.ws, self.task)), 2)

    def test_index_only_change_is_in_plan(self):
        path = self.repo / "file.txt"
        path.write_text("staged work")
        self.git(self.repo, "add", "file.txt")
        path.write_text("base\n")
        plan = resources.plan(self.ws, self.task)
        self.assertEqual(len(plan["entries"]), 1)
        resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "")

    def test_index_drift_after_confirmation_is_preserved(self):
        path = self.repo / "file.txt"
        path.write_text("staged one")
        self.git(self.repo, "add", "file.txt")
        path.write_text("base\n")
        plan = resources.plan(self.ws, self.task)
        path.write_text("staged two")
        self.git(self.repo, "add", "file.txt")
        path.write_text("base\n")
        with self.assertRaisesRegex(ValueError, "变化"):
            resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.assertEqual(self.git(self.repo, "show", ":file.txt"), "staged two")

    def test_resource_in_another_repository_is_not_owned(self):
        path = self.ws / "source/other/repo/file"
        path.parent.mkdir(parents=True)
        path.write_text("retain")
        self.register_files("source/other/repo/file")
        with self.assertRaisesRegex(ValueError, "不属于"):
            resources.plan(self.ws, self.task)
        self.assertTrue(path.exists())

    def test_live_process_blocks_without_killing(self):
        task_store._write_json_atomic(self.ws / ".agenticops/evidence/resources.json", {
            "run_id": self.task["run_id"], "entries": [{"kind": "process", "pid": 1234, "started_at": "known-start"}]})
        result = mock.Mock(returncode=0, stdout="known-start\n")
        with mock.patch.object(resources.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "仍存活"):
                resources.verify_stopped(self.ws, self.task)

    def test_quiesced_external_can_archive_then_confirm_cleanup(self):
        entry = {"kind": "external", "id": "test-data", "producer": "test", "status": "quiesced", "readback_ref": "stopped-proof",
                 "action": "delete", "before": {"protected": False, "state": "present"}}
        task_store._write_json_atomic(self.ws / ".agenticops/evidence/resources.json", {
            "run_id": self.task["run_id"], "entries": [entry]})
        plan = resources.plan(self.ws, self.task)
        self.op["external_confirmation_digest"] = plan["external_confirmation_digest"]
        with self.assertRaisesRegex(ValueError, "清理完成"):
            resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [dict(entry, status="cleaned", readback_ref="deleted-proof")])
        resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.assertTrue(any(name.startswith("external:") for name in self.op["steps"]))

    def test_active_evidence_drift_preserves_runtime(self):
        path = self.ws / "runtime/output"
        path.write_text("keep until confirmed")
        self.register_files("runtime/output")
        plan = resources.plan(self.ws, self.task)
        task_store._write_json_atomic(self.ws / ".agenticops/evidence/late.json", {"late": True})
        with self.assertRaisesRegex(ValueError, "活动材料变化"):
            resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.assertTrue(path.exists())

    def test_external_readback_does_not_change_confirmation_scope(self):
        entry = {"kind": "external", "id": "pr:owner/repo:1", "producer": "test", "status": "quiesced",
                 "readback_ref": "before", "action": "close", "before": {"head": "a" * 40, "protected": False}}
        task_store._write_json_atomic(self.ws / ".agenticops/evidence/resources.json", {"run_id": self.task["run_id"], "entries": [entry]})
        plan = resources.plan(self.ws, self.task)
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [dict(entry, status="cleaned", readback_ref="closed")])
        self.assertEqual(plan, resources.plan(self.ws, self.task))
        with self.assertRaisesRegex(ValueError, "再次确认"):
            resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        self.op["external_confirmation_digest"] = plan["external_confirmation_digest"]
        resources.clean(self.ws, self.task, plan, plan["digest"], self.op)

    def test_active_disappearance_requires_persisted_delete_intent(self):
        evidence = self.ws / ".agenticops/evidence/proof.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text("proof")
        plan = resources.plan(self.ws, self.task)
        evidence.unlink()
        with self.assertRaisesRegex(ValueError, "活动材料变化"):
            resources.verify_active(self.ws, plan, self.op)
        name = "clear-active:0:" + plan["digest"]
        self.op["steps"][name] = {"receipt": None}
        resources.verify_active(self.ws, plan, self.op)
        evidence.write_text("changed")
        with self.assertRaisesRegex(ValueError, "活动材料变化"):
            resources.verify_active(self.ws, plan, self.op)
        self.op["steps"][name]["receipt"] = {"done": True}
        evidence.write_text("proof")
        with self.assertRaisesRegex(ValueError, "活动材料变化"):
            resources.verify_active(self.ws, plan, self.op)

    def test_existing_v1_plan_keeps_original_shape(self):
        self.op.update(kind="clean", cleanup_plan=resources.plan(self.ws, self.task, version=1))
        station_operation.save(self.ws, self.op)
        self.assertEqual(resources.plan(self.ws, self.task), self.op["cleanup_plan"])

    def test_legacy_run_new_exit_keeps_v1(self):
        self.task["facts"] = {}
        for kind in ("takeover", "archive", "clean", "release"):
            self.op.update(kind=kind, status="done")
            station_operation.save(self.ws, self.op)
            self.assertEqual(resources.plan(self.ws, self.task)["schema_version"], 1)

    def test_new_external_requires_action_and_before(self):
        self.task["archive_ref"] = None
        task_store.write_task(self.ws, self.task)
        station_operation.finish(self.ws, self.op)
        entry = {"kind": "external", "id": "branch:test", "producer": "test", "status": "quiesced", "readback_ref": "proof"}
        for extra in ({}, {"action": "delete"}, {"action": "delete", "before": {"head": self.sha}},
                      {"action": "delete", "before": {"protected": True}}):
            with self.assertRaises(ValueError):
                resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [dict(entry, **extra)])
        self.task["facts"] = {}
        task_store.write_task(self.ws, self.task)
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry])

    def test_external_retain_and_protected_delete(self):
        self.task["archive_ref"] = None
        task_store.write_task(self.ws, self.task)
        station_operation.finish(self.ws, self.op)
        entry = {"kind": "external", "id": "branch:owner/repo:main", "producer": "test", "status": "quiesced",
                 "readback_ref": "protected-main", "action": "delete", "before": {"protected": True, "head": "a" * 40}}
        with self.assertRaisesRegex(ValueError, "不受保护"):
            resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry])
        entry.update(action="retain", status="retained")
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry])
        plan = resources.plan(self.ws, self.task)
        self.task["archive_ref"] = {"digest": "test"}
        self.op["external_confirmation_digest"] = plan["external_confirmation_digest"]
        resources.clean(self.ws, self.task, plan, plan["digest"], self.op)
        receipt = next(step["receipt"] for name, step in self.op["steps"].items() if name.startswith("external:"))
        self.assertEqual(receipt["status"], "retained")

    def test_recovery_registration_requires_exact_current_operation(self):
        self.op["kind"] = "clean"
        station_operation.save(self.ws, self.op)
        entry = {"kind": "file", "path": "runtime/late", "producer": "known build"}
        (self.ws / "runtime/late").write_text("late output")
        with self.assertRaisesRegex(ValueError, "操作"):
            resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry], "op-stale-one")
        self.assertEqual(resources.inventory(self.ws, self.task), [])
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry], self.op["operation_id"])
        self.assertEqual(resources.inventory(self.ws, self.task), [entry])

    def test_late_resource_after_standalone_archive_can_prepare_clean(self):
        self.op["kind"] = "archive"
        station_operation.finish(self.ws, self.op)
        entry = {"kind": "file", "path": "runtime/late", "producer": "known build"}
        (self.ws / "runtime/late").write_text("late")
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry], self.op["operation_id"])
        self.assertEqual(len(resources.plan(self.ws, self.task)["entries"]), 1)

    def test_completed_task_can_register_cleanup_only_before_release(self):
        self.task.update(archive_ref=None, outcome="completed")
        task_store.write_task(self.ws, self.task)
        station_operation.finish(self.ws, self.op)
        entry = {"kind": "file", "path": "runtime/late", "producer": "known build"}
        (self.ws / "runtime/late").write_text("late")
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry], self.op["operation_id"])
        self.assertEqual(resources.inventory(self.ws, self.task), [entry])

    def test_unpublished_archive_recovery_registers_only_owned_files(self):
        self.task["archive_ref"] = None
        task_store.write_task(self.ws, self.task)
        self.op["kind"] = "archive"
        station_operation.save(self.ws, self.op)
        entry = {"kind": "file", "path": "runtime/late", "producer": "known build"}
        (self.ws / "runtime/late").write_text("late")
        with self.assertRaises(ValueError):
            resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry])
        resources.register(self.ws, self.task["issue_key"], self.task["run_id"], [entry], self.op["operation_id"])
        self.assertEqual(resources.inventory(self.ws, self.task), [entry])
        with self.assertRaises(ValueError):
            resources.register(self.ws, self.task["issue_key"], self.task["run_id"],
                               [{"kind": "external", "id": "new", "producer": "test", "status": "cleaned", "readback_ref": "proof"}], self.op["operation_id"])


if __name__ == "__main__":
    unittest.main()
