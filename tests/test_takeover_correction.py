#!/usr/bin/env python3
"""AO-290：真实隔离 Git 远端、旧接管中间态与受控更正闭环。"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import test_station_lifecycle as fixture
from workflow import station, station_operation as operations, station_takeover, task_store
from workflow import engineering_baseline as baseline
from internal.tests.timing import TimedTestRunner


class TakeoverCorrectionTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    prepare_engineering = fixture.StationTests.prepare_engineering
    write = fixture.StationTests.write
    git = fixture.StationTests.git

    def branches(self):
        self.prepare_engineering()
        self.git(self.remote, "branch", "release-v4.21.0", "develop")
        self.correct_request = dict(self.request, version="release-v4.21.0",
                                    explicit_branches={"tapdata/tapdata": "release-v4.21.0"})

    def legacy_failure(self):
        self.branches()
        self.wrong = dict(self.request, version="release-v2.0.8", explicit_branches={})
        # 原版本先 begin，再写 current、准备仓库，最后 resolve。保留实际旧请求格式。
        with task_store.task_state_lock(self.ws):
            operations.begin(self.ws, "takeover", "op-takeover-old", 0, self.wrong)
        with self.assertRaisesRegex(ValueError, "分支"):
            station.takeover(self.ws, self.wrong, "op-takeover-old", 0)
        task, operation = task_store.read_task(self.ws), operations.read(self.ws)
        # 还原旧程序已完成 clone/fetch、随后解析失败的真实中间态。
        _, _, catalog, profile = station_takeover.inputs(self.ws, self.wrong)
        selected = baseline.selected_repositories(profile, catalog)
        from workflow import station_source
        station_source.prepare_repositories(self.ws, catalog, selected, operation)
        self.correction_args = (self.ws, task["issue_key"], task["run_id"], operation["operation_id"],
            task["_revision"], operation["request_digest"], self.correct_request["version"],
            self.correct_request["explicit_branches"], "user:两个仓库的正确分支")
        return task, operation

    def test_invalid_input_does_not_reserve_run_or_task(self):
        self.branches()
        with self.assertRaisesRegex(ValueError, "tapdata/tapdata.*release-v2.0.8"):
            station.takeover(self.ws, dict(self.request, version="release-v2.0.8", explicit_branches={}),
                             "op-takeover-new", 0)
        self.assertIsNone(operations.read(self.ws))
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertFalse((self.product / ".archive").exists())
        self.assertEqual([], list((self.ws / "source").iterdir()))

    def test_aggregates_missing_branch_and_unreachable_remote(self):
        self.prepare_engineering(3)
        catalog_path = self.product / "projects/tapdata/repositories.json"
        catalog = json.loads(catalog_path.read_text())
        catalog["repositories"]["tapdata/tapdata-connectors"]["origin"] = str(self.root / "missing.git")
        self.write(catalog_path, catalog)
        request = copy.deepcopy(self.request)
        request["explicit_branches"]["tapdata/tapdata-common-lib"] = "missing"
        with self.assertRaises(ValueError) as caught:
            station.takeover(self.ws, request, "op-invalid-refs", 0)
        self.assertIn("tapdata/tapdata-common-lib：显式分支 missing 不存在", str(caught.exception))
        self.assertIn("tapdata/tapdata-connectors：远端引用无法核验", str(caught.exception))
        self.assertIsNone(operations.read(self.ws))

    def test_legacy_correction_reuses_run_clone_and_is_idempotent(self):
        task, before = self.legacy_failure()
        repository = self.ws / "source/tapdata/tapdata"
        inode = repository.stat().st_ino
        with self.assertRaisesRegex(ValueError, "不同请求"):
            station.takeover(self.ws, self.correct_request, "op-takeover-old", 0)
        operation = station_takeover.correct(*self.correction_args)
        self.assertEqual(before["steps"], operation["steps"])
        self.assertEqual(before["request"], operation["takeover_revisions"][0]["before_request"])
        self.assertEqual(operation, station_takeover.correct(*self.correction_args))
        station.takeover(self.ws, operation["request"], operation["operation_id"], 0)
        after = task_store.read_task(self.ws)
        self.assertEqual(task["run_id"], after["run_id"])
        self.assertEqual(inode, repository.stat().st_ino)
        self.assertEqual("release-v4.21.0", after["engineering_baseline"]["resolution_input"]["version"])
        self.assertEqual("done", station_takeover.correct(*self.correction_args)["status"])

    def test_failure_before_and_after_atomic_save_recovers(self):
        _, before = self.legacy_failure()
        original_save = operations.save
        with mock.patch.object(operations, "save", side_effect=OSError("before save")):
            with self.assertRaises(OSError):
                station_takeover.correct(*self.correction_args)
        self.assertEqual(before, operations.read(self.ws))
        def saved_then_interrupted(base, value):
            original_save(base, value)
            raise OSError("after save")
        with mock.patch.object(operations, "save", side_effect=saved_then_interrupted):
            with self.assertRaises(OSError):
                station_takeover.correct(*self.correction_args)
        corrected = station_takeover.correct(*self.correction_args)
        self.assertEqual(1, len(corrected["takeover_revisions"]))
        station.takeover(self.ws, corrected["request"], corrected["operation_id"], 0)

    def test_dirty_or_checkout_or_stale_request_cannot_be_corrected(self):
        _, before = self.legacy_failure()
        args = list(self.correction_args)
        args[5] = "0" * 64
        with self.assertRaisesRegex(ValueError, "已变化"):
            station_takeover.correct(*args)
        path = self.ws / "source/tapdata/tapdata/user.txt"
        path.write_text("unique user work")
        with self.assertRaisesRegex(ValueError, "工作树内容"):
            station_takeover.correct(*self.correction_args)
        self.assertEqual(before, operations.read(self.ws))
        path.unlink()
        changed = copy.deepcopy(before)
        changed["steps"]["checkout:tapdata/tapdata"] = {"before": {}, "expected": {}, "receipt": None}
        operations.save(self.ws, changed)
        with self.assertRaisesRegex(ValueError, "checkout"):
            station_takeover.correct(*self.correction_args)

    def test_pinned_sha_survives_remote_branch_advance(self):
        self.branches()
        old_sha = self.git(self.remote, "rev-parse", "develop")
        with mock.patch.object(station.source, "prepare_repositories", side_effect=ValueError("offline")):
            with self.assertRaisesRegex(ValueError, "offline"):
                station.takeover(self.ws, self.request, "op-pinned-source", 0)
        (self.seed / "file.txt").write_text("remote advanced\n")
        self.git(self.seed, "commit", "-am", "advance")
        self.git(self.seed, "push", str(self.remote), "develop")
        station.takeover(self.ws, self.request, "op-pinned-source", 0)
        self.assertEqual(old_sha, self.git(self.ws / "source/tapdata/tapdata", "rev-parse", "HEAD"))

    def test_frozen_baseline_rejects_new_correction(self):
        self.branches()
        station.takeover(self.ws, self.request, "op-frozen-source", 0)
        task, operation = task_store.read_task(self.ws), operations.read(self.ws)
        with self.assertRaisesRegex(ValueError, "冻结基线"):
            station_takeover.correct(self.ws, task["issue_key"], task["run_id"], operation["operation_id"],
                task["_revision"], operation["request_digest"], "release-v4.21.0", {}, "user:change")

    def test_two_repository_versions_and_original_clone_are_preserved(self):
        self.prepare_engineering(2)
        main, connector = "tapdata/tapdata", "tapdata/tapdata-connectors-enterprise"
        profile_path = self.product / "projects/tapdata/engineering-profiles.json"
        profiles = json.loads(profile_path.read_text())
        profiles["profiles"]["full-application"]["repositories"] = [main, connector]
        self.write(profile_path, profiles)
        connector_remote = self.root / "connector.git"
        self.git(self.root, "clone", "--bare", str(self.seed), str(connector_remote))
        self.git(connector_remote, "branch", "release-v2.0.8", "develop")
        self.git(self.remote, "branch", "release-v4.21.0", "develop")
        catalog_path = self.product / "projects/tapdata/repositories.json"
        catalog = json.loads(catalog_path.read_text())
        catalog["repositories"][connector]["origin"] = str(connector_remote)
        self.write(catalog_path, catalog)
        wrong = dict(self.request, version="release-v2.0.8", explicit_branches={connector: "release-v2.0.8"})
        with task_store.task_state_lock(self.ws):
            operations.begin(self.ws, "takeover", "op-two-repositories", 0, wrong)
        with self.assertRaisesRegex(ValueError, main):
            station.takeover(self.ws, wrong, "op-two-repositories", 0)
        task, old = task_store.read_task(self.ws), operations.read(self.ws)
        result = station_takeover.correct(self.ws, task["issue_key"], task["run_id"], old["operation_id"],
            task["_revision"], old["request_digest"], "release-v4.21.0", {connector: "release-v2.0.8"}, "user:branches")
        self.assertEqual(old["steps"], result["steps"])
        station.takeover(self.ws, result["request"], result["operation_id"], 0)
        entries = task_store.read_task(self.ws)["engineering_baseline"]["repositories"]
        self.assertEqual("release-v4.21.0", entries[main]["ref_name"])
        self.assertEqual("release-v2.0.8", entries[connector]["ref_name"])

    def test_correct_new_remote_object_without_rewriting_prepared_refs(self):
        _, old = self.legacy_failure()
        (self.seed / "file.txt").write_text("new branch object\n")
        self.git(self.seed, "commit", "-am", "new")
        self.git(self.seed, "push", str(self.remote), "HEAD:refs/heads/release-v4.21.0")
        operation = station_takeover.correct(*self.correction_args)
        station.takeover(self.ws, operation["request"], operation["operation_id"], 0)
        self.assertEqual(self.git(self.seed, "rev-parse", "HEAD"),
                         self.git(self.ws / "source/tapdata/tapdata", "rev-parse", "HEAD"))
        self.assertEqual(old["steps"]["fetch:tapdata/tapdata"],
                         operations.read(self.ws)["steps"]["fetch:tapdata/tapdata"])

    def test_preflight_failure_and_concurrent_source_change_keep_old_request(self):
        _, before = self.legacy_failure()
        args = list(self.correction_args)
        args[6], args[7] = "missing", {}
        with self.assertRaisesRegex(ValueError, "分支"):
            station_takeover.correct(*args)
        self.assertEqual(before, operations.read(self.ws))
        preflight = station_takeover.preflight
        def mutate_after_probe(*values):
            result = preflight(*values)
            (self.ws / "source/tapdata/tapdata/user.txt").write_text("keep")
            return result
        with mock.patch.object(station_takeover, "preflight", side_effect=mutate_after_probe):
            with self.assertRaisesRegex(ValueError, "工作树内容"):
                station_takeover.correct(*self.correction_args)
        self.assertEqual(before, operations.read(self.ws))

    def test_project_object_derivation_before_task_creation(self):
        self.prepare_engineering(3)
        config = json.loads((self.product / "projects/tapdata/version-branch-alignments.json").read_text())
        plugin = config["derivation"]["plugin_version_source"]
        path = self.seed / plugin["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plugin["key"] + "=2.0.8\n")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "plugin version")
        self.git(self.seed, "push", str(self.remote), "HEAD:refs/heads/release-v4.21.0")
        self.git(self.remote, "branch", "release-v2.0.8", "develop")
        request = dict(self.request, version="release-v4.21.0", explicit_branches={})
        value = station_takeover.preflight(self.ws, request)
        self.assertEqual("release-v2.0.8", value["repositories"]["tapdata/tapdata-connectors"]["ref_name"])
        self.assertIsNone(operations.read(self.ws))
        self.assertEqual([], list((self.ws / "source").iterdir()))

    def test_malformed_correction_history_fails_closed(self):
        self.legacy_failure()
        operation = station_takeover.correct(*self.correction_args)
        for history in ([None], [{}], [dict(operation["takeover_revisions"][0], after_digest="bad")]):
            invalid = copy.deepcopy(operation)
            invalid["takeover_revisions"] = history
            operations.save(self.ws, invalid)
            with self.assertRaisesRegex(ValueError, "更正历史"):
                operations.read(self.ws)
        operations.save(self.ws, operation)

    def test_cli_correction_then_cleanup_and_new_takeover(self):
        import contextlib
        import io
        from workflow import task as cli, station_cleanup
        from station_fixture import coordination_proof
        task, operation = self.legacy_failure()
        args = ["task.py", "takeover-correct", "--dir", str(self.ws), "--issue-key", task["issue_key"],
                "--expected-run-id", task["run_id"], "--operation-id", operation["operation_id"],
                "--expected-revision", str(task["_revision"]), "--expected-request-digest", operation["request_digest"],
                "--version", "release-v4.21.0", "--explicit-branch", "tapdata/tapdata=release-v4.21.0",
                "--decision-ref", "user:correct"]
        output = io.StringIO()
        with mock.patch.object(sys, "argv", args), contextlib.redirect_stdout(output):
            self.assertEqual(0, cli.main())
        self.assertEqual("done", json.loads(output.getvalue())["status"])
        task = task_store.read_task(self.ws)
        plan = station_cleanup.plan(self.ws, "task")
        request = {"scope": "task", "materials": "preserved", "expected_plan_digest": plan["digest"],
                   "decision_ref": "user:clean"}
        result = station_cleanup.execute(self.ws, task["issue_key"], task["run_id"], task["_revision"],
                                          "op-clean-correction", request)
        self.assertTrue(result["cleanup_result"]["complete"])
        from station_fixture import ownership_snapshot
        request = dict(self.request, issue_key="TAP-124", jira_snapshot=ownership_snapshot(self.ws, "TAP-124"))
        coordination_proof(self.ws, request["issue_key"])
        station.takeover(self.ws, request, "op-next-takeover", task_store.read_current(self.ws)["revision"])
        self.assertEqual("TAP-124", task_store.read_task(self.ws)["issue_key"])

    def test_feature_tag_derivation_uses_temporary_graph(self):
        self.prepare_engineering(2)
        profile_path = self.product / "projects/tapdata/engineering-profiles.json"
        profiles = json.loads(profile_path.read_text())
        profiles["profiles"]["full-application"]["repositories"] = ["tapdata/tapdata", "tapdata/tapdata-enterprise"]
        self.write(profile_path, profiles)
        self.git(self.remote, "tag", "4.21.0", "develop")
        self.git(self.remote, "branch", "feature-one", "develop")
        self.git(self.remote, "branch", "release-v4.21.0", "develop")
        # 同名分支在第二仓不存在，使其按主仓 first-parent Tag 推导。
        other = self.root / "other.git"
        self.git(self.root, "clone", "--bare", str(self.seed), str(other))
        self.git(other, "branch", "release-v4.21.0", "develop")
        catalog_path = self.product / "projects/tapdata/repositories.json"
        catalog = json.loads(catalog_path.read_text())
        catalog["repositories"]["tapdata/tapdata-enterprise"]["origin"] = str(other)
        self.write(catalog_path, catalog)
        value = station_takeover.preflight(self.ws, dict(self.request, version="feature-one", explicit_branches={}))
        self.assertEqual("release-v4.21.0", value["repositories"]["tapdata/tapdata-enterprise"]["ref_name"])


if __name__ == "__main__":
    unittest.main(testRunner=TimedTestRunner)
