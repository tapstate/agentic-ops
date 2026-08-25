from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ao_work.task_run.manifest import TaskRunManifestService
from ao_work.task_run.protocol import digest
from ao_work.task_run.service import TaskRunProtocol
from ao_work.output import RuntimeErrorResult
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.task_state.io import read_json
from ao_work.workspace import Workspace
from install_auth_fixture import configure_install_authorization, v5_agent


ISSUE_KEY = "TAP-12289"
RUN_ID = "run-TAP-12289-manifest"
REPOSITORY = "tapdata/tapdata-connectors"
TASK_BRANCH = "harsen-mini-test-bot/TAP-12289/develop"


class TaskRunManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        self.workspace_root = root / "workspace"
        self.workspace_root.mkdir()
        self.default_source = root / "default-source"
        self.default_source.mkdir()
        self.worktree = root / "task-worktree"
        self.worktree.mkdir()
        self.install = root / "install"
        configure_install_authorization(
            self.install,
            git_email="harsen-test-bot@example.com",
        )
        self._git("init", "-b", "develop")
        self._git("config", "user.name", "Harsen Test Bot")
        self._git("config", "user.email", "harsen-test-bot@example.com")
        (self.worktree / "README.md").write_text("# connector\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "初始化 connector")
        self._git("checkout", "-b", TASK_BRANCH)
        self.head = self._git("rev-parse", "HEAD")

        profile = self.install / "developer/standards/projects/tapdata/profile.yaml"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            "profile_id: tapdata\n"
            "connection_id: tapdata-cloud\n"
            "jira:\n"
            "  project_key: TAP\n"
            "  task_query: project = TAP\n"
            "repositories:\n"
            "  default: tapdata/tapdata\n"
            "  list: [tapdata/tapdata, tapdata/tapdata-connectors]\n"
            "statuses:\n"
            "  正在进行: implementation\n",
            encoding="utf-8",
        )
        state = self.workspace_root / ".agentic-ops"
        state.mkdir()
        config = state / "agent.json"
        config.write_text(
            json.dumps(
                v5_agent(
                    self.install,
                    project_profile="tapdata",
                    connection_id="tapdata-cloud",
                    jira_base_url="https://tapdata.atlassian.net",
                    jira_site="tapdata.atlassian.net",
                    jira_project="TAP",
                    source_root=str(self.default_source.resolve()),
                    repository="tapdata/tapdata",
                )
            ),
            encoding="utf-8",
        )
        overlay = state / "profiles/tapdata.local.yaml"
        overlay.parent.mkdir()
        overlay.write_text(
            "workspace:\n"
            f"  source_root: {self.default_source.resolve()}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )
        self.workspace = Workspace(
            root=self.workspace_root,
            workplane="developer",
            config_path=config,
        )
        self.store = TaskStore(self.workspace_root)
        self.store.initialize(
            TaskIdentity(
                connection_id="tapdata-cloud",
                jira_issue_id="12289",
                issue_key=ISSUE_KEY,
                project_key="TAP",
                agentic_run_id=RUN_ID,
            )
        )
        row = {
            "repository": REPOSITORY,
            "from_branch": "develop",
            "confirmed_branch_sha": self.head,
            "task_branch": TASK_BRANCH,
            "worktree_path": str(self.worktree.resolve()),
            "worktree_status": "not_created",
        }
        self.store.record_repository_proposal(
            ISSUE_KEY,
            RUN_ID,
            {
                "problem_version": "develop",
                "problem_version_repository": REPOSITORY,
                "problem_version_sha": self.head,
                "proposed_repository_branch_map": [row],
            },
        )
        self.store.confirm_repository_mapping(ISSUE_KEY, RUN_ID, [row], [])
        self.store.update_repository_worktree(
            ISSUE_KEY,
            RUN_ID,
            REPOSITORY,
            {
                "worktree_status": "prepared",
                "worktree_path": str(self.worktree.resolve()),
                "worktree_baseline_sha": self.head,
            },
        )
        run_root = state / "tasks" / ISSUE_KEY / "runs" / RUN_ID / "gates"
        run_root.mkdir(parents=True)
        source_context = {
                    "schema_version": 1,
                    "issue_key": ISSUE_KEY,
                    "agentic_run_id": RUN_ID,
                    "workspace_defaults": {
                        "project_profile": "tapdata",
                        "repository": REPOSITORY,
                        "source_root": str(self.worktree.resolve()),
                        "jira_base_url": "https://tapdata.atlassian.net",
                        "jira_account_id": "jira-account-1",
                    },
                    "issue": {
                        "id": "12289",
                        "key": ISSUE_KEY,
                        "project_key": "TAP",
                        "assignee_account_id": "jira-account-1",
                        "issue_content_sha256": "b" * 64,
                    },
                    "project_profile": {"profile_id": "tapdata"},
                    "runtime_readback": {
                        "agentic_run_id": RUN_ID,
                        "issue_content_sha256": "b" * 64,
                    },
                }
        source_context["context_digest"] = digest(source_context)
        source_context["observed_at"] = "2026-08-25T00:00:00+00:00"
        (run_root / "source-context.json").write_text(
            json.dumps(source_context, ensure_ascii=False),
            encoding="utf-8",
        )
        solution = {
                    "schema_version": 1,
                    "issue_key": ISSUE_KEY,
                    "agentic_run_id": RUN_ID,
                    "intake_digest": "a" * 64,
                    "source_context_digest": source_context["context_digest"],
                    "solution_level": "L1",
                    "head_sha": self.head,
                    "proposed_solution": "修复 MySQL 批读 SQL 并补充回归测试。",
                    "scope": {
                        "included": [
                            "connectors/mysql-connector/src/main/java/**",
                            "connectors/mysql-connector/src/test/java/**",
                        ],
                        "excluded": ["connectors/postgres-connector/**"],
                    },
                    "residual_risks": ["真实数据库回归仍由后续集成测试覆盖。"],
                    "execution_plan": {
                        "change_repository": REPOSITORY,
                        "verification": [
                            {
                                "id": "mysql-unit",
                                "command": [
                                    "mvn",
                                    "--batch-mode",
                                    "--offline",
                                    "-pl",
                                    "connectors/mysql-connector",
                                    "-am",
                                    "-Dtest=MysqlConnectorTest",
                                    "test",
                                ],
                                "working_directory": ".",
                                "timeout_seconds": 600,
                            }
                        ],
                        "review_summary": "只修改 MySQL connector 源码和测试，并推进到 PR 审查。",
                        "normalization_changes": [],
                    },
                }
        solution["solution_digest"] = digest(solution)
        solution["classified_at"] = "2026-08-25T00:01:00+00:00"
        (run_root / "solution.json").write_text(
            json.dumps(solution, ensure_ascii=False),
            encoding="utf-8",
        )
        self.service = TaskRunManifestService(
            self.workspace,
            self.install,
            self.store,
            lock_timeout=1.0,
        )

    def test_prepare_authorize_and_open_use_prepared_repository(self) -> None:
        prepared = self.service.prepare(ISSUE_KEY)
        self.assertTrue(prepared["confirmation_required"])
        self.assertEqual(
            "none", prepared["agentic_next_action"]["ownership_effect"]
        )
        self.assertEqual(
            REPOSITORY,
            prepared["confirmation_package"]["repository"]["slug"],
        )
        self.assertNotEqual(
            "tapdata/tapdata",
            prepared["confirmation_package"]["repository"]["slug"],
        )

        authorized = self.service.authorize(
            ISSUE_KEY,
            confirmed_by="研发工程师",
            confirm=True,
        )
        manifest_path = self.workspace_root / str(authorized["manifest_path"])
        manifest = read_json(manifest_path)
        self.assertEqual(REPOSITORY, manifest["repository"]["slug"])
        self.assertEqual(str(self.worktree.resolve()), manifest["repository"]["root"])
        self.assertTrue(authorized["authorization_reference"].startswith(
            f"user-confirmation:{ISSUE_KEY}:{RUN_ID}:"
        ))

        protocol = TaskRunProtocol(
            self.workspace,
            install_root=self.install,
            lock_timeout=1.0,
        )
        opened = protocol.open(str(authorized["manifest_path"]))
        self.assertTrue(opened["created"])
        self.assertIn(RUN_ID, opened["protocol_root"])
        (self.worktree / "README.md").write_text(
            "# connector\n\n实现已开始。\n", encoding="utf-8"
        )
        reopened = protocol.open(str(authorized["manifest_path"]))
        self.assertFalse(reopened["created"])

    def test_authorize_rejects_manually_changed_solution_before_output(self) -> None:
        self.service.prepare(ISSUE_KEY)
        solution_path = (
            self.workspace_root
            / ".agentic-ops"
            / "tasks"
            / ISSUE_KEY
            / "runs"
            / RUN_ID
            / "gates"
            / "solution.json"
        )
        solution = read_json(solution_path)
        solution["proposed_solution"] = "手工扩大方案范围。"
        solution_path.write_text(
            json.dumps(solution, ensure_ascii=False), encoding="utf-8"
        )

        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.authorize(
                ISSUE_KEY,
                confirmed_by="研发工程师",
                confirm=True,
            )

        self.assertEqual("task_run_solution_digest_mismatch", captured.exception.code)
        output_root = self.workspace_root / "inputs" / "agentic-ops" / ISSUE_KEY / RUN_ID
        self.assertFalse(output_root.exists())

    def _git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.worktree), *arguments],
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
