from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work.installation import validate_install_root
from ao_work.output import RuntimeErrorResult


class InstallationIdentityTest(unittest.TestCase):
    @staticmethod
    def _validate(install: Path) -> Path:
        venv = install.resolve() / "developer" / ".venv"
        python = venv / "bin" / "python"
        with (
            mock.patch(
                "ao_work.installation.default_install_root",
                return_value=install.resolve(),
            ),
            mock.patch("ao_work.installation.sys.prefix", str(venv)),
            mock.patch("ao_work.installation.sys.executable", str(python)),
            mock.patch.dict(
                os.environ,
                {
                    "VIRTUAL_ENV": str(venv),
                    "PATH": f"{venv / 'bin'}{os.pathsep}/usr/bin:/bin",
                },
                clear=False,
            ),
        ):
            return validate_install_root()

    def _prepare(self, root: Path) -> Path:
        install = root / "install"
        for path in (
            install / "developer" / "AGENTS.md",
            install / "developer" / "bootstrap" / "ao-work",
            install / "developer" / "pyproject.toml",
            install / "developer" / "rules" / "ai-execution.md",
            install / "developer" / "runtime" / "src" / "ao_work" / "__init__.py",
            install / "developer" / "skills" / "example" / "SKILL.md",
            install / "developer" / "standards" / "README.md",
            install / "developer" / "uv.lock",
            install / "shared" / "README.md",
            install / "shared" / "integration" / "README.md",
            install / "shared" / "integration" / "task-to-pr-manifest.schema.json",
            install / "shared" / "integration" / "task-to-pr-event.schema.json",
            install / "shared" / "integration" / "task-to-pr-result.schema.json",
            install / "shared" / "standards" / "jira-comment-template.schema.json",
            install / "shared" / "standards" / "step-result-v2.schema.json",
            install / ".python-version",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test\n", encoding="utf-8")
        subprocess.run(["git", "init", "-b", "main", str(install)], check=True, capture_output=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(install),
                "remote",
                "add",
                "origin",
                "git@github.com:tapstate/agentic-ops.git",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(install), "config", "user.email", "test@example.test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(install), "config", "user.name", "AgenticOps Test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(install), "add", "."],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(install), "commit", "-m", "fixture"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(install),
                "sparse-checkout",
                "set",
                "--no-cone",
                "/developer/AGENTS.md",
                "/developer/bootstrap/",
                "/developer/pyproject.toml",
                "/developer/rules/",
                "/developer/runtime/",
                "/developer/skills/",
                "/developer/standards/",
                "/developer/uv.lock",
                "/shared/integration/",
                "/shared/standards/",
                "/.python-version",
            ],
            check=True,
            capture_output=True,
        )
        python = install / "developer" / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        python.chmod(0o700)
        (install / ".local").mkdir()
        self._record_head(install)
        return install

    @staticmethod
    def _record_head(install: Path) -> str:
        head = subprocess.run(
            ["git", "-C", str(install), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(install), "update-ref", "refs/remotes/origin/main", head],
            check=True,
            capture_output=True,
        )
        (install / ".local" / "current-ref").write_text(head + "\n", encoding="utf-8")
        return head

    def test_official_repository_and_developer_sparse_checkout_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self._prepare(root)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(install.resolve(), self._validate(install))
            self.assertFalse((install / "shared" / "README.md").exists())
            self.assertTrue((install / "shared" / "integration" / "README.md").is_file())

    def test_runtime_python_must_match_installation_venv(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            with (
                mock.patch(
                    "ao_work.installation.default_install_root",
                    return_value=install.resolve(),
                ),
                mock.patch.dict(
                    os.environ,
                    {"VIRTUAL_ENV": "/tmp/other-venv", "PATH": "/usr/bin:/bin"},
                    clear=True,
                ),
                self.assertRaises(RuntimeErrorResult) as captured,
            ):
                validate_install_root()
            self.assertEqual(
                "runtime_python_environment_mismatch", captured.exception.code
            )

    def test_broader_shared_sparse_checkout_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "sparse-checkout",
                    "set",
                    "--no-cone",
                    "/developer/AGENTS.md",
                    "/developer/bootstrap/",
                    "/developer/pyproject.toml",
                    "/developer/rules/",
                    "/developer/runtime/",
                    "/developer/skills/",
                    "/developer/standards/",
                    "/developer/uv.lock",
                    "/shared/",
                    "/.python-version",
                ],
                check=True,
                capture_output=True,
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual("developer_sparse_checkout_invalid", captured.exception.code)

    def test_untracked_shared_distribution_contamination_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            (install / "shared" / "integration" / "forbidden.py").write_text(
                "raise SystemExit\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual(
                "developer_shared_distribution_invalid", captured.exception.code
            )

    def test_hidden_non_admitted_shared_source_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "sparse-checkout",
                    "set",
                    "--no-cone",
                    "/developer/AGENTS.md",
                    "/developer/bootstrap/",
                    "/developer/pyproject.toml",
                    "/developer/rules/",
                    "/developer/runtime/",
                    "/developer/skills/",
                    "/developer/standards/",
                    "/developer/uv.lock",
                    "/shared/",
                    "/.python-version",
                ],
                check=True,
                capture_output=True,
            )
            forbidden = install / "shared" / "hidden" / "entry.sh"
            forbidden.parent.mkdir()
            forbidden.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(install), "add", "shared/hidden/entry.sh"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(install), "commit", "-m", "add forbidden shared path"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "sparse-checkout",
                    "set",
                    "--no-cone",
                    "/developer/AGENTS.md",
                    "/developer/bootstrap/",
                    "/developer/pyproject.toml",
                    "/developer/rules/",
                    "/developer/runtime/",
                    "/developer/skills/",
                    "/developer/standards/",
                    "/developer/uv.lock",
                    "/shared/integration/",
                    "/shared/standards/",
                    "/.python-version",
                ],
                check=True,
                capture_output=True,
            )
            self._record_head(install)
            self.assertFalse(forbidden.exists(), "非准入路径应隐藏在 sparse 工作树之外")
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual("developer_shared_source_invalid", captured.exception.code)

    def test_executable_shared_source_asset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            schema = "shared/integration/task-to-pr-event.schema.json"
            subprocess.run(
                ["git", "-C", str(install), "update-index", "--chmod=+x", schema],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(install), "commit", "-m", "make shared executable"],
                check=True,
                capture_output=True,
            )
            self._record_head(install)
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual("developer_shared_source_invalid", captured.exception.code)

    def test_tests_and_fake_producer_are_not_valid_developer_distribution_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            tests = install / "developer" / "tests" / "fixtures"
            tests.mkdir(parents=True)
            (tests / "task_to_pr_producer.py").write_text(
                "raise SystemExit('fake')\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual(
                "developer_distribution_contaminated", captured.exception.code
            )

    def test_wrong_origin_and_non_sparse_clone_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self._prepare(root)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "remote",
                    "set-url",
                    "origin",
                    str(root / "different"),
                ],
                check=True,
                capture_output=True,
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual("install_origin_mismatch", captured.exception.code)

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "remote",
                    "set-url",
                    "origin",
                    "git@github.com:tapstate/agentic-ops.git",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(install), "sparse-checkout", "disable"],
                check=True,
                capture_output=True,
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertIn(
                captured.exception.code,
                {"install_identity_check_failed", "developer_sparse_checkout_invalid"},
            )

    def test_git_transport_rewrite_and_pushurl_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self._prepare(root)
            official = "git@github.com:tapstate/agentic-ops.git"
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "config",
                    f"url.{root / 'rewritten'}.insteadOf",
                    official,
                ],
                check=True,
                capture_output=True,
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual(
                "install_transport_rewrite_forbidden", captured.exception.code
            )

            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "config",
                    "--unset-all",
                    f"url.{root / 'rewritten'}.insteadOf",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(install),
                    "remote",
                    "set-url",
                    "--push",
                    "origin",
                    str(root / "push-target"),
                ],
                check=True,
                capture_output=True,
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual(
                "install_transport_rewrite_forbidden", captured.exception.code
            )

    def test_all_install_identity_override_environments_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self._prepare(root)
            for variable_name in (
                "AGENTIC_OPS_TEST_MODE",
                "AGENTIC_OPS_TEST_LAUNCHER",
                "AGENTIC_OPS_TEST_EXPECTED_REPOSITORY",
                "AGENTIC_OPS_REPO_URL",
                "AGENTIC_OPS_GITHUB_REPOSITORY",
                "AGENTIC_OPS_BRANCH",
            ):
                with self.subTest(variable_name=variable_name), mock.patch.dict(
                    os.environ,
                    {variable_name: "override"},
                    clear=True,
                ), self.assertRaises(RuntimeErrorResult) as captured:
                    self._validate(install)
                self.assertEqual(
                    "install_identity_override_forbidden", captured.exception.code
                )

    def test_checkout_integrity_rejects_dirty_and_ref_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self._prepare(root)
            (install / "developer" / "AGENTS.md").write_text(
                "tampered\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual(
                "install_tracked_changes_forbidden", captured.exception.code
            )

            subprocess.run(
                ["git", "-C", str(install), "restore", "developer/AGENTS.md"],
                check=True,
                capture_output=True,
            )
            (install / ".local" / "current-ref").write_text(
                "0" * 40 + "\n", encoding="utf-8"
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual("install_ref_integrity_invalid", captured.exception.code)

    def test_source_and_fabricated_directory_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fabricated = root / "fabricated"
            (fabricated / "developer").mkdir(parents=True)
            with self.assertRaises(RuntimeErrorResult) as captured:
                self._validate(fabricated)
            self.assertEqual("install_root_identity_invalid", captured.exception.code)

            source = root / "source"
            source.mkdir()
            (source / ".agentic-ops-source").write_text("maintainer\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                self._validate(source)
            self.assertEqual("install_root_source_rejected", captured.exception.code)

    def _checkout_develop(self, install: Path) -> str:
        subprocess.run(
            ["git", "-C", str(install), "checkout", "-b", "develop"],
            check=True,
            capture_output=True,
        )
        (install / "developer" / "AGENTS.md").write_text("develop\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(install), "add", "developer/AGENTS.md"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(install), "commit", "-m", "develop ahead"],
            check=True,
            capture_output=True,
        )
        head = subprocess.run(
            ["git", "-C", str(install), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(install), "update-ref", "refs/remotes/origin/develop", head],
            check=True,
            capture_output=True,
        )
        (install / ".local" / "current-ref").write_text(head + "\n", encoding="utf-8")
        return head

    @staticmethod
    def _write_verification_marker(install: Path) -> None:
        (install / ".agentic-ops").mkdir(exist_ok=True)
        (install / ".agentic-ops" / "verification-only").write_text(
            '{"verification_only": true}\n', encoding="utf-8"
        )

    def test_verification_install_on_non_main_branch_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            self._checkout_develop(install)
            self._write_verification_marker(install)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(install.resolve(), self._validate(install))

    def test_non_main_install_without_verification_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            self._checkout_develop(install)
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual("install_identity_check_failed", captured.exception.code)

    def test_verification_install_with_unreachable_head_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = self._prepare(Path(temporary))
            self._checkout_develop(install)
            self._write_verification_marker(install)
            subprocess.run(
                ["git", "-C", str(install), "update-ref", "-d", "refs/remotes/origin/develop"],
                check=True,
                capture_output=True,
            )
            with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(
                RuntimeErrorResult
            ) as captured:
                self._validate(install)
            self.assertEqual("verification_branch_unreachable", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
