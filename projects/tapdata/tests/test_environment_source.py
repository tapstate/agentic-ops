"""真实临时 Git 仓库验证空闲源码准备与任务交接，不访问业务仓库。"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_station_lifecycle as fixture
from station_fixture import task_state_snapshot
from projects.tapdata.scripts import environment_source as subject
from workflow import station, station_clean, task_store


class SourcePreparationTests(unittest.TestCase):
    setUp = fixture.StationTests.setUp
    write = fixture.StationTests.write
    git = fixture.StationTests.git
    prepare_engineering = fixture.StationTests.prepare_engineering
    takeover = fixture.StationTests.takeover

    def test_empty_station_prepare_repeat_and_fast_forward_without_task(self):
        self.prepare_engineering(count=2)
        original = task_state_snapshot(self.ws)
        result = subject.prepare(self.ws)
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["repositories"]["tapdata/tapdata-common-lib"]["branch"], "main")
        self.assertEqual(task_state_snapshot(self.ws), original)
        self.assertFalse((self.ws / ".agenticops/operation.json").exists())
        self.assertFalse(list((self.ws / "runtime").iterdir()))
        repo = self.ws / "source/tapdata/tapdata"
        self.assertFalse((repo / ".git/objects/info/alternates").exists())
        subject.prepare(self.ws)
        (self.seed / "file.txt").write_text("advanced")
        self.git(self.seed, "commit", "-am", "advance")
        self.git(self.seed, "push", str(self.remote), "develop")
        result = subject.prepare(self.ws)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), result["repositories"]["tapdata/tapdata"]["head"])
        self.assertEqual((repo / "file.txt").read_text(), "advanced")

    def test_dirty_ignored_and_wrong_branch_preserved(self):
        self.prepare_engineering()
        subject.prepare(self.ws)
        repo = self.ws / "source/tapdata/tapdata"
        before = self.git(repo, "rev-parse", "HEAD")
        (repo / "file.txt").write_text("local")
        with self.assertRaisesRegex(ValueError, "未提交"):
            subject.prepare(self.ws)
        self.assertEqual((repo / "file.txt").read_text(), "local")
        self.git(repo, "restore", "file.txt")
        (repo / ".git/info/exclude").write_text("cache\n")
        (repo / "cache").write_text("retain")
        with self.assertRaisesRegex(ValueError, "ignored"):
            subject.prepare(self.ws)
        (repo / "cache").unlink()
        self.git(repo, "checkout", "-b", "feature")
        with self.assertRaisesRegex(ValueError, "分支"):
            subject.prepare(self.ws)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git(repo, "branch", "--show-current"), "feature")

    def test_divergence_and_origin_rejected(self):
        self.prepare_engineering()
        subject.prepare(self.ws)
        repo = self.ws / "source/tapdata/tapdata"
        self.git(repo, "config", "user.name", "Test")
        self.git(repo, "config", "user.email", "test@example.invalid")
        (repo / "local.txt").write_text("local")
        self.git(repo, "add", "local.txt")
        self.git(repo, "commit", "-m", "local")
        before = self.git(repo, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "独有提交"):
            subject.prepare(self.ws)
        self.assertEqual(self.git(repo, "rev-parse", "HEAD"), before)
        self.git(repo, "remote", "set-url", "origin", str(self.root / "other.git"))
        with self.assertRaisesRegex(ValueError, "origin"):
            subject.prepare(self.ws)

    def test_failed_download_does_not_publish_partial_clone(self):
        self.prepare_engineering()
        original = subject.source.git
        def fail(path, *args, **kw):
            if args[0] == "checkout":
                raise ValueError("injected checkout failure")
            return original(path, *args, **kw)
        with mock.patch.object(subject.source, "git", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "Git 操作失败"):
                subject.prepare(self.ws)
        self.assertFalse((self.ws / "source/tapdata/tapdata").exists())
        self.assertEqual(subject.prepare(self.ws)["status"], "prepared")

    def test_partial_publish_can_resume_from_git_facts(self):
        self.prepare_engineering(count=2)
        original = subject.source.git
        checkouts = 0
        def fail(path, *args, **kw):
            nonlocal checkouts
            if args[0] == "checkout":
                checkouts += 1
                if checkouts == 2:
                    raise ValueError("second checkout failed")
            return original(path, *args, **kw)
        with mock.patch.object(subject.source, "git", side_effect=fail):
            with self.assertRaises(ValueError):
                subject.prepare(self.ws)
        self.assertTrue((self.ws / "source/tapdata/tapdata/.git").is_dir())
        self.assertFalse((self.ws / "source/tapdata/tapdata-common-lib").exists())
        self.assertEqual(len(subject.prepare(self.ws)["repositories"]), 2)

    def test_process_exit_leaves_recoverable_runtime_not_unknown_source(self):
        self.prepare_engineering(count=2)
        current = task_state_snapshot(self.ws)
        code = """
