import unittest

from internal import test_selection


class TestSelectionTests(unittest.TestCase):
    def test_station_resources_selects_its_direct_consumers_once(self):
        suites, unmapped = test_selection.select(["workflow/station_resources.py"])
        self.assertEqual(suites, ["station_clean", "station_resources", "station_lifecycle"])
        self.assertEqual(unmapped, [])

    def test_station_operation_selects_resource_and_lifecycle_consumers(self):
        self.assertEqual(test_selection.select(["workflow/station_operation.py"]), (["station_clean", "station_resources", "station_lifecycle"], []))

    def test_multiple_paths_are_deduplicated(self):
        suites, unmapped = test_selection.select(["workflow/quality.py", "workflow/jira_status.py"])
        self.assertEqual(suites, ["quality", "jira_status", "jira_watermark", "issue_versions", "workflow", "checkpoints", "failures", "task_identity", "station_state"])
        self.assertEqual(unmapped, [])

    def test_test_file_selects_itself(self):
        self.assertEqual(test_selection.select(["tests/test_quality.py"]), (["quality"], []))
        self.assertEqual(test_selection.select(["tests/test_repair_strategy.py"]), (["repair_strategy"], []))

    def test_unknown_path_fails_closed(self):
        self.assertEqual(test_selection.select(["new/unmapped.py"]), ([], ["new/unmapped.py"]))


if __name__ == "__main__":
    unittest.main()
