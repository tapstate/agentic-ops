from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from agentic_ops.cli import main
from agentic_ops.jira.client import TransportResponse
from agentic_ops.workspace_init.service import normalize_agent_id


class TTYStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class WorkspaceInitTransport:
    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> TransportResponse:
        if path == "/rest/api/3/myself":
            return TransportResponse(200, {"accountId": "developer-1"})
        if path == "/rest/api/3/project/TAP":
            return TransportResponse(200, {"key": "TAP", "name": "TapData"})
        return TransportResponse(404, None)


class WorkspaceInitTest(unittest.TestCase):
    def prepare_install(self, root: Path) -> Path:
        install = root / "install"
        connection = install / "standards" / "connections" / "tap-cloud.yaml"
        connection.parent.mkdir(parents=True)
        connection.write_text(
            "connection_id: tap-cloud\n"
            "base_url: https://jira.example.test\n"
            "auth:\n"
            "  email_env: TEST_JIRA_EMAIL\n"
            "  token_env: TEST_JIRA_TOKEN\n",
            encoding="utf-8",
        )
        profile = install / "standards" / "projects" / "tapdata" / "profile.yaml"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            "profile_id: tapdata\n"
            "connection_id: tap-cloud\n"
            "jira:\n"
            "  project_key: TAP\n"
            "  task_query: project = TAP\n"
            "repositories:\n"
            "  default: tapdata/tapdata\n",
            encoding="utf-8",
        )
        return install

    def run_cli(
        self,
        arguments: tuple[str, ...],
        *,
        stdin: io.StringIO | None = None,
        token: str = "token-secret-123",
    ) -> tuple[int, dict[str, object], str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        input_stream = stdin or io.StringIO(f"{token}\n")

        def fake_git(
            _initializer: object,
            command: list[str],
            *,
            timeout: float | None = None,
        ) -> subprocess.CompletedProcess[str]:
            if command[0] == "clone":
                target = Path(command[-1])
                (target / ".git").mkdir(parents=True)
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[0] == "-C":
                return subprocess.CompletedProcess(
                    command, 0, "git@github.com:tapdata/tapdata.git\n", ""
                )
            return subprocess.CompletedProcess(command, 0, "HEAD\n", "")

        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch("sys.stdin", input_stream),
            mock.patch(
                "agentic_ops.workspace_init.service.UrllibJiraTransport",
                return_value=WorkspaceInitTransport(),
            ),
            mock.patch(
                "agentic_ops.workspace_init.service.WorkspaceInitializer._run_git",
                new=fake_git,
            ),
        ):
            exit_code = main(arguments)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return exit_code, json.loads(lines[0]), stderr.getvalue(), stdout.getvalue()

    def test_non_interactive_init_checks_auth_and_writes_workspace_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            common = (
                "--workspace-root",
                str(workspace),
                "--install-root",
                str(install),
                "workspace",
                "init",
                "--non-interactive",
                "--project",
                "tapdata",
                "--agent-id",
                "developer_1",
                "--jira-email",
                "developer@example.test",
                "--token-stdin",
                "--confirm",
            )
            exit_code, payload, _, raw = self.run_cli(common)
            self.assertEqual(0, exit_code)
            self.assertEqual("developer_1", payload["agent_id"])
            self.assertEqual("TAP", payload["jira_project"])
            self.assertEqual("passed", payload["post_preflight_status"])
            self.assertNotIn("token-secret-123", raw)
            agent = json.loads(
                (workspace / ".agentic-ops" / "agent.json").read_text(encoding="utf-8")
            )
            self.assertEqual("project_execution", agent["mode"])
            self.assertEqual("tapdata", agent["project_profile"])
            env_path = workspace / ".agentic-ops" / ".env"
            self.assertEqual(0o600, env_path.stat().st_mode & 0o777)
            self.assertIn("TEST_JIRA_TOKEN=token-secret-123", env_path.read_text())
            self.assertIn("agentic-cli workspace preflight", (workspace / "AGENTS.md").read_text())
            index = json.loads(
                (install / "user" / "workspace-index.json").read_text(encoding="utf-8")
            )
            self.assertEqual("developer_1", index["workspaces"][0]["agent_id"])

    def test_zero_parameter_interactive_entry_uses_hostname_default_and_confirms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            stdin = TTYStringIO("tapdata\n\ndeveloper@example.test\ny\n")
            with mock.patch(
                "agentic_ops.workspace_init.cli.getpass.getpass",
                return_value="token-secret-123",
            ), mock.patch(
                "agentic_ops.workspace_init.service.socket.gethostname",
                return_value="Dev.MacBook.LOCAL",
            ):
                exit_code, payload, stderr, _ = self.run_cli(
                    (
                        "--workspace-root",
                        str(workspace),
                        "--install-root",
                        str(install),
                        "workspace",
                        "init",
                    ),
                    stdin=stdin,
                )
            self.assertEqual(0, exit_code)
            self.assertEqual("dev-macbook-local", payload["agent_id"])
            self.assertIn("初始化摘要", stderr)
            self.assertIn("确认使用以上信息", stderr)

    def test_agent_id_collision_blocks_second_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            for name, expected in (("one", 0), ("two", 2)):
                workspace = root / name
                workspace.mkdir()
                result = self.run_cli(
                    (
                        "--workspace-root",
                        str(workspace),
                        "--install-root",
                        str(install),
                        "workspace",
                        "init",
                        "--non-interactive",
                        "--project",
                        "tapdata",
                        "--agent-id",
                        "same-agent",
                        "--jira-email",
                        "developer@example.test",
                        "--token-stdin",
                        "--confirm",
                    )
                )
                self.assertEqual(expected, result[0])
                if name == "two":
                    self.assertEqual("agent_id_conflict", result[1]["code"])
                    self.assertFalse(
                        (workspace / ".agentic-ops" / "agent.json").exists()
                    )

    def test_missing_non_interactive_authorization_blocks_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            with mock.patch.dict("os.environ", {}, clear=True):
                result = self.run_cli(
                    (
                        "--workspace-root",
                        str(workspace),
                        "--install-root",
                        str(install),
                        "workspace",
                        "init",
                        "--non-interactive",
                        "--project",
                        "tapdata",
                        "--agent-id",
                        "developer-2",
                        "--confirm",
                    ),
                    stdin=io.StringIO(""),
                )
            self.assertEqual(2, result[0])
            self.assertEqual("jira_credentials_missing", result[1]["code"])
            self.assertFalse((workspace / ".agentic-ops" / "agent.json").exists())

    def test_invalid_email_blocks_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            result = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "--install-root",
                    str(install),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer-3",
                    "--jira-email",
                    "not-an-email",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(2, result[0])
            self.assertEqual("authorization_email_invalid", result[1]["code"])
            self.assertFalse((workspace / ".agentic-ops" / "agent.json").exists())

    def test_agent_id_normalization_contract(self) -> None:
        self.assertEqual("dev-mac-local", normalize_agent_id("Dev.Mac LOCAL"))
