from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ao_work.output import RuntimeErrorResult
from ao_work.stage_runtime import (
    advance_stage,
    get_stage,
    get_stage_steps,
    get_step,
    load_stage_registry,
    stage_registry_path,
    validate_admission,
)

_SAMPLE_REGISTRY = """\
schema_version: 2
workplane: developer
stages:
  - id: task_intake
    name: 任务准入
    description: 任务准入
    admission:
      - jira_issue_facts
    exit:
      next_stage: solution_classification
  - id: solution_classification
    name: 方案分级
    description: 方案分级
    admission:
      - confirmed_intake_digest
    exit:
      next_stage: waiting_takeover
  - id: waiting_takeover
    name: 等待接管
    description: 等待接管
    admission:
      - assignee
    exit:
      next_stage: implementation
      jira_transition: start_progress
  - id: implementation
    name: 实现与验证
    description: 实现与验证
    admission:
      - task_taken_over
    exit:
      next_stage: pr_review
    steps:
      - id: code_writing
        name: 编写代码
        description: 编写代码
        exit:
          next_step: test_writing
      - id: test_writing
        name: 编写测试
        description: 编写测试
        exit:
          next_step: verification
      - id: verification
        name: 验证
        description: 验证
        exit:
          next_step: null
  - id: pr_review
    name: PR 审查
    description: PR 审查
    admission:
      - pr_url
    exit:
      next_stage: completed
      jira_transition: complete
  - id: completed
    name: 完成
    description: 完成
    admission:
      - agentic_completion_evidence
    exit:
      next_stage: null
"""

