#!/usr/bin/env python3
"""AO-126：质量决策、恢复及证据隔离的可执行验收；不写外部 Jira。"""
import copy
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import authorization, ci, evidence, failures, issue_versions, pr_ready, quality, quality_contract, task, task_store
from station_fixture import save_task as save_station_task, initialize_station


def proof():
    return {"actor": "测试用户", "source": "user_message", "reference": "fixture:confirmation-1",
            "at": datetime.now(timezone.utc).isoformat()}


def concurrent_apply(base, run, revision, command, queue):
    try:
        quality.apply(base, "TAP-123", run, revision, command)
        queue.put("saved")
    except ValueError:
        queue.put("stale")


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ao-quality-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        product = self.base / "product"
        self.product = product
        shutil.copytree(ROOT / "projects" / "tapdata", product / "projects" / "tapdata")
        for name in ("quality.json", "quality-feature.json"):
            path = product / "projects/tapdata" / name
            rules = json.loads(path.read_text())
            rules.pop("verification_checkpoints", None)
            rules["pr_ready"].pop("required_verification", None)
            path.write_text(json.dumps(rules))
        (self.base / ".agenticops").mkdir()
        (self.base / ".agenticops/station.json").write_text(json.dumps({"project": "tapdata", "product_root": str(product)}))
        self.task = {"issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "defect_fix",
                     "stage": "implementation", "facts": {"fix_plan": {
                         "format": "structured-v1",
                         "problem_statements": [{"id": "P1", "text": "夹具目标行为错误", "source_ref": "fixture:issue"}],
                         "evidence": [{"id": "F1", "source_ref": "fixture:source", "observation": "夹具可稳定验证当前行为"}],
                         "hypotheses": [{"id": "H1", "explains": ["P1"], "evidence_ids": ["F1"], "status": "confirmed", "falsifier": "针对性断言不再失败"}],
                         "blocking_inputs": [], "changes": [{"scope": "夹具实现", "hypothesis_ids": ["H1"]}],
                         "risks": ["仅夹具范围"], "rollback": "回退夹具改动", "test_links": []}}, "repositories": [
                         {"repository": "tapdata/tapdata", "approved_scope": "bug-fix"},
                         {"repository": "tapdata/tapdata-manager", "approved_scope": "bug-fix"}],
                     "pending": None, "history": []}
        self.save_task()


    def save_task(self):
        save_station_task(self.base, self.task)

    def view(self):
        return quality.report(quality.load(self.base, self.task), quality.config(self.base, self.task), quality.context(self.base, self.task))

    def feature_profile(self):
        """仅在夹具配置功能；生产功能准入与 Jira 接入由 AO-142 交付。"""
        self.plan()
        project = self.product / "projects/tapdata"
        admission_path = project / "admission.json"
        admission = json.loads(admission_path.read_text())
        feature = admission["task_classes"]["feature_change"]
        feature["quality_profile"] = "quality-feature.json"
        feature["optional_facts"].append({"key": "implementation_plan", "label": "实施方案"})
        admission_path.write_text(json.dumps(admission))
        rules = json.loads((project / "quality.json").read_text())
        rules.update(task_classes=["feature_change"], structured_fix_plan=False,
                     intake_fact_keys=["acceptance_criteria", "target_repo", "verification_method", "risk_level"],
                     plan_fact_keys=["implementation_plan", "scope_boundary"],
                     plan_contract={"fact_key": "implementation_plan", "required_fields": ["objective", "changes", "acceptance", "risks", "rollback"]})
        self.profile_path = project / "quality-feature.json"
        self.profile_path.write_text(json.dumps(rules))
        self.task.update(task_class="feature_change", stage="design_review", facts={
            "acceptance_criteria": "正常、边界和失败场景", "target_repo": "tapdata/tapdata",
            "verification_method": "运行模块测试", "risk_level": "T3", "scope_boundary": "目标模块",
            "implementation_plan": {"objective": "目标行为", "changes": ["新增行为"],
                                    "acceptance": ["目标断言"], "risks": ["兼容性"], "rollback": "回退改动"}})
        self.task["repositories"] = self.task["repositories"][:1]
        for repo in self.task["repositories"]:
            repo.update(authorized_endpoint="github.com/" + repo["repository"], base_branch="develop",
                        work_branch="feature/TAP-123", base_sha="a" * 40, verification_method="模块测试",
                        approved_scope="目标功能模块")
        self.save_task()

    def test_feature_confirmation_grant_advance_and_drift(self):
        self.feature_profile()
        self.select(); self.checkpoint("q1-intake"); self.checkpoint("q2-plan")
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"],
                               agent_id="fixture", plan_version="v1", ttl_hours=8)
        self.assertEqual(authorization.cmd_grant(args), 0)
        record = json.loads(task_store.authorization_path(self.base, "TAP-123").read_text())
        renewal = SimpleNamespace(**vars(args), expected_authorization_digest=authorization.record_digest(record),
                                  confirmed_by="fixture", confirmation_ref="fixture:renew")
        renewal.ttl_hours = 16
        self.assertEqual(authorization.cmd_renew(renewal), 0)
        spec = task.admission(self.base)
        self.assertEqual(task._check_advance(self.task, "implementation", self.base, spec), [])
        before = quality.q2_digest(self.base, self.task)
        self.task["facts"]["note"] = "仅展示备注"; self.save_task()
        self.assertEqual(quality.q2_digest(self.base, self.task), before)
        self.task["facts"]["implementation_plan"]["changes"] = ["扩大行为"]
        self.save_task()
        self.assertTrue(task._check_advance(self.task, "implementation", self.base, spec))
        with self.assertRaisesRegex(ValueError, "尚未有效确认"):
            quality.q2_digest(self.base, self.task)

    def test_validate_cli_is_station_independent_and_reports_safe_field_details(self):
        self.plan()
        self.execute(result="NOT_RUN")
        command = quality.load(self.base, self.task)["events"][-1]["command"]
        path = self.base / "input.json"
        path.write_text(json.dumps(command))
        missing_station = self.base / "does-not-exist"
        result = subprocess.run([sys.executable, str(ROOT / "workflow/quality.py"), "validate",
                                 "--dir", str(missing_station), "--input", str(path)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)["contract_valid"])
        self.assertFalse(missing_station.exists())
        command["payload"]["execution"]["raw_result"] = "secret-value-must-not-appear"
        with self.assertRaisesRegex(ValueError, r"\$\.payload\.execution\.raw_result.*允许") as caught:
            quality.validate_command(command)
        self.assertNotIn("secret-value-must-not-appear", str(caught.exception))
        command["payload"]["execution"]["raw_result"] = "NOT_RUN"
        command["payload"]["execution"]["nonexecution_reason"] = []
        with self.assertRaisesRegex(ValueError, "预期 string，实际 list"):
            quality.validate_command(command)
        with self.assertRaises(ValueError):
            quality.validate_command([command])

    def test_quality_input_diagnostics_and_readonly_validation(self):
        self.plan()
        before = quality.state_path(self.base, self.task).read_bytes()
        with self.assertRaisesRegex(ValueError, r"\$\.payload.*execution"):
            quality.validate_command({"action": "execute", "payload": {"item_id": "case-a"}})
        self.execute(result="NOT_RUN")
        state = quality.load(self.base, self.task)
        command = state["events"][-1]["command"]
        quality.validate_command(command)
        del command["payload"]["execution"]["nonexecution_reason"]
        with self.assertRaisesRegex(ValueError, "nonexecution_reason"):
            quality.validate_command(command)
        # 已保存的旧事件不按新增写入约束拒绝，历史内容保持原样。
        quality_contract.validate(command, "quality-action.schema.json")
        snapshot = quality.state_path(self.base, self.task).read_bytes()
        command["payload"]["execution"]["nonexecution_reason"] = "环境缺失"
        with self.assertRaisesRegex(ValueError, "expected=0 actual=2.*不是任务 revision"):
            quality.apply(self.base, "TAP-123", self.task["run_id"], 0, command)
        self.assertEqual(snapshot, quality.state_path(self.base, self.task).read_bytes())
        self.assertNotEqual(before, snapshot)

    def test_scope_amend_invalidates_q1_q2_and_publication_without_rewriting_log(self):
        self.feature_profile()
        self.select(); self.checkpoint("q1-intake"); self.checkpoint("q2-plan")
        self.publish_checkpoint("q2-plan")
        prior = self.view()
        state = quality.load(self.base, self.task)
        self.task["repositories"][0]["approved_scope"] = "新增删除对账"
        self.save_task()
        after = self.view()
        for checkpoint in ("q1-intake", "q2-plan"):
            self.assertFalse(after["checkpoints"][checkpoint]["reviewed"])
            self.assertNotEqual(prior["checkpoints"][checkpoint]["digest"], after["checkpoints"][checkpoint]["digest"])
        self.assertEqual(state, quality.load(self.base, self.task))
        self.assertTrue(prior["publications"]["q2-plan"]["snapshot_current"])
        self.assertFalse(after["publications"]["q2-plan"]["snapshot_current"])

    def test_persisted_verification_event_replays_original_scope(self):
        from workflow import verification
        self.feature_profile()
        ctx = quality.context(self.base, self.task)
        ctx["repositories"]["tapdata/tapdata"]["live_revision"] = "a" * 40
        with mock.patch.object(quality, "context", return_value=ctx):
            self.apply("verification", {"kind": "review", "repository": "tapdata/tapdata",
                "target_revision": "a" * 40, "source_ref": "fixture:review", "complete": True, "items": []})
        path = quality.state_path(self.base, self.task)
        original_bytes = path.read_bytes()
        state = quality.load(self.base, self.task)
        self.assertNotIn("bindings", state["events"][-1]["command"]["payload"])
        model = quality.replay(state)
        self.assertEqual([], verification.problems(model, ctx, ["review"]))
        changed = copy.deepcopy(ctx)
        changed["repositories"]["tapdata/tapdata"]["approved_scope"] = "expanded"
        self.assertTrue(verification.problems(model, changed, ["review"]))
        self.assertEqual(original_bytes, path.read_bytes())

    def test_feature_publication_contains_complete_plan_and_accepts_draft(self):
        self.feature_profile(); self.select(); self.checkpoint("q1-intake")
        q1 = self.view()["checkpoints"]["q1-intake"]["publication_body"]
        self.assertNotIn("implementation_plan", q1)
        self.checkpoint("q2-plan")
        body = self.view()["checkpoints"]["q2-plan"]["publication_body"]
        plan = self.task["facts"]["implementation_plan"]
        self.assertIn(json.dumps(plan, ensure_ascii=False, sort_keys=True), body)
        self.assertIn('方案事实 scope_boundary："目标模块"', body)
        self.assertEqual(body.count("方案事实 implementation_plan："), 1)
        self.apply("draft", {"id": "feature-plan", "checkpoint": "q2-plan", "body": body})
        self.assertEqual(self.view()["publications"]["feature-plan"]["body"], body)
        with self.assertRaisesRegex(ValueError, "完整 publication_body"):
            self.apply("draft", {"id": "incomplete-plan", "checkpoint": "q2-plan",
                                  "body": body.replace(json.dumps(plan, ensure_ascii=False, sort_keys=True), "省略方案")})

    def test_feature_record_input_and_configuration_drift(self):
        self.feature_profile()
        source = self.base / "plan.json"
        source.write_text(json.dumps(self.task["facts"]["implementation_plan"]))
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"],
                               key="implementation_plan", input=str(source), value=None, force=False)
        self.assertEqual(task.cmd_record(args), 0)
        self.select(); self.checkpoint("q1-intake"); self.checkpoint("q2-plan")
        rules = json.loads(self.profile_path.read_text())
        rules["plan_contract"]["required_fields"].append("compatibility")
        self.profile_path.write_text(json.dumps(rules))
        with self.assertRaisesRegex(ValueError, "尚未有效确认"):
            quality.q2_digest(self.base, self.task)

    def test_feature_profile_invalid_path_and_json_are_diagnostic(self):
        self.feature_profile()
        path = self.product / "projects/tapdata/admission.json"
        spec = json.loads(path.read_text())
        for value in (None, "../quality.json", "", {}, "/tmp/quality.json"):
            spec["task_classes"]["feature_change"]["quality_profile"] = value
            path.write_text(json.dumps(spec))
            with self.assertRaises(ValueError):
                quality.config(self.base, self.task)
        spec["task_classes"]["feature_change"]["quality_profile"] = "quality-feature.json"
        path.write_text(json.dumps(spec))
        for value in ("{", "null", "{}", '{"schema_version":1,"checkpoints":null}'):
            self.profile_path.write_text(value)
            with self.assertRaises(ValueError):
                quality.config(self.base, self.task)

    def test_feature_contract_and_intake_cannot_be_empty(self):
        self.feature_profile(); self.select()
        self.task["facts"]["implementation_plan"]["acceptance"] = []
        self.save_task()
        with self.assertRaisesRegex(ValueError, "acceptance"):
            self.checkpoint("q2-plan")
        self.task["facts"]["implementation_plan"]["acceptance"] = ["目标断言"]
        self.task["facts"]["acceptance_criteria"] = ""
        self.save_task()
        self.assertTrue(quality.context(self.base, self.task)["missing_facts"])
        self.assertTrue(task._check_advance(self.task, "design_review", self.base, task.admission(self.base)))

    def test_task_profile_missing_mismatch_and_invalid_checks_fail_closed(self):
        self.feature_profile()
        original = self.profile_path.read_text()
        self.profile_path.unlink()
        with self.assertRaisesRegex(ValueError, "配置缺失"):
            quality.advance_problems(self.base, self.task, "implementation")
        for change in ({"task_classes": ["defect_fix"]}, {"plan_fact_keys": []},
                       {"stage_checkpoints": {"implementation": []}}, {"plan_contract": {}},
                       {"intake_fact_keys": ["target_repo"]}, {"plan_fact_keys": ["unknown_plan"]},
                       {"stage_checkpoints": {"implementation": ["q1-intake", "q2-plan"]}},
                       {"plan_contract": {"fact_key": "implementation_plan", "required_fields": []}}):
            with self.subTest(change=change):
                rules = json.loads(original); rules.update(change)
                self.profile_path.write_text(json.dumps(rules))
                with self.assertRaises(ValueError):
                    quality.q1_digest(self.base, self.task)

    def test_unmapped_feature_rejects_authorization_and_legacy_digest_unchanged(self):
        import hashlib
        old = hashlib.sha256(json.dumps(self.task["facts"]["fix_plan"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(authorization.plan_digest(self.task, self.base), old)
        path = self.product / "projects/tapdata/admission.json"
        spec = json.loads(path.read_text())
        spec["task_classes"]["feature_change"].pop("quality_profile", None)
        path.write_text(json.dumps(spec))
        self.task["task_class"] = "feature_change"; self.save_task()
        with self.assertRaisesRegex(ValueError, "未配置"):
            quality.q1_digest(self.base, self.task)

    def test_tapdata_feature_project_contract_and_manual_verification(self):
        local_head = mock.patch.object(pr_ready, "local_head", return_value="a" * 40)
        local_head.start(); self.addCleanup(local_head.stop)
        """AO-142：读取实际 Project 配置，不在测试中生成替代功能规则。"""
        self.task.update(task_class="feature_change", stage="design_review", facts={
            "acceptance_criteria": "正常和失败场景符合约定", "target_repo": "tapdata/tapdata",
            "verification_method": "模块集成测试", "risk_level": "T3", "scope_boundary": "目标模块",
            "implementation_plan": {"objective": "新增目标行为", "changes": ["修改目标模块"],
                                    "acceptance": ["case-a 验证约定行为"], "risks": ["边界输入"],
                                    "rollback": "回退目标改动"}})
        self.task["repositories"] = self.task["repositories"][:1]
        self.task["repositories"][0].update(authorized_endpoint="github.com/tapdata/tapdata",
            base_branch="develop", work_branch="feature/TAP-123", base_sha="a" * 40,
            verification_method="模块集成测试")
        self.save_task()
        rules = quality.config(self.base, self.task)
        self.assertEqual(rules["plan_contract"]["fact_key"], "implementation_plan")
        self.assertFalse(rules["structured_fix_plan"])
        self.assertFalse(rules["pr_ready"]["require_linked_test_tasks"])
        self.assertIn("feature_change", quality.project_rules.load_profile(station=self.base)
                         ["jira"]["status_sync"]["task_classes"])
        self.apply("item", {"plan": {"id": "case-a", "checkpoint": "q4-acceptance",
            "timing": "after_fix", "case_ref": "src/test/FeatureTest.java#behavior", "case_version": "test-v1",
            "case_status": "existing", "method": "integration", "repository": "tapdata/tapdata",
            "target_revision": "a" * 40, "criterion": "约定行为", "steps": "执行目标模块测试",
            "expected_result": "PASS", "scope": "目标模块"}, "reason": "功能验收对应工程用例"})
        self.select(); self.checkpoint("q1-intake")
        plan = self.task["facts"]["implementation_plan"]
        rollback = plan.pop("rollback"); self.save_task()
        with self.assertRaisesRegex(ValueError, "rollback"):
            self.checkpoint("q2-plan")
        plan["rollback"] = rollback; self.save_task(); self.checkpoint("q2-plan")
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"],
                               agent_id="fixture", plan_version="v1", ttl_hours=8)
        self.assertEqual(authorization.cmd_grant(args), 0)
        self.assertEqual(task._check_advance(self.task, "implementation", self.base, task.admission(self.base)), [])
        body = self.view()["checkpoints"]["q2-plan"]["publication_body"]
        self.assertIn(json.dumps(plan, ensure_ascii=False, sort_keys=True), body)
        self.assertNotIn("fix_plan", self.task["facts"])
        self.execute(result="FAIL", kind="assertion")
        with self.assertRaisesRegex(ValueError, "未满足预期"):
            self.automatic_checkpoint()
        self.execute(execution_id="run-2"); self.automatic_checkpoint()
        self.decide(evidence_id="run-2"); self.checkpoint("q4-acceptance")
        self.assertEqual(quality.advance_problems(self.base, self.task, "ci_validation"), [])
        self.task["stage"] = "ci_validation"
        self.task["repositories"][0].update(pull_request="1", worktree={"final_revision": "a" * 40})
        self.save_task()
        jira_input = self.base / "feature-jira.json"
        jira_input.write_text(json.dumps({"source_ref": "fixture:jira/TAP-123",
            "issue": {"key": "TAP-123", "fields": {"issuelinks": []}}}))
        missing_ci = pr_ready.check(self.base, "TAP-123", jira_input)
        self.assertTrue(missing_ci["checks"]["linked_test_tasks"]["passed"])
        self.assertFalse(missing_ci["ready"])
        self.assertFalse(missing_ci["checks"]["pr_checks"]["passed"])
        checks = ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata")
        checks["history"].append({"head": "a" * 40, "verdict": "success"})
        ci.save_state(self.base, "TAP-123", "1", checks)
        self.automatic_checkpoint(); self.decide(evidence_id="run-2"); self.checkpoint("q4-acceptance")
        ready = pr_ready.check(self.base, "TAP-123", jira_input)
        self.assertTrue(ready["ready"], ready)
        self.assertTrue(ready["jira_status_todos"])
        plan["changes"] = ["改变功能范围"]; self.save_task()
        self.assertTrue(task._check_advance(self.task, "implementation", self.base, task.admission(self.base)))
        self.assertFalse(pr_ready.check(self.base, "TAP-123", jira_input)["ready"])

    def apply(self, action, payload):
        return quality.apply(self.base, "TAP-123", self.task["run_id"], self.view()["revision"],
                             {"action": action, "payload": payload})

    def plan(self, key="case-a", method="integration", before=False, repo="tapdata/tapdata", case_status="existing"):
        plan = {"id": key, "checkpoint": "q2-plan" if before else "q4-acceptance",
                "timing": "before_fix" if before else "after_fix", "case_ref": "case:" + key,
                "case_version": "test-v1", "case_status": case_status, "method": method,
                "repository": repo, "target_revision": "a" * 40, "criterion": "目标行为符合预期",
                "steps": "启动测试服务，执行目标操作，检查日志和返回结果",
                "expected_result": "FAIL" if before else "PASS", "scope": "目标故障路径"}
        self.apply("item", {"plan": plan, "reason": "已有覆盖，建议复用"})
        if not before:
            self.task["facts"]["fix_plan"]["test_links"] = [
                {"item_id": item_key, "case_status": item["plan"]["case_status"],
                 **({"source_ref": "fixture:jira/" + item["plan"]["case_ref"]}
                    if item["plan"]["case_status"] == "existing" else {"owner": "fixture-tester"})}
                for item_key, item in quality.replay(quality.load(self.base, self.task))["items"].items()
                if item["plan"]["timing"] == "after_fix"
            ]
            self.save_task()
        return plan

    def test_q2_blocks_missing_input_and_unproven_problem(self):
        self.plan(); self.select()
        self.task["facts"]["fix_plan"]["blocking_inputs"] = [{"request": "提供启动日志", "owner": "研发"}]
        self.save_task()
        with self.assertRaisesRegex(ValueError, "关键输入"):
            self.checkpoint("q2-plan")
        self.task["facts"]["fix_plan"]["blocking_inputs"] = []
        self.task["facts"]["fix_plan"]["hypotheses"][0]["status"] = "hypothesis"
        self.save_task()
        with self.assertRaisesRegex(ValueError, "尚无已证实根因"):
            self.checkpoint("q2-plan")

    def test_defect_confirmation_failure_retest_and_same_version_resume(self):
        local_head = mock.patch.object(pr_ready, "local_head", return_value="a" * 40)
        local_head.start(); self.addCleanup(local_head.stop)
        """AO-143：真实质量/授权入口衔接，不模拟检查通过或访问外部服务。"""
        self.task["stage"] = "design_review"
        self.task["repositories"] = self.task["repositories"][:1]
        self.task["repositories"][0].update(
            authorized_endpoint="github.com/tapdata/tapdata", base_branch="develop",
            work_branch="fix/TAP-123", base_sha="a" * 40,
            verification_method="夹具集成测试")
        version_input = {"issue": {"key": "TAP-123", "fields": {"versions": [{"name": "fixture-version"}]}},
                         "source_ref": "fixture:jira/TAP-123",
                         "develop": {"status": "present", "revision": "a" * 40, "source_ref": "fixture:analysis"},
                         "effective": {"execution_branch": "develop", "proof": proof()}}
        # 仅替换远端读取；版本解析、检查点与授权判定均使用产品实现。
        with mock.patch.object(issue_versions, "remote_refs", return_value={"develop": "a" * 40}):
            self.task["facts"][issue_versions.FACT] = issue_versions.resolve(self.base, self.task, version_input)
        self.save_task()
        plan = self.plan(method="manual")
        self.apply("item", {"plan": dict(plan, case_ref="TAP-T1"), "reason": "绑定夹具 Jira Test"})
        self.select(); self.checkpoint("q1-intake")
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"],
                               expected_stage="design_review", note="fixture:AO-143",
                               agent_id="fixture", plan_version="v1", ttl_hours=8)
        self.assertEqual(task.cmd_advance(args), 3)
        self.checkpoint("q2-plan")
        self.assertEqual(authorization.cmd_grant(args), 0)
        self.assertEqual(task.cmd_advance(args), 0)
        self.task = task.load(self.base, "TAP-123")
        self.assertEqual(self.task["stage"], "implementation")
        self.assertNotIn("implementation_plan", self.task["facts"])

        # 新进程从磁盘恢复，而非沿用当前 Python 对象中的检查点状态。
        resumed = subprocess.run(
            [sys.executable, "-c", "import sys; from workflow import task, quality; "
             "t=task.load(sys.argv[1], 'TAP-123'); "
             "assert t['stage']=='implementation'; quality.q2_digest(sys.argv[1], t)", str(self.base)],
            cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        args.expected_stage = "implementation"
        self.execute(result="FAIL", kind="assertion", origin="manual")
        with self.assertRaisesRegex(ValueError, "未满足预期 PASS"):
            self.automatic_checkpoint()
        self.assertEqual(task.cmd_advance(args), 3)
        self.execute(execution_id="run-2", origin="manual")
        self.automatic_checkpoint()
        self.assertEqual(task.cmd_advance(args), 0)
        self.task = task.load(self.base, "TAP-123")
        args.expected_stage = "pr_review"
        self.assertEqual(task.cmd_advance(args), 3)
        self.decide(evidence_id="run-2"); self.checkpoint("q4-acceptance")
        self.assertEqual(task.cmd_advance(args), 0)
        recovered = task.load(self.base, "TAP-123")
        self.assertEqual(recovered["stage"], "ci_validation")
        self.assertEqual(recovered["run_id"], args.expected_run_id)
        self.assertEqual(recovered["repositories"], self.task["repositories"])
        self.assertEqual([x["raw_result"] for x in self.view()["items"]["case-a"]["executions"]],
                         ["FAIL", "PASS"])
        self.task = recovered
        jira_input = self.base / "jira-tests.json"
        jira_input.write_text(json.dumps({"source_ref": "fixture:jira",
            "issue": {"key": "TAP-123", "fields": {"issuelinks": [
                {"type": {"inward": "is tested by", "outward": "tests"}, "inwardIssue": {
                    "key": "TAP-T1", "fields": {"issuetype": {"name": "Test"}}}}]}},
            "linked_test_details": [{"key": "TAP-T1", "test_type": "Manual",
                                     "case_version": "test-v1", "source_ref": "fixture:jira/TAP-T1"}]}))
        self.assertFalse(pr_ready.check(self.base, "TAP-123", jira_input)["ready"])
        self.task["repositories"][0].update(pull_request="1", worktree={"final_revision": "a" * 40})
        self.save_task()
        checks = ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata")
        checks["history"].append({"head": "a" * 40, "verdict": "success"})
        ci.save_state(self.base, "TAP-123", "1", checks)
        self.assertFalse(pr_ready.check(self.base, "TAP-123", jira_input)["ready"])
        self.automatic_checkpoint()
        self.decide(evidence_id="run-2")
        self.checkpoint("q4-acceptance")
        ready = pr_ready.check(self.base, "TAP-123", jira_input)
        self.assertTrue(ready["ready"], ready)
        self.assertTrue(ready["jira_status_todos"])
        before = task_store.task_path(self.base, "TAP-123").read_bytes()
        self.assertTrue(pr_ready.check(self.base, "TAP-123", jira_input)["ready"])
        self.assertEqual(task_store.task_path(self.base, "TAP-123").read_bytes(), before)
        # 另一提交的绿色 CI 不能替代当前任务 Head 的验证。
        checks["history"].append({"head": "b" * 40, "verdict": "success"})
        ci.save_state(self.base, "TAP-123", "1", checks)
        self.assertFalse(pr_ready.check(self.base, "TAP-123", jira_input)["ready"])

    def test_proposed_test_key_does_not_invalidate_q2_selection(self):
        self.plan(case_status="proposed"); self.select()
        self.assertTrue(self.view()["items"]["case-a"]["selected"])
        state = quality.load(self.base, self.task)
        model = quality.replay(state)
        model["items"]["case-a"]["plan"].update(case_ref="TAP-456", case_version="jira-v2")
        self.assertTrue(quality.item_view(model["items"]["case-a"], quality.config(self.base), quality.context(self.base, self.task))["selected"])

    def select(self, key="case-a"):
        return self.apply("select", {"item_id": key, "digest": self.view()["items"][key]["plan_digest"], "proof": proof()})

    def execute(self, key="case-a", result="PASS", execution_id="run-1", origin="local_maven", kind="none", **changes):
        plan = self.view()["items"][key]["plan"]
        execution = {k: plan[k] for k in ("case_ref", "case_version", "method", "repository", "target_revision")}
        execution.update(id=execution_id, origin=origin, source_ref="fixture:report/" + execution_id,
                         environment="local-fixture", observed_at=proof()["at"], raw_result=result,
                         failure_kind=kind, observation="观察目标断言与报告")
        if result == "NOT_RUN":
            execution["nonexecution_reason"] = "夹具环境未就绪"
        execution.update(changes)
        return self.apply("execute", {"item_id": key, "execution": execution})

    def decide(self, key="case-a", outcome="accept", evidence_id="run-1", **fields):
        decision = {"outcome": outcome, "reason": "测试用户检查证据后决断", "proof": proof()}
        if evidence_id and self.view()["items"][key]["executions"]:
            decision["evidence_id"] = evidence_id
        decision.update(fields)
        return self.apply("decide", {"item_id": key, "digest": self.view()["items"][key]["digest"], "decision": decision})

    def checkpoint(self, cp, outcome="accept", **fields):
        return self.apply("checkpoint", {"checkpoint": cp, "digest": self.view()["checkpoints"][cp]["digest"],
                           "decision": dict(outcome=outcome, reason="检查点已完整核对", proof=proof(), **fields)})

    def automatic_checkpoint(self, cp="q3-draft"):
        return self.apply("auto_checkpoint", {"checkpoint": cp,
                           "digest": self.view()["checkpoints"][cp]["automatic_digest"],
                           "reason": "已确认方案的全部首轮执行证据均符合预期"})

    def test_multiple_items_single_method_and_phase(self):
        self.plan(); self.plan("case-b", "manual")
        self.select(); self.select("case-b")
        view = self.checkpoint("q2-plan")
        self.assertEqual(view["checkpoints"]["q2-plan"]["not_due"], ["case-a", "case-b"])
        self.assertTrue(view["checkpoints"]["q2-plan"]["reviewed"])
        invalid = dict(view["items"]["case-a"]["plan"], method=["integration", "manual"])
        with self.assertRaises(ValueError):
            self.apply("item", {"plan": invalid, "reason": "多方式不合法"})
        with self.assertRaises(ValueError):
            self.checkpoint("q4-acceptance")

    def test_q1_state_write_does_not_require_product_root_local(self):
        self.assertFalse((self.product / ".local").exists())
        self.checkpoint("q1-intake", outcome="not_applicable")
        self.assertTrue(quality.state_path(self.base, self.task).is_file())
        self.assertFalse((self.product / ".local").exists())

    def test_before_fix_reproduction_preserves_fail(self):
        self.plan(before=True); self.select()
        self.execute(result="FAIL", kind="environment")
        with self.assertRaisesRegex(ValueError, "环境失败"):
            self.decide()
        self.execute(result="FAIL", kind="assertion", execution_id="run-2")
        view = self.decide(evidence_id="run-2")
        self.assertTrue(view["items"]["case-a"]["decision_valid"])
        self.assertEqual(view["items"]["case-a"]["executions"][-1]["raw_result"], "FAIL")
        self.checkpoint("q2-plan")

    def test_unavailable_and_risk_are_not_pass(self):
        self.plan(before=True); self.select(); self.execute(result="NOT_RUN")
        with self.assertRaises(ValueError):
            self.decide(outcome="accept_risk")
        self.decide(outcome="accept_risk", owner="测试责任人", follow_up="修复后回归")
        view = self.checkpoint("q2-plan")
        self.assertTrue(view["checkpoints"]["q2-plan"]["reviewed"])
        self.assertEqual(view["items"]["case-a"]["executions"][0]["raw_result"], "NOT_RUN")

    def test_defer_requires_owner_and_deadline_and_rework_holds(self):
        self.plan(); self.select()
        with self.assertRaisesRegex(ValueError, "deadline"):
            self.decide(outcome="defer", owner="tester", follow_up="补测")
        with self.assertRaisesRegex(ValueError, "deadline"):
            self.decide(outcome="accept_risk", owner="tester", follow_up="补测", deadline="明天")
        self.decide(outcome="defer", owner="tester", follow_up="补测",
                    deadline=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat())
        self.checkpoint("q4-acceptance")
        self.decide(outcome="rework", owner="dev", follow_up="修改再测")
        with self.assertRaisesRegex(ValueError, "返工"):
            self.checkpoint("q4-acceptance")

    def test_raw_results_and_latest_applicable_execution(self):
        self.plan(); self.select(); self.execute(); self.decide()
        for index, result in enumerate(("FAIL", "SKIPPED", "NOT_RUN", "UNKNOWN")):
            self.execute(result=result, kind="assertion" if result == "FAIL" else "none", execution_id="retry-%s" % index)
            self.assertFalse(self.view()["items"]["case-a"]["decision_valid"])
            with self.assertRaises(ValueError):
                self.decide(evidence_id="run-1")
            with self.assertRaises(ValueError):
                self.decide(evidence_id="retry-%s" % index)
        self.execute(execution_id="ci-run-2", origin="ci")
        self.decide(evidence_id="ci-run-2")
        with self.assertRaisesRegex(ValueError, "执行编号"):
            self.execute(execution_id="ci-run-2", origin="ci")

    def test_changed_item_invalidates_only_affected_confirmation(self):
        plan = self.plan(); self.plan("case-b", "manual")
        self.select(); self.select("case-b")
        self.execute(); self.decide()
        self.execute("case-b", origin="manual"); self.decide("case-b")
        self.apply("item", {"plan": dict(plan, criterion="新预期"), "reason": "范围调整"})
        self.assertFalse(self.view()["items"]["case-a"]["selected"])
        self.assertTrue(self.view()["items"]["case-b"]["decision_valid"])
        with self.assertRaises(ValueError):
            self.decide()
        self.apply("item", {"plan": plan, "reason": "恢复旧计划也须再次确认"})
        self.assertFalse(self.view()["items"]["case-a"]["selected"])

    def test_stale_target_and_method_evidence_rejected(self):
        self.plan(); self.select()
        for key, value in (("target_revision", "b" * 40), ("case_version", "old-test"), ("case_ref", "another-case"), ("repository", "tapdata/tapdata-manager")):
            self.execute(execution_id="wrong-" + key.replace("_", "-"), **{key: value})
            with self.assertRaises(ValueError):
                self.decide(evidence_id="wrong-" + key.replace("_", "-"))

    def test_proof_digest_unknown_version_corruption(self):
        self.plan()
        for payload in ({"item_id": "case-a", "digest": "stale", "proof": proof()},
                        {"item_id": "case-a", "digest": self.view()["items"]["case-a"]["plan_digest"], "proof": {"confirmed": True}}):
            with self.assertRaises(ValueError):
                self.apply("select", payload)
        path = quality.state_path(self.base, self.task)
        for bad in ('{"schema_version":99}', '{broken'):
            path.write_text(bad)
            with self.assertRaises(ValueError):
                quality.load(self.base, self.task)
            self.assertEqual(path.read_text(), bad)

    def test_cas_process_concurrency_and_crash_before_replace(self):
        command = {"action": "draft", "payload": {"id": "summary", "body": "测试草稿"}}
        ctx = multiprocessing.get_context("fork")
        queue = ctx.Queue()
        processes = [ctx.Process(target=concurrent_apply, args=(self.base, self.task["run_id"], 0, command, queue)) for _ in range(2)]
        for process in processes: process.start()
        for process in processes: process.join(15); self.assertEqual(process.exitcode, 0)
        self.assertCountEqual([queue.get(timeout=1), queue.get(timeout=1)], ["saved", "stale"])
        prior = quality.state_path(self.base, self.task).read_bytes()
        with mock.patch.object(quality.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.apply("draft", {"id": "summary", "body": "修改正文"})
        self.assertEqual(quality.state_path(self.base, self.task).read_bytes(), prior)

    def publication(self):
        self.apply("draft", {"id": "summary", "body": "测试报告：尚未验证"})
        d = self.view()["publications"]["summary"]["digest"]
        self.apply("confirm", {"id": "summary", "digest": d, "proof": proof()})
        return self.apply("prepare_write", {"id": "summary", "digest": d})["publications"]["summary"]

    def publish_checkpoint(self, cp):
        self.apply("draft", {"id": cp, "checkpoint": cp, "body": self.view()["checkpoints"][cp]["publication_body"]})
        record = self.view()["publications"][cp]
        self.apply("confirm", {"id": cp, "digest": record["digest"], "proof": proof()})
        record = self.apply("prepare_write", {"id": cp, "digest": record["digest"]})["publications"][cp]
        self.apply("receipt", {"id": cp, "operation_id": record["operation_id"], "result": "created", "comment_id": cp})
        return self.apply("readback", {"id": cp, "operation_id": record["operation_id"], "site": record["site"],
                         "issue_key": "TAP-123", "comment_id": cp, "body": record["body"], "source_ref": "fixture:jira/" + cp})

    def test_plan_confirmation_survives_code_and_execution_but_not_plan_change(self):
        self.checkpoint("q1-intake", outcome="not_applicable")
        plan = self.plan()
        plan["target_revision"] = "pending"
        self.apply("item", {"plan": plan, "reason": "尚未编码"})
        self.select(); self.checkpoint("q2-plan")
        self.publish_checkpoint("q2-plan")
        before = self.view()["checkpoints"]["q2-plan"]["digest"]
        plan["target_revision"] = "a" * 40
        self.apply("item", {"plan": plan, "reason": "绑定实际提交"})
        self.assertTrue(self.view()["items"]["case-a"]["selected"])
        self.execute(); self.decide(); self.checkpoint("q4-acceptance")
        self.task["facts"]["verification"] = "完成首轮测试"; self.save_task()
        ci_state = ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata")
        ci_state["history"].append({"head": "a" * 40, "verdict": "success"})
        ci.save_state(self.base, "TAP-123", "1", ci_state)
        view = self.view()
        self.assertTrue(view["checkpoints"]["q1-intake"]["reviewed"])
        self.assertTrue(view["checkpoints"]["q2-plan"]["published"])
        self.assertEqual(view["checkpoints"]["q2-plan"]["digest"], before)
        self.assertFalse(view["checkpoints"]["q4-acceptance"]["reviewed"])
        self.apply("item", {"plan": dict(plan, steps="改变验证步骤"), "reason": "调整方案"})
        self.assertFalse(self.view()["checkpoints"]["q2-plan"]["reviewed"])
        self.assertFalse(self.view()["checkpoints"]["q2-plan"]["published"])

    def test_precise_manual_evidence_and_actionable_handoff(self):
        plan = self.plan(method="manual"); self.select()
        for revision in ("develop", "fix/TAP-123", "a3561f47", "pending", "a" * 40 + ":worktree:" + "b" * 64):
            with self.assertRaisesRegex(ValueError, "完整提交 SHA"):
                self.execute(origin="manual", target_revision=revision)
        self.assertEqual(self.view()["items"]["case-a"]["executions"], [])
        handoff = self.view()["checkpoints"]["q4-acceptance"]["handoff"]
        self.assertEqual(handoff["verify"][0]["steps"], plan["steps"])
        self.assertEqual(handoff["verify"][0]["target_revision"], "a" * 40)
        self.assertIn("精确提交 SHA", handoff["return"])
        self.execute(origin="manual"); self.decide()
        self.apply("item", {"plan": dict(plan, target_revision="b" * 40), "reason": "新提交"})
        self.assertTrue(self.view()["items"]["case-a"]["selected"])
        self.assertFalse(self.view()["items"]["case-a"]["decision_valid"])

    def test_worktree_evidence_allowed_before_final_acceptance(self):
        plan = self.plan()
        plan.update(checkpoint="q3-draft", target_revision="a" * 40 + ":worktree:" + "b" * 64)
        self.apply("item", {"plan": plan, "reason": "首轮工作区验证"})
        self.select(); self.execute(); self.decide()
        plan["checkpoint"] = "q4-acceptance"
        self.apply("item", {"plan": plan, "reason": "改为最终验收"})
        self.select()
        with self.assertRaisesRegex(ValueError, "完整提交 SHA"):
            self.decide()

    def test_verified_clean_commit_survives_neutral_checkout(self):
        self.task["repositories"] = self.task["repositories"][:1]
        repo = self.base / "source/tapdata/tapdata"; repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "fixture", "--allow-empty"], check=True)
        sha = quality.git_revision(repo)
        self.task["repositories"][0]["worktree"] = {"status": "prepared", "path": str(repo)}; self.save_task()
        plan = self.plan()
        self.apply("item", {"plan": dict(plan, target_revision=sha), "reason": "核验目标提交"})
        self.select(); self.execute(); self.decide(); self.checkpoint("q4-acceptance")
        self.publish_checkpoint("q4-acceptance")
        subprocess.run(["git", "-C", str(repo), "checkout", "--detach", sha], check=True, capture_output=True)
        view = self.view()
        self.assertTrue(view["checkpoints"]["q4-acceptance"]["reviewed"])
        self.assertTrue(view["checkpoints"]["q4-acceptance"]["published"])
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "new candidate", "--allow-empty"], check=True)
        self.assertFalse(self.view()["checkpoints"]["q4-acceptance"]["reviewed"])

    def test_checkpoint_publication_warns_without_blocking_and_preserves_binding(self):
        self.plan(); self.select(); self.execute(); self.automatic_checkpoint()
        self.assertEqual(quality.advance_problems(self.base, self.task, "pr_review"), [])
        from workflow import external_sync
        self.assertTrue(any(w["kind"] == "checkpoint:q3-draft" for w in external_sync.warnings(self.base, self.task)))
        self.publication()
        self.assertFalse(self.view()["checkpoints"]["q3-draft"]["published"])
        with self.assertRaisesRegex(ValueError, "完整 publication_body"):
            self.apply("draft", {"id": "wrong", "checkpoint": "q3-draft", "body": "略"})
        record = self.view()["publications"]["summary"]
        self.apply("readback", {"id": "summary", "operation_id": record["operation_id"], "site": record["site"],
                   "issue_key": "TAP-123", "comment_id": "1", "body": record["body"], "source_ref": "fixture:jira/1"})
        self.publish_checkpoint("q3-draft")
        self.assertEqual(quality.advance_problems(self.base, self.task, "pr_review"), [])

    def test_old_event_rules_preserve_replay_without_reinterpreting_evidence(self):
        rules = quality.config(self.base)
        for key in ("contract_revision", "commit_evidence_checkpoint", "require_checkpoint_publication"):
            rules.pop(key, None)
        plan = self.plan()
        plan["target_revision"] = "develop"
        state = {"events": [{"command": {"action": "item", "payload": {"plan": plan, "reason": "旧记录"}},
                              "rules": rules, "context": quality.context(self.base, self.task)}]}
        self.assertEqual(quality.replay(state)["items"]["case-a"]["plan"]["target_revision"], "develop")

    def test_write_confirmation_unknown_receipt_and_readback(self):
        self.apply("draft", {"id": "summary", "body": "草稿"})
        d = self.view()["publications"]["summary"]["digest"]
        with self.assertRaises(ValueError): self.apply("prepare_write", {"id": "summary", "digest": d})
        record = self.publication()
        op = record["operation_id"]
        self.apply("receipt", {"id": "summary", "operation_id": op, "result": "unknown"})
        with self.assertRaises(ValueError): self.apply("prepare_write", {"id": "summary", "digest": record["digest"]})
        readback = {"id": "summary", "operation_id": op, "site": record["site"], "issue_key": "TAP-123", "comment_id": "100", "body": "different", "source_ref": "fixture:jira/100"}
        with self.assertRaises(ValueError): self.apply("readback", readback)
        self.assertEqual(self.view()["publications"]["summary"]["status"], "unknown")
        view = self.apply("readback", dict(readback, body=record["body"]))
        self.assertEqual(view["publications"]["summary"]["status"], "verified")

    def test_unknown_comment_allows_different_publication_and_survives_summary(self):
        original = self.publication()
        self.apply("receipt", {"id": "summary", "operation_id": original["operation_id"], "result": "unknown"})
        self.apply("draft", {"id": "next-stage", "body": "下一阶段的独立评论"})
        record = self.view()["publications"]["next-stage"]
        self.apply("confirm", {"id": "next-stage", "digest": record["digest"], "proof": proof()})
        next_record = self.apply("prepare_write", {"id": "next-stage", "digest": record["digest"]})["publications"]["next-stage"]
        self.apply("receipt", {"id": "next-stage", "operation_id": next_record["operation_id"], "result": "deferred", "reason": "服务明确拒绝，未写入"})
        from workflow import external_sync
        warnings = external_sync.warnings(self.base, self.task)
        self.assertTrue(any(w["kind"] == "comment:summary" and w["status"] == "unknown" for w in warnings))
        self.assertTrue(any(w["kind"] == "comment:next-stage" and w["status"] == "deferred" for w in warnings))
        text = evidence.build_summary(self.task, None, [], [], task.admission(self.base), sync_warnings=warnings)
        self.assertIn("执行过程被跳过的处理与警告", text)
        self.assertIn("服务明确拒绝", text)
        with self.assertRaises(ValueError):
            self.apply("receipt", {"id": "summary", "operation_id": original["operation_id"], "result": "deferred", "reason": "猜测失败"})

    def test_checkpoint_comment_uses_applicable_execution(self):
        self.plan(); self.select()
        self.execute(execution_id="current-pass")
        self.execute(execution_id="historical-fail", result="FAIL", kind="assertion", target_revision="b" * 40)
        self.decide(evidence_id="current-pass")
        self.checkpoint("q4-acceptance")
        self.automatic_checkpoint()
        for checkpoint in ("q3-draft", "q4-acceptance"):
            body = self.view()["checkpoints"][checkpoint]["publication_body"]
            self.assertIn("结果：PASS；版本：" + "a" * 40, body)
            self.assertIn("fixture:report/current-pass", body)
            self.assertNotIn("historical-fail", body)
            self.assertNotIn("结果：FAIL", body)
        self.checkpoint("q2-plan")
        self.assertNotIn("结果：", self.view()["checkpoints"]["q2-plan"]["publication_body"])

    def test_checkpoint_comment_preserves_checkpoint_handoff(self):
        deadline = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        self.checkpoint("q1-intake", outcome="defer", owner="接力负责人", follow_up="补齐任务事实", deadline=deadline)
        body = self.view()["checkpoints"]["q1-intake"]["publication_body"]
        for value in ("接力负责人", "补齐任务事实", deadline):
            self.assertIn(value, body)

    def test_old_run_warnings_only_keep_unresolved_writes(self):
        from workflow import external_sync
        statuses = ("draft", "confirmed", "deferred", "intent", "unknown", "created")
        for status in statuses:
            self.apply("draft", {"id": status, "body": "状态夹具：" + status})
            record = self.view()["publications"][status]
            if status == "draft":
                continue
            self.apply("confirm", {"id": status, "digest": record["digest"], "proof": proof()})
            if status == "confirmed":
                continue
            record = self.apply("prepare_write", {"id": status, "digest": record["digest"]})["publications"][status]
            if status != "intent":
                payload = {"id": status, "operation_id": record["operation_id"], "result": status}
                if status == "created":
                    payload["comment_id"] = "fixture-comment"
                if status == "deferred":
                    payload["reason"] = "明确未写入"
                self.apply("receipt", payload)
        current = [w for w in external_sync.warnings(self.base, self.task) if w["kind"].startswith("comment:")]
        self.assertEqual({w["kind"] for w in current}, {"comment:" + s for s in statuses})
        path = quality.state_path(self.base, self.task)
        original = path.read_bytes()
        old_run = self.task["run_id"]
        self.task["run_id"] = "run-abcdef012345"
        self.save_task()
        self.apply("draft", {"id": "current", "body": "当前 run 草稿"})
        warnings = [w for w in external_sync.warnings(self.base, self.task) if w["kind"].startswith("comment:")]
        self.assertEqual({w["kind"] for w in warnings}, {"comment:current", "comment:intent", "comment:unknown", "comment:created"})
        for warning in warnings:
            if warning["run_id"] == old_run:
                self.assertEqual(warning["status"], "unknown")
                self.assertIn("禁止盲目重发", warning["recovery"])
        self.assertEqual(path.read_bytes(), original)

    def test_checkpoint_comment_is_human_text_and_normalized_readback_checks_body(self):
        self.plan(); self.select(); self.execute(); self.automatic_checkpoint()
        body = self.view()["checkpoints"]["q3-draft"]["publication_body"]
        self.assertIn("记录：AO-", body)
        self.assertIn("验证 case:case-a", body)
        self.assertNotIn('"executions":', body)
        self.apply("draft", {"id": "human", "checkpoint": "q3-draft", "body": body})
        record = self.view()["publications"]["human"]
        self.apply("confirm", {"id": "human", "digest": record["digest"], "proof": proof()})
        record = self.apply("prepare_write", {"id": "human", "digest": record["digest"]})["publications"]["human"]
        payload = {"id": "human", "operation_id": record["operation_id"], "site": record["site"],
                   "issue_key": "TAP-123", "comment_id": "10", "source_ref": "fixture:comment/10", "body": body + "错误内容"}
        with self.assertRaises(ValueError):
            self.apply("readback", payload)
        payload["body"] = body.replace("\n", "\r\n")
        self.assertEqual(self.apply("readback", payload)["publications"]["human"]["status"], "verified")

    def test_prepare_write_ignores_quality_action_input_files(self):
        self.apply("draft", {"id": "q1-intake-jira", "body": "检查点评论"})
        record = self.view()["publications"]["q1-intake-jira"]
        self.apply("confirm", {"id": "q1-intake-jira", "digest": record["digest"], "proof": proof()})
        command = {"action": "prepare_write", "payload": {"id": "q1-intake-jira", "digest": record["digest"]}}
        (task_store.task_directory(self.base, "TAP-123") / "quality-prepare-q1.json").write_text(
            json.dumps(command), encoding="utf-8")
        view = self.apply("prepare_write", command["payload"])
        self.assertEqual(view["publications"]["q1-intake-jira"]["status"], "intent")

    def test_changed_draft_and_facts_require_new_confirmation(self):
        self.apply("draft", {"id": "summary", "body": "正文一"})
        d = self.view()["publications"]["summary"]["digest"]
        self.apply("confirm", {"id": "summary", "digest": d, "proof": proof()})
        self.apply("draft", {"id": "summary", "body": "正文二"})
        with self.assertRaises(ValueError): self.apply("prepare_write", {"id": "summary", "digest": d})
        d = self.view()["publications"]["summary"]["digest"]
        self.apply("confirm", {"id": "summary", "digest": d, "proof": proof()})
        self.task["facts"]["problem_version"] = "new-version"; self.save_task()
        with self.assertRaises(ValueError): self.apply("prepare_write", {"id": "summary", "digest": d})

    def test_old_run_isolation_rejects_late_receipt(self):
        record = self.publication(); old_run = self.task["run_id"]
        old_revision = self.view()["revision"]
        self.task["run_id"] = "run-fedcba987654"; self.save_task()
        self.assertEqual(self.view()["revision"], 0)
        with self.assertRaisesRegex(ValueError, "run 已变化"):
            quality.apply(self.base, "TAP-123", old_run, old_revision, {"action": "draft", "payload": {"id": "summary", "body": "x"}})
        with self.assertRaisesRegex(ValueError, "旧 run"):
            self.publication()
        before = quality.state_path(self.base, dict(self.task, run_id=old_run)).read_bytes()
        with self.assertRaisesRegex(ValueError, "run 已变化"):
            quality.apply(self.base, "TAP-123", old_run, old_revision, {"action": "readback", "payload": {
                "id": "summary", "operation_id": record["operation_id"], "site": record["site"],
                "issue_key": "TAP-123", "comment_id": "100", "body": record["body"], "source_ref": "fixture:jira/100"}})
        self.assertEqual(quality.state_path(self.base, dict(self.task, run_id=old_run)).read_bytes(), before)

    def test_archive_freezes_quality_ci_authorization_and_jira_writes(self):
        from workflow import jira_status, jira_watermark
        checks = ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata")
        self.task["archive_ref"] = {"path": "archive/TAP-123/" + self.task["run_id"], "digest": "a" * 64}
        self.save_task()
        before = {p: p.read_bytes() for p in (self.base / ".agenticops").rglob("*") if p.is_file()}
        actions = [
            lambda: self.apply("draft", {"id": "late", "body": "归档后草稿"}),
            lambda: ci.save_state(self.base, "TAP-123", "1", checks),
            lambda: authorization.cmd_grant(SimpleNamespace(dir=self.base, issue_key="TAP-123", expected_run_id=self.task["run_id"])),
            lambda: jira_status.save_state(self.base, self.task, jira_status.load_state(self.base, self.task)),
            lambda: jira_watermark.save_state(self.base, self.task, jira_watermark.load_state(self.base, self.task)),
        ]
        for action in actions:
            with self.assertRaisesRegex(ValueError, "归档"):
                action()
        for script in ("jira_status.py", "jira_watermark.py"):
            result = subprocess.run([sys.executable, str(ROOT / "workflow" / script), "status",
                "--dir", str(self.base), "--issue-key", "TAP-123"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {p: p.read_bytes() for p in (self.base / ".agenticops").rglob("*") if p.is_file()})

    def test_ci_unknown_skipped_repo_run_isolation_and_cas(self):
        for checks, expected in (([{"status": "COMPLETED"}], "unknown"), ([{"conclusion": "SKIPPED"}], "skipped"),
                                 ([{"state": "alien"}], "unknown"), ([{"state": "SUCCESS"}], "success")):
            self.assertEqual(ci.classify(checks)[0], expected)
        a = ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata")
        b = copy.deepcopy(a)
        a["history"].append({"verdict": "failure"}); ci.save_state(self.base, "TAP-123", "1", a)
        self.assertEqual(ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata-manager")["history"], [])
        with self.assertRaises(ValueError): ci.save_state(self.base, "TAP-123", "1", b)
        self.task["run_id"] = "run-fedcba987654"; self.save_task()
        self.assertEqual(ci.current_states(self.base, self.task), [])
        with self.assertRaises(ValueError): ci.save_state(self.base, "TAP-123", "1", a)

    def test_ci_update_invalidates_affected_repo_only(self):
        self.plan(); self.plan("case-b", repo="tapdata/tapdata-manager")
        for key in ("case-a", "case-b"):
            self.select(key); self.execute(key); self.decide(key)
        state = ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata")
        state["history"].append({"head": "new-sha", "verdict": "failure"})
        ci.save_state(self.base, "TAP-123", "1", state)
        self.assertFalse(self.view()["items"]["case-a"]["decision_valid"])
        self.assertTrue(self.view()["items"]["case-b"]["decision_valid"])

    def test_watch_records_unknown_as_handoff_and_preserves_raw_checks(self):
        args = SimpleNamespace(dir=self.base, issue_key="TAP-123", repo="tapdata/tapdata", pr="8",
                               interval=0, start_timeout=0, finish_timeout=0, expected_run_id=self.task["run_id"])
        checks = [{"name": "integration", "status": "COMPLETED", "conclusion": ""}]
        with mock.patch.object(ci, "fetch_rollup", return_value=(checks, "known-sha")):
            self.assertEqual(ci.cmd_watch(args), 3)
        state = ci.load_state(self.base, "TAP-123", "8", args.repo)
        self.assertEqual(state["history"][-1]["verdict"], "unknown")
        self.assertEqual(state["history"][-1]["checks"], checks)
        for checks in ([{"state": 1}], {"state": "SUCCESS"}):
            self.assertEqual(ci.classify(checks)[0], "unknown")

    def test_taptest_and_manual_use_the_same_evidence_contract(self):
        for key, method, origin in (("taptest", "taptest", "taptest"), ("manual", "manual", "manual")):
            self.plan(key, method); self.select(key)
            with self.assertRaisesRegex(ValueError, "来源"):
                self.execute(key, origin="local_maven")
            self.execute(key, origin=origin); self.decide(key)
        self.checkpoint("q4-acceptance")

    def test_expired_checkpoint_and_missing_rules_fail_closed(self):
        cp = "q2-plan"
        decision = {"outcome": "defer", "reason": "仅用于过期检查", "owner": "tester", "follow_up": "重新验证",
                    "proof": dict(proof(), at="2020-01-01T00:00:00+00:00"), "deadline": "2020-01-02T00:00:00+00:00"}
        self.apply("checkpoint", {"checkpoint": cp, "digest": self.view()["checkpoints"][cp]["digest"], "decision": decision})
        self.assertTrue(any("到期" in p for p in quality.advance_problems(self.base, self.task, "implementation")))
        (self.base / "product/projects/tapdata/quality.json").unlink()
        with self.assertRaisesRegex(ValueError, "缺失"):
            quality.advance_problems(self.base, self.task, "pr_review")

    def test_quality_does_not_replace_authorization_or_green_gate(self):
        self.plan(); self.select(); self.checkpoint("q1-intake"); self.checkpoint("q2-plan")
        spec = quality.project_rules.load_admission(station=self.base)
        problems = task._check_advance(self.task, "implementation", self.base, spec)
        self.assertTrue(any("授权" in p for p in problems))
        self.execute(result="FAIL", kind="assertion")
        self.decide(outcome="accept_risk", owner="owner", follow_up="补充回归")
        self.checkpoint("q4-acceptance")
        self.publish_checkpoint("q4-acceptance")
        self.assertEqual(quality.advance_problems(self.base, self.task, "ci_validation"), [])

    def test_q3_is_automatic_only_after_all_confirmed_after_fix_evidence_passes(self):
        self.plan(); self.plan("case-b", "manual")
        self.select(); self.select("case-b")
        with self.assertRaisesRegex(ValueError, "首轮执行证据"):
            self.automatic_checkpoint()
        self.execute(); self.execute("case-b", origin="manual")
        view = self.automatic_checkpoint()
        checkpoint = view["checkpoints"]["q3-draft"]
        self.assertTrue(checkpoint["reviewed"])
        self.assertEqual(checkpoint["mode"], "automatic")
        self.assertEqual(checkpoint["outcome"], "observed")
        with self.assertRaisesRegex(ValueError, "自动记录"):
            self.checkpoint("q3-draft")

    def test_live_code_change_invalidates_after_but_not_reproduction(self):
        self.task["repositories"] = self.task["repositories"][:1]
        repo = self.base / "source/tapdata/tapdata"; repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-q", "--allow-empty", "-m", "fixture"], check=True)
        self.task["repositories"][0]["worktree"] = {"status": "prepared", "path": str(repo)}; self.save_task()
        sha = quality.git_revision(repo)
        plan = self.plan(); self.apply("item", {"plan": dict(plan, target_revision=sha), "reason": "绑定当前代码"})
        before = self.plan("before", before=True); self.select("before")
        self.execute("before", result="FAIL", kind="assertion"); self.decide("before")
        self.select(); self.execute(); self.decide()
        (repo / "changed.txt").write_text("changed")
        self.assertFalse(self.view()["items"]["case-a"]["decision_valid"])
        self.assertTrue(self.view()["items"]["before"]["decision_valid"])
        with self.assertRaisesRegex(ValueError, "本地代码"):
            self.decide()

    def test_sensitive_input_not_saved_and_broken_events_not_hidden(self):
        with self.assertRaisesRegex(ValueError, "敏感"):
            self.apply("draft", {"id": "summary", "body": "password=example-secret"})
        self.assertEqual(self.view()["revision"], 0)
        events = task_store.events_path(self.base, "TAP-123"); events.write_text('{bad\n')
        with self.assertRaises(ValueError): evidence.load_events(events)


class FeatureFlowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="ao-feature-flow-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.ws, self.product = self.root / "station", self.root / "product"
        self.seed, self.remote = self.root / "seed", self.root / "remote.git"
        self.repo = "tapdata/tapdata"
        shutil.copytree(ROOT / "projects", self.product / "projects")
        shutil.copytree(ROOT / "contracts", self.product / "contracts")
        self.git("init", "-q", "-b", "develop", str(self.seed))
        (self.seed / "feature.py").write_text("def value():\n    return 0\n")
        (self.seed / "verify.py").write_text("from feature import value\nassert value() == 1\n")
        self.git("-C", str(self.seed), "add", ".")
        self.git("-C", str(self.seed), "commit", "-qm", "fixture base")
        self.git("clone", "-q", "--bare", str(self.seed), str(self.remote))
        catalog = self.product / "projects/tapdata/repositories.json"
        doc = json.loads(catalog.read_text())
        doc["repositories"][self.repo]["origin"] = str(self.remote)
        task_store._write_json_atomic(catalog, doc)
        task_store._write_json_atomic(self.ws / ".agenticops/station.json", {
            "schema_version": 4, "product_root": str(self.product), "source_pool": str(self.root / "pool"), "project": "tapdata",
            "station_id": "3" * 32, "agents": ["codex"],
            "branch_identity": {"schema_version": 1, "git_name": "Fixture", "source": "git_global_user_name"}})
        task_store.initialize_current(self.ws)
        initialize_station(self.ws)
        for name in ("source", "config", "runtime", "archive"):
            (self.ws / name).mkdir(exist_ok=True)
        profiles = self.product / "projects/tapdata/engineering-profiles.json"
        profile_doc = json.loads(profiles.read_text())
        profile_doc["profiles"]["full-application"]["repositories"] = [self.repo]
        profile_doc["profiles"]["full-application"]["optional_repositories"] = []
        profiles.write_text(json.dumps(profile_doc))

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "user.name=Fixture", "-c",
            "user.email=fixture@example.test", *args], text=True).strip()

    def read(self):
        return task.load(self.ws, "TAP-123")

    def cli(self, tool, *args, expected=0, mutation=True):
        command = [sys.executable, str(ROOT / "workflow" / tool), *args,
                   "--issue-key", "TAP-123", "--dir", str(self.ws)]
        if mutation and args[0] != "takeover":
            command += ["--expected-run-id", self.read()["run_id"]]
        if args[0] == "takeover" or args[:2] == ("repository", "add"):
            command += ["--operation-id", "op-fixture-" + str(task_store.read_current(self.ws)["revision"]),
                        "--expected-revision", str(task_store.read_current(self.ws)["revision"])]
        if args[0] == "advance":
            command += ["--expected-stage", self.read()["stage"]]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout

    def view(self):
        state = self.read()
        return quality.report(quality.load(self.ws, state), quality.config(self.ws, state),
                              quality.context(self.ws, state))

    def apply(self, action, payload):
        return quality.apply(self.ws, "TAP-123", self.read()["run_id"], self.view()["revision"],
                             {"action": action, "payload": payload})

    def proof(self):
        return {"actor": "fixture-reviewer", "source": "user_message", "reference": "fixture:decision",
                "at": datetime.now(timezone.utc).isoformat()}

    def checkpoint(self, name):
        view = self.view()["checkpoints"][name]
        if name == "q3-draft":
            return self.apply("auto_checkpoint", {"checkpoint": name, "digest": view["automatic_digest"],
                                                   "reason": "实际临时仓库测试已通过"})
        return self.apply("checkpoint", {"checkpoint": name, "digest": view["digest"],
            "decision": {"outcome": "accept", "reason": "夹具审查确认", "proof": self.proof()}})

    def execute(self, worktree, execution_id, expected):
        # 测试产物放到当前 run；避免 Python 缓存改变受控工作树指纹。
        result = subprocess.run([sys.executable, "-B", "verify.py"], cwd=worktree,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected)
        path = self.cli("task.py", "interaction-path", "--name", execution_id + ".log").strip()
        Path(path).write_text(result.stdout + result.stderr + "\nexit=" + str(result.returncode))
        self.cli("task.py", "record", "--key", "verification", "--value",
                 "python -B verify.py exit=" + str(result.returncode) + "; fixture:run/" + Path(path).name)
        plan = self.view()["items"]["behavior"]["plan"]
        self.apply("execute", {"item_id": "behavior", "execution": {
            **{k: plan[k] for k in ("case_ref", "case_version", "method", "repository", "target_revision")},
            "id": execution_id, "origin": "local_maven", "source_ref": "fixture:run/" + Path(path).name,
            "environment": "isolated-python-fixture", "observed_at": self.proof()["at"],
            "raw_result": "PASS" if result.returncode == 0 else "FAIL",
            "failure_kind": "none" if result.returncode == 0 else "assertion",
            "observation": "python -B verify.py exit=" + str(result.returncode)}})
        if result.returncode == 0:
            self.local_material = {"kind": "local", "repository": self.repo,
                "target_revision": plan["target_revision"], "source_ref": "fixture:run/" + Path(path).name,
                "analysis_ref": "fixture:confirmed-value-behavior", "case_review_ref": "fixture:verify.py-assertion",
                "case_version": "v1", "dependency_analysis_ref": "fixture:no-jar-dependency",
                "required_scope": ["feature.py:value"], "results": [{"scope": "feature.py:value", "result": "PASS",
                    "report_ref": "fixture:run/" + Path(path).name, "tests": 1, "failures": 0, "errors": 0, "skipped": 0}]}
            self.apply("verification", self.local_material)

    def test_cli_intake_failure_recovery_upstream_merge_and_pr_ready(self):
        self.cli("task.py", "takeover", "--task-class", "feature_change", "--version", "develop")
        run = self.read()["run_id"]
        self.cli("task.py", "takeover", "--task-class", "feature_change", "--version", "develop", expected=2)
        self.assertEqual(self.read()["run_id"], run)
        initial = Path(self.cli("task.py", "interaction-path", "--name", "jira-intake.json").strip())
        initial.write_text(json.dumps({"source_ref": "fixture:jira-intake", "issue": {
            "key": "TAP-123", "fields": {"issuetype": {"id": "10010", "name": "Story"},
                "status": {"name": "Analyzed"}, "assignee": {"accountId": "fixture-reviewer"}}}}))
        self.cli("task.py", "snapshot", "--input", str(initial))
        self.cli("task.py", "advance", "--note", "fixture:已读 Analyzed 与负责人")
        self.cli("task.py", "advance", "--note", "缺项不能进入设计", expected=3)
        self.cli("task.py", "repository", "add", "--repo", self.repo,
                 "--base-branch", "develop", "--scope", "feature.py", "--verification", "python -B verify.py")
        repo = self.read()["repositories"][0]
        worktree = Path(repo["worktree"]["path"])
        frozen = repo["base_sha"]
        for key, value in {"acceptance_criteria": "value 返回 1", "target_repo": self.repo,
                           "verification_method": "python -B verify.py",
                           "scope_boundary": "feature.py"}.items():
            self.cli("task.py", "record", "--key", key, "--value", value)
        self.cli("task.py", "advance", "--note", "基线与输入已确认")
        plan_path = self.cli("task.py", "interaction-path", "--name", "implementation-plan.json").strip()
        Path(plan_path).write_text(json.dumps({"objective": "value 返回 1", "changes": ["修改返回值"],
            "acceptance": ["verify.py 断言返回值"], "risks": ["仅夹具"], "rollback": "回退 feature.py"}))
        self.cli("task.py", "record", "--key", "implementation_plan", "--input", plan_path)
        plan = {"id": "behavior", "checkpoint": "q4-acceptance", "timing": "after_fix",
                "case_ref": "verify.py", "case_version": "v1", "case_status": "existing", "method": "unit",
                "repository": self.repo, "target_revision": frozen, "criterion": "value 返回 1",
                "steps": "python -B verify.py", "expected_result": "PASS", "scope": "feature.py"}
        self.apply("item", {"plan": plan, "reason": "验收场景对应独立断言"})
        self.apply("select", {"item_id": "behavior", "digest": self.view()["items"]["behavior"]["plan_digest"],
                              "proof": self.proof()})
        self.cli("task.py", "advance", "--note", "未确认方案", expected=3)
        self.checkpoint("q1-intake"); self.checkpoint("q2-plan")
        self.cli("authorization.py", "grant", "--agent-id", "fixture", "--plan-version", "v1", expected=2)
        self.cli("task.py", "source-readiness")
        self.cli("authorization.py", "grant", "--agent-id", "fixture", "--plan-version", "v1")
        self.cli("task.py", "advance", "--note", "确认后开始开发")
        self.execute(worktree, "before", 1)
        failure = failures.apply(self.ws, "TAP-123", run, 0, {"action": "observe", "repository": self.repo,
            "check_id": "behavior", "label": "目标返回值断言失败", "attribution": "current_change",
            "evidence": "fixture:before-assertion"})
        problem = failure["problem_id"]
        failures.apply(self.ws, "TAP-123", run, 1, {"action": "start", "problem_id": problem, "stage": "local"})
        self.cli("task.py", "advance", "--note", "失败不能推进", expected=3)
        self.cli("task.py", "block", "--reason", "夹具断言失败，修复后重验")
        self.cli("task.py", "status", mutation=False)
        self.assertEqual(self.read()["run_id"], run)
        (worktree / "feature.py").write_text("def value():\n    return 1\n")
        self.git("-C", str(worktree), "add", "feature.py")
        self.git("-C", str(worktree), "commit", "-qm", "fixture implementation")
        before_merge = self.git("-C", str(worktree), "rev-parse", "HEAD")
        # 在本地裸仓制造真实上游变化，再按授权合并到任务分支，原冻结基线不变。
        (self.seed / "upstream.txt").write_text("upstream change\n")
        self.git("-C", str(self.seed), "add", "upstream.txt")
        self.git("-C", str(self.seed), "commit", "-qm", "fixture upstream")
        self.git("-C", str(self.seed), "push", "-q", str(self.remote), "develop")
        self.git("-C", str(worktree), "fetch", "-q", "origin", "develop")
        self.git("-C", str(worktree), "merge", "--no-edit", "origin/develop")
        head = self.git("-C", str(worktree), "rev-parse", "HEAD")
        self.assertNotEqual(head, frozen)
        self.assertEqual(self.read()["repositories"][0]["base_sha"], frozen)
        self.apply("item", {"plan": dict(plan, target_revision=head), "reason": "绑定合并后实际代码"})
        self.execute(worktree, "after", 0)
        with self.assertRaisesRegex(ValueError, "source_sync"):
            self.checkpoint("q3-draft")
        self.apply("verification", {"kind": "source_sync", "repository": self.repo, "target_revision": head,
            "source_ref": "fixture:bare-origin-fetch", "observed_at": self.proof()["at"], "source_branch": "develop",
            "source_revision": self.git("-C", str(worktree), "rev-parse", "origin/develop"),
            "before_merge_revision": before_merge, "impact_analysis_ref": "fixture:upstream-only-adds-text-file"})
        with self.assertRaisesRegex(ValueError, "失败"):
            self.checkpoint("q3-draft")
        failures.apply(self.ws, "TAP-123", run, 2, {"action": "finish", "problem_id": problem,
            "result": "PASS", "source_revision": head, "evidence": self.local_material["source_ref"]})
        self.checkpoint("q3-draft")
        body = self.view()["checkpoints"]["q3-draft"]["publication_body"]
        self.assertIn("范围 feature.py:value", body)
        self.assertIn("用例数 1", body)
        self.cli("task.py", "advance", "--note", "首轮验证完成")
        self.cli("task.py", "advance", "--note", "缺少人工验收", expected=3)
        self.cli("task.py", "repository", "record-result", "--repo", self.repo, "--pr", "1")
        args = SimpleNamespace(dir=self.ws, issue_key="TAP-123", expected_run_id=run,
                               repo=self.repo, pr="1", start_timeout=1, finish_timeout=1, interval=1)
        with mock.patch.object(ci, "fetch_rollup", return_value=([
                {"name": "fixture-check", "status": "COMPLETED", "conclusion": "SUCCESS"}], head)):
            self.assertEqual(ci.cmd_watch(args), 0)
        self.apply("verification", dict(self.local_material, kind="ci", run_ref="fixture:ci-run-1",
            checkout_ref="fixture:checkout-head", head_revision=head, attempt=1))
        self.checkpoint("q3-draft")
        self.apply("decide", {"item_id": "behavior", "digest": self.view()["items"]["behavior"]["digest"],
            "decision": {"outcome": "accept", "evidence_id": "after", "reason": "夹具人工验收",
                         "proof": self.proof()}})
        self.checkpoint("q4-acceptance")
        self.cli("task.py", "advance", "--note", "验收已确认")
        snapshot = Path(self.cli("task.py", "interaction-path", "--name", "jira-readback.json").strip())
        snapshot.write_text(json.dumps({"source_ref": "fixture:jira-readback", "issue": {
            "key": "TAP-123", "fields": {"issuetype": {"id": "10010", "name": "Story"},
                "status": {"name": "Tests Passed"}, "issuelinks": []}}}))
        result = pr_ready.check(self.ws, "TAP-123", snapshot)
        self.assertTrue(result["ready"], result)
        self.assertTrue(result["jira_status_todos"])
        before = task_store.task_path(self.ws, "TAP-123").read_bytes()
        self.assertTrue(pr_ready.check(self.ws, "TAP-123", snapshot)["ready"])
        self.assertEqual(task_store.task_path(self.ws, "TAP-123").read_bytes(), before)
        (worktree / "feature.py").write_text("def value():\n    return 2\n")
        self.assertFalse(pr_ready.check(self.ws, "TAP-123", snapshot)["ready"])



