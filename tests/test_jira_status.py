#!/usr/bin/env python3
"""Jira 非阻断状态同步和 PR Ready 验收测试；不访问外部 Jira/GitHub。"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import jira_status, jira_tests, pr_ready, task_store  # noqa: E402


class JiraStatusTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ao-jira-status-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        product = self.base / "product"
        shutil.copytree(ROOT / "projects" / "tapdata", product / "projects" / "tapdata")
        (self.base / ".agenticops").mkdir()
        task_store._write_json_atomic(
            self.base / ".agenticops/workspace.json",
            {"project": "tapdata", "product_root": str(product)},
        )
        self.task = {"issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "defect_fix",
                     "stage": "task_intake", "facts": {}, "repositories": [], "pending": None, "history": []}
        task_store._write_json_atomic(task_store.task_path(self.base, "TAP-123"), self.task)
        task_store.register(self.base, "TAP-123")

    def snapshot(self, status="Analyzed", assignee="u-1", required=False):
        return {"source_ref": "fixture:jira/TAP-123", "current_user": {"accountId": "u-1"},
                "issue": {"key": "TAP-123", "fields": {"status": {"id": "10", "name": status},
                                                            "assignee": {"accountId": assignee}}},
                "transitions": [{"id": "421", "name": "Start Investigation",
                                 "to": {"id": "20", "name": "In Progress"},
                                 "fields": {"fixVersions": {"required": required}}}]}

    def test_takeover_prepares_once_and_readback_completes(self):
        first = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot())
        self.assertEqual(first["outcome"], "ready")
        self.assertEqual(first["transition_id"], "421")
        repeated = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot())
        self.assertTrue(repeated["repeated"])
        readback = self.snapshot(status="In Progress")
        done = jira_status.complete(self.base, "TAP-123", "takeover", "unknown", readback, "")
        self.assertEqual(done["outcome"], "succeeded")

    def test_takeover_accepts_configured_chinese_target_status(self):
        snapshot = self.snapshot()
        snapshot["transitions"][0]["to"]["name"] = "正在进行"
        ready = jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)
        self.assertEqual(ready["outcome"], "ready")
        self.assertEqual(ready["target_statuses"], ["In Progress", "正在进行"])
        readback = self.snapshot(status="正在进行")
        done = jira_status.complete(self.base, "TAP-123", "takeover", "unknown", readback, "")
        self.assertEqual(done["outcome"], "succeeded")

    def test_mismatch_and_missing_fields_skip_without_retry(self):
        result = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot(status="Open"))
        self.assertEqual(result["reason"], "jira_status_mismatch")
        self.task["run_id"] = "run-fedcba987654"
        task_store._write_json_atomic(task_store.task_path(self.base, "TAP-123"), self.task)
        result = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot(required=True))
        self.assertEqual(result["reason"], "required_fields_missing")
        self.assertEqual(result["missing_fields"], ["fixVersions"])

    def test_tests_passed_is_independent_and_requires_q4(self):
        self.task["stage"] = "ci_validation"
        task_store._write_json_atomic(task_store.task_path(self.base, "TAP-123"), self.task)
        snapshot = self.snapshot(status="In Progress")
        snapshot["transitions"] = [{"id": "501", "name": "Tests Pass",
                                    "to": {"id": "30", "name": "Tests Passed"}, "fields": {}}]
        with mock.patch.object(jira_status, "tests_passed_ready",
                               return_value=(False, "quality_not_verified", ["Q4 尚未通过"], [])):
            blocked = jira_status.prepare(self.base, "TAP-123", "tests_passed", snapshot)
        self.assertEqual(blocked["reason"], "quality_not_verified")
        with mock.patch.object(jira_status, "tests_passed_ready", return_value=(True, "", [], [])):
            ready = jira_status.prepare(self.base, "TAP-123", "tests_passed", snapshot)
        self.assertEqual(ready["outcome"], "ready")
        self.assertEqual(ready["transition_id"], "501")
        self.assertEqual(len(ready["preflight_history"]), 1)
        self.assertIn("issue_analysis", {item["mapping"] for item in ready["field_plan"]})

    def test_external_error_message_is_redacted(self):
        jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot())
        result = jira_status.complete(self.base, "TAP-123", "takeover", "failed",
                                      self.snapshot(), "password=do-not-store")
        self.assertEqual(result["message"], "外部错误信息含敏感内容，原文未保存")

    def test_pr_ready_requires_all_three_groups(self):
        self.task["stage"] = "ci_validation"
        task_store._write_json_atomic(task_store.task_path(self.base, "TAP-123"), self.task)
        input_path = self.base / "jira.json"
        def linked_test(status_name="Done", category="done"):
            return {
                "source_ref": "fixture:jira",
                "issue": {"key": "TAP-123", "fields": {"issuelinks": [
                    {"type": {"outward": "tests"}, "outwardIssue": {
                        "key": "TAP-T1", "fields": {"issuetype": {"name": "Test"}, "status": {
                            "name": status_name, "statusCategory": {"key": category}}}}}
                ]}},
                "linked_test_details": [{"key": "TAP-T1", "test_type": "Manual", "case_version": "updated:1",
                                         "source_ref": "fixture:jira/TAP-T1"}],
            }
        rules = {"tests_passed": {"linked_test_task": {"relations": ["tests"], "issue_types": ["Test"]},
                                   "test_types": {"Manual": {"method": "manual"}}, "ignored_test_types": {}},
                 "pr_ready": {"require_linked_test_tasks": True}}
        input_path.write_text(json.dumps(linked_test()))
        with mock.patch.object(pr_ready, "quality_problems", return_value=[]), \
                mock.patch.object(pr_ready, "linked_test_confirmation_problems", return_value=[]), \
                mock.patch.object(pr_ready, "ci_problems", return_value=[]), \
                mock.patch.object(pr_ready.quality, "config", return_value=rules):
            result = pr_ready.check(self.base, "TAP-123", input_path)
        self.assertTrue(result["ready"])
        input_path.write_text(json.dumps(linked_test("In Progress", "indeterminate")))
        with mock.patch.object(pr_ready, "quality_problems", return_value=[]), \
                mock.patch.object(pr_ready, "linked_test_confirmation_problems", return_value=[]), \
                mock.patch.object(pr_ready, "ci_problems", return_value=[]), \
                mock.patch.object(pr_ready.quality, "config", return_value=rules):
            result = pr_ready.check(self.base, "TAP-123", input_path)
        self.assertTrue(result["ready"])

    def test_pr_ready_derives_test_tasks_from_issue_links_and_checks_type(self):
        rules = {"tests_passed": {"linked_test_task": {"relations": ["tests"], "issue_types": ["Test"]},
                                   "test_types": {"Manual": {"method": "manual"}}, "ignored_test_types": {}},
                 "pr_ready": {"require_linked_test_tasks": True}}

        def problems(links, details=None):
            path = self.base / "jira-links.json"
            path.write_text(json.dumps({"source_ref": "fixture:jira", "issue": {
                "key": "TAP-123", "fields": {"issuelinks": links}}, "linked_test_details": details or []}))
            return pr_ready.jira_test_tasks(path, "TAP-123", rules)[0]

        valid = {"type": {"outward": "tests"}, "outwardIssue": {"key": "TAP-12834", "fields": {
            "issuetype": {"name": "Test"}, "status": {"name": "Done", "statusCategory": {"key": "done"}}}}}
        details = [{"key": "TAP-12834", "test_type": "Manual", "case_version": "updated:1",
                    "source_ref": "fixture:jira/TAP-12834"}]
        self.assertEqual(problems([valid], details), [])

        wrong_relation = {"type": {"outward": "relates to"}, "outwardIssue": valid["outwardIssue"]}
        self.assertTrue(any("未返回" in item for item in problems([wrong_relation], details)))

        wrong_type = {"type": {"outward": "tests"}, "outwardIssue": {"key": "TAP-1", "fields": {
            "issuetype": {"name": "Bug"}, "status": {"name": "Done", "statusCategory": {"key": "done"}}}}}
        self.assertTrue(any("测试任务类型" in item for item in problems([wrong_type], details)))

        unfinished = {"type": {"outward": "tests"}, "outwardIssue": {"key": "TAP-2", "fields": {
            "issuetype": {"name": "Test"}, "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}}}
        self.assertEqual(problems([unfinished], [{"key": "TAP-2", "test_type": "Manual", "case_version": "updated:1",
                                                   "source_ref": "fixture:jira/TAP-2"}]), [])

    def test_pr_ready_rejects_legacy_derived_test_tasks_and_missing_link_metadata(self):
        rules = {"tests_passed": {"linked_test_task": {"relations": ["tests"], "issue_types": ["Test"]},
                                   "test_types": {"Manual": {"method": "manual"}}, "ignored_test_types": {}},
                 "pr_ready": {"require_linked_test_tasks": True}}
        path = self.base / "jira-links.json"
        path.write_text(json.dumps({"source_ref": "fixture:jira", "issue": {"key": "TAP-123", "fields": {}},
                                    "linked_test_tasks": [{"key": "TAP-12834"}]}))
        problems, _, _ = pr_ready.jira_test_tasks(path, "TAP-123", rules)
        self.assertTrue(any("issuelinks" in item for item in problems))

        missing_status = {"type": {"outward": "tests"}, "outwardIssue": {
            "key": "TAP-12834", "fields": {"issuetype": {"name": "Test"}}}}
        path.write_text(json.dumps({"source_ref": "fixture:jira", "issue": {
            "key": "TAP-123", "fields": {"issuelinks": [missing_status]}}, "linked_test_details": [{
                "key": "TAP-12834", "test_type": "Manual", "case_version": "updated:1", "source_ref": "fixture:jira/TAP-12834"}]}))
        problems, _, tests = pr_ready.jira_test_tasks(path, "TAP-123", rules)
        self.assertEqual(problems, [])
        self.assertEqual(tests[0]["key"], "TAP-12834")

        path.write_text(json.dumps({"source_ref": "fixture:jira", "issue": {
            "key": "TAP-123", "fields": {"issuelinks": [], "customfield_10416": "Exception Approved - Low Risk"}}}))
        problems, _, _ = pr_ready.jira_test_tasks(path, "TAP-123", rules)
        self.assertTrue(any("未返回" in item for item in problems))

        self.assertTrue(any("请用户" in item for item in problems))

    def test_pr_ready_requires_user_acceptance_for_each_linked_test(self):
        rules = {"pr_ready": {"require_user_confirmation_per_linked_test": True,
                                "accepted_outcomes": ["accept", "not_applicable"]}}
        task = dict(self.task)
        linked = [{"key": "TAP-12834", "case_version": "updated:1", "method": "manual"}]
        report = {"items": {"test-12834": {"plan": {"checkpoint": "q4-acceptance", "case_ref": "TAP-12834",
                                                       "case_version": "updated:1", "method": "manual"},
                                               "decision_valid": True,
                                               "decision": {"decision": {"outcome": "accept"}}}}}
        with mock.patch.object(pr_ready.quality, "load"), \
                mock.patch.object(pr_ready.quality, "context"), \
                mock.patch.object(pr_ready.quality, "report", return_value=report):
            self.assertEqual(pr_ready.linked_test_confirmation_problems(self.base, task, rules, linked), [])
        report["items"]["test-12834"]["decision"]["decision"]["outcome"] = "not_applicable"
        with mock.patch.object(pr_ready.quality, "load"), \
                mock.patch.object(pr_ready.quality, "context"), \
                mock.patch.object(pr_ready.quality, "report", return_value=report):
            self.assertTrue(any("确认测试成功" in item for item in
                                pr_ready.linked_test_confirmation_problems(self.base, task, rules, linked)))

    def test_linked_tests_distinguishes_supported_and_ignored_types(self):
        rules = {"tests_passed": {"linked_test_task": {"relations": ["tests"], "issue_types": ["Test"]},
                                   "test_types": {"Manual": {"method": "manual"}, "Unit": {"method": "unit"}},
                                   "ignored_test_types": {"TapCE": "当前不纳管"}}}
        document = {"issue": {"key": "TAP-123", "fields": {"issuelinks": [
            {"type": {"outward": "tests"}, "outwardIssue": {"key": "TAP-M", "fields": {"issuetype": {"name": "Test"}}}},
            {"type": {"outward": "tests"}, "outwardIssue": {"key": "TAP-C", "fields": {"issuetype": {"name": "Test"}}}},
        ]}}, "linked_test_details": [
            {"key": "TAP-M", "test_type": "Manual", "case_version": "updated:1", "source_ref": "fixture:TAP-M"},
            {"key": "TAP-C", "test_type": "TapCE", "case_version": "updated:2", "source_ref": "fixture:TAP-C"},
        ]}
        problems, managed, ignored = jira_tests.linked_tests(document, "TAP-123", rules)
        self.assertEqual(problems, [])
        self.assertEqual([(item["key"], item["method"]) for item in managed], [("TAP-M", "manual")])
        self.assertEqual([(item["key"], item["test_type"]) for item in ignored], [("TAP-C", "TapCE")])
        document["linked_test_details"] = document["linked_test_details"][1:]
        problems, managed, ignored = jira_tests.linked_tests(document, "TAP-123", rules)
        self.assertEqual(managed, [])
        self.assertTrue(any("没有可纳管" in problem for problem in problems))
        self.assertEqual(len(ignored), 1)

    def test_tests_passed_requires_jira_type_and_current_case_version(self):
        self.task["stage"] = "ci_validation"
        task_store._write_json_atomic(task_store.task_path(self.base, "TAP-123"), self.task)
        rules = {"tests_passed": {"linked_test_task": {"relations": ["tests"], "issue_types": ["Test"]},
                                   "test_types": {"Manual": {"method": "manual"}}, "ignored_test_types": {}}}
        report = {
            "checkpoints": {"q4-acceptance": {"decision": {"decision": {"outcome": "accept"}}}},
            "items": {"manual": {"plan": {"checkpoint": "q4-acceptance", "case_ref": "TAP-T1",
                                                "case_version": "updated:1", "method": "manual"},
                                  "decision_valid": True, "decision": {"decision": {"outcome": "accept"}}}},
        }
        snapshot = self.snapshot(status="In Progress")
        snapshot["issue"]["fields"]["issuelinks"] = [{"type": {"outward": "tests"}, "outwardIssue": {
            "key": "TAP-T1", "fields": {"issuetype": {"name": "Test"}}}}]
        snapshot["linked_test_details"] = [{"key": "TAP-T1", "test_type": "Manual", "case_version": "updated:1",
                                             "source_ref": "fixture:TAP-T1"}]
        with mock.patch.object(jira_status, "strict_checkpoint_ready", return_value=(True, [])), \
                mock.patch.object(jira_status.quality, "config", return_value=rules), \
                mock.patch.object(jira_status.quality, "load"), \
                mock.patch.object(jira_status.quality, "context"), \
                mock.patch.object(jira_status.quality, "report", return_value=report):
            self.assertEqual(jira_status.tests_passed_ready(self.base, self.task, snapshot)[:3], (True, "", []))
        snapshot["linked_test_details"][0]["case_version"] = "updated:2"
        with mock.patch.object(jira_status, "strict_checkpoint_ready", return_value=(True, [])), \
                mock.patch.object(jira_status.quality, "config", return_value=rules), \
                mock.patch.object(jira_status.quality, "load"), \
                mock.patch.object(jira_status.quality, "context"), \
                mock.patch.object(jira_status.quality, "report", return_value=report):
            ready, reason, problems, _ = jira_status.tests_passed_ready(self.base, self.task, snapshot)
        self.assertFalse(ready)
        self.assertEqual(reason, "linked_test_facts_not_ready")
        self.assertTrue(any("当前 Jira 用例版本" in problem for problem in problems))

    def test_pr_checks_bind_success_to_current_head(self):
        repository = self.base / "repo"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c",
                        "user.email=test@example.test", "commit", "-q", "--allow-empty", "-m", "fixture"], check=True)
        head = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
        task = dict(self.task, repositories=[{"repository": "tapdata/tapdata", "pull_request": "8",
                                              "worktree": {"status": "prepared", "path": str(repository)}}])
        state = {"repository": "tapdata/tapdata", "pr": "8",
                 "history": [{"verdict": "success", "head": head}]}
        with mock.patch.object(pr_ready.ci, "current_states", return_value=[state]):
            self.assertEqual(pr_ready.ci_problems(self.base, task), [])
        (repository / "dirty.txt").write_text("uncommitted", encoding="utf-8")
        with mock.patch.object(pr_ready.ci, "current_states", return_value=[state]):
            self.assertTrue(any("未提交修改" in item for item in pr_ready.ci_problems(self.base, task)))
        (repository / "dirty.txt").unlink()
        state["history"][0]["head"] = "0" * 40
        with mock.patch.object(pr_ready.ci, "current_states", return_value=[state]):
            self.assertTrue(any("Head" in item for item in pr_ready.ci_problems(self.base, task)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