_SAMPLE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "workplane", "stages"],
    "properties": {
        "schema_version": {"type": "integer"},
        "workplane": {"const": "developer"},
        "stages": {
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
                        "required": ["next_stage"],
                        "properties": {
                            "next_stage": {
                                "anyOf": [
                                    {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                                    {"type": "null"},
                                ]
                            },
                            "jira_transition": {"type": "string"},
                        },
                    },
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["id", "name", "description", "exit"],
                            "properties": {
                                "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "exit": {
                                    "type": "object",
                                    "required": ["next_step"],
                                    "properties": {
                                        "next_step": {
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
            },
        },
    },
}


class StageRuntimeTest(unittest.TestCase):
    def _install_root(self, registry: str = _SAMPLE_REGISTRY) -> Path:
        import shutil

        root = Path(tempfile.mkdtemp(prefix="stage-runtime-test-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        stages_dir = root / "developer" / "standards" / "stages"
        stages_dir.mkdir(parents=True)
        (stages_dir / "stages.yaml").write_text(registry, encoding="utf-8")
        (stages_dir / "stages.schema.json").write_text(
            json.dumps(_SAMPLE_SCHEMA, ensure_ascii=False), encoding="utf-8"
        )
        return root

    def test_load_registry_valid(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        self.assertEqual(2, registry["schema_version"])
        self.assertEqual("developer", registry["workplane"])
        self.assertEqual(6, len(registry["stages"]))

    def test_load_registry_missing_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_stage_registry(root)
            self.assertEqual("stage_registry_missing", captured.exception.code)

    def test_load_registry_duplicate_id_blocks(self) -> None:
        duplicate = _SAMPLE_REGISTRY.replace(
            "- id: implementation\n", "- id: task_intake\n", 1
        )
        root = self._install_root(duplicate)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_stage_registry(root)
        self.assertEqual("stage_registry_duplicate_id", captured.exception.code)

    def test_load_registry_bad_next_stage_blocks(self) -> None:
        broken = _SAMPLE_REGISTRY.replace(
            "next_stage: solution_classification",
            "next_stage: does_not_exist",
            1,
        )
        root = self._install_root(broken)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_stage_registry(root)
        self.assertEqual("stage_registry_next_stage_unknown", captured.exception.code)

    def test_load_registry_bad_next_step_blocks(self) -> None:
        broken = _SAMPLE_REGISTRY.replace(
            "next_step: test_writing",
            "next_step: does_not_exist",
            1,
        )
        root = self._install_root(broken)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_stage_registry(root)
        self.assertEqual("stage_registry_next_step_unknown", captured.exception.code)

    def test_load_registry_schema_invalid_blocks(self) -> None:
        bad = _SAMPLE_REGISTRY.replace(
            "    admission:\n      - jira_issue_facts\n", "", 1
        )
        root = self._install_root(bad)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_stage_registry(root)
        self.assertEqual("stage_registry_schema_invalid", captured.exception.code)

    def test_get_stage_unknown_blocks(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        with self.assertRaises(RuntimeErrorResult) as captured:
            get_stage(registry, "no_such_stage")
        self.assertEqual("stage_unknown", captured.exception.code)

    def test_resolve_stage_exit_with_jira_transition(self) -> None:
        from ao_work.stage_runtime import resolve_stage_exit

        root = self._install_root()
        registry = load_stage_registry(root)
        result = resolve_stage_exit(
            registry,
            "waiting_takeover",
            {"start_progress": {"name": "In Progress", "id": "31"}},
        )
        self.assertEqual("implementation", result["next_stage"])
        self.assertEqual("start_progress", result["jira_transition"])
        self.assertTrue(result["jira_mapping_valid"])
        self.assertEqual("31", result["mapping_detail"]["configured"]["id"])

    def test_resolve_stage_exit_without_jira_transition(self) -> None:
        from ao_work.stage_runtime import resolve_stage_exit

        root = self._install_root()
        registry = load_stage_registry(root)
        result = resolve_stage_exit(
            registry,
            "implementation",
            {"start_progress": {"name": "In Progress", "id": "31"}},
        )
        self.assertEqual("pr_review", result["next_stage"])
        self.assertIsNone(result["jira_transition"])
        self.assertTrue(result["jira_mapping_valid"])

    def test_resolve_stage_exit_terminal(self) -> None:
        from ao_work.stage_runtime import resolve_stage_exit

        root = self._install_root()
        registry = load_stage_registry(root)
        result = resolve_stage_exit(registry, "completed", {})
        self.assertIsNone(result["next_stage"])
        self.assertIsNone(result["jira_transition"])

    def test_resolve_stage_exit_transition_not_configured(self) -> None:
        from ao_work.stage_runtime import resolve_stage_exit

        root = self._install_root()
        registry = load_stage_registry(root)
        result = resolve_stage_exit(
            registry,
            "waiting_takeover",
            {"some_other_transition": {}},
        )
        self.assertFalse(result["jira_mapping_valid"])
        self.assertIn("未配置", result["mapping_detail"]["reason"])

    def test_stage_registry_path_shape(self) -> None:
        root = self._install_root()
        path = stage_registry_path(root)
        self.assertTrue(path.name == "stages.yaml")
        self.assertTrue(path.is_file())

    def test_get_stage_steps(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        steps = get_stage_steps(registry, "implementation")
        self.assertEqual(
            ["code_writing", "test_writing", "verification"],
            [s["id"] for s in steps],
        )
        self.assertEqual([], get_stage_steps(registry, "waiting_takeover"))

    def test_get_step_unknown_blocks(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        with self.assertRaises(RuntimeErrorResult) as captured:
            get_step(registry, "implementation", "no_such_step")
        self.assertEqual("stage_step_unknown", captured.exception.code)

    def test_validate_admission(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        self.assertEqual([], validate_admission(registry, "waiting_takeover", {"assignee": "u"}))
        self.assertEqual(["assignee"], validate_admission(registry, "waiting_takeover", {}))

    def test_advance_stage_with_jira_transition(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        result = advance_stage(
            registry,
            "waiting_takeover",
            {"start_progress": {"name": "In Progress", "id": "31"}},
            available={"assignee": "u"},
        )
        self.assertTrue(result["admission_ok"])
        self.assertEqual("implementation", result["next_stage"])
        self.assertEqual("start_progress", result["jira_transition"])
        self.assertTrue(result["jira_mapping_valid"])
        self.assertFalse(result["terminal"])

    def test_advance_stage_admission_blocked(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        with self.assertRaises(RuntimeErrorResult) as captured:
            advance_stage(
                registry,
                "waiting_takeover",
                {"start_progress": {"name": "In Progress", "id": "31"}},
                available={},
            )
        self.assertEqual("stage_admission_not_met", captured.exception.code)

    def test_advance_stage_step_progression(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        result = advance_stage(
            registry,
            "implementation",
            {},
            current_step="code_writing",
            available={"task_taken_over": True},
        )
        self.assertEqual("test_writing", result["next_step"])
        self.assertIsNone(result["jira_transition"])
        self.assertFalse(result["terminal"])

    def test_advance_stage_step_terminal_returns_stage_exit(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        result = advance_stage(
            registry,
            "implementation",
            {},
            current_step="verification",
            available={"task_taken_over": True},
        )
        self.assertEqual("pr_review", result["next_stage"])
        self.assertIsNone(result["jira_transition"])

    def test_advance_stage_terminal(self) -> None:
        root = self._install_root()
        registry = load_stage_registry(root)
        result = advance_stage(
            registry,
            "completed",
            {},
            available={"agentic_completion_evidence": "e"},
        )
        self.assertIsNone(result["next_stage"])
        self.assertTrue(result["terminal"])
