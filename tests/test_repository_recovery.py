#!/usr/bin/env python3
"""真实本地 Git 图验证 PR 续办和新分支重做；不访问外部网络。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import repository_worktree as rw, task, task_store


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ao-recovery-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.ws = self.root / "workspace"
        self.product = self.root / "product"
        self.pool = self.root / "pool"
        self.seed = self.root / "seed"
        self.remote = self.root / "remote.git"
        self.repo = "tapdata/tapdata"
        shutil.copytree(ROOT / "projects", self.product / "projects")
        shutil.copytree(ROOT / "contracts", self.product / "contracts")
        self.git("init", "-q", "-b", "develop", str(self.seed))
        self.git("-C", str(self.seed), "config", "user.email", "test@example.test")
        self.git("-C", str(self.seed), "config", "user.name", "Test")
        self.git("-C", str(self.seed), "commit", "--allow-empty", "-qm", "base")
        self.original = self.git("-C", str(self.seed), "rev-parse", "HEAD")
        self.git("clone", "-q", "--bare", str(self.seed), str(self.remote))
        catalog = self.product / "projects/tapdata/repositories.json"
        doc = json.loads(catalog.read_text())
        doc["repositories"][self.repo]["origin"] = str(self.remote)
        task_store._write_json_atomic(catalog, doc)
        task_store._write_json_atomic(self.product / ".local/repository-pool.json", {
            "schema_version": 1, "root": str(self.pool), "provisioning": "auto-clone"})
        task_store._write_json_atomic(self.ws / ".agenticops/workspace.json", {
            "schema_version": 2, "product_root": str(self.product), "project": "tapdata",
            "workspace_id": "1" * 32, "agents": ["codex"],
            "repository_pool": {"root": str(self.pool), "source": "workspace-override"}})
        self.state = {
            "issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "technical_task",
            "stage": "task_intake", "facts": {}, "pending": None, "history": [], "repositories": [{
                "repository": self.repo, "authorized_endpoint": rw._normalize_origin(str(self.remote)),
                "base_branch": "develop", "work_branch": "fix/old", "approved_scope": "旧修复",
                "verification_method": "旧验证", "base_sha": None, "catalog_digest": None,
                "worktree": None, "pull_request": None, "ci": None}]}
        self.save()
        task_store.register(self.ws, "TAP-123")
        self.main = self.pool / self.repo
        self.main.parent.mkdir(parents=True)
        self.git("clone", "-q", str(self.remote), str(self.main))
        self.git("-C", str(self.main), "branch", "fix/old", self.original)

    def git(self, *args):
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE).strip()

    def save(self):
        task_store._write_json_atomic(task_store.task_path(self.ws, "TAP-123"), self.state)

    def read(self):
        return json.loads(task_store.task_path(self.ws, "TAP-123").read_text())

    def args(self, **kwargs):
        values = dict(dir=str(self.ws), issue_key="TAP-123", expected_run_id=self.read()["run_id"],
                      repo=self.repo, work_branch="fix/new", scope="新修复", verification="新验证",
                      reason="用户选择新分支", stage="task_intake", note="新分支处理")
        values.update(kwargs)
        return SimpleNamespace(**values)

    def advance_target(self):
        self.git("-C", str(self.seed), "commit", "--allow-empty", "-qm", "upstream")
        self.git("-C", str(self.seed), "push", "-q", str(self.remote), "develop")
        return self.git("-C", str(self.seed), "rev-parse", "HEAD")

    def prepare(self, **kwargs):
        return rw.prepare_task(self.ws, "TAP-123", reuse_existing_branch=True, **kwargs)

    def assert_no_worktree(self):
        self.assertFalse(rw.task_worktree_path(self.ws, self.read(), self.repo).exists())
        listing = self.git("-C", str(self.main), "worktree", "list", "--porcelain")
        self.assertEqual(listing.count("worktree "), 1)
        self.assertEqual(rw._load_leases(self.product), [])
        self.assertIsNone(self.read()["repositories"][0]["worktree"])

    def test_old_branch_rejects_latest_before_creation_then_explicit_continuation_works(self):
        latest = self.advance_target()
        before = self.read()
        with self.assertRaisesRegex(ValueError, "祖先"):
            self.prepare()
        self.assertEqual(self.read(), before)
        self.assert_no_worktree()
        self.prepare(continuation={self.repo: self.original})
        self.assertEqual(self.read()["repositories"][0]["base_sha"], self.original)
        root = rw.task_roots(self.ws, "TAP-123")[0]
        self.assertEqual(self.git("-C", str(root), "rev-parse", "HEAD"), self.original)
        self.assertNotEqual(latest, self.original)
        self.assertEqual(self.read()["history"][-2]["source"], "explicit_continuation")

    def test_same_run_keeps_original_base_after_upstream_and_local_commits(self):
        root = self.prepare()[0]
        self.git("-C", str(root), "-c", "user.name=Test", "-c", "user.email=t@example.test",
                 "commit", "--allow-empty", "-qm", "CI fix")
        head = self.git("-C", str(root), "rev-parse", "HEAD")
        latest = self.advance_target()
        self.prepare()
        self.assertEqual(self.read()["repositories"][0]["base_sha"], self.original)
        self.assertEqual(self.git("-C", str(rw.task_roots(self.ws, "TAP-123")[0]), "rev-parse", "HEAD"), head)
        with self.assertRaisesRegex(ValueError, "不可改变"):
            self.prepare(continuation={self.repo: latest})
        rw.cleanup_task(self.ws, "TAP-123")
        self.prepare()
        self.assertEqual(self.read()["repositories"][0]["base_sha"], self.original)

    def test_explicit_base_must_be_common_history(self):
        self.git("-C", str(self.main), "-c", "user.name=Test", "-c", "user.email=t@example.test",
                 "commit", "--allow-empty", "-qm", "local only")
        local = self.git("-C", str(self.main), "rev-parse", "HEAD")
        self.git("-C", str(self.main), "branch", "-f", "fix/old", local)
        with self.assertRaisesRegex(ValueError, "祖先"):
            self.prepare(continuation={self.repo: local})
        self.assert_no_worktree()

    def test_missing_frozen_branch_cannot_silently_start_from_latest(self):
        self.prepare()
        rw.cleanup_task(self.ws, "TAP-123")
        self.git("-C", str(self.main), "branch", "-d", "fix/old")
        self.advance_target()
        before = self.read()
        with self.assertRaisesRegex(ValueError, "分支已缺失"):
            self.prepare()
        self.assertEqual(before, self.read())

    def test_dirty_cleanup_preserves_uncommitted_work(self):
        root = self.prepare()[0]
        (root / "unsaved.txt").write_text("keep me", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未提交"):
            rw.cleanup_task(self.ws, "TAP-123")
        self.assertEqual((root / "unsaved.txt").read_text(), "keep me")
        with self.assertRaisesRegex(ValueError, "worktree"):
            task.cmd_repository_update(self.args())

    def test_post_creation_failure_removes_worktree_before_outer_rollback(self):
        real = rw._require_ancestor

        def fail_after_creation(main, *args):
            if Path(main) != self.main:
                raise ValueError("post-create injected failure")
            return real(main, *args)

        with mock.patch.object(rw, "_require_ancestor", side_effect=fail_after_creation):
            with self.assertRaisesRegex(ValueError, "injected"):
                self.prepare()
        self.assert_no_worktree()

    def test_new_branch_flow_preserves_git_and_archives_old_references(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "基线"):
            task.cmd_repository_update(self.args())
        rw.cleanup_task(self.ws, "TAP-123")
        task.cmd_reset(self.args())
        self.state = self.read()
        self.assertTrue(any(h["event"] == "archive_repository_baseline" for h in self.state["history"]))
        self.state["repositories"][0].update(pull_request="https://example.test/pull/899", ci="old-result")
        self.save()
        auth = task_store.authorization_path(self.ws, "TAP-123")
        task_store._write_json_atomic(auth, {"status": "active"})
        task.cmd_repository_update(self.args())
        current = self.read()
        self.assertEqual(json.loads(auth.read_text())["status"], "revoked")
        self.assertEqual(current["repositories"][0]["work_branch"], "fix/new")
        self.assertIsNone(current["repositories"][0]["pull_request"])
        self.assertEqual(current["history"][-1]["before"]["ci"], "old-result")
        self.assertEqual(self.git("-C", str(self.main), "rev-parse", "fix/old"), self.original)
        latest = self.advance_target()
        rw.prepare_task(self.ws, "TAP-123")
        self.assertEqual(self.read()["repositories"][0]["base_sha"], latest)
        self.assertEqual(len(rw.task_roots(self.ws, "TAP-123")), 1)

    def test_update_rejects_stale_run_stage_and_residual_lease(self):
        with self.assertRaisesRegex(ValueError, "run"):
            task.cmd_repository_update(self.args(expected_run_id="run-old"))
        self.state["stage"] = "implementation"
        self.save()
        with self.assertRaisesRegex(ValueError, "task_intake"):
            task.cmd_repository_update(self.args())
        self.state["stage"] = "task_intake"
        self.save()
        rw._write_leases(self.product, [{"pool_root": str(self.pool), "repository": self.repo,
                                       "branch": "fix/new", "workspace_id": "other"}])
        with self.assertRaisesRegex(ValueError, "租约"):
            task.cmd_repository_update(self.args())

    def test_update_remote_failure_and_existing_branch_leave_state_unchanged(self):
        before = self.read()
        self.git("-C", str(self.main), "push", "-q", "origin", "fix/old:fix/new")
        with self.assertRaisesRegex(ValueError, "远端已存在"):
            task.cmd_repository_update(self.args())
        self.assertEqual(before, self.read())
        real = rw._run

        def unreachable(arguments, **kwargs):
            if "ls-remote" in arguments:
                raise ValueError("network unreachable")
            return real(arguments, **kwargs)

        with mock.patch.object(rw, "_run", side_effect=unreachable):
            with self.assertRaisesRegex(ValueError, "network"):
                task.cmd_repository_update(self.args(work_branch="fix/another"))
        self.assertEqual(before, self.read())

    def test_update_state_write_failure_leaves_authorization_revoked(self):
        before = self.read()
        auth = task_store.authorization_path(self.ws, "TAP-123")
        task_store._write_json_atomic(auth, {"status": "active"})
        with mock.patch.object(task, "save", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                task.cmd_repository_update(self.args())
        self.assertEqual(before, self.read())
        self.assertEqual(json.loads(auth.read_text())["status"], "revoked")
        self.assertEqual(task.cmd_repository_update(self.args()), 0)

    def test_continuation_arguments_are_explicit_and_repository_bound(self):
        with self.assertRaises(ValueError):
            rw.continuation_bases([self.repo + "=develop"])
        with self.assertRaises(ValueError):
            rw.continuation_bases([self.repo + "=" + self.original] * 2)
        with self.assertRaises(ValueError):
            rw.prepare_task(self.ws, "TAP-123", continuation={self.repo: self.original})
        with self.assertRaises(ValueError):
            self.prepare(continuation={"other/repo": self.original})
        with self.assertRaises(ValueError):
            self.prepare(continuation_heads={"other/repo": self.original})
        self.assert_no_worktree()

    def publish_remote_only(self):
        self.git("-C", str(self.main), "checkout", "-q", "fix/old")
        self.git("-C", str(self.main), "-c", "user.name=Test", "-c", "user.email=t@example.test",
                 "commit", "--allow-empty", "-qm", "PR fix")
        head = self.git("-C", str(self.main), "rev-parse", "HEAD")
        self.git("-C", str(self.main), "push", "-q", "origin", "fix/old")
        self.git("-C", str(self.main), "checkout", "-q", "develop")
        # 模拟全新 Source Pool，只克隆 develop，不持有 PR 的本地 ref/对象。
        shutil.rmtree(self.main)
        self.git("clone", "-q", "--single-branch", "--branch", "develop",
                 str(self.remote), str(self.main))
        return head

    def test_remote_only_pr_is_fetched_at_expected_head_via_cli(self):
        head = self.publish_remote_only()
        self.advance_target()
        proc = subprocess.run([
            sys.executable, str(ROOT / "workflow/task.py"), "repository", "prepare",
            "--issue-key", "TAP-123", "--expected-run-id", self.read()["run_id"],
            "--dir", str(self.ws), "--reuse-existing-branch",
            "--continuation-base", self.repo + "=" + self.original,
            "--continuation-head", self.repo + "=" + head,
        ], text=True, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        root = rw.task_roots(self.ws, "TAP-123")[0]
        self.assertEqual(self.git("-C", str(root), "rev-parse", "HEAD"), head)
        self.assertEqual(self.read()["repositories"][0]["base_sha"], self.original)
        self.assertTrue(self.read()["repositories"][0]["worktree"]["branch_reused"])
        self.assertEqual(self.read()["history"][-2]["remote_head"], head)

    def test_remote_head_change_and_missing_branch_do_not_create_local_branch(self):
        self.publish_remote_only()
        before = self.read()
        with self.assertRaisesRegex(ValueError, "Head 已变化"):
            self.prepare(continuation={self.repo: self.original}, continuation_heads={self.repo: self.original})
        self.assert_no_worktree()
        self.assertEqual(self.read(), before)
        self.assertNotIn("refs/heads/fix/old", self.git("-C", str(self.main), "show-ref"))
        self.git("-C", str(self.seed), "push", "-q", str(self.remote), ":refs/heads/fix/old")
        with self.assertRaises(ValueError):
            self.prepare(continuation={self.repo: self.original}, continuation_heads={self.repo: self.original})
        self.assert_no_worktree()

    def test_remote_recovery_network_failure_and_post_creation_failure(self):
        head = self.publish_remote_only()
        kwargs = dict(continuation={self.repo: self.original}, continuation_heads={self.repo: head})
        real_run = rw._run

        def fail_fetch(arguments, **kwargs):
            if "refs/heads/fix/old" in arguments and "fetch" in arguments:
                raise ValueError("network unavailable")
            return real_run(arguments, **kwargs)

        with mock.patch.object(rw, "_run", side_effect=fail_fetch):
            with self.assertRaisesRegex(ValueError, "network"):
                self.prepare(**kwargs)
        self.assert_no_worktree()
        real_check = rw._require_ancestor

        def fail_created(main, *args):
            if Path(main) != self.main:
                raise ValueError("post-create failure")
            return real_check(main, *args)

        with mock.patch.object(rw, "_require_ancestor", side_effect=fail_created):
            with self.assertRaisesRegex(ValueError, "post-create"):
                self.prepare(**kwargs)
        self.assert_no_worktree()
        self.assertNotIn("refs/heads/fix/old", self.git("-C", str(self.main), "show-ref"))
        self.prepare(**kwargs)
        self.assertEqual(len(rw.task_roots(self.ws, "TAP-123")), 1)

    def test_existing_local_branch_is_never_overwritten_by_expected_head(self):
        latest = self.advance_target()
        with self.assertRaisesRegex(ValueError, "本地工作分支"):
            self.prepare(continuation={self.repo: self.original}, continuation_heads={self.repo: latest})
        self.assertEqual(self.git("-C", str(self.main), "rev-parse", "fix/old"), self.original)
        self.assert_no_worktree()

    def version_payload(self, latest):
        return {
            "issue": {"key": "TAP-123", "fields": {"versions": [{"name": "develop"}]}},
            "source_ref": "fixture:current-jira",
            "develop": {"status": "present", "revision": latest, "source_ref": "fixture:develop"},
            "effective": {"execution_branch": "develop", "proof": {
                "actor": "Test", "source": "user_message", "reference": "continue PR",
                "at": "2026-09-08T10:00:00+08:00"}},
        }

    def import_versions(self, payload):
        path = self.root / "versions.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return task.cmd_issue_versions(self.args(input=str(path)))

    def test_historical_development_base_allows_current_version_plan_and_reimport(self):
        self.state["task_class"] = "defect_fix"
        self.save()
        latest = self.advance_target()
        root = self.prepare(continuation={self.repo: self.original})[0]
        self.assertEqual(self.import_versions(self.version_payload(latest)), 0)
        newer = self.advance_target()
        payload = self.version_payload(newer)
        payload["effective"]["versions"] = [{"name": "corrected-version"}]
        self.assertEqual(self.import_versions(payload), 0)
        state = self.read()
        self.assertEqual(state["repositories"][0]["base_sha"], self.original)
        self.assertEqual(state["facts"]["issue_version_plan"]["refs"]["develop"], newer)
        self.assertEqual(self.git("-C", str(root), "rev-parse", "HEAD"), self.original)
        self.assertEqual(state["run_id"], self.state["run_id"])

    def test_version_plan_still_rejects_stale_evidence_and_invalid_development_context(self):
        self.state["task_class"] = "defect_fix"
        self.save()
        latest = self.advance_target()
        self.prepare(continuation={self.repo: self.original})
        with self.assertRaisesRegex(ValueError, "当前完整 SHA"):
            self.import_versions(self.version_payload(self.original))
        rw._write_leases(self.product, [])
        with self.assertRaisesRegex(ValueError, "租约"):
            self.import_versions(self.version_payload(latest))
        self.assertNotIn("issue_version_plan", self.read()["facts"])


if __name__ == "__main__":
    unittest.main()
