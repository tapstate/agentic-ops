"""执行编号、工作空间 git_name 与自动工作分支的回归。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bootstrap"))
from bootstrap import render, workspace_registry
from workflow import station_operation, task_store


class TaskIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name) / "workspace"
        (self.workspace / ".agenticops").mkdir(parents=True)
        task_store.initialize_current(self.workspace)

    def binding(self, identity=None, workspace=None):
        workspace = workspace or self.workspace
        value = {"schema_version": 3, "product_root": str(ROOT), "workspace_id": "a" * 32,
                 "project": "tapdata", "agents": ["codex"]}
        if identity is not None:
            value["branch_identity"] = identity
        task_store._write_json_atomic(workspace / ".agenticops/workspace.json", value)
        task_store._write_json_atomic(workspace / ".agenticops/init.json", {"workspace_state_epoch": 4})

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
            operation = station_operation.begin(self.workspace, "takeover", "op-identity-one", 0, {"issue_key": "TAP-123"})
        self.assertEqual(operation["run_id"], "TAP-123-69d8a1f0")
        retry = station_operation.begin(self.workspace, "takeover", "op-identity-one", 0, {"issue_key": "TAP-123"})
        self.assertEqual(retry["run_id"], operation["run_id"])

        other = Path(self.temporary.name) / "collision"
        (other / ".agenticops").mkdir(parents=True)
        task_store.initialize_current(other)
        self.binding({"schema_version": 1, "git_name": "developer", "source": "git_global_user_name"}, other)
        (other / "archive/TAP-123/TAP-123-69d8a1f0").mkdir(parents=True)
        with mock.patch.object(task_store, "timestamp_hex", return_value="69d8a1f0"):
            with self.assertRaisesRegex(ValueError, "档案冲突"):
                station_operation.begin(other, "takeover", "op-identity-two", 0, {"issue_key": "TAP-123"})

    def test_takeover_without_workspace_identity_keeps_non_code_flow_available(self):
        self.binding()
        with mock.patch.object(task_store, "timestamp_hex", return_value="69d8a1f0"):
            operation = station_operation.begin(self.workspace, "takeover", "op-identity-missing", 0, {"issue_key": "TAP-123"})
        self.assertEqual(operation["run_id"], "TAP-123-69d8a1f0")
        with self.assertRaisesRegex(ValueError, "未配置 git_name"):
            task_store.generated_work_branch(self.workspace, {"issue_key": "TAP-123", "run_id": operation["run_id"]})
        self.assertIsNone(task_store.read_current(self.workspace)["current"])

    def test_workspace_identity_is_first_write_wins_and_generates_branch(self):
        self.binding()
        with mock.patch.object(workspace_registry, "configured_git_name", return_value=("developer", "git_global_user_name")):
            workspace_registry.command_identity(type("Args", (), {"workspace": str(self.workspace)})(), ROOT)
        task = {"issue_key": "TAP-123", "run_id": "TAP-123-69d8a1f0"}
        self.assertEqual(task_store.generated_work_branch(self.workspace, task), "developer/TAP-123-69d8a1f0")
        document = json.loads((self.workspace / ".agenticops/workspace.json").read_text())
        self.assertEqual(document["branch_identity"]["git_name"], "developer")
        with mock.patch.object(workspace_registry, "configured_git_name", return_value=("other", "git_global_user_name")):
            workspace_registry.command_identity(type("Args", (), {"workspace": str(self.workspace)})(), ROOT)
        self.assertEqual(json.loads((self.workspace / ".agenticops/workspace.json").read_text())["branch_identity"]["git_name"], "developer")

    def test_new_workspace_reads_valid_global_git_name_once(self):
        with mock.patch.object(render, "global_git_name", return_value="developer"):
            created = render.workspace_document(ROOT, self.workspace, "tapdata", ["codex"], None)
        self.assertEqual(created["branch_identity"]["git_name"], "developer")
        preserved = render.workspace_document(ROOT, self.workspace, "tapdata", ["codex"], {
            "workspace_id": "b" * 32, "branch_identity": created["branch_identity"]})
        self.assertEqual(preserved["branch_identity"], created["branch_identity"])
        with mock.patch.object(render, "global_git_name", return_value="recovered"):
            backfilled = render.workspace_document(ROOT, self.workspace, "tapdata", ["codex"], {
                "workspace_id": "c" * 32,
            })
        self.assertEqual(backfilled["branch_identity"]["git_name"], "recovered")

    def test_git_name_rejects_invalid_git_ref_prefix(self):
        self.assertEqual(task_store.validate_git_name("开发者_1"), "开发者_1")
        with self.assertRaisesRegex(ValueError, "合法"):
            task_store.validate_git_name("developer..team")


if __name__ == "__main__":
    unittest.main()
