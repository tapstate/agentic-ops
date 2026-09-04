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
        self.refs = {"develop": "a" * 40, "release-v4.18.0": "b" * 40, "release-v4.21.0": "c" * 40}

    def resolve(self, payload=None):
        with mock.patch.object(issue_versions, "remote_refs", return_value=self.refs):
            return issue_versions.resolve(self.base, self.task, payload or self.payload)

    def test_develop_first_keeps_all_versions_and_manual_merge_list(self):
        self.payload["selected_version_id"] = "2"
        result = self.resolve()
        self.assertEqual(result["primary_branch"], "develop")
        self.assertIsNone(result["selected_version_id"])
        self.assertEqual([v["id"] for v in result["manual_merge"]], ["1", "2"])
        self.assertEqual(result["refs"]["develop"], "a" * 40)
        self.assertTrue(result["refs_verified_at"])

    def test_absent_develop_selects_exactly_one_and_never_repairs_both(self):
        self.payload["develop"]["status"] = "absent"
        with self.assertRaisesRegex(ValueError, "选择一个"):
            self.resolve()
        self.payload["selected_version_id"] = "2"
        result = self.resolve()
        self.assertEqual(result["primary_branch"], "release-v4.21.0")
        self.assertEqual([v["id"] for v in result["manual_merge"]], ["1"])
        self.payload["selected_version_id"] = "9"
        with self.assertRaisesRegex(ValueError, "不属于"):
            self.resolve()

    def test_single_version_is_automatic_only_when_develop_is_absent(self):
        self.payload["develop"]["status"] = "absent"
        self.payload["issue"]["fields"]["versions"] = [{"id": "1", "name": "v4.18.0"}]
        result = self.resolve()
        self.assertEqual(result["selected_version_id"], "1")
        self.assertEqual(result["manual_merge"], [])

    def test_missing_any_version_rejects_even_when_develop_has_bug(self):
        del self.refs["release-v4.21.0"]
        with self.assertRaisesRegex(ValueError, "不存在.*release-v4.21.0"):
            self.resolve()

    def test_no_description_fixversions_unknown_or_stale_fallback(self):
        for raw in ({"description": "问题版本 develop", "fixVersions": [{"id": "1", "name": "develop"}]},
                    {"versions": []}, {"versions": [{"id": "1", "name": "latest"}]}):
            payload = copy.deepcopy(self.payload); payload["issue"]["fields"] = raw
            with self.assertRaises(ValueError): self.resolve(payload)
        for develop in ({"status": "unknown", "source_ref": "fixture:x"},
                        {"status": "absent", "revision": "a3561f47", "source_ref": "fixture:x"}):
            payload = dict(self.payload, develop=develop)
            with self.assertRaises(ValueError): self.resolve(payload)

    def test_network_failure_is_not_missing_branch(self):
        with mock.patch.object(issue_versions.subprocess, "run", return_value=SimpleNamespace(returncode=128)):
            with self.assertRaisesRegex(ValueError, "核验失败.*不能认定"):
                issue_versions.remote_refs("fixture:remote", {"develop"})
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
                task.cmd_record(SimpleNamespace(dir=self.base, issue_key="TAP-123", key=key, value="develop", force=True))
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
        with mock.patch.object(issue_versions, "remote_refs", return_value=self.refs), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(task.cmd_issue_versions(args), 0)
        with self.assertRaisesRegex(ValueError, "cleanup/reset"):
            task.cmd_issue_versions(args)

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
