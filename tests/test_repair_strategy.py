#!/usr/bin/env python3
"""AO-137 非阻断缺陷修复策略回归。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import repair_strategy, task, task_store


CATALOG = {
    "schema_version": 1,
    "kind": "planning_tuning",
    "enforcement": "advisory",
    "task_class": "defect_fix",
    "default": "minimal_sufficient",
    "strategies": [
        {"id": "minimal_sufficient", "label": "最小充分修复", "description": "默认",
         "guidance": ["限制无关扩散"]},
        {"id": "context_driven", "label": "根据问题选择方案", "description": "按上下文",
         "guidance": ["按根因决定范围"]},
    ],
}


class RepairStrategyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "product"
        self.workspace = base / "workspace"
        (self.root / "policies").mkdir(parents=True)
        (self.root / "projects" / "demo").mkdir(parents=True)
        (self.workspace / ".agenticops").mkdir(parents=True)
        self.catalog_path = self.root / repair_strategy.CATALOG_PATH
        self.catalog_path.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        (self.workspace / ".agenticops" / "workspace.json").write_text(json.dumps({
            "schema_version": 1, "product_root": str(self.root), "project": "demo", "agents": []
        }), encoding="utf-8")
        self.issue = "DEMO-1"
        self.run_id = "run-0123456789ab"
        self.state = {
            "issue_key": self.issue, "run_id": self.run_id, "task_class": "defect_fix",
            "stage": "task_intake", "facts": {}, "repositories": [], "pending": None, "history": [],
        }
        task_store._write_json_atomic(task_store.task_path(self.workspace, self.issue), self.state)
        task_store.register(self.workspace, self.issue, status="active")

    def tearDown(self):
        self.temp.cleanup()

    def read_state(self):
        return json.loads(task_store.task_path(self.workspace, self.issue).read_text(encoding="utf-8"))

    def args(self, **values):
        base = {"dir": str(self.workspace), "issue_key": self.issue,
                "expected_run_id": self.run_id, "id": "context_driven", "note": "用户选择"}
        base.update(values)
        return SimpleNamespace(**base)

    def test_default_and_non_defect_scope(self):
        resolved = repair_strategy.resolve(self.workspace, self.state)
        self.assertTrue(resolved["applicable"])
        self.assertTrue(resolved["available"])
        self.assertEqual(resolved["effective"], {
            "id": "minimal_sufficient", "label": "最小充分修复", "source": "company_default"
        })
        other = dict(self.state, task_class="feature_change")
        self.assertEqual(repair_strategy.resolve(self.workspace, other), {"applicable": False})

    def test_project_and_user_override(self):
        planning = self.root / "projects" / "demo" / repair_strategy.PROJECT_PATH
        planning.write_text(json.dumps({
            "schema_version": 1, "repair_strategy": {"default": "context_driven"}
        }), encoding="utf-8")
        resolved = repair_strategy.resolve(self.workspace, self.state)
        self.assertEqual(resolved["effective"]["source"], "project_default")
        self.state["facts"][repair_strategy.OVERRIDE_FACT] = {
            "id": "minimal_sufficient", "source": "user", "note": ""
        }
        resolved = repair_strategy.resolve(self.workspace, self.state)
        self.assertEqual(resolved["effective"]["source"], "user_override")
        self.assertEqual(resolved["effective"]["id"], "minimal_sufficient")

    def test_invalid_configuration_is_advisory(self):
        self.catalog_path.write_text("{broken", encoding="utf-8")
        resolved = repair_strategy.resolve(self.workspace, self.state)
        self.assertTrue(resolved["applicable"])
        self.assertFalse(resolved["available"])
        self.assertTrue(resolved["warnings"])

    def test_set_clear_and_unknown_are_atomic(self):
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 0)
        self.assertEqual(
            self.read_state()["facts"][repair_strategy.OVERRIDE_FACT]["id"], "context_driven"
        )
        before = self.read_state()
        self.assertEqual(task.cmd_repair_strategy_set(self.args(id="missing")), 2)
        self.assertEqual(self.read_state(), before)
        self.assertEqual(task.cmd_repair_strategy_clear(self.args()), 0)
        self.assertNotIn(repair_strategy.OVERRIDE_FACT, self.read_state()["facts"])

    def test_generic_record_cannot_bypass_strategy_mutation_rules(self):
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 0)
        before = self.read_state()
        with self.assertRaisesRegex(ValueError, "repair-strategy set/clear"):
            task.cmd_record(self.args(
                key=repair_strategy.OVERRIDE_FACT,
                value="minimal_sufficient",
                force=True,
                input=None,
            ))
        self.assertEqual(self.read_state(), before)

    def test_reset_archives_override_instead_of_leaking_to_new_run(self):
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 0)
        reset_args = self.args(stage="task_intake", note="重新规划")
        self.assertEqual(task.cmd_reset(reset_args), 0)
        current = self.read_state()
        self.assertNotEqual(current["run_id"], self.run_id)
        self.assertNotIn(repair_strategy.OVERRIDE_FACT, current["facts"])
        archived = [item for item in current["history"]
                    if item.get("event") == "archive_fact"
                    and item.get("key") == repair_strategy.OVERRIDE_FACT]
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["value"]["id"], "context_driven")

    def test_q2_or_later_change_does_not_create_mixed_state(self):
        self.state["stage"] = "implementation"
        task_store._write_json_atomic(task_store.task_path(self.workspace, self.issue), self.state)
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 2)
        self.assertNotIn(repair_strategy.OVERRIDE_FACT, self.read_state()["facts"])

    def test_active_authorization_prevents_pre_advance_strategy_drift(self):
        self.state["stage"] = "design_review"
        task_store._write_json_atomic(task_store.task_path(self.workspace, self.issue), self.state)
        task_store._write_json_atomic(task_store.authorization_path(self.workspace, self.issue), {
            "status": "active"
        })
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 2)
        self.assertNotIn(repair_strategy.OVERRIDE_FACT, self.read_state()["facts"])


if __name__ == "__main__":
    unittest.main()
