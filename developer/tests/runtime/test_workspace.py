from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ao_work.output import RuntimeErrorResult
from ao_work.workspace import (
    DEVELOPER,
    resolve_developer_workspace,
)


class WorkspaceTest(unittest.TestCase):
    def test_developer_requires_explicit_workplane(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"workplane": DEVELOPER}), encoding="utf-8")
            workspace = resolve_developer_workspace(str(root))
            self.assertEqual(DEVELOPER, workspace.workplane)

    def test_legacy_mode_requires_explicit_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"mode":"project_execution"}\n', encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(root))
            self.assertEqual("workspace_schema_upgrade_required", captured.exception.code)

    def test_source_repo_cannot_be_developer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            marker = root / ".agentic-ops-source"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            marker.write_text("maintainer\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(root))
            self.assertEqual("workplane_mismatch", captured.exception.code)

    def test_source_repo_descendant_cannot_be_developer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            (source / ".agentic-ops-source").write_text("maintainer\n", encoding="utf-8")
            workspace_root = source / "nested" / "workspace"
            config = workspace_root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(workspace_root))
            self.assertEqual("workplane_mismatch", captured.exception.code)

    def test_maintainer_entry_blocks_developer_even_when_marker_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            entry = source / "maintainer" / "AGENTS.md"
            entry.parent.mkdir(parents=True)
            entry.write_text("# 维护入口\n", encoding="utf-8")
            workspace_root = source / "nested" / "workspace"
            config = workspace_root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")

            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(workspace_root))
            self.assertEqual("workplane_mismatch", captured.exception.code)

    def test_broken_source_marker_blocks_developer_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text('{"workplane":"developer"}\n', encoding="utf-8")
            (root / ".agentic-ops-source").symlink_to(root / "missing-marker")

            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(root))
            self.assertEqual("workplane_mismatch", captured.exception.code)

    def test_configured_source_root_cannot_contain_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            workspace_root = source / "ai-workspace"
            config = workspace_root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "workplane": "developer",
                        "source_root": str(source),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(workspace_root))
            self.assertEqual(
                "workspace_source_boundary_invalid", captured.exception.code
            )

    def test_workspace_state_directory_cannot_be_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_root = root / "workspace"
            external_state = root / "external-state"
            workspace_root.mkdir()
            external_state.mkdir()
            (external_state / "agent.json").write_text(
                '{"workplane":"developer"}\n', encoding="utf-8"
            )
            (workspace_root / ".agentic-ops").symlink_to(external_state, target_is_directory=True)

            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(workspace_root))
            self.assertEqual(
                "workspace_state_symlink_forbidden", captured.exception.code
            )

    def test_agent_config_cannot_symlink_to_another_workspace_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_a = root / "workspace-a"
            workspace_b = root / "workspace-b"
            state_a = workspace_a / ".agentic-ops"
            state_b = workspace_b / ".agentic-ops"
            state_a.mkdir(parents=True)
            state_b.mkdir(parents=True)
            config_b = state_b / "agent.json"
            config_b.write_text('{"workplane":"developer","agent_id":"b"}\n', encoding="utf-8")
            (state_a / "agent.json").symlink_to(config_b)

            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(workspace_a))
            self.assertEqual("workspace_managed_path_unsafe", captured.exception.code)

    def test_all_managed_top_level_paths_reject_directory_symlinks(self) -> None:
        for name in (
            "profiles",
            "connections",
            "runs",
            "audit",
            "feedback",
            "handoff",
            "locks",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                state = workspace / ".agentic-ops"
                external = root / "external"
                state.mkdir(parents=True)
                external.mkdir()
                (state / "agent.json").write_text('{"workplane":"developer"}\n', encoding="utf-8")
                (state / name).symlink_to(external, target_is_directory=True)
                with self.assertRaises(RuntimeErrorResult) as captured:
                    resolve_developer_workspace(str(workspace))
                self.assertEqual("workspace_managed_path_unsafe", captured.exception.code)

    def test_workspace_inspect_blocks_configured_agenticops_source_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace_root = root / "workspace"
            source_root = root / "business-source"
            config = workspace_root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            source_root.mkdir()
            (source_root / ".agentic-ops-source").write_text(
                "maintainer\n", encoding="utf-8"
            )
            config.write_text(
                json.dumps(
                    {
                        "workplane": "developer",
                        "source_root": str(source_root),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_developer_workspace(str(workspace_root))
            self.assertEqual("workplane_mismatch", captured.exception.code)

if __name__ == "__main__":
    unittest.main()
