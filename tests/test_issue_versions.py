#!/usr/bin/env python3
"""影响版本与 develop 优先的离线回归；所有 Git 远端均为临时本地仓库。"""
import copy
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import issue_versions, task, task_store


class IssueVersionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ao-versions-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        product = self.base / "product"
        shutil.copytree(ROOT / "projects/tapdata", product / "projects/tapdata")
        (self.base / ".agenticops").mkdir()
        (self.base / ".agenticops/workspace.json").write_text(json.dumps({"product_root": str(product), "project": "tapdata"}))
        self.task = {"issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "defect_fix",
                     "stage": "task_intake", "facts": {}, "repositories": [], "pending": None, "history": []}
        task.save(self.base, self.task); task_store.register(self.base, "TAP-123")
        self.payload = {"issue": {"key": "TAP-123", "fields": {"versions": [
            {"id": "1", "name": "4.18.0"}, {"id": "2", "name": "release-v4.21.0"}]}},
            "source_ref": "fixture:jira/TAP-123", "develop": {"status": "present", "revision": "a" * 40, "source_ref": "fixture:source-analysis"}}
        self.payload["effective"] = {"execution_branch": "develop", "proof": {
            "actor": "fixture-user", "source": "user_message", "reference": "fixture:confirmation",
            "at": "2026-09-07T10:00:00+08:00"}}
        self.refs = {"develop": "a" * 40, "release-v4.18.0": "b" * 40, "release-v4.21.0": "c" * 40}

    def resolve(self, payload=None):
        with mock.patch.object(issue_versions, "remote_refs", return_value=self.refs):
            return issue_versions.resolve(self.base, self.task, payload or self.payload)

    def test_arbitrary_version_names_do_not_require_version_branches(self):
        self.payload["issue"]["fields"]["versions"] = [{"id": "1", "name": "v4.22"}]
        self.refs = {"develop": "a" * 40}
        result = self.resolve()
        self.assertEqual(result["versions"], [{"id": "1", "name": "v4.22"}])
        self.assertEqual(result["primary_branch"], "develop")
        self.assertNotIn("manual_merge", result)

    def test_local_correction_preserves_initial_observation_without_jira_id(self):
        self.payload["effective"]["versions"] = [{"name": "4.22.0"}]
        result = self.resolve()
        self.assertEqual(result["sync_status"], "pending")
        self.assertEqual(result["observed"]["issue"], self.payload["issue"])
        self.task["facts"][issue_versions.FACT] = result
        newer = copy.deepcopy(self.payload)
        newer["issue"]["fields"]["versions"] = [{"name": "externally changed"}]
        self.assertEqual(self.resolve(newer)["observed"], result["observed"])

    def test_execution_branch_requires_confirmation_and_real_git_ref(self):
        self.payload["effective"].pop("proof")
        with self.assertRaises(ValueError):
            self.resolve()
        self.setUp_payload_proof()
        self.payload["develop"]["status"] = "absent"
        self.payload["effective"]["execution_branch"] = "custom-fix-line"
        with self.assertRaisesRegex(ValueError, "不存在"):
            self.resolve()
        self.refs["custom-fix-line"] = "d" * 40
        self.assertEqual(self.resolve()["primary_branch"], "custom-fix-line")

    def setUp_payload_proof(self):
        self.payload["effective"]["proof"] = {"actor": "fixture-user", "source": "user_message",
            "reference": "fixture:confirmation", "at": "2026-09-07T10:00:00+08:00"}

    def test_missing_versions_can_be_supplied_locally_but_unknown_evidence_cannot(self):
        self.payload["issue"]["fields"]["versions"] = []
        with self.assertRaises(ValueError):
            self.resolve()
        self.payload["effective"]["versions"] = [{"name": "confirmed-version"}]
        self.assertEqual(self.resolve()["versions"], [{"name": "confirmed-version"}])
        self.payload["develop"]["status"] = "unknown"
        with self.assertRaises(ValueError):
            self.resolve()

    def test_network_failure_is_not_missing_branch(self):
        with mock.patch.object(issue_versions.subprocess, "run", return_value=SimpleNamespace(returncode=128)):
            with self.assertRaisesRegex(ValueError, "核验失败.*不能认定"):
                issue_versions.remote_refs("fixture:remote", {"develop"})

    def test_field_readback_clears_warning_without_changing_effective_facts(self):
        from workflow import external_sync, quality
        self.payload["effective"]["versions"] = [{"name": "4.22.0"}]
        plan = self.resolve()
        self.task["facts"] = {issue_versions.FACT: plan, "problem_version": "4.22.0"}
        before = copy.deepcopy(self.task)
        payload = {"fact_key": "problem_version", "expected_fact_digest": quality.digest("4.22.0"),
                   "jira_field": "versions", "issue": {"key": "TAP-123", "fields": {"versions": [{"id": "9", "name": "4.22.0"}]}},
                   "source_ref": "fixture:readback"}
        external_sync.record_readback(self.base, self.task, payload)
        self.assertEqual(self.task, before)
        self.assertFalse(any(w["kind"] == "affected_versions" for w in external_sync.warnings(self.base, self.task)))
        self.task["facts"]["problem_version"] = "4.23.0"
        with self.assertRaisesRegex(ValueError, "摘要不匹配"):
            external_sync.record_readback(self.base, self.task, payload)

    def test_snapshot_is_immutable_and_wrong_run_cannot_replace_it(self):
        path = self.base / "snapshot.json"
        path.write_text(json.dumps(self.payload))
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"], input=str(path))
        with contextlib.redirect_stdout(io.StringIO()):
            task.cmd_snapshot(args)
            before = task_store.task_path(self.base, "TAP-123").read_bytes()
            path.write_text(json.dumps({"issue": {"key": "OTHER-1"}}))
            task.cmd_snapshot(args)
        self.assertEqual(task_store.task_path(self.base, "TAP-123").read_bytes(), before)
        args.expected_run_id = "run-ffffffffffff"
        with self.assertRaises(ValueError):
            task.cmd_snapshot(args)
        with mock.patch.object(issue_versions.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)):
            with self.assertRaisesRegex(ValueError, "超时"):
                issue_versions.remote_refs("fixture:remote", {"develop"})

    def test_command_rejects_wrong_run_and_force_record_then_gates_branch(self):
        path = self.base / "input.json"; path.write_text(json.dumps(self.payload))
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"], input=str(path))
        with mock.patch.object(issue_versions, "remote_refs", return_value=self.refs), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(task.cmd_issue_versions(args), 0)
        current = task.load(self.base, "TAP-123")
        self.assertEqual(current["facts"]["problem_version"], "4.18.0、release-v4.21.0")
        self.assertEqual(issue_versions.problems(self.base, current), [])
        current["repositories"] = [{"repository": "tapdata/tapdata", "base_branch": "release-v4.18.0"}]
        self.assertTrue(issue_versions.problems(self.base, current))
        current["repositories"] = [{"repository": "tapdata/hazelcast", "base_branch": "release-v5.5.0"}]
        self.assertEqual(issue_versions.problems(self.base, current), [])
        for key in ("problem_version", issue_versions.FACT):
            with self.assertRaisesRegex(ValueError, "不允许 record"):
                task.cmd_record(SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"],
                                                key=key, value="develop", force=True))
        args.expected_run_id = "run-ffffffffffff"
        with self.assertRaisesRegex(ValueError, "run 已变化"): task.cmd_issue_versions(args)
        current["run_id"] = args.expected_run_id
        self.assertTrue(issue_versions.problems(self.base, current))

    def test_actual_git_refs_not_checkout_or_tags(self):
        remote = self.base / "local-remote"
        subprocess.run(["git", "init", "-q", "-b", "develop", str(remote)], check=True)
        subprocess.run(["git", "-C", str(remote), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "fixture", "--allow-empty"], check=True)
        subprocess.run(["git", "-C", str(remote), "tag", "release-v4.18.0"], check=True)
        refs = issue_versions.remote_refs(str(remote), {"develop", "release-v4.18.0"})
        self.assertEqual(set(refs), {"develop"})
        self.assertEqual(len(refs["develop"]), 40)

    def test_initial_analysis_on_prepared_develop_does_not_require_reset(self):
        self.task["repositories"] = [{"repository": "tapdata/tapdata", "base_branch": "develop", "base_sha": "a" * 40}]
        task.save(self.base, self.task)
        path = self.base / "input.json"; path.write_text(json.dumps(self.payload))
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"], input=str(path))
        with mock.patch.object(task.repository_worktree, "task_roots", return_value=[self.base]), \
                mock.patch.object(issue_versions, "remote_refs", return_value=self.refs), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(task.cmd_issue_versions(args), 0)
        with mock.patch.object(task.repository_worktree, "task_roots", return_value=[self.base]), \
                mock.patch.object(issue_versions, "remote_refs", return_value=self.refs), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(task.cmd_issue_versions(args), 0)

    def test_next_is_read_only_and_reports_real_blockers(self):
        previous = task_store.task_path(self.base, "TAP-123").read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            task.cmd_next(SimpleNamespace(dir=self.base, issue_key="TAP-123"))
        report = json.loads(output.getvalue())
        self.assertFalse(report["advance_ready"])
        self.assertEqual(report["next_stage"], "design_review")
        self.assertTrue(any("影响版本" in b for b in report["blockers"]))
        self.assertEqual(task_store.task_path(self.base, "TAP-123").read_bytes(), previous)

    def test_next_does_not_apply_defect_checkpoints_to_other_task_classes(self):
        self.task.update(task_class="technical_task", stage="implementation")
        task.save(self.base, self.task)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            task.cmd_next(SimpleNamespace(dir=self.base, issue_key="TAP-123"))
        self.assertEqual(json.loads(output.getvalue())["checkpoints"], {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
