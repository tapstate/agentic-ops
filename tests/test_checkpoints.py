#!/usr/bin/env python3
"""流程检查点：重复请求、旧 run、并发写入与 Hook 迁移，不访问外部服务。"""
import copy
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bootstrap"))
from workflow import authorization, jira_status, task, task_store
from station_fixture import save_task as save_station_task
from agent_registry import discover
from render import check_checkpoint_migration
from workspace_paths import WorkspaceDirectory


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ao-checkpoints-")
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        task_store._write_json_atomic(self.base / ".agenticops/workspace.json",
                                     {"product_root": str(ROOT), "project": "tapdata"})
        self.state = {"issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "technical_task",
                      "stage": "waiting_takeover", "facts": {}, "repositories": [], "pending": None, "history": []}
        self.save()

        self.q1_digest = mock.patch.object(authorization.quality, "q1_digest", return_value="fixture-q1-digest")
        self.q2_digest = mock.patch.object(authorization.quality, "q2_digest", return_value="fixture-q2-digest")
        self.q1_digest.start()
        self.q2_digest.start()
        self.addCleanup(self.q1_digest.stop)
        self.addCleanup(self.q2_digest.stop)
        completion = mock.patch("workflow.station.completion_proof", return_value={"verified": True})
        completion.start()
        self.addCleanup(completion.stop)

    def save(self):
        save_station_task(self.base, self.state)

    def read(self):
        return task_store.read_task(self.base, "TAP-123")

    def args(self, **kwargs):
        values = dict(dir=str(self.base), issue_key="TAP-123", expected_run_id=self.state["run_id"],
                      expected_stage=self.state["stage"], note="test checkpoint")
        values.update(kwargs)
        return SimpleNamespace(**values)

    def confirmation(self, stage="ci_validation", expired=False):
        self.state.update(stage=stage, facts={"fix_plan": "unchanged"}, repositories=[{
            "repository": "tapdata/tapdata", "authorized_endpoint": "github.com/tapdata/tapdata",
            "base_branch": "develop", "work_branch": "fix/TAP-123", "base_sha": "a" * 40,
            "approved_scope": "test", "verification_method": "test"}])
        self.save()
        record = {"scope": "task_execution", "status": "active", "issue_key": "TAP-123",
                  "agentic_run_id": self.state["run_id"], "agent_id": "reviewer", "approved_plan_version": "v1",
                  "approved_plan_digest": authorization.plan_digest(self.state),
                  "approved_q1_digest": "fixture-q1-digest", "approved_q2_digest": "fixture-q2-digest",
                  "repositories": authorization.repository_bindings(self.state["repositories"]),
                  "expires_at_epoch": time.time() + (-1 if expired else 3600)}
        self.auth_path = task_store.authorization_path(self.base, "TAP-123")
        task_store._write_json_atomic(self.auth_path, record)
        return record

    def renewal(self, record, **kwargs):
        return self.args(expected_authorization_digest=authorization.record_digest(record), ttl_hours=8,
                         confirmed_by="reviewer", confirmation_ref="fixture:explicit-human-confirmation", **kwargs)

    def test_completion_state_write_failure_does_not_revoke_confirmation(self):
        record = self.confirmation()
        before = self.read()
        with mock.patch.object(task.quality, "advance_problems", return_value=[]), \
                mock.patch.object(task, "save", side_effect=OSError("state write failed")):
            with self.assertRaises(OSError):
                task.cmd_advance(self.args())
        self.assertEqual(self.read(), before)
        self.assertEqual(json.loads(self.auth_path.read_text()), record)
        with mock.patch.object(task.quality, "advance_problems", return_value=[]):
            self.assertEqual(task.cmd_advance(self.args()), 0)

    def test_completion_retry_finishes_each_partial_write_without_rechecking(self):
        for module, name in ((task, "revoke_authorization"),):
            with self.subTest(failed_write=name):
                self.confirmation()
                with mock.patch.object(task.quality, "advance_problems", return_value=[]), \
                        mock.patch.object(module, name, side_effect=OSError("interrupted")):
                    with self.assertRaises(OSError):
                        task.cmd_advance(self.args())
                self.assertEqual(self.read()["stage"], "completed")
                self.assertEqual(self.read()["outcome"], "completed")
                with mock.patch.object(task, "_check_advance", side_effect=AssertionError("must not recheck")):
                    self.assertEqual(task.cmd_advance(self.args()), 0)
                    before = self.auth_path.read_bytes(), self.read(), task_store.current_path(self.base).read_bytes()
                    self.assertEqual(task.cmd_advance(self.args()), 0)
                    self.assertEqual(before, (self.auth_path.read_bytes(), self.read(), task_store.current_path(self.base).read_bytes()))
                self.assertEqual(task_store.task_status(self.base, "TAP-123"), "completed")
                self.assertEqual(len(self.read()["history"]), 1)
                with self.assertRaises(ValueError):
                    task.cmd_advance(self.args(expected_run_id="run-old"))

    def test_expired_confirmation_renews_without_changing_run_or_evidence(self):
        for stage in ("implementation", "pr_review", "ci_validation"):
            with self.subTest(stage=stage):
                record = self.confirmation(stage, expired=True)
                before = task_store.task_path(self.base, "TAP-123").read_bytes()
                self.assertEqual(authorization.cmd_renew(self.renewal(record)), 0)
                self.assertEqual(task_store.task_path(self.base, "TAP-123").read_bytes(), before)
                renewed = json.loads(self.auth_path.read_text())
                self.assertGreater(renewed["expires_at_epoch"], time.time())
                self.assertEqual({k: renewed[k] for k in record if k != "expires_at_epoch"},
                                 {k: v for k, v in record.items() if k != "expires_at_epoch"})
                self.assertEqual(renewed["renewals"][0]["confirmation_ref"], "fixture:explicit-human-confirmation")
        with mock.patch.object(task.quality, "advance_problems", return_value=[]):
            self.assertEqual(task.cmd_advance(self.args()), 0)

    def test_renewal_rejects_changed_plan_repository_run_and_revoked_record(self):
        for change in ("plan", "scope", "base", "endpoint", "run", "revoked", "legacy", "completed", "archived"):
            with self.subTest(change=change):
                self.state.update(outcome="in_progress", archive_ref=None)
                record = self.confirmation(expired=True)
                args = self.renewal(record)
                if change == "plan": self.state["facts"]["fix_plan"] = "changed"
                elif change in ("scope", "base", "endpoint"):
                    field = {"scope": "approved_scope", "base": "base_sha", "endpoint": "authorized_endpoint"}[change]
                    self.state["repositories"][0][field] = "b" * 40 if change == "base" else "changed"
                elif change == "run": self.state["run_id"] = "run-fedcba987654"
                elif change == "revoked": record["status"] = "revoked"
                elif change == "legacy": record.pop("approved_plan_digest")
                elif change == "completed": self.state["stage"] = "completed"
                else: self.state["archive_ref"] = {"path": "archive/fixture", "digest": "a" * 64}
                self.save()
                task_store._write_json_atomic(self.auth_path, record)
                args.expected_authorization_digest = authorization.record_digest(record)
                before = self.auth_path.read_bytes(), self.read()
                with self.assertRaises(ValueError): authorization.cmd_renew(args)
                self.assertEqual(before, (self.auth_path.read_bytes(), self.read()))

    def test_renewal_replay_and_concurrent_requests_only_extend_once(self):
        record = self.confirmation(expired=True)
        request = self.renewal(record)
        def renew():
            try: return authorization.cmd_renew(copy.copy(request))
            except ValueError: return "stale"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: renew(), range(2))), [0, "stale"])
        self.assertEqual(renew(), "stale")
        self.assertEqual(len(json.loads(self.auth_path.read_text())["renewals"]), 1)

    def test_renewal_requires_confirmation_valid_ttl_and_atomic_write(self):
        record = self.confirmation(expired=True)
        before = self.auth_path.read_bytes()
        for field, value in (("confirmed_by", " "), ("confirmation_ref", ""),
                             ("ttl_hours", float("nan")), ("ttl_hours", float("inf")), ("ttl_hours", 0)):
            args = self.renewal(record)
            setattr(args, field, value)
            with self.assertRaises(ValueError): authorization.cmd_renew(args)
            self.assertEqual(self.auth_path.read_bytes(), before)
        with mock.patch.object(task_store, "_write_json_atomic", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError): authorization.cmd_renew(self.renewal(record))
        self.assertEqual(self.auth_path.read_bytes(), before)

    def test_renewal_rejects_missing_null_or_changed_quality_confirmation(self):
        for field, value in (("approved_q1_digest", None), ("approved_q2_digest", None),
                             ("approved_q1_digest", "changed"), ("approved_q2_digest", "changed")):
            with self.subTest(field=field, value=value):
                record = self.confirmation(expired=True)
                record[field] = value
                task_store._write_json_atomic(self.auth_path, record)
                with self.assertRaises(ValueError):
                    authorization.cmd_renew(self.renewal(record))

    def test_renewal_cli_digest_and_explicit_confirmation(self):
        record = self.confirmation(expired=True)
        def cli(*arguments):
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(sys, "argv", ["authorization.py", *arguments,
                                                    "--issue-key", "TAP-123", "--dir", str(self.base)]), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    code = authorization.main()
                except SystemExit as error:
                    code = error.code
            return SimpleNamespace(returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())
        shown = cli("show", "--digest")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(shown.stdout.strip(), authorization.record_digest(record))
        arguments = ["renew", "--expected-run-id", self.state["run_id"],
                     "--expected-authorization-digest", shown.stdout.strip(), "--confirmed-by", "reviewer"]
        self.assertEqual(cli(*arguments).returncode, 2)
        self.assertEqual(cli(*arguments, "--confirmation-ref", "fixture:confirmed").returncode, 0)
        self.assertEqual(cli(*arguments, "--confirmation-ref", "fixture:confirmed").returncode, 2)

    def test_reconfirm_preserves_scope_expiry_history_and_rejects_replay(self):
        record = self.confirmation("pr_review")
        before = self.read()
        args = self.renewal(record, expected_q2_digest="confirmed-manual-q2")
        with mock.patch.object(authorization.quality, "q2_digest", return_value=args.expected_q2_digest):
            self.assertEqual(authorization.cmd_reconfirm(args), 0)
            updated = json.loads(self.auth_path.read_text())
            self.assertEqual(self.read(), before)
            for key in record:
                if key != "approved_q2_digest": self.assertEqual(updated[key], record[key])
            self.assertEqual(updated["approved_q2_digest"], args.expected_q2_digest)
            self.assertEqual(updated["reconfirmations"][0]["previous_q2_digest"], record["approved_q2_digest"])
            with self.assertRaises(ValueError): authorization.cmd_reconfirm(args)
            second = self.renewal(updated, expected_q2_digest="confirmed-second-q2")
            with mock.patch.object(authorization.quality, "q2_digest", return_value=second.expected_q2_digest):
                self.assertEqual(authorization.cmd_reconfirm(second), 0)
            self.assertEqual(json.loads(self.auth_path.read_text())["reconfirmations"][0], updated["reconfirmations"][0])

    def test_reconfirm_rejects_invalid_confirmation_and_changed_bindings_without_writes(self):
        for change in ("expired", "revoked", "run", "plan", "scope", "branch", "q1", "legacy",
                       "unconfirmed", "target", "old_digest", "same", "proof", "history", "archived"):
            with self.subTest(change=change):
                self.state.pop("archive_ref", None)
                record = self.confirmation("pr_review", expired=change == "expired")
                args = self.renewal(record, expected_q2_digest="new-q2")
                if change == "revoked": record["status"] = "revoked"
                elif change == "run": args.expected_run_id = "run-other"
                elif change == "plan": self.state["facts"]["fix_plan"] = "changed"
                elif change == "scope": self.state["repositories"][0]["approved_scope"] = "expanded"
                elif change == "branch": self.state["repositories"][0]["work_branch"] = "other"
                elif change == "q1": record["approved_q1_digest"] = "other"
                elif change == "legacy": record.pop("approved_q2_digest")
                elif change == "target": args.expected_q2_digest = "wrong"
                elif change == "same": record["approved_q2_digest"] = "new-q2"
                elif change == "proof": args.confirmation_ref = " "
                elif change == "history": record["reconfirmations"] = {}
                elif change == "archived": self.state["archive_ref"] = {"path": "archive/fixture", "digest": "a" * 64}
                self.save()
                task_store._write_json_atomic(self.auth_path, record)
                args.expected_authorization_digest = "wrong" if change == "old_digest" else authorization.record_digest(record)
                before = self.auth_path.read_bytes(), self.read()
                with mock.patch.object(authorization.quality, "q2_digest", return_value="new-q2",
                                       side_effect=ValueError("Q2 not confirmed") if change == "unconfirmed" else None):
                    with self.assertRaises(ValueError): authorization.cmd_reconfirm(args)
                self.assertEqual(before, (self.auth_path.read_bytes(), self.read()))

    def test_reconfirm_q1_requires_explicit_current_digest_and_preserves_original_scope(self):
        record = self.confirmation("ci_validation")
        args = self.renewal(record, expected_q2_digest="new-q2")
        before = self.auth_path.read_bytes()
        with mock.patch.object(authorization.quality, "q1_digest", return_value="new-q1"), \
                mock.patch.object(authorization.quality, "q2_digest", return_value="new-q2"):
            with self.assertRaises(ValueError): authorization.cmd_reconfirm(args)
            args.expected_q1_digest = "stale-q1"
            with self.assertRaises(ValueError): authorization.cmd_reconfirm(args)
            self.assertEqual(self.auth_path.read_bytes(), before)
            args.expected_q1_digest = "new-q1"
            with mock.patch.object(authorization.quality, "q1_digest", side_effect=ValueError("unconfirmed Q1")):
                with self.assertRaises(ValueError): authorization.cmd_reconfirm(args)
            self.state["repositories"][0]["approved_scope"] = "expanded"
            self.save()
            with self.assertRaises(ValueError): authorization.cmd_reconfirm(args)
            self.state["repositories"][0]["approved_scope"] = "test"
            self.save()
            self.assertEqual(authorization.cmd_reconfirm(args), 0)
        updated = json.loads(self.auth_path.read_text())
        for key in record:
            if key not in ("approved_q1_digest", "approved_q2_digest"):
                self.assertEqual(updated[key], record[key])
        self.assertEqual(updated["reconfirmations"][0]["previous_q1_digest"], record["approved_q1_digest"])
        self.assertEqual(updated["approved_q1_digest"], "new-q1")

    def test_missing_evidence_keeps_state_and_confirmation_unchanged(self):
        self.state["stage"] = "implementation"
        self.save()
        before = task_store.task_path(self.base, "TAP-123").read_bytes()
        self.assertEqual(task.cmd_advance(self.args()), 3)
        self.assertEqual(task_store.task_path(self.base, "TAP-123").read_bytes(), before)
        self.assertFalse(task_store.authorization_path(self.base, "TAP-123").exists())

    def test_missing_watermark_does_not_block_local_intake(self):
        self.assertEqual(task.cmd_advance(self.args()), 0)
        self.assertEqual(self.read()["stage"], "task_intake")

    def test_duplicate_and_concurrent_advance_cannot_skip_stage(self):
        request = self.args()
        def advance():
            try:
                return task.cmd_advance(copy.copy(request))
            except ValueError:
                return "stale"
        # 门禁内容由集成流程测试覆盖；这里隔离持锁阶段 CAS 和重复交付。
        with mock.patch.object(task, "_check_advance", return_value=[]):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: advance(), range(2)))
            self.assertCountEqual(results, [0, "stale"])
            self.assertEqual(advance(), "stale")
        self.assertEqual(self.read()["stage"], "task_intake")
        self.assertEqual(len(self.read()["history"]), 1)

    def test_concurrent_record_does_not_lose_other_facts(self):
        def record(index):
            return task.cmd_record(self.args(key="fact_%s" % index, value="value", force=True))
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(record, range(12))), [0] * 12)
        self.assertEqual(len(self.read()["facts"]), 12)
        self.assertEqual(len(self.read()["history"]), 12)

    def test_old_run_cannot_record_block_or_change_confirmation(self):
        commands = [
            (task.cmd_record, dict(key="anything", value="value", force=True)),
            (task.cmd_block, dict(reason="old request")),
            (task.cmd_repository_add, dict(expected_revision=self.read()["_revision"], operation_id="op-stale",
                 repo="tapdata/tapdata", work_branch="fix/stale", base_branch="develop", scope="fixture", verification="test",
                 expected_head=None, historical_base=None)),
            (task.cmd_repository_record, {}),
            (authorization.cmd_grant, {}), (authorization.cmd_revoke, {}),
        ]
        before = self.read()
        for function, extra in commands:
            with self.subTest(function=function.__name__):
                with self.assertRaisesRegex(ValueError, "run 已变化"):
                    function(self.args(expected_run_id="run-old", **extra))
                self.assertEqual(self.read(), before)

    def test_cli_does_not_fill_missing_run_or_stage(self):
        for arguments in (["record", "--key", "x", "--value", "y"],
                          ["advance", "--note", "repeat", "--expected-run-id", self.state["run_id"]]):
            result = subprocess.run([sys.executable, str(ROOT / "workflow/task.py"), *arguments,
                                     "--issue-key", "TAP-123", "--dir", str(self.base)],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(self.read(), self.state)

    def test_waiting_old_run_writer_cannot_overwrite_replacement(self):
        with task_store.task_run_lock(self.base, "TAP-123"):
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "workflow/task.py"), "record", "--key", "old_fact",
                 "--value", "old", "--force", "--issue-key", "TAP-123", "--dir", str(self.base),
                 "--expected-run-id", self.state["run_id"]], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=0.15)
                self.state["run_id"] = "run-fedcba987654"
                self.save()
            except BaseException:
                process.kill()
                process.communicate()
                raise
        _, error = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 2, error)
        self.assertEqual(self.read(), self.state)

    def test_followup_checkpoint_rechecks_confirmation_run_and_plan(self):
        self.state.update(stage="implementation", facts={"fix_plan": "original", "verification": "mvn test PASS"})
        self.save()
        spec = task.admission(self.base)
        auth = {"issue_key": "TAP-123", "agentic_run_id": "run-old", "repositories": [],
                "approved_plan_digest": authorization.plan_digest(self.state)}
        with mock.patch.object(task.engine, "load_authorization_for_issue", return_value=(auth, None)), \
                mock.patch.object(task.engine, "check_authorization", return_value=(True, [])):
            self.assertTrue(any("run" in p for p in task._check_advance(self.state, "pr_review", self.base, spec)))
            auth["agentic_run_id"] = self.state["run_id"]
            self.state["facts"]["fix_plan"] = "changed"
            self.assertTrue(any("方案已变化" in p for p in task._check_advance(self.state, "pr_review", self.base, spec)))

    def test_unknown_jira_outcome_can_converge_by_readback_without_new_attempt(self):
        self.state.update(stage="task_intake")
        self.save()
        sync = {"schema_version": 1, "issue_key": "TAP-123", "run_id": self.state["run_id"],
                "attempts": {"takeover": {"outcome": "unknown", "target_status": "In Progress"}}}
        snapshot = {"source_ref": "fixture:readback", "issue": {"key": "TAP-123", "fields": {
            "status": {"id": "3", "name": "In Progress"}}}}
        with mock.patch.object(jira_status, "load_state", return_value=sync), \
                mock.patch.object(jira_status, "save_state") as saved:
            result = jira_status.complete(self.base, "TAP-123", "takeover", "unknown", snapshot, "")
        self.assertEqual(result["outcome"], "succeeded")
        saved.assert_called_once()
        self.assertEqual(self.read()["stage"], "task_intake")

    def test_builtin_agents_do_not_generate_retired_hooks(self):
        for manifest in discover(ROOT).values():
            generated = {item["target"] for item in manifest["artifacts"]}
            self.assertTrue(manifest["retired_artifacts"])
            self.assertFalse(generated & set(manifest["retired_artifacts"]))

    def test_migration_requires_confirmation_and_checks_all_owned_files(self):
        manifests = {"custom": {"retired_artifacts": [".custom/one", ".custom/two"]}}
        directory = self.base / ".custom"
        directory.mkdir()
        owned = {}
        for name in ("one", "two"):
            (directory / name).write_text("generated")
            owned[".custom/" + name] = {"kind": "file", "sha256": hashlib.sha256(b"generated").hexdigest()}
        with WorkspaceDirectory(self.base) as tree:
            with self.assertRaisesRegex(ValueError, "显式迁移"):
                check_checkpoint_migration(owned, manifests, False, tree)
        (directory / "two").write_text("user changes")
        with WorkspaceDirectory(self.base) as tree:
            with self.assertRaisesRegex(ValueError, "已被修改"):
                check_checkpoint_migration(owned, manifests, True, tree)
        self.assertEqual((directory / "one").read_text(), "generated")
        self.assertEqual((directory / "two").read_text(), "user changes")


if __name__ == "__main__":
    unittest.main()
