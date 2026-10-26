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
        # 普通任务夹具不启用外部环境；Docker 专项用例另行声明并模拟驱动。
        cleanup_path = self.product / 'projects/tapdata/station-clean.json'
        cleanup = json.loads(cleanup_path.read_text())
        cleanup['resources'] = []
        cleanup_path.write_text(json.dumps(cleanup))
        shutil.copytree(ROOT / "contracts", self.product / "contracts")
        shutil.copytree(ROOT / "policies", self.product / "policies")
        (self.ws / ".agenticops").mkdir(parents=True)
        for name in ("source", "config", "runtime", "archive"):
            (self.ws / name).mkdir()
        self.write(self.ws / ".agenticops/station.json", {"schema_version": 4, "product_root": str(self.product), "source_pool": str(self.root / "pool"), "project": "tapdata", "agents": ["codex"], "station_id": "a" * 32, "branch_identity": {"schema_version": 1, "git_name": "Test", "source": "git_global_user_name"}})
        epoch = json.loads((self.product / "contracts/station-state-compatibility.json").read_text())["station_state_epoch"]
        self.write(self.ws / ".agenticops/init.json", {"station_state_epoch": epoch, "cleanup": json.loads((self.product / "policies/station-clean.json").read_text())})
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

        from station_fixture import coordination_proof
        coordination_proof(self.ws, self.request["issue_key"])

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
        from station_fixture import coordination_proof
        coordination_proof(self.ws, self.request["issue_key"])
        revision = task_store.read_current(self.ws)["revision"]
        station.takeover(self.ws, self.request, operation, revision)
        task = task_store.read_task(self.ws)
        return task

    def test_reference_refresh_precedes_run_and_skips_operation_replay(self):
        self.prepare_engineering()
        def refresh(base):
            self.assertIsNone(station_operation.read(base))
            self.assertIsNone(task_store.read_current(base)['current'])
            self.assertFalse((self.product / '.archive').exists())
        with mock.patch.object(station.source, 'refresh_shared_references', side_effect=refresh) as update:
            self.takeover()
            station.takeover(self.ws, self.request, 'op-takeover-one', 0)
            self.assertEqual(update.call_count, 1)

    def test_reference_refresh_skips_invalid_revision_and_resumed_failure(self):
        self.prepare_engineering()
        with mock.patch.object(station.source, 'refresh_shared_references') as update:
            with self.assertRaisesRegex(ValueError, 'revision'):
                station.takeover(self.ws, self.request, 'op-takeover-one', 99)
            update.assert_not_called()
            with mock.patch.object(station.source, 'prepare_repositories', side_effect=ValueError('offline')):
                with self.assertRaisesRegex(ValueError, 'offline'):
                    station.takeover(self.ws, self.request, 'op-takeover-one', 0)
            station.takeover(self.ws, self.request, 'op-takeover-one', 0)
            self.assertEqual(update.call_count, 1)

    def test_takeover_does_not_require_obsolete_reset_branch(self):
        self.prepare_engineering()
        catalog_path = self.product / 'projects/tapdata/repositories.json'
        catalog = json.loads(catalog_path.read_text())
        catalog['repositories']['tapdata/tapdata']['dev_branch'] = 'obsolete-reset-only'
        self.write(catalog_path, catalog)
        task = self.takeover()
        self.assertTrue(task['source_prepared'])
        self.assertNotIn('reset_baseline', task)
        self.assertEqual(task['engineering_baseline']['repositories']['tapdata/tapdata']['commit_sha'],
                         self.git(self.ws / 'source/tapdata/tapdata', 'rev-parse', 'HEAD'))

    def rule_task(self, names=()):
        """规则测试直接构造合法状态；Git 行为由真实仓库测试负责。"""
        patcher = mock.patch.object(station.source, "git", side_effect=AssertionError("规则测试不得执行 Git"))
        git = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(git.assert_not_called)
        engineering = {"status": "resolving"}
        if names:
            engineering = baseline.freeze(
                {"id": "rule-fixture", "revision": 1, "repositories": list(names)},
                {name: {"origin": "https://example.com/" + name + ".git"} for name in names},
                {name: {"verification": "verified", "ref_kind": "branch", "ref_name": "develop",
                        "commit_sha": "a" * 40, "resolution_source": "explicit_branch", "rule_version": "fixture"} for name in names},
                {"version": "develop"})
        task = {"issue_key": "TAP-123", "run_id": "run-abc12345", "task_class": "technical_task",
                "stage": "waiting_takeover", "outcome": "in_progress", "facts": {"station_contract": 3},
                "history": [], "pending": None, "engineering_baseline": engineering,
                "task_repositories": {}, "terminal_proof": None, "archive_ref": None}
        task_store.compare_and_set(self.ws, 0, task)
        return task_store.read_task(self.ws)

    def new_contract(self, task):
        task["facts"]["station_contract"] = 3
        task_store.write_task(self.ws, task)
        return task

    def execute(self, base, kind, issue, run, revision, operation, request):
        return station.execute(base, kind, issue, run, revision, operation, {})

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


    def test_completion_requires_merged_head_and_quality(self):
        task = self.rule_task(("tapdata/tapdata",))
        observed = {"tapdata/tapdata": {"head": "b" * 40, "branch": "fix/x", "dirty": False}}
        patcher = mock.patch.object(station.source, "inspect", return_value=observed)
        patcher.start()
        self.addCleanup(patcher.stop)
        task["stage"] = "ci_validation"
        with mock.patch.object(task_checks, "check_advance", return_value=["质量检查未通过"]):
            with self.assertRaisesRegex(ValueError, "质量"):
                station.completion_proof(self.ws, task)
        task["task_repositories"]["tapdata/tapdata"] = baseline.task_repository(task["engineering_baseline"], "tapdata/tapdata", "fix/x", "develop", ["file.txt"], "test")
        with self.assertRaisesRegex(ValueError, "唯一有效合并"):
            station.completion_proof(self.ws, task)
        head = observed["tapdata/tapdata"]["head"]
        task["task_repositories"]["tapdata/tapdata"]["deliveries"] = [{"repository": "tapdata/tapdata", "pr": "123",
            "target_branch": "develop", "candidate_head": head, "pr_head": head,
            "merged_at": "2026-09-15T01:00:00Z", "merge_commit": head, "readback_ref": "fixture:merged-pr"}]
        with mock.patch.object(task_checks, "check_advance", return_value=[]):
            proof = station.completion_proof(self.ws, task)
        delivery = task["task_repositories"]["tapdata/tapdata"]["deliveries"][0]
        for field, value in (("candidate_head", "c" * 40), ("pr_head", "c" * 40),
                             ("target_branch", "main"), ("readback_ref", "")):
            with self.subTest(field=field):
                original = delivery[field]
                delivery[field] = value
                with mock.patch.object(task_checks, "check_advance", return_value=[]):
                    with self.assertRaisesRegex(ValueError, "合并事实"):
                        station.completion_proof(self.ws, task)
                delivery[field] = original
        self.assertNotEqual(task["task_repositories"]["tapdata/tapdata"].get("disposition"), "merged")
        self.assertEqual(proof["dispositions"]["tapdata/tapdata"], "merged")


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

    def test_external_continuation_across_epochs_needs_no_old_runtime(self):
        self.prepare_engineering()
        old = self.git(self.seed, 'rev-parse', 'HEAD')
        self.git(self.seed, 'checkout', '-b', 'fix/recovered')
        (self.seed / 'file.txt').write_text('preserved result\n')
        self.git(self.seed, 'add', '.')
        self.git(self.seed, 'commit', '-m', 'preserved result')
        head = self.git(self.seed, 'rev-parse', 'HEAD')
        self.git(self.seed, 'push', str(self.remote), 'fix/recovered')
        # 来源成果已在远端；接管只消费同一标准输入，不访问旧版现场。
        for epoch in (26, 27, 42):
            with self.subTest(epoch=epoch):
                ws = self.root / ('rebuilt-' + str(epoch))
                self.write(self.product / 'contracts/station-state-compatibility.json',
                           {'station_state_epoch': epoch})
                binding = json.loads((self.ws / '.agenticops/station.json').read_text())
                binding['station_id'] = '%032x' % epoch
                self.write(ws / '.agenticops/station.json', binding)
                self.write(ws / '.agenticops/init.json', {'station_state_epoch': epoch, 'cleanup': json.loads((self.product / 'policies/station-clean.json').read_text())})
                task_store.initialize_current(ws)
                for name in ('source', 'config', 'runtime'):
                    (ws / name).mkdir()
                request = dict(self.request, issue_key='TAP-' + str(epoch),
                               continuations={'tapdata/tapdata': {
                                   'work_branch': 'fix/recovered', 'baseline_sha': old,
                                   'expected_head': head}})
                from station_fixture import coordination_proof
                coordination_proof(ws, request['issue_key'])
                station.takeover(ws, request, 'op-recover-' + str(epoch), 0)
                task = task_store.read_task(ws)
                self.assertEqual(old, task['engineering_baseline']['repositories']['tapdata/tapdata']['commit_sha'])
                station.scope_change(ws, task['issue_key'], task['run_id'], task['_revision'],
                                     'op-scope-recovered-' + str(epoch), 'tapdata/tapdata', 'fix/recovered',
                                     'develop', ['file.txt'], 'unit tests', head)
                self.assertEqual(head, self.git(ws / 'source/tapdata/tapdata', 'rev-parse', 'HEAD'))
                self.assertEqual('preserved result\n', (ws / 'source/tapdata/tapdata/file.txt').read_text())
                self.assertFalse((ws / '.agenticops/authorization.json').exists())
                self.assertFalse(list((ws / '.agenticops/evidence').glob('quality-*.json')))


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


    def other_repository_state(self):
        path = self.ws / "source/tapdata/tapdata-common-lib"
        return (self.git(path, "rev-parse", "HEAD"), self.git(path, "status", "--porcelain"),
                (path / "file.txt").read_bytes())


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
        task = self.rule_task()
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
