import unittest
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

from internal import test_selection


class TestSelectionTests(unittest.TestCase):
    def test_python_environment_defaults_overrides_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(directory)
            internal = root / ".local/venv/internal/bin/python"
            internal.parent.mkdir(parents=True)
            internal.symlink_to(sys.executable)
            with mock.patch.object(test_selection.shutil, "which", return_value=sys.executable):
                self.assertEqual(test_selection.command_for(root, "story_gate")[0], str(internal))
                self.assertEqual(test_selection.command_for(root, "quality")[0], sys.executable)
                self.assertEqual(test_selection.command_for(root, "release"), ("bash", "internal/tests/test_release.sh"))
                internal.unlink()
                self.assertEqual(test_selection.command_for(root, "story_gate")[0], sys.executable)
            with mock.patch.dict(os.environ, {"AGENTIC_OPS_TEST_PYTHON": "custom-product",
                                              "AGENTIC_OPS_INTERNAL_TEST_PYTHON": sys.executable}):
                self.assertEqual(test_selection.command_for(root, "quality")[0], "custom-product")
                self.assertEqual(test_selection.command_for(root, "story_gate")[0], sys.executable)

    def test_preview_and_execution_share_resolved_commands(self):
        output = io.StringIO()
        root = Path.cwd().resolve()
        with mock.patch.object(sys, "argv", ["selector", "--source", "staged", "--run"]), \
                mock.patch.object(test_selection, "changes", return_value=["internal/story_gate/service.py"]), \
                mock.patch.object(test_selection.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                mock.patch.dict(os.environ, {"AGENTIC_OPS_INTERNAL_TEST_PYTHON": sys.executable, "PYTHONPATH": "previous"}), \
                contextlib.redirect_stdout(output):
            test_selection.main()
        preview = json.loads(output.getvalue().splitlines()[0])
        self.assertEqual(preview["commands"], [list(call.args[0]) for call in run.call_args_list])
        self.assertEqual(run.call_args_list[0].kwargs["env"]["PYTHONPATH"], str(root) + os.pathsep + "previous")
        self.assertTrue(all(call.kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for call in run.call_args_list))

    def test_failed_suite_stops_later_execution(self):
        with mock.patch.object(sys, "argv", ["selector", "--source", "staged", "--run"]), \
                mock.patch.object(test_selection, "changes", return_value=["internal/story_gate/service.py"]), \
                mock.patch.object(test_selection.subprocess, "run", return_value=subprocess.CompletedProcess([], 9)) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                test_selection.main()
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(run.call_count, 1)

    def test_station_resources_selects_its_direct_consumers_once(self):
        suites, unmapped = test_selection.select(["workflow/station_resources.py"])
        self.assertTrue({"station_clean", "station_resources", "station_lifecycle"}.issubset(suites))
        self.assertEqual(len(suites), len(set(suites)))
        self.assertEqual(unmapped, [])

    def test_station_operation_selects_resource_and_lifecycle_consumers(self):
        suites, unmapped = test_selection.select(["workflow/station_operation.py"])
        self.assertTrue({"station_clean", "station_resources", "station_lifecycle"}.issubset(suites))
        self.assertEqual(unmapped, [])

    def test_product_verification_does_not_select_internal_namesake(self):
        suites, unmapped = test_selection.select(["workflow/verification.py"])
        self.assertIn("quality", suites)
        self.assertNotIn("verification", suites)
        self.assertEqual(unmapped, [])
        internal, missing = test_selection.select(["internal/story_gate/evidence.py"])
        self.assertIn("verification", internal)
        self.assertEqual(missing, [])
        self.assertEqual(test_selection.select(["tests/test_verification.py"]), ([], ["tests/test_verification.py"]))

    def test_direct_selection_uses_command_path_even_with_suite_alias(self):
        suites = dict(test_selection.TEST_SUITES)
        suites["renamed_quality"] = suites.pop("quality")
        with mock.patch.object(test_selection, "TEST_SUITES", suites), mock.patch.object(test_selection, "RULES", ()):
            self.assertEqual(test_selection.select(["tests/test_quality.py"]), (["renamed_quality"], []))
            self.assertEqual(test_selection.select(["workflow/quality.py"]), (["renamed_quality"], []))
            self.assertEqual(test_selection.select(["tests/quality.py"]), ([], ["tests/quality.py"]))

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
