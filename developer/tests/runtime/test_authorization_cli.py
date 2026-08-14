from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_work.work_cli import main
from ao_work.jira.client import TransportResponse
from test_jira_service import FakeTransport


class AuthorizationCliTest(unittest.TestCase):
    def run_cli(
        self, *arguments: str, stdin: str = ""
    ) -> tuple[int, dict[str, object], str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin_stream = io.StringIO(stdin)
        with redirect_stdout(stdout), redirect_stderr(stderr), mock.patch("sys.stdin", stdin_stream), mock.patch(
            "ao_work.work_cli.validate_install_root",
            return_value=self.install_root,
        ):
            exit_code = main(arguments)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return exit_code, json.loads(lines[0]), stderr.getvalue(), stdout.getvalue()

    def prepare(self, root: Path) -> tuple[Path, Path]:
        install = root / "install"
        self.install_root = install.resolve()
        workspace = root / "workspace"
        connection = install / "developer" / "standards" / "connections" / "tap-cloud.yaml"
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
        agent.write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "workplane": "developer",
                    "connection_id": "tap-cloud",
                    "jira_base_url": "https://jira.example.test",
                    "jira_site": "jira.example.test",
                    "jira_account_id": "owner-1",
                }
            ),
            encoding="utf-8",
        )
        return install, workspace

    def common(self, install: Path, workspace: Path) -> tuple[str, ...]:
        return (
            "--workspace-root",
            str(workspace),
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

    def test_verify_does_not_implicitly_read_process_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = self.common(install, workspace)
            workspace_env = workspace / ".agentic-ops" / ".env"
            workspace_env.write_text(
                "TEST_JIRA_EMAIL=workspace@example.test\nTEST_JIRA_TOKEN=workspace-token-123\n",
                encoding="utf-8",
            )
            environment = {
                "TEST_JIRA_EMAIL": "owner@example.test",
                "TEST_JIRA_TOKEN": "process-token-123",
            }
            with mock.patch.dict(os.environ, environment, clear=False), mock.patch(
                "ao_work.authorization.cli.UrllibJiraTransport",
                return_value=FakeTransport(),
            ):
                verified = self.run_cli(*common, "verify")
            self.assertEqual(0, verified[0])
            self.assertEqual(True, verified[1]["verified"])
            self.assertEqual("owner-1", verified[1]["jira_user"])
            self.assertNotIn("process-token-123", verified[3])
            self.assertEqual("workspace", verified[1]["credential_source"])

    def test_verify_blocks_when_live_account_differs_from_workspace_identity(self) -> None:
        class OtherAccountTransport(FakeTransport):
            def request(self, method: str, path: str, **kwargs: object) -> TransportResponse:
                if path == "/rest/api/3/myself":
                    return TransportResponse(200, {"accountId": "other-account"})
                return super().request(method, path, **kwargs)

        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            (workspace / ".agentic-ops/.env").write_text(
                "TEST_JIRA_EMAIL=workspace@example.test\nTEST_JIRA_TOKEN=workspace-token-123\n",
                encoding="utf-8",
            )
            with mock.patch(
                "ao_work.authorization.cli.UrllibJiraTransport",
                return_value=OtherAccountTransport(),
            ):
                verified = self.run_cli(*self.common(install, workspace), "verify")
            self.assertEqual(2, verified[0])
            self.assertEqual("jira_workspace_account_drift", verified[1]["code"])

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

    def test_env_symlink_is_blocked_without_touching_external_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install, workspace = self.prepare(root)
            external = root / "external.env"
            external.write_text("SENTINEL=unchanged\n", encoding="utf-8")
            (workspace / ".agentic-ops" / ".env").symlink_to(external)

            blocked = self.run_cli(
                *self.common(install, workspace),
                "set",
                "--email",
                "owner@example.test",
            )
            self.assertEqual(2, blocked[0])
            self.assertEqual("workspace_env_symlink_forbidden", blocked[1]["code"])
            self.assertEqual("SENTINEL=unchanged\n", external.read_text(encoding="utf-8"))

    def test_tracked_env_is_blocked_before_auth_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            subprocess.run(
                ["git", "init", "-b", "main", str(workspace)],
                check=True,
                capture_output=True,
            )
            env_path = workspace / ".agentic-ops" / ".env"
            original = (
                "TEST_JIRA_EMAIL=old@example.test\n"
                "TEST_JIRA_TOKEN=old-secret-token\n"
            )
            env_path.write_text(original, encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(workspace), "add", "-f", ".agentic-ops/.env"],
                check=True,
                capture_output=True,
            )

            blocked = self.run_cli(
                *self.common(install, workspace),
                "set",
                "--email",
                "new@example.test",
                "--token-stdin",
                stdin="new-secret-token\n",
            )
            self.assertEqual(2, blocked[0])
            self.assertEqual("workspace_env_tracked", blocked[1]["code"])
            self.assertEqual(original, env_path.read_text(encoding="utf-8"))
            self.assertNotIn("new-secret-token", blocked[3])

    def test_auth_write_adds_local_exclude_and_stays_out_of_git_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            subprocess.run(
                ["git", "init", "-b", "main", str(workspace)],
                check=True,
                capture_output=True,
            )
            configured = self.run_cli(
                *self.common(install, workspace),
                "set",
                "--email",
                "owner@example.test",
                "--token-stdin",
                stdin="secret-token-value-123\n",
            )
            self.assertEqual(0, configured[0])
            self.assertEqual("git_local_exclude", configured[1]["credential_protection"])
            status = subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn(".agentic-ops/", status)
            self.assertNotIn("secret-token-value-123", status)
