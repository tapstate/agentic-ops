"""真实类型分流、专用流程防降级及通用同步隔离。"""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import issue_routing, jira_status, project_rules, quality, station, verification
from test_station_lifecycle import StationTests


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.profile = project_rules.load_profile(root=ROOT, project="tapdata")

    def snapshot(self, kind="10008"):
        return {"site": self.profile["jira"]["site"], "source_ref": "jira:read/123",
                "issue": {"id": "123", "key": "TAP-123", "fields": {
                    "project": {"key": "TAP"}, "issuetype": {"id": kind, "name": "原生类型"}}}}

    def test_specialized_and_default_types(self):
        for kind, expected in (("10011", "defect_fix"), ("10010", "feature_change"),
                               ("10008", "general_task"), ("10009", "general_task"),
                               ("99999", "general_task")):
            self.assertEqual(expected, issue_routing.resolve(self.profile, "TAP-123", self.snapshot(kind))["task_class"])

    def test_missing_identity_never_means_default(self):
        for path in (("source_ref",), ("issue", "id"), ("issue", "fields", "issuetype"),
                     ("issue", "fields", "project")):
            snapshot = self.snapshot()
            parent = snapshot
            for key in path[:-1]:
                parent = parent[key]
            del parent[path[-1]]
            with self.assertRaises(ValueError):
                issue_routing.resolve(self.profile, "TAP-123", snapshot)
        snapshot = self.snapshot()
        snapshot["site"] = "https://other.invalid"
        with self.assertRaises(ValueError):
            issue_routing.resolve(self.profile, "TAP-123", snapshot)

    def test_invalid_specialized_mapping_never_falls_back(self):
        self.profile["jira"]["routing"]["by_issue_type_id"]["10010"] = None
        with self.assertRaisesRegex(ValueError, "禁止降级"):
            issue_routing.resolve(self.profile, "TAP-123", self.snapshot("10010"))

    def task(self, kind="10008"):
        binding = issue_routing.resolve(self.profile, "TAP-123", self.snapshot(kind))
        return {"task_class": binding["task_class"], "facts": {"jira_routing": binding}}

    def test_unknown_type_markers_require_bound_run(self):
        self.assertTrue(issue_routing.marker_type_allowed(self.profile, self.task("99999"), "99999"))
        self.assertFalse(issue_routing.marker_type_allowed(self.profile, self.task(), "99999"))

    def test_type_change_cannot_silently_change_run(self):
        with self.assertRaisesRegex(ValueError, "不能静默"):
            issue_routing.check_task(self.profile, self.task(), self.snapshot("10010")["issue"]["fields"])

    def test_unchanged_sources_never_exempt_local_review_or_specialized_checks(self):
        repo = {"base_sha": "a" * 40, "live_revision": "a" * 40}
        ctx = {"repositories": {"repo": repo}}
        kinds = ["local", "source_sync", "ci", "review"]
        rules = {"change_only_verification": ["source_sync", "ci"]}
        self.assertEqual(2, len(verification.problems({}, ctx, kinds, rules)))
        self.assertEqual(4, len(verification.problems({}, ctx, kinds, {})))
        for revision in ("b" * 40, "a" * 40 + ":worktree:dirty", "unavailable", None):
            repo["live_revision"] = revision
            self.assertEqual(4, len(verification.problems({}, ctx, kinds, rules)))
        self.assertFalse(verification.unchanged_repository({}))

    def test_default_has_no_automatic_transitions_and_explicit_mapping_is_scoped(self):
        with mock.patch.object(project_rules, "load_profile", return_value=self.profile):
            self.assertEqual({}, jira_status.config("unused", self.task())["attempts"])
            self.profile["jira"]["status_sync"]["by_issue_type_id"] = {
                "10008": {"issue_type_ids": ["10008"], "attempts": {"takeover": {"transition_id": "61", "from": ["待办"], "to": "正在进行", "local_stages": ["task_intake"]}}}}
            self.assertIn("takeover", jira_status.config("unused", self.task())["attempts"])
            self.assertEqual({}, jira_status.config("unused", self.task("99999"))["attempts"])
            self.assertEqual(["10010"], jira_status.config("unused", self.task("10010"))["issue_type_ids"])


class TakeoverRoutingTests(StationTests):
    # 仅新增分流场景；父类的旧合同回归由原测试入口执行。
    def test_new_takeover_checks_type_before_source_or_operation(self):
        self.prepare_engineering()
        profile_path = self.product / "projects/tapdata/profile.json"
        profile = json.loads(profile_path.read_text())
        profile["jira"]["routing"] = project_rules.load_profile(ROOT, "tapdata")["jira"]["routing"]
        self.write(profile_path, profile)
        snapshot = RoutingTests()
        snapshot.setUp()
        self.request["jira_snapshot"] = snapshot.snapshot("10010")
        self.request["task_class"] = "general_task"
        with self.assertRaisesRegex(ValueError, "feature_change"):
            station.takeover(self.ws, self.request, "op-routing-test", 0)
        self.assertFalse((self.ws / ".agenticops/operation.json").exists())
        self.request["jira_snapshot"] = snapshot.snapshot()
        station.takeover(self.ws, self.request, "op-routing-test", 0)
        from workflow import task_store
        task = task_store.read_task(self.ws)
        self.assertEqual("general_task", task["task_class"])
        self.assertTrue(quality.enabled(task, quality.config(self.ws, task)))
        self.assertTrue(task["source_prepared"])
        original = json.loads((ROOT / "projects/tapdata/quality-feature.json").read_text())
        feature = quality.config(self.ws, dict(task, task_class="feature_change"))
        feature["jira"].pop("site")
        self.assertEqual(original, feature, "通用配置不改变故事规则或其确认摘要")
        general = quality.config(self.ws, task)
        self.assertEqual(original["verification_checkpoints"], general["verification_checkpoints"])
        self.assertEqual(original["stage_checkpoints"], general["stage_checkpoints"])


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RoutingTests)
    suite.addTest(TakeoverRoutingTests("test_new_takeover_checks_type_before_source_or_operation"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(not result.wasSuccessful())
