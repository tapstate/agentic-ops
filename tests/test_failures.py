#!/usr/bin/env python3
"""失败续办的持久化、预算及人工处置回归。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import failures, task_store


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        (self.base / ".agenticops").mkdir()
        (self.base / ".agenticops/workspace.json").write_text(json.dumps({
            "schema_version": 1, "product_root": str(ROOT), "project": "tapdata", "agents": []}))
        self.task = {"issue_key": "DEMO-1", "run_id": "run-one", "repositories": [{"repository": "owner/repo"}]}
        task_store._write_json_atomic(task_store.task_path(self.base, "DEMO-1"), self.task)
        task_store.register(self.base, "DEMO-1", status="active")

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, **event):
        state, _ = failures.load(self.base, self.task)
        return failures.apply(self.base, "DEMO-1", "run-one", state["revision"], event)

    def observe(self, **changes):
        event = dict(action="observe", repository="owner/repo", check_id="acceptance-1",
                     label="目标断言失败", attribution="current_change", evidence="report:baseline-comparison")
        event.update(changes)
        return self.apply(**event)["problem_id"]

    def finish(self, key, result="FAIL"):
        return self.apply(action="finish", problem_id=key, result=result,
                          source_revision="a" * 40, evidence="report:rerun")

    def decision(self, key, **changes):
        event = dict(action="decide", problem_id=key, decision="continue", additional_rounds=1,
                     reason="研发允许再试一次", proof=dict(actor="研发", source="user_message",
                     reference="message:1", at="2026-09-11T16:00:00+08:00"))
        event.update(changes)
        return self.apply(**event)

    def test_three_rounds_shared_across_stages_recovery_and_rename(self):
        key = self.observe()
        for stage in ("local", "ci", "review"):
            self.apply(action="start", problem_id=key, stage=stage)
            self.finish(key)
            self.assertEqual(key, self.observe(label="问题换个名字"))
        with self.assertRaisesRegex(ValueError, "轮数已用尽"):
            self.apply(action="start", problem_id=key, stage="ci")
        state, problems = failures.load(self.base, self.task)
        self.assertEqual(3, problems[key]["attempts"])
        self.decision(key)
        self.apply(action="start", problem_id=key, stage="local")
        result = self.finish(key, "PASS")
        self.assertEqual(4, result["problems"][key]["attempts"])
        self.assertEqual("resolved", result["problems"][key]["status"])
        self.observe()
        with self.assertRaises(ValueError):
            self.apply(action="start", problem_id=key, stage="ci")

    def test_non_change_failure_needs_explicit_decision(self):
        for attribution in ("preexisting", "environment", "unknown"):
            key = self.observe(attribution=attribution)
            with self.assertRaisesRegex(ValueError, "研发决定"):
                self.apply(action="start", problem_id=key, stage="local")
        self.decision(key)
        self.apply(action="start", problem_id=key, stage="local")
        self.finish(key, "UNKNOWN")
        with self.assertRaises(ValueError):
            self.apply(action="start", problem_id=key, stage="ci")

    def test_gap_is_not_pass_or_permission_to_repair(self):
        key = self.observe()
        result = self.decision(key, decision="accept_gap", uncovered="连接断开恢复", follow_up="关联后续开发")
        self.assertEqual("accepted_gap", result["problems"][key]["status"])
        with self.assertRaises(ValueError):
            self.apply(action="start", problem_id=key, stage="ci")

    def test_pending_round_cannot_be_restarted_or_hidden(self):
        key = self.observe()
        self.apply(action="start", problem_id=key, stage="local")
        for event in [dict(action="start", problem_id=key, stage="ci"),
                      dict(action="finish", problem_id=key, result="PASS", source_revision="develop", evidence="log")]:
            with self.assertRaises(ValueError):
                self.apply(**event)
        with self.assertRaises(ValueError):
            self.observe(label="改名重开")
        self.finish(key, "NOT_RUN")
        _, problems = failures.load(self.base, self.task)
        self.assertEqual(1, problems[key]["attempts"])

    def test_stale_run_revision_and_unknown_repository_preserve_file(self):
        self.observe()
        before = failures.path(self.base, self.task).read_bytes()
        for run, rev in [("old-run", 1), ("run-one", 0)]:
            with self.assertRaises(ValueError):
                failures.apply(self.base, "DEMO-1", run, rev, {})
        with self.assertRaises(ValueError):
            self.observe(repository="another/repo")
        self.assertEqual(before, failures.path(self.base, self.task).read_bytes())

    def test_addition_does_not_change_existing_task_or_ci_state(self):
        old_task = task_store.task_path(self.base, "DEMO-1").read_bytes()
        ci_path = task_store.task_directory(self.base, "DEMO-1") / "ci-existing.json"
        ci_path.write_text('{"existing":true}')
        self.observe()
        self.assertEqual(old_task, task_store.task_path(self.base, "DEMO-1").read_bytes())
        self.assertEqual('{"existing":true}', ci_path.read_text())
        new_run = dict(self.task, run_id="run-two")
        self.assertEqual({}, failures.load(self.base, new_run)[1])

    def test_invalid_decision_or_corrupt_state_rejected(self):
        key = self.observe()
        for changes in [dict(additional_rounds=True), dict(additional_rounds=0),
                        dict(proof={}), dict(decision="accept_gap", uncovered="")]:
            with self.assertRaises(ValueError):
                self.decision(key, **changes)
        path = failures.path(self.base, self.task)
        state = json.loads(path.read_text())
        state["revision"] = 999
        path.write_text(json.dumps(state))
        with self.assertRaises(ValueError):
            failures.load(self.base, self.task)


if __name__ == "__main__":
    unittest.main()
