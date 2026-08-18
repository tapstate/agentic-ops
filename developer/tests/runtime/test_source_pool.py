from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work.output import RuntimeErrorResult
from ao_work.workspace import (
    normalize_worktree_from_branch,
    repository_short_name,
    task_worktree_path,
    validate_source_pool_root,
)
from ao_work.workspace_init.service import WorkspaceInitializer


class SourcePoolPathTest(unittest.TestCase):
    def test_task_worktree_path_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = Path(temporary) / "pool"
            pool.mkdir()
            path = task_worktree_path(pool, "TAP-123", "feature/x", "tapdata/tapdata")
            self.assertEqual(
                (pool / "TAP-123" / "feature-x" / "tapdata").resolve(), path
            )

    def test_from_branch_slash_normalized(self) -> None:
        self.assertEqual("feature-x", normalize_worktree_from_branch("feature/x"))
        self.assertEqual("release-2.0", normalize_worktree_from_branch("release/2.0"))

    def test_from_branch_path_traversal_rejected(self) -> None:
        for value in ("../escape", "a/../../b", "-leading", "has..dots", "a b", "@{x}"):
            with self.subTest(value=value), self.assertRaises(RuntimeErrorResult):
                normalize_worktree_from_branch(value)

    def test_repository_short_name(self) -> None:
        self.assertEqual("tapdata", repository_short_name("tapdata/tapdata"))
        with self.assertRaises(RuntimeErrorResult):
            repository_short_name("no-slash")
        with self.assertRaises(RuntimeErrorResult):
            repository_short_name("owner/bad name")

    def test_invalid_jira_key_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = Path(temporary) / "pool"
            pool.mkdir()
            for key in ("TAP", "tap-0", "TAP-01", "TAP-", "-1", "TAP/1"):
                with self.subTest(key=key), self.assertRaises(RuntimeErrorResult):
                    task_worktree_path(pool, key, "main", "tapdata/tapdata")

    def test_pool_root_rejects_home_and_source_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(RuntimeErrorResult):
                validate_source_pool_root(Path.home())
            source_marker = root / "fake-source"
            source_marker.mkdir()
            (source_marker / ".agentic-ops-source").write_text("maintainer\n", encoding="utf-8")
            (source_marker / "maintainer").mkdir()
            (source_marker / "maintainer" / "AGENTS.md").write_text("# maintainer\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult):
                validate_source_pool_root(source_marker)

    def test_pool_root_missing_requires_allow_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = Path(temporary) / "pool"
            # 默认必须存在：不存在时阻断。
            with self.assertRaises(RuntimeErrorResult) as captured:
                validate_source_pool_root(pool)
            self.assertEqual("source_pool_root_invalid", captured.exception.code)
            # allow_missing=True：允许不存在（init 会自动创建）。
            validated = validate_source_pool_root(pool, allow_missing=True)
            self.assertEqual(pool.resolve(), validated)
            # 不是目录（是文件）时即使 allow_missing 也阻断。
            pool_file = Path(temporary) / "not-a-dir"
            pool_file.write_text("x\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult):
                validate_source_pool_root(pool_file, allow_missing=True)


class SourcePoolRootRequiredTest(unittest.TestCase):
    def test_prepare_blocks_when_pool_root_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            install.mkdir()
            (install / "developer" / "standards" / "connections").mkdir(parents=True)
            (install / "developer" / "standards" / "connections" / "tap.yaml").write_text(
                "connection_id: tap\nbase_url: https://jira.example.test\n"
                "auth:\n  email_env: E\n  token_env: T\n",
                encoding="utf-8",
            )
            profile_dir = install / "developer" / "standards" / "projects" / "tapdata"
            profile_dir.mkdir(parents=True)
            (profile_dir / "profile.yaml").write_text(
                "profile_id: tapdata\nconnection_id: tap\n"
                "jira:\n  project_key: TAP\n  task_query: project = TAP\n"
                "repositories:\n  default: tapdata/tapdata\n",
                encoding="utf-8",
            )
            (install / "developer" / "AGENTS.md").write_text("# developer\n", encoding="utf-8")
            workspace = root / "workspace"
            workspace.mkdir()
            initializer = WorkspaceInitializer(workspace, install)
            with self.assertRaises(RuntimeErrorResult) as captured:
                initializer.prepare("tapdata", "agent-1")
            self.assertEqual("source_pool_root_invalid", captured.exception.code)

    def test_prepare_allows_missing_pool_root_dir(self) -> None:
        # 池根已配置但目录不存在：prepare 不阻断（apply 自动创建），池模式生效。
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            install.mkdir()
            (install / "developer" / "standards" / "connections").mkdir(parents=True)
            (install / "developer" / "standards" / "connections" / "tap.yaml").write_text(
                "connection_id: tap\nbase_url: https://jira.example.test\n"
                "auth:\n  email_env: E\n  token_env: T\n",
                encoding="utf-8",
            )
            profile_dir = install / "developer" / "standards" / "projects" / "tapdata"
            profile_dir.mkdir(parents=True)
            (profile_dir / "profile.yaml").write_text(
                "profile_id: tapdata\nconnection_id: tap\n"
                "jira:\n  project_key: TAP\n  task_query: project = TAP\n"
                "repositories:\n"
                "  default: tapdata/tapdata\n"
                "  list:\n"
                "    - tapdata/tapdata\n",
                encoding="utf-8",
            )
            (install / "developer" / "AGENTS.md").write_text("# developer\n", encoding="utf-8")
            user_config = install / "user" / "config.yaml"
            user_config.parent.mkdir(parents=True)
            pool = root / "source-pool" / "nested" / "pool"
            user_config.write_text(
                f"source_pool_root: {pool}\n",
                encoding="utf-8",
            )
            workspace = root / "workspace"
            workspace.mkdir()
            initializer = WorkspaceInitializer(workspace, install)
            candidate = initializer.prepare("tapdata", "agent-1")
            self.assertTrue(candidate.pool_mode)
            self.assertEqual(pool.resolve(), candidate.source_pool_root.resolve())

            def fake_run_git(initializer_obj, command, *, timeout=None):
                if "--get-regexp" in command:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[0] == "-C" and command[2:] == [
                    "config",
                    "--get-all",
                    "remote.origin.pushurl",
                ]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[0] == "-C":
                    repo_dir = Path(command[1])
                    short = repo_dir.name
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"git@github.com:tapdata/{short}.git\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "HEAD\n", "")

            def fake_run_git_streaming(initializer_obj, command, *, stall_warn_interval=30.0):
                target = Path(command[-1])
                (target / ".git").mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(command, 0, "", "")

            # apply 阶段自动创建池根并写入容器 README。
            with (
                mock.patch.object(
                    WorkspaceInitializer, "_run_git", new=fake_run_git
                ),
                mock.patch.object(
                    WorkspaceInitializer, "_run_git_streaming", new=fake_run_git_streaming
                ),
            ):
                result, skipped = initializer._prepare_pool_members(candidate)
            self.assertIn("adopted=0", result)
            self.assertEqual([], skipped)
            self.assertTrue((pool / "README.md").is_file())
            self.assertTrue((pool / "tapdata" / "tapdata" / ".git").exists())


class SourcePoolMemberPrepareTest(unittest.TestCase):
    def test_pool_members_adopt_existing_and_clone_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            install.mkdir()
            (install / "developer" / "standards" / "connections").mkdir(parents=True)
            (install / "developer" / "standards" / "connections" / "tap.yaml").write_text(
                "connection_id: tap\nbase_url: https://jira.example.test\n"
                "auth:\n  email_env: E\n  token_env: T\n",
                encoding="utf-8",
            )
            profile_dir = install / "developer" / "standards" / "projects" / "tapdata"
            profile_dir.mkdir(parents=True)
            (profile_dir / "profile.yaml").write_text(
                "profile_id: tapdata\nconnection_id: tap\n"
                "jira:\n  project_key: TAP\n  task_query: project = TAP\n"
                "repositories:\n"
                "  default: tapdata/tapdata\n"
                "  list:\n"
                "    - tapdata/tapdata\n"
                "    - tapdata/tapdata-web\n",
                encoding="utf-8",
            )
            (install / "developer" / "AGENTS.md").write_text("# developer\n", encoding="utf-8")
            pool = root / "pool"
            pool.mkdir()
            # 已有池成员：tapdata/tapdata（普通克隆，应被认领）。
            existing = pool / "tapdata" / "tapdata"
            existing.mkdir(parents=True)
            (existing / ".git").mkdir()

            workspace = root / "workspace"
            workspace.mkdir()
            initializer = WorkspaceInitializer(workspace, install)

            def fake_run_git(initializer_obj, command, *, timeout=None):
                if "--get-regexp" in command:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[0] == "-C" and command[2:] == ["rev-parse", "--is-shallow-repository"]:
                    return subprocess.CompletedProcess(command, 0, "false\n", "")
                if command[0] == "-C" and command[2:] == ["config", "--get-all", "remote.origin.pushurl"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[0] == "-C":
                    repo_dir = Path(command[1])
                    short = repo_dir.name
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"git@github.com:tapdata/{short}.git\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "HEAD\n", "")

            def fake_run_git_streaming(initializer_obj, command, *, stall_warn_interval=30.0):
                target = Path(command[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                mock.patch.object(
                    WorkspaceInitializer, "_run_git", new=fake_run_git
                ),
                mock.patch.object(
                    WorkspaceInitializer, "_run_git_streaming", new=fake_run_git_streaming
                ),
            ):
                candidate = initializer.prepare(
                    "tapdata", "agent-1", source_pool_root=str(pool)
                )
                result, _ = initializer._prepare_pool_members(candidate)
            self.assertIn("adopted=1", result)
            self.assertIn("cloned=1", result)
            self.assertTrue((pool / "README.md").is_file())
            self.assertTrue((pool / "tapdata" / "tapdata-web" / ".git").exists())

    def test_pool_member_shallow_auto_unshallowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            install.mkdir()
            (install / "developer" / "standards" / "connections").mkdir(parents=True)
            (install / "developer" / "standards" / "connections" / "tap.yaml").write_text(
                "connection_id: tap\nbase_url: https://jira.example.test\n"
                "auth:\n  email_env: E\n  token_env: T\n",
                encoding="utf-8",
            )
            profile_dir = install / "developer" / "standards" / "projects" / "tapdata"
            profile_dir.mkdir(parents=True)
            (profile_dir / "profile.yaml").write_text(
                "profile_id: tapdata\nconnection_id: tap\n"
                "jira:\n  project_key: TAP\n  task_query: project = TAP\n"
                "repositories:\n  default: tapdata/tapdata\n",
                encoding="utf-8",
            )
            (install / "developer" / "AGENTS.md").write_text("# developer\n", encoding="utf-8")
            pool = root / "pool"
            existing = pool / "tapdata" / "tapdata"
            existing.mkdir(parents=True)
            (existing / ".git").mkdir()
            workspace = root / "workspace"
            workspace.mkdir()
            initializer = WorkspaceInitializer(workspace, install)
            unshallowed = []

            def fake_run_git(initializer_obj, command, *, timeout=None):
                if "--get-regexp" in command:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[0] == "-C" and command[2:] == ["rev-parse", "--is-shallow-repository"]:
                    return subprocess.CompletedProcess(command, 0, "true\n", "")
                if command[0] == "-C" and command[2:] == ["config", "--get-all", "remote.origin.pushurl"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[0] == "-C":
                    return subprocess.CompletedProcess(
                        command, 0, "git@github.com:tapdata/tapdata.git\n", ""
                    )
                return subprocess.CompletedProcess(command, 0, "HEAD\n", "")

            def fake_run_git_streaming(initializer_obj, command, *, stall_warn_interval=30.0):
                if any("unshallow" in str(item) for item in command):
                    unshallowed.append(command)
                return subprocess.CompletedProcess(command, 0, "", "")

            with (
                mock.patch.object(
                    WorkspaceInitializer, "_run_git", new=fake_run_git
                ),
                mock.patch.object(
                    WorkspaceInitializer, "_run_git_streaming", new=fake_run_git_streaming
                ),
            ):
                candidate = initializer.prepare(
                    "tapdata", "agent-1", source_pool_root=str(pool)
                )
                initializer._prepare_pool_members(candidate)
            self.assertEqual(1, len(unshallowed))
            self.assertIn("--unshallow", unshallowed[0])


if __name__ == "__main__":
    unittest.main()
