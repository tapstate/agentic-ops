"""执行编号、工位 git_name 与自动工作分支的回归。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bootstrap"))
from bootstrap import render, station_registry
from workflow import station_operation, task_store


class TaskIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.station = Path(self.temporary.name) / "station"
        (self.station / ".agenticops").mkdir(parents=True)
        task_store.initialize_current(self.station)

    def binding(self, identity=None, station=None):
        station = station or self.station
        value = {"schema_version": 4, "product_root": str(ROOT), "source_pool": str(self.station.parent / "pool"), "station_id": "a" * 32,
                 "project": "tapdata", "agents": ["codex"]}
        if identity is not None:
            value["branch_identity"] = identity
        task_store._write_json_atomic(station / ".agenticops/station.json", value)
        task_store._write_json_atomic(station / ".agenticops/init.json", {"station_state_epoch": 13})

    def test_timestamp_hex_and_new_run_are_fixed_and_issue_bound(self):
        self.assertEqual(task_store.timestamp_hex(0), "00000000")
        self.assertEqual(task_store.timestamp_hex(0xFFFFFFFF), "ffffffff")
        self.assertEqual(task_store.new_run_id("tap-123", 0x69D8A1F0), "TAP-123-69d8a1f0")
        with self.assertRaisesRegex(ValueError, "超出"):
            task_store.timestamp_hex(0x100000000)
        self.assertEqual(task_store.validate_run_id("TAP-123", "run-old-fixture"), "run-old-fixture")
        with self.assertRaisesRegex(ValueError, "不一致"):
            task_store.validate_run_id("TAP-123", "TAP-124-69d8a1f0")

    def test_takeover_run_reuses_operation_and_rejects_archive_collision(self):
        self.binding({"schema_version": 1, "git_name": "developer", "source": "git_global_user_name"})
        with mock.patch.object(task_store, "timestamp_hex", return_value="69d8a1f0"):
            operation = station_operation.begin(self.station, "takeover", "op-identity-one", 0, {"issue_key": "TAP-123"})
        self.assertEqual(operation["run_id"], "TAP-123-69d8a1f0")
        retry = station_operation.begin(self.station, "takeover", "op-identity-one", 0, {"issue_key": "TAP-123"})
        self.assertEqual(retry["run_id"], operation["run_id"])

        other = Path(self.temporary.name) / "collision"
        (other / ".agenticops").mkdir(parents=True)
        task_store.initialize_current(other)
        self.binding({"schema_version": 1, "git_name": "developer", "source": "git_global_user_name"}, other)
        (other / "archive/TAP-123/TAP-123-69d8a1f0").mkdir(parents=True)
        with mock.patch.object(task_store, "timestamp_hex", return_value="69d8a1f0"):
            with self.assertRaisesRegex(ValueError, "档案冲突"):
                station_operation.begin(other, "takeover", "op-identity-two", 0, {"issue_key": "TAP-123"})

    def test_takeover_without_station_identity_keeps_non_code_flow_available(self):
        self.binding()
        with mock.patch.object(task_store, "timestamp_hex", return_value="69d8a1f0"):
            operation = station_operation.begin(self.station, "takeover", "op-identity-missing", 0, {"issue_key": "TAP-123"})
        self.assertEqual(operation["run_id"], "TAP-123-69d8a1f0")
        with self.assertRaisesRegex(ValueError, "未配置 git_name"):
            task_store.generated_work_branch(self.station, {"issue_key": "TAP-123", "run_id": operation["run_id"]})
        self.assertIsNone(task_store.read_current(self.station)["current"])

    def test_station_identity_is_first_write_wins_and_generates_branch(self):
        self.binding()
        with mock.patch.object(station_registry, "configured_git_name", return_value=("developer", "git_global_user_name")):
            station_registry.command_identity(type("Args", (), {"station": str(self.station)})(), ROOT)
        task = {"issue_key": "TAP-123", "run_id": "TAP-123-69d8a1f0"}
        self.assertEqual(task_store.generated_work_branch(self.station, task), "developer/TAP-123-69d8a1f0")
        document = json.loads((self.station / ".agenticops/station.json").read_text())
        self.assertEqual(document["branch_identity"]["git_name"], "developer")
        with mock.patch.object(station_registry, "configured_git_name", return_value=("other", "git_global_user_name")):
            station_registry.command_identity(type("Args", (), {"station": str(self.station)})(), ROOT)
        self.assertEqual(json.loads((self.station / ".agenticops/station.json").read_text())["branch_identity"]["git_name"], "developer")

    def test_new_station_reads_valid_global_git_name_once(self):
        with mock.patch.object(render, "global_git_name", return_value="developer"):
            created = render.station_document(ROOT, self.station, "tapdata", ["codex"], self.station.parent / "pool", None)
        self.assertEqual(created["branch_identity"]["git_name"], "developer")
        preserved = render.station_document(ROOT, self.station, "tapdata", ["codex"], self.station.parent / "pool", {
            "station_id": "b" * 32, "branch_identity": created["branch_identity"]})
        self.assertEqual(preserved["branch_identity"], created["branch_identity"])
        with mock.patch.object(render, "global_git_name", return_value="recovered"):
            backfilled = render.station_document(ROOT, self.station, "tapdata", ["codex"], self.station.parent / "pool", {
                "station_id": "c" * 32,
            })
        self.assertEqual(backfilled["branch_identity"]["git_name"], "recovered")

    def test_git_name_rejects_invalid_git_ref_prefix(self):
        self.assertEqual(task_store.validate_git_name("开发者_1"), "开发者_1")
        with self.assertRaisesRegex(ValueError, "合法"):
            task_store.validate_git_name("developer..team")


if __name__ == "__main__":
    unittest.main()
