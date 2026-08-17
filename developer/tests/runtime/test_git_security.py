from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work import workspace_security
from ao_work.git_security import parse_github_repository_url
from ao_work.output import RuntimeErrorResult
from ao_work.workspace_init.service import WorkspaceInitializer


class GitSecurityTest(unittest.TestCase):
    def test_only_exact_github_repository_urls_are_accepted(self) -> None:
        for value in (
            "git@github.com:tapdata/tapdata.git",
            "ssh://git@github.com/tapdata/tapdata.git",
            "https://github.com/tapdata/tapdata.git",
        ):
            with self.subTest(value=value):
                self.assertEqual("tapdata/tapdata", parse_github_repository_url(value))
        for value in (
            "https://github.com.evil.test/tapdata/tapdata.git",
            "https://user@github.com/tapdata/tapdata.git",
            "https://github.com/tapdata/tapdata/extra",
            "https://github.com/tapdata%2Fother/tapdata.git",
            "ssh://git@github.com:22/tapdata/tapdata.git",
            "git@github.com.evil.test:tapdata/tapdata.git",
            "git://github.com/tapdata/tapdata.git",
        ):
            with self.subTest(value=value):
                self.assertEqual("", parse_github_repository_url(value))

    def test_url_rewrite_is_rejected_before_remote_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            install = root / "install"
            workspace.mkdir()
            initializer = WorkspaceInitializer(workspace, install)
            calls: list[list[str]] = []

            def fake_git(arguments: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
                calls.append(arguments)
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    "file:/tmp/config\turl.ssh://mirror/.insteadOf git@github.com:\n",
                    "",
                )

            with mock.patch.object(initializer, "_run_git", side_effect=fake_git):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    initializer._reject_git_url_rewrites(None)
            self.assertEqual("git_url_rewrite_forbidden", captured.exception.code)
            self.assertEqual(1, len(calls))
            self.assertNotIn("ls-remote", calls[0])

    def test_secondary_effective_push_url_cannot_hide_repository_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = root / "source"
            workspace.mkdir()
            source.mkdir()
            initializer = WorkspaceInitializer(workspace, root / "install")

            def fake_git(arguments: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
                output = "git@github.com:tapdata/tapdata.git\n"
                if arguments[2:] == ["remote", "get-url", "--push", "--all", "origin"]:
                    output += "git@github.com:attacker/repository.git\n"
                if arguments[2:] == ["config", "--get-all", "remote.origin.pushurl"]:
                    return subprocess.CompletedProcess(arguments, 1, "", "")
                return subprocess.CompletedProcess(arguments, 0, output, "")

            with mock.patch.object(initializer, "_run_git", side_effect=fake_git):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    initializer._validate_repository_remotes(source, "tapdata/tapdata")
            self.assertEqual("source_repository_mismatch", captured.exception.code)

    def test_three_raw_pushurls_cannot_hide_attacker_between_official_urls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            source = root / "source"
            workspace.mkdir()
            source.mkdir()
            initializer = WorkspaceInitializer(workspace, root / "install")
            official = "git@github.com:tapdata/tapdata.git\n"

            def fake_git(arguments: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
                if arguments[2:] == ["config", "--get-all", "remote.origin.pushurl"]:
                    return subprocess.CompletedProcess(
                        arguments,
                        0,
                        official
                        + "git@github.com:attacker/repository.git\n"
                        + official,
                        "",
                    )
                return subprocess.CompletedProcess(arguments, 0, official, "")

            with mock.patch.object(initializer, "_run_git", side_effect=fake_git):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    initializer._validate_repository_remotes(source, "tapdata/tapdata")
            self.assertEqual("source_repository_mismatch", captured.exception.code)

    def test_git_check_timeout_reports_command_and_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            initializer = WorkspaceInitializer(workspace, root / "install")
            with mock.patch(
                "subprocess.run",
                side_effect=subprocess.TimeoutExpired("git", 20.0),
            ):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    initializer._run_git(
                        ["ls-remote", "git@github.com:tapdata/tapdata.git", "HEAD"]
                    )
        error = captured.exception
        self.assertEqual("git_check_failed", error.code)
        self.assertIn("git ls-remote git@github.com:tapdata/tapdata.git HEAD", error.message)
        self.assertIn("已等待", error.message)
        self.assertEqual(
            "git ls-remote git@github.com:tapdata/tapdata.git HEAD",
            error.details["git_command"],
        )
        self.assertGreaterEqual(error.details["elapsed_seconds"], 0)
        self.assertEqual(20.0, error.details["git_timeout_seconds"])

    def test_security_git_timeout_reports_command_and_elapsed(self) -> None:
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("git", 20.0),
        ):
            with self.assertRaises(RuntimeErrorResult) as captured:
                workspace_security._run_git(
                    ["config", "--get-regexp", r"^url\..*insteadOf$"]
                )
        error = captured.exception
        self.assertEqual("git_check_failed", error.code)
        self.assertIn("config --get-regexp", error.message)
        self.assertIn("已等待", error.message)
        self.assertIn("git_command", error.details)
        self.assertGreaterEqual(error.details["elapsed_seconds"], 0)
        self.assertEqual(20.0, error.details["git_timeout_seconds"])
