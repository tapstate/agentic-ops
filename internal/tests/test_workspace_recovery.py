"""一次性旧工位恢复，不导入旧任务、不删除原材料。"""
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest import mock

from internal import workspace_recovery as recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.ws, self.product, self.backup = root / "workspace", root / "product", root / "backup"
        self.ws.mkdir()
        self.product.mkdir()
        self.write(".agenticops/workspace.json", {"schema_version": 2, "project": "tapdata",
            "product_root": str(self.product), "workspace_id": "old-id"})
        self.write(".agenticops/tasks/index.json", {"schema_version": 1, "project": "tapdata", "tasks": {}})
        self.write(".agenticops/git-ref-cache-v2.json", {"schema_version": 2, "roots": {}})
        self.write(".agenticops/git-ref-cache-v2.json.lock", {})
        self.write(".agenticops/tasks.lock", {})
        artifacts = []
        for name in sorted(recovery.WIRES):
            path = self.ws / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if "/skills/" in name:
                path.symlink_to("../../../product/skills/" + path.name)
                artifacts.append({"path": name, "kind": "symlink", "target": str(path.readlink())})
            else:
                path.write_text("generated")
                artifacts.append({"path": name, "kind": "file", "sha256": recovery.hashlib.sha256(path.read_bytes()).hexdigest()})
        self.write(".agenticops/init.json", {"schema_version": 2, "workspace_state_epoch": 2,
            "product_ref": recovery.OLD_REF, "artifacts": artifacts})
        for name in ("source/repo/code", "config/secret", "archive/old", "user.txt", ".claude/user.json"):
            self.write(name, {"keep": True})

    def write(self, name, value):
        path = self.ws / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def plan(self):
        return recovery.plan(self.ws, self.backup, self.product)

    def test_export_preserves_materials_and_retry(self):
        plan = self.plan()
        result = recovery.apply(self.backup, plan["digest"], True)
        self.assertEqual(result["status"], "exported")
        self.assertFalse((self.ws / ".agenticops").exists())
        self.assertTrue((self.backup / ".agenticops/git-ref-cache-v2.json").is_file())
        for name in ("source/repo/code", "config/secret", "archive/old", "user.txt", ".claude/user.json"):
            self.assertTrue((self.ws / name).is_file())
        recovery.apply(self.backup, plan["digest"], True)

    def test_active_task_rejected(self):
        self.write(".agenticops/tasks/index.json", {"schema_version": 1, "project": "tapdata", "tasks": {"TAP-1": {}}})
        with self.assertRaises(ValueError):
            self.plan()

    def test_unknown_file_and_modified_wire_rejected(self):
        self.write(".agenticops/unknown", {})
        with self.assertRaises(ValueError):
            self.plan()
        (self.ws / ".agenticops/unknown").unlink()
        (self.ws / "AGENTS.md").write_text("user changes")
        with self.assertRaises(ValueError):
            self.plan()

    def test_drift_after_plan_preserves_everything(self):
        plan = self.plan()
        self.write(".agenticops/late", {})
        with self.assertRaises(ValueError):
            recovery.apply(self.backup, plan["digest"], True)
        self.assertTrue((self.ws / "AGENTS.md").is_file())

    def test_interrupted_rename_resumes_from_backup(self):
        plan = self.plan()
        rename = recovery.os.rename
        calls = []
        def interrupt(*args, **kwargs):
            calls.append(args)
            if len(calls) == 3:
                raise OSError("injected interruption")
            return rename(*args, **kwargs)
        with mock.patch.object(recovery.os, "rename", side_effect=interrupt):
            with self.assertRaises(OSError):
                recovery.apply(self.backup, plan["digest"], True)
        recovery.apply(self.backup, plan["digest"], True)
        self.assertFalse((self.ws / ".agenticops").exists())

    def test_retry_never_touches_new_workspace(self):
        plan = self.plan()
        recovery.apply(self.backup, plan["digest"], True)
        self.write(".agenticops/current-task.json", {"current": None})
        with self.assertRaises(ValueError):
            recovery.apply(self.backup, plan["digest"], True)
        self.assertTrue((self.ws / ".agenticops/current-task.json").is_file())

    def test_state_exported_receipt_failure_recovers(self):
        plan = self.plan()
        original = recovery.WorkspaceDirectory.write_json_atomic
        def fail_receipt(tree, path, document):
            if path == "receipt.json":
                raise OSError("receipt interrupted")
            return original(tree, path, document)
        with mock.patch.object(recovery.WorkspaceDirectory, "write_json_atomic", fail_receipt):
            with self.assertRaises(OSError):
                recovery.apply(self.backup, plan["digest"], True)
        self.assertFalse((self.ws / ".agenticops").exists())
        recovery.apply(self.backup, plan["digest"], True)
        self.assertTrue((self.backup / "receipt.json").is_file())

    def test_active_cache_or_task_lock_prevents_any_move(self):
        plan = self.plan()
        for name in ("tasks.lock", "git-ref-cache-v2.json.lock"):
            process = subprocess.Popen([sys.executable, "-c",
                "import fcntl,sys; f=open(sys.argv[1]); fcntl.flock(f,fcntl.LOCK_EX); print('ready',flush=True); sys.stdin.read()",
                str(self.ws / ".agenticops" / name)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "ready")
                with self.assertRaises(BlockingIOError):
                    recovery.apply(self.backup, plan["digest"], True)
                self.assertTrue((self.ws / "AGENTS.md").is_file())
            finally:
                process.communicate(timeout=5)

    def test_confirmation_and_stopped_writers_required(self):
        plan = self.plan()
        for digest, stopped in (("wrong", True), (plan["digest"], False)):
            with self.assertRaises(ValueError):
                recovery.apply(self.backup, digest, stopped)
        self.assertTrue((self.ws / "AGENTS.md").is_file())

    def test_symlink_parent_rejected(self):
        external = self.product / "external"
        external.mkdir()
        (self.ws / ".agenticops/tasks/index.json").unlink()
        (self.ws / ".agenticops/tasks").rmdir()
        (self.ws / ".agenticops/tasks").symlink_to(external)
        with self.assertRaises(ValueError):
            self.plan()


if __name__ == "__main__":
    unittest.main()
