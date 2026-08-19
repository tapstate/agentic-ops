from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ao_work.node_runtime import (
    get_node,
    load_node_registry,
    node_registry_path,
    resolve_node_exit,
)
from ao_work.output import RuntimeErrorResult

_SAMPLE_REGISTRY = """\
schema_version: 1
workplane: developer
nodes:
  - id: task_intake
    name: 任务准入
    description: 任务准入
    admission:
      - jira_issue_facts
    exit:
      next_node: solution_classification
  - id: solution_classification
    name: 方案分级
    description: 方案分级
    admission:
      - confirmed_intake_digest
    exit:
      next_node: waiting_takeover
  - id: waiting_takeover
    name: 等待接管
    description: 等待接管
    admission:
      - assignee
    exit:
      next_node: implementation
      jira_transition: start_progress
  - id: implementation
    name: 实现与验证
    description: 实现与验证
    admission:
      - task_taken_over
    exit:
      next_node: pr_review
  - id: pr_review
    name: PR 审查
    description: PR 审查
    admission:
      - pr_url
    exit:
      next_node: completed
      jira_transition: complete
  - id: completed
    name: 完成
    description: 完成
    admission:
      - agentic_completion_evidence
    exit:
      next_node: null
"""

_SAMPLE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "workplane", "nodes"],
    "properties": {
        "schema_version": {"type": "integer"},
        "workplane": {"const": "developer"},
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "name", "description", "admission", "exit"],
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "admission": {"type": "array", "items": {"type": "string"}},
                    "exit": {
                        "type": "object",
                        "required": ["next_node"],
                        "properties": {
                            "next_node": {
                                "anyOf": [
                                    {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                                    {"type": "null"},
                                ]
                            },
                            "jira_transition": {"type": "string"},
                        },
                    },
                },
            },
        },
    },
}


class NodeRuntimeTest(unittest.TestCase):
    def _install_root(self, registry: str = _SAMPLE_REGISTRY) -> Path:
        import shutil

        root = Path(tempfile.mkdtemp(prefix="node-runtime-test-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        nodes_dir = root / "developer" / "standards" / "nodes"
        nodes_dir.mkdir(parents=True)
        (nodes_dir / "registry.yaml").write_text(registry, encoding="utf-8")
        (nodes_dir / "nodes.schema.json").write_text(
            json.dumps(_SAMPLE_SCHEMA, ensure_ascii=False), encoding="utf-8"
        )
        return root

    def test_load_registry_valid(self) -> None:
        root = self._install_root()
        registry = load_node_registry(root)
        self.assertEqual(1, registry["schema_version"])
        self.assertEqual("developer", registry["workplane"])
        self.assertEqual(6, len(registry["nodes"]))

    def test_load_registry_missing_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_node_registry(root)
            self.assertEqual("node_registry_missing", captured.exception.code)

    def test_load_registry_duplicate_id_blocks(self) -> None:
        duplicate = _SAMPLE_REGISTRY.replace(
            "- id: implementation\n", "- id: task_intake\n", 1
        )
        root = self._install_root(duplicate)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_node_registry(root)
        self.assertEqual("node_registry_duplicate_id", captured.exception.code)

    def test_load_registry_bad_next_node_blocks(self) -> None:
        broken = _SAMPLE_REGISTRY.replace(
            "next_node: solution_classification",
            "next_node: does_not_exist",
            1,
        )
        root = self._install_root(broken)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_node_registry(root)
        self.assertEqual("node_registry_next_node_unknown", captured.exception.code)

    def test_load_registry_schema_invalid_blocks(self) -> None:
        # 缺少 admission 字段违反 schema（minimal 兜底只查 id/name/exit）
        bad = _SAMPLE_REGISTRY.replace(
            "    admission:\n      - jira_issue_facts\n", "", 1
        )
        root = self._install_root(bad)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_node_registry(root)
        self.assertEqual("node_registry_schema_invalid", captured.exception.code)

    def test_get_node_unknown_blocks(self) -> None:
        root = self._install_root()
        registry = load_node_registry(root)
        with self.assertRaises(RuntimeErrorResult) as captured:
            get_node(registry, "no_such_node")
        self.assertEqual("node_unknown", captured.exception.code)

    def test_resolve_node_exit_with_jira_transition(self) -> None:
        root = self._install_root()
        registry = load_node_registry(root)
        result = resolve_node_exit(
            registry,
            "waiting_takeover",
            {"start_progress": {"name": "In Progress", "id": "31"}},
        )
        self.assertEqual("implementation", result["next_node"])
        self.assertEqual("start_progress", result["jira_transition"])
        self.assertTrue(result["jira_mapping_valid"])
        self.assertEqual("31", result["mapping_detail"]["configured"]["id"])

    def test_resolve_node_exit_without_jira_transition(self) -> None:
        root = self._install_root()
        registry = load_node_registry(root)
        result = resolve_node_exit(
            registry,
            "implementation",
            {"start_progress": {"name": "In Progress", "id": "31"}},
        )
        self.assertEqual("pr_review", result["next_node"])
        self.assertIsNone(result["jira_transition"])
        self.assertTrue(result["jira_mapping_valid"])

    def test_resolve_node_exit_terminal(self) -> None:
        root = self._install_root()
        registry = load_node_registry(root)
        result = resolve_node_exit(registry, "completed", {})
        self.assertIsNone(result["next_node"])
        self.assertIsNone(result["jira_transition"])

    def test_resolve_node_exit_transition_not_configured(self) -> None:
        root = self._install_root()
        registry = load_node_registry(root)
        result = resolve_node_exit(
            registry,
            "waiting_takeover",
            {"some_other_transition": {}},
        )
        self.assertFalse(result["jira_mapping_valid"])
        self.assertIn("未配置", result["mapping_detail"]["reason"])

    def test_node_registry_path_shape(self) -> None:
        root = self._install_root()
        path = node_registry_path(root)
        self.assertTrue(path.name == "registry.yaml")
        self.assertTrue(path.is_file())
