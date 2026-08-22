from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work.authorization.cli import execute_authorization
from ao_work.output import RuntimeErrorResult
from ao_work.work_cli import build_parser


class InstallationAuthorizationCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.install = Path(self.temporary.name) / "install"
        self.install.mkdir()

    def _run(self, arguments: tuple[str, ...], *, stdin: str = "") -> dict:
        args = build_parser().parse_args(("auth", *arguments))
        with mock.patch("sys.stdin", io.StringIO(stdin)):
            return execute_authorization(args, self.install)

    def test_non_interactive_authorization_is_install_scoped_and_masked(self) -> None:
        result = self._run(
            (
                "--agent-id",
                "developer-1",
                "--jira-email",
                "developer@example.test",
                "--git-name",
                "Developer One",
                "--git-email",
                "developer@example.test",
                "--github-login",
                "developer-one",
                "--token-stdin",
                "--non-interactive",
            ),
            stdin="token-secret-123\n",
        )
        self.assertTrue(result["configured"])
        self.assertEqual("installation", result["authorization_scope"])
        self.assertNotIn("token-secret-123", str(result))
        shown = self._run(("--show",))
        self.assertEqual("developer-1", shown["agent_id"])
        self.assertEqual("de*******@example.test", shown["jira_email"])
        self.assertTrue(shown["jira_credentials_configured"])
        self.assertNotIn("token-secret-123", str(shown))

    def test_reauthorization_reuses_identity_and_rotates_token(self) -> None:
        common = (
            "--agent-id",
            "developer-1",
            "--jira-email",
            "developer@example.test",
            "--git-name",
            "Developer One",
            "--git-email",
            "developer@example.test",
            "--github-login",
            "developer-one",
            "--token-stdin",
            "--non-interactive",
        )
        self._run(common, stdin="token-secret-123\n")
        rotated = self._run(
            ("--token-stdin", "--non-interactive"),
            stdin="token-secret-456\n",
        )
        self.assertTrue(rotated["configured"])
        self.assertIn(
            "TAPDATA_JIRA_API_TOKEN=token-secret-456",
            (self.install / "user" / ".env").read_text(encoding="utf-8"),
        )

    def test_empty_token_fails_without_writing_identity(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self._run(
                (
                    "--agent-id",
                    "developer-1",
                    "--jira-email",
                    "developer@example.test",
                    "--git-name",
                    "Developer One",
                    "--git-email",
                    "developer@example.test",
                    "--github-login",
                    "developer-one",
                    "--token-stdin",
                    "--non-interactive",
                ),
                stdin="\n",
            )
        self.assertEqual("authorization_token_empty", captured.exception.code)
        self.assertFalse((self.install / "user" / "identity.yaml").exists())

    def test_interactive_mode_requires_terminal(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self._run(())
        self.assertEqual("interactive_terminal_required", captured.exception.code)

    def test_invalid_jira_email_fails_before_authorization_write(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self._run(
                (
                    "--agent-id",
                    "developer-1",
                    "--jira-email",
                    "invalid-email",
                    "--git-name",
                    "Developer One",
                    "--git-email",
                    "developer@example.test",
                    "--github-login",
                    "developer-one",
                    "--token-stdin",
                    "--non-interactive",
                ),
                stdin="token-secret-123\n",
            )
        self.assertEqual("authorization_email_invalid", captured.exception.code)
        self.assertFalse((self.install / "user" / "identity.yaml").exists())


if __name__ == "__main__":
    unittest.main()
