#!/usr/bin/env python3
"""Git refs 单仓、单范围缓存的离线合同测试。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import git_refs


class GitRefsTests(unittest.TestCase):
    def identity(self, repository, remote, repository_id=None, source_pool_root=None):
        identity = {"repository_id": repository_id or str(Path(repository).resolve()), "remote": remote,
                    "origin": "github.test/a/repo.git", "repository_path": str(Path(repository).resolve()),
                    "git_common_dir": "/git"}
        if source_pool_root:
            identity["source_pool_root"] = str(Path(source_pool_root).resolve())
        return "key", identity

    def test_per_scope_ttl_and_refresh(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", side_effect=[{"main": "a" * 40}, {"v1": {"object": "b" * 40}}]) as query:
                first = git_refs.snapshot("/repo", scopes=("heads",), cache_file=cache, now=100)
                cached = git_refs.snapshot("/repo", scopes=("heads",), cache_file=cache, now=101)
                tags = git_refs.snapshot("/repo", scopes=("tags",), cache_file=cache, now=101)
            self.assertEqual("refreshed", first["scopes"]["heads"]["freshness"])
            self.assertEqual("cached", cached["scopes"]["heads"]["freshness"])
            self.assertEqual("refreshed", tags["scopes"]["tags"]["freshness"])
            self.assertEqual(2, query.call_count)

    def test_failed_refresh_keeps_last_success_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", return_value={"main": "a" * 40}):
                git_refs.snapshot("/repo", cache_file=cache, now=100)
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", side_effect=git_refs.GitRefsError("network")):
                result = git_refs.snapshot("/repo", cache_file=cache, now=500)
            self.assertEqual("refresh_failed", result["scopes"]["heads"]["freshness"])
            self.assertEqual("a" * 40, result["scopes"]["heads"]["refs"]["main"])
            document = json.loads(cache.read_text(encoding="utf-8"))
            self.assertEqual(100, document["repositories"]["key"]["scopes"]["heads"]["last_success_epoch"])

    def test_repository_identity_change_does_not_reuse_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            identities = [("a", {"repository_path": "/a", "git_common_dir": "/a/.git", "remote": "origin", "origin": "github/a"}),
                          ("b", {"repository_path": "/b", "git_common_dir": "/b/.git", "remote": "origin", "origin": "github/b"})]
            with mock.patch.object(git_refs, "repository_identity", side_effect=identities), \
                    mock.patch.object(git_refs, "_query", side_effect=[{"main": "a" * 40}, {"main": "b" * 40}]):
                one = git_refs.snapshot("/a", cache_file=cache, now=100)
                two = git_refs.snapshot("/b", cache_file=cache, now=101)
            self.assertNotEqual(one["scopes"]["heads"]["refs"], two["scopes"]["heads"]["refs"])

    def test_probe_is_uncached_and_returns_missing_as_none(self):
        completed = mock.Mock(returncode=0, stdout="a" * 40 + "\trefs/heads/main\n", stderr="")
        with mock.patch.object(git_refs, "_run", return_value=completed):
            result = git_refs.probe("git@github.test:owner/repo.git", ["main", "feature/x"])
        self.assertEqual("a" * 40, result["heads"]["main"])
        self.assertIsNone(result["heads"]["feature/x"])

    def test_read_snapshot_does_not_create_or_write_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "missing" / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query") as query:
                result = git_refs.read_snapshot("/repo", cache_file=cache, now=100, repository_id="owner/repo")
            self.assertEqual("stale", result["scopes"]["heads"]["freshness"])
            self.assertFalse(cache.exists())
            query.assert_not_called()

    def test_auto_fresh_cache_does_not_take_write_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_query", return_value={"main": "a" * 40}):
                git_refs.snapshot("/repo", cache_file=cache, now=100)
            with mock.patch.object(git_refs, "repository_identity", side_effect=self.identity), \
                    mock.patch.object(git_refs, "_write_cache") as write:
                result = git_refs.snapshot("/repo", cache_file=cache, now=101)
            self.assertEqual("cached", result["scopes"]["heads"]["freshness"])
            write.assert_not_called()

    def test_repository_id_and_source_pool_are_part_of_cache_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache.json"
            with mock.patch.object(git_refs, "repository_identity", wraps=git_refs.repository_identity) as identity, \
                    mock.patch.object(git_refs, "_query", return_value={"main": "a" * 40}):
                identity.side_effect = self.identity
                git_refs.snapshot("/repo", cache_file=cache, repository_id="owner/repo", source_pool_root="/pool-a", now=100)
                other = git_refs.read_snapshot("/repo", cache_file=cache, repository_id="owner/repo", source_pool_root="/pool-b", now=101)
            self.assertEqual("stale", other["scopes"]["heads"]["freshness"])

    def test_cache_write_permission_error_is_normalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            blocked_parent = Path(temporary) / "not-a-directory"
            blocked_parent.write_text("file", encoding="utf-8")
            with self.assertRaisesRegex(git_refs.GitRefsError, "无法锁定 Git refs 缓存"):
                with git_refs._cache_lock(blocked_parent / "cache.json"):
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
