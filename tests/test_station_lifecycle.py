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
from workflow import archive_store, authorization, task_checks, engineering_baseline as baseline, station, station_archive, station_operation, task_store
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
        epoch = json.loads((self.product / "contracts/station-state-compatibility.json").read_text())["station_state_epoch"]
        self.write(self.ws / ".agenticops/init.json", {"station_state_epoch": epoch})
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
        request.setdefault("cleanup_version", 6)
        if kind == "clean":
            request.setdefault("abandon_changes", True)
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
        self.assertEqual(task["engineering_baseline"]["profile"]["id"], "full-application")
        self.assertEqual(task["stage"], "waiting_takeover")
        self.assertEqual(task["outcome"], "in_progress")
        self.assertIsNone(task["terminal_proof"])
        self.assertEqual(task["task_repositories"], {})
        self.assertEqual(list((self.ws / "runtime").iterdir()), [])
        self.assertFalse((self.ws / ".agenticops/authorization.json").exists())
        for name, entry in task["engineering_baseline"]["repositories"].items():
            path = self.ws / "source" / name
            self.assertTrue((path / ".git").is_dir())
            self.assertEqual(self.git(path, "rev-parse", "HEAD"), entry["commit_sha"])
            self.assertEqual((path / "file.txt").read_text(), "baseline\n")
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
            station.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-no-decision", {"summary": "停止", "reason": "取消", "abandon_changes": True})
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
        request = {"summary": "尚未编码", "reason": "用户取消", "decision_ref": "fixture:user", "abandon_changes": True,
                   "confirmed_digest": plan["digest"]}
        station.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-new-clean", request)
        self.assertIsNone(task_store.read_task(self.ws))
        archived = archive_store.run_directory(self.ws, task["run_id"])
        record = json.loads((archived / "record.json").read_text())
        self.assertEqual(record["task_result"], "incomplete")
        self.assertEqual(archived.parent, (self.product / ".archive").resolve())
        self.assertEqual(archived.stat().st_mode & 0o777, 0o700)
        for name in ("record.json", "summary.md", "evidence.json", "source-artifacts.json", "runtime-evidence.json"):
            self.assertEqual((archived / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual((archived / "receipts").stat().st_mode & 0o777, 0o700)
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
        evidence = json.loads((archive_store.from_reference(self.ws, current["archive_ref"]) / "evidence.json").read_text())
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
        self.assertTrue((archive_store.run_directory(self.ws, task["run_id"]) / "record.json").is_file())
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
        path=archive_store.from_reference(self.ws, task["archive_ref"])/"record.json"; before=path.read_bytes()
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
        with mock.patch.object(task_checks, "check_advance", return_value=["质量检查未通过"]):
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
        with mock.patch.object(task_checks, "check_advance", return_value=[]):
            proof = station.completion_proof(self.ws, task)
        self.assertNotEqual(task["task_repositories"]["tapdata/tapdata"].get("disposition"), "merged")
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
        from workflow import station_resources
        task = self.takeover()
        task["stage"] = "ci_validation"
        task_store.write_task(self.ws, task)
        with mock.patch.object(task_checks, "check_advance", return_value=[]):
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
        record = json.loads((archive_store.run_directory(self.ws, task["run_id"]) / "record.json").read_text())
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
        before = (archive_store.from_reference(self.ws, current["archive_ref"]) / "record.json").read_bytes()
        plan = station_resources.plan(self.ws, current)
        with mock.patch.object(station, "completion_proof", side_effect=AssertionError("must not complete archived task")):
            with self.assertRaisesRegex(ValueError, "未完成档案"):
                self.execute(self.ws, "release", current["issue_key"], current["run_id"], current["_revision"], "op-wrong-release", {"confirmed_digest": plan["digest"], "candidate_digest": "unused"})
        self.assertEqual(task_store.read_task(self.ws)["outcome"], "in_progress")
        self.assertEqual((archive_store.from_reference(self.ws, current["archive_ref"]) / "record.json").read_bytes(), before)

    def test_cleanup_amend_preserves_pending_step_and_cleans_new_owned_file(self):
        from workflow import station_resources
        task=self.takeover()
        first=station_resources.plan(self.ws,task)
        request={"summary":"未完成","reason":"停止","confirmed_digest":first["digest"]}
        with mock.patch.object(station_resources,"clean",side_effect=OSError("before directory reset")), self.assertRaises(OSError):
            self.execute(self.ws,"clean",task["issue_key"],task["run_id"],task["_revision"],"op-amend-clean",request)
        current=task_store.read_task(self.ws)
        record=archive_store.from_reference(self.ws, current["archive_ref"])/"record.json";before=record.read_bytes()
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
        from workflow import station_resources
        task = self.takeover()
        untouched = self.other_repository_state()
        station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-release-scope", "tapdata/tapdata", None, "develop", ["file.txt"], "unit test")
        task = task_store.read_task(self.ws)
        task["stage"] = "ci_validation"
        with mock.patch.object(task_checks, "check_advance", return_value=[]):
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
        # 先具备开发分支目标及成果引用，隔离验证同摘要的重复目录清理。
        initial = station_resources.plan(self.ws, task)
        for name, entry in initial["source"].items():
            self.git(self.ws / "source" / name, "update-ref", entry["preserved_ref"], entry["preserved_head"])
            self.git(self.ws / "source" / name, "checkout", "-B", entry["checkout_branch"], entry["neutral"]["sha"])
            for ref, sha in entry["baseline_refs"].items():
                self.git(self.ws / "source" / name, "update-ref", "-d", ref, sha)
        plan = station_resources.plan(self.ws, task)
        request = {"summary": "未完成", "reason": "停止", "confirmed_digest": plan["digest"]}
        original = station_resources.clean
        def fail_after_directories(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("directories_only"):
                raise OSError("after cleaned")
            return result
        with mock.patch.object(station_resources, "clean", side_effect=fail_after_directories):
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
        receipts = archive_store.run_directory(self.ws, task["run_id"]) / "receipts"
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
        draft = archive_store.root(self.ws) / ("." + task["run_id"] + ".op-draft-amend.0")
        old_record = (draft / "record.json").read_bytes()
        file.write_text("changed before publication")
        second = station_resources.plan(self.ws, current)
        operation = station.amend_cleanup(self.ws, task["issue_key"], task["run_id"], current["_revision"], "op-draft-amend", first["digest"], {"confirmed_digest": second["digest"], "expected_plan_revision": 0})
        self.assertEqual(operation["archive_drafts"][0]["record"], json.loads(old_record))
        self.assertNotIn("archive_record", operation)
        self.assertEqual((draft / "record.json").read_bytes(), old_record)
        self.execute(self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-draft-amend", request)
        self.assertIsNone(task_store.read_task(self.ws))
        target = archive_store.run_directory(self.ws, task["run_id"])
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
        target = archive_store.run_directory(self.ws, task["run_id"])
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
        task = self.amend_fixture()
        points = [(station_operation, "begin"), (station_operation, "intent"),
                  (authorization, "revoke_authorization"), (task_store, "write_task"),
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

    def test_completion_preflight_aggregates_missing_merges_without_writes(self):
        import contextlib
        import copy
        import io
        from types import SimpleNamespace
        from workflow import task as task_cli
        self.prepare_engineering(2)
        task = self.takeover()
        task["stage"] = "ci_validation"
        names = list(task["engineering_baseline"]["repositories"])
        for name in names:
            path = self.ws / "source" / name
            self.git(path, "checkout", "-b", "fix/test")
            self.git(path, "config", "user.email", "test@example.com")
            self.git(path, "config", "user.name", "Test")
            (path / "file.txt").write_text("candidate")
            self.git(path, "add", ".")
            self.git(path, "commit", "-m", "candidate")
            task["task_repositories"][name] = baseline.task_repository(
                task["engineering_baseline"], name, "fix/test", "develop", ["file.txt"], "test")
        task_store.write_task(self.ws, task)
        task = task_store.read_task(self.ws)
        before = copy.deepcopy(task)
        import os
        for name in names:
            path = self.ws / "source" / name / "file.txt"
            os.utime(path, (1, 1))  # 内容未变，普通 git status 会刷新索引 stat 缓存。
        files = {str(p): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}
        args = SimpleNamespace(dir=str(self.ws), issue_key=task["issue_key"],
                               expected_run_id=task["run_id"], expected_stage="ci_validation", note="test")
        with mock.patch.object(task_checks, "check_advance", return_value=[]):
            result = station.evaluate_completion(self.ws, task)
            self.assertFalse(result["ready"])
            self.assertEqual(2, sum("awaiting_merge" in p for p in result["problems"]))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                task_cli.cmd_next(args)
            report = json.loads(output.getvalue())
            self.assertFalse(report["advance_ready"])
            self.assertEqual(result["problems"], report["blockers"])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(3, task_cli.cmd_advance(args))
        self.assertEqual(before, task)
        self.assertEqual(files, {str(p): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()})

    def test_next_does_not_advertise_advance_during_unfinished_operation(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from workflow import task as task_cli
        task = self.takeover()
        task["stage"] = "ci_validation"
        task_store.write_task(self.ws, task)
        station_operation.begin(self.ws, "scope_change", "op-scope-pending",
                                task_store.read_current(self.ws)["revision"], {}, task["run_id"])
        output = io.StringIO()
        with mock.patch.object(task_checks, "check_advance", return_value=[]), contextlib.redirect_stdout(output):
            task_cli.cmd_next(SimpleNamespace(dir=str(self.ws), issue_key=task["issue_key"]))
        result = json.loads(output.getvalue())
        self.assertFalse(result["advance_ready"])
        self.assertTrue(any("未完成" in message for message in result["blockers"]))

    def test_completion_rechecks_source_after_successful_preflight(self):
        task = self.takeover()
        task["stage"] = "ci_validation"
        with mock.patch.object(task_checks, "check_advance", return_value=[]):
            self.assertTrue(station.evaluate_completion(self.ws, task)["ready"])
            (self.ws / "source/tapdata/tapdata/file.txt").write_text("later edit")
            with self.assertRaisesRegex(ValueError, "洁净"):
                station.completion_proof(self.ws, task)

    def replan_fixture(self, addition=False):
        from workflow import station_replan
        task = self.takeover()
        station.scope_change(self.ws, task["issue_key"], task["run_id"], task["_revision"],
                             "op-replan-scope", "tapdata/tapdata", None, "develop", ["file.txt"], "fixture")
        task = task_store.read_task(self.ws)
        task["stage"] = "pr_review"
        task["task_repositories"]["tapdata/tapdata"]["observation"] = {"results": {"pull_request": "fixture:pr"}}
        task.pop("repositories", None)
        task_store.write_task(self.ws, task)
        path = self.ws / "source/tapdata/tapdata/file.txt"
        path.write_text("preserve dirty source")
        task = task_store.read_task(self.ws)
        task_store._write_json_atomic(task_store.authorization_path(self.ws, task["issue_key"]), {"status": "active"})
        request = {"reason": "纠正实施方向", "facts": {"fix_plan": "new plan"},
                   "repositories": {"tapdata/tapdata": {"scope": ["file.txt", "new.txt"], "verification": "new test"}},
                   "additions": {}, "impact": {"repositories": ["tapdata/tapdata"], "items": [], "rationale": "源码分析确认影响目标模块"}}
        if addition:
            request["additions"]["tapdata/t-layer3-test"] = {"scope": ["file.txt"], "verification": "test",
                "ref_name": "develop", "commit_sha": self.git(self.seed, "rev-parse", "HEAD")}
            request["impact"]["repositories"].append("tapdata/t-layer3-test")
        prepared = station_replan.prepare(self.ws, task["issue_key"], task["run_id"], request)
        return task, prepared

    def test_replan_preserves_dirty_source_pr_run_and_appends_optional_baseline(self):
        from workflow import station_replan, station_source
        task, prepared = self.replan_fixture(addition=True)
        original = task["engineering_baseline"]
        args = (self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-replan-apply", prepared, "fixture:decision")
        station_replan.apply(*args)
        result = task_store.read_task(self.ws)
        self.assertEqual(task["run_id"], result["run_id"])
        self.assertEqual("design_review", result["stage"])
        self.assertEqual("preserve dirty source", (self.ws / "source/tapdata/tapdata/file.txt").read_text())
        self.assertEqual(original["repositories"]["tapdata/tapdata"], result["engineering_baseline"]["repositories"]["tapdata/tapdata"])
        self.assertEqual("fixture:pr", result["task_repositories"]["tapdata/tapdata"]["observation"]["results"]["pull_request"])
        self.assertEqual(task_store.generated_work_branch(self.ws, task), result["task_repositories"]["tapdata/t-layer3-test"]["work_branch"])
        self.assertEqual(2, result["engineering_baseline"]["revision"])
        self.assertEqual("revoked", json.loads(task_store.authorization_path(self.ws, task["issue_key"]).read_text())["status"])
        with task_store.task_state_lock(self.ws):
            station_source.prepare_readiness(self.ws, result)
            self.assertTrue(station_source.require_readiness(self.ws, result))
        station_replan.apply(*args)
        self.assertEqual(result["_revision"], task_store.read_task(self.ws)["_revision"])
        (self.ws / "source/tapdata/tapdata/file.txt").write_text("later drift")
        with self.assertRaisesRegex(ValueError, "指纹变化"):
            station_source.readiness_snapshot(self.ws, result)

    def test_replan_prepare_rejects_unknown_scope_and_is_readonly(self):
        import copy
        from workflow import station_replan
        task, prepared = self.replan_fixture()
        before = {str(p): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()}
        self.assertEqual(prepared, station_replan.prepare(self.ws, task["issue_key"], task["run_id"], prepared["request"]))
        for kind in ("unknown_repo", "identity", "unknown_item", "missing_impact"):
            request = copy.deepcopy(prepared["request"])
            if kind == "unknown_repo":
                request["additions"]["tapdata/not-allowed"] = {}
            elif kind == "identity":
                request["facts"]["station_contract"] = 0
            elif kind == "unknown_item":
                request["impact"]["items"] = ["not-an-item"]
            else:
                request["impact"]["repositories"] = []
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                station_replan.prepare(self.ws, task["issue_key"], task["run_id"], request)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.ws.rglob("*") if p.is_file()})

    def test_replan_rejects_drift_before_revoking_authorization(self):
        from workflow import station_replan
        task, prepared = self.replan_fixture()
        (self.ws / "source/tapdata/tapdata/file.txt").write_text("new user edit")
        with self.assertRaisesRegex(ValueError, "事实已变化"):
            station_replan.apply(self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-replan-drift", prepared, "fixture:decision")
        self.assertEqual("active", json.loads(task_store.authorization_path(self.ws, task["issue_key"]).read_text())["status"])
        self.assertEqual("pr_review", task_store.read_task(self.ws)["stage"])

    def test_replan_recovers_after_current_write_without_duplicate_history(self):
        from workflow import station_replan
        task, prepared = self.replan_fixture()
        args = (self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-replan-current", prepared, "fixture:decision")
        original = station_operation.receipt
        def receipt(base, operation, name, result):
            if name == "current":
                raise OSError("crash after current")
            return original(base, operation, name, result)
        with mock.patch.object(station_operation, "receipt", side_effect=receipt), self.assertRaises(OSError):
            station_replan.apply(*args)
        station_replan.apply(*args)
        current = task_store.read_task(self.ws)
        self.assertEqual(1, sum(h.get("event") == "replan" for h in current["history"]))
        self.assertEqual("done", station_operation.read(self.ws)["status"])

    def test_replan_recovers_created_branch_without_checkout_of_old_source(self):
        from workflow import station_replan
        task, prepared = self.replan_fixture(addition=True)
        args = (self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-replan-branch", prepared, "fixture:decision")
        original = station_operation.receipt
        def receipt(base, operation, name, result):
            if name.startswith("replan-branch:"):
                raise OSError("crash after branch")
            return original(base, operation, name, result)
        with mock.patch.object(station_operation, "receipt", side_effect=receipt), self.assertRaises(OSError):
            station_replan.apply(*args)
        station_replan.apply(*args)
        self.assertEqual("preserve dirty source", (self.ws / "source/tapdata/tapdata/file.txt").read_text())
        self.assertEqual("done", station_operation.read(self.ws)["status"])

    def test_replan_abort_preserves_partial_clone_and_allows_cleanup_preflight(self):
        from workflow import station_replan, station_source, station_resources
        task, prepared = self.replan_fixture(addition=True)
        args = (self.ws, task["issue_key"], task["run_id"], task["_revision"], "op-replan-abort", prepared, "fixture:decision")
        with mock.patch.object(station_source, "checkout_baseline", side_effect=OSError("crash before checkout")), self.assertRaises(OSError):
            station_replan.apply(*args)
        path = self.ws / "source/tapdata/t-layer3-test"
        self.assertTrue((path / ".git").is_dir())
        result = station_replan.abort(self.ws, task["issue_key"], task["run_id"], args[4], "fixture:abort")
        self.assertEqual("aborted", result["outcome"])
        current = task_store.read_task(self.ws)
        self.assertIn("tapdata/t-layer3-test", current["replan_preserved"])
        self.assertTrue(station_resources.plan(self.ws, current)["digest"])
        self.assertEqual(result, station_replan.abort(self.ws, task["issue_key"], task["run_id"], args[4], "fixture:abort"))
        cleanup = station_resources.plan(self.ws, current, version=6)
        station.execute(self.ws, 'clean', current['issue_key'], current['run_id'], current['_revision'], 'op-clean-aborted-replan',
            {'summary': '保留部分准备仓并退出', 'reason': 'fixture', 'decision_ref': 'fixture:cleanup',
             'cleanup_version': 6, 'abandon_changes': True, 'confirmed_digest': cleanup['digest']})
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertTrue((path / '.git').is_dir())


    def test_initialized_feature_replan_grant_advance_clean_purge_cycle(self):
        import contextlib
        import io
        import os
        from types import SimpleNamespace
        from datetime import datetime, timezone
        from bootstrap import station_registry
        from workflow import authorization, quality, station_replan, station_source, station_resources
        from workflow import task as task_cli
        from test_quality import feature_review
        # 从真实同版生成入口开始；只复制产品资产，所有 Git/Jira 输入为隔离夹具。
        shutil.rmtree(self.ws)
        for name in ('bootstrap', 'adapters', 'gate', 'workflow', 'skills'):
            shutil.copytree(ROOT / name, self.product / name)
        shutil.copy2(ROOT / 'agenticops', self.product / 'agenticops')
        init = subprocess.run(['bash', str(self.product / 'bootstrap/station-init.sh'), '--station', str(self.ws),
            '--project', 'tapdata', '--agent', 'codex', '--source-pool', str(self.root / 'pool')],
            env={**os.environ, 'AGENTIC_OPS_HOME': str(self.product)}, capture_output=True, text=True)
        self.assertEqual(0, init.returncode, init.stdout + init.stderr)
        self.prepare_engineering()
        self.request['task_class'] = 'feature_change'
        task = self.takeover()
        name = 'tapdata/tapdata'
        station.scope_change(self.ws, task['issue_key'], task['run_id'], task['_revision'], 'op-scope-feature-cycle',
                             name, None, 'develop', ['file.txt'], 'fixture assertion')
        task = task_store.read_task(self.ws)
        task['stage'] = 'pr_review'
        plan = {'objective': '目标行为', 'changes': ['file.txt'], 'acceptance': ['目标行为'],
                'risks': ['夹具'], 'rollback': '回退修改', **feature_review('目标行为', name, 'file.txt', 'behavior')}
        task['facts'].update(acceptance_criteria='目标行为', target_repo=name, verification_method='fixture assertion',
                             scope_boundary='file.txt', implementation_plan=plan)
        task['task_repositories'][name]['observation'] = {'results': {'pull_request': 'fixture:pr-draft'}}
        task.pop('repositories', None)
        task_store.write_task(self.ws, task)
        task = task_store.read_task(self.ws)
        def apply(action, payload):
            current = task_store.read_task(self.ws)
            return quality.apply(self.ws, current['issue_key'], current['run_id'], quality.load(self.ws, current)['revision'],
                                 {'action': action, 'payload': payload})
        def view():
            current = task_store.read_task(self.ws)
            return quality.report(quality.load(self.ws, current), quality.config(self.ws, current), quality.context(self.ws, current), base=self.ws, task=current)
        proof = {'actor': 'Fixture', 'source': 'user_message', 'reference': 'fixture:confirmed-plan',
                 'at': datetime.now(timezone.utc).isoformat()}
        apply('item', {'plan': {'id': 'behavior', 'checkpoint': 'q4-acceptance', 'timing': 'after_fix',
            'case_ref': 'fixture:test', 'case_version': 'v1', 'case_status': 'existing', 'method': 'unit',
            'repository': name, 'target_revision': task['engineering_baseline']['repositories'][name]['commit_sha'],
            'criterion': '目标行为', 'steps': 'fixture assertion', 'expected_result': 'PASS', 'scope': 'file.txt'}, 'reason': '验收映射'})
        apply('select', {'item_id': 'behavior', 'digest': view()['items']['behavior']['plan_digest'], 'proof': proof})
        def confirm(point):
            apply('checkpoint', {'checkpoint': point, 'digest': view()['checkpoints'][point]['digest'],
                                'decision': {'outcome': 'accept', 'reason': 'fixture 确认', 'proof': proof}})
        confirm('q1-intake'); confirm('q2-plan')
        q1 = quality.q1_digest(self.ws, task_store.read_task(self.ws))
        task_store._write_json_atomic(task_store.authorization_path(self.ws, task['issue_key']), {'status': 'active'})
        path = self.ws / 'source' / name / 'file.txt'
        path.write_text('preserved feature work')
        new_plan = dict(plan, objective='修订目标实现方式')
        request = {'reason': 'Draft 后修订实现', 'facts': {'implementation_plan': new_plan},
            'repositories': {name: {'scope': ['file.txt'], 'verification': 'fixture assertion'}}, 'additions': {},
            'impact': {'repositories': [name], 'items': ['behavior'], 'rationale': '当前模块实现方式调整'}}
        current = task_store.read_task(self.ws)
        prepared = station_replan.prepare(self.ws, current['issue_key'], current['run_id'], request)
        station_replan.apply(self.ws, current['issue_key'], current['run_id'], current['_revision'], 'op-replan-feature-cycle', prepared, 'fixture:confirmed-plan')
        current = task_store.read_task(self.ws)
        self.assertEqual(q1, quality.q1_digest(self.ws, current))
        self.assertEqual(task['run_id'], current['run_id'])
        self.assertEqual('fixture:pr-draft', current['task_repositories'][name]['observation']['results']['pull_request'])
        self.assertEqual('preserved feature work', path.read_text())
        apply('select', {'item_id': 'behavior', 'digest': view()['items']['behavior']['plan_digest'], 'proof': proof})
        confirm('q2-plan')
        with task_store.task_state_lock(self.ws):
            station_source.prepare_readiness(self.ws, current)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, authorization.cmd_grant(SimpleNamespace(dir=str(self.ws), issue_key=current['issue_key'],
                expected_run_id=current['run_id'], agent_id='fixture', plan_version='replan-v2', ttl_hours=1)))
            self.assertEqual(0, task_cli.cmd_advance(SimpleNamespace(dir=str(self.ws), issue_key=current['issue_key'],
                expected_run_id=current['run_id'], expected_stage='design_review', note='恢复实施')))
        current = task_store.read_task(self.ws)
        self.assertEqual('implementation', current['stage'])
        self.assertEqual('preserved feature work', path.read_text())
        cleanup = station_resources.plan(self.ws, current, version=6)
        station.execute(self.ws, 'clean', current['issue_key'], current['run_id'], current['_revision'], 'op-clean-feature-cycle',
            {'summary': '结束隔离演练', 'reason': '夹具完成', 'decision_ref': 'fixture:cleanup',
             'cleanup_version': 6, 'abandon_changes': True, 'confirmed_digest': cleanup['digest']})
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertTrue(list(archive_store.root(self.ws).iterdir()))
        station_registry.detach(self.product, self.ws, purge=True)
        self.assertFalse((self.ws / '.agenticops').exists())
        self.assertTrue((self.ws / 'source' / name / '.git').is_dir())
        self.assertTrue(list((self.product / '.archive').iterdir()))

    def test_replan_resumes_revoke_clone_and_before_current_crashes(self):
        from workflow import station_replan, station_source
        for phase in ('revoke', 'clone', 'before-current'):
            with self.subTest(phase=phase):
                case = StationTests()
                case.setUp()
                try:
                    task, prepared = case.replan_fixture(addition=True)
                    args = (case.ws, task['issue_key'], task['run_id'], task['_revision'], 'op-replan-' + phase, prepared, 'fixture:confirmed')
                    if phase == 'revoke':
                        original = station_operation.receipt
                        def crash(base, op, name, observed):
                            if name == 'revoke':
                                raise OSError('crash after revoke')
                            return original(base, op, name, observed)
                        patch = mock.patch.object(station_operation, 'receipt', side_effect=crash)
                    elif phase == 'clone':
                        original = station_source.prepare_repositories
                        def crash(*a, **kw):
                            original(*a, **kw)
                            raise OSError('crash after clone')
                        patch = mock.patch.object(station_source, 'prepare_repositories', side_effect=crash)
                    else:
                        original = task_store.write_task
                        def crash(base, value):
                            if value.get('replan'):
                                raise OSError('crash before current')
                            return original(base, value)
                        patch = mock.patch.object(task_store, 'write_task', side_effect=crash)
                    with patch, self.assertRaises(OSError):
                        station_replan.apply(*args)
                    self.assertEqual('revoked', json.loads(task_store.authorization_path(case.ws, task['issue_key']).read_text())['status'])
                    station_replan.apply(*args)
                    current = task_store.read_task(case.ws)
                    self.assertEqual(task['run_id'], current['run_id'])
                    self.assertEqual('preserve dirty source', (case.ws / 'source/tapdata/tapdata/file.txt').read_text())
                    self.assertEqual(1, sum(row.get('event') == 'replan' for row in current['history']))
                    self.assertEqual('done', station_operation.read(case.ws)['status'])
                finally:
                    case.doCleanups()

    def test_no_change_disposition_is_frozen_in_completion_proof(self):
        task = self.takeover()
        task["stage"] = "ci_validation"
        task["task_repositories"]["tapdata/tapdata"] = baseline.task_repository(task["engineering_baseline"], "tapdata/tapdata", "fix/check", "develop", ["file.txt"], "test")
        with mock.patch.object(task_checks, "check_advance", return_value=[]):
            proof = station.completion_proof(self.ws, task)
        self.assertNotEqual(task["task_repositories"]["tapdata/tapdata"].get("disposition"), "no_change")
        self.assertEqual(proof["dispositions"]["tapdata/tapdata"], "no_change")


if __name__ == "__main__":
    unittest.main(testRunner=TimedTestRunner)
