import unittest
from unittest import mock

from internal import test_selection


class TestSelectionTests(unittest.TestCase):
    def test_station_resources_selects_its_direct_consumers_once(self):
        suites, unmapped = test_selection.select(["workflow/station_resources.py"])
        self.assertTrue({"station_clean", "station_resources", "station_lifecycle"}.issubset(suites))
        self.assertEqual(len(suites), len(set(suites)))
        self.assertEqual(unmapped, [])

    def test_station_operation_selects_resource_and_lifecycle_consumers(self):
        suites, unmapped = test_selection.select(["workflow/station_operation.py"])
        self.assertTrue({"station_clean", "station_resources", "station_lifecycle"}.issubset(suites))
        self.assertEqual(unmapped, [])

    def test_multiple_paths_are_deduplicated(self):
        suites, unmapped = test_selection.select(["workflow/quality.py", "workflow/jira_status.py"])
        self.assertEqual(suites, ["quality", "jira_status", "jira_watermark", "issue_versions", "workflow", "checkpoints", "failures", "task_identity", "station_state"])
        self.assertEqual(unmapped, [])

    def test_test_file_selects_itself(self):
        self.assertEqual(test_selection.select(["tests/test_quality.py"]), (["quality"], []))
        self.assertEqual(test_selection.select(["tests/test_repair_strategy.py"]), (["repair_strategy"], []))

    def test_broad_prefix_does_not_hide_source_consumers(self):
        paths = ["workflow/station_source.py"]
        suites, unmapped = test_selection.select(paths)
        self.assertTrue({"station_source", "repository_recovery", "station_lifecycle",
                         "station_clean", "station_resources"}.issubset(suites))
        self.assertEqual(unmapped, [])
        with mock.patch.object(test_selection, "RULES", tuple(reversed(test_selection.RULES))):
            reordered, _ = test_selection.select(paths)
        self.assertEqual(set(suites), set(reordered))
        self.assertEqual(len(suites), len(set(suites)))

    def test_known_workflow_module_includes_direct_tests(self):
        for name in ("git_refs", "repair_strategy", "jira_status", "shared_repositories"):
            with self.subTest(module=name):
                suites, unmapped = test_selection.select(["workflow/" + name + ".py"])
                self.assertIn(name, suites)
                self.assertIn("workflow", suites)
                self.assertEqual(unmapped, [])

    def test_all_existing_prefix_coverage_is_preserved(self):
        for prefix, required in test_selection.RULES:
            with self.subTest(prefix=prefix):
                suites, unmapped = test_selection.select([prefix])
                self.assertTrue(set(required).issubset(suites))
                self.assertEqual(unmapped, [])

    def test_unknown_path_fails_closed(self):
        self.assertEqual(test_selection.select(["new/unmapped.py"]), ([], ["new/unmapped.py"]))


if __name__ == "__main__":
    unittest.main()
