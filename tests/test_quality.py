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
from workflow import ci, evidence, quality, quality_contract, task, task_store


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
        (self.base / ".agenticops").mkdir()
        (self.base / ".agenticops/workspace.json").write_text(json.dumps({"project": "tapdata", "product_root": str(product)}))
        self.task = {"issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "defect_fix",
                     "stage": "implementation", "facts": {"fix_plan": "修正目标断言的实现并回归，风险限定在夹具"}, "repositories": [
                         {"repository": "tapdata/tapdata", "approved_scope": "bug-fix"},
                         {"repository": "tapdata/tapdata-manager", "approved_scope": "bug-fix"}],
                     "pending": None, "history": []}
        self.save_task()
        task_store.register(self.base, "TAP-123")

    def save_task(self):
        task_store._write_json_atomic(task_store.task_path(self.base, self.task["issue_key"]), self.task)

    def view(self):
        return quality.report(quality.load(self.base, self.task), quality.config(self.base), quality.context(self.base, self.task))

    def apply(self, action, payload):
        return quality.apply(self.base, "TAP-123", self.task["run_id"], self.view()["revision"],
                             {"action": action, "payload": payload})

    def plan(self, key="case-a", method="integration", before=False, repo="tapdata/tapdata"):
        plan = {"id": key, "checkpoint": "q2-plan" if before else "q4-acceptance",
                "timing": "before_fix" if before else "after_fix", "case_ref": "case:" + key,
                "case_version": "test-v1", "case_status": "existing", "method": method,
                "repository": repo, "target_revision": "a" * 40, "criterion": "目标行为符合预期",
                "steps": "启动测试服务，执行目标操作，检查日志和返回结果",
                "expected_result": "FAIL" if before else "PASS", "scope": "目标故障路径"}
        self.apply("item", {"plan": plan, "reason": "已有覆盖，建议复用"})
        return plan

    def select(self, key="case-a"):
        return self.apply("select", {"item_id": key, "digest": self.view()["items"][key]["plan_digest"], "proof": proof()})

    def execute(self, key="case-a", result="PASS", execution_id="run-1", origin="local_maven", kind="none", **changes):
        plan = self.view()["items"][key]["plan"]
        execution = {k: plan[k] for k in ("case_ref", "case_version", "method", "repository", "target_revision")}
        execution.update(id=execution_id, origin=origin, source_ref="fixture:report/" + execution_id,
                         environment="local-fixture", observed_at=proof()["at"], raw_result=result,
                         failure_kind=kind, observation="观察目标断言与报告")
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

    def test_verified_clean_commit_survives_controlled_worktree_cleanup(self):
        repo = self.base / "git-repo"; repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.test",
                        "commit", "-qm", "fixture", "--allow-empty"], check=True)
        sha = quality.git_revision(repo)
        self.task["repositories"][0]["worktree"] = {"status": "prepared", "path": str(repo)}; self.save_task()
        plan = self.plan()
        self.apply("item", {"plan": dict(plan, target_revision=sha), "reason": "核验目标提交"})
        self.select(); self.execute(); self.decide(); self.checkpoint("q4-acceptance")
        self.publish_checkpoint("q4-acceptance")
        self.task["repositories"][0]["worktree"] = {"status": "removed", "final_revision": sha}; self.save_task()
        view = self.view()
        self.assertTrue(view["checkpoints"]["q4-acceptance"]["reviewed"])
        self.assertTrue(view["checkpoints"]["q4-acceptance"]["published"])
        self.task["repositories"][0]["worktree"]["final_revision"] = "b" * 40; self.save_task()
        self.assertFalse(self.view()["checkpoints"]["q4-acceptance"]["reviewed"])

    def test_checkpoint_publication_is_mandatory_and_cannot_be_faked_by_generic_comment(self):
        self.plan(); self.select(); self.execute(); self.automatic_checkpoint()
        self.assertTrue(any("Jira" in p for p in quality.advance_problems(self.base, self.task, "pr_review")))
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

    def test_old_run_isolation_and_pending_write_recovery(self):
        record = self.publication(); old_run = self.task["run_id"]
        old_revision = self.view()["revision"]
        self.task["run_id"] = "run-fedcba987654"; self.save_task()
        self.assertEqual(self.view()["revision"], 0)
        with self.assertRaisesRegex(ValueError, "run 已变化"):
            quality.apply(self.base, "TAP-123", old_run, old_revision, {"action": "draft", "payload": {"id": "summary", "body": "x"}})
        with self.assertRaisesRegex(ValueError, "旧 run"):
            self.publication()
        quality.apply(self.base, "TAP-123", old_run, old_revision, {"action": "readback", "payload": {
            "id": "summary", "operation_id": record["operation_id"], "site": record["site"],
            "issue_key": "TAP-123", "comment_id": "100", "body": record["body"], "source_ref": "fixture:jira/100"}})
        self.publication()

    def test_ci_unknown_skipped_repo_run_isolation_and_cas(self):
        for checks, expected in (([{"status": "COMPLETED"}], "unknown"), ([{"conclusion": "SKIPPED"}], "skipped"),
                                 ([{"state": "alien"}], "unknown"), ([{"state": "SUCCESS"}], "success")):
            self.assertEqual(ci.classify(checks)[0], expected)
        a = ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata")
        b = copy.deepcopy(a)
        a["fix_attempts"] = 2; ci.save_state(self.base, "TAP-123", "1", a)
        self.assertEqual(ci.load_state(self.base, "TAP-123", "1", "tapdata/tapdata-manager")["fix_attempts"], 0)
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
                               interval=0, start_timeout=0, finish_timeout=0)
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
        spec = quality.project_rules.load_admission(workspace=self.base)
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
        repo = self.base / "git-repo"; repo.mkdir()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
