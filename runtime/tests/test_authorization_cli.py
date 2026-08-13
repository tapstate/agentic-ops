from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from agentic_ops.cli import main
from test_jira_service import FakeTransport


class AuthorizationCliTest(unittest.TestCase):
    def run_cli(
        self, *arguments: str, stdin: str = ""
    ) -> tuple[int, dict[str, object], str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin_stream = io.StringIO(stdin)
        with redirect_stdout(stdout), redirect_stderr(stderr), mock.patch("sys.stdin", stdin_stream):
            exit_code = main(arguments)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return exit_code, json.loads(lines[0]), stderr.getvalue(), stdout.getvalue()

    def prepare(self, root: Path) -> tuple[Path, Path]:
        install = root / "install"
        workspace = root / "workspace"
        connection = install / "standards" / "connections" / "tap-cloud.yaml"
        connection.parent.mkdir(parents=True)
        connection.write_text(
            """\
connection_id: tap-cloud
base_url: https://jira.example.test
auth:
  email_env: TEST_JIRA_EMAIL
  token_env: TEST_JIRA_TOKEN
""",
            encoding="utf-8",
        )
        agent = workspace / ".agentic-ops" / "agent.json"
        agent.parent.mkdir(parents=True)
        agent.write_text('{"mode":"project_execution"}\n', encoding="utf-8")
        return install, workspace

    def common(self, install: Path, workspace: Path) -> tuple[str, ...]:
        return (
            "--workspace-root",
            str(workspace),
            "--mode",
            "project_execution",
            "--install-root",
            str(install),
            "auth",
            "jira",
        )

    def test_list_set_show_modify_and_remove_without_exposing_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = self.common(install, workspace)
            listed = self.run_cli(*common, "list")
            self.assertEqual(["tap-cloud"], listed[1]["connections"])

            token = "secret-token-value-123"
            configured = self.run_cli(
                *common,
                "set",
                "--email",
                "owner@example.test",
                "--token-stdin",
                stdin=token + "\n",
            )
            self.assertEqual(0, configured[0])
            self.assertEqual(True, configured[1]["ready"])
            self.assertNotIn(token, configured[3])
            self.assertEqual("ow***@example.test", configured[1]["email_hint"])
            env_path = workspace / ".agentic-ops" / ".env"
            self.assertEqual(0o600, env_path.stat().st_mode & 0o777)

            modified = self.run_cli(
                *common,
                "set",
                "--email",
                "changed@example.test",
            )
            self.assertEqual(["email"], modified[1]["updated_fields"])
            self.assertIn("TEST_JIRA_TOKEN=secret-token-value-123", env_path.read_text())

            with mock.patch.dict(os.environ, {}, clear=True):
                shown = self.run_cli(
                    *common,
                    "show",
                )
            self.assertEqual("workspace", shown[1]["account_scope"])
            self.assertEqual("workspace", shown[1]["credential_source"])
            self.assertNotIn(token, shown[3])

            removed = self.run_cli(
                *common,
                "remove",
                "--field",
                "token",
            )
            self.assertEqual(False, removed[1]["token_configured"])
            self.assertNotIn("TEST_JIRA_TOKEN", env_path.read_text())

    def test_install_user_credentials_cannot_replace_workspace_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = self.common(install, workspace)
            install_env = install / "user" / ".env"
            install_env.parent.mkdir(parents=True)
            install_env.write_text(
                "TEST_JIRA_EMAIL=user@example.test\nTEST_JIRA_TOKEN=user-token-123\n",
                encoding="utf-8",
            )
            workspace_env = workspace / ".agentic-ops" / ".env"
            workspace_env.write_text(
                "TEST_JIRA_EMAIL=workspace@example.test\nTEST_JIRA_TOKEN=workspace-token-123\n",
                encoding="utf-8",
            )
            with mock.patch.dict(os.environ, {}, clear=True):
                shown = self.run_cli(*common, "show")
            self.assertEqual("wo*******@example.test", shown[1]["email_hint"])
            self.assertEqual("workspace", shown[1]["credential_source"])

    def test_verify_returns_identity_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = self.common(install, workspace)
            environment = {
                "TEST_JIRA_EMAIL": "owner@example.test",
                "TEST_JIRA_TOKEN": "process-token-123",
            }
            with mock.patch.dict(os.environ, environment, clear=False), mock.patch(
                "agentic_ops.authorization.cli.UrllibJiraTransport",
                return_value=FakeTransport(),
            ):
                verified = self.run_cli(*common, "verify")
            self.assertEqual(0, verified[0])
            self.assertEqual(True, verified[1]["verified"])
            self.assertEqual("owner-1", verified[1]["jira_user"])
            self.assertNotIn("process-token-123", verified[3])

    def test_invalid_email_and_empty_token_are_blocked_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = self.common(install, workspace)
            blocked = self.run_cli(
                *common,
                "set",
                "--email",
                "not-an-email",
            )
            self.assertEqual(2, blocked[0])
            self.assertEqual("authorization_email_invalid", blocked[1]["code"])
            self.assertFalse((workspace / ".agentic-ops" / ".env").exists())

            empty = self.run_cli(
                *common,
                "set",
                "--token-stdin",
                stdin="\n",
            )
            self.assertEqual("authorization_token_empty", empty[1]["code"])
