#!/usr/bin/env python3
"""固定 source 的中断恢复与对象保留合同。"""
import json
import unittest
from unittest import mock
from test_station_source import SourceFixture
from workflow import station_source as source, station_resources as resources, task_store, station_directories as directories


class RecoveryTests(SourceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        path = self.root / "product/projects/tapdata/repositories.json"
        catalog = json.loads(path.read_text())
        catalog["repositories"][self.name]["origin"] = str(self.remote)
        path.write_text(json.dumps(catalog))

    def test_failed_fetch_keeps_clone_for_retry(self):
        original = source.git
        def fail(path, *args, **kwargs):
            if args[0] == "fetch" and path == self.repo:
                raise ValueError("network unavailable")
            return original(path, *args, **kwargs)
        with mock.patch.object(source, "git", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "network"):
                source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)
        self.assertTrue((self.repo / ".git").is_dir())
        self.assertIsNone(self.op["steps"]["fetch:" + self.name]["receipt"])
        source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)

    def partial(self):
        source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)
        task = {"issue_key": "TAP-123", "run_id": self.op["run_id"],
                "engineering_baseline": {"status": "resolving"}, "task_repositories": {}}
        task_store.write_task(self.ws, task)
        (self.ws / "runtime").mkdir(exist_ok=True)
        directories.create(self.ws, task, "runtime", "workflow", adopt=True)
        return task

    def test_partial_clone_can_be_neutralized(self):
        task = self.partial()
        self.op["cleanup_plan"] = resources.plan(self.ws, task)
        self.assertEqual(self.op["cleanup_plan"]["entries"], [])
        resources.neutral(self.ws, task, self.op)
        self.assertEqual({p.name for p in self.repo.iterdir()}, {".git"})
        self.assertTrue(self.op["cleanup_plan"]["source"][self.name]["initial_checkout"])

    def test_partial_clone_unknown_file_preserved(self):
        task = self.partial()
        (self.repo / "personal.txt").write_text("retain")
        with self.assertRaises(ValueError):
            resources.plan(self.ws, task)
        self.assertEqual((self.repo / "personal.txt").read_text(), "retain")

    def test_fetch_receipt_rejects_reference_drift(self):
        self.prepare()
        self.git(self.repo, "update-ref", "-d", "refs/remotes/origin/develop")
        with self.assertRaisesRegex(ValueError, "漂移"):
            source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)

    def test_neutral_preserves_unmerged_branch_and_commits(self):
        self.prepare()
        self.git(self.repo, "checkout", "-b", "fix/retained")
        self.git(self.repo, "config", "user.email", "test@example.com")
        self.git(self.repo, "config", "user.name", "Test")
        self.git(self.repo, "commit", "--allow-empty", "-m", "unfinished")
        head = self.git(self.repo, "rev-parse", "HEAD")
        task = {"issue_key": "TAP-123", "run_id": self.op["run_id"], "source_prepared": True,
                "engineering_baseline": {"status": "frozen", "profile": {"id": "full-application", "revision": 2},
                    "repositories": {self.name: {"origin": str(self.remote)}}},
                "reset_baseline": {self.name: {"sha": self.sha}}, "task_repositories": {}}
        task_store.write_task(self.ws, task)
        (self.ws / "runtime").mkdir(exist_ok=True)
        directories.create(self.ws, task, "runtime", "workflow", adopt=True)
        self.op["cleanup_plan"] = resources.plan(self.ws, task)
        resources.neutral(self.ws, task, self.op)
        self.assertEqual(self.git(self.repo, "rev-parse", "fix/retained"), head)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "")


if __name__ == "__main__":
    unittest.main()
