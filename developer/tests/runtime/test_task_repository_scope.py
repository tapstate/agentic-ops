from __future__ import annotations

import unittest
import tempfile
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
            domain_id="product",
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
            "review_and_confirm_task_domain", next_action["action"]
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
                action="review_and_confirm_task_domain",
                required_inputs=("confirmation_template",),
                allowed_operations=("task_repositories_confirm",),
                requires_authorization=True,
                stop_workflow=True,
                reason="请确认关系表。",
            ),
            _repository_next_action(
                executor="human",
                action="confirm_task_domain",
                required_inputs=("task_domain",),
                allowed_operations=("task_repositories_confirm",),
                requires_authorization=True,
                stop_workflow=True,
                reason="请确认完整关系表。",
            ),
            _repository_next_action(
                executor="ai",
                action="prepare_confirmed_domain_worktrees",
                required_inputs=(),
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

    def test_domain_confirmation_uses_runtime_derived_repository_map(self) -> None:
        repository = "tapdata/tapdata-connectors"
        sha = "a" * 40
        proposal_row = {
            "repository": repository,
            "proposed_from_branch": "develop",
            "task_branch": "agent/TAPSTATE-87/develop",
            "derivation_rule": "tap_align_branches",
            "analysis_branch": "develop",
            "analysis_baseline_sha": sha,
        }
        profile = mock.Mock()
        profile.repository_candidates.return_value = (repository,)
        profile.domain_for.return_value = SimpleNamespace(domain_id="product")
        context = SimpleNamespace(profile=profile)
        store = mock.Mock()
        store.inspect.return_value = {
            "task": {"agentic_run_id": "run-TAPSTATE-87"},
            "repository_scope": {
                "task_domain": "product",
                "problem_version": "develop",
                "problem_version_repository": repository,
                "problem_version_sha": sha,
                "proposed_repository_branch_map": [proposal_row],
            },
        }
        store.confirm_repository_mapping.return_value = {"path": "/state/scope.json"}
        mapping = {
            "issue_key": "TAPSTATE-87",
            "agentic_run_id": "run-TAPSTATE-87",
            "task_domain": "product",
            "problem_version": "develop",
            "problem_version_repository": repository,
            "problem_version_sha": sha,
        }

        with (
            mock.patch.object(
                task_repository_scope,
                "_live_context",
                return_value=(context, object(), object()),
            ),
            mock.patch.object(
                task_repository_scope, "resolve_source_pool_root", return_value=Path("/pool")
            ),
            mock.patch.object(task_repository_scope, "_refresh_pool_member"),
            mock.patch.object(
                task_repository_scope, "_resolve_remote_baseline", return_value=sha
            ),
            mock.patch.object(
                task_repository_scope,
                "load_install_identity",
                return_value={"execution_identity": {"github_actor_login": "agent"}},
            ),
            mock.patch.object(
                task_repository_scope,
                "task_worktree_path",
                return_value=Path("/pool/.worktree/TAPSTATE-87/connectors/develop"),
            ),
        ):
            result = task_repository_scope.execute_repository_confirm(
                SimpleNamespace(),
                Path("/install"),
                store,
                "TAPSTATE-87",
                mapping,
                confirm=True,
            )
            with self.assertRaises(Exception) as captured:
                task_repository_scope.execute_repository_confirm(
                    SimpleNamespace(),
                    Path("/install"),
                    store,
                    "TAPSTATE-87",
                    {**mapping, "repository_branch_map": []},
                    confirm=True,
                )

        confirmed = store.confirm_repository_mapping.call_args.args[2]
        self.assertEqual(
            "repository_mapping_override_unsupported", captured.exception.code
        )
        self.assertEqual("product", result["task_domain"])
        self.assertEqual(repository, confirmed[0]["repository"])
        self.assertEqual("develop", confirmed[0]["from_branch"])
        self.assertEqual(
            "prepare_confirmed_domain_worktrees",
            result["agentic_next_action"]["action"],
        )

    def test_prepare_without_repository_creates_entire_confirmed_domain(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        pool = Path(temporary.name)
        repositories = ("tapdata/tapdata", "tapdata/tapdata-connectors")
        rows = [
            {
                "repository": repository,
                "from_branch": "develop",
                "confirmed_branch_sha": "a" * 40,
                "task_branch": f"agent/TAPSTATE-87/{repository.rsplit('/', 1)[1]}",
                "worktree_status": "not_created",
            }
            for repository in repositories
        ]
        scope = {
            "task_domain": "product",
            "problem_version": "develop",
            "problem_version_repository": "tapdata/tapdata",
            "confirmed_repository_branch_map": rows,
            "actual_change_repositories": [],
        }
        store = mock.Mock()
        store.inspect.return_value = {
            "task": {"agentic_run_id": "run-TAPSTATE-87"},
            "repository_scope": scope,
        }
        context = SimpleNamespace(profile=SimpleNamespace(status_mapping={"进行中": "implementation"}))
        issue = SimpleNamespace(status="进行中")

        def prepared_plan(plan, **_kwargs):
            return task_repository_scope.TaskWorktreePlan(
                issue_key=plan.issue_key,
                from_branch=plan.from_branch,
                pool_root=plan.pool_root,
                entries=tuple(
                    task_repository_scope.WorktreePlanEntry(
                        entry.repository,
                        entry.worktree_dir,
                        entry.branch,
                        created=True,
                    )
                    for entry in plan.entries
                ),
                target_repository=plan.target_repository,
                baseline_repository=plan.baseline_repository,
            )

        git_result = SimpleNamespace(returncode=1, stdout="", stderr="")

        with (
            mock.patch.object(
                task_repository_scope,
                "_live_context",
                return_value=(context, object(), issue),
            ),
            mock.patch.object(
                task_repository_scope, "resolve_source_pool_root", return_value=pool
            ),
            mock.patch.object(
                task_repository_scope,
                "load_install_identity",
                return_value={"execution_identity": {}},
            ),
            mock.patch.object(
                task_repository_scope,
                "prepare_task_worktrees",
                side_effect=prepared_plan,
            ) as prepare,
            mock.patch.object(
                task_repository_scope, "_git_text", return_value="a" * 40
            ),
            mock.patch.object(
                task_repository_scope,
                "subprocess_git",
                side_effect=lambda arguments, timeout: (
                    SimpleNamespace(returncode=0, stdout="", stderr="")
                    if "switch" in arguments
                    else git_result
                ),
            ),
            mock.patch.object(
                task_repository_scope,
                "record_current_task_source_context",
                return_value={"intake_source": "inputs/intake.json"},
            ) as record_context,
        ):
            result = task_repository_scope.execute_worktree_prepare(
                SimpleNamespace(), Path("/install"), store, "TAPSTATE-87"
            )

        plan = prepare.call_args.args[0]
        self.assertEqual(set(repositories), {entry.repository for entry in plan.entries})
        self.assertEqual(2, store.update_repository_worktree.call_count)
        self.assertEqual(2, len(result["worktrees"]))
        self.assertEqual(
            2,
            len(record_context.call_args.kwargs["confirmed_worktrees"]),
        )
