from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work.config.loader import _install_identity_ref, install_entry_sha256, validate_workspace_jira_binding
from ao_work.installation import (
    install_user_dir,
    load_install_credentials,
    load_install_identity,
    save_install_credentials,
    save_install_identity,
)
from ao_work.jira.client import JiraConnection
from ao_work.output import RuntimeErrorResult
from ao_work.workspace import Workspace

IDENTITY = {
    "agent_id": "harsen-mini-test-bot",
    "jira_email": "harsen@example.test",
    "execution_authorization": {"mode": "global", "ssh_key_fingerprint": ""},
    "execution_identity": {
        "git_author_name": "Harsen Test Bot",
        "git_author_email": "harsen@example.test",
        "git_committer_name": "Harsen Test Bot",
        "git_committer_email": "harsen@example.test",
        "github_actor_login": "harsen-mini-test-bot",
    },
}


class InstallIdentityStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.install = Path(self.temporary.name) / "install"
        self.install.mkdir()
        entry = self.install / "bin" / "ao-work"
        entry.parent.mkdir()
        entry.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        entry.chmod(0o700)

    def test_save_and_load_identity_roundtrip(self) -> None:
        save_install_identity(self.install, IDENTITY)
        loaded = load_install_identity(self.install)
        self.assertEqual("harsen-mini-test-bot", loaded["agent_id"])
        self.assertEqual("harsen@example.test", loaded["jira_email"])
        self.assertEqual(IDENTITY["execution_identity"], loaded["execution_identity"])
        identity_path = self.install / "user" / "identity.yaml"
        self.assertEqual(0o600, identity_path.stat().st_mode & 0o777)

    def test_missing_identity_blocks(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_install_identity(self.install)
        self.assertEqual("install_identity_missing", captured.exception.code)

    def test_invalid_identity_blocks(self) -> None:
        (self.install / "user").mkdir()
        (self.install / "user" / "identity.yaml").write_text(
            "agent_id: only-agent\n", encoding="utf-8"
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_install_identity(self.install)
        self.assertEqual("install_identity_invalid", captured.exception.code)

    def test_identity_symlink_blocks(self) -> None:
        (self.install / "user").mkdir()
        target = self.install / "outside.yaml"
        target.write_text("agent_id: x\n", encoding="utf-8")
        (self.install / "user" / "identity.yaml").symlink_to(target)
        with self.assertRaises(RuntimeErrorResult) as captured:
            load_install_identity(self.install)
        self.assertEqual("install_identity_missing", captured.exception.code)

    def test_credentials_roundtrip_with_0600(self) -> None:
        save_install_credentials(self.install, "harsen@example.test", "token-secret")
        email, token = load_install_credentials(self.install)
        self.assertEqual(("harsen@example.test", "token-secret"), (email, token))
        env_path = self.install / "user" / ".env"
        self.assertEqual(0o600, env_path.stat().st_mode & 0o777)
        self.assertIn("TAPDATA_JIRA_API_TOKEN=token-secret", env_path.read_text())

    def test_missing_credentials_returns_none(self) -> None:
        self.assertIsNone(load_install_credentials(self.install))

    def test_credentials_roundtrip_parses_env(self) -> None:
        save_install_credentials(self.install, "harsen@example.test", "token-secret")
        credentials = load_install_credentials(self.install)
        assert credentials is not None
        self.assertEqual("harsen@example.test", credentials[0])
        self.assertEqual("token-secret", credentials[1])


class InstallIdentityRefTest(unittest.TestCase):
    def test_ref_stable_across_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install = Path(temporary) / "install"
            install.mkdir()
            save_install_identity(install, IDENTITY)
            first = _install_identity_ref(install, load_install_identity(install))
            second = _install_identity_ref(install, load_install_identity(install))
            self.assertEqual(first, second)
            self.assertTrue(first.startswith("install:"))


class InstallIdentityBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / "install"
        self.install.mkdir()
        entry = self.install / "bin" / "ao-work"
        entry.parent.mkdir()
        entry.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        entry.chmod(0o700)
        self.workspace = self.root / "workspace"
        (self.workspace / ".agentic-ops").mkdir(parents=True)
        self.connection = JiraConnection(
            connection_id="tap",
            base_url="https://tapdata.atlassian.net",
            email_env="TAPDATA_JIRA_EMAIL",
            token_env="TAPDATA_JIRA_API_TOKEN",
            timeout_seconds=20,
        )
        save_install_identity(self.install, IDENTITY)

    def _agent(self, schema: int = 5, ref: str | None = None) -> dict:
        payload = {
            "schema_version": schema,
            "workplane": "developer",
            "project_profile": "tapdata",
            "jira_project": "TAP",
            "connection_id": "tap",
            "jira_base_url": "https://tapdata.atlassian.net",
            "jira_site": "tapdata.atlassian.net",
            "source_root": str(self.root / "pool"),
            "repository": "tapdata/tapdata",
        }
        if schema == 5:
            payload["install_identity_ref"] = ref or _install_identity_ref(
                self.install, load_install_identity(self.install)
            )
            payload["workspace_entry"] = ".agentic-ops/bin/ao-work"
            payload["install_entry_sha256"] = install_entry_sha256(self.install)
        else:
            payload["jira_account_id"] = "jira-account-1"
            payload["agent_id"] = "harsen-mini-test-bot"
        return payload

    def _workspace(self, payload: dict) -> Workspace:
        config_path = (self.workspace / ".agentic-ops" / "agent.json").resolve()
        config_path.write_text(json.dumps(payload), encoding="utf-8")
        return Workspace(
            root=self.workspace.resolve(),
            workplane="developer",
            config_path=config_path,
        )

    def test_v5_binding_passes_with_matching_install_identity(self) -> None:
        workspace = self._workspace(self._agent())
        agent = validate_workspace_jira_binding(
            workspace,
            self.connection,
            install_root=self.install,
        )
        self.assertEqual(5, agent["schema_version"])

    def test_v5_binding_drift_blocked(self) -> None:
        other = dict(IDENTITY)
        other["agent_id"] = "other-engineer"
        workspace = self._workspace(self._agent(ref="install:stale-fingerprint"))
        with self.assertRaises(RuntimeErrorResult) as captured:
            validate_workspace_jira_binding(
                workspace,
                self.connection,
                install_root=self.install,
            )
        self.assertEqual("install_identity_drift", captured.exception.code)

    def test_v5_binding_requires_install_root(self) -> None:
        workspace = self._workspace(self._agent())
        with self.assertRaises(RuntimeErrorResult) as captured:
            validate_workspace_jira_binding(workspace, self.connection)
        self.assertEqual("install_identity_missing", captured.exception.code)

    def test_v4_workspace_requires_explicit_reinitialization(self) -> None:
        workspace = self._workspace(self._agent(schema=4))
        with self.assertRaises(RuntimeErrorResult) as captured:
            validate_workspace_jira_binding(
                workspace,
                self.connection,
                install_root=self.install,
            )
        self.assertEqual("workspace_jira_identity_upgrade_required", captured.exception.code)
        self.assertIn("--confirm-existing-config", captured.exception.required_human_action)

    def test_v5_binding_rejects_install_entry_drift(self) -> None:
        payload = self._agent()
        (self.install / "bin" / "ao-work").write_text(
            "#!/usr/bin/env bash\necho changed\n", encoding="utf-8"
        )
        workspace = self._workspace(payload)
        with self.assertRaises(RuntimeErrorResult) as captured:
            validate_workspace_jira_binding(
                workspace,
                self.connection,
                install_root=self.install,
            )
        self.assertEqual("install_entry_drift", captured.exception.code)

    def test_v3_binding_is_rejected_before_workspace_credentials_are_used(self) -> None:
        workspace = self._workspace(self._agent(schema=3))
        with self.assertRaises(RuntimeErrorResult) as captured:
            validate_workspace_jira_binding(
                workspace,
                self.connection,
                install_root=self.install,
            )
        self.assertEqual(
            "workspace_jira_identity_upgrade_required",
            captured.exception.code,
        )
        self.assertIn("ao-work auth", captured.exception.required_human_action)

    def test_user_dir_symlink_blocked(self) -> None:
        import shutil

        user_dir = self.install / "user"
        if user_dir.exists():
            shutil.rmtree(user_dir)
        target = self.root / "outside"
        target.mkdir()
        (self.install / "user").symlink_to(target, target_is_directory=True)
        with self.assertRaises(RuntimeErrorResult) as captured:
            install_user_dir(self.install)
        self.assertEqual("install_user_dir_invalid", captured.exception.code)


class InstallIdentityCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.install = self.root / "install"
        self.install.mkdir()

    def _run(self, arguments: tuple[str, ...], *, stdin: str = "") -> dict:
        from ao_work.authorization.cli import execute_authorization
        from ao_work.work_cli import build_parser

        parser = build_parser()
        args = parser.parse_args(("auth", *arguments))
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            mock.patch(
                "ao_work.authorization.cli._validate_global_authorization"
            ),
        ):
            return execute_authorization(args, self.install)

    def test_set_and_show_identity_non_interactive(self) -> None:
        result = self._run(
            (
                "--agent-id",
                "harsen-mini-test-bot",
                "--git-name",
                "Harsen Test Bot",
                "--git-email",
                "harsen@example.test",
                "--github-login",
                "harsen-mini-test-bot",
                "--execution-auth-mode",
                "global",
                "--jira-email",
                "harsen@example.test",
                "--token-stdin",
                "--non-interactive",
            ),
            stdin="token-secret-123\n",
        )
        self.assertTrue(result["configured"])
        shown = self._run(("--show",))
        self.assertTrue(shown["configured"])
        self.assertEqual("harsen-mini-test-bot", shown["agent_id"])
        self.assertTrue(shown["jira_credentials_configured"])

    def test_show_when_unconfigured(self) -> None:
        shown = self._run(("--show",))
        self.assertFalse(shown["configured"])

    def test_non_interactive_auth_requires_complete_identity(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self._run(
                ("--jira-email", "harsen@example.test", "--token-stdin", "--non-interactive"),
                stdin="token-secret-123\n",
            )
        self.assertEqual("install_identity_incomplete", captured.exception.code)

    def test_show_cannot_be_combined_with_write_arguments(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self._run(("--show", "--agent-id", "invalid-combination"))
        self.assertEqual(
            "authorization_show_arguments_invalid",
            captured.exception.code,
        )


if __name__ == "__main__":
    unittest.main()
