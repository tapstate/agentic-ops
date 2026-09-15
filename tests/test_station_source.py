#!/usr/bin/env python3
"""固定独立仓库的身份、引用和恢复验证。"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import station_source as source, station_operation as operations, task_store


class SourceFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.ws = self.root / "workspace"
        (self.ws / ".agenticops").mkdir(parents=True)
        task_store.initialize_current(self.ws)
        task_store._write_json_atomic(self.ws / ".agenticops/workspace.json", {
            "schema_version": 3, "product_root": str(ROOT), "project": "tapdata", "workspace_id": "a" * 32})
        task_store._write_json_atomic(self.ws / ".agenticops/init.json", {"workspace_state_epoch": 3})
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.git(self.seed, "init", "-b", "develop")
        self.git(self.seed, "config", "user.name", "Test")
        self.git(self.seed, "config", "user.email", "test@example.com")
        (self.seed / "file.txt").write_text("base\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "base")
        self.remote = self.root / "remote.git"
        self.git(self.root, "clone", "--bare", str(self.seed), str(self.remote))
        self.name = "tapdata/tapdata"
        self.repo = source.repository_path(self.ws, self.name)
        self.catalog = {self.name: {"origin": str(self.remote)}}
        self.op = operations.begin(self.ws, "takeover", "op-source-test", 0, {})

    def git(self, path, *args):
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def prepare(self):
        values = source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)
        self.sha = values[self.name]["_refs"]["develop"]
        self.git(self.repo, "checkout", "--detach", self.sha)
        return values


class SourceTests(SourceFixture, unittest.TestCase):
    def test_independent_clone_and_durable_fetch(self):
        self.prepare()
        source.identity(self.repo, str(self.remote))
        self.assertTrue((self.repo / ".git").is_dir())
        self.assertFalse((self.repo / ".git/objects/info/alternates").exists())
        self.assertEqual(self.op["steps"]["fetch:" + self.name]["receipt"]["refs"]["develop"], self.sha)
        source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)

    def test_wrong_push_destination_rejected(self):
        self.prepare()
        self.git(self.repo, "config", "remote.origin.pushurl", str(self.root / "other.git"))
        with self.assertRaisesRegex(ValueError, "地址"):
            source.identity(self.repo, str(self.remote))

    def test_multiple_push_destinations_rejected(self):
        self.prepare()
        for _ in range(2):
            self.git(self.repo, "config", "--add", "remote.origin.pushurl", str(self.remote))
        with self.assertRaises(ValueError):
            source.identity(self.repo, str(self.remote))

    def test_url_rewrite_rejected(self):
        self.prepare()
        self.git(self.repo, "config", "url." + str(self.root / "other.git") + ".insteadOf", str(self.remote))
        with self.assertRaises(ValueError):
            source.identity(self.repo, str(self.remote))

    def test_dirty_and_symlink_rejected(self):
        self.prepare()
        (self.repo / "file.txt").write_text("work")
        with self.assertRaisesRegex(ValueError, "未提交"):
            source.require_clean(self.repo)
        (self.ws / "source/tapdata/alias").symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(ValueError):
            source.repository_path(self.ws, "tapdata/alias")

    def test_existing_local_tag_is_not_force_rewritten(self):
        self.prepare()
        self.git(self.repo, "tag", "keep-tag", self.sha)
        self.git(self.seed, "commit", "--allow-empty", "-m", "next")
        self.git(self.seed, "tag", "keep-tag")
        self.git(self.seed, "push", str(self.remote), "refs/tags/keep-tag")
        self.op["steps"].pop("fetch:" + self.name)
        operations.save(self.ws, self.op)
        with self.assertRaises(ValueError):
            source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)
        self.assertEqual(self.git(self.repo, "rev-parse", "keep-tag"), self.sha)


if __name__ == "__main__":
    unittest.main()
