#!/usr/bin/env python3
"""Jira 非阻断状态同步和 PR Ready 验收测试；不访问外部 Jira/GitHub。"""
import json
import copy
import test_quality as fixtures
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import jira_status, jira_tests, pr_ready, task_store, verification  # noqa: E402
from station_fixture import save_task as save_station_task


from station_fixture import task_state_snapshot

class JiraStatusTests(unittest.TestCase):
    def seed_two_repository_collection(self, task_class):
        self.task['task_class'] = task_class
        self.task['repositories'] = [
            {'repository': name, 'worktree': {'status': 'prepared',
             'path': '/Users/fixture/station/source/' + name}}
            for name in ('tapdata/tapdata', 'tapdata/tapdata-web')]
        save_station_task(self.base, self.task)
        # 工位加载按宿主目录生成路径；模拟 macOS 用户目录下的同一投影。
        for repo in self.task['repositories']:
            repo['worktree']['path'] = '/Users/fixture/station/source/' + repo['repository']
        return self.snapshot()

    def test_two_repository_fix_details_wait_for_actual_summary(self):
        from workflow import jira_collect
        for task_class in ('feature_change', 'defect_fix'):
            with self.subTest(task_class=task_class):
                snapshot = self.seed_two_repository_collection(task_class)
                before = task_state_snapshot(self.base)
                for checkpoint in ('intake', 'design_review'):
                    packet = jira_collect.collect(self.base, self.task, checkpoint, snapshot)
                    self.assertIn('customfield_10093', packet['future'])
                    self.assertEqual([], packet['fields']['customfield_10093']['suggestions'])
                if task_class == 'defect_fix':
                    self.assertIn('customfield_10098', packet['pending'])
                    self.assertEqual([], packet['fields']['customfield_10098']['suggestions'])
                    self.assertFalse(packet['fields']['customfield_10098']['valid_value'])
                packet = jira_collect.collect(self.base, self.task, 'acceptance', snapshot)
                suggestion = packet['fields']['customfield_10093']['suggestions'][0]
                self.assertEqual('actual_verification', suggestion['source'])
                self.assertIn('待核验', suggestion['value'])
                self.assertIn('tapdata/tapdata-web', suggestion['value'])
                self.assertNotIn('/Users/', json.dumps(packet))
                self.assertNotIn('customfield_10093', packet['confirmed'])
                self.assertEqual(before, task_state_snapshot(self.base))

    def test_native_required_fix_details_collects_summary_early(self):
        from workflow import jira_collect
        snapshot = self.seed_two_repository_collection('feature_change')
        snapshot['transitions'][0]['fields']['customfield_10093'] = {
            'name': 'Fix Details', 'schema': {'type': 'string'}, 'required': True}
        packet = jira_collect.collect(self.base, self.task, 'design_review', snapshot)
        self.assertIn('customfield_10093', packet['pending'])
        self.assertNotIn('customfield_10093', packet['future'])
        self.assertEqual('actual_verification',
                         packet['fields']['customfield_10093']['suggestions'][0]['source'])
        self.assertFalse(packet['fields']['customfield_10093']['valid_value'])

    def test_fix_details_explicit_sensitive_values_still_rejected(self):
        from workflow import jira_collect
        snapshot = self.seed_two_repository_collection('feature_change')
        before = task_state_snapshot(self.base)
        for value in ('/Users/fixture/private/report', 'password=fixture-secret'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '采集结果含敏感内容'):
                    jira_collect.collect(self.base, self.task, 'design_review', snapshot,
                                         {'customfield_10093': value})
                snapshot['issue']['fields']['customfield_10093'] = value
                with self.assertRaisesRegex(ValueError, 'Jira 采集快照含敏感内容'):
                    jira_collect.collect(self.base, self.task, 'design_review', snapshot)
                del snapshot['issue']['fields']['customfield_10093']
        self.assertEqual(before, task_state_snapshot(self.base))

    def test_sensitive_packet_reports_location_without_disclosing_value(self):
        from workflow import jira_collect
        spec = json.loads((ROOT / "projects/tapdata/admission.json").read_text())
        for text in ("password=fixture-secret", "/Users/fixture/private/report.json",
                     "Bearer fixture-token-123456"):
            with self.subTest(value=text):
                with self.assertRaises(ValueError) as error:
                    jira_collect.require_safe(spec, {"fields": {"customfield_a": {
                        "suggestions": [{"value": text}]}}}, "采集结果")
                self.assertIn("$.fields.customfield_a.suggestions[0].value", str(error.exception))
                self.assertNotIn(text, str(error.exception))
        jira_collect.require_safe(spec, {"value": "runtime/reports/check.json"}, "采集结果")

    def test_sensitive_scan_aggregates_keys_values_and_bounds_display(self):
        from workflow import jira_collect
        spec = json.loads((ROOT / "projects/tapdata/admission.json").read_text())
        payload = {"password=first-secret": "Bearer fixture-token-123456",
                   "password=second-secret": ["password=third-secret", "/Users/fixture/report"]}
        before = json.dumps(payload)
        with self.assertRaises(ValueError) as error:
            jira_collect.require_safe(spec, payload, "采集结果")
        message = str(error.exception)
        self.assertIn("共 5 项", message)
        self.assertIn("<key#1>（键）", message)
        self.assertIn("<key#2>[1]", message)
        for secret in ("first-secret", "second-secret", "third-secret", "fixture-token", "/Users/fixture"):
            self.assertNotIn(secret, message)
        self.assertEqual(before, json.dumps(payload))
        for count in (50, 51):
            with self.subTest(count=count), self.assertRaises(ValueError) as error:
                jira_collect.require_safe(spec, ["password=hidden"] * count, "测试")
            self.assertIn("共 %d 项，展示 50 项，未展示 %d 项" % (count, count - 50), str(error.exception))
        with self.assertRaises(ValueError) as error:
            jira_collect.require_safe(spec, {"x" * 500: "password=hidden"}, "测试")
        self.assertNotIn("x" * 100, str(error.exception))

    def test_sensitive_scan_deduplicates_reasons_and_hides_token_keys(self):
        from workflow import jira_collect
        spec = json.loads((ROOT / "projects/tapdata/admission.json").read_text())
        key = "ghp_fixturetoken123456"
        payload = {key: "password=first\npassword=second", "other": "password=third"}
        with self.assertRaises(ValueError) as error:
            jira_collect.require_safe(spec, payload, "测试")
        self.assertIn("共 3 项", str(error.exception))
        self.assertNotIn(key, str(error.exception))
        del payload[key]
        with self.assertRaisesRegex(ValueError, "共 1 项"):
            jira_collect.require_safe(spec, payload, "测试")
        payload["other"] = "runtime/reports/check.json"
        jira_collect.require_safe(spec, payload, "测试")

    def test_sensitive_scan_whole_fallback_and_incomplete_coverage(self):
        from workflow import jira_collect
        spec = {"evidence_rules": {"forbidden_patterns": [
            {"pattern": r'"a": "b"', "reason": "跨结构规则"}]}}
        with self.assertRaisesRegex(ValueError, "整体检查，无法定位叶子"):
            jira_collect.require_safe(spec, {"a": "b"}, "测试")
        cyclic = []; cyclic.append(cyclic)
        with self.assertRaisesRegex(ValueError, "未完成，仅部分覆盖"):
            jira_collect.require_safe(spec, cyclic, "测试")

    def test_sensitive_collect_boundaries_and_recovery_are_readonly(self):
        from workflow import jira_collect
        snapshot = self.collect_fixture()
        original = jira_collect.collect(self.base, self.task, "design_review", snapshot)
        snapshot["extra"] = ["password=first-secret", "password=second-secret"]
        with mock.patch.object(jira_collect, "config") as config:
            with self.assertRaisesRegex(ValueError, "共 2 项.*生成结果尚未检查"):
                jira_collect.collect(self.base, self.task, "design_review", snapshot)
            config.assert_not_called()
        del snapshot["extra"]
        self.task["facts"]["fix_plan"] = "password=local-secret"
        save_station_task(self.base, self.task)
        before = task_state_snapshot(self.base)
        proof = {"actor": "fixture", "source": "user_message", "reference": "fixture:confirm",
                 "at": "2026-09-20T12:00:00+08:00"}
        actions = [
            lambda: jira_collect.collect(self.base, self.task, "design_review", snapshot),
            lambda: jira_collect.confirm(self.base, "TAP-123", self.task["run_id"],
                "design_review", snapshot, {}, {}, proof),
            lambda: jira_status.prepare(self.base, "TAP-123", "takeover", snapshot, "op-sensitive")]
        for action in actions:
            with self.assertRaisesRegex(ValueError, "采集结果含敏感内容") as error:
                action()
            message = str(error.exception)
            self.assertIn("suggestions", message)
            self.assertIn("dependencies", message)
            self.assertNotIn("local-secret", message)
            self.assertEqual(before, task_state_snapshot(self.base))
        self.task["facts"]["fix_plan"] = "confirmed-plan"
        save_station_task(self.base, self.task)
        self.assertEqual(original, jira_collect.collect(self.base, self.task, "design_review", snapshot))

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

    def test_takeover_existing_assignee_needs_no_duplicate_decision(self):
        for task_class in ('feature_change', 'defect_fix'):
            with self.subTest(task_class=task_class):
                self.task['task_class'] = task_class
                save_station_task(self.base, self.task)
                snapshot = self.feature_snapshot() if task_class == 'feature_change' else self.snapshot()
                snapshot['transitions'] = [snapshot['transitions'][-1]]
                snapshot['transitions'][0]['fields'] = {}
                for assignee, expected in (('other', 'assignee_mismatch'),
                                                              (None, 'assignee_mismatch'),
                                                              ('u-1', 'transition_prepared')):
                    snapshot['issue']['fields']['assignee'] = {'accountId': assignee}
                    result = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot,
                                                 operation_id='op-' + task_class.replace('_', '-'))
                    self.assertEqual(result['reason'], expected)
                    self.assertNotIn('assignee', result['decision_packet']['pending'])
                snapshot['issue']['fields']['status']['name'] = 'In Progress'
                done = jira_status.complete(self.base, 'TAP-123', 'takeover', 'unknown', snapshot, '',
                                            operation_id='op-' + task_class.replace('_', '-'))
                self.assertEqual(done['outcome'], 'succeeded')

    def test_native_required_assignee_still_requires_decision(self):
        snapshot = self.feature_snapshot()
        snapshot['transitions'] = [snapshot['transitions'][-1]]
        snapshot['transitions'][0]['fields'] = {'assignee': {
            'required': True, 'schema': {'type': 'user'}, 'name': 'Assignee'}}
        result = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot,
                                     operation_id='op-required-assignee')
        self.assertEqual(result['reason'], 'decision_inputs_pending')
        self.assertIn('assignee', result['decision_packet']['pending'])

    def test_old_unsent_assignee_preflight_retries_after_config_fix(self):
        snapshot = self.feature_snapshot()
        snapshot['transitions'] = [snapshot['transitions'][-1]]
        snapshot['transitions'][0]['fields'] = {}
        path = self.base / 'product/projects/tapdata/jira-transitions.json'
        current = path.read_text()
        previous = json.loads(current)
        previous['task_classes']['feature_change']['fields']['assignee']['collect_at'] = 'intake'
        path.write_text(json.dumps(previous))
        blocked = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot,
                                      operation_id='op-existing-intake')
        self.assertEqual(blocked['reason'], 'decision_inputs_pending')
        path.write_text(current)
        ready = jira_status.prepare(self.base, 'TAP-123', 'takeover', snapshot,
                                    operation_id='op-existing-intake')
        self.assertEqual(ready['outcome'], 'ready')
        self.assertEqual(ready['preflight_history'][-1]['reason'], 'decision_inputs_pending')

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
        from workflow import archive_store
        reservation = archive_store.reserve(self.base, self.task['issue_key'], self.task['run_id'])
        current = task_store.current_path(self.base)
        before = task_state_snapshot(self.base)
        first = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot())
        self.assertEqual(first["outcome"], "ready")
        self.assertEqual(first["transition_id"], "421")
        repeated = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot())
        self.assertTrue(repeated["repeated"])
        readback = self.snapshot(status="In Progress")
        done = jira_status.complete(self.base, "TAP-123", "takeover", "unknown", readback, "")
        self.assertEqual(done["outcome"], "succeeded")
        self.assertEqual(task_state_snapshot(self.base), before)
        self.assertEqual([], list(reservation.iterdir()))

    def test_jira_done_does_not_complete_local_task(self):
        current = task_store.current_path(self.base)
        before = task_state_snapshot(self.base)
        result = jira_status.prepare(self.base, "TAP-123", "takeover", self.snapshot(status="Done"))
        self.assertEqual(result["reason"], "jira_status_mismatch")
        self.assertEqual(task_state_snapshot(self.base), before)

    def test_native_pr_approved_hands_off_without_write_intent(self):
        snapshot = self.snapshot(status="Pull Request Submitted")
        snapshot["transitions"] = [{"id": "291", "name": "PR Approved", "to": {"name": "Merged"}, "fields": {}}]
        before = {str(p): task_state_snapshot(self.base) if p.resolve() == task_store.current_path(self.base) else p.read_bytes() for p in (self.base / ".agenticops").rglob("*") if p.is_file()}
        result = jira_status.prepare(self.base, "TAP-123", "native:291", snapshot)
        self.assertEqual(result["outcome"], "handoff")
        self.assertEqual(result["reason"], "human_only_transition")
        after = {str(p): task_state_snapshot(self.base) if p.resolve() == task_store.current_path(self.base) else p.read_bytes() for p in (self.base / ".agenticops").rglob("*") if p.is_file()}
        self.assertEqual(after, before)

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

    def test_collect_rejects_conflicting_option_identity_without_changing_confirmation(self):
        from workflow import jira_collect
        snapshot = self.collect_fixture()
        values = {'customfield_a': {'id': '1', 'value': 'ordinary'}}
        self.assertEqual(['customfield_a'], self.confirm_packet('design_review', snapshot, values)['confirmed'])
        before = {str(p): p.read_bytes() for p in (self.base / '.agenticops').rglob('*') if p.is_file()}
        for value in ({'id': 'wrong', 'value': 'ordinary'}, {'id': '1', 'value': 'wrong'}):
            with self.subTest(value=value):
                proposals = {'customfield_a': value}
                packet = jira_collect.collect(self.base, self.task, 'design_review', snapshot, proposals)
                self.assertFalse(packet['fields']['customfield_a']['valid_value'])
                with self.assertRaisesRegex(ValueError, '字段值'):
                    self.confirm_packet('design_review', snapshot, proposals)
                self.assertEqual(before, {str(p): p.read_bytes() for p in (self.base / '.agenticops').rglob('*') if p.is_file()})
        self.assertEqual(['customfield_a'], jira_collect.collect(self.base, self.task, 'design_review', snapshot)['confirmed'])

    def test_option_identity_must_match_one_allowed_choice_including_children(self):
        from workflow import jira_collect
        options = [{'id': '1', 'value': 'first', 'name': 'First',
                    'children': [{'id': '11', 'value': 'child'}]},
                   {'id': '2', 'value': 'second', 'name': 'Second'}]
        for value in ({'id': '1'}, {'value': 'first'}, {'name': 'First'},
                      {'id': '1', 'value': 'first', 'name': 'First'},
                      {'id': '1', 'child': {'id': '11', 'value': 'child'}}):
            with self.subTest(value=value):
                self.assertTrue(jira_collect.valid_value(value, {'type': 'option'}, options))
        for value in ({'id': '1', 'value': 'second'}, {'id': 'wrong', 'name': 'First'},
                      {'id': '1', 'name': 'Second'}, {'other': 'first'},
                      {'id': '1', 'child': {'id': 'wrong', 'value': 'child'}}):
            with self.subTest(value=value):
                self.assertFalse(jira_collect.valid_value(value, {'type': 'option-with-child'}, options))
                self.assertFalse(jira_collect.valid_value([{'id': '2'}, value], {'type': 'array'}, options))
        self.assertTrue(jira_collect.valid_value([{'id': '1'}, {'id': '2'}], {'type': 'array'}, options))

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
        self.assertTrue(result["preflight_ready"])
        input_path.write_text(json.dumps(linked_test("In Progress", "indeterminate")))
        with mock.patch.object(pr_ready, "quality_problems", return_value=[]), \
                mock.patch.object(pr_ready, "linked_test_confirmation_problems", return_value=[]), \
                mock.patch.object(pr_ready, "ci_problems", return_value=[]), \
                mock.patch.object(pr_ready.quality, "config", return_value=rules):
            result = pr_ready.check(self.base, "TAP-123", input_path)
        self.assertTrue(result["preflight_ready"])

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
        before = task_state_snapshot(self.base)
        done = jira_status.complete(self.base, "TAP-123", "takeover", "unknown", self.snapshot("In Progress"), "", operation_id)
        self.assertEqual(done["outcome"], "succeeded")
        self.assertEqual(done["operation_id"], record["operation_id"])
        self.assertEqual(before, task_state_snapshot(self.base))
        station_resources.verify_known_external(self.base, self.task)

    def test_defect_takeover_recovery_window_and_explicit_not_written(self):
        for stage in ("design_review", "implementation"):
            self.task["stage"] = stage
            save_station_task(self.base, self.task)
            record = self.prepare()
            self.assertEqual(record["outcome"], "ready")
        with self.assertRaisesRegex(ValueError, "未写入"):
            jira_status.complete(self.base, "TAP-123", "takeover", "not_written", self.snapshot(), "", "op-original-takeover")


