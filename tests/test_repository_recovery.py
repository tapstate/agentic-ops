#!/usr/bin/env python3
"""固定 source 的中断恢复与对象保留合同。"""
import json
import shutil
import unittest
from unittest import mock
from test_station_source import ROOT, SourceFixture
from workflow import station_source as source, station_resources as resources, task_store, station_directories as directories, engineering_baseline, station, station_cleanup


class RecoveryTests(SourceFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / 'policies', self.root / 'product/policies')
        path = self.root / "product/projects/tapdata/repositories.json"
        catalog = json.loads(path.read_text())
        catalog["repositories"][self.name]["origin"] = str(self.remote)
        path.write_text(json.dumps(catalog))
        init = self.ws / '.agenticops/init.json'
        value = json.loads(init.read_text()); value['cleanup'] = json.loads((ROOT / 'policies/station-clean.json').read_text()); init.write_text(json.dumps(value))
        (self.ws / 'config').mkdir()
        cleanup = self.root / 'product/projects/tapdata/station-clean.json'
        value = json.loads(cleanup.read_text())
        value['resources'] = []  # 此夹具只验证 Git 恢复，不访问宿主机 Docker。
        cleanup.write_text(json.dumps(value))

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
                "task_class": "technical_task", "stage": "waiting_takeover", "outcome": "in_progress",
                "facts": {}, "history": [], "pending": None, "terminal_proof": None, "archive_ref": None,
                "engineering_baseline": {"status": "resolving"}, "task_repositories": {}}
        task_store.write_task(self.ws, task)
        (self.ws / "runtime").mkdir(exist_ok=True)
        directories.create(self.ws, task, "runtime", "workflow", adopt=True)
        return task

    def exit(self, task, materials="discard"):
        current = task_store.read_task(self.ws)
        return station.execute(self.ws, 'clean', task['issue_key'], task['run_id'], current['_revision'], 'op-partial-cleanup', {'scope': 'task', 'expected_plan_digest': station_cleanup.plan(self.ws)['digest'], 'materials': materials, 'decision_ref': '确认处理本地源码范围'})

    def test_partial_clone_can_be_cleaned_after_disposition(self):
        task = self.partial()
        head = self.git(self.repo, 'rev-parse', 'HEAD')
        result = self.exit(task)
        self.assertTrue(result['cleanup_result']['complete'], result)
        self.assertTrue((self.repo / '.git').is_dir())
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), head)
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')
        self.assertIsNone(task_store.read_task(self.ws))

    def test_partial_clone_unknown_file_preserved(self):
        task = self.partial()
        (self.repo / 'personal.txt').write_text('retain')
        with self.assertRaisesRegex(ValueError, '尚有未保全'):
            self.exit(task, materials='preserved')
        self.assertEqual((self.repo / 'personal.txt').read_text(), 'retain')
        result = self.exit(task)
        self.assertTrue(result['cleanup_result']['complete'], result)

    def test_fetch_receipt_rejects_reference_drift(self):
        self.prepare()
        self.git(self.repo, "update-ref", "-d", "refs/remotes/origin/develop")
        with self.assertRaisesRegex(ValueError, "漂移"):
            source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)

    def test_cleanup_requires_discard_for_unmerged_local_commits(self):
        self.prepare()
        self.git(self.repo, "checkout", "-b", "fix/retained")
        self.git(self.repo, "config", "user.email", "test@example.com")
        self.git(self.repo, "config", "user.name", "Test")
        self.git(self.repo, "commit", "--allow-empty", "-m", "unfinished")
        head = self.git(self.repo, "rev-parse", "HEAD")
        frozen = engineering_baseline.freeze({"id": "full-application", "revision": 3, "repositories": [self.name]},
            self.catalog, {self.name: {"verification": "verified", "ref_kind": "commit", "ref_name": self.sha,
                "commit_sha": self.sha, "resolution_source": "fixture", "rule_version": "1"}}, {"fixture": True})
        task = {"issue_key": "TAP-123", "run_id": self.op["run_id"], "source_prepared": True,
                "task_class": "technical_task", "stage": "waiting_takeover", "outcome": "in_progress",
                "facts": {}, "history": [], "pending": None, "terminal_proof": None, "archive_ref": None,
                "engineering_baseline": frozen,
                "task_repositories": {}}
        task_store.write_task(self.ws, task)
        (self.ws / "runtime").mkdir(exist_ok=True)
        directories.create(self.ws, task, "runtime", "workflow", adopt=True)
        with self.assertRaisesRegex(ValueError, "尚有未保全"):
            self.exit(task, materials="preserved")
        self.assertEqual(self.git(self.repo, "rev-parse", "fix/retained"), head)
        result = self.exit(task)
        self.assertTrue(result["cleanup_result"]["complete"], result)
        self.assertTrue((self.repo / '.git').is_dir())
        self.assertEqual(self.git(self.repo, 'rev-parse', 'HEAD'), self.sha)
        self.assertEqual(self.git(self.repo, 'status', '--porcelain'), '')
        self.assertEqual(self.git(self.repo, 'for-each-ref', '--format=%(refname)', 'refs/heads'), '')


if __name__ == "__main__":
    unittest.main()
