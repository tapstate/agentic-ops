#!/usr/bin/env python3
"""AO-137 非阻断缺陷修复策略回归。"""
from __future__ import annotations

import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import repair_strategy, task, task_store
from station_fixture import save_task as save_station_task


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
        self.station = base / "station"
        (self.root / "policies").mkdir(parents=True)
        (self.root / "projects" / "demo").mkdir(parents=True)
        (self.station / ".agenticops").mkdir(parents=True)
        self.catalog_path = self.root / repair_strategy.CATALOG_PATH
        self.catalog_path.write_text(json.dumps(CATALOG, ensure_ascii=False), encoding="utf-8")
        (self.station / ".agenticops" / "station.json").write_text(json.dumps({
            "schema_version": 1, "product_root": str(self.root), "project": "demo", "agents": []
        }), encoding="utf-8")
        self.issue = "DEMO-1"
        self.run_id = "run-0123456789ab"
        self.state = {
            "issue_key": self.issue, "run_id": self.run_id, "task_class": "defect_fix",
            "stage": "task_intake", "facts": {}, "repositories": [], "pending": None, "history": [],
        }
        save_station_task(self.station, self.state)


    def tearDown(self):
        self.temp.cleanup()

    def read_state(self):
        return task_store.read_task(self.station, self.issue)

    def args(self, **values):
        base = {"dir": str(self.station), "issue_key": self.issue,
                "expected_run_id": self.run_id, "id": "context_driven", "note": "用户选择"}
        base.update(values)
        return SimpleNamespace(**base)

    def test_default_and_non_defect_scope(self):
        resolved = repair_strategy.resolve(self.station, self.state)
        self.assertTrue(resolved["applicable"])
        self.assertTrue(resolved["available"])
        self.assertEqual(resolved["effective"], {
            "id": "minimal_sufficient", "label": "最小充分修复", "source": "company_default"
        })
        other = dict(self.state, task_class="feature_change")
        self.assertEqual(repair_strategy.resolve(self.station, other), {"applicable": False})

    def test_project_and_user_override(self):
        planning = self.root / "projects" / "demo" / repair_strategy.PROJECT_PATH
        planning.write_text(json.dumps({
            "schema_version": 1, "repair_strategy": {"default": "context_driven"}
        }), encoding="utf-8")
        resolved = repair_strategy.resolve(self.station, self.state)
        self.assertEqual(resolved["effective"]["source"], "project_default")
        self.state["facts"][repair_strategy.OVERRIDE_FACT] = {
            "id": "minimal_sufficient", "source": "user", "note": ""
        }
        resolved = repair_strategy.resolve(self.station, self.state)
        self.assertEqual(resolved["effective"]["source"], "user_override")
        self.assertEqual(resolved["effective"]["id"], "minimal_sufficient")

    def test_invalid_configuration_is_advisory(self):
        self.catalog_path.write_text("{broken", encoding="utf-8")
        resolved = repair_strategy.resolve(self.station, self.state)
        self.assertTrue(resolved["applicable"])
        self.assertFalse(resolved["available"])
        self.assertTrue(resolved["warnings"])

    def test_project_configuration_errors_fall_back_without_mutating_task(self):
        path = self.root / "projects/demo" / repair_strategy.PROJECT_PATH
        before = self.read_state()
        for value in (None, [], 1, "text", {"schema_version": 1, "repair_strategy": {"default": []}},
                      {"schema_version": 1, "repair_strategy": {"default": {}}}):
            with self.subTest(value=value):
                path.write_text(json.dumps(value), encoding="utf-8")
                resolved = repair_strategy.resolve(self.station, self.state)
                self.assertTrue(resolved["available"])
                self.assertEqual(resolved["effective"]["source"], "company_default")
                self.assertTrue(resolved["warnings"])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(task.cmd_repair_strategy_show(self.args(json=True)), 0)
                self.assertTrue(json.loads(output.getvalue())["warnings"])
                self.assertEqual(self.read_state(), before)

    def test_missing_project_is_advisory(self):
        (self.root / "projects/demo").rmdir()
        resolved = repair_strategy.resolve(self.station, self.state)
        self.assertTrue(resolved["available"])
        self.assertEqual(resolved["effective"]["source"], "company_default")
        self.assertTrue(resolved["warnings"])

    def test_company_configuration_structure_errors_are_advisory(self):
        for value in (None, [], 1, dict(CATALOG, default=[]), dict(CATALOG, default={})):
            with self.subTest(value=value):
                self.catalog_path.write_text(json.dumps(value), encoding="utf-8")
                resolved = repair_strategy.resolve(self.station, self.state)
                self.assertFalse(resolved["available"])
                self.assertTrue(resolved["warnings"])

    def test_invalid_task_override_preserves_project_default(self):
        path = self.root / "projects/demo" / repair_strategy.PROJECT_PATH
        path.write_text(json.dumps({"schema_version": 1, "repair_strategy": {"default": "context_driven"}}))
        for value in ([], {}, 1, "missing"):
            with self.subTest(value=value):
                self.state["facts"][repair_strategy.OVERRIDE_FACT] = {"id": value}
                resolved = repair_strategy.resolve(self.station, self.state)
                self.assertEqual(resolved["effective"]["id"], "context_driven")
                self.assertEqual(resolved["effective"]["source"], "project_default")
                self.assertTrue(resolved["warnings"])

    def test_non_object_authorization_blocks_strategy_change_without_crash(self):
        task_store.authorization_path(self.station, self.issue).write_text("null")
        before = self.read_state()
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 2)
        self.assertEqual(self.read_state(), before)

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

    def test_archive_freezes_override_and_new_run_uses_default(self):
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 0)
        self.state = self.read_state()
        self.state["archive_ref"] = {"path": "archive/DEMO-1/" + self.run_id, "digest": "a" * 64}
        save_station_task(self.station, self.state)
        archived = self.read_state()
        with self.assertRaisesRegex(ValueError, "归档"):
            task.cmd_repair_strategy_clear(self.args())
        self.assertEqual(self.read_state(), archived)
        current = dict(self.state, run_id="run-new", facts={}, archive_ref=None)
        save_station_task(self.station, current)
        self.assertNotEqual(current["run_id"], self.run_id)
        self.assertNotIn(repair_strategy.OVERRIDE_FACT, current["facts"])
        self.assertEqual(archived["facts"][repair_strategy.OVERRIDE_FACT]["id"], "context_driven")

    def test_q2_or_later_change_does_not_create_mixed_state(self):
        self.state["stage"] = "implementation"
        save_station_task(self.station, self.state)
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 2)
        self.assertNotIn(repair_strategy.OVERRIDE_FACT, self.read_state()["facts"])

    def test_active_authorization_prevents_pre_advance_strategy_drift(self):
        self.state["stage"] = "design_review"
        save_station_task(self.station, self.state)
        task_store._write_json_atomic(task_store.authorization_path(self.station, self.issue), {
            "status": "active"
        })
        self.assertEqual(task.cmd_repair_strategy_set(self.args()), 2)
        self.assertNotIn(repair_strategy.OVERRIDE_FACT, self.read_state()["facts"])


if __name__ == "__main__":
    unittest.main()