import os, sys
from unittest import mock
from projects.tapdata.scripts import environment_source as subject
original = subject.source.git
count = 0
def interrupted(path, *args, **kwargs):
    global count
    if args[0] == 'checkout':
        count += 1
        if count == int(sys.argv[2]):
            os._exit(73)
    return original(path, *args, **kwargs)
with mock.patch.object(subject.source, 'git', side_effect=interrupted):
    subject.prepare(sys.argv[1])
"""
        # 第一次在发布前终止；第二次保留第一仓发布结果后终止。
        for stop_at in (1, 2):
            result = subprocess.run([sys.executable, "-c", code, str(self.ws), str(stop_at)],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 73, result.stderr)
        residuals = sorted((self.ws / "runtime").glob("tapdata-source-preparation-*"))
        self.assertTrue(residuals)
        self.assertTrue((self.ws / "source/tapdata/tapdata/.git").is_dir())
        self.assertFalse(list((self.ws / "source/tapdata").glob(".prepare-*")))
        prepared = subject.prepare(self.ws)
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(len(prepared["preparation_residuals"]), len(residuals))
        self.assertIn("保全", prepared["recovery_hint"])
        self.assertTrue(all(path.is_dir() for path in residuals))
        self.assertEqual(task_state_snapshot(self.ws), current)
        self.assertFalse((self.ws / ".agenticops/operation.json").exists())
        # 使用真实清理入口核验：含 Git 的残留保留，不能绕过嵌套仓库保护。
        result = station_clean.execute(self.ws, "work-clean", None, "run-" + "a" * 32,
                                       task_store.read_current(self.ws)["revision"], "op-source-clean",
                                       {"clear_config": []})
        self.assertEqual(result["status"], "done")
        self.assertFalse(result["cleanup_result"]["complete"])
        self.assertTrue(any("嵌套 Git" in item["reason"] for item in result["cleanup_result"]["residuals"]))
        self.assertTrue(any(path.is_dir() for path in residuals))
        self.assertTrue((self.ws / "source/tapdata/tapdata/.git").is_dir())

    def cli_error(self):
        from projects.tapdata.scripts.test_environment import main
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            code = main(["prepare-source", "--station", str(self.ws)])
        self.assertEqual(code, 2)
        return json.loads(output.getvalue())["error"]

    def test_cli_reports_actionable_source_errors_and_preserves_work(self):
        self.prepare_engineering()
        subject.prepare(self.ws)
        repo = self.ws / "source/tapdata/tapdata"
        (repo / "file.txt").write_text("local")
        self.assertIn("未提交修改", self.cli_error())
        self.assertEqual((repo / "file.txt").read_text(), "local")
        self.git(repo, "restore", "file.txt")
        (repo / ".git/info/exclude").write_text("cache\n")
        (repo / "cache").write_text("retain")
        self.assertIn("ignored", self.cli_error())
        (repo / "cache").unlink()
        self.git(repo, "checkout", "-b", "feature")
        self.assertIn("分支", self.cli_error())
        self.git(repo, "checkout", "develop")
        self.git(repo, "config", "user.name", "Test")
        self.git(repo, "config", "user.email", "test@example.invalid")
        (repo / "file.txt").write_text("local commit")
        self.git(repo, "commit", "-am", "local")
        self.assertIn("独有提交", self.cli_error())
        self.git(repo, "remote", "set-url", "origin", "https://private:secret@example.invalid/repo")
        error = self.cli_error()
        self.assertIn("origin", error)
        self.assertNotIn("secret", error)
        self.assertEqual((repo / "file.txt").read_text(), "local commit")

    def test_cli_hides_git_details_and_preserves_unknown_source(self):
        self.prepare_engineering()
        with mock.patch.object(subject.source, "git", side_effect=ValueError("private-token stderr")):
            error = self.cli_error()
        self.assertIn("Git 操作失败", error)
        self.assertNotIn("private-token", error)
        unknown = self.ws / "source/tapdata/.prepare-unknown"
        unknown.mkdir(parents=True)
        (unknown / "keep").write_text("retained")
        self.assertIn("源码布局", self.cli_error())
        self.assertEqual((unknown / "keep").read_text(), "retained")

    def test_runtime_blocks_takeover_until_cleaned_and_active_task_blocks_prepare(self):
        self.prepare_engineering()
        subject.prepare(self.ws)
        directory = self.ws / "runtime/tapdata-test-env"
        directory.mkdir()
        (directory / "owner.json").write_text(json.dumps({"station_id": "a" * 32}))
        with self.assertRaises(ValueError):
            self.takeover()
        self.assertIsNone(task_store.read_task(self.ws))
        self.assertFalse((self.ws / ".agenticops/operation.json").exists())
        # 复用真实无任务清理，只替换 Docker 边界；不直接删除运行区或修改状态。
        original = subprocess.run
        def docker_empty(args, **kwargs):
            if args[0] == "docker":
                return subprocess.CompletedProcess(args, 0, "", "")
            return original(args, **kwargs)
        with mock.patch("workflow.station_resources.subprocess.run", side_effect=docker_empty):
            result = station_clean.execute(self.ws, "work-clean", None, "run-" + "a" * 32,
                                           task_store.read_current(self.ws)["revision"], "op-idle-clean",
                                           {"clear_config": []})
        self.assertEqual(result["status"], "done")
        self.assertFalse(list((self.ws / "runtime").iterdir()))
        self.takeover()
        with self.assertRaisesRegex(ValueError, "无当前任务"):
            subject.prepare(self.ws)

    def test_environment_uses_the_same_station_lock(self):
        from projects.tapdata.scripts.test_environment import Environment
        env = Environment(self.ws)
        # 子进程独立打开同一个目录锁，不能借用父进程线程重入。
        code = "import os,fcntl,sys; f=os.open(sys.argv[1],os.O_RDONLY); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
        with env.lock():
            result = subprocess.run([sys.executable, "-c", code, str(self.ws / ".agenticops")], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([sys.executable, "-c", code, str(self.ws / ".agenticops")], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pending_cleanup_allows_observation_and_uninstall_not_new_deployment(self):
        from projects.tapdata.scripts.test_environment import Environment, EnvironmentError
        from workflow import station_operation
        env = Environment(self.ws)
        with task_store.task_state_lock(self.ws):
            pending = station_operation.begin(self.ws, "work-clean", "op-clean-pending",
                                              task_store.read_current(self.ws)["revision"],
                                              {"clear_config": []}, "run-" + "a" * 32)
        original = (self.ws / ".agenticops/operation.json").read_bytes()
        for name in ("status", "list", "uninstall"):
            with env.lock(name):
                self.assertEqual((self.ws / ".agenticops/operation.json").read_bytes(), original)
        for name in ("deploy", "update", "prepare-source", "configure"):
            with self.assertRaisesRegex(EnvironmentError, "未完成操作"):
                with env.lock(name):
                    self.fail("pending cleanup must block mutation")
        self.assertNotEqual(pending["status"], "done")


if __name__ == "__main__":
    unittest.main()
