from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest

from ao_work.authorization.execution import (
    authorization_existing_summary,
    authorization_paths,
    operational_environment,
    prepare_installation_authorization,
)
from ao_work.output import RuntimeErrorResult


class ExecutionAuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / "install"
        self.install.mkdir()
        trusted = (
            self.install
            / "developer/standards/security/github-known-hosts"
        )
        trusted.parent.mkdir(parents=True)
        source = (
            Path(__file__).resolve().parents[2]
            / "standards/security/github-known-hosts"
        )
        shutil.copyfile(source, trusted)

    def test_installation_mode_creates_private_ssh_over_443_assets(self) -> None:
        result = prepare_installation_authorization(
            self.install,
            github_login="developer-one",
        )
        self.assertEqual("installation", result["mode"])
        self.assertTrue(result["ssh_key_fingerprint"].startswith("SHA256:"))
        paths = authorization_paths(self.install)
        self.assertEqual(0o700, stat.S_IMODE(paths["user"].stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(paths["ssh"].stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(paths["gh_config"].stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(paths["private_key"].stat().st_mode))
        config = paths["ssh_config"].read_text(encoding="utf-8")
        self.assertIn("HostName ssh.github.com", config)
        self.assertIn("Port 443", config)
        self.assertIn("IdentitiesOnly yes", config)
        self.assertIn("IdentityAgent none", config)
        self.assertIn("StrictHostKeyChecking yes", config)

        environment = operational_environment(
            self.install,
            {
                "execution_authorization": {
                    "mode": "installation",
                    "ssh_key_fingerprint": result["ssh_key_fingerprint"],
                }
            },
            base={"PATH": os.environ.get("PATH", ""), "SSH_AUTH_SOCK": "/tmp/global-agent"},
        )
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        self.assertEqual(str(paths["gh_config"]), environment["GH_CONFIG_DIR"])
        self.assertIn(str(paths["ssh_config"]), environment["GIT_SSH_COMMAND"])

    def test_unmanaged_existing_config_is_never_overwritten(self) -> None:
        paths = authorization_paths(self.install)
        paths["user"].mkdir(mode=0o700)
        paths["ssh"].mkdir(mode=0o700)
        paths["gh_config"].mkdir(mode=0o700)
        paths["ssh_config"].write_text("Host *\n  IdentityFile /existing/key\n")
        os.chmod(paths["ssh_config"], 0o600)
        before = paths["ssh_config"].read_bytes()
        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_installation_authorization(
                self.install,
                github_login="developer-one",
            )
        self.assertEqual(
            "existing_authorization_unmanaged_conflict",
            captured.exception.code,
        )
        self.assertEqual(before, paths["ssh_config"].read_bytes())

    def test_managed_config_change_requires_explicit_allowance(self) -> None:
        prepare_installation_authorization(
            self.install,
            github_login="developer-one",
        )
        paths = authorization_paths(self.install)
        paths["ssh_config"].write_text(
            paths["ssh_config"].read_text(encoding="utf-8")
            + "# previous managed version\n",
            encoding="utf-8",
        )
        before = paths["ssh_config"].read_bytes()
        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_installation_authorization(
                self.install,
                github_login="developer-one",
            )
        self.assertEqual(
            "existing_authorization_change_confirmation_required",
            captured.exception.code,
        )
        self.assertEqual(before, paths["ssh_config"].read_bytes())

        prepare_installation_authorization(
            self.install,
            github_login="developer-one",
            allow_managed_update=True,
        )
        self.assertNotEqual(before, paths["ssh_config"].read_bytes())
        self.assertNotIn(
            "previous managed version",
            paths["ssh_config"].read_text(encoding="utf-8"),
        )

    def test_incomplete_existing_key_pair_blocks_before_directory_writes(self) -> None:
        paths = authorization_paths(self.install)
        paths["user"].mkdir(mode=0o700)
        paths["ssh"].mkdir(mode=0o700)
        paths["private_key"].write_text("existing-private-key", encoding="utf-8")
        os.chmod(paths["private_key"], 0o600)

        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_installation_authorization(
                self.install,
                github_login="developer-one",
            )
        self.assertEqual(
            "existing_authorization_unmanaged_conflict",
            captured.exception.code,
        )
        self.assertFalse(paths["gh_config"].exists())
        self.assertEqual(
            b"existing-private-key",
            paths["private_key"].read_bytes(),
        )

    def test_unsafe_existing_user_directory_permissions_block(self) -> None:
        user = self.install / "user"
        user.mkdir(mode=0o755)
        os.chmod(user, 0o755)
        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_installation_authorization(
                self.install,
                github_login="developer-one",
            )
        self.assertEqual(
            "existing_authorization_permissions_unsafe",
            captured.exception.code,
        )
        self.assertEqual(0o755, stat.S_IMODE(user.stat().st_mode))

    def test_existing_summary_is_redacted_and_does_not_read_private_key(self) -> None:
        summary = authorization_existing_summary(
            self.install,
            {
                "agent_id": "developer-one",
                "execution_identity": {"github_actor_login": "developer-one"},
                "execution_authorization": {
                    "mode": "global",
                    "ssh_key_fingerprint": "",
                },
            },
        )
        self.assertEqual("configured", summary["identity"])
        self.assertEqual("global", summary["mode"])
        self.assertNotIn("token", str(summary).lower())


if __name__ == "__main__":
    unittest.main()
