from __future__ import annotations

import tempfile
import unittest
import shutil
from pathlib import Path

from ao_maint.integration.real_e2e import (
    REQUIRED_CAPABILITIES,
    RealTaskToPrE2EPreflight,
)
from ao_maint.output import RuntimeErrorResult


class RealTaskToPrE2EPreflightTest(unittest.TestCase):
    def test_current_catalog_fails_closed_before_external_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = self._source_with_current_catalog(Path(temporary))
            preflight = RealTaskToPrE2EPreflight(source_root)
            preflight.prepare_config(
                agent_id="harsen-mini-test-bot",
                project_profile="tapdata",
                expected_confirmer="harsen",
            )
            result = preflight.run("TAP-12289")

        self.assertFalse(result["ready"])
        self.assertFalse(result["external_access_performed"])
        self.assertFalse(result["business_workspace_created"])
        self.assertFalse(result["credentials_read"])
        self.assertEqual(
            {
                "takeover_task",
                "git_commit",
                "git_push_task_branch",
                "github_pr_create",
            },
            {entry["id"] for entry in result["blocking_capabilities"]},
        )
        self.assertEqual("harsen-mini-test-bot", result["test_identity"]["agent_id"])
        self.assertEqual("harsen", result["test_identity"]["expected_confirmer"])
        self.assertEqual("tapdata", result["test_identity"]["project_profile"])
        self.assertEqual("task_to_pr_e2e_config", result["test_identity"]["source"])
        self.assertTrue(result["next_action"]["stop_workflow"])

    def test_missing_configuration_blocks_without_identity_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = self._source_with_current_catalog(Path(temporary))
            with self.assertRaises(RuntimeErrorResult) as captured:
                RealTaskToPrE2EPreflight(source_root).run("TAP-12289")
        self.assertEqual("e2e_configuration_missing", captured.exception.code)

    def test_configuration_is_created_once_and_contains_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source_root = self._source_with_current_catalog(Path(temporary))
            preflight = RealTaskToPrE2EPreflight(source_root)
            result = preflight.prepare_config(
                agent_id="harsen-mini-test-bot",
                project_profile="tapdata",
                expected_confirmer="harsen",
            )
            payload = Path(result["config_path"]).read_text(encoding="utf-8")
            self.assertNotIn("token", payload.lower())
            self.assertNotIn("email", payload.lower())
            with self.assertRaises(RuntimeErrorResult) as captured:
                preflight.prepare_config(
                    agent_id="another-agent",
                    project_profile="tapdata",
                    expected_confirmer="another-user",
                )
        self.assertEqual("e2e_configuration_exists", captured.exception.code)

    def test_ready_only_when_all_required_capabilities_are_implemented(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "developer" / "standards" / "capabilities"
            path.mkdir(parents=True)
            entries = "\n".join(
                f"  - id: {capability}\n"
                "    status: implemented\n"
                "    next_action: 继续执行\n"
                for capability in REQUIRED_CAPABILITIES
            )
            (path / "operations.yaml").write_text(
                "schema_version: '1'\nworkplane: developer\ncapabilities:\n" + entries,
                encoding="utf-8",
            )
            preflight = RealTaskToPrE2EPreflight(root)
            preflight.prepare_config(
                agent_id="harsen-mini-test-bot",
                project_profile="tapdata",
                expected_confirmer="harsen",
            )
            result = preflight.run("TAP-12289")
        self.assertTrue(result["ready"])
        self.assertEqual([], result["blocking_capabilities"])
        self.assertFalse(result["next_action"]["stop_workflow"])

    def _source_with_current_catalog(self, root: Path) -> Path:
        source_catalog = (
            Path(__file__).resolve().parents[3]
            / "developer/standards/capabilities/operations.yaml"
        )
        target = root / "developer/standards/capabilities/operations.yaml"
        target.parent.mkdir(parents=True)
        shutil.copyfile(source_catalog, target)
        return root


if __name__ == "__main__":
    unittest.main()
