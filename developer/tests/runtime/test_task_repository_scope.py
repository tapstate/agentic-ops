from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ao_work.output import success
from ao_work.output import RuntimeErrorResult
from ao_work import task_repository_scope
from ao_work.task_repository_scope import (
    _jira_branch_overrides,
    _repository_next_action,
    _stale_worktree_cleanup_plan,
)
from ao_work.workspace import task_worktree_path


class RepositoryScopeNextActionTest(unittest.TestCase):
    def test_unresolved_branch_stops_for_human_with_domain_bound_recovery(self) -> None:
        profile = mock.Mock()
        domain = SimpleNamespace(
            domain_id="assistant",
            problem_version_repository="tapdata/tapdata",
            baseline_repository="tapdata/tapdata",
        )
        profile.domain_by_id.return_value = domain
        profile.repository_candidates.return_value = (
            "tapdata/tapdata",
            "tapdata/tapdata-common-lib",
        )
        context = SimpleNamespace(profile=profile)
        store = mock.Mock()
        store.inspect.return_value = {"task": {"agentic_run_id": "run-TAP-123"}}
        alignment_error = RuntimeErrorResult(
            code="branch_alignment_failed",
            message="无法对齐领域仓库分支：tapdata/tapdata-common-lib",
            status="blocked",
            exit_code=2,
            retry_safe=False,
            required_human_action="请补充明确的关联仓库分支后重试",
            details={
                "issue_key": "TAP-123",
                "repository": "tapdata/tapdata-common-lib",
                "problem_version": "develop",
                "reason": "对应 release 分支无法解析",
                "repository_branch_override_template": (
                    "tapdata/tapdata-common-lib: <confirmed-branch>"
                ),
            },
        )

        with (
            mock.patch.object(
                task_repository_scope,
                "_live_context",
                return_value=(context, object(), SimpleNamespace(description=""), object()),
            ),
            mock.patch.object(task_repository_scope, "collect_task_facts", return_value={}),
            mock.patch.object(
                task_repository_scope, "resolve_source_pool_root", return_value=Path("/pool")
            ),
            mock.patch.object(
                task_repository_scope,
                "resolve_target_repository",
                return_value="tapdata/tapdata",
            ),
            mock.patch.object(task_repository_scope, "plan_task_worktrees", return_value=object()),
            mock.patch.object(
                task_repository_scope,
                "analyze_task_worktree_plan",
                side_effect=alignment_error,
            ),
        ):
            with self.assertRaises(RuntimeErrorResult) as captured:
                task_repository_scope.execute_repository_assess(
                    SimpleNamespace(),
                    Path("/install"),
                    store,
                    "TAP-123",
                    task_domain="assistant",
                )

        error = captured.exception
        self.assertEqual("assistant", error.details["task_domain"])
        self.assertFalse(error.retry_safe)
        next_action = error.next_step
        self.assertEqual("human", next_action["executor"])
        self.assertEqual("confirm_repository_branch_override", next_action["action"])
        self.assertEqual(["jira_description_plan"], next_action["allowed_operations"])
        self.assertNotIn("jira_inspect", next_action["allowed_operations"])
        self.assertIn("task_domain", next_action["required_inputs"])
        self.assertTrue(next_action["requires_authorization"])
        self.assertTrue(next_action["stop_workflow"])

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
        store.record_repository_proposal.return_value = {
            "repository_scope": {
                "content_version": 1,
                "issue_key": "TAPSTATE-87",
                "agentic_run_id": "run-TAPSTATE-87",
                "task_domain": "product",
                "problem_version": "develop",
                "problem_version_repository": "tapdata/tapdata",
                "problem_version_sha": "a" * 40,
                "proposed_repository_branch_map": [],
            }
        }

        with (
            mock.patch.object(
                task_repository_scope,
                "_live_context",
                return_value=(context, object(), issue, object()),
            ),
            mock.patch.object(task_repository_scope, "collect_task_facts", return_value={}),
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
            ) as plan_task_worktrees,
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
            mock.patch.object(task_repository_scope, "RepositoryConfirmationStore") as confirmations,
        ):
            confirmations.return_value.create.return_value = {
                "confirmation_id": "rc_" + "a" * 32,
                "status": "recorded",
            }
            result = task_repository_scope.execute_repository_assess(
                SimpleNamespace(root=Path("/workspace")), Path("/install"), store, "TAPSTATE-87"
            )

        rendered = success("task_repositories_assess", **result)
        self.assertEqual({}, plan_task_worktrees.call_args.kwargs["branch_overrides"])
        next_action = rendered["next_step"]
        self.assertEqual("human", next_action["executor"])
        self.assertEqual(
            "review_and_confirm_task_domain", next_action["action"]
        )
        self.assertEqual(["confirmation_id", "task_domain"], next_action["required_inputs"])
        self.assertEqual("rc_" + "a" * 32, result["confirmation_ref"]["confirmation_id"])
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
                required_inputs=("confirmation_id", "task_domain"),
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
                    "task_repositories_assess", next_step=next_action
                )
                normalized = result["next_step"]
                for field, value in next_action.items():
                    self.assertEqual(value, normalized[field])
                for field in (
                    "operation_id",
                    "command_argv",
                    "command_line",
                    "bound_arguments",
                    "input_artifacts",
                    "reason",
                ):
                    self.assertIn(field, normalized)

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
                "content_version": 1,
            },
        }
        store.confirm_repository_mapping.return_value = {"path": "/state/scope.json"}
        confirmation_id = "rc_" + "a" * 32

        with (
            mock.patch.object(
                task_repository_scope,
                "_live_context",
                return_value=(context, object(), object(), object()),
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
            mock.patch.object(task_repository_scope, "RepositoryConfirmationStore") as confirmations,
        ):
            confirmations.return_value.validate.return_value = {"status": "recorded"}
            confirmations.return_value.reference.return_value = {"confirmation_id": confirmation_id}
            confirmations.return_value.consume.return_value = {"confirmation_id": confirmation_id, "status": "consumed"}
            result = task_repository_scope.execute_repository_confirm(
                SimpleNamespace(root=Path("/workspace")),
                Path("/install"),
                store,
                "TAPSTATE-87",
                confirmation_id,
                "product",
                confirm=True,
            )
            with self.assertRaises(Exception) as captured:
                task_repository_scope.execute_repository_confirm(
                    SimpleNamespace(root=Path("/workspace")),
                    Path("/install"),
                    store,
                    "TAPSTATE-87",
                    confirmation_id,
                    "assistant",
                    confirm=True,
                )

        confirmed = store.confirm_repository_mapping.call_args.args[2]
        self.assertEqual(
            "task_domain_changed", captured.exception.code
        )
        self.assertEqual("product", result["task_domain"])
        self.assertEqual(repository, confirmed[0]["repository"])
        self.assertEqual("develop", confirmed[0]["from_branch"])
        self.assertEqual(
            "prepare_confirmed_domain_worktrees",
            result["next_step"]["action"],
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
                return_value=(context, object(), issue, object()),
            ),
            mock.patch.object(task_repository_scope, "collect_task_facts", return_value={}),
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


class StaleWorktreeRecoveryTest(unittest.TestCase):
    def test_plan_only_allows_clean_branch_without_unique_commits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = Path(temporary) / "pool"
            member = pool / "tapdata/tapdata-connectors"
            member.mkdir(parents=True)
            self._git(member, "init", "-b", "develop")
            self._git(member, "config", "user.name", "Harsen Test Bot")
            self._git(member, "config", "user.email", "harsen@example.test")
            (member / "README.md").write_text("old\n", encoding="utf-8")
            self._git(member, "add", "README.md")
            self._git(member, "commit", "-m", "old baseline")
            old_head = self._git(member, "rev-parse", "HEAD")
            (member / "README.md").write_text("new\n", encoding="utf-8")
            self._git(member, "commit", "-am", "new baseline")
            confirmed_head = self._git(member, "rev-parse", "HEAD")
            issue_key = "TAPSTATE-90"
            repository = "tapdata/tapdata-connectors"
            task_branch = "HarsenLin/TAPSTATE-90/develop"
            worktree = task_worktree_path(pool, issue_key, "develop", repository)
            worktree.parent.mkdir(parents=True)
            self._git(
                member,
                "worktree",
                "add",
                "-b",
                task_branch,
                str(worktree),
                old_head,
            )
            row = {
                "repository": repository,
                "from_branch": "develop",
                "confirmed_branch_sha": confirmed_head,
                "task_branch": task_branch,
                "worktree_status": "not_created",
            }
            scope = {
                "content_version": 3,
                "actual_change_repositories": [],
            }

            plan = _stale_worktree_cleanup_plan(
                pool, issue_key, "run-TAPSTATE-90", scope, [row]
            )

            self.assertTrue(plan["eligible"])
            self.assertEqual(1, len(plan["candidates"]))
            candidate = plan["candidates"][0]
            self.assertTrue(candidate["eligible"])
            self.assertEqual(old_head, candidate["head_sha"])
            self.assertEqual([], candidate["blocking_reasons"])

    def test_confirmed_recovery_removes_exact_candidates_then_retries_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pool = Path(temporary) / "pool"
            member = pool / "tapdata/tapdata-connectors"
            member.mkdir(parents=True)
            (pool / ".locks").mkdir()
            self._git(member, "init", "-b", "develop")
            self._git(member, "config", "user.name", "Harsen Test Bot")
            self._git(member, "config", "user.email", "harsen@example.test")
            (member / "README.md").write_text("old\n", encoding="utf-8")
            self._git(member, "add", "README.md")
            self._git(member, "commit", "-m", "old baseline")
            old_head = self._git(member, "rev-parse", "HEAD")
            (member / "README.md").write_text("new\n", encoding="utf-8")
            self._git(member, "commit", "-am", "new baseline")
            confirmed_head = self._git(member, "rev-parse", "HEAD")
            issue_key = "TAPSTATE-90"
            repository = "tapdata/tapdata-connectors"
            task_branch = "HarsenLin/TAPSTATE-90/develop"
            worktree = task_worktree_path(pool, issue_key, "develop", repository)
            worktree.parent.mkdir(parents=True)
            self._git(member, "worktree", "add", "-b", task_branch, str(worktree), old_head)
            row = {
                "repository": repository,
                "from_branch": "develop",
                "confirmed_branch_sha": confirmed_head,
                "task_branch": task_branch,
                "worktree_status": "not_created",
            }
            scope = {
                "content_version": 3,
                "confirmed_repository_branch_map": [row],
                "actual_change_repositories": [],
            }
            store = mock.Mock()
            store.inspect.return_value = {
                "task": {"agentic_run_id": "run-TAPSTATE-90"},
                "repository_scope": scope,
            }
            with (
                mock.patch.object(task_repository_scope, "resolve_source_pool_root", return_value=pool),
                mock.patch.object(
                    task_repository_scope,
                    "execute_worktree_prepare",
                    return_value={"next_step": {"action": "assess_task_intake"}},
                ) as prepare,
            ):
                preview = task_repository_scope.execute_worktree_recover(
                    SimpleNamespace(), Path("/install"), store, issue_key,
                    cleanup_digest=None, confirm=False,
                )
                result = task_repository_scope.execute_worktree_recover(
                    SimpleNamespace(), Path("/install"), store, issue_key,
                    cleanup_digest=preview["cleanup_plan"]["cleanup_digest"], confirm=True,
                )

            self.assertTrue(preview["confirmation_required"])
            self.assertEqual([repository], [item["repository"] for item in result["cleanup"]])
            self.assertFalse(worktree.exists())
            self.assertNotEqual(
                0,
                subprocess.run(
                    ["git", "-C", str(member), "rev-parse", "--verify", f"refs/heads/{task_branch}"],
                    capture_output=True,
                    text=True,
                ).returncode,
            )
            store.append_decision.assert_called_once()
            prepare.assert_called_once()
            self.assertEqual("assess_task_intake", result["next_step"]["action"])

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
