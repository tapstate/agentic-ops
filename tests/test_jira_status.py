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
from station_fixture import save_task as save_station_task


class JiraStatusTests(unittest.TestCase):
    def test_tapdata_profiles_recognize_real_jira_test_link_direction(self):
        link_type = {"id": "10009", "name": "Test", "inward": "is tested by", "outward": "tests"}
        target = {"key": "TAP-12921", "fields": {"issuetype": {"name": "Test"}}}
        for profile in ("quality.json", "quality-feature.json"):
            with self.subTest(profile=profile):
                rules = json.loads((ROOT / "projects/tapdata" / profile).read_text())
                document = {"issue": {"key": "TAP-12833", "fields": {"issuelinks": [
                    {"type": link_type, "inwardIssue": target}]}},
                    "linked_test_details": [{"key": "TAP-12921", "test_type": "Manual",
                                             "case_version": "updated:1", "source_ref": "fixture:jira/TAP-12921"}]}
                problems, tests, ignored = jira_tests.linked_tests(document, "TAP-12833", rules)
                self.assertEqual(problems, [])
                self.assertEqual([item["key"] for item in tests], ["TAP-12921"])
                self.assertEqual(tests[0]["method"], "manual")
                self.assertEqual(ignored, [])
                for invalid in (
                    {"type": link_type, "outwardIssue": target},
                    {"type": link_type},
                    {"type": link_type, "inwardIssue": {"key": "TAP-1", "fields": {"issuetype": {"name": "Bug"}}}},
                ):
                    document["issue"]["fields"]["issuelinks"] = [invalid]
                    self.assertTrue(jira_tests.linked_tests(document, "TAP-12833", rules)[0])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ao-jira-status-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        product = self.base / "product"
        shutil.copytree(ROOT / "projects" / "tapdata", product / "projects" / "tapdata")
        (self.base / ".agenticops").mkdir()
        task_store._write_json_atomic(
            self.base / ".agenticops/station.json",
            {"project": "tapdata", "product_root": str(product)},
        )
        self.task = {"issue_key": "TAP-123", "run_id": "run-0123456789ab", "task_class": "defect_fix",
                     "stage": "task_intake", "facts": {}, "repositories": [], "pending": None, "history": []}
        save_station_task(self.base, self.task)


    def snapshot(self, status="Analyzed", assignee="u-1", required=False):
        return {"source_ref": "fixture:jira/TAP-123", "current_user": {"accountId": "u-1"},
                "issue": {"key": "TAP-123", "fields": {"status": {"id": "10", "name": status},
                                                            "assignee": {"accountId": assignee}}},
                "transitions": [{"id": "421", "name": "Start Investigation",
                                 "to": {"id": "20", "name": "In Progress"},
                                 "fields": {"fixVersions": {"required": required}}}]}

    def feature_snapshot(self):
        self.task["task_class"] = "feature_change"
        save_station_task(self.base, self.task)
        snapshot = self.snapshot()
        snapshot["issue"]["fields"]["issuetype"] = {"id": "10010"}
        snapshot["transitions"].append(dict(snapshot["transitions"][0], id="51", name="Development Started"))
        return snapshot

    def test_story_selects_own_transition_and_reads_back(self):
        snapshot = self.feature_snapshot()
        prepared = jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)
        self.assertEqual(prepared["transition_id"], "51")
        snapshot["issue"]["fields"]["status"]["name"] = "正在进行"
        self.assertEqual(jira_status.complete(self.base, "TAP-123", "takeover", "unknown", snapshot, "")["outcome"], "succeeded")
        # Same-version persisted records still load; no repeated write after completion.
        self.assertTrue(jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)["repeated"])

    def test_story_rejects_wrong_issue_type_even_with_same_status(self):
        snapshot = self.feature_snapshot()
        snapshot["issue"]["fields"]["issuetype"]["id"] = "10008"
        with self.assertRaisesRegex(ValueError, "工作类型"):
            jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)

    def test_preflight_missing_fields_can_be_filled_before_write(self):
        snapshot = self.feature_snapshot()
        snapshot["transitions"][1]["fields"] = {"fixVersions": {"required": True}}
        self.assertEqual(jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)["reason"], "required_fields_missing")
        snapshot["issue"]["fields"]["fixVersions"] = [{"id": "fixture-version"}]
        result = jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)
        self.assertEqual(result["outcome"], "ready")
        self.assertEqual(len(result["preflight_history"]), 1)
        jira_status.complete(self.base, "TAP-123", "takeover", "unknown", snapshot, "timeout")
        self.assertEqual(jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)["outcome"], "unknown")

    def test_story_tests_passed_requires_quality_and_own_fields(self):
        snapshot = self.feature_snapshot()
        self.task["stage"] = "ci_validation"
        save_station_task(self.base, self.task)
        snapshot["issue"]["fields"]["status"]["name"] = "In Progress"
        snapshot["transitions"] = [{"id": "71", "to": {"name": "Tests Passed"}, "fields": {}}]
        with mock.patch.object(jira_status, "tests_passed_ready", return_value=(False, "quality_not_verified", ["Q4 pending"], [])):
            self.assertEqual(jira_status.prepare(self.base, "TAP-123", "tests_passed", snapshot)["reason"], "quality_not_verified")
        with mock.patch.object(jira_status, "tests_passed_ready", return_value=(True, "", [], [])):
            result = jira_status.prepare(self.base, "TAP-123", "tests_passed", snapshot)
        self.assertEqual(result["transition_id"], "71")
        self.assertEqual({x["mapping"] for x in result["field_plan"]}, {"story_test_review", "fix_versions"})

    def test_unavailable_transition_is_not_prepared(self):
        snapshot = self.feature_snapshot()
        snapshot["transitions"][1]["isAvailable"] = False
        self.assertEqual(jira_status.prepare(self.base, "TAP-123", "takeover", snapshot)["reason"], "transition_unavailable")

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
        save_station_task(self.base, self.task)
        result = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot(required=True))
        self.assertEqual(result["reason"], "required_fields_missing")
        self.assertEqual(result["missing_fields"], ["fixVersions"])

    def test_tests_passed_is_independent_and_requires_q4(self):
        self.task["stage"] = "ci_validation"
        save_station_task(self.base, self.task)
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

    def collect_fixture(self):
        from workflow import jira_collect
        path = self.base / 'product/projects/tapdata/jira-transitions.json'
        config = json.loads(path.read_text())
        rule = {'jira_id': 'customfield_a', 'aliases': ['Choice'], 'value_type': 'option', 'source': ['facts.fix_plan'],
                'required_when': True, 'collect_at': 'design_review', 'decision_owner': 'Engineering DRI',
                'auto_extract': False, 'confirm_when': 'value-options-dependencies-owner-change', 'depends_on': ['facts.fix_plan']}
        config['task_classes']['defect_fix']['fields'] = {
            'choice': rule,
            'details': dict(rule, jira_id='customfield_b', aliases=['Details'], value_type='string',
                            required_when={'field': 'customfield_a', 'equals': 'need-details'}),
            'future': dict(rule, jira_id='customfield_c', aliases=['Result'], value_type='string', collect_at='acceptance')}
        config['task_classes']['defect_fix']['transitions'] = [
            {'key': 'forward', 'fields': ['choice'], 'actor': 'agent'},
            {'key': 'return', 'fields': ['choice', 'details'], 'actor': 'human'}]
        path.write_text(json.dumps(config))
        snapshot = self.snapshot()
        metadata = {'name': 'Choice', 'schema': {'type': 'option'}, 'required': False,
                    'allowedValues': [{'id': '1', 'value': 'ordinary'}, {'id': '2', 'value': 'need-details'}]}
        snapshot['transitions'][0]['fields'] = {'customfield_a': metadata}
        snapshot['transitions'].append({'id': '999', 'to': {'name': 'Open'}, 'fields': {'customfield_a': metadata}})
        self.task['facts']['fix_plan'] = 'confirmed-plan'; save_station_task(self.base, self.task)
        return snapshot

    def confirm_packet(self, checkpoint, snapshot, proposals):
        from workflow import jira_collect
        packet = jira_collect.collect(self.base, self.task, checkpoint, snapshot, proposals)
        proof = {'actor': 'fixture', 'source': 'user_message', 'reference': 'fixture:batch-confirmation',
                 'at': '2026-09-20T12:00:00+08:00'}
        return jira_collect.confirm(self.base, self.task['issue_key'], self.task['run_id'], checkpoint, snapshot,
            proposals, {key: packet['fields'][key]['digest'] for key in proposals}, proof)

    def test_collect_is_readonly_deduplicates_and_defers_future_fields(self):
        from workflow import jira_collect
        snapshot = self.collect_fixture()
        before = {str(p): p.read_bytes() for p in (self.base / '.agenticops').rglob('*') if p.is_file()}
        packet = jira_collect.collect(self.base, self.task, 'design_review', snapshot)
        self.assertEqual(['customfield_a'], packet['pending'])
        self.assertEqual(['customfield_c'], packet['future'])
        self.assertEqual(['forward', 'native:421', 'native:999', 'return'], packet['fields']['customfield_a']['transitions'])
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.base / '.agenticops').rglob('*') if p.is_file()})
        packet = self.confirm_packet('design_review', snapshot, {'customfield_a': {'id': '1'}})
        self.assertEqual(['customfield_a'], packet['confirmed'])
        snapshot['issue']['fields']['comment'] = 'unrelated'
        packet = jira_collect.collect(self.base, self.task, 'design_review', snapshot)
        self.assertEqual(['customfield_a'], packet['confirmed'])
        snapshot['issue']['fields']['assignee'] = {'accountId': 'another-owner'}
        self.assertEqual(['customfield_a'], jira_collect.collect(self.base, self.task, 'design_review', snapshot)['pending'])

    def test_collect_condition_options_dependencies_and_dynamic_fields(self):
        from workflow import jira_collect
        snapshot = self.collect_fixture()
        values = {'customfield_a': {'id': '2'}}
        packet = jira_collect.collect(self.base, self.task, 'design_review', snapshot, values)
        self.assertEqual(['customfield_a', 'customfield_b'], packet['pending'])
        with self.assertRaisesRegex(ValueError, '一次确认全部'):
            self.confirm_packet('design_review', snapshot, values)
        values['customfield_b'] = 'cause and evidence'
        self.assertEqual(['customfield_a', 'customfield_b'], self.confirm_packet('design_review', snapshot, values)['confirmed'])
        snapshot['transitions'][0]['fields']['customfield_a']['allowedValues'] = [{'id': '3', 'value': 'new'}]
        packet = jira_collect.collect(self.base, self.task, 'design_review', snapshot)
        self.assertIn('customfield_a', packet['pending'])
        self.assertFalse(packet['fields']['customfield_a']['valid_value'])
        snapshot['transitions'][0]['fields']['customfield_new'] = {'name': 'New required field', 'required': True, 'schema': {'type': 'string'}}
        self.assertIn('customfield_new', jira_collect.collect(self.base, self.task, 'design_review', snapshot)['pending'])
        self.task['facts']['fix_plan'] = 'changed plan'; save_station_task(self.base, self.task)
        self.assertIn('customfield_b', jira_collect.collect(self.base, self.task, 'design_review', snapshot)['pending'])

    def test_status_reentry_unknown_write_and_old_operation_reuse(self):
        snapshot = self.collect_fixture()
        # Intake does not ask for design/acceptance values.
        first = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot, 'op-jira-first')
        self.assertEqual('ready', first['outcome'])
        jira_status.complete(self.base, 'TAP-123', 'takeover', 'unknown', snapshot, 'timeout', 'op-jira-first')
        with self.assertRaisesRegex(ValueError, '前次 Jira 写入'):
            jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot, 'op-jira-second')
        with self.assertRaisesRegex(ValueError, '明确结果'):
            jira_status.complete(self.base, 'TAP-123', 'takeover', 'not_written', snapshot, '', 'op-jira-first')
        snapshot['operation_result'] = {'operation_id': 'op-jira-first', 'effect': 'not_written', 'source_ref': 'fixture:rejected-before-send'}
        jira_status.complete(self.base, 'TAP-123', 'takeover', 'not_written', snapshot, '', 'op-jira-first')
        second = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot, 'op-jira-second')
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])
        snapshot['issue']['fields']['status']['name'] = 'In Progress'
        jira_status.complete(self.base, 'TAP-123', 'takeover', 'unknown', snapshot, '', 'op-jira-second')
        snapshot['issue']['fields']['status']['name'] = 'Analyzed'
        third = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot, 'op-jira-third')
        self.assertEqual('ready', third['outcome'])
        repeated = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot, 'op-jira-second')
        self.assertTrue(repeated['repeated'])
        self.assertEqual('succeeded', repeated['outcome'])
        self.assertEqual(3, len(jira_status.load_state(self.base, self.task)['attempts_by_id']))

    def test_manual_transition_returns_collection_without_auto_authority(self):
        snapshot = self.collect_fixture()
        result = jira_status.prepare(self.base, 'TAP-123', 'native:999', snapshot, 'op-jira-human')
        self.assertEqual('handoff', result['outcome'])
        self.assertIn('decision_packet', result)
        self.assertFalse(jira_status.load_state(self.base, self.task)['attempts'])
        self.task['task_class'] = 'technical_task'; save_station_task(self.base, self.task)
        self.assertEqual('handoff', jira_status.prepare(self.base, 'TAP-123', 'native:999', snapshot, 'op-jira-tech')['outcome'])
        with self.assertRaisesRegex(ValueError, '未知 Jira'):
            jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot, 'op-jira-tech')

    def test_pr_ready_requires_all_three_groups(self):
        self.task["stage"] = "ci_validation"
        save_station_task(self.base, self.task)
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
        rules = {"task_classes": [], "tests_passed": {"linked_test_task": {"relations": ["tests"], "issue_types": ["Test"]},
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

    def test_optional_linked_tests_only_allow_verified_absence(self):
        rules = {"tests_passed": {"linked_test_task": {"relations": ["tests"], "issue_types": ["Test"]},
                 "test_types": {"Manual": {"method": "manual"}}, "ignored_test_types": {}},
                 "pr_ready": {"require_linked_test_tasks": False}}
        doc = {"issue": {"key": "TAP-123", "fields": {"issuelinks": []}}}
        self.assertEqual(jira_tests.linked_tests(doc, "TAP-123", rules), ([], [], []))
        for value in (True, None, "false", 0):
            with self.subTest(required=value):
                rules["pr_ready"]["require_linked_test_tasks"] = value
                self.assertTrue(jira_tests.linked_tests(doc, "TAP-123", rules)[0])
        rules["pr_ready"]["require_linked_test_tasks"] = False
        for links in (None, [{}], [{"type": {}}], [{"type": {"outward": "tests"}}]):
            with self.subTest(links=links):
                doc["issue"]["fields"]["issuelinks"] = links
                self.assertTrue(jira_tests.linked_tests(doc, "TAP-123", rules)[0])

        doc["issue"]["fields"]["issuelinks"] = [{"type": {"outward": "tests"},
            "outwardIssue": {"key": "TAP-T1", "fields": {"issuetype": {"name": "Test"}}}}]
        self.assertTrue(jira_tests.linked_tests(doc, "TAP-123", rules)[0])
        doc["linked_test_details"] = [{"key": "TAP-T1", "test_type": "Manual", "case_version": "v1",
                                       "source_ref": "fixture:jira/TAP-T1"}]
        problems, tests, ignored = jira_tests.linked_tests(doc, "TAP-123", rules)
        self.assertEqual(problems, [])
        self.assertEqual([item["key"] for item in tests], ["TAP-T1"])
        self.assertTrue(jira_tests.confirmation_problems({"items": {}}, tests))
        doc["linked_test_details"][0]["test_type"] = "unknown"
        self.assertTrue(jira_tests.linked_tests(doc, "TAP-123", rules)[0])

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
        save_station_task(self.base, self.task)
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


class TerminalStatusTests(unittest.TestCase):
    setUp = JiraStatusTests.setUp
    snapshot = JiraStatusTests.snapshot

    def prepare(self):
        # 本组只验证恢复边界；表单采集由原有 collect 回归覆盖。
        with mock.patch("workflow.jira_collect.collect", return_value={"pending": [], "unknown": []}):
            return jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot(), "op-original-takeover")

    def test_completed_original_transition_can_read_back_not_prepare(self):
        from workflow import external_sync, station_resources
        operation_id = "op-original-takeover"
        record = self.prepare()
        jira_status.complete(self.base, "TAP-123", "takeover", "failed", self.snapshot(), "timeout", operation_id)
        self.task.update(stage="completed", outcome="completed")
        save_station_task(self.base, self.task)
        with self.assertRaisesRegex(ValueError, "未知"):
            station_resources.verify_known_external(self.base, self.task)
        with self.assertRaises(ValueError):
            jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot(), "op-new-takeover")
        before = (self.base / ".agenticops/current-task.json").read_bytes()
        done = jira_status.complete(self.base, "TAP-123", "takeover", "unknown", self.snapshot("In Progress"), "", operation_id)
        self.assertEqual(done["outcome"], "succeeded")
        self.assertEqual(done["operation_id"], record["operation_id"])
        self.assertEqual(before, (self.base / ".agenticops/current-task.json").read_bytes())
        station_resources.verify_known_external(self.base, self.task)

    def test_defect_takeover_recovery_window_and_explicit_not_written(self):
        for stage in ("design_review", "implementation"):
            self.task["stage"] = stage
            save_station_task(self.base, self.task)
            record = self.prepare()
            self.assertEqual(record["outcome"], "ready")
        with self.assertRaisesRegex(ValueError, "未写入"):
            jira_status.complete(self.base, "TAP-123", "takeover", "not_written", self.snapshot(), "", "op-original-takeover")


if __name__ == "__main__":
    unittest.main(verbosity=2)