class SubmissionClosureTests(unittest.TestCase):
    setUp = JiraStatusTests.setUp
    snapshot = JiraStatusTests.snapshot
    feature_snapshot = JiraStatusTests.feature_snapshot
    def seed_submission(self):
        from datetime import datetime, timezone
        self.task.update(stage='ci_validation', repositories=[
            {'repository': 'owner/first', 'pull_request': 42, 'authorized_endpoint': 'github.com/owner/first',
             'worktree': {'status': 'removed', 'final_revision': 'a' * 40}},
            {'repository': 'owner/second', 'pull_request': 43, 'authorized_endpoint': 'github.com/owner/second',
             'worktree': {'status': 'removed', 'final_revision': 'b' * 40}}])
        save_station_task(self.base, self.task)
        self.packet = {'schema_version': 1, 'issue_key': 'TAP-123', 'run_id': self.task['run_id'],
                       'checks_exceptions': [], 'pull_requests': []}
        self.observations = []
        for repo in self.task['repositories']:
            head = ('a' if repo['repository'] == 'owner/first' else 'b') * 40
            latest = {'verdict': 'success', 'head': head, 'checks': [{'name': 'build', 'conclusion': 'SUCCESS'}], 'failing': []}
            self.observations.append({'repository': repo['repository'], 'pr': str(repo['pull_request']), 'history': [latest]})
            self.packet['pull_requests'].append({'repository': repo['repository'], 'number': repo['pull_request'],
                'url': 'https://github.com/%s/pull/%s' % (repo['repository'], repo['pull_request']),
                'headRefOid': head, 'state': 'OPEN', 'isDraft': False, 'source_ref': 'fixture:github/readback',
                'observed_at': datetime.now(timezone.utc).isoformat()})
        self.input_path = task_store.interaction_path(self.base, 'TAP-123', self.task['run_id'], 'pr-ready-input.json', create=True)
        self.proof = {'actor': '用户', 'source': 'user_message', 'reference': 'fixture:accepted-checks-and-submit',
                      'at': datetime.now(timezone.utc).isoformat()}

    def packet_write(self):
        self.input_path.write_text(json.dumps(self.packet))
        return pr_ready.submission_input(self.base, self.task)

    def closure(self):
        snapshot = self.base / 'pr-jira.json'; snapshot.write_text(json.dumps(self.snapshot()))
        rules = {'task_classes': [], 'pr_ready': {'required_verification': []}}
        with mock.patch.object(pr_ready, 'local_head', side_effect=lambda r: ('a' if r['repository'] == 'owner/first' else 'b') * 40), \
             mock.patch.object(pr_ready.quality, 'config', return_value=rules), \
             mock.patch.object(jira_tests, 'acceptance_tests', return_value=([], [], [])), \
             mock.patch.object(pr_ready, 'linked_test_confirmation_problems', return_value=[]), \
             mock.patch.object(pr_ready, 'quality_problems', return_value=[]), \
             mock.patch.object(pr_ready.ci, 'current_states', return_value=self.observations):
            return pr_ready.check(self.base, 'TAP-123', snapshot)

    def test_preflight_never_claims_ready_without_readback(self):
        self.seed_submission()
        result = self.closure()
        self.assertTrue(result['preflight_ready']); self.assertFalse(result['ready'])
        self.assertEqual('pending', result['submission_status'])
        self.assertEqual(2, len(result['remaining']))
        self.packet_write()
        result = self.closure()
        self.assertTrue(result['ready']); self.assertEqual('confirmed', result['submission_status'])

    def test_multi_repository_partial_and_unknown_write_recovery(self):
        self.seed_submission()
        self.packet['pull_requests'][1]['isDraft'] = True
        self.packet_write(); result = self.closure()
        self.assertFalse(result['ready']); self.assertTrue(result['pull_requests'][0]['confirmed'])
        self.assertEqual(['owner/second'], [r['repository'] for r in result['remaining']])
        # 原生 ready 超时后不推定成功；只回读同一 PR，补齐后恢复。
        self.packet['pull_requests'][1]['isDraft'] = False
        self.packet_write(); self.assertTrue(self.closure()['ready'])

    def test_readback_identity_head_state_and_time_are_required(self):
        import copy
        self.seed_submission(); original = copy.deepcopy(self.packet)
        for field, bad in [('url', 'https://github.com/foreign/repo/pull/42'), ('headRefOid', 'c' * 40),
                           ('state', 'CLOSED'), ('isDraft', True), ('isDraft', 0),
                           ('source_ref', ''), ('observed_at', '2026-10-09T10:00:00')]:
            with self.subTest(field=field, bad=bad):
                self.packet = copy.deepcopy(original); self.packet['pull_requests'][0][field] = bad
                self.packet_write(); self.assertFalse(self.closure()['ready'])
        for field, bad in [('number', 999), ('repository', 'foreign/repo')]:
            self.packet = copy.deepcopy(original); self.packet['pull_requests'][0][field] = bad
            with self.assertRaises(ValueError): self.packet_write()
        self.packet = copy.deepcopy(original); self.packet['run_id'] = 'other-run'
        with self.assertRaises(ValueError): self.packet_write()

    def test_specific_checks_exception_preserves_original_and_rejects_drift(self):
        import copy
        self.seed_submission()
        latest = self.observations[0]['history'][-1]
        latest.update(verdict='skipped', checks=[{'name': 'deploy', 'conclusion': 'SKIPPED'}])
        before = copy.deepcopy(self.observations)
        self.packet_write(); self.assertFalse(self.closure()['preflight_ready'])
        exception = {'repository': 'owner/first', 'pr': 42, 'head': 'a' * 40,
            'checks_digest': pr_ready.checks_digest(latest), 'reason': '接受 deploy 跳过，使用已有应用测试提审',
            'decision': 'accept_checks_and_submit', 'proof': self.proof}
        self.packet['checks_exceptions'] = [exception]
        self.packet_write(); self.assertTrue(self.closure()['ready'])
        self.assertEqual(before, self.observations)
        for verdict in ('failure', 'start_timeout', 'none'):
            latest['verdict'] = verdict; exception['checks_digest'] = pr_ready.checks_digest(latest)
            self.packet_write(); self.assertTrue(self.closure()['ready'])
        latest['checks'].append({'name': 'new-failure', 'conclusion': 'FAILURE'})
        self.assertFalse(self.closure()['preflight_ready'])
        exception['checks_digest'] = pr_ready.checks_digest(latest); exception['head'] = 'c' * 40
        self.packet_write(); self.assertFalse(self.closure()['preflight_ready'])
        exception['head'] = 'a' * 40
        for verdict in ('pending', 'finish_timeout', 'unknown'):
            latest['verdict'] = verdict; exception['checks_digest'] = pr_ready.checks_digest(latest)
            self.packet_write(); self.assertFalse(self.closure()['preflight_ready'])
        exception['decision'] = 'risk-only'
        with self.assertRaises(ValueError): self.packet_write()
        # 完成预检的仓库子集复用同 run 全仓证据，不误拒其它仓库的回读。
        exception['decision'] = 'accept_checks_and_submit'; latest['verdict'] = 'skipped'
        exception['checks_digest'] = pr_ready.checks_digest(latest); self.packet_write()
        with mock.patch.object(pr_ready.ci, 'current_states', return_value=self.observations), \
             mock.patch.object(pr_ready, 'local_head', return_value='a' * 40):
            candidate = dict(self.task, repositories=self.task['repositories'][:1])
            self.assertEqual([], pr_ready.ci_problems(self.base, candidate))

    def test_story_review_collected_at_design_and_reused_at_acceptance(self):
        from workflow import jira_collect
        snapshot = self.feature_snapshot()
        self.task['facts']['implementation_plan'] = {'objective': '保持统计语义', 'changes': ['替换接口']}
        save_station_task(self.base, self.task)
        values = {'customfield_10413': {'id': 'approved', 'value': 'Approved'}, 'fixVersions': [{'id': 'develop'}]}
        snapshot['transitions'][0]['fields'] = {
            'customfield_10413': {'name': 'Story Test Design Review Result', 'schema': {'type': 'option'},
                                  'allowedValues': [values['customfield_10413']]},
            'fixVersions': {'schema': {'type': 'array'}, 'allowedValues': values['fixVersions']}}
        for transition in snapshot['transitions']:
            transition['fields'] = snapshot['transitions'][0]['fields']
        packet = jira_collect.collect(self.base, self.task, 'design_review', snapshot, values)
        self.assertIn('customfield_10413', packet['pending'])
        self.assertIn('保持统计语义', packet['fields']['customfield_10092']['suggestions'][0]['value'])
        jira_collect.confirm(self.base, 'TAP-123', self.task['run_id'], 'design_review', snapshot, values,
            {key: packet['fields'][key]['digest'] for key in values}, self.proof if hasattr(self, 'proof') else
            {'actor': '用户', 'source': 'user_message', 'reference': 'fixture:approved', 'at': '2026-10-09T10:00:00+08:00'})
        reused = jira_collect.collect(self.base, self.task, 'acceptance', snapshot)
        self.assertIn('customfield_10413', reused['confirmed'])
        self.task['facts']['implementation_plan']['objective'] = '范围改变'; save_station_task(self.base, self.task)
        self.assertIn('customfield_10413', jira_collect.collect(self.base, self.task, 'acceptance', snapshot)['pending'])


