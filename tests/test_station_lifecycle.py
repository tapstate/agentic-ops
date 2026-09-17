#!/usr/bin/env python3
"""单工位顺序复用、归档不可变与失败占用的真实 Git 回归。"""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import engineering_baseline as baseline, station, station_archive, station_operation, task_store
from internal.tests.timing import TimedTestRunner


class StationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ws = self.root / "ws"
        self.product = self.root / "product"
        shutil.copytree(ROOT / "projects", self.product / "projects")
        shutil.copytree(ROOT / "contracts", self.product / "contracts")
        shutil.copytree(ROOT / "policies", self.product / "policies")
        (self.ws / ".agenticops").mkdir(parents=True)
        for name in ("source", "config", "runtime", "archive"):
            (self.ws / name).mkdir()
        self.write(self.ws / ".agenticops/station.json", {"schema_version": 4, "product_root": str(self.product), "source_pool": str(self.root / "pool"), "project": "tapdata", "station_id": "a" * 32, "branch_identity": {"schema_version": 1, "git_name": "Test", "source": "git_global_user_name"}})
        self.write(self.ws / ".agenticops/init.json", {"station_state_epoch": 8})
        task_store.initialize_current(self.ws)

    def prepare_engineering(self, count=1):
        """显式选择临时工程规模；不改正式项目、不共享可变仓库。"""
        self.assertFalse(hasattr(self, "seed"), "工程夹具只能准备一次")
        self.seed = self.root / "seed"
        self.seed.mkdir()
        self.git(self.seed, "init", "-b", "develop")
        self.git(self.seed, "config", "user.email", "test@example.com")
        self.git(self.seed, "config", "user.name", "Test")
        (self.seed / "file.txt").write_text("baseline\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "initial")
        self.git(self.seed, "branch", "main")
        self.remote = self.root / "remote.git"
        self.git(self.root, "clone", "--bare", str(self.seed), str(self.remote))
        catalog_path = self.product / "projects/tapdata/repositories.json"
        catalog = json.loads(catalog_path.read_text())
        for entry in catalog["repositories"].values():
            entry["origin"] = str(self.remote)
            entry["dev_branch"] = "develop"
        self.write(catalog_path, catalog)
        profile_path = self.product / "projects/tapdata/engineering-profiles.json"
        profiles = json.loads(profile_path.read_text())
        profile = profiles["profiles"]["full-application"]
        profile["repositories"] = profile["repositories"][:count]
        self.write(profile_path, profiles)
        self.request = {"issue_key": "TAP-123", "task_class": "technical_task", "version": "develop", "profile": "full-application",
                        "explicit_branches": {name: "develop" for name in profile["repositories"]}}

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def git(self, path, *args):
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def takeover(self, operation="op-takeover-one"):
        if not hasattr(self, "seed"):
            self.prepare_engineering()
        revision = task_store.read_current(self.ws)["revision"]
        station.takeover(self.ws, self.request, operation, revision)
        task = task_store.read_task(self.ws)
        return task

    def new_contract(self, task):
        task["facts"]["station_contract"] = 3
        task_store.write_task(self.ws, task)
        return task

    def execute(self, base, kind, issue, run, revision, operation, request):
        from workflow import station_resources
        task = task_store.read_task(base)
        if task:
            request.setdefault("decision_ref", "fixture:user-continue")
            if "confirmed_digest" not in request:
                request["confirmed_digest"] = station_resources.plan(base, task)["digest"]
        return station.execute(base, kind, issue, run, revision, operation, request)

    def test_takeover_full_engineering_and_idempotency(self):
        self.prepare_engineering(9)
        task = self.takeover()
        self.assertEqual(len(task["engineering_baseline"]["repositories"]), 9)
        self.assertTrue(task["source_prepared"])
        for name in task["engineering_baseline"]["repositories"]:
            self.assertTrue((self.ws / "source" / name / ".git").is_dir())
        station.takeover(self.ws, self.request, "op-takeover-one", 0)
        self.assertEqual(task_store.read_task(self.ws)["run_id"], task["run_id"])
        with self.assertRaisesRegex(ValueError, "当前任务"):
            station.takeover(self.ws, self.request, "op-takeover-two", task["_revision"])

    def test_cleanup_requires_user_decision_and_preflight_is_read_only(self):
        from workflow import station_resources
        task = self.new_contract(self.takeover())
        before = {p: p.read_bytes() for p in (self.ws / ".agenticops").rglob("*") if p.is_file()}
        preflight = station_resources.preflight(self.ws, task)
        self.assertTrue(preflight["decision_required"])
        with self.assertRaisesRegex(ValueError, "重置范围"):
            station.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-no-decision", {"summary": "停止", "reason": "取消"})
        self.assertEqual(before, {p: p.read_bytes() for p in (self.ws / ".agenticops").rglob("*") if p.is_file()})

    def test_readiness_tracks_remote_advance_dirty_and_divergence(self):
        self.prepare_engineering(2)
        from workflow import station_source
        task = self.new_contract(self.takeover())
        untouched = self.other_repository_state()
        station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-ready-scope", "tapdata/tapdata", None, "develop", ["file.txt"], "unit")
        task = task_store.read_task(self.ws)
        with self.assertRaisesRegex(ValueError, "source-readiness"):
            station_source.require_readiness(self.ws, task)
        record = station_source.prepare_readiness(self.ws, task)
        self.assertEqual(station_source.require_readiness(self.ws, task), record["snapshot"]["digest"])
        path = self.ws / "source/tapdata/tapdata/file.txt"
        path.write_text("uncommitted")
        with self.assertRaisesRegex(ValueError, "未提交"):
            station_source.require_readiness(self.ws, task)
        path.write_text("baseline\n")
        (self.seed / "file.txt").write_text("upstream\n")
        self.git(self.seed, "commit", "-am", "upstream")
        self.git(self.seed, "push", str(self.remote), "develop")
        with self.assertRaisesRegex(ValueError, "远端引用变化"):
            station_source.require_readiness(self.ws, task)
        record = station_source.prepare_readiness(self.ws, task)
        with self.assertRaisesRegex(ValueError, "目标分支已推进"):
            station_source.require_readiness(self.ws, task)
        record.update(accepted_digest=record["snapshot"]["digest"], decision_ref="fixture:accept-old-base")
        task_store._write_json_atomic(self.ws / ".agenticops/evidence/source-readiness.json", record)
        self.assertEqual(station_source.require_readiness(self.ws, task), record["snapshot"]["digest"])
        self.git(self.seed, "checkout", "--orphan", "unrelated")
        self.git(self.seed, "commit", "-am", "different root")
        self.git(self.seed, "push", "--force", str(self.remote), "HEAD:develop")
        with self.assertRaisesRegex(ValueError, "分叉或回退"):
            station_source.prepare_readiness(self.ws, task)
        self.assertEqual(self.other_repository_state(), untouched)

    def test_new_run_archives_and_resets_with_one_confirmation(self):
        from workflow import station_resources
        task = self.takeover()
        plan = station_resources.plan(self.ws, task)
        request = {"summary": "尚未编码", "reason": "用户取消", "decision_ref": "fixture:user",
                   "confirmed_digest": plan["digest"]}
        station.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-new-clean", request)
        self.assertIsNone(task_store.read_task(self.ws))
        record = json.loads((self.ws / "archive" / task["issue_key"] / task["run_id"] / "record.json").read_text())
        self.assertEqual(record["task_result"], "incomplete")
        self.assertTrue((self.ws / "runtime").is_dir())
        self.assertEqual(list((self.ws / "runtime").iterdir()), [])

    def test_scope_binding_and_existing_branch_rejected(self):
        task = self.takeover()
        with self.assertRaisesRegex(ValueError, "必须使用工位 git_name"):
            station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-scope-wrong", "tapdata/tapdata", "fix/test", "develop", ["file.txt"], "unit tests")
        station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-scope-one", "tapdata/tapdata", None, "develop", ["file.txt"], "unit tests")
        current = task_store.read_current(self.ws)["current"]
        self.assertNotIn("repositories", current)
        expected = "Test/" + task["run_id"]
        self.assertEqual(current["task_repositories"]["tapdata/tapdata"]["work_branch"], expected)
        self.assertEqual(self.git(self.ws / "source/tapdata/tapdata", "branch", "--show-current"), expected)

    def test_incomplete_archive_does_not_complete_or_unbind(self):
        self.prepare_engineering()
        task = self.takeover()
        from workflow import station_resources
        with mock.patch.object(station_resources, "clean", side_effect=AssertionError("archive does not reset")):
            self.execute(self.ws, "archive", task["issue_key"], task["run_id"], task["_revision"], "op-archive-one", {"summary":"处理到定位；尚未修改。","reason":"研发决定停止"})
        current = task_store.read_task(self.ws)
        self.assertEqual(current["outcome"], "in_progress")
        record = station_archive.verify(self.ws, current["archive_ref"], current)
        self.assertEqual(record["task_result"], "incomplete")
        binding_path = self.ws / ".agenticops/station.json"
        binding = json.loads(binding_path.read_text())
        binding["station_id"] = "b" * 32
        self.write(binding_path, binding)
        with self.assertRaisesRegex(ValueError, "工位"):
            station_archive.verify(self.ws, current["archive_ref"], current)
        evidence = json.loads((self.ws / current["archive_ref"]["path"] / "evidence.json").read_text())
        self.assertEqual(len(evidence["current"]["engineering_baseline"]["repositories"]), 1)
        self.assertNotIn("repositories", evidence["current"])
        with self.assertRaisesRegex(ValueError, "归档"):
            task_store.require_development(self.ws, current)

    def test_wrong_confirmation_keeps_current(self):
        task = self.takeover()
        resources = mock.Mock()
        resources.plan.return_value = {"run_id": task["run_id"], "entries": [], "digest": "plan"}
        with mock.patch.object(station, "_resources", return_value=resources):
            with self.assertRaisesRegex(ValueError, "确认"):
                self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-clean-one", {"summary": "未完成", "reason": "取消", "confirmed_digest": "wrong"})
        self.assertEqual(task_store.read_task(self.ws)["run_id"], task["run_id"])
        resources.clean.assert_not_called()

    def test_clean_then_new_takeover_retains_archive_and_refs(self):
        task = self.takeover()
        self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-clean-one", {"summary":"未完成", "reason":"取消"})
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertTrue((self.ws / "archive" / task["issue_key"] / task["run_id"] / "record.json").is_file())
        next_task=self.takeover("op-takeover-next")
        self.assertNotEqual(next_task["run_id"],task["run_id"])

    def test_cleanup_failure_keeps_interrupted_occupied(self):
        from workflow import station_resources
        task = self.takeover()
        with mock.patch.object(station_resources,"clean",side_effect=ValueError("资源指纹变化")), self.assertRaisesRegex(ValueError,"指纹"):
            self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-clean-one",{"summary":"未完成","reason":"取消"})
        self.assertEqual(task_store.read_task(self.ws)["outcome"],"interrupted")
        self.assertNotEqual(station_operation.read(self.ws)["status"],"done")

    def test_unbind_crash_recovers_without_cleaning_again(self):
        from workflow import station_resources
        task=self.takeover(); request={"summary":"未完成","reason":"取消"}
        original=station_operation.receipt
        def crash(base,operation,name,value):
            if name=="unbind": raise OSError("crash after CAS")
            return original(base,operation,name,value)
        with mock.patch.object(station_operation,"receipt",side_effect=crash), self.assertRaises(OSError):
            self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-clean-crash",request)
        self.assertIsNone(task_store.read_task(self.ws))
        with mock.patch.object(station_resources,"clean",side_effect=AssertionError("no replay")):
            self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-clean-crash",request)
        self.assertEqual(station_operation.read(self.ws)["status"],"done")

    def test_incomplete_archive_reused_by_clean(self):
        task=self.takeover(); request={"summary":"未完成","reason":"取消"}
        self.execute(self.ws,"archive",task["issue_key"],task["run_id"],task["_revision"],"op-archive-only",request)
        task=task_store.read_task(self.ws)
        path=self.ws/task["archive_ref"]["path"]/"record.json"; before=path.read_bytes()
        self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-clean-later",request)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(json.loads(before)["task_result"],"incomplete")

    def test_failed_takeover_can_handoff_to_clean(self):
        self.prepare_engineering()
        with mock.patch.object(station.source,"prepare_repositories",side_effect=ValueError("network")), self.assertRaises(ValueError):
            station.takeover(self.ws,self.request,"op-takeover-fail",0)
        task=task_store.read_task(self.ws)
        self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-clean-partial",{"summary":"网络失败","reason":"取消"})
        self.assertEqual(station_operation.read(self.ws)["supersedes"]["operation_id"],"op-takeover-fail")
        self.assertIsNone(task_store.read_task(self.ws))

    def test_completion_requires_merged_head_and_quality(self):
        task = self.takeover()
        task["stage"] = "ci_validation"
        from workflow import task as task_cli
        with mock.patch.object(task_cli, "_check_advance", return_value=["质量检查未通过"]):
            with self.assertRaisesRegex(ValueError, "质量"):
                station.completion_proof(self.ws, task)
        task["task_repositories"]["tapdata/tapdata"] = baseline.task_repository(task["engineering_baseline"], "tapdata/tapdata", "fix/x", "develop", ["file.txt"], "test")
        path = self.ws / "source/tapdata/tapdata"
        self.git(path, "checkout", "-b", "fix/x")
        self.git(path, "config", "user.email", "test@example.com")
        self.git(path, "config", "user.name", "Test")
        (path / "file.txt").write_text("changed\n")
        self.git(path, "add", ".")
        self.git(path, "commit", "-m", "changed")
        with self.assertRaisesRegex(ValueError, "唯一有效合并"):
            station.completion_proof(self.ws, task)
        head = self.git(path, "rev-parse", "HEAD")
        task["task_repositories"]["tapdata/tapdata"]["deliveries"] = [{"repository": "tapdata/tapdata", "pr": "123",
            "target_branch": "develop", "candidate_head": head, "pr_head": head,
            "merged_at": "2026-09-15T01:00:00Z", "merge_commit": head, "readback_ref": "fixture:merged-pr"}]
        with mock.patch.object(task_cli, "_check_advance", return_value=[]):
            proof = station.completion_proof(self.ws, task)
        self.assertEqual(task["task_repositories"]["tapdata/tapdata"]["disposition"], "merged")
        self.assertEqual(proof["dispositions"]["tapdata/tapdata"], "merged")

    def test_real_resources_clean_and_sequential_takeover(self):
        self.prepare_engineering()
        from workflow import station_resources
        task = self.takeover()
        path = self.ws / "runtime/logs/build.log"
        path.parent.mkdir()
        path.write_text("test build output")
        station_resources.register(self.ws, task["issue_key"], task["run_id"], [{"kind": "file", "path": "runtime/logs/build.log", "producer": "test-build"}])
        plan = station_resources.plan(self.ws, task)
        self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-real-clean", {"summary": "准备后停止，无代码修改", "reason": "取消", "confirmed_digest": plan["digest"]})
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual(list((self.ws / "runtime").iterdir()), [])
        self.assertFalse((self.ws / ".agenticops/evidence").exists())
        self.assertNotEqual(self.takeover("op-next-real")["run_id"], task["run_id"])

    def test_release_keeps_completed_fact_after_neutral_crash(self):
        self.prepare_engineering()
        from workflow import station_resources, task as task_cli
        task = self.takeover()
        task["stage"] = "ci_validation"
        task_store.write_task(self.ws, task)
        with mock.patch.object(task_cli, "_check_advance", return_value=[]):
            proof = station.completion_proof(self.ws, task)
        task.update(outcome="completed", stage="completed", terminal_proof=proof)
        task_store.write_task(self.ws, task)
        plan = station_resources.plan(self.ws, task)
        request = {"summary": "已完成验证，无代码差异", "reason": "释放", "candidate_digest": proof["candidate_digest"], "confirmed_digest": plan["digest"]}
        with mock.patch.object(station_resources, "neutral", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                self.execute(self.ws, "release", task["issue_key"], task["run_id"], task["_revision"], "op-release-crash", request)
        self.assertEqual(task_store.read_task(self.ws)["outcome"], "completed")
        self.execute(self.ws, "release", task["issue_key"], task["run_id"], task["_revision"], "op-release-crash", request)
        self.assertIsNone(task_store.read_task(self.ws))
        record = json.loads((self.ws / "archive" / task["issue_key"] / task["run_id"] / "record.json").read_text())
        self.assertEqual(record["task_result"], "completed")

    def test_explicit_continuation_preserves_historical_baseline(self):
        self.prepare_engineering()
        old = self.git(self.seed, "rev-parse", "HEAD")
        self.git(self.seed, "checkout", "-b", "fix/continued")
        (self.seed / "file.txt").write_text("previous work\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "previous work")
        head = self.git(self.seed, "rev-parse", "HEAD")
        self.git(self.seed, "push", str(self.remote), "fix/continued")
        self.request["continuations"] = {"tapdata/tapdata": {"work_branch": "fix/continued", "baseline_sha": old, "expected_head": head}}
        task = self.takeover()
        entry = task["engineering_baseline"]["repositories"]["tapdata/tapdata"]
        self.assertEqual(entry["commit_sha"], old)
        self.assertEqual(entry["resolution_source"], "explicit_continuation")
        station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-scope-continue", "tapdata/tapdata", "fix/continued", "develop", ["file.txt"], "unit tests", head)
        self.assertEqual(self.git(self.ws / "source/tapdata/tapdata", "rev-parse", "HEAD"), head)
        current = task_store.read_task(self.ws)
        self.assertEqual(current["task_repositories"]["tapdata/tapdata"]["deliveries"], [])

    def test_incomplete_archive_cannot_be_completed_by_release(self):
        task = self.takeover()
        from workflow import station_resources
        self.execute(self.ws, "archive", task["issue_key"], task["run_id"], task["_revision"], "op-archive-final", {"summary": "未完成", "reason": "停止处理"})
        current = task_store.read_task(self.ws)
        before = (self.ws / current["archive_ref"]["path"] / "record.json").read_bytes()
        plan = station_resources.plan(self.ws, current)
        with mock.patch.object(station, "completion_proof", side_effect=AssertionError("must not complete archived task")):
            with self.assertRaisesRegex(ValueError, "未完成档案"):
                self.execute(self.ws, "release", current["issue_key"], current["run_id"], current["_revision"], "op-wrong-release", {"confirmed_digest": plan["digest"], "candidate_digest": "unused"})
        self.assertEqual(task_store.read_task(self.ws)["outcome"], "in_progress")
        self.assertEqual((self.ws / current["archive_ref"]["path"] / "record.json").read_bytes(), before)

    def test_cleanup_amend_preserves_pending_step_and_cleans_new_owned_file(self):
        from workflow import station_resources
        task=self.takeover()
        first=station_resources.plan(self.ws,task)
        request={"summary":"未完成","reason":"停止","confirmed_digest":first["digest"]}
        with mock.patch.object(station_resources,"clean",side_effect=OSError("before directory reset")), self.assertRaises(OSError):
            self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-amend-clean",request)
        current=task_store.read_task(self.ws)
        record=self.ws/current["archive_ref"]["path"]/"record.json";before=record.read_bytes()
        late=self.ws/"runtime/logs/late.log";late.parent.mkdir();late.write_text("late generated output")
        # 独占目录内变化不扩权、不重算逐文件摘要，也不要求第二次确认。
        self.assertEqual(station_resources.plan(self.ws,current)["digest"],first["digest"])
        self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-amend-clean",request)
        self.assertFalse(late.exists());self.assertEqual(record.read_bytes(),before)

    def test_operation_read_rejects_damaged_done_log(self):
        self.write(self.ws / ".agenticops/operation.json", {"schema_version": 1, "status": "done", "steps": {}})
        with self.assertRaisesRegex(ValueError, "身份"):
            station_operation.read(self.ws)

    def test_operation_read_rejects_digest_mismatch_and_unfinished_done(self):
        request = {"issue_key": "TAP-123"}
        operation = station_operation.begin(self.ws, "takeover", "op-read-check", 0, request)
        operation["request"] = {"changed": True}
        station_operation.save(self.ws, operation)
        with self.assertRaisesRegex(ValueError, "摘要"):
            station_operation.read(self.ws)
        operation["request"] = request
        operation["status"] = "done"
        operation["steps"] = {"sample": {"before": {}, "expected": {}, "receipt": None}}
        station_operation.save(self.ws, operation)
        with self.assertRaisesRegex(ValueError, "未核验"):
            station_operation.read(self.ws)

    def test_cleanup_amend_after_active_evidence_clear_intent(self):
        from workflow import station_resources
        task = self.takeover()
        task_store._write_json_atomic(self.ws / ".agenticops/evidence/sample.json", {"run_id": task["run_id"]})
        first = station_resources.plan(self.ws, task)
        request = {"summary": "未完成", "reason": "停止", "confirmed_digest": first["digest"]}
        def crash(base, operation):
            path = self.ws / ".agenticops/evidence/sample.json"
            from hashlib import sha256
            station_operation.intent(base, operation, "clear-active:0:" + first["digest"], {}, {"files": {"evidence/sample.json": sha256(path.read_bytes()).hexdigest()}})
            raise OSError("clear evidence interrupted")
        with mock.patch.object(station, "_clear_active", side_effect=crash):
            with self.assertRaises(OSError):
                self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-amend-evidence", request)
        current = task_store.read_task(self.ws)
        late = self.ws / "runtime/late.log"
        late.write_text("late report")
        station_resources.register(self.ws, task["issue_key"], task["run_id"], [{"kind": "file", "path": "runtime/late.log", "producer": "fixture"}], expected_operation_id="op-amend-evidence")
        second = station_resources.plan(self.ws, current)
        station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-amend-evidence", first["digest"], {"confirmed_digest": second["digest"], "expected_plan_revision": 0})
        self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-amend-evidence", request)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertFalse((self.ws / ".agenticops/evidence").exists())

    def test_release_amend_discards_confirmed_late_file_change_before_recheck(self):
        self.prepare_engineering(2)
        from workflow import station_resources, task as task_cli
        task = self.takeover()
        untouched = self.other_repository_state()
        station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-release-scope", "tapdata/tapdata", None, "develop", ["file.txt"], "unit test")
        task = task_store.read_task(self.ws)
        task["stage"] = "ci_validation"
        with mock.patch.object(task_cli, "_check_advance", return_value=[]):
            proof = station.completion_proof(self.ws, task)
        task.update(stage="completed", outcome="completed", terminal_proof=proof)
        task_store.write_task(self.ws, task)
        first = station_resources.plan(self.ws, task)
        request = {"summary": "已完成", "reason": "释放", "confirmed_digest": first["digest"], "candidate_digest": proof["candidate_digest"]}
        with mock.patch.object(station_resources, "clean", side_effect=OSError("before cleanup")):
            with self.assertRaises(OSError):
                self.execute(self.ws, "release", task["issue_key"], task["run_id"], task["_revision"], "op-release-amend", request)
        current = task_store.read_task(self.ws)
        file = self.ws / "source/tapdata/tapdata/file.txt"
        file.write_text("late modification explicitly discarded")
        second = station_resources.plan(self.ws, current)
        entry=second["entries"][0]
        snapshot={k:entry[k] for k in ("head","before","before_index","index_patch","worktree_patch")}
        station_resources.register(self.ws,task["issue_key"],task["run_id"],[{"kind":"source-disposition","producer":"user","path":entry["path"],"preservation":{"action":"discard","snapshot":snapshot}}],expected_operation_id="op-release-amend")
        second=station_resources.plan(self.ws,current)
        station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-release-amend", first["digest"], {"confirmed_digest": second["digest"], "expected_plan_revision": 0,"discard_digest":baseline.digest(second["entries"])})
        self.execute(self.ws, "release", task["issue_key"], task["run_id"], task["_revision"], "op-release-amend", request)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual(file.read_text(), "baseline\n")
        self.assertEqual(self.other_repository_state(), untouched)

    def test_cleanup_amend_same_digest_uses_new_execution_revision(self):
        from workflow import station_resources
        task = self.takeover()
        file = self.ws / "runtime/logs/repeated.log"; file.parent.mkdir()
        file.write_text("identical regenerated output")
        station_resources.register(self.ws, task["issue_key"], task["run_id"], [{"kind": "file", "path": "runtime/logs/repeated.log", "producer": "build"}])
        plan = station_resources.plan(self.ws, task)
        request = {"summary": "未完成", "reason": "停止", "confirmed_digest": plan["digest"]}
        with mock.patch.object(station_resources, "neutral", side_effect=OSError("after cleaned")):
            with self.assertRaises(OSError):
                self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-repeat-plan", request)
        current = task_store.read_task(self.ws)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("identical regenerated output")
        self.assertEqual(station_resources.plan(self.ws, current)["digest"], plan["digest"])
        amendment = {"confirmed_digest": plan["digest"], "expected_plan_revision": 0}
        station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-repeat-plan", plan["digest"], amendment)
        station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-repeat-plan", plan["digest"], amendment)
        self.assertEqual(len(station_operation.read(self.ws)["plan_revisions"]), 1)
        self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-repeat-plan", request)
        operation = station_operation.read(self.ws)
        receipts = self.ws / "archive" / task["issue_key"] / task["run_id"] / "receipts"
        self.assertEqual(len(list(receipts.glob("op-repeat-plan-root-v*-done.json"))), 2)
        self.assertIsNone(task_store.read_task(self.ws))

    def test_cleanup_amend_before_archive_publication_preserves_old_draft(self):
        from workflow import station_resources
        task = self.takeover()
        file = self.ws / "runtime/logs/draft.log"; file.parent.mkdir()
        file.write_text("first output")
        station_resources.register(self.ws, task["issue_key"], task["run_id"], [{"kind": "file", "path": "runtime/logs/draft.log", "producer": "build"}])
        first = station_resources.plan(self.ws, task)
        request = {"summary": "未完成", "reason": "停止", "confirmed_digest": first["digest"]}
        with mock.patch.object(station_archive.os, "rename", side_effect=OSError("before formal publication")):
            with self.assertRaises(OSError):
                self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-draft-amend", request)
        current = task_store.read_task(self.ws)
        self.assertIsNone(current["archive_ref"])
        draft = self.ws / "archive" / task["issue_key"] / ("." + task["run_id"] + ".op-draft-amend.0")
        old_record = (draft / "record.json").read_bytes()
        file.write_text("changed before publication")
        second = station_resources.plan(self.ws, current)
        operation = station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-draft-amend", first["digest"], {"confirmed_digest": second["digest"], "expected_plan_revision": 0})
        self.assertEqual(operation["archive_drafts"][0]["record"], json.loads(old_record))
        self.assertNotIn("archive_record", operation)
        self.assertEqual((draft / "record.json").read_bytes(), old_record)
        self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-draft-amend", request)
        self.assertIsNone(task_store.read_task(self.ws))
        target = self.ws / "archive" / task["issue_key"] / task["run_id"]
        record = json.loads((target / "record.json").read_text())
        self.assertEqual(record["resource_inventory_digest"], second["digest"])
        self.assertTrue((target / "receipts" / ("op-draft-amend-amendment-1-" + second["digest"] + ".json")).is_file())
        self.assertEqual((draft / "record.json").read_bytes(), old_record)

    def test_cleanup_amend_recovers_published_archive_without_rewriting(self):
        self.prepare_engineering(2)
        from workflow import station_resources
        task = self.takeover()
        untouched = self.other_repository_state()
        file = self.ws / "runtime/logs/published.log"; file.parent.mkdir()
        file.write_text("first output")
        station_resources.register(self.ws, task["issue_key"], task["run_id"], [{"kind": "file", "path": "runtime/logs/published.log", "producer": "build"}])
        first = station_resources.plan(self.ws, task)
        request = {"summary": "未完成", "reason": "停止", "confirmed_digest": first["digest"]}
        original_receipt = station_operation.receipt
        def crash(base, operation, name, value):
            if name.startswith("archive-publish:"):
                raise OSError("published before receipt")
            return original_receipt(base, operation, name, value)
        with mock.patch.object(station_operation, "receipt", side_effect=crash):
            with self.assertRaises(OSError):
                self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-published-amend", request)
        current = task_store.read_task(self.ws)
        self.assertIsNone(current["archive_ref"])
        target = self.ws / "archive" / task["issue_key"] / task["run_id"]
        old_record = (target / "record.json").read_bytes()
        file.write_text("later output")
        second = station_resources.plan(self.ws, current)
        station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-published-amend", first["digest"], {"confirmed_digest": second["digest"], "expected_plan_revision": 0})
        self.assertIsNotNone(task_store.read_task(self.ws)["archive_ref"])
        self.assertEqual((target / "record.json").read_bytes(), old_record)

        self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-published-amend", request)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual((target / "record.json").read_bytes(), old_record)
        self.assertEqual(self.other_repository_state(), untouched)

    def other_repository_state(self):
        path = self.ws / "source/tapdata/tapdata-common-lib"
        return (self.git(path, "rev-parse", "HEAD"), self.git(path, "status", "--porcelain"),
                (path / "file.txt").read_bytes())

    def test_standalone_archive_refreshes_only_unpublished_draft(self):
        from workflow import station_resources
        task = self.takeover()
        file = self.ws / "runtime/logs/archive.log"; file.parent.mkdir()
        file.write_text("initial output")
        station_resources.register(self.ws, task["issue_key"], task["run_id"], [{"kind": "file", "path": "runtime/logs/archive.log", "producer": "build"}])
        request = {"summary": "未完成", "reason": "停止"}
        with mock.patch.object(station_archive.os, "rename", side_effect=OSError("unpublished")):
            with self.assertRaises(OSError):
                self.execute(self.ws, "archive", task["issue_key"], task["run_id"], task["_revision"], "op-archive-refresh", request)
        current = task_store.read_task(self.ws)
        operation = station_operation.read(self.ws)
        original_plan = operation["cleanup_plan"]["digest"]
        file.write_text("changed output")
        late = self.ws / "runtime/logs/archive-late.log"
        late.write_text("known producer late output")
        station_resources.register(self.ws, task["issue_key"], task["run_id"], [{"kind": "file", "path": "runtime/logs/archive-late.log", "producer": "build"}], expected_operation_id="op-archive-refresh")
        plan = station_resources.plan(self.ws, current)
        station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-archive-refresh", original_plan, {"expected_plan_revision": 0, "confirmed_digest": plan["digest"]})
        with mock.patch.object(station_resources, "clean", side_effect=AssertionError("archive must not delete")):
            self.execute(self.ws, "archive", task["issue_key"], task["run_id"], task["_revision"], "op-archive-refresh", request)
        archived = task_store.read_task(self.ws)
        self.assertEqual(archived["outcome"], "in_progress")
        self.assertTrue(late.is_file())
        self.assertEqual(file.read_text(), "changed output")
        operation = station_operation.read(self.ws)
        self.assertNotIn("cleanup_manifest", operation)
        self.assertEqual(operation["status"], "done")
        record = station_archive.verify(self.ws, archived["archive_ref"], archived)
        self.assertEqual(record["task_result"], "incomplete")
        self.assertEqual(record["resource_inventory_digest"], plan["digest"])

    def amend_fixture(self):
        task = self.takeover()
        task["stage"] = "task_intake"
        task_store.write_task(self.ws, task)
        station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"],
                             "op-amend-register", "tapdata/tapdata", None, "develop", ["old"], "unit")
        return task_store.read_task(self.ws)

    def amend_args(self, task, suffix="one"):
        binding = task["task_repositories"]["tapdata/tapdata"]
        return [self.ws, task["issue_key"], task["run_id"], task["_revision"],
                "op-amend-" + suffix, "tapdata/tapdata", baseline.digest(binding),
                self.git(self.ws / "source/tapdata/tapdata", "rev-parse", "HEAD"),
                ["new-" + suffix], "unit and integration", "user:confirmed-minimal-amend"]

    def test_amend_scope_preserves_binding_revokes_and_is_idempotent(self):
        task = self.amend_fixture()
        args = self.amend_args(task)
        before = task["task_repositories"]["tapdata/tapdata"]
        self.write(self.ws / ".agenticops/authorization.json", {"status": "active"})
        station.amend_scope(*args)
        after = task_store.read_task(self.ws)
        expected = dict(before, approved_scope=args[8], verification_method=args[9])
        self.assertEqual(expected, after["task_repositories"]["tapdata/tapdata"])
        self.assertEqual(task["_revision"] + 1, after["_revision"])
        auth = json.loads((self.ws / ".agenticops/authorization.json").read_text())
        self.assertEqual("revoked", auth["status"])
        station.amend_scope(*args)
        self.assertEqual(after, task_store.read_task(self.ws))
        with self.assertRaisesRegex(ValueError, "相同 operation_id"):
            station.amend_scope(*args[:-1], "different-decision")

    def test_amend_scope_rejects_changed_inputs_without_writes(self):
        task = self.amend_fixture()
        args = self.amend_args(task)
        for index, value in ((2, "TAP-123-00000000"), (3, args[3] - 1),
                             (5, "tapdata/not-registered"), (6, "0" * 64),
                             (7, "0" * 40), (8, []), (9, ""), (10, "")):
            with self.subTest(index=index):
                snapshot = {p: p.read_bytes() for p in (self.ws / ".agenticops").rglob("*") if p.is_file()}
                changed = list(args); changed[index] = value
                with self.assertRaises(ValueError):
                    station.amend_scope(*changed)
                self.assertEqual(snapshot, {p: p.read_bytes() for p in snapshot})
        no_op = list(args); no_op[8:10] = [["old"], "unit"]
        with self.assertRaisesRegex(ValueError, "未变化"):
            station.amend_scope(*no_op)
        path = self.ws / "source/tapdata/tapdata"
        for kind in ("untracked", "unstaged", "staged"):
            target = path / ("extra.txt" if kind == "untracked" else "file.txt")
            target.write_text("modified")
            if kind == "staged":
                self.git(path, "add", "file.txt")
            with self.assertRaises(ValueError):
                station.amend_scope(*args)
            if kind == "untracked":
                target.unlink()
            else:
                target.write_text("baseline\n")
                self.git(path, "add", "file.txt")
        self.git(path, "checkout", "--detach")
        with self.assertRaisesRegex(ValueError, "分支或 Head"):
            station.amend_scope(*args)

    def test_amend_scope_rejects_late_stage_or_delivery(self):
        task = self.amend_fixture()
        for stage, changes in (("implementation", {}), ("task_intake", {"deliveries": [{"pr": 1}]}),
                               ("design_review", {"observation": {"results": {"ci": "passed"}}}),
                               ("task_intake", {"disposition": "no_change"})):
            with self.subTest(stage=stage, changes=changes):
                task = task_store.read_task(self.ws)
                task["stage"] = stage
                binding = task["task_repositories"]["tapdata/tapdata"]
                binding.update(deliveries=[], observation=None, disposition="pending")
                binding.update(changes)
                task.pop("repositories", None)
                task_store.write_task(self.ws, task)
                args = self.amend_args(task)
                with self.assertRaises(ValueError):
                    station.amend_scope(*args)

    def test_amend_scope_recovers_each_durable_boundary_once(self):
        from workflow import task as task_cli
        task = self.amend_fixture()
        points = [(station_operation, "begin"), (station_operation, "intent"),
                  (task_cli, "revoke_authorization"), (task_store, "write_task"),
                  (station_operation, "receipt"), (station_operation, "finish")]
        for index, (module, name) in enumerate(points):
            with self.subTest(point=name):
                task = task_store.read_task(self.ws)
                args = self.amend_args(task, "crash-" + str(index))
                original = getattr(module, name)
                def crash(*a, **kw):
                    original(*a, **kw)
                    raise RuntimeError("injected-crash")
                with mock.patch.object(module, name, side_effect=crash):
                    with self.assertRaisesRegex(RuntimeError, "injected-crash"):
                        station.amend_scope(*args)
                user_file = self.ws / "source/tapdata/tapdata/user-change.txt"
                if name == "write_task":
                    user_file.write_text("preserve user's later edit")
                station.amend_scope(*args)
                result = task_store.read_task(self.ws)
                self.assertEqual(task["_revision"] + 1, result["_revision"])
                self.assertEqual(1, sum(h.get("operation_id") == args[4] for h in result["history"]))
                self.assertEqual("done", station_operation.read(self.ws)["status"])
                if name == "write_task":
                    self.assertEqual("preserve user's later edit", user_file.read_text())
                    user_file.unlink()

    def test_no_change_disposition_is_frozen_in_completion_proof(self):
        from workflow import task as task_cli
        task = self.takeover()
        task["stage"] = "ci_validation"
        task["task_repositories"]["tapdata/tapdata"] = baseline.task_repository(task["engineering_baseline"], "tapdata/tapdata", "fix/check", "develop", ["file.txt"], "test")
        with mock.patch.object(task_cli, "_check_advance", return_value=[]):
            proof = station.completion_proof(self.ws, task)
        self.assertEqual(task["task_repositories"]["tapdata/tapdata"]["disposition"], "no_change")
        self.assertEqual(proof["dispositions"]["tapdata/tapdata"], "no_change")


if __name__ == "__main__":
    unittest.main(testRunner=TimedTestRunner)
