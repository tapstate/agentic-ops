#!/usr/bin/env python3
"""工作空间状态代际与跨版本升级门禁测试。"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import workspace_compatibility as compatibility  # noqa: E402


def manifest(epoch, minimum_updater=1, supported=None, legacy=1):
    return {
        "schema_version": 1,
        "minimum_updater_protocol_version": minimum_updater,
        "workspace_state_epoch": epoch,
        "legacy_workspace_state_epoch": legacy,
        "supported_workspace_state_epochs": supported or [epoch],
    }


class WorkspaceCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.product_root = Path(self.temporary.name) / "product"
        self.workspace = Path(self.temporary.name) / "workspace"
        state = self.workspace / ".agenticops"
        tasks = state / "tasks"
        tasks.mkdir(parents=True)
        self.product_root.mkdir()
        (state / "workspace.json").write_text(
            json.dumps({"product_root": str(self.product_root.resolve())}) + "\n",
            encoding="utf-8",
        )
        (state / "init.json").write_text(
            json.dumps({"workspace_state_epoch": 1}) + "\n",
            encoding="utf-8",
        )
        (tasks / "index.json").write_text(
            json.dumps({"schema_version": 1, "project": "tapdata", "tasks": {}}) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def add_task(self, issue="TAP-123", status="active", run_id="run-abc"):
        tasks = self.workspace / ".agenticops" / "tasks"
        (tasks / issue).mkdir()
        (tasks / issue / "state.json").write_text(
            json.dumps({"issue_key": issue, "run_id": run_id}) + "\n",
            encoding="utf-8",
        )
        (tasks / "index.json").write_text(
            json.dumps({
                "schema_version": 1,
                "project": "tapdata",
                "tasks": {issue: {"status": status}},
            }) + "\n",
            encoding="utf-8",
        )

    def test_empty_workspace_can_cross_epoch(self):
        reasons = compatibility.workspace_blockers(
            self.product_root, self.workspace, manifest(2)
        )
        self.assertEqual(["状态代际 1 不受目标代际 2 支持"], reasons)
        self.assertEqual(
            2,
            compatibility.require_workspace_can_adopt(
                self.product_root, self.workspace, manifest(2)
            ),
        )

    def test_supported_old_epoch_is_preserved_during_repair(self):
        target = manifest(2, supported=[1, 2])
        self.assertEqual(
            1,
            compatibility.require_workspace_can_adopt(
                self.product_root, self.workspace, target
            ),
        )

    def test_registered_task_blocks_cross_epoch_and_reports_identity(self):
        self.add_task()
        with self.assertRaisesRegex(
            ValueError, "任务 TAP-123（status=active，run=run-abc）"
        ):
            compatibility.require_workspace_can_adopt(
                self.product_root, self.workspace, manifest(2)
            )

    def test_unknown_root_state_blocks_cross_epoch(self):
        legacy = self.workspace / ".agenticops" / "tap-123-old-readback.json"
        legacy.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未识别的旧状态文件"):
            compatibility.require_workspace_can_adopt(
                self.product_root, self.workspace, manifest(2)
            )

    def test_symlinked_task_registry_blocks_cross_epoch(self):
        registry = self.workspace / ".agenticops" / "tasks" / "index.json"
        external = Path(self.temporary.name) / "external-index.json"
        external.write_text(registry.read_text(encoding="utf-8"), encoding="utf-8")
        registry.unlink()
        registry.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "任务注册表是符号链接"):
            compatibility.require_workspace_can_adopt(
                self.product_root, self.workspace, manifest(2)
            )

    def test_direct_upgrade_uses_target_epoch_without_scanning_intermediate_versions(self):
        self.add_task(status="inactive")
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[manifest(1), manifest(4)],
        ), mock.patch.object(
            compatibility,
            "load_workspace_registry",
            return_value=[str(self.workspace)],
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
            "load_workspace_registry",
        ) as registry:
            compatibility.check_upgrade(self.product_root, "old", "new")
        registry.assert_not_called()

    def test_dropping_old_support_without_epoch_bump_still_checks_workspaces(self):
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
            "load_workspace_registry",
            return_value=[str(self.workspace)],
        ):
            with self.assertRaisesRegex(ValueError, "状态代际 1 不受目标代际 2 支持"):
                compatibility.check_upgrade(self.product_root, "old", "new")

    def test_target_cannot_relabel_legacy_workspace_during_upgrade(self):
        init_path = self.workspace / ".agenticops" / "init.json"
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
            "load_workspace_registry",
            return_value=[str(self.workspace)],
        ):
            with self.assertRaisesRegex(ValueError, "状态代际 1 不受目标代际 2 支持"):
                compatibility.check_upgrade(self.product_root, "old", "new")

    def test_newer_upgrade_protocol_requires_bridge_release(self):
        with mock.patch.object(
            compatibility,
            "manifest_at_ref",
            side_effect=[
                manifest(1, minimum_updater=1),
                manifest(1, minimum_updater=2),
            ],
        ):
            with self.assertRaisesRegex(ValueError, "过渡版本"):
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
        contracts.mkdir()
        (contracts / "workspace-state-compatibility.json").write_text(
            json.dumps(manifest(2)) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "请先执行 agenticops repair"):
            with compatibility.task_store.task_state_lock(self.workspace):
                pass
        init_path = self.workspace / ".agenticops" / "init.json"
        init_path.write_text(
            json.dumps({"workspace_state_epoch": 2}) + "\n", encoding="utf-8"
        )
        with compatibility.task_store.task_state_lock(self.workspace):
            pass

    def test_update_script_checks_before_fast_forward(self):
        script = (ROOT / "bootstrap" / "update.sh").read_text(encoding="utf-8")
        self.assertLess(
            script.index("workspace_compatibility.py"),
            script.index('merge --ff-only "$target_ref"'),
        )

    def test_task_state_mutation_stops_during_product_lifecycle(self):
        lifecycle = self.product_root / ".local" / "lifecycle.lock"
        lifecycle.mkdir(parents=True)
        (lifecycle / "owner").write_text(str(__import__("os").getpid()) + "\n", encoding="utf-8")
        (lifecycle / "operation").write_text("update:installed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "生命周期操作"):
            with compatibility.task_store.task_state_lock(self.workspace):
                pass
        with compatibility.task_store.task_state_lock(
            self.workspace, allow_product_lifecycle=True
        ):
            pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
