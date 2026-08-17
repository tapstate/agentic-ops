from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_work.work_cli import main
from ao_work.jira.client import TransportResponse
from ao_work.output import RuntimeErrorResult
from ao_work.workspace_init.service import (
    WorkspaceInitializer,
    build_execution_identity,
    normalize_agent_id,
)


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
    def test_execution_identity_is_confirmed_once_and_validated(self) -> None:
        self.assertEqual(
            "harsen-mini-test-bot",
            build_execution_identity(
                "harsen-mini-test-bot",
                "harsen@example.test",
                "harsen-mini-test-bot",
            )["github_actor_login"],
        )
        for values in (
            ("", "harsen@example.test", "harsen"),
            ("harsen", "invalid", "harsen"),
            ("harsen", "harsen@example.test", "bad_login"),
        ):
            with self.subTest(values=values), self.assertRaises(RuntimeErrorResult):
                build_execution_identity(*values)

    def prepare_install(self, root: Path) -> Path:
        install = root / "install"
        self.install_root = install.resolve()
        connection = install / "developer" / "standards" / "connections" / "tap-cloud.yaml"
        connection.parent.mkdir(parents=True)
        connection.write_text(
            "connection_id: tap-cloud\n"
            "base_url: https://jira.example.test\n"
            "auth:\n"
            "  email_env: TEST_JIRA_EMAIL\n"
            "  token_env: TEST_JIRA_TOKEN\n",
            encoding="utf-8",
        )
        profile = install / "developer" / "standards" / "projects" / "tapdata" / "profile.yaml"
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
        (install / "developer" / "AGENTS.md").write_text(
            "# 研发工作入口\n\n不得加载 maintainer 工作面。\n",
            encoding="utf-8",
        )
        rule_path = install / "developer" / "rules" / "ai-execution.md"
        rule_path.parent.mkdir(parents=True)
        rule_path.write_text(
            "# 研发规则\n\n真实 Jira 写入必须经过人工门禁。\n",
            encoding="utf-8",
        )
        for name in ("configure-authorization", "initialize-project-workspace"):
            skill = install / "developer" / "skills" / name / "SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text(
                "---\n"
                f"name: {name}\n"
                "description: Test developer workflow.\n"
                "metadata:\n"
                "  workplane: developer\n"
                "---\n\n"
                f"# {name}\n",
                encoding="utf-8",
            )
        return install

    def run_cli(
        self,
        arguments: tuple[str, ...],
        *,
        stdin: io.StringIO | None = None,
        token: str = "token-secret-123",
        clone_source_marker: bool = False,
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
            if "--get-regexp" in command:
                return subprocess.CompletedProcess(command, 1, "", "")
            if command[0] == "clone":
                target = Path(command[-1])
                (target / ".git").mkdir(parents=True)
                if clone_source_marker:
                    (target / ".agentic-ops-source").write_text(
                        "maintainer\n", encoding="utf-8"
                    )
                return subprocess.CompletedProcess(command, 0, "", "")
            if command[0] == "-C":
                if command[2:] == ["rev-parse", "--show-toplevel"]:
                    return subprocess.CompletedProcess(command, 1, "", "not a repository")
                if command[2:] == ["config", "--get-all", "remote.origin.pushurl"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return subprocess.CompletedProcess(
                    command, 0, "git@github.com:tapdata/tapdata.git\n", ""
                )
            return subprocess.CompletedProcess(command, 0, "HEAD\n", "")

        def fake_git_streaming(
            _initializer: object,
            command: list[str],
            *,
            stall_warn_interval: float = 30.0,
        ) -> subprocess.CompletedProcess[str]:
            target = Path(command[-1])
            (target / ".git").mkdir(parents=True)
            if clone_source_marker:
                (target / ".agentic-ops-source").write_text(
                    "maintainer\n", encoding="utf-8"
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch("sys.stdin", input_stream),
            mock.patch(
                "ao_work.workspace_init.service.UrllibJiraTransport",
                return_value=WorkspaceInitTransport(),
            ),
            mock.patch(
                "ao_work.workspace_init.service.WorkspaceInitializer._run_git",
                new=fake_git,
            ),
            mock.patch(
                "ao_work.workspace_init.service.WorkspaceInitializer._run_git_streaming",
                new=fake_git_streaming,
            ),
            mock.patch(
                "ao_work.work_cli.validate_install_root",
                return_value=self.install_root,
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
                "workspace",
                "init",
                "--non-interactive",
                "--project",
                "tapdata",
                "--agent-id",
                "developer_1",
                "--jira-email",
                "developer@example.test",
                "--git-name",
                "Developer One",
                "--git-email",
                "developer@example.test",
                "--github-login",
                "developer-one",
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
            self.assertEqual("developer", agent["workplane"])
            self.assertEqual("tapdata", agent["project_profile"])
            self.assertEqual(3, agent["schema_version"])
            self.assertEqual("tap-cloud", agent["connection_id"])
            self.assertEqual(
                {
                    "git_author_name": "Developer One",
                    "git_author_email": "developer@example.test",
                    "git_committer_name": "Developer One",
                    "git_committer_email": "developer@example.test",
                    "github_actor_login": "developer-one",
                },
                agent["execution_identity"],
            )
            self.assertEqual("https://jira.example.test", agent["jira_base_url"])
            self.assertEqual("jira.example.test", agent["jira_site"])
            self.assertEqual("developer-1", agent["jira_account_id"])
            env_path = workspace / ".agentic-ops" / ".env"
            self.assertEqual(0o600, env_path.stat().st_mode & 0o777)
            self.assertIn("TEST_JIRA_TOKEN=token-secret-123", env_path.read_text())
            self.assertIn("ao-work workspace preflight", (workspace / "AGENTS.md").read_text())
            self.assertEqual(
                {"configure-authorization", "initialize-project-workspace"},
                {
                    path.name
                    for path in (workspace / ".agents" / "skills").iterdir()
                },
            )
            self.assertFalse((workspace / ".agents" / "skills" / "maintainer").exists())
            index = json.loads(
                (install / "user" / "workspace-index.json").read_text(encoding="utf-8")
            )
            self.assertEqual("developer_1", index["workspaces"][0]["agent_id"])
            expected_source = (root / "workspace-code" / "tapdata").resolve()
            self.assertEqual(str(expected_source), agent["source_root"])
            self.assertEqual(str(expected_source), index["workspaces"][0]["source_root"])
            code_readme = root / "workspace-code" / "README.md"
            self.assertTrue(code_readme.is_file())
            self.assertIn(
                "<!-- agentic-ops:workspace-code:start -->",
                code_readme.read_text(encoding="utf-8"),
            )
            self.assertTrue((expected_source / ".git").exists())

            preflight = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "preflight",
                ),
                stdin=io.StringIO(""),
            )
            self.assertEqual(0, preflight[0], preflight[1])
            preserved = json.loads(
                (workspace / ".agentic-ops" / "agent.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(agent["execution_identity"], preserved["execution_identity"])

    def test_non_interactive_init_emits_stage_progress_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            exit_code, payload, stderr, _ = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "progress-agent",
                    "--jira-email",
                    "developer@example.test",
                    "--git-name",
                    "Progress Agent",
                    "--git-email",
                    "developer@example.test",
                    "--github-login",
                    "progress-agent",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(0, exit_code, payload)
            for expected in (
                "初始化步骤 1/5",
                "初始化步骤 2/5：下载业务源码仓库",
                "源码仓库下载完成",
                "初始化步骤 3/5",
                "初始化步骤 4/5",
                "初始化步骤 5/5",
                "初始化完成：业务项目工作空间已就绪",
            ):
                self.assertIn(expected, stderr, stderr)

    def test_explicit_source_root_reused_without_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            source = root / "explicit-repo"
            workspace.mkdir()
            (source / ".git").mkdir(parents=True)
            exit_code, payload, _, _ = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer_explicit",
                    "--source-root",
                    str(source),
                    "--jira-email",
                    "developer@example.test",
                    "--git-name",
                    "Developer Explicit",
                    "--git-email",
                    "developer@example.test",
                    "--github-login",
                    "developer-explicit",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(0, exit_code, payload)
            self.assertEqual("reused", payload["source_checkout_status"])
            self.assertEqual(str(source.resolve()), payload["source_root"])
            self.assertFalse((root / "README.md").exists())

    def test_source_root_conflict_with_another_workspace_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            first = root / "workspace-a"
            first.mkdir()
            first_result = self.run_cli(
                (
                    "--workspace-root",
                    str(first),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer_a",
                    "--jira-email",
                    "developer@example.test",
                    "--git-name",
                    "Developer A",
                    "--git-email",
                    "developer@example.test",
                    "--github-login",
                    "developer-a",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(0, first_result[0], first_result[1])
            second = root / "workspace-b"
            second.mkdir()
            shared_source = (root / "workspace-a-code" / "tapdata").resolve()
            second_result = self.run_cli(
                (
                    "--workspace-root",
                    str(second),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer_b",
                    "--source-root",
                    str(shared_source),
                    "--jira-email",
                    "developer@example.test",
                    "--git-name",
                    "Developer B",
                    "--git-email",
                    "developer@example.test",
                    "--github-login",
                    "developer-b",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(2, second_result[0])
            self.assertEqual("source_root_conflict", second_result[1]["code"])
            self.assertFalse((second / ".agentic-ops" / "agent.json").exists())

    def test_zero_parameter_interactive_entry_uses_hostname_default_and_confirms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            stdin = TTYStringIO(
                "tapdata\n\ndeveloper@example.test\n\n\n\ny\n"
            )
            with mock.patch(
                "ao_work.workspace_init.cli.getpass.getpass",
                return_value="token-secret-123",
            ), mock.patch(
                "ao_work.workspace_init.service.socket.gethostname",
                return_value="Dev.MacBook.LOCAL",
            ):
                exit_code, payload, stderr, _ = self.run_cli(
                    (
                        "--workspace-root",
                        str(workspace),
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

    def test_source_repository_cannot_be_used_as_business_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            source = root / "agentic-ops-source"
            (source / ".git").mkdir(parents=True)
            (source / ".agentic-ops-source").write_text("maintainer\n", encoding="utf-8")
            result = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer-source-boundary",
                    "--source-root",
                    str(source),
                    "--jira-email",
                    "developer@example.test",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(2, result[0])
            self.assertEqual("workplane_mismatch", result[1]["code"])
            self.assertFalse((workspace / ".agentic-ops" / "agent.json").exists())

    def test_source_root_cannot_equal_or_nest_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            for source in (workspace, workspace / "source", root):
                with self.subTest(source=source):
                    result = self.run_cli(
                        (
                            "--workspace-root",
                            str(workspace),
                            "workspace",
                            "init",
                            "--non-interactive",
                            "--project",
                            "tapdata",
                            "--agent-id",
                            "developer-boundary",
                            "--source-root",
                            str(source),
                            "--jira-email",
                            "developer@example.test",
                            "--token-stdin",
                            "--confirm",
                        )
                    )
                    self.assertEqual(2, result[0])
                    self.assertEqual(
                        "workspace_source_boundary_invalid", result[1]["code"]
                    )

    def test_cloned_agenticops_source_is_rejected_and_rolled_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            source = root / "business-source"
            workspace.mkdir()
            result = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer-post-clone-boundary",
                    "--source-root",
                    str(source),
                    "--jira-email",
                    "developer@example.test",
                    "--token-stdin",
                    "--confirm",
                ),
                clone_source_marker=True,
            )
            self.assertEqual(2, result[0])
            self.assertEqual("workplane_mismatch", result[1]["code"])
            self.assertFalse(source.exists())
            self.assertFalse((workspace / ".agentic-ops").exists())
            self.assertFalse((workspace / "AGENTS.md").exists())
            self.assertFalse((install / "user" / "workspace-index.json").exists())

    def test_workspace_state_symlink_blocks_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            external_state = root / "external-state"
            workspace.mkdir()
            external_state.mkdir()
            (workspace / ".agentic-ops").symlink_to(
                external_state, target_is_directory=True
            )
            result = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer-state-symlink",
                    "--jira-email",
                    "developer@example.test",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(2, result[0])
            self.assertEqual(
                "workspace_state_symlink_forbidden", result[1]["code"]
            )
            self.assertEqual([], list(external_state.iterdir()))

    def test_root_agents_symlink_cannot_read_or_modify_outside_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            outside = root / "outside-agents.md"
            sentinel = "OUTSIDE-SENTINEL-DO-NOT-READ-OR-WRITE\n"
            outside.write_text(sentinel, encoding="utf-8")
            (workspace / "AGENTS.md").symlink_to(outside)

            result = self.run_cli(
                (
                    "--workspace-root", str(workspace),
                    "workspace", "init",
                    "--non-interactive", "--project", "tapdata",
                    "--agent-id", "agents-boundary-test",
                    "--jira-email", "developer@example.test",
                    "--token-stdin", "--confirm",
                )
            )
            self.assertEqual(2, result[0])
            self.assertEqual("workspace_managed_path_unsafe", result[1]["code"])
            self.assertNotIn("OUTSIDE-SENTINEL", result[3])
            self.assertEqual(sentinel, outside.read_text(encoding="utf-8"))
            self.assertTrue((workspace / "AGENTS.md").is_symlink())
            self.assertFalse((workspace / ".agentic-ops/agent.json").exists())

    def test_managed_profile_directory_symlink_blocks_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            state = workspace / ".agentic-ops"
            external = root / "external-profiles"
            state.mkdir(parents=True)
            external.mkdir()
            (state / "profiles").symlink_to(external, target_is_directory=True)
            result = self.run_cli(
                (
                    "--workspace-root", str(workspace),
                    "workspace", "init",
                    "--non-interactive", "--project", "tapdata",
                    "--agent-id", "managed-path-test",
                    "--jira-email", "developer@example.test",
                    "--token-stdin", "--confirm",
                )
            )
            self.assertEqual(2, result[0])
            self.assertEqual("workspace_managed_path_unsafe", result[1]["code"])
            self.assertEqual([], list(external.iterdir()))

    def test_workspace_index_symlink_blocks_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            external = root / "external-index.json"
            external.write_text('{"sentinel":true}\n', encoding="utf-8")
            user = install / "user"
            user.mkdir()
            (user / "workspace-index.json").symlink_to(external)
            result = self.run_cli(
                (
                    "--workspace-root", str(workspace),
                    "workspace", "init",
                    "--non-interactive", "--project", "tapdata",
                    "--agent-id", "index-path-test",
                    "--jira-email", "developer@example.test",
                    "--token-stdin", "--confirm",
                )
            )
            self.assertEqual(2, result[0])
            self.assertEqual("workspace_index_path_unsafe", result[1]["code"])
            self.assertEqual('{"sentinel":true}\n', external.read_text(encoding="utf-8"))

    def test_plain_preflight_cannot_confirm_effective_profile_rebind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            init = self.run_cli(
                (
                    "--workspace-root", str(workspace),
                    "workspace", "init",
                    "--non-interactive", "--project", "tapdata",
                    "--agent-id", "profile-drift-test",
                    "--jira-email", "developer@example.test",
                    "--token-stdin", "--confirm",
                )
            )
            self.assertEqual(0, init[0])
            agent_path = workspace / ".agentic-ops/agent.json"
            original = agent_path.read_text(encoding="utf-8")
            profile = install / "developer/standards/projects/tapdata/profile.yaml"
            profile.write_text(
                profile.read_text(encoding="utf-8").replace(
                    "default: tapdata/tapdata", "default: attacker/repository"
                ),
                encoding="utf-8",
            )
            preflight = self.run_cli(
                (
                    "--workspace-root", str(workspace),
                    "workspace", "preflight",
                ),
                stdin=io.StringIO(""),
            )
            self.assertEqual(2, preflight[0])
            self.assertEqual("workspace_project_identity_drift", preflight[1]["code"])
            self.assertEqual(original, agent_path.read_text(encoding="utf-8"))

    def test_workspace_env_symlink_blocks_initialization_without_external_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            state_root = workspace / ".agentic-ops"
            external_env = root / "external.env"
            state_root.mkdir(parents=True)
            external_env.write_text("SENTINEL=unchanged\n", encoding="utf-8")
            (state_root / ".env").symlink_to(external_env)
            result = self.run_cli(
                (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer-env-symlink",
                    "--jira-email",
                    "developer@example.test",
                    "--token-stdin",
                    "--confirm",
                )
            )
            self.assertEqual(2, result[0])
            self.assertEqual("workspace_env_symlink_forbidden", result[1]["code"])
            self.assertEqual(
                "SENTINEL=unchanged\n", external_env.read_text(encoding="utf-8")
            )
            self.assertFalse((state_root / "agent.json").exists())

    def test_tracked_workspace_env_blocks_initialization_before_secret_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            workspace = root / "workspace"
            state_root = workspace / ".agentic-ops"
            state_root.mkdir(parents=True)
            subprocess.run(
                ["git", "init", "-b", "main", str(workspace)],
                check=True,
                capture_output=True,
            )
            env_path = state_root / ".env"
            original = "TEST_JIRA_EMAIL=old@example.test\nTEST_JIRA_TOKEN=old-token-123\n"
            env_path.write_text(original, encoding="utf-8")
            subprocess.run(
                ["git", "-C", str(workspace), "add", "-f", ".agentic-ops/.env"],
                check=True,
                capture_output=True,
            )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                redirect_stdout(stdout),
                redirect_stderr(stderr),
                mock.patch("sys.stdin", io.StringIO("new-token-secret-123\n")),
                mock.patch(
                    "ao_work.workspace_init.service.UrllibJiraTransport",
                    return_value=WorkspaceInitTransport(),
                ),
                mock.patch(
                    "ao_work.work_cli.validate_install_root",
                    return_value=self.install_root,
                ),
                mock.patch.object(
                    WorkspaceInitializer, "_check_source", return_value="ready_to_clone"
                ),
            ):
                exit_code = main(
                    (
                        "--workspace-root",
                        str(workspace),
                        "workspace",
                        "init",
                        "--non-interactive",
                        "--project",
                        "tapdata",
                        "--agent-id",
                        "developer-tracked-env",
                        "--jira-email",
                        "developer@example.test",
                        "--token-stdin",
                        "--confirm",
                    )
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(2, exit_code)
            self.assertEqual("workspace_env_tracked", payload["code"])
            self.assertEqual(original, env_path.read_text(encoding="utf-8"))
            self.assertFalse((state_root / "agent.json").exists())
            self.assertNotIn("new-token-secret-123", stdout.getvalue())

    def test_generated_agents_embeds_developer_rules_and_credentials_stay_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = self.prepare_install(root)
            (install / "developer" / "AGENTS.md").write_text(
                "# 研发工作入口\n\n不得加载 maintainer 工作面。\n",
                encoding="utf-8",
            )
            rule_path = install / "developer" / "rules" / "ai-execution.md"
            rule_path.parent.mkdir(parents=True, exist_ok=True)
            rule_path.write_text(
                "# 研发规则\n\n真实 Jira 写入必须经过人工门禁。\n",
                encoding="utf-8",
            )
            workspace = root / "business-repository"
            workspace.mkdir()
            subprocess.run(["git", "init", "-b", "main", str(workspace)], check=True, capture_output=True)

            stdout = io.StringIO()
            stderr = io.StringIO()
            stdin_stream = io.StringIO("token-secret-123\n")
            with (
                redirect_stdout(stdout),
                redirect_stderr(stderr),
                mock.patch("sys.stdin", stdin_stream),
                mock.patch(
                    "ao_work.workspace_init.service.UrllibJiraTransport",
                    return_value=WorkspaceInitTransport(),
                ),
                mock.patch(
                    "ao_work.work_cli.validate_install_root",
                    return_value=self.install_root,
                ),
                mock.patch.object(
                    WorkspaceInitializer, "_check_source", return_value="ready_to_clone"
                ),
                mock.patch.object(
                    WorkspaceInitializer, "_ensure_source_checkout", return_value="cloned"
                ),
            ):
                exit_code = main(
                    (
                    "--workspace-root",
                    str(workspace),
                    "workspace",
                    "init",
                    "--non-interactive",
                    "--project",
                    "tapdata",
                    "--agent-id",
                    "developer-git-boundary",
                    "--jira-email",
                    "developer@example.test",
                    "--token-stdin",
                    "--confirm",
                    )
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(0, exit_code)
            self.assertEqual("git_local_exclude", payload["credential_protection"])
            agents = (workspace / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("不得加载 maintainer 工作面", agents)
            self.assertIn("真实 Jira 写入必须经过人工门禁", agents)
            self.assertIn(".agents/skills/", agents)
            self.assertNotIn("developer/skills/", agents)
            self.assertNotIn(str(install / "developer" / "AGENTS.md"), agents)
            status = subprocess.run(
                ["git", "-C", str(workspace), "status", "--porcelain", "--untracked-files=all"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertNotIn(".agentic-ops/", status)
            self.assertNotIn("token-secret-123", status)

    def test_preflight_rejects_missing_or_maintainer_workspace_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            common = (
                "--workspace-root", str(workspace), "workspace", "init",
                "--non-interactive", "--project", "tapdata",
                "--agent-id", "developer-skill-boundary",
                "--jira-email", "developer@example.test", "--token-stdin", "--confirm",
            )
            self.assertEqual(0, self.run_cli(common)[0])
            missing = workspace / ".agents" / "skills" / "configure-authorization" / "SKILL.md"
            missing.unlink()
            exit_code, payload, _, _ = self.run_cli(
                ("--workspace-root", str(workspace), "workspace", "preflight")
            )
            self.assertEqual(2, exit_code)
            self.assertEqual("workspace_ai_asset_missing", payload["code"])

            missing.write_text(
                (self.install_root / "developer/skills/configure-authorization/SKILL.md").read_text(),
                encoding="utf-8",
            )
            maintainer = workspace / ".agents" / "skills" / "maintainer-release"
            maintainer.mkdir()
            (maintainer / "SKILL.md").write_text(
                "---\nname: maintainer-release\ndescription: forbidden\n---\n",
                encoding="utf-8",
            )
            exit_code, payload, _, _ = self.run_cli(
                ("--workspace-root", str(workspace), "workspace", "preflight")
            )
            self.assertEqual(2, exit_code)
            self.assertEqual("workspace_ai_asset_contaminated", payload["code"])

    def test_preflight_rejects_developer_rule_entry_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.prepare_install(root)
            workspace = root / "workspace"
            workspace.mkdir()
            common = (
                "--workspace-root", str(workspace), "workspace", "init",
                "--non-interactive", "--project", "tapdata",
                "--agent-id", "developer-rule-boundary",
                "--jira-email", "developer@example.test", "--token-stdin", "--confirm",
            )
            self.assertEqual(0, self.run_cli(common)[0])
            agents = workspace / "AGENTS.md"
            agents.write_text(
                agents.read_text(encoding="utf-8").replace(
                    "真实 Jira 写入必须经过人工门禁。",
                    "真实 Jira 写入可以跳过人工门禁。",
                ),
                encoding="utf-8",
            )
            exit_code, payload, _, _ = self.run_cli(
                ("--workspace-root", str(workspace), "workspace", "preflight")
            )
            self.assertEqual(2, exit_code)
            self.assertEqual("workspace_ai_entry_drift", payload["code"])
