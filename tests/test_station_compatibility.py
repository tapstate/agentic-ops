#!/usr/bin/env python3
"""不兼容标记与跨版本升级前绑定检查。"""
from __future__ import annotations

import json
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import station_compatibility as compatibility  # noqa: E402
from workflow import task_store  # noqa: E402


def manifest(epoch):
    return {"station_state_epoch": epoch}


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

    def test_epoch_eleven_rejected_by_status_evidence_epoch_twelve(self):
        (self.product_root / "contracts/station-state-compatibility.json").write_text(json.dumps(manifest(12)))
        path = self.station / ".agenticops/init.json"
        path.write_text(json.dumps({"station_state_epoch": 11}))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            with task_store.task_state_lock(self.station):
                self.fail("旧工位不能进入写入区")
        self.assertEqual(before, path.read_bytes())

    def test_epoch_twelve_rejected_by_jira_attempt_epoch_thirteen(self):
        (self.product_root / "contracts/station-state-compatibility.json").write_text(json.dumps(manifest(13)))
        path = self.station / ".agenticops/init.json"
        path.write_text(json.dumps({"station_state_epoch": 12}))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            with task_store.task_state_lock(self.station):
                self.fail("旧工位不能进入写入区")
        self.assertEqual(before, path.read_bytes())

    def test_epoch_thirteen_rejected_by_native_cleanup_epoch_fourteen(self):
        (self.product_root / "contracts/station-state-compatibility.json").write_text(json.dumps(manifest(14)))
        path = self.station / ".agenticops/init.json"
        path.write_text(json.dumps({"station_state_epoch": 13}))
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            with task_store.task_state_lock(self.station):
                self.fail("旧工位不能进入原生清理写入区")
        self.assertEqual(before, path.read_bytes())

    def test_fingerprint_epoch_rejects_old_state_and_bound_upgrade_or_rollback(self):
        current = json.loads((ROOT / "contracts/station-state-compatibility.json").read_text())
        self.assertEqual(19, current["station_state_epoch"])
        (self.product_root / "contracts/station-state-compatibility.json").write_text(json.dumps(current))
        path = self.station / ".agenticops/init.json"
        path.write_text(json.dumps({"station_state_epoch": 14}))
        record = self.station / ".agenticops/recovery-fixture.json"
        record.write_text('{"fingerprint":"old-unframed-dirty-digest"}')
        before = {p: p.read_bytes() for p in (path, record)}
        with self.assertRaises(ValueError):
            with task_store.task_state_lock(self.station):
                self.fail("旧指纹工位不能进入新版本写入区")
        self.write_registry([str(self.station)])
        for operation, epochs in (("update", (14, 17)), ("rollback", (17, 14)),
                                  ("update", (16, 17)), ("rollback", (17, 16))):
            with mock.patch.object(compatibility, "manifest_at_ref", side_effect=[manifest(e) for e in epochs]):
                with self.assertRaisesRegex(ValueError, "仍有绑定工位"):
                    compatibility.check_upgrade(self.product_root, "old", "new", operation=operation)
        self.assertEqual(before, {p: p.read_bytes() for p in (path, record)})

    def test_source_reference_epoch_rejects_bound_upgrade_and_rollback(self):
        self.write_registry([str(self.station)])
        for old, new in ((17, 18), (18, 17), (18, 19), (19, 18)):
            (self.product_root / 'contracts/station-state-compatibility.json').write_text(json.dumps(manifest(new)))
            path = self.station / '.agenticops/init.json'
            path.write_text(json.dumps(manifest(old)))
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                with task_store.task_state_lock(self.station):
                    self.fail('跨代际不得进入状态写入区')
            with mock.patch.object(compatibility, 'manifest_at_ref', side_effect=[manifest(old), manifest(new)]):
                with self.assertRaisesRegex(ValueError, '仍有绑定工位'):
                    compatibility.check_upgrade(self.product_root, 'old', 'new', operation='update' if new > old else 'rollback')
            self.assertEqual(before, path.read_bytes())

    def test_all_pre_recovery_epochs_are_rejected_by_current_product(self):
        current_manifest = json.loads((ROOT / 'contracts/station-state-compatibility.json').read_text())
        (self.product_root / 'contracts/station-state-compatibility.json').write_text(json.dumps(current_manifest))
        path = self.station / '.agenticops/init.json'
        for old in range(9, current_manifest['station_state_epoch']):
            with self.subTest(epoch=old):
                path.write_text(json.dumps({'station_state_epoch': old}))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    with task_store.task_state_lock(self.station):
                        self.fail('旧工位不得进入写入区')
                self.assertEqual(before, path.read_bytes())

    def test_compatibility_materials_reject_non_objects_booleans_and_bad_encoding(self):
        registry = self.product_root / ".local/stations.json"
        registry.parent.mkdir()
        init = self.station / ".agenticops/init.json"
        for value in (None, [], True, 1, "fixture"):
            raw = json.dumps(value).encode()
            registry.write_bytes(raw)
            init.write_bytes(raw)
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    compatibility.load_station_registry(self.product_root, required=True)
                with self.assertRaises(ValueError):
                    compatibility.require_station_can_adopt(self.product_root, self.station)
                self.assertEqual(raw, registry.read_bytes())
                self.assertEqual(raw, init.read_bytes())
        document = manifest(1)
        document["station_state_epoch"] = True
        with self.assertRaises(ValueError):
            compatibility.validate_manifest(document, "fixture")
        for extra in ("schema_version", "minimum_updater_protocol_version", "legacy_station_state_epoch", "supported_station_state_epochs"):
            document = manifest(1)
            document[extra] = 1
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                compatibility.validate_manifest(document, "fixture")
        registry.write_text('{"schema_version":true,"stations":[]}')
        with self.assertRaises(ValueError):
            compatibility.load_station_registry(self.product_root, required=True)
        init.write_text('{"station_state_epoch":true}')
        with self.assertRaises(ValueError):
            compatibility.require_station_can_adopt(self.product_root, self.station)
        for path, read in ((registry, lambda: compatibility.load_station_registry(self.product_root, required=True)),
                           (init, lambda: compatibility.require_station_can_adopt(self.product_root, self.station)),
                           (self.product_root / "contracts/station-state-compatibility.json", lambda: compatibility.load_manifest(self.product_root))):
            path.write_bytes(b"\xff")
            with self.assertRaises(ValueError):
                read()
            self.assertEqual(b"\xff", path.read_bytes())
        with mock.patch.object(compatibility.subprocess, "run", side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "fixture")):
            with self.assertRaisesRegex(ValueError, "编码无效"):
                compatibility.manifest_at_ref(self.product_root, "target")
        registry.write_text('null')
        with mock.patch.object(compatibility, "manifest_at_ref", side_effect=[manifest(14), manifest(15)]):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(2, compatibility.main(["--product-root", str(self.product_root),
                    "check-upgrade", "--current-ref", "old", "--target-ref", "new"]))
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertIn("结构无效", stderr.getvalue())

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

    def test_runtime_rejects_non_object_manifest_and_init_without_traceback(self):
        manifest_path = self.product_root / "contracts/station-state-compatibility.json"
        init_path = self.station / ".agenticops/init.json"
        for target in (manifest_path, init_path):
            original = target.read_bytes()
            target.write_text("[]\n", encoding="utf-8")
            with self.subTest(path=target), self.assertRaisesRegex(ValueError, "无效"):
                with task_store.task_state_lock(self.station):
                    pass
            target.write_bytes(original)

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
