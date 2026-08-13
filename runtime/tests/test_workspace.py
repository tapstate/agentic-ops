from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentic_ops.output import RuntimeErrorResult
from agentic_ops.workspace import PROJECT_EXECUTION, SOURCE_MAINTENANCE, resolve_workspace


class WorkspaceTest(unittest.TestCase):
    def test_detects_source_maintenance_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "docs" / "strategy" / "project-goals.md"
            marker.parent.mkdir(parents=True)
            marker.write_text("# 目标\n", encoding="utf-8")
            workspace = resolve_workspace(str(root), SOURCE_MAINTENANCE)
            self.assertEqual(SOURCE_MAINTENANCE, workspace.mode)

    def test_detects_project_execution_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"mode": PROJECT_EXECUTION}), encoding="utf-8")
            workspace = resolve_workspace(str(root), PROJECT_EXECUTION)
            self.assertEqual(PROJECT_EXECUTION, workspace.mode)

    def test_blocks_cross_mode_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"mode": PROJECT_EXECUTION}), encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_workspace(str(root), SOURCE_MAINTENANCE)
            self.assertEqual("workspace_mode_mismatch", captured.exception.code)

    def test_source_repo_cannot_claim_project_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / ".agentic-ops" / "agent.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({"mode": PROJECT_EXECUTION}), encoding="utf-8")
            marker = root / "docs" / "strategy" / "project-goals.md"
            marker.parent.mkdir(parents=True)
            marker.write_text("# 目标\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                resolve_workspace(str(root), PROJECT_EXECUTION)
            self.assertEqual("workspace_mode_mismatch", captured.exception.code)
