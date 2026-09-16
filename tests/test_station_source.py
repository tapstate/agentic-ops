#!/usr/bin/env python3
"""固定独立仓库的身份、引用和恢复验证。"""
from pathlib import Path
import os
import shutil
import threading
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import station_source as source, station_operation as operations, task_store


class GitProcessTests(unittest.TestCase):
    def fake_git(self, directory, body):
        executable = Path(directory) / "git"
        executable.write_text("#!%s\n%s" % (sys.executable, body))
        executable.chmod(0o755)
        return mock.patch.dict(os.environ, {"PATH": directory + os.pathsep + os.environ["PATH"]})

    def test_network_budget_does_not_extend_local_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.fake_git(directory, "import time\ntime.sleep(.15)\nprint('download complete')\n"), \
                    mock.patch.object(source, "GIT_LOCAL_TIMEOUT", .05), \
                    mock.patch.object(source, "GIT_NETWORK_TIMEOUT", 2):
                for operation in ("clone", "fetch"):
                    self.assertEqual(source.git(directory, operation).stdout.strip(), "download complete")
                with self.assertRaisesRegex(ValueError, "Git 操作超时"):
                    source.git(directory, "status")

    def test_timeout_reaps_descendants_holding_output_pipes(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "unexpected-write"
            child = "import time; from pathlib import Path; time.sleep(3); Path(%r).write_text('late')" % str(marker)
            script = "import subprocess,sys,time\nsubprocess.Popen([sys.executable, '-c', %r])\ntime.sleep(3)\n" % child
            with self.fake_git(directory, script), mock.patch.object(source, "GIT_NETWORK_TIMEOUT", .5):
                started = time.monotonic()
                with self.assertRaisesRegex(ValueError, "恢复原操作"):
                    source.git(directory, "clone")
                self.assertLess(time.monotonic() - started, 2)
                self.assertFalse(marker.exists())


class SourceFixture:
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        shutil.copytree(ROOT / "contracts", self.root / "product/contracts")
        shutil.copytree(ROOT / "projects", self.root / "product/projects")
        self.ws = self.root / "workspace"
        (self.ws / ".agenticops").mkdir(parents=True)
        task_store.initialize_current(self.ws)
        task_store._write_json_atomic(self.ws / ".agenticops/workspace.json", {
            "schema_version": 3, "product_root": str(self.root / "product"), "project": "tapdata", "workspace_id": "a" * 32})
        task_store._write_json_atomic(self.ws / ".agenticops/init.json", {"workspace_state_epoch": 4})
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
    def test_readiness_failure_revokes_old_observation_without_changing_source(self):
        from workflow import engineering_baseline as baseline
        import json
        self.prepare()
        value = baseline.freeze({"id": "test", "revision": 1, "repositories": [self.name]}, self.catalog,
            {self.name: {"verification": "verified", "ref_kind": "branch", "ref_name": "develop", "commit_sha": self.sha,
                         "resolution_source": "fixture", "rule_version": "1"}}, {"fixture": True})
        task = {"issue_key": "TAP-123", "run_id": self.op["run_id"], "facts": {"station_contract": 2},
                "source_prepared": True, "engineering_baseline": value,
                "task_repositories": {self.name: baseline.task_repository(value, self.name, "fix/ready", "develop", ["file.txt"], "unit")}}
        self.git(self.repo, "checkout", "-b", "fix/ready")
        task_store.write_task(self.ws, task)
        with mock.patch.object(source, "git", wraps=source.git) as calls:
            record = source.prepare_readiness(self.ws, task)
            self.assertEqual(record["snapshot"]["digest"], source.require_readiness(self.ws, task))
            self.assertFalse({call.args[1] for call in calls.call_args_list} & {"clean", "reset", "merge", "rebase", "restore", "push", "checkout"})
        real_git = source.git
        def fail_fetch(path, *args, **kwargs):
            if args[0] == "fetch":
                raise ValueError("network unavailable")
            return real_git(path, *args, **kwargs)
        with mock.patch.object(source, "git", side_effect=fail_fetch):
            with self.assertRaisesRegex(ValueError, "network unavailable"):
                source.prepare_readiness(self.ws, task)
        with self.assertRaisesRegex(ValueError, "证据失效"):
            source.require_readiness(self.ws, task)
        stored = json.loads((self.ws / ".agenticops/evidence/source-readiness.json").read_text())
        self.assertEqual(stored["status"], "refreshing")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.sha)
        self.assertEqual(self.git(self.repo, "branch", "--show-current"), "fix/ready")
        source.prepare_readiness(self.ws, task)
        self.assertEqual(source.require_readiness(self.ws, task), record["snapshot"]["digest"])
        self.git(self.repo, "push", "origin", "HEAD:refs/heads/fix/ready")
        with self.assertRaisesRegex(ValueError, "远端工作分支"):
            source.require_readiness(self.ws, task)
        with self.assertRaisesRegex(ValueError, "远端工作分支"):
            source.prepare_readiness(self.ws, task)

    def test_independent_clone_and_durable_fetch(self):
        self.prepare()
        source.identity(self.repo, str(self.remote))
        self.assertTrue((self.repo / ".git").is_dir())
        self.assertFalse((self.repo / ".git/objects/info/alternates").exists())
        self.assertEqual(self.op["steps"]["fetch:" + self.name]["receipt"]["refs"]["develop"], self.sha)
        source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)

    def test_cold_pool_precedes_workspace_and_warm_pool_uses_local_transfer(self):
        with mock.patch.object(source, "git", wraps=source.git) as calls:
            self.prepare()
            clones = [call.args for call in calls.call_args_list if call.args[1] == "clone"]
            self.assertEqual(len(clones), 2)
            self.assertIn(str(self.remote), clones[0])
            cache = source.source_pool.pool_path(self.ws, self.name, str(self.remote))
            self.assertIn(str(cache), clones[1])
            other = self.root / "other-workspace"
            shutil.copytree(self.ws / ".agenticops", other / ".agenticops")
            operation = dict(self.op, steps={})
            calls.reset_mock()
            source.prepare_repositories(other, self.catalog, [self.name], operation)
            clones = [call.args for call in calls.call_args_list if call.args[1] == "clone"]
            self.assertEqual(len(clones), 1)
            self.assertIn(str(cache), clones[0])
            source.identity(source.repository_path(other, self.name), str(self.remote))
        shutil.rmtree(cache)
        # 已完成操作不再访问缓存或远端；工位仍拥有完整对象。
        with mock.patch.object(source.source_pool, "refreshed", side_effect=AssertionError("unexpected refresh")):
            source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)
        self.assertEqual(self.git(self.repo, "show", self.sha + ":file.txt"), "base")
        self.git(self.repo, "fsck", "--full")

    def test_incremental_remote_update_and_old_pending_fetch(self):
        self.prepare()
        self.git(self.seed, "commit", "--allow-empty", "-m", "next")
        self.git(self.seed, "push", str(self.remote), "develop")
        expected = self.git(self.seed, "rev-parse", "HEAD")
        # 旧版本相同结构的未完成 fetch 意图可以恢复，无需迁移。
        self.op["steps"]["fetch:" + self.name]["receipt"] = None
        operations.save(self.ws, self.op)
        values = source.prepare_repositories(self.ws, self.catalog, [self.name], self.op)
        self.assertEqual(values[self.name]["_refs"]["develop"], expected)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), self.sha)

    def test_failed_download_does_not_publish_pool_or_workspace(self):
        actual = source.git
        def fail(path, *args, **kwargs):
            if args[0] == "clone":
                raise ValueError("download failed")
            return actual(path, *args, **kwargs)
        with mock.patch.object(source, "git", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "download failed"):
                self.prepare()
        cache = source.source_pool.pool_path(self.ws, self.name, str(self.remote))
        self.assertFalse(cache.exists())
        self.assertFalse(self.repo.exists())
        self.prepare()

    def test_pool_identity_and_symlink_rejected(self):
        self.prepare()
        cache = source.source_pool.pool_path(self.ws, self.name, str(self.remote))
        self.op["steps"]["fetch:" + self.name]["receipt"] = None
        self.git(cache, "remote", "set-url", "origin", str(self.seed))
        with self.assertRaisesRegex(ValueError, "源码池 origin"):
            self.prepare()
        shutil.rmtree(cache)
        cache.symlink_to(self.remote, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "真实目录"):
            self.prepare()

    def test_pool_lock_covers_consumer(self):
        self.prepare()
        acquired = threading.Event()
        started = threading.Event()
        errors = []
        def consumer():
            started.set()
            try:
                with source.source_pool.refreshed(self.ws, self.name, str(self.remote), source.git):
                    acquired.set()
            except Exception as error:
                errors.append(error)
        with source.source_pool.refreshed(self.ws, self.name, str(self.remote), source.git):
            thread = threading.Thread(target=consumer)
            thread.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(acquired.wait(.1))
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertTrue(acquired.is_set())

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
