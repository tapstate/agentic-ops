#!/usr/bin/env python3
"""不兼容标记与跨版本升级前绑定检查。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import station_compatibility as compatibility  # noqa: E402
from workflow import task_store  # noqa: E402


def manifest(epoch, minimum_updater=1):
    return {
        "schema_version": 1,
        "minimum_updater_protocol_version": minimum_updater,
        "station_state_epoch": epoch,
        "legacy_station_state_epoch": epoch,
        "supported_station_state_epochs": [epoch],
    }


class StationCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.product_root = Path(self.temporary.name) / "product"
        self.station = Path(self.temporary.name) / "station"
        state = self.station / ".agenticops"
        state.mkdir(parents=True)
        self.product_root.mkdir()
        (self.product_root / "contracts").mkdir()
        (self.product_root / "contracts/station-state-compatibility.json").write_text(
            json.dumps(manifest(1)), encoding="utf-8"
        )
        (state / "station.json").write_text(
            json.dumps({"schema_version": 4, "station_id": "a" * 32,
                        "project": "tapdata", "agents": ["codex"],
                        "product_root": str(self.product_root.resolve()),
                        "source_pool": str(self.station / "pool")}) + "\n",
            encoding="utf-8",
        )
        (state / "init.json").write_text(
            json.dumps({"station_state_epoch": 1}) + "\n", encoding="utf-8"
        )

    def tearDown(self):
        self.temporary.cleanup()

    def write_registry(self, stations):
        local = self.product_root / ".local"
        local.mkdir()
        (local / "stations.json").write_text(
            json.dumps({"schema_version": 1, "stations": stations}) + "\n",
            encoding="utf-8",
        )

    def test_epoch_nine_rejected_before_mutating_epoch_ten_station(self):
        (self.product_root / "contracts/station-state-compatibility.json").write_text(json.dumps(manifest(10)))
        path = self.station / ".agenticops/init.json"
        path.write_text(json.dumps({"station_state_epoch": 9}))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            with task_store.task_state_lock(self.station):
                self.fail("旧工位不能进入写入区")
        self.assertEqual(before, path.read_bytes())

    def test_epoch_ten_rejected_by_replan_epoch_eleven(self):
        (self.product_root / "contracts/station-state-compatibility.json").write_text(json.dumps(manifest(11)))
        path = self.station / ".agenticops/init.json"
        path.write_text(json.dumps({"station_state_epoch": 10}))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            with task_store.task_state_lock(self.station):
                self.fail("旧工位不能进入写入区")
        self.assertEqual(before, path.read_bytes())

    def test_manifest_rejects_old_epoch_support(self):
        document = manifest(2)
        document["supported_station_state_epochs"] = [1, 2]
        with self.assertRaisesRegex(ValueError, "不得声明旧工位状态兼容性"):
            compatibility.validate_manifest(document, "fixture")

    def test_manifest_rejects_legacy_epoch_mapping(self):
        document = manifest(2)
        document["legacy_station_state_epoch"] = 1
        with self.assertRaisesRegex(ValueError, "不得声明旧工位状态兼容性"):
            compatibility.validate_manifest(document, "fixture")

    def test_same_epoch_does_not_require_station_cleanup(self):
        with mock.patch.object(
            compatibility, "manifest_at_ref", side_effect=[manifest(1), manifest(1)]
        ), mock.patch.object(compatibility, "load_station_registry") as registry:
            self.assertEqual([], compatibility.check_upgrade(self.product_root, "old", "new"))
        registry.assert_not_called()

    def test_cross_epoch_allows_only_an_explicit_empty_registry(self):
        self.write_registry([])
        with mock.patch.object(
            compatibility, "manifest_at_ref", side_effect=[manifest(1), manifest(2)]
        ):
            self.assertEqual([], compatibility.check_upgrade(self.product_root, "old", "new"))

    def test_cross_epoch_rejects_missing_registry(self):
        with mock.patch.object(
            compatibility, "manifest_at_ref", side_effect=[manifest(1), manifest(2)]
        ), self.assertRaisesRegex(ValueError, "登记清单缺失"):
            compatibility.check_upgrade(self.product_root, "old", "new")

    def test_cross_epoch_rejects_each_bound_station_without_reading_its_state(self):
        self.write_registry([str(self.station)])
        legacy = self.station / ".agenticops" / "legacy-state"
        legacy.write_text("target version must not parse this", encoding="utf-8")
        with mock.patch.object(
            compatibility, "manifest_at_ref", side_effect=[manifest(1), manifest(2)]
        ), self.assertRaisesRegex(ValueError, "仍有绑定工位") as error:
            compatibility.check_upgrade(self.product_root, "old", "new")
        self.assertIn(str(self.station), str(error.exception))
        self.assertEqual("target version must not parse this", legacy.read_text(encoding="utf-8"))

    def test_rollback_uses_the_same_bound_station_requirement(self):
        self.write_registry([str(self.station)])
        with mock.patch.object(
            compatibility, "manifest_at_ref", side_effect=[manifest(2), manifest(1)]
        ), self.assertRaisesRegex(ValueError, "agenticops rollback"):
            compatibility.check_upgrade(
                self.product_root, "new", "old", operation="rollback"
            )

    def test_newer_upgrade_protocol_requires_reinstallation(self):
        with mock.patch.object(
            compatibility, "manifest_at_ref", side_effect=[
                manifest(1),
                manifest(1, minimum_updater=compatibility.UPDATER_PROTOCOL_VERSION + 1),
            ]), self.assertRaisesRegex(ValueError, "重新安装"):
            compatibility.check_upgrade(self.product_root, "old", "new")

    def test_runtime_requires_the_exact_product_epoch(self):
        contracts = self.product_root / "contracts"
        (contracts / "station-state-compatibility.json").write_text(
            json.dumps(manifest(2)) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "受控解绑并重建"):
            with task_store.task_state_lock(self.station):
                pass
        init_path = self.station / ".agenticops" / "init.json"
        init_path.write_text(
            json.dumps({"station_state_epoch": 2}) + "\n", encoding="utf-8"
        )
        with task_store.task_state_lock(self.station):
            pass

    def test_update_script_checks_before_fast_forward(self):
        script = (ROOT / "bootstrap" / "update.sh").read_text(encoding="utf-8")
        self.assertLess(
            script.index("station_compatibility.py"),
            script.index('merge --ff-only "$target_ref"'),
        )

    def test_rollback_passes_the_rollback_operation_to_the_checker(self):
        script = (ROOT / "bootstrap" / "rollback.sh").read_text(encoding="utf-8")
        self.assertIn("--operation rollback", script)

    def test_public_binding_commands_share_the_lifecycle_lock(self):
        entry = (ROOT / "agenticops").read_text(encoding="utf-8")
        for operation in ("station-start-refresh:", "station-repair:"):
            self.assertIn(operation, entry)
        self.assertIn("prune|repair|clean|detach|purge", entry)
        self.assertIn('"station-$station_command_name"', entry)
        self.assertIn("--lifecycle-held", entry)

    def test_task_state_mutation_stops_during_product_lifecycle(self):
        lifecycle = self.product_root / ".local" / "lifecycle.lock"
        lifecycle.mkdir(parents=True)
        (lifecycle / "owner").write_text(
            str(__import__("os").getpid()) + "\n", encoding="utf-8"
        )
        (lifecycle / "operation").write_text("update:installed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "生命周期操作"):
            with task_store.task_state_lock(self.station):
                pass
        with task_store.task_state_lock(
            self.station, allow_product_lifecycle=True
        ):
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
