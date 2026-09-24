"""名单判定及唯一版本 6 清理闭环；旧计划拒绝执行，不依赖构建工具。"""
import io
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import archive_store, station_clean_rules as rules, station_clean, station_reset_result
from workflow import station_resources as resources, station, task_store, station_operation
import test_station_resources as fixture


class StationCleanTests(unittest.TestCase):
    setUp = fixture.ResourceTests.setUp
    prepare_engineering = fixture.ResourceTests.prepare_engineering
    write = fixture.ResourceTests.write
    git = fixture.ResourceTests.git
    takeover = fixture.ResourceTests.takeover
    ready = fixture.ResourceTests.ready
    execute = fixture.ResourceTests.execute

    def result_request(self, task):
        return dict(summary="保存并重置", reason="用户清理", decision_ref="fixture:user",
                    cleanup_version=6, abandon_changes=True,
                    confirmed_digest=resources.plan(self.ws, task, version=6)["digest"])

    def test_result_clean_preserves_tracked_target_and_archives_ignored(self):
        self.prepare_engineering()
        package = self.seed / "io/tapdata/mock/target"
        package.mkdir(parents=True)
        (package / "Source.java").write_text("class Source {}")
        self.git(self.seed, "add", ".")
        self.git(self.seed, "commit", "-m", "tracked package")
        self.git(self.seed, "push", str(self.remote), "develop")
        task = self.ready()
        (self.repo / ".git/info/exclude").write_text("build-output/\n")
        (self.repo / "build-output").mkdir()
        (self.repo / "build-output/report.txt").write_text("important report")
        (self.repo / "file.txt").write_text("worktree change")
        # 不读取构建配方；项目迭代或损坏配方不阻塞源码重置。
        (self.product / "projects/tapdata/repo-cleanup.json").write_text("broken")
        profile_path = self.product / "projects/tapdata/engineering-profiles.json"
        profiles = json.loads(profile_path.read_text())
        profiles["profiles"]["full-application"]["revision"] += 1
        self.write(profile_path, profiles)
        request = self.result_request(task)
        result = self.execute(task, request)
        self.assertEqual(result["status"], "done")
        self.assertTrue((self.repo / "io/tapdata/mock/target/Source.java").is_file())
        self.assertFalse((self.repo / "build-output").exists())
        archive = archive_store.from_reference(self.ws, result["archive_ref"])
        saved = json.loads((archive / "source-artifacts.json").read_text())
        self.assertIn("build-output/report.txt", saved["repositories"][self.name]["untracked"])

    def test_agent_cleanup_can_finish_without_executor_receipts(self):
        task = self.ready()
        (self.repo / "new.bin").write_text("save me")
        request = self.result_request(task)
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-agent-cleanup", request)
        result = station.execute(*args, cleanup_mode="prepare")
        self.assertEqual(result["phase"], "awaiting_cleanup_result")
        self.assertTrue((self.repo / "new.bin").exists())
        with self.assertRaises(ValueError):
            station.execute(*args, cleanup_mode="verify")
        entry = result["cleanup_plan"]["source"][self.name]
        # 模拟 Agent 的原生工具动作；不补任何脚本退出码或中间状态。
        (self.repo / "new.bin").unlink()
        self.git(self.repo, "update-ref", entry["preserved_ref"], entry["preserved_head"])
        self.git(self.repo, "checkout", "--detach", entry["neutral"]["sha"])
        from workflow import station_reset_result
        with mock.patch.object(station_reset_result, "apply", side_effect=AssertionError("不得调用执行器")):
            result = station.execute(*args, cleanup_mode="verify")
        self.assertEqual(result["status"], "done")
        self.assertIsNone(task_store.read_task(self.ws))

    def test_successful_executor_does_not_replace_result_check(self):
        task = self.ready()
        request = self.result_request(task)
        from workflow import station_reset_result
        with mock.patch.object(station_reset_result, "apply", return_value=None):
            with self.assertRaises(ValueError):
                self.execute(task, request)
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_result_clean_rejects_scope_drift_and_preserves_new_file(self):
        task = self.ready()
        request = self.result_request(task)
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-result-drift", request)
        station.execute(*args, cleanup_mode="prepare")
        (self.repo / "late.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "内容变化"):
            station.execute(*args)
        self.assertEqual((self.repo / "late.txt").read_text(), "keep")

    def test_failed_script_intent_can_be_closed_by_observed_result(self):
        task = self.ready()
        (self.repo / "new.bin").write_text("save me")
        request = self.result_request(task)
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-partial-script", request)
        original = station_operation.receipt
        def fail(base, operation, name, value):
            if name.startswith("source-reset:"):
                raise OSError("模拟脚本中断")
            return original(base, operation, name, value)
        with mock.patch.object(station_operation, "receipt", side_effect=fail):
            with self.assertRaises(OSError):
                station.execute(*args)
        operation = station_operation.read(self.ws)
        entry = operation["cleanup_plan"]["source"][self.name]
        self.git(self.repo, "update-ref", entry["preserved_ref"], entry["preserved_head"])
        self.git(self.repo, "checkout", "--detach", entry["neutral"]["sha"])
        result = station.execute(*args, cleanup_mode="verify")
        self.assertEqual(result["status"], "done")
        receipts = [s["receipt"] for name, s in result["steps"].items() if name.startswith("source-reset:")]
        self.assertEqual(receipts[0]["verification"], "observed_result")

    def interrupted_added_file(self, boundary="index", version=6):
        task = self.ready()
        target = self.repo / "new.bin"
        target.write_text("staged addition")
        self.git(self.repo, "add", "new.bin")
        request = self.result_request(task)
        request.update(cleanup_version=version, confirmed_digest=resources.plan(self.ws, task, version=version)["digest"])
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-added-recovery", request)
        git, unlink, receipt = station.source.git, Path.unlink, station_operation.receipt
        def fail_git(path, *arguments, **kwargs):
            result = git(path, *arguments, **kwargs)
            if boundary == "index" and arguments[:3] == ("--literal-pathspecs", "rm", "--cached"):
                raise OSError("injected interruption")
            return result
        def fail_unlink(path, *arguments, **kwargs):
            result = unlink(path, *arguments, **kwargs)
            if boundary == "file" and path.resolve() == target.resolve():
                raise OSError("injected interruption")
            return result
        def fail_receipt(base, operation, name, value):
            if boundary == "receipt" and name.startswith("source-reset:"):
                raise OSError("injected interruption")
            return receipt(base, operation, name, value)
        with mock.patch.object(station.source, "git", side_effect=fail_git), \
                mock.patch.object(Path, "unlink", new=fail_unlink), \
                mock.patch.object(station_operation, "receipt", side_effect=fail_receipt):
            with self.assertRaisesRegex(OSError, "injected interruption"):
                station.execute(*args)
        return args

    def test_added_file_resumes_after_index_removal(self):
        args = self.interrupted_added_file()
        self.assertEqual(self.git(self.repo, "status", "--porcelain"), "?? new.bin")
        self.assertEqual(station.execute(*args)["status"], "done")

    def test_added_file_resumes_after_file_removal(self):
        args = self.interrupted_added_file("file")
        self.assertEqual(station.execute(*args)["status"], "done")

    def test_added_file_resumes_before_receipt(self):
        args = self.interrupted_added_file("receipt")
        self.assertEqual(station.execute(*args)["status"], "done")

    def test_added_file_resume_rejects_changed_content(self):
        args = self.interrupted_added_file()
        target = self.repo / "new.bin"
        target.write_text("new user content")
        with self.assertRaisesRegex(ValueError, "内容变化"):
            station.execute(*args)
        self.assertEqual(target.read_text(), "new user content")

    def test_added_file_resume_rejects_replaced_file(self):
        args = self.interrupted_added_file()
        target = self.repo / "new.bin"
        replacement = self.root / "replacement"
        replacement.write_bytes(target.read_bytes())
        replacement.replace(target)
        with self.assertRaisesRegex(ValueError, "内容变化"):
            station.execute(*args)
        self.assertTrue(target.exists())

    def test_added_file_resume_rejects_restaged_content(self):
        args = self.interrupted_added_file()
        target = self.repo / "new.bin"
        target.write_text("new staged content")
        self.git(self.repo, "add", "new.bin")
        with self.assertRaisesRegex(ValueError, "内容变化"):
            station.execute(*args)
        self.assertEqual(target.read_text(), "new staged content")

    def test_added_file_resume_rejects_new_path(self):
        args = self.interrupted_added_file()
        (self.repo / "late.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "内容变化"):
            station.execute(*args)
        self.assertTrue((self.repo / "new.bin").exists())
        self.assertTrue((self.repo / "late.txt").exists())

    def test_added_file_intermediate_requires_matching_pending_intent(self):
        from workflow import station_reset_result
        import copy
        self.interrupted_added_file()
        operation = station_operation.read(self.ws)
        plan = operation["cleanup_plan"]
        prior = next(row for row in plan["entries"] if row["file"] == "new.bin")
        observed = resources.artifacts.snapshot(self.ws, self.name, {}, {}, include_ignored=True)["entries"][0]
        key = next(key for key in operation["steps"] if key.startswith("source-reset:"))
        self.assertTrue(station_reset_result._pending_added_file(self.repo, operation, self.name, prior, observed))
        for change in ("missing", "completed", "superseded", "different-plan", "different-expected"):
            with self.subTest(change=change):
                altered = copy.deepcopy(operation)
                step = altered["steps"][key]
                if change == "missing":
                    del altered["steps"][key]
                elif change == "completed":
                    step["receipt"] = step["expected"]
                elif change == "superseded":
                    step["superseded_by"] = "another-plan"
                elif change == "different-plan":
                    altered["cleanup_plan"]["digest"] = "another-plan"
                else:
                    step["expected"]["entries_digest"] = "another-snapshot"
                self.assertFalse(station_reset_result._pending_added_file(self.repo, altered, self.name, prior, observed))

    def test_result_clean_rejects_nested_repository(self):
        task = self.ready()
        nested = self.repo / "nested"
        nested.mkdir()
        self.git(nested, "init")
        with self.assertRaisesRegex(ValueError, "Git"):
            self.result_request(task)

    def test_readiness_does_not_require_ignored_output_registration(self):
        from workflow import station_source
        task = self.ready()
        (self.repo / ".git/info/exclude").write_text("local-output\n")
        (self.repo / "local-output").write_text("generated")
        station_source.readiness_snapshot(self.ws, task)

    def test_result_clean_rejects_old_epoch_without_touching_task(self):
        task = self.ready()
        path = self.ws / ".agenticops/init.json"
        init = json.loads(path.read_text())
        init["station_state_epoch"] = 18
        self.write(path, init)
        current = (self.ws / ".agenticops/current-task.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "代际|epoch|原版本"):
            station_clean.main(["--dir", str(self.ws)])
        self.assertEqual(current, (self.ws / ".agenticops/current-task.json").read_bytes())

    def test_result_clean_uses_reset_sha_when_task_baseline_differs(self):
        self.prepare_engineering()
        self.git(self.seed, "checkout", "-b", "release-test")
        (self.seed / "file.txt").write_text("release baseline")
        self.git(self.seed, "commit", "-am", "release")
        self.git(self.seed, "push", str(self.remote), "release-test")
        self.request["explicit_branches"]["tapdata/tapdata"] = "release-test"
        self.request["version"] = "release-test"
        task = self.ready()
        frozen = task["engineering_baseline"]["repositories"][self.name]["commit_sha"]
        reset = task["reset_baseline"][self.name]["sha"]
        self.assertNotEqual(frozen, reset)
        result = self.execute(task, self.result_request(task))
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), reset)
        self.assertEqual(self.git(self.repo, "rev-parse", self.branch), frozen)
        self.assertEqual(result["status"], "done")

    def test_result_release_retains_delivery(self):
        task = self.ready()
        (self.repo / "file.txt").write_text("delivered")
        self.git(self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-am", "delivery")
        observed = station.source.inspect(self.ws, task["engineering_baseline"])
        task.update(stage="completed", outcome="completed", terminal_proof={
            "run_id": task["run_id"], "repositories": observed, "deliveries": [],
            "dispositions": {self.name: "merged"}, "candidate_digest": resources.baseline.digest(observed)})
        task_store.write_task(self.ws, task)
        request = self.result_request(task)
        request["candidate_digest"] = task["terminal_proof"]["candidate_digest"]
        result = self.execute(task, request, kind="release")
        self.assertEqual(result["status"], "done")
        self.assertEqual(self.git(self.repo, "rev-parse", self.branch), observed[self.name]["head"])

    def test_result_verifier_rejects_changed_refs_without_unbinding(self):
        task = self.ready()
        request = self.result_request(task)
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-ref-drift", request)
        station.execute(*args, cleanup_mode="prepare")
        self.git(self.repo, "branch", "unexpected")
        with self.assertRaisesRegex(ValueError, "Git 引用变化"):
            station.execute(*args, cleanup_mode="verify")
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_agent_can_clear_registered_root_without_script_delete_receipt(self):
        task = self.ready()
        (self.product / "projects/tapdata/station-clean.json").write_text(json.dumps({
            "version": 1, "preserve": [], "clean": [{"pattern": "scratch", "action": "remove"}]}))
        resources.register(self.ws, task["issue_key"], task["run_id"], [{
            "kind": "directory", "producer": "fixture", "path": "scratch"}])
        request = self.result_request(task)
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-native-root", request)
        operation = station.execute(*args, cleanup_mode="prepare")
        (self.ws / "scratch").rmdir()
        entry = operation["cleanup_plan"]["source"][self.name]
        self.git(self.repo, "update-ref", entry["preserved_ref"], entry["preserved_head"])
        self.git(self.repo, "checkout", "--detach", entry["neutral"]["sha"])
        self.assertEqual(station.execute(*args, cleanup_mode="verify")["status"], "done")

    def test_result_resume_after_active_clear_keeps_export_and_terminal_evidence(self):
        from workflow import station_export
        task = self.ready()
        (self.repo / "new.bin").write_text("private saved content")
        output = self.root.resolve() / "private-export"
        output.mkdir(mode=0o700)
        station_export.export(self.ws, task["issue_key"], task["run_id"],
                              "source/" + self.name + "/new.bin", str(output / "saved.json"))
        resources.register(self.ws, task["issue_key"], task["run_id"], [{
            "kind": "external", "id": "fixture-container", "resource_type": "container", "producer": "fixture",
            "action": "delete", "before": {"protected": False, "id": "fixture-container"},
            "status": "cleaned", "readback_ref": "fixture:removed"}])
        request = self.result_request(task)
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-after-clear", request)
        original = station._clear_active
        def fail(*values):
            original(*values)
            raise OSError("清除活动材料后中断")
        with mock.patch.object(station, "_clear_active", side_effect=fail):
            with self.assertRaises(OSError):
                station.execute(*args)
        result = station.execute(*args, cleanup_mode="verify")
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["cleanup_manifest"]["result"]["external"][0]["readback_ref"], "fixture:removed")

    def test_result_already_accepted_does_not_delete_new_runtime_content(self):
        task = self.ready()
        request = self.result_request(task)
        args = (self.ws, "clean", task["issue_key"], task["run_id"], task["_revision"], "op-new-runtime", request)
        with mock.patch.object(station, "_clear_active", side_effect=OSError("验收后中断")):
            with self.assertRaises(OSError):
                station.execute(*args)
        (self.ws / "runtime/new-result").write_text("keep")
        with self.assertRaisesRegex(ValueError, "残留"):
            station.execute(*args)
        self.assertEqual((self.ws / "runtime/new-result").read_text(), "keep")

    def test_cleanup_scope_is_read_only_and_keeps_retained_choices(self):
        from workflow import station_clean_view
        task = self.ready()
        plan = resources.plan(self.ws, task, version=6)
        before = json.dumps(plan, sort_keys=True)
        view = station_clean_view.describe(self.ws, task, plan)
        self.assertTrue(view["complete"])
        self.assertTrue(view["executable"], view)
        self.assertEqual(view["plan_digest"], plan["digest"])
        self.assertEqual(before, json.dumps(plan, sort_keys=True))
        retained = {row["target"] for row in view["retain"]}
        self.assertIn("config", retained)
        self.assertIn("source 独立仓库", retained)
        self.assertIn("Product Root .archive", retained)
        reset = next(row for row in view["remove_or_reset"] if row["target"] == "source/" + self.name)
        self.assertEqual(reset["source"]["neutral"]["sha"], task["reset_baseline"][self.name]["sha"])

    def test_partial_cleanup_scope_lists_unknown_and_preserved_without_digest(self):
        task = self.ready()
        (self.ws / "user-notes.txt").write_text("private retained material")
        before = {p: p.read_bytes() for p in (self.ws / ".agenticops").rglob("*") if p.is_file()}
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            station_clean.main(["--dir", str(self.ws)])
        result = json.loads(output.getvalue())
        view = result["cleanup_scope"]
        self.assertFalse(view["complete"])
        self.assertFalse(view["executable"])
        self.assertNotIn("plan_digest", view)
        self.assertIn("user-notes.txt", [row["target"] for row in view["unknown"]])
        self.assertIn("config", [row["target"] for row in view["retain"]])
        self.assertEqual(before, {p: p.read_bytes() for p in (self.ws / ".agenticops").rglob("*") if p.is_file()})
        self.assertTrue((self.ws / "user-notes.txt").exists())

    def test_resume_view_uses_original_plan_and_completed_receipts(self):
        from workflow import station_clean_view
        task = self.ready()
        plan = resources.plan(self.ws, task, version=6)
        operation = {"operation_id": "op-view-reset", "status": "running", "phase": "intent", "cleanup_plan": plan,
                     "steps": {"first": {"receipt": {}}, "second": {"receipt": None}}}
        view = station_clean_view.describe(self.ws, task, plan, operation)
        self.assertEqual(view["plan_digest"], plan["digest"])
        self.assertEqual(view["operation"]["completed_steps"], ["first"])
        self.assertEqual(view["operation"]["pending_steps"], ["second"])
        self.assertFalse(view["executable"])

    def test_terminal_readback_preserves_current_task_and_cleanup_contract(self):
        from workflow import quality
        task = self.ready()
        task["task_class"] = "defect_fix"
        task_store.write_task(self.ws, task)
        def apply(action, payload):
            return quality.apply(self.ws, task["issue_key"], task["run_id"], quality.load(self.ws, task)["revision"],
                                 {"action": action, "payload": payload})
        apply("draft", {"id": "summary", "body": "夹具完成报告"})
        record = quality.replay(quality.load(self.ws, task))["publications"]["summary"]
        apply("confirm", {"id": "summary", "digest": record["digest"], "proof": {"actor": "fixture", "source": "user_message", "reference": "fixture:user", "at": "2026-09-22T00:00:00Z"}})
        apply("prepare_write", {"id": "summary", "digest": record["digest"]})
        record = quality.replay(quality.load(self.ws, task))["publications"]["summary"]
        task.update(outcome="completed", stage="completed")
        task_store.write_task(self.ws, task)
        current_before = (self.ws / ".agenticops/current-task.json").read_bytes()
        apply("readback", {"id": "summary", "operation_id": record["operation_id"], "site": record["site"],
              "issue_key": task["issue_key"], "comment_id": "123", "body": record["body"], "source_ref": "fixture:comment/123"})
        self.assertEqual(current_before, (self.ws / ".agenticops/current-task.json").read_bytes())
        current = task_store.read_task(self.ws)
        plan = resources.plan(self.ws, current, version=6)
        self.assertEqual(plan["schema_version"], 6)
        resources.verify_known_external(self.ws, current)

    def cleanup_request(self, task):
        return dict(summary='清理测试', reason='用户确认停止', decision_ref='fixture:user',
                    cleanup_version=6, abandon_changes=True,
                    confirmed_digest=resources.plan(self.ws, task, version=6)['digest'])

    def state_bytes(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for root in (self.ws, self.product / '.archive') if root.exists()
                for p in root.rglob('*') if p.is_file() and '.git' not in p.parts}

    def test_old_requested_versions_are_rejected_without_writes(self):
        task = self.ready()
        (self.repo / 'file.txt').write_text('must preserve')
        request = self.result_request(task)
        before = self.state_bytes()
        for version in (3, 4, 5, 6.0, True, '6'):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, '仅支持清理计划版本 6'):
                    resources.plan(self.ws, task, version=version)
                for kind in ('archive', 'clean', 'release'):
                    with self.assertRaisesRegex(ValueError, '仅支持清理计划版本 6'):
                        station.execute(self.ws, kind, task['issue_key'], task['run_id'], task['_revision'],
                                        'op-old-version', dict(request, cleanup_version=version))
                self.assertEqual(before, self.state_bytes())

    def test_old_operation_and_handoff_plans_are_not_adopted(self):
        import copy
        task = self.ready()
        request = self.result_request(task)
        original = station_operation.read(self.ws)
        plan = resources.plan(self.ws, task)
        for version in (3, 4, 5):
            for status, field in (('running', 'cleanup_plan'), ('done', 'cleanup_plan'), ('running', 'handoff')):
                with self.subTest(version=version, status=status, field=field):
                    operation = copy.deepcopy(original)
                    old_plan = dict(plan, schema_version=version)
                    operation['status'] = status
                    operation[field] = {'cleanup_plan': old_plan} if field == 'handoff' else old_plan
                    station_operation.save(self.ws, operation)
                    before = self.state_bytes()
                    with self.assertRaisesRegex(ValueError, '清理计划版本'):
                        resources.plan(self.ws, task)
                    with self.assertRaisesRegex(ValueError, '清理计划版本'):
                        self.execute(task, request)
                    self.assertEqual(before, self.state_bytes())
        station_operation.save(self.ws, original)

    def project_rules(self, preserve=(), clean=()):
        self.write(self.product/'projects/tapdata/station-clean.json',
                   dict(version=1, preserve=list(preserve), clean=list(clean)))

    def test_priority_and_unmatched(self):
        self.project_rules(['cache/'], [{'pattern':'source/', 'action':'remove'}, {'pattern':'scratch/', 'action':'remove'}])
        config = rules.load(self.ws)
        for name, expected in [('config', 'preserve'), ('cache', 'preserve'), ('source', 'source-reset'), ('scratch', 'remove'), ('unknown', 'block')]:
            with self.subTest(name=name):
                self.assertEqual(expected, rules.classify(config, name, True)['action'])

    def test_whitelist_exception_and_duplicate_rules(self):
        self.project_rules(['cache/'], [{'pattern': 'c*', 'action': 'remove'}])
        self.assertEqual('preserve', rules.classify(rules.load(self.ws), 'cache', True)['action'])
        self.project_rules(['cache/', 'cache/'])
        with self.assertRaisesRegex(ValueError, '重复'):
            rules.load(self.ws)

    def test_inventory_always_enforces_current_rules(self):
        self.ready()
        self.project_rules(['source/'])
        with self.assertRaisesRegex(ValueError, '生命周期'):
            resources.verify_station_inventory(self.ws)
        with self.assertRaisesRegex(ValueError, '生命周期'):
            rules.inspect(self.ws)

    def test_snapshot_versions_and_types(self):
        for plan in ({'schema_version': 4}, {'schema_version': 3, 'rules': {}},
                     {'schema_version': 6, 'rules': {'digests': [], 'objects': {}}},
                     {'schema_version': 6, 'rules': {'digests': ['a'*64, None], 'objects': {'x': {'action': 'remove', 'layer': 'invalid'}}}}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                rules.validate_snapshot(plan)

    def test_invalid_patterns_and_core_conflicts(self):
        for value in ('**', '../x', '/x', 'a/b', '!x', '[ab]', '', '.'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rules.pattern(value)
        self.project_rules(['source/'])
        with self.assertRaisesRegex(ValueError, '生命周期冲突'):
            rules.inspect(self.ws)

    def test_unknown_and_unowned_clean_objects_block(self):
        self.project_rules(clean=[{'pattern':'scratch/', 'action':'remove'}])
        (self.ws/'scratch').mkdir()
        (self.ws/'unknown').write_text('keep')
        with self.assertRaisesRegex(ValueError, '归属.*未知'):
            rules.inspect(self.ws)

    def test_modern_clean_archives_source_before_clearing_runtime(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('saved change')
        (self.ws/'.idea').mkdir()
        (self.ws/'.idea/station.xml').write_text('keep')
        (self.ws/'runtime/report').write_text('temporary')
        original = resources.neutral
        def observe(*args, **kwargs):
            self.assertTrue((self.ws/'runtime/report').exists())
            self.assertTrue(task_store.read_task(self.ws)['archive_ref'])
            return original(*args, **kwargs)
        with mock.patch.object(resources, 'neutral', side_effect=observe) as called:
            self.execute(task, self.cleanup_request(task))
            self.assertEqual(1, called.call_count)
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertEqual('keep', (self.ws/'.idea/station.xml').read_text())
        self.assertEqual([], list((self.ws/'runtime').iterdir()))
        self.assertEqual('baseline', (self.repo/'file.txt').read_text().strip())

    def test_no_abandon_is_zero_write(self):
        self.ready()
        before = {p.relative_to(self.ws):p.read_bytes() for p in (self.ws/'.agenticops').rglob('*') if p.is_file()}
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(0, station_clean.main(['--dir', str(self.ws), '--abandon-changes', 'no']))
        after = {p.relative_to(self.ws):p.read_bytes() for p in (self.ws/'.agenticops').rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_cli_confirmed_cleanup(self):
        task = self.ready()
        request = self.root/'cleanup-request.json'
        self.write(request, self.result_request(task))
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws), '--issue-key', task['issue_key'],
                                  '--expected-run-id', task['run_id'], '--expected-revision', str(task['_revision']),
                                  '--operation-id', 'op-cli-cleanup', '--input', str(request), '--abandon-changes', 'yes'])
        self.assertEqual('done', json.loads(output.getvalue())['status'])
        self.assertIsNone(task_store.read_task(self.ws))

    def test_cli_failed_takeover_handoff_and_resume(self):
        self.prepare_engineering()
        with mock.patch.object(station.source, 'prepare_repositories', side_effect=ValueError('network')), self.assertRaises(ValueError):
            station.takeover(self.ws, self.request, 'op-takeover-fail', 0)
        task = task_store.read_task(self.ws)
        before = station_operation.path(self.ws).read_bytes()
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws), '--abandon-changes', 'no'])
        self.assertEqual('cancelled', json.loads(output.getvalue())['status'])
        self.assertEqual(before, station_operation.path(self.ws).read_bytes())
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws)])
        self.assertIn('cleanup_plan', json.loads(output.getvalue()))
        path = self.root/'handoff-request.json'
        self.write(path, self.result_request(task))
        args = ['--dir', str(self.ws), '--issue-key', task['issue_key'], '--expected-run-id', task['run_id'],
                '--expected-revision', str(task['_revision']), '--operation-id', 'op-clean-handoff',
                '--input', str(path), '--abandon-changes', 'yes']
        original = station_operation.save
        def fail(base, operation):
            if operation['kind'] == 'clean':
                raise OSError('after handoff')
            return original(base, operation)
        with mock.patch.object(station_operation, 'save', side_effect=fail), self.assertRaises(OSError):
            station_clean.main(args)
        changed = json.loads(path.read_text())
        changed['reason'] = 'different'
        self.write(path, changed)
        with self.assertRaisesRegex(ValueError, '原清理交接'):
            station_clean.main(args)
        changed['reason'] = '用户清理'
        self.write(path, changed)
        with mock.patch('sys.stdout', new_callable=io.StringIO):
            station_clean.main(args)
        operation = station_operation.read(self.ws)
        self.assertEqual(6, operation['cleanup_plan']['schema_version'])
        self.assertEqual('done', operation['status'])

    def test_cli_takeover_before_task_write_requires_original_resume(self):
        self.prepare_engineering()
        with mock.patch.object(task_store, 'write_task', side_effect=OSError('before task')), self.assertRaises(OSError):
            station.takeover(self.ws, self.request, 'op-takeover-fail', 0)
        operation = station_operation.read(self.ws)
        before = station_operation.path(self.ws).read_bytes()
        for extra in ([], ['--abandon-changes', 'no']):
            with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
                station_clean.main(['--dir', str(self.ws)] + extra)
            self.assertEqual('resume_required', json.loads(output.getvalue())['status'])
        with self.assertRaisesRegex(ValueError, '尚未绑定任务'):
            station_clean.main(['--dir', str(self.ws), '--issue-key', self.request['issue_key'],
                                  '--expected-run-id', operation['run_id'], '--expected-revision', '0',
                                  '--operation-id', 'op-no-task-clean', '--input', str(self.root/'missing.json')])
        self.assertEqual(before, station_operation.path(self.ws).read_bytes())

    def test_registered_root_removed_by_same_directory_mechanism(self):
        task = self.ready()
        self.project_rules(clean=[{'pattern':'scratch/', 'action':'remove'}])
        resources.register(self.ws, task['issue_key'], task['run_id'], [{'kind':'directory','path':'scratch','producer':'fixture'}])
        (self.ws/'scratch/generated').write_text('output')
        self.execute(task, self.cleanup_request(task))
        self.assertFalse((self.ws/'scratch').exists())

    def test_build_root_is_covered_without_native_preclean(self):
        task = self.ready()
        resources.register(self.ws,task['issue_key'],task['run_id'],[{'kind':'directory','path':'source/'+self.name+'/target','producer':'fixture'}])
        (self.repo/'target/output').write_text('preserve before reset')
        plan = resources.plan(self.ws, task)
        self.assertFalse(any(e['kind'] == 'source-generated' for e in plan['directories']))
        self.assertTrue(any(e['file'] == 'target/output' and e['preservation']['action'] == 'archive' for e in plan['entries']))
        self.execute(task, self.cleanup_request(task))
        self.assertFalse((self.repo/'target').exists())

    def test_changed_config_blocks_without_operation(self):
        task = self.ready()
        request = self.cleanup_request(task)
        self.project_rules(['.vscode/'])
        with self.assertRaisesRegex(ValueError, '确认'):
            self.execute(task,request)
        self.assertEqual('scope_change', station_operation.read(self.ws)['kind'])

    def test_source_reset_requires_archive(self):
        task = self.ready()
        with self.assertRaises(ValueError):
            plan = resources.plan(self.ws, task)
            station_reset_result.apply(self.ws, task, {
                'cleanup_plan': plan, 'run_id': task['run_id'], 'kind': 'clean',
                'status': 'running', 'request': {'confirmed_digest': plan['digest']}})
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_resume_after_source_reset_does_not_repeat_git_restore(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('saved')
        request = self.cleanup_request(task)
        original = resources.clean
        def fail(*args, **kwargs):
            if kwargs.get('directories_only'):
                raise ValueError('fixture interruption')
            return original(*args, **kwargs)
        with mock.patch.object(resources, 'clean', side_effect=fail):
            with self.assertRaisesRegex(ValueError,'fixture interruption'):
                self.execute(task,request)
        self.execute(task,request)
        self.assertIsNone(task_store.read_task(self.ws))

    def interrupted_before_directories(self):
        task = self.ready()
        request = self.cleanup_request(task)
        original = resources.clean
        def fail(*args, **kwargs):
            if kwargs.get('directories_only'):
                raise ValueError('fixture interruption')
            return original(*args, **kwargs)
        with mock.patch.object(resources, 'clean', side_effect=fail), self.assertRaises(ValueError):
            self.execute(task, request)
        return task, request

    def test_pending_no_reports_resume_not_zero_write(self):
        self.interrupted_before_directories()
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws), '--abandon-changes', 'no'])
        self.assertEqual('resume_required', json.loads(output.getvalue())['status'])

    def test_preview_returns_task_question_with_blockers(self):
        task = self.ready()
        (self.ws/'unknown').write_text('keep')
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            station_clean.main(['--dir', str(self.ws)])
        result = json.loads(output.getvalue())
        self.assertEqual(task['run_id'], result['run_id'])
        self.assertTrue(result['question'])
        self.assertIn('未知', result['blockers'][0])
        self.assertNotIn('cleanup_plan', result)

    def test_resume_rejects_ignored_files_and_preserved_ref_drift(self):
        task, request = self.interrupted_before_directories()
        (self.repo/'.git/info/exclude').write_text('ignored-output\n')
        (self.repo/'ignored-output').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'ignored'):
            self.execute(task, request)
        (self.repo/'ignored-output').unlink()
        plan = station_operation.read(self.ws)['cleanup_plan']
        self.git(self.repo, 'update-ref', '-d', plan['source'][self.name]['preserved_ref'])
        with self.assertRaises(ValueError):
            self.execute(task, request)
        self.assertIsNotNone(task_store.read_task(self.ws))

    def test_all_root_identities_checked_before_git(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('keep')
        request = self.cleanup_request(task)
        original = station_reset_result.apply
        def replace(*args, **kwargs):
            (self.ws/'runtime').rename(self.root/'old-runtime')
            (self.ws/'runtime').mkdir()
            return original(*args, **kwargs)
        with mock.patch.object(station_reset_result, 'apply', side_effect=replace), self.assertRaisesRegex(ValueError, '身份'):
            self.execute(task, request)
        self.assertEqual('keep', (self.repo/'file.txt').read_text())

    reset_request = cleanup_request
    reset_amend_after_neutral = fixture.ResourceTests.reset_amend_after_neutral

    def test_modern_clean_amend_after_neutral(self):
        self.reset_amend_after_neutral('clean')

    def test_modern_release_amend_after_neutral(self):
        self.reset_amend_after_neutral('release')


# 阶段回归沿用清理测试入口和故事映射。
import os
from workflow import station_operation as operations, station_cleanup_stages as stages, station_disposition
from workflow.engineering_baseline import digest


class CleanupStagesTests(unittest.TestCase):
    setUp = fixture.ResourceTests.setUp
    prepare_engineering = fixture.ResourceTests.prepare_engineering
    write = fixture.ResourceTests.write
    git = fixture.ResourceTests.git
    takeover = fixture.ResourceTests.takeover
    ready = fixture.ResourceTests.ready
    reset_request = fixture.ResourceTests.reset_request

    def args(self, task):
        return (self.ws, 'clean', task['issue_key'], task['run_id'], task['_revision'],
                'op-stages-cleanup', self.reset_request(task))

    def test_all_stages_complete_without_agent(self):
        task = self.ready()
        (self.repo/'file.txt').write_text('change')
        result = station.execute(*self.args(task))
        self.assertTrue(all(row['verified'] for row in stages.describe(result)))
        self.assertEqual('', self.git(self.repo, 'branch', '--show-current'))
        self.assertIsNone(task_store.read_task(self.ws))

    def test_links_archive_without_following_external_target(self):
        self.prepare_engineering()
        outside = self.root/'outside'; outside.write_text('outside preserved')
        (self.seed/'tracked-link').symlink_to('file.txt')
        self.git(self.seed, 'add', '.'); self.git(self.seed, 'commit', '-m', 'links')
        self.git(self.seed, 'push', str(self.remote), 'develop')
        task = self.ready()
        (self.repo/'tracked-link').unlink(); (self.repo/'tracked-link').symlink_to(outside)
        (self.repo/'untracked-link').symlink_to(self.root/'missing')
        result = station.execute(*self.args(task))
        self.assertEqual('file.txt', os.readlink(self.repo/'tracked-link'))
        self.assertEqual('outside preserved', outside.read_text())
        self.assertFalse((self.repo/'untracked-link').is_symlink())
        archived = archive_store.from_reference(self.ws, result['archive_ref'])
        bundle = json.loads((archived/'source-artifacts.json').read_text())['repositories'][self.name]
        self.assertTrue(bundle['untracked']['untracked-link']['fingerprint']['link'])

    def test_target_link_directory_transition(self):
        task = self.ready()
        (self.repo/'file.txt').unlink()
        (self.repo/'file.txt').mkdir(); (self.repo/'file.txt/child').write_text('task content')
        self.git(self.repo, 'add', '.')
        self.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-m', 'directory')
        station.execute(*self.args(task))
        self.assertEqual('baseline\n', (self.repo/'file.txt').read_text())

    def test_batch_restore_uses_literal_paths(self):
        self.prepare_engineering()
        for name in ['a*', 'ab', ':literal', 'space name']:
            (self.seed/name).write_text('base')
        self.git(self.seed, 'add', '.'); self.git(self.seed, 'commit', '-m', 'names')
        self.git(self.seed, 'push', str(self.remote), 'develop')
        task = self.ready()
        for name in ['a*', 'ab', ':literal', 'space name']:
            (self.repo/name).write_text('dirty')
        original = resources.source.git
        with mock.patch.object(resources.source, 'git', wraps=original) as called:
            station.execute(*self.args(task))
        restores = [call for call in called.call_args_list if 'restore' in call.args]
        self.assertEqual(1, len(restores))
        for name in ['a*', 'ab', ':literal', 'space name']:
            self.assertEqual('base', (self.repo/name).read_text())

    def test_partial_inventory_lists_all_special_objects_and_changes(self):
        task = self.ready()
        (self.repo/'notes').write_text('keep')
        os.mkfifo(self.repo/'fifo-a'); os.mkfifo(self.repo/'fifo-b')
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            station_clean.main(['--dir', str(self.ws)])
        view = json.loads(output.getvalue())['cleanup_scope']
        self.assertFalse(view['executable'])
        row = view['source_inventory'][0]
        self.assertTrue(any(e['path'].endswith('/notes') for e in row['objects']))
        self.assertEqual(2, len(row['problems']))
        self.assertEqual('keep', (self.repo/'notes').read_text())

    def branch_entry(self, task, location='local'):
        return {'kind':'external', 'producer':'fixture', 'id':'fixture:'+location,
                'resource_type':'git-branch', 'action':'delete', 'decision_ref':'fixture:user',
                'status':'observed', 'before':{'repository':self.name, 'ref':'refs/heads/'+self.branch,
                    'sha':self.git(self.repo,'rev-parse','HEAD'), 'protected':False, 'location':location}}

    def test_local_branch_delete_keeps_archive_ref(self):
        task = self.ready(); entry = self.branch_entry(task)
        resources.register(self.ws, task['issue_key'], task['run_id'], [entry])
        result = station.execute(*self.args(task))
        self.assertIsNone(stages.read_ref(self.repo, entry['before']['ref']))
        self.assertIn(entry['before']['sha'], self.git(self.repo, 'for-each-ref', '--format=%(objectname)', 'refs/agenticops/archive/'))
        self.assertEqual('done', result['status'])

    def test_remote_handoff_resumes_same_operation(self):
        task = self.ready(); entry = self.branch_entry(task, 'remote')
        resources.register(self.ws, task['issue_key'], task['run_id'], [entry])
        (self.ws/'runtime/cache').write_text('not yet removed')
        args = self.args(task)
        with self.assertRaisesRegex(ValueError, '分支/PR'):
            station.execute(*args)
        op = operations.read(self.ws)
        self.assertEqual('cleanup_disposition', op['phase'])
        self.assertTrue(op['steps'][stages.key(op,'source')]['receipt'])
        self.assertTrue((self.ws/'runtime/cache').exists())
        intent = {'disposition_id':'disposition-remote-branch', 'phase':'intent','object_id':entry['id'],
                  'resource_type':entry['resource_type'], 'action':entry['action'],
                  'before':entry['before'],'decision_ref':'fixture:user'}
        intent['confirmed_digest'] = digest(intent)
        station_disposition.record(self.ws, task['issue_key'], task['run_id'], intent)
        # 夹具模拟原生服务返回的回读；不声称这是真实 GitHub 验证。
        result = dict(disposition_id=intent['disposition_id'],phase='readback',object_id=entry['id'],
                      intent_digest=digest(intent),status='deleted',readback_ref='fixture:remote-absent')
        station_disposition.record(self.ws, task['issue_key'], task['run_id'], result)
        self.assertEqual('done', station.execute(*args)['status'])

    def test_completed_source_is_not_cleaned_again(self):
        task = self.ready(); entry = self.branch_entry(task, 'remote')
        resources.register(self.ws, task['issue_key'], task['run_id'], [entry])
        args = self.args(task)
        with self.assertRaises(ValueError): station.execute(*args)
        (self.repo/'late').write_text('do not delete')
        with self.assertRaises(ValueError): station.execute(*args)
        self.assertEqual('do not delete',(self.repo/'late').read_text())

    def test_resource_cleanup_waits_for_source(self):
        task = self.ready(); args = self.args(task)
        station.execute(*args, cleanup_mode='prepare')
        (self.repo/'late').write_text('new')
        (self.ws/'runtime/cache').write_text('keep')
        with self.assertRaises(ValueError): station.execute(*args)
        self.assertEqual('cleanup_source',operations.read(self.ws)['phase'])
        self.assertTrue((self.ws/'runtime/cache').exists())

    def test_source_continues_other_repository_before_stage_failure(self):
        self.prepare_engineering(count=2)
        task = self.ready()
        other = next(name for name in task['engineering_baseline']['repositories'] if name != self.name)
        second = self.ws/'source'/other
        (second/'file.txt').write_text('save other repository')
        args = self.args(task)
        station.execute(*args, cleanup_mode='prepare')
        (self.repo/'late').write_text('unconfirmed')
        with self.assertRaises(ValueError): station.execute(*args)
        self.assertEqual('baseline\n', (second/'file.txt').read_text())
        self.assertEqual('', self.git(second,'branch','--show-current'))
        self.assertEqual('unconfirmed', (self.repo/'late').read_text())

    def test_quiesced_external_does_not_block_source_but_blocks_resource_exit(self):
        task = self.ready()
        entry = {'kind':'external','producer':'fixture','id':'fixture:container',
                 'resource_type':'container','action':'delete','before':{'protected':False,'id':'fixture'},
                 'status':'quiesced','readback_ref':'fixture:stopped'}
        resources.register(self.ws,task['issue_key'],task['run_id'],[entry])
        args = self.args(task)
        with self.assertRaisesRegex(ValueError,'清理完成'): station.execute(*args)
        op = operations.read(self.ws)
        self.assertEqual('cleanup_resources',op['phase'])
        self.assertTrue(op['steps'][stages.key(op,'source')]['receipt'])
        entry.update(status='cleaned',readback_ref='fixture:removed')
        resources.register(self.ws,task['issue_key'],task['run_id'],[entry],expected_operation_id=args[5])
        self.assertEqual('done',station.execute(*args)['status'])

    def test_release_receipt_interruption_recovers_without_task(self):
        task = self.ready(); args = self.args(task)
        original = operations.receipt
        def fail(base, operation, name, value):
            if name.startswith('cleanup-stage:') and name.endswith(':release'):
                raise OSError('release receipt interrupted')
            return original(base,operation,name,value)
        with mock.patch.object(operations,'receipt',side_effect=fail), self.assertRaises(OSError):
            station.execute(*args)
        self.assertIsNone(task_store.read_task(self.ws))
        result = station.execute(*args)
        self.assertEqual('done',result['status'])
        self.assertTrue(all(row['verified'] for row in stages.describe(result)))

    def test_amend_after_local_branch_deleted_preserves_remaining_work(self):
        task = self.ready(); entry = self.branch_entry(task)
        resources.register(self.ws,task['issue_key'],task['run_id'],[entry])
        args = self.args(task)
        original = resources.clean
        def fail(*a, **kw):
            if kw.get('directories_only'): raise OSError('before resources')
            return original(*a, **kw)
        with mock.patch.object(resources,'clean',side_effect=fail), self.assertRaises(OSError):
            station.execute(*args)
        self.assertIsNone(stages.read_ref(self.repo,entry['before']['ref']))
        current = task_store.read_task(self.ws); op = operations.read(self.ws)
        updated = resources.plan(self.ws,current)
        station.amend_cleanup(self.ws,task['issue_key'],task['run_id'],current['_revision'],args[5],
            op['cleanup_plan']['digest'],{'confirmed_digest':updated['digest'],'expected_plan_revision':0})
        self.assertEqual('done',station.execute(*args)['status'])

    def test_committed_directory_replaced_by_baseline_link_does_not_touch_target(self):
        self.prepare_engineering()
        outside = self.root/'outside-tree'; outside.mkdir(); (outside/'keep').write_text('preserve')
        (self.seed/'switch').symlink_to(outside,target_is_directory=True)
        self.git(self.seed,'add','.'); self.git(self.seed,'commit','-m','baseline link')
        self.git(self.seed,'push',str(self.remote),'develop')
        task = self.ready()
        (self.repo/'switch').unlink(); (self.repo/'switch').mkdir(); (self.repo/'switch/task').write_text('task')
        self.git(self.repo,'add','.')
        self.git(self.repo,'-c','user.name=Test','-c','user.email=test@example.com','commit','-m','task directory')
        station.execute(*self.args(task))
        self.assertTrue((self.repo/'switch').is_symlink())
        self.assertEqual('preserve',(outside/'keep').read_text())

if __name__ == '__main__':
    unittest.main()
