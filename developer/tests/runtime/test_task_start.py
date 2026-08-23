from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ao_work.config import load_project_profile
from ao_work.jira.adf import markdown_to_adf
from ao_work.jira.client import TransportResponse
from ao_work.task_start import (
    _prepare_pool_task_worktrees,
    _profile_snapshot,
    _resolve_non_pool_target_branch,
)
from ao_work.task_worktree import TaskWorktreePlan, WorktreePlanEntry
from ao_work.work_cli import main
from install_auth_fixture import configure_install_authorization, v5_agent


class TaskStartTransport:
    def __init__(self, *, assignee: str = "jira-account-1", status: str = "正在进行") -> None:
        self.assignee = assignee
        self.status = status
        self.requests: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> TransportResponse:
        self.requests.append((method, path))
        if path == "/rest/api/3/myself":
            return TransportResponse(
                200,
                {"accountId": "jira-account-1", "displayName": "Harsen Test Bot"},
            )
        if path == "/rest/api/3/issue/TAP-12289":
            return TransportResponse(
                200,
                {
                    "id": "12289",
                    "key": "TAP-12289",
                    "fields": {
                        "project": {"key": "TAP"},
                        "summary": "减少 AgenticOps 配置阻塞",
                        "status": {"name": self.status},
                        "issuetype": {"name": "任务"},
                        "assignee": {"accountId": self.assignee},
                        "description": markdown_to_adf("从 Jira 自动读取任务信息。"),
                    },
                },
            )
        return TransportResponse(404, None)


class TaskStartTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.install = root / "install"
        self.workspace = root / "workspace"
        self.source = root / "source"
        self.workspace.mkdir()
        self.source.mkdir()
        connection = self.install / "developer/standards/connections/tapdata-cloud.yaml"
        profile = self.install / "developer/standards/projects/tapdata/profile.yaml"
        connection.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        connection.write_text(
            "connection_id: tapdata-cloud\n"
            "base_url: https://tapdata.atlassian.net\n"
            "auth:\n"
            "  email_env: TAPDATA_JIRA_EMAIL\n"
            "  token_env: TAPDATA_JIRA_TOKEN\n",
            encoding="utf-8",
        )
        profile.write_text(
            "profile_id: tapdata\n"
            "connection_id: tapdata-cloud\n"
            "jira:\n"
            "  project_key: TAP\n"
            "  issue_types: [任务]\n"
            "statuses:\n"
            "  正在进行: implementation\n"
            "  完成: completed\n"
            "repositories:\n"
            "  default: tapdata/tapdata\n",
            encoding="utf-8",
        )
        install_identity_ref = configure_install_authorization(self.install)
        state = self.workspace / ".agentic-ops"
        (state / "profiles").mkdir(parents=True)
        (state / "agent.json").write_text(
            json.dumps(
                v5_agent(
                    self.install,
                    project_profile="tapdata",
                    jira_project="TAP",
                    connection_id="tapdata-cloud",
                    jira_base_url="https://tapdata.atlassian.net",
                    jira_site="tapdata.atlassian.net",
                    source_root=str(self.source.resolve()),
                    repository="tapdata/tapdata",
                )
            ),
            encoding="utf-8",
        )
        (state / "profiles/tapdata.local.yaml").write_text(
            "workspace:\n"
            f"  source_root: {self.source.resolve()}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )
        (state / ".env").write_text(
            "TAPDATA_JIRA_EMAIL=harsen@example.test\n"
            "TAPDATA_JIRA_TOKEN=test-token-secret\n",
            encoding="utf-8",
        )

    def run_cli(
        self, transport: TaskStartTransport
    ) -> tuple[int, dict[str, object], str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch("ao_work.work_cli.validate_install_root", return_value=self.install),
            mock.patch(
                "ao_work.task_start.UrllibJiraTransport",
                return_value=transport,
            ),
        ):
            code = main(
                (
                    "--workspace-root",
                    str(self.workspace),
                    "task",
                    "start",
                    "TAP-12289",
                )
            )
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def test_task_start_resolves_workspace_profile_and_jira_without_questionnaire(self) -> None:
        code, payload, stderr = self.run_cli(TaskStartTransport())
        self.assertEqual(0, code, (payload, stderr))
        self.assertEqual("TAP-12289", payload["issue"]["key"])
        self.assertEqual("12289", payload["issue"]["id"])
        self.assertEqual("implementation", payload["issue"]["mapped_status"])
        self.assertEqual("tapdata/tapdata", payload["workspace_defaults"]["repository"])
        self.assertEqual(
            "harsen-mini-test-bot", payload["workspace_defaults"]["agent_id"]
        )
        self.assertTrue(payload["task_state_created"])
        self.assertTrue(str(payload["agentic_run_id"]).startswith("run-TAP-12289-"))
        self.assertEqual(3, len(payload["review_required"]))
        self.assertIn("从 Jira 自动读取", payload["issue"]["description"])
        self.assertEqual("ai", payload["agentic_next_action"]["executor"])
        self.assertEqual(
            "harsen-mini-test-bot", payload["task_ownership"]["task_owner"]
        )
        self.assertEqual(
            "same_owner_until_pr_review",
            payload["task_ownership"]["continuity"],
        )
        self.assertEqual(
            "capability_gap", payload["task_ownership"]["transfer_capability"]
        )
        self.assertEqual("none", payload["agentic_next_action"]["ownership_effect"])
        self.assertEqual(
            "assess_task_intake",
            payload["agentic_next_action"]["action"],
        )
        self.assertEqual(
            [
                "issue_key",
                "agentic_run_id",
                "intake_input_file",
            ],
            payload["agentic_next_action"]["required_inputs"],
        )
        self.assertFalse(payload["agentic_next_action"]["requires_authorization"])
        self.assertEqual(
            ["task_intake_assess"],
            payload["agentic_next_action"]["allowed_operations"],
        )
        self.assertRegex(payload["intake_source"]["context_digest"], r"^[0-9a-f]{64}$")
        self.assertTrue(Path(payload["intake_source"]["source_context_path"]).is_file())
        self.assertEqual(
            "prepare_and_classify_solution",
            payload["intake_gate"]["required_sequence"][-1],
        )
        self.assertFalse(
            payload["intake_gate"]["user_confirmation_required_before_solution"]
        )
        self.assertEqual(
            "review_task_design",
            payload["solution_gate"]["levels"]["L1"]["action"],
        )
        self.assertEqual(
            "decide_solution_risk",
            payload["solution_gate"]["levels"]["L2"]["action"],
        )
        self.assertEqual(
            {"L1", "L2", "L3", "L4"},
            set(payload["solution_gate"]["levels"]),
        )

        second_code, second, second_stderr = self.run_cli(TaskStartTransport())
        self.assertEqual(0, second_code, (second, second_stderr))
        self.assertFalse(second["task_state_created"])
        self.assertEqual(payload["agentic_run_id"], second["agentic_run_id"])

    def test_task_start_blocks_wrong_owner_and_completed_task_before_local_state(self) -> None:
        for transport, expected in (
            (TaskStartTransport(assignee="another-user"), "jira_assignee_mismatch"),
            (TaskStartTransport(status="完成"), "jira_task_already_completed"),
        ):
            with self.subTest(expected=expected):
                code, payload, _ = self.run_cli(transport)
                self.assertEqual(2, code)
                self.assertEqual(expected, payload["code"])
                self.assertFalse((self.workspace / ".agentic-ops/tasks/TAP-12289").exists())


class ProjectProfileSnapshotTest(unittest.TestCase):
    def test_problem_version_and_actual_target_branch_are_distinct(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        issue = SimpleNamespace(
            description=markdown_to_adf("## 问题版本\n\ndevelop\n\n补充备注。\n"),
            assignee="jira-account-1",
            summary="测试任务",
            fields={},
        )

        snapshot = _profile_snapshot(
            profile,
            issue,
            task_worktrees={
                "repository": "tapdata/tapdata-common-lib",
                "problem_version": "develop",
                "target_branch": "release-v1.2.6",
            },
        )

        resolved = snapshot["resolved_fields"]
        self.assertEqual("develop", resolved["problem_version"]["value"])
        self.assertEqual(
            "task_worktrees.problem_version",
            resolved["problem_version"]["reference"],
        )
        self.assertEqual("release-v1.2.6", resolved["target_branch"]["value"])
        self.assertEqual(
            "task_worktrees.target_branch",
            resolved["target_branch"]["reference"],
        )
        self.assertEqual(
            "tapdata/tapdata-common-lib",
            resolved["target_repo"]["value"],
        )

    def test_baseline_problem_version_fills_source_context_when_jira_omits_it(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        issue = SimpleNamespace(
            description=markdown_to_adf("没有显式问题版本。"),
            assignee="jira-account-1",
            summary="测试任务",
            fields={},
        )

        snapshot = _profile_snapshot(
            profile,
            issue,
            task_worktrees={
                "repository": "tapdata/tapdata-common-lib",
                "problem_version": "develop",
                "target_branch": "release-v1.2.6",
            },
        )

        problem_version = snapshot["resolved_fields"]["problem_version"]
        self.assertEqual("develop", problem_version["value"])
        self.assertEqual("task_worktrees.problem_version", problem_version["reference"])
        self.assertEqual("jira_description_section", problem_version["source"])
        self.assertEqual("问题版本", problem_version["section"])

    def test_renamed_product_domain_still_uses_alignment_script(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        loaded = load_project_profile(repository_root, "tapdata")
        product = loaded.worktree_domains[0]
        profile = replace(
            loaded,
            worktree_domains=(
                replace(product, domain_id="renamed-product"),
                *loaded.worktree_domains[1:],
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            pool = Path(temporary).resolve()
            target_dir = pool / ".worktree/TAP-123/develop/tapdata/tapdata"
            prepared = TaskWorktreePlan(
                issue_key="TAP-123",
                from_branch="develop",
                pool_root=pool,
                entries=(
                    WorktreePlanEntry(
                        repository="tapdata/tapdata",
                        worktree_dir=target_dir,
                        branch="develop",
                    ),
                ),
                target_repository="tapdata/tapdata",
                baseline_repository="tapdata/tapdata",
            )
            issue = SimpleNamespace(
                key="TAP-123",
                description=markdown_to_adf("## 问题版本\n\ndevelop\n"),
            )
            with (
                mock.patch(
                    "ao_work.task_start.resolve_source_pool_root",
                    return_value=pool,
                ),
                mock.patch(
                    "ao_work.task_start.plan_task_worktrees",
                    return_value=prepared,
                ) as planned,
                mock.patch(
                    "ao_work.task_start.prepare_task_worktrees",
                    return_value=prepared,
                ),
            ):
                _prepare_pool_task_worktrees(
                    install_root=pool / "install",
                    profile=profile,
                    issue=issue,
                    agent_config={"source_root": str(pool)},
                )

        alignment_script = planned.call_args.kwargs["alignment_script"]
        self.assertEqual(
            pool
            / "install/developer/standards/projects/tapdata/scripts/tap_align_branches.py",
            alignment_script,
        )

    def test_non_pool_checkout_resolves_required_target_branch(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        issue = SimpleNamespace(
            description=markdown_to_adf(
                "## 目标仓库\n\ntapdata/tapdata-common-lib\n\n"
                "## 问题版本\n\ndevelop\n"
            ),
            assignee="jira-account-1",
            summary="测试任务",
            fields={},
        )

        target_branch = _resolve_non_pool_target_branch(
            profile,
            issue,
            Path("/nonexistent/source"),
            "tapdata/tapdata-common-lib",
        )
        snapshot = _profile_snapshot(
            profile,
            issue,
            target_branch=target_branch,
        )

        self.assertEqual("main", target_branch)
        resolved = snapshot["resolved_fields"]["target_branch"]
        self.assertEqual("main", resolved["value"])
        self.assertEqual("workspace_defaults.target_branch", resolved["reference"])


if __name__ == "__main__":
    unittest.main()
