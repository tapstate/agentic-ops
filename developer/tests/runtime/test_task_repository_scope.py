from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ao_work.output import success
from ao_work import task_repository_scope
from ao_work.task_repository_scope import _jira_branch_overrides, _repository_next_action


class RepositoryScopeNextActionTest(unittest.TestCase):
    def test_jira_branch_overrides_accept_known_repositories(self) -> None:
        overrides = _jira_branch_overrides(
            {
                "仓库分支": (
                    "tapdata/tapdata-connectors: develop\n"
                    "tapdata/tapdata-connectors-enterprise: release-v2.0.8"
                )
            },
            {
                "tapdata/tapdata-connectors",
                "tapdata/tapdata-connectors-enterprise",
            },
        )

        self.assertEqual(
            {
                "tapdata/tapdata-connectors": "develop",
                "tapdata/tapdata-connectors-enterprise": "release-v2.0.8",
            },
            overrides,
        )

    def test_jira_branch_overrides_reject_unknown_repository(self) -> None:
        with self.assertRaises(Exception) as captured:
            _jira_branch_overrides(
                {"仓库分支": "tapdata/unknown: develop"},
                {"tapdata/tapdata-connectors"},
            )

        self.assertEqual("jira_repository_branch_invalid", captured.exception.code)

    def test_assess_output_has_fixed_control_fields(self) -> None:
        profile = mock.Mock()
        profile.domain_for.return_value = SimpleNamespace(
            problem_version_repository="tapdata/tapdata",
            baseline_repository="tapdata/tapdata",
        )
        profile.baseline_branch.return_value = "develop"
        profile.repository_candidates.return_value = ("tapdata/tapdata",)
        context = SimpleNamespace(profile=profile)
        issue = SimpleNamespace(description="")
        analyzed = SimpleNamespace(
            entries=(
                SimpleNamespace(
                    repository="tapdata/tapdata",
                    branch="develop",
                ),
            ),
            baseline_repository="tapdata/tapdata",
            from_branch="develop",
            pool_root=Path("/pool"),
        )
        store = mock.Mock()
        store.inspect.return_value = {"task": {"agentic_run_id": "run-TAPSTATE-87"}}
        store.record_repository_proposal.return_value = {"path": "/state/scope.json"}

        with (
            mock.patch.object(
                task_repository_scope,
                "_live_context",
                return_value=(context, object(), issue),
            ),
            mock.patch.object(
                task_repository_scope,
                "resolve_source_pool_root",
                return_value=Path("/pool"),
            ),
            mock.patch.object(
                task_repository_scope,
                "resolve_target_repository",
                return_value="tapdata/tapdata",
            ),
            mock.patch.object(
                task_repository_scope,
                "plan_task_worktrees",
                return_value=object(),
            ),
            mock.patch.object(
                task_repository_scope,
                "analyze_task_worktree_plan",
                return_value=(analyzed, {"tapdata/tapdata": "a" * 40}),
            ),
            mock.patch.object(
                task_repository_scope,
                "load_install_identity",
                return_value={"execution_identity": {"github_actor_login": "agent"}},
            ),
            mock.patch.object(
                task_repository_scope,
                "_resolve_remote_baseline",
                return_value="a" * 40,
            ),
            mock.patch.object(
                task_repository_scope,
                "task_worktree_path",
                return_value=Path("/pool/.worktree/TAPSTATE-87/tapdata"),
            ),
        ):
            result = task_repository_scope.execute_repository_assess(
                SimpleNamespace(), Path("/install"), store, "TAPSTATE-87"
            )

        rendered = success("task_repositories_assess", **result)
        next_action = rendered["agentic_next_action"]
        self.assertEqual("human", next_action["executor"])
        self.assertEqual(
            "review_and_confirm_repository_branch_mapping", next_action["action"]
        )
        self.assertEqual(["confirmation_template"], next_action["required_inputs"])
        self.assertEqual(
            ["task_repositories_confirm"], next_action["allowed_operations"]
        )
        self.assertTrue(next_action["requires_authorization"])
        self.assertTrue(next_action["stop_workflow"])
        self.assertEqual("none", next_action["ownership_effect"])

    def test_next_actions_have_fixed_control_fields(self) -> None:
        cases = (
            _repository_next_action(
                executor="human",
                action="review_and_confirm_repository_branch_mapping",
                required_inputs=("confirmation_template",),
                allowed_operations=("task_repositories_confirm",),
                requires_authorization=True,
                stop_workflow=True,
                reason="请确认关系表。",
            ),
            _repository_next_action(
                executor="human",
                action="confirm_repository_branch_mapping",
                required_inputs=("repository_branch_map",),
                allowed_operations=("task_repositories_confirm",),
                requires_authorization=True,
                stop_workflow=True,
                reason="请确认完整关系表。",
            ),
            _repository_next_action(
                executor="ai",
                action="prepare_confirmed_repository_worktree_when_needed",
                required_inputs=("repository",),
                allowed_operations=("task_worktrees_prepare",),
                requires_authorization=False,
                stop_workflow=False,
                reason="按需创建工作树。",
            ),
            _repository_next_action(
                executor="ai",
                action="assess_task_intake",
                required_inputs=("issue_key", "agentic_run_id", "intake_input_file"),
                allowed_operations=("task_intake_assess",),
                requires_authorization=False,
                stop_workflow=False,
                reason="继续信息分析。",
            ),
        )

        for next_action in cases:
            with self.subTest(action=next_action["action"]):
                result = success(
                    "task_repositories_assess", agentic_next_action=next_action
                )
                self.assertEqual(next_action, result["agentic_next_action"])