class VerificationContractTests(unittest.TestCase):
    def setUp(self):
        from workflow import verification
        self.v = verification
        self.ctx = {"repositories": {"a/repo": {"live_revision": "a" * 40, "ci_digest": "run-1"}}, "failures": {}}
        self.p = {"kind": "local", "repository": "a/repo", "target_revision": "a" * 40,
            "source_ref": "fixture:report", "analysis_ref": "fixture:diff", "case_review_ref": "fixture:assertion",
            "case_version": "case-v1", "dependency_analysis_ref": "fixture:no-jars",
            "required_scope": ["module:behavior"], "results": [{"scope": "module:behavior", "result": "PASS",
                "report_ref": "fixture:report", "tests": 1, "failures": 0, "errors": 0, "skipped": 0}]}

    def test_missing_scopes_missing_reports_and_false_pass_rejected(self):
        for change in ({"results": []}, {"required_scope": ["module:behavior", "upper:consumer"]}):
            with self.assertRaises(ValueError):
                self.v.record({}, dict(self.p, **change), self.ctx)
        for change in ({"tests": 0}, {"skipped": 1}, {"failures": 1}, {"report_ref": ""}):
            p = copy.deepcopy(self.p); p["results"][0].update(change)
            with self.assertRaises(ValueError):
                self.v.record({}, p, self.ctx)

    def test_current_versions_and_ci_observation_bind_records(self):
        model = {}
        self.v.record(model, self.p, self.ctx)
        self.assertEqual([], self.v.problems(model, self.ctx, ["local"]))
        self.assertTrue(self.v.problems(model, self.ctx, ["ci"]))
        ci_p = dict(self.p, kind="ci", head_revision="a" * 40, run_ref="fixture:run", checkout_ref="fixture:checkout", attempt=1)
        self.v.record(model, ci_p, self.ctx)
        self.ctx["repositories"]["a/repo"]["ci_digest"] = "rerun-2"
        self.assertTrue(self.v.problems(model, self.ctx, ["ci"]))
        self.assertEqual([], self.v.problems(model, self.ctx, ["local"]))
        self.ctx["repositories"]["upper/repo"] = {"live_revision": "b" * 40}
        self.assertTrue(self.v.problems(model, self.ctx, ["local"]))
        with self.assertRaises(ValueError):
            self.v.record({}, dict(ci_p, head_revision="b" * 40), self.ctx)

    def test_accepting_gap_keeps_unknown_and_requires_exact_scope(self):
        p = copy.deepcopy(self.p); p["results"][0]["result"] = "UNKNOWN"
        model = {}; self.v.record(model, p, self.ctx)
        self.assertTrue(self.v.problems(model, self.ctx, ["local"]))
        decision = {"reason": "框架不支持", "uncovered": "other", "follow_up": "关联任务处理", "proof": proof()}
        p["results"][0]["decision"] = decision
        with self.assertRaises(ValueError):
            self.v.record({}, p, self.ctx)
        decision["uncovered"] = "module:behavior"
        self.v.record(model, p, self.ctx)
        self.assertEqual([], self.v.problems(model, self.ctx, ["local"]))
        self.assertEqual("UNKNOWN", model["verification"]["a/repo"]["local"]["data"]["results"][0]["result"])

    def test_scope_change_invalidates_same_sha_material_and_keeps_history(self):
        for field in ("approved_scope", "verification_method", "catalog_digest", "base_branch", "work_branch"):
            with self.subTest(field=field):
                ctx = copy.deepcopy(self.ctx)
                model = {}
                self.v.record(model, self.p, ctx)
                saved = copy.deepcopy(model)
                ctx["repositories"]["a/repo"][field] = "changed"
                self.assertTrue(self.v.problems(model, ctx, ["local"]))
                self.assertEqual(saved, model)
                # 历史日志按事件时 context 重放，无需迁移状态或更改 epoch。
                replayed = {}
                self.v.record(replayed, self.p, self.ctx)
                self.assertEqual(saved, replayed)

    def test_pending_review_and_unresolved_failure_block(self):
        p = {"kind": "review", "repository": "a/repo", "target_revision": "a" * 40,
             "source_ref": "fixture:all-pages", "complete": True,
             "items": [{"id": "thread-1", "source_ref": "fixture:thread", "reason": "待修复", "status": "pending"}]}
        model = {}; self.v.record(model, p, self.ctx)
        self.assertTrue(self.v.problems(model, self.ctx, ["review"]))
        p["items"][0].update(status="fixed", verification_ref="fixture:retest")
        self.v.record(model, p, self.ctx)
        self.assertEqual([], self.v.problems(model, self.ctx, ["review"]))
        self.ctx["failures"]["problem"] = {"status": "running", "attempts": 1}
        self.assertTrue(self.v.problems(model, self.ctx, ["review"]))

    def test_changed_jar_invalidates_without_breaking_history_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            jar = Path(directory).resolve() / "library.jar"
            jar.write_bytes(b"version-one")
            digest = self.v.file_hash(jar)
            p = dict(self.p, jars=[{"built_path": str(jar), "consumed_path": str(jar),
                "built_sha256": digest, "consumed_sha256": digest, "loaded_from": "fixture:classpath"}])
            model = {}; self.v.record(model, p, self.ctx); self.v.verify_artifacts(p)
            self.assertEqual([], self.v.problems(model, self.ctx, ["local"]))
            jar.write_bytes(b"version-two")
            # 重放历史不读当下文件，仍可读取旧结果并提交重新验证。
            self.v.record({}, p, self.ctx)
            self.assertTrue(self.v.problems(model, self.ctx, ["local"]))
            with self.assertRaises(ValueError):
                self.v.verify_artifacts(p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
