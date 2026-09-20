#!/usr/bin/env python3
"""Git refs 单仓、单范围缓存的离线合同测试。"""
import json
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import git_refs, source_sync


class GitRefsTests(unittest.TestCase):
    def identity(self, repository, remote, repository_id=None, source_root=None):
        identity = {"repository_id": repository_id or str(Path(repository).resolve()), "remote": remote,
                    "origin": "github.test/a/repo.git", "repository_path": str(Path(repository).resolve()),
                    "git_common_dir": "/git"}
        if source_root:
            identity["source_root"] = str(Path(source_root).resolve())
        return "key", identity

    def test_per_scope_ttl_and_refresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", side_effect=[{"main": "a" * 40}, {"v1": {"object": "b" * 40}}]) as query:
                first = git_refs.snapshot("/repo", scopes=("heads",), cache_file=cache, cache_root="/root", now=100)
                cached = git_refs.snapshot("/repo", scopes=("heads",), cache_file=cache, cache_root="/root", now=101)
                tags = git_refs.snapshot("/repo", scopes=("tags",), cache_file=cache, cache_root="/root", now=101)
            self.assertEqual("refreshed", first["scopes"]["heads"]["freshness"])
            self.assertEqual("cached", cached["scopes"]["heads"]["freshness"])
            self.assertEqual("refreshed", tags["scopes"]["tags"]["freshness"])
            self.assertEqual(2, query.call_count)

    def test_failed_refresh_keeps_last_success_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", return_value={"main": "a" * 40}):
                git_refs.snapshot("/repo", cache_file=cache, cache_root="/root", now=100)
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", side_effect=git_refs.GitRefsError("network")):
                result = git_refs.snapshot("/repo", cache_file=cache, cache_root="/root", now=500)
            self.assertEqual("refresh_failed", result["scopes"]["heads"]["freshness"])
            self.assertEqual("a" * 40, result["scopes"]["heads"]["refs"]["main"])
            document = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(100, document["roots"]["/root"]["repositories"]["key"]["scopes"]["heads"]["last_success_epoch"])

    def test_repository_identity_change_does_not_reuse_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            identities = [("a", {"repository_path": "/a", "git_common_dir": "/a/.git", "remote": "origin", "origin": "github/a"}),
                          ("b", {"repository_path": "/b", "git_common_dir": "/b/.git", "remote": "origin", "origin": "github/b"})]
            with mock.patch.object(git_refs, "repository_identity", side_effect=identities), \
                    mock.patch.object(git_refs, "_query", side_effect=[{"main": "a" * 40}, {"main": "b" * 40}]):
                one = git_refs.snapshot("/a", cache_file=cache, cache_root="/root-a", now=100)
                two = git_refs.snapshot("/b", cache_file=cache, cache_root="/root-a", now=101)
            self.assertNotEqual(one["scopes"]["heads"]["refs"], two["scopes"]["heads"]["refs"])

    def test_probe_is_uncached_and_returns_missing_as_none(self):
        completed = mock.Mock(returncode=0, stdout="a" * 40 + "\trefs/heads/main\n", stderr="")
        with mock.patch.object(git_refs, "_run", return_value=completed):
            result = git_refs.probe("git@github.test:owner/repo.git", ["main", "feature/x"])
        self.assertEqual("a" * 40, result["heads"]["main"])
        self.assertIsNone(result["heads"]["feature/x"])

    def test_query_heads_filters_unrequested_refs_and_accepts_sha256(self):
        completed = mock.Mock(returncode=0, stderr="", stdout=(
            "a" * 64 + "\trefs/heads/origin/topic\n" +
            "b" * 40 + "\trefs/heads/other\n" +
            "c" * 40 + "\trefs/tags/missing\n"))
        with mock.patch.object(git_refs, "_run", return_value=completed) as run:
            refs = git_refs.query_heads("fixture:remote", ["origin/topic", "missing", "origin/topic"])
        self.assertEqual(refs, {"origin/topic": "a" * 64})
        run.assert_called_once_with(["git", "ls-remote", "--heads", "fixture:remote",
                                     "refs/heads/origin/topic", "refs/heads/missing"])

    def test_query_heads_rejects_invalid_inputs_without_query(self):
        for heads in ([], [""], ["a\x00b"], [{}], [1]):
            with self.subTest(heads=heads), mock.patch.object(git_refs, "_run") as run:
                with self.assertRaises(git_refs.GitRefsError):
                    git_refs.query_heads("fixture:remote", heads)
                run.assert_not_called()

    def test_probe_keeps_its_branch_input_contract(self):
        for head in ("origin/topic", "refs/topic"):
            with self.subTest(head=head), mock.patch.object(git_refs, "_run") as run:
                with self.assertRaises(git_refs.GitRefsError):
                    git_refs.probe("fixture:remote", [head])
                run.assert_not_called()

    def test_read_snapshot_does_not_create_or_write_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "missing" / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query") as query:
                result = git_refs.read_snapshot("/repo", cache_file=cache, cache_root="/root", now=100, repository_id="owner/repo")
            self.assertEqual("stale", result["scopes"]["heads"]["freshness"])
            self.assertFalse(cache.exists())
            query.assert_not_called()

    def test_auto_fresh_cache_does_not_take_write_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", return_value={"main": "a" * 40}):
                git_refs.snapshot("/repo", cache_file=cache, cache_root="/root", now=100)
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_write_cache") as write:
                result = git_refs.snapshot("/repo", cache_file=cache, cache_root="/root", now=101)
            self.assertEqual("cached", result["scopes"]["heads"]["freshness"])
            write.assert_not_called()

    def test_repository_id_and_source_root_are_part_of_cache_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", wraps=git_refs.repository_identity) as identity, \
                    mock.patch.object(git_refs, "_query", return_value={"main": "a" * 40}):
                identity.side_effect = self.identity
                git_refs.snapshot("/repo", cache_file=cache, cache_root="/root", repository_id="owner/repo", source_root="/pool-a", now=100)
                other = git_refs.read_snapshot("/repo", cache_file=cache, cache_root="/root", repository_id="owner/repo", source_root="/pool-b", now=101)
            self.assertEqual("stale", other["scopes"]["heads"]["freshness"])

    def test_roots_are_isolated_and_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", side_effect=[{"main": "a" * 40}, {"main": "b" * 40}]):
                git_refs.snapshot("/repo-a", cache_file=cache, cache_root="/root-a", now=100)
                git_refs.snapshot("/repo-b", cache_file=cache, cache_root="/root-b", now=101)
            document = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(2, document["schema_version"])
            self.assertEqual("a" * 40, document["roots"]["/root-a"]["repositories"]["key"]["scopes"]["heads"]["refs"]["main"])
            self.assertEqual("b" * 40, document["roots"]["/root-b"]["repositories"]["key"]["scopes"]["heads"]["refs"]["main"])

    def test_v1_cache_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "git-ref-cache-v1.json"
            legacy = {"schema_version": 1, "repositories": {"key": {}}}
            cache.write_text(json.dumps(legacy), encoding="utf-8")
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity):
                with self.assertRaisesRegex(git_refs.GitRefsError, "schema 不兼容"):
                    git_refs.snapshot("/repo", cache_file=cache, cache_root="/root", now=100)
            self.assertEqual(legacy, json.loads(cache.read_text(encoding="utf-8")))

    def test_cache_write_permission_error_is_normalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            blocked_parent = Path(temporary) / "not-a-directory"
            blocked_parent.write_text("file", encoding="utf-8")
            with self.assertRaisesRegex(git_refs.GitRefsError, "无法锁定 Git refs 缓存"):
                with git_refs._cache_lock(blocked_parent / "cache.json"):
                    pass


class SourceSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git("init", "-q", "-b", "develop")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.base = self.commit("base", "base")
        self.git("checkout", "-qb", "feature")
        self.task = self.commit("task", "task")
        self.git("checkout", "develop")
        self.source = self.commit("source", "source")
        self.git("checkout", "feature")

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return source_sync.git(self.root, *args)

    def commit(self, path, content):
        (self.root / path).write_text(content)
        self.git("add", path)
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def verify(self, **values):
        return source_sync.verify(self.root, values.get("branch", "feature"), self.base,
                                  values.get("source", self.source))

    def test_unmerged_source_rejected_then_merge_preserves_task(self):
        with self.assertRaisesRegex(ValueError, "尚未包含"):
            self.verify()
        self.git("merge", "--no-edit", "develop")
        result = self.verify()
        self.assertTrue(result["contains_source"])
        self.assertTrue(source_sync.ancestor(self.root, self.task, result["task_revision"]))
        self.assertEqual(self.base, result["base_revision"])

    def test_dirty_wrong_branch_and_non_sha_rejected(self):
        self.git("merge", "--no-edit", "develop")
        with self.assertRaises(ValueError):
            self.verify(branch="develop")
        with self.assertRaises(ValueError):
            self.verify(source="develop")
        (self.root / "untracked").write_text("keep")
        with self.assertRaisesRegex(ValueError, "未提交"):
            self.verify()
        self.assertEqual("keep", (self.root / "untracked").read_text())

    def test_conflict_keeps_scene_and_does_not_report_synced(self):
        self.commit("source", "different")
        result = subprocess.run(["git", "-C", str(self.root), "merge", "--no-edit", "develop"],
                                capture_output=True)
        self.assertNotEqual(0, result.returncode)
        with self.assertRaisesRegex(ValueError, "冲突"):
            self.verify()
        self.assertIn("<<<<<<<", (self.root / "source").read_text())

    def test_conflict_free_merge_still_requires_impact_analysis(self):
        self.git("merge", "--no-edit", "develop")
        result = source_sync.impact(self.root, "feature", self.base, self.source, self.task)
        self.assertTrue(result["analysis_required"])
        self.assertEqual(["task"], result["comparisons"]["original_task"]["paths"])
        self.assertEqual(["source"], result["comparisons"]["incoming_source"]["paths"])
        self.assertEqual(["task"], result["comparisons"]["final_task"]["paths"])
        old_revision = result["task_revision"]
        self.commit("task", "revised behavior")
        updated = source_sync.impact(self.root, "feature", self.base, self.source, self.task)
        self.assertNotEqual(old_revision, updated["task_revision"])
        self.assertNotEqual(result["comparisons"]["final_task"]["diff_sha256"],
                            updated["comparisons"]["final_task"]["diff_sha256"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
