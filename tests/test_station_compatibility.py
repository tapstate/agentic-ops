#!/usr/bin/env python3
"""工位状态代际与跨版本升级门禁测试。"""
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


def manifest(epoch, minimum_updater=1, supported=None, legacy=1):
    return {
        "schema_version": 1,
        "minimum_updater_protocol_version": minimum_updater,
        "station_state_epoch": epoch,
        "legacy_station_state_epoch": legacy,
        "supported_station_state_epochs": supported or [epoch],
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
        (self.product_root / "contracts/station-state-compatibility.json").write_text(json.dumps(manifest(1)))
        (state / "station.json").write_text(
            json.dumps({"schema_version": 3, "station_id": "a" * 32,
                        "project": "tapdata", "agents": ["codex"],
                        "product_root": str(self.product_root.resolve())}) + "\n",
            encoding="utf-8",
        )
        (state / "init.json").write_text(
            json.dumps({"station_state_epoch": 1}) + "\n",
            encoding="utf-8",
        )


    def tearDown(self):
        self.temporary.cleanup()

    def add_task(self, issue="TAP-123", status="active", run_id="run-abc"):
        (self.station / ".agenticops/current-task.json").write_text(json.dumps({
            "schema_version": 1, "revision": 1,
            "current": {"issue_key": issue, "run_id": run_id, "outcome": status}
        }))

    def test_empty_station_cannot_adopt_incompatible_epoch(self):
        reasons = compatibility.station_blockers(
            self.product_root, self.station, manifest(2)
        )
        self.assertEqual(["状态代际 1 不受目标代际 2 支持"], reasons)
        with self.assertRaisesRegex(ValueError, "受控解绑并重建"):
            compatibility.require_station_can_adopt(
                self.product_root, self.station, manifest(2)
            )

    def test_empty_binding_blocks_upgrade_and_rollback_without_state_changes(self):
        init = self.station / ".agenticops/init.json"
        before = init.read_bytes()
        for rollback in (False, True):
            with self.subTest(rollback=rollback), mock.patch.object(
                compatibility, "manifest_at_ref", side_effect=[manifest(1), manifest(2)]
            ), mock.patch.object(compatibility, "load_station_registry", return_value=[str(self.station)]):
                with self.assertRaisesRegex(ValueError, "agenticops " + ("rollback" if rollback else "update")) as error:
                    compatibility.check_upgrade(self.product_root, "old-sha", "target-sha", rollback)
                self.assertIn("old-sha -> target-sha", str(error.exception))
                self.assertEqual(before, init.read_bytes())

    def test_unregistered_stations_do_not_block_switch(self):
        with mock.patch.object(compatibility, "manifest_at_ref", side_effect=[manifest(1), manifest(2)]), \
                mock.patch.object(compatibility, "load_station_registry", return_value=[]):
            self.assertEqual([], compatibility.check_upgrade(self.product_root, "old", "new"))

    def test_supported_old_epoch_is_preserved_during_repair(self):
        target = manifest(2, supported=[1, 2])
        self.assertEqual(
            1,
            compatibility.require_station_can_adopt(
                self.product_root, self.station, target
            ),
        )

    def test_registered_task_blocks_cross_epoch_and_reports_identity(self):
        self.add_task()
        with self.assertRaisesRegex(
            ValueError, "不兼容"
        ):
            compatibility.require_station_can_adopt(
                self.product_root, self.station, manifest(2)
            )

    def test_old_state_is_never_parsed_or_mutated_by_new_version(self):
        legacy = self.station / ".agenticops/tasks"
        legacy.mkdir()
        index = legacy / "index.json"
        index.write_text("unreadable old-format fixture")
        with self.assertRaisesRegex(ValueError, "受控解绑并重建"):
            compatibility.require_station_can_adopt(self.product_root, self.station, manifest(3))
        self.assertEqual(index.read_text(), "unreadable old-format fixture")

    def test_direct_upgrade_uses_target_epoch_without_scanning_intermediate_versions(self):
        self.add_task(status="inactive")
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[manifest(1), manifest(4)],
        ), mock.patch.object(
            compatibility,
            "load_station_registry",
            return_value=[str(self.station)],
        ):
            with self.assertRaisesRegex(ValueError, "目标版本包含不兼容"):
                compatibility.check_upgrade(self.product_root, "v1.20", "v1.60")

    def test_same_epoch_does_not_require_task_cleanup(self):
        self.add_task()
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[manifest(1), manifest(1)],
        ), mock.patch.object(
            compatibility,
            "load_station_registry",
        ) as registry:
            compatibility.check_upgrade(self.product_root, "old", "new")
        registry.assert_not_called()

    def test_dropping_old_support_without_epoch_bump_still_checks_stations(self):
        self.add_task()
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[
                manifest(2, supported=[1, 2]),
                manifest(2, supported=[2]),
            ],
        ), mock.patch.object(
            compatibility,
            "load_station_registry",
            return_value=[str(self.station)],
        ):
            with self.assertRaisesRegex(ValueError, "状态代际 1 不受目标代际 2 支持"):
                compatibility.check_upgrade(self.product_root, "old", "new")

    def test_target_cannot_relabel_legacy_station_during_upgrade(self):
        init_path = self.station / ".agenticops" / "init.json"
        init_path.write_text("{}\n", encoding="utf-8")
        self.add_task()
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[
                manifest(2, supported=[1, 2], legacy=1),
                manifest(2, supported=[2], legacy=2),
            ],
        ), mock.patch.object(
            compatibility,
            "load_station_registry",
            return_value=[str(self.station)],
        ):
            with self.assertRaisesRegex(ValueError, "状态代际 1 不受目标代际 2 支持"):
                compatibility.check_upgrade(self.product_root, "old", "new")

    def test_newer_upgrade_protocol_requires_reinstallation(self):
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[
                manifest(1, minimum_updater=1),
                manifest(1, minimum_updater=compatibility.UPDATER_PROTOCOL_VERSION + 1),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "重新安装"):
                compatibility.check_upgrade(self.product_root, "old", "new")

    def test_bridge_updater_can_reach_later_protocol(self):
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[manifest(1), manifest(1, minimum_updater=1)],
        ), mock.patch.object(compatibility, "UPDATER_PROTOCOL_VERSION", 1):
            compatibility.check_upgrade(self.product_root, "old", "bridge")
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[
                manifest(1, minimum_updater=1),
                manifest(1, minimum_updater=2),
            ],
        ), mock.patch.object(compatibility, "UPDATER_PROTOCOL_VERSION", 2):
            compatibility.check_upgrade(self.product_root, "bridge", "new")

    def test_task_mutation_rejects_unadopted_epoch_after_upgrade(self):
        contracts = self.product_root / "contracts"
        contracts.mkdir(exist_ok=True)
        (contracts / "station-state-compatibility.json").write_text(
            json.dumps(manifest(2)) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "受控解绑并重建"):
            with compatibility.task_store.task_state_lock(self.station):
                pass
        init_path = self.station / ".agenticops" / "init.json"
        init_path.write_text(
            json.dumps({"station_state_epoch": 2}) + "\n", encoding="utf-8"
        )
        with compatibility.task_store.task_state_lock(self.station):
            pass

    def test_update_script_checks_before_fast_forward(self):
        script = (ROOT / "bootstrap" / "update.sh").read_text(encoding="utf-8")
        self.assertLess(
            script.index("station_compatibility.py"),
            script.index('merge --ff-only "$target_ref"'),
        )

    def test_task_state_mutation_stops_during_product_lifecycle(self):
        lifecycle = self.product_root / ".local" / "lifecycle.lock"
        lifecycle.mkdir(parents=True)
        (lifecycle / "owner").write_text(str(__import__("os").getpid()) + "\n", encoding="utf-8")
        (lifecycle / "operation").write_text("update:installed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "生命周期操作"):
            with compatibility.task_store.task_state_lock(self.station):
                pass
        with compatibility.task_store.task_state_lock(
            self.station, allow_product_lifecycle=True
        ):
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
