from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from ao_work.output import RuntimeErrorResult
from ao_work.version import inspect_version


class VersionTest(unittest.TestCase):
    def _prepare(self, temporary: str, *, tag: bool = False) -> tuple[Path, str]:
        install = Path(temporary) / "install"
        metadata = install / ".local" / "installation.json"
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            json.dumps({"schema_version": 1, "installed_at": "2026-08-22T00:00:00Z"}),
            encoding="utf-8",
        )
        metadata.chmod(0o600)
        project = install / "developer" / "pyproject.toml"
        project.parent.mkdir(parents=True)
        project.write_text("[project]\nversion = '1.2.3'\n", encoding="utf-8")
        subprocess.run(["git", "init", "-b", "main", str(install)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(install), "config", "user.email", "test@example.test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(install), "config", "user.name", "AgenticOps Test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(install), "add", "developer/pyproject.toml"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(install), "commit", "-m", "fixture"], check=True, capture_output=True)
        if tag:
            subprocess.run(["git", "-C", str(install), "tag", "v1.2.3"], check=True, capture_output=True)
        head = subprocess.run(["git", "-C", str(install), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        return install, head

    def test_returns_verified_version_facts_without_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, head = self._prepare(temporary)
            result = inspect_version(install)
            self.assertEqual("1.2.3", result["runtime_version"])
            self.assertEqual(str(install), result["install_root"])
            self.assertEqual("2026-08-22T00:00:00Z", result["installed_at"])
            self.assertEqual(head, result["git_head"])
            self.assertEqual(head[:12], result["git_short_sha"])
            self.assertIsNone(result["git_tag"])
            self.assertIsNone(result["git_describe"])

    def test_returns_exact_tag_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, _ = self._prepare(temporary, tag=True)
            result = inspect_version(install)
            self.assertEqual("v1.2.3", result["git_tag"])
            self.assertEqual("v1.2.3", result["git_describe"])

    def test_rejects_missing_or_unsafe_installation_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, _ = self._prepare(temporary)
            metadata = install / ".local" / "installation.json"
            metadata.unlink()
            with self.assertRaises(RuntimeErrorResult) as captured:
                inspect_version(install)
            self.assertEqual("install_metadata_missing", captured.exception.code)

            metadata.symlink_to(install / "developer" / "pyproject.toml")
            with self.assertRaises(RuntimeErrorResult) as captured:
                inspect_version(install)
            self.assertEqual("install_metadata_missing", captured.exception.code)

    def test_rejects_invalid_installation_metadata_and_release_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, _ = self._prepare(temporary)
            metadata = install / ".local" / "installation.json"
            metadata.write_text('{"schema_version":1,"installed_at":"invalid"}', encoding="utf-8")
            metadata.chmod(0o600)
            with self.assertRaises(RuntimeErrorResult) as captured:
                inspect_version(install)
            self.assertEqual("install_metadata_invalid", captured.exception.code)

            metadata.write_text(
                '{"schema_version":1,"installed_at":"2026-08-22T00:00:00Z"}',
                encoding="utf-8",
            )
            metadata.chmod(0o600)
            project = install / "developer" / "pyproject.toml"
            project.write_text("[project]\n", encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                inspect_version(install)
            self.assertEqual("install_version_metadata_invalid", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