class AcceptedITTests(unittest.TestCase):
    def setUp(self):
        f = fixtures.TestCoverageClosureTests(); f.setUp()
        self.rules = json.loads((ROOT / 'projects/tapdata/quality.json').read_text())
        self.ctx, self.material = f.ctx, f.material
        self.ctx.update(issue_key='TAP-123', run_id='original-run')
        self.repo, self.row = f.repo, f.row
        self.model = {}
        verification.record(self.model, self.material, self.ctx, self.rules)
        local = copy.deepcopy(self.material); local['kind'] = 'local'
        verification.record(self.model, local, self.ctx, self.rules)
        self.report = {'checkpoints': {'q4-acceptance': {'reviewed': True, 'outcome': 'accept', 'due': ['it']}},
            'items': {'it': {'plan': {'method': 'unit', 'timing': 'after_fix', 'expected_result': 'PASS',
                                    'repository': f.repo, 'scope': f.row['scope'],
                                    'target_revision': f.head},
                             'decision_valid': True, 'decision': {'decision': {'outcome': 'accept'}}}}}
        self.doc = {'issue': {'key': 'TAP-123', 'fields': {'issuelinks': []}}}

    def accepted(self):
        return jira_tests.accepted_it(self.model, self.report, self.ctx, self.rules)

    def test_complete_it_acceptance_without_extra_test(self):
        self.assertTrue(self.accepted())
        self.assertTrue(jira_tests.linked_tests(self.doc, 'TAP-123', self.rules)[0])

    def test_confirmation_and_manual_scenarios_are_not_exempt(self):
        for field, value in [('reviewed', False), ('outcome', 'accept_risk'), ('due', [])]:
            with self.subTest(field=field):
                point = self.report['checkpoints']['q4-acceptance']; old = point[field]
                point[field] = value; self.assertFalse(self.accepted()); point[field] = old
        for field, value in [('timing', 'before_fix'), ('expected_result', 'FAIL')]:
            plan = self.report['items']['it']['plan']; old = plan[field]
            plan[field] = value; self.assertFalse(self.accepted()); plan[field] = old
        for method in ('manual', 'taptest'):
            self.report['items']['it']['plan']['method'] = method
            self.assertFalse(self.accepted())

    def test_changed_head_ci_plan_and_missing_material_fail_closed(self):
        for field, value in [('live_revision', 'b' * 40), ('ci_digest', 'different')]:
            old = self.ctx['repositories'][self.repo][field]
            self.ctx['repositories'][self.repo][field] = value
            self.assertFalse(self.accepted()); self.ctx['repositories'][self.repo][field] = old
        self.row['case_strategy'] = 'changed plan'; self.assertFalse(self.accepted())
        self.setUp(); del self.model['verification'][self.repo]['local']; self.assertFalse(self.accepted())

    def test_non_pass_zero_skips_and_unwritten_are_not_coverage(self):
        for kind in ('local', 'ci'):
            result = self.model['verification'][self.repo][kind]['data']['results'][0]
            for field, value in [('result', 'SKIPPED'), ('tests', 0), ('skipped', 1), ('errors', 1)]:
                with self.subTest(kind=kind, field=field):
                    old = result[field]; result[field] = value
                    self.assertFalse(self.accepted()); result[field] = old
            case = result['case_coverage']['cases'][0]; old = case['status']
            case['status'] = 'missing'; self.assertFalse(self.accepted()); case['status'] = old
        self.row.update(level='L3', framework_supported=False); self.assertFalse(self.accepted())

    def test_live_admission_preserves_existing_links_and_default_policy(self):
        from workflow import jira_status, quality
        task = {'issue_key': 'TAP-123'}
        with mock.patch.object(jira_status, 'config', return_value={}) as config, \
             mock.patch.object(quality, 'load', return_value={}), \
             mock.patch.object(quality, 'context', return_value=self.ctx), \
             mock.patch.object(quality, 'report', return_value=self.report), \
             mock.patch.object(quality, 'replay', return_value=self.model):
            self.assertTrue(jira_tests.acceptance_tests('.', task, self.doc, self.rules)[0])
            config.return_value = {'allow_accepted_it_without_test': True}
            self.assertEqual(([], [], []), jira_tests.acceptance_tests('.', task, self.doc, self.rules))
            for links in (None, [{}], [{'type': {'inward': 'is tested by'}, 'inwardIssue': {
                    'key': 'TAP-456', 'fields': {'issuetype': {'name': 'Test'}}}}]):
                self.doc['issue']['fields']['issuelinks'] = links
                self.assertTrue(jira_tests.acceptance_tests('.', task, self.doc, self.rules)[0])



if __name__ == "__main__":
    unittest.main(verbosity=2)
