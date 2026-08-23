from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work.config import load_project_profile
from ao_work.output import RuntimeErrorResult
from ao_work.task_worktree import (
    TaskWorktreePlan,
    _run_git,
    plan_task_worktrees,
    prepare_task_worktrees,
    resolve_from_branch,
    resolve_target_repository,
)
from ao_work.workspace import task_worktree_path


def build_profile(
    *,
    default_repository: str = "tapdata/tapdata",
    repository_list: tuple[str, ...] = ("tapdata/tapdata", "tapdata/tapdata-web"),
    analysis_mount: dict | None = None,
    branch_derivation: dict | None = None,
    baseline_branches: dict[str, str] | None = None,
) -> object:
    from ao_work.config.model import (
        AnalysisMount,
        BranchDerivation,
        ProjectProfile,
        RepositoryBranchRule,
    )

    def _mount() -> AnalysisMount:
        if not analysis_mount:
            return AnalysisMount()
        return AnalysisMount(
            mode=analysis_mount.get("mode", "all"),
            include=tuple(analysis_mount.get("include", [])),
            exclude=tuple(analysis_mount.get("exclude", [])),
        )

    def _derivation() -> BranchDerivation:
        if not branch_derivation and not baseline_branches:
            return BranchDerivation()
        config = branch_derivation or {}
        return BranchDerivation(
            derive_from=config.get("derive_from", "default"),
            default_branch=config.get("default_branch", "main"),
            default_rule=config.get("default_rule", "same_name"),
            dev_branches=tuple(
                (str(repo), str(branch))
                for repo, branch in config.get("dev_branches", {}).items()
            ),
            baseline_branches=tuple((str(repo), str(branch)) for repo, branch in (baseline_branches or {}).items()),
            overrides=tuple(
                RepositoryBranchRule(
                    from_branch=item["from_branch"],
                    repo=item["repo"],
                    branch=item["branch"],
                )
                for item in config.get("overrides", [])
            ),
        )

    return ProjectProfile(
        profile_id="demo",
        connection_id="tapdata-cloud",
        project_key="TAP",
        task_query="project = TAP",
        issue_types=("任务",),
        default_repository=default_repository,
        repository_list=repository_list,
        analysis_mount=_mount(),
        branch_derivation=_derivation(),
    )


class ResolveTargetRepositoryTest(unittest.TestCase):
    def test_default_repository_when_no_declared_section(self) -> None:
        profile = build_profile()
        self.assertEqual(
            "tapdata/tapdata",
            resolve_target_repository(profile, {}),
        )

    def test_declared_repository_in_list(self) -> None:
        profile = build_profile()
        self.assertEqual(
            "tapdata/tapdata-web",
            resolve_target_repository(profile, {"目标仓库": "tapdata/tapdata-web\n"}),
        )

    def test_unknown_declared_repository_blocked(self) -> None:
        profile = build_profile()
        with self.assertRaises(RuntimeErrorResult) as captured:
            resolve_target_repository(profile, {"目标仓库": "evil/unknown"})
        self.assertEqual("target_repository_unknown", captured.exception.code)


class ResolveFromBranchTest(unittest.TestCase):
    def test_explicit_baseline_mapping_is_used_for_target_repository(self) -> None:
        profile = build_profile(baseline_branches={"tapdata/tapdata": "develop"})
        self.assertEqual("develop", resolve_from_branch(profile, {}, target_repository="tapdata/tapdata"))

    def test_missing_explicit_baseline_mapping_blocks(self) -> None:
        profile = build_profile(baseline_branches={"tapdata/tapdata": "develop"})
        with self.assertRaises(RuntimeErrorResult) as captured:
            resolve_from_branch(profile, {}, target_repository="tapdata/tapdata-web")
        self.assertEqual("task_baseline_unresolved", captured.exception.code)


class TapdataProfileBranchDerivationTest(unittest.TestCase):
    def test_develop_product_domain_only_mounts_product_repositories(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        expected_repositories = {
            "tapdata/tapdata",
            "tapdata/tapdata-enterprise",
            "tapdata/tapdata-web",
            "tapdata/tapdata-license",
            "tapdata/tapdata-common-lib",
            "tapdata/tapdata-application",
        }
        with tempfile.TemporaryDirectory() as temporary:
            plan = plan_task_worktrees(
                pool_root=Path(temporary),
                profile=profile,
                issue_key="TAP-123",
                description_sections={},
            )

        by_repository = {entry.repository: entry.branch for entry in plan.entries}
        self.assertEqual("develop", plan.from_branch)
        self.assertEqual(expected_repositories, set(by_repository))
        self.assertEqual("develop", by_repository["tapdata/tapdata"])
        self.assertEqual("main", by_repository["tapdata/tapdata-common-lib"])
        self.assertTrue(set(dict(profile.branch_derivation.dev_branches)).issubset(profile.repository_candidates()))

    def test_declared_section_wins(self) -> None:
        profile = build_profile()
        self.assertEqual(
            "feature/x",
            resolve_from_branch(profile, {"修复分支": "feature/x\n"}),
        )

    def test_problem_version_overrides_legacy_repair_branch(self) -> None:
        profile = build_profile()
        self.assertEqual(
            "release-v3.8.0",
            resolve_from_branch(
                profile,
                {"问题版本": "release-v3.8.0\n", "修复分支": "develop\n"},
            ),
        )

    def test_tapdata_connector_domain_uses_connector_baseline(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        with tempfile.TemporaryDirectory() as temporary:
            plan = plan_task_worktrees(
                pool_root=Path(temporary),
                profile=profile,
                issue_key="TAP-123",
                description_sections={
                    "目标仓库": "tapdata/tapdata-connectors\n",
                    "问题版本": "release-v3.8.0\n",
                },
            )
        self.assertEqual("release-v3.8.0", plan.from_branch)
        self.assertEqual(
            {"tapdata/tapdata-connectors", "tapdata/tapdata-connectors-enterprise"},
            {entry.repository for entry in plan.entries},
        )
        self.assertTrue(all("/.worktree/TAP-123/" in str(entry.worktree_dir) for entry in plan.entries))

    def test_product_target_uses_product_baseline_problem_version(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        with tempfile.TemporaryDirectory() as temporary:
            plan = plan_task_worktrees(
                pool_root=Path(temporary),
                profile=profile,
                issue_key="TAP-123",
                description_sections={
                    "目标仓库": "tapdata/tapdata-common-lib\n",
                },
            )

        self.assertEqual("develop", plan.from_branch)
        self.assertEqual("tapdata/tapdata", plan.baseline_repository)
        self.assertEqual("tapdata/tapdata-common-lib", plan.target_repository)

    def test_product_alignment_spec_is_separate_from_problem_version_path(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        with tempfile.TemporaryDirectory() as temporary:
            plan = plan_task_worktrees(
                pool_root=Path(temporary),
                profile=profile,
                issue_key="TAP-123",
                description_sections={
                    "问题版本": (
                        "release-v3.8.0,release-v3.8-enterprise,release-v3.8-web\n"
                    ),
                },
                alignment_script=Path(temporary) / "tap_align_branches.py",
            )

        self.assertEqual("release-v3.8.0", plan.from_branch)
        self.assertEqual(
            "release-v3.8.0,release-v3.8-enterprise,release-v3.8-web",
            plan.alignment_spec,
        )
        self.assertTrue(
            all("/release-v3.8.0/" in str(entry.worktree_dir) for entry in plan.entries)
        )

    def test_tapdata_unclassified_repository_blocks_without_full_mount(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        with self.assertRaises(RuntimeErrorResult) as captured:
            with tempfile.TemporaryDirectory() as temporary:
                plan_task_worktrees(
                    pool_root=Path(temporary),
                    profile=profile,
                    issue_key="TAP-123",
                    description_sections={"目标仓库": "tapdata/docs\n", "问题版本": "develop\n"},
                )
        self.assertEqual("task_domain_unresolved", captured.exception.code)

    def test_tapdata_empty_domain_overlay_blocks_without_full_mount(self) -> None:
        from dataclasses import replace

        repository_root = Path(__file__).resolve().parents[3]
        profile = replace(
            load_project_profile(repository_root, "tapdata"),
            worktree_domains=(),
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            with tempfile.TemporaryDirectory() as temporary:
                plan_task_worktrees(
                    pool_root=Path(temporary),
                    profile=profile,
                    issue_key="TAP-123",
                    description_sections={"问题版本": "develop\n"},
                )

        self.assertEqual("task_domain_unresolved", captured.exception.code)

    def test_default_branch_from_derivation(self) -> None:
        profile = build_profile(
            branch_derivation={"default_branch": "main", "default_rule": "same_name"}
        )
        self.assertEqual("main", resolve_from_branch(profile, {}))

    def test_default_main_when_no_derivation(self) -> None:
        profile = build_profile()
        self.assertEqual("main", resolve_from_branch(profile, {}))

    def test_traversal_branch_rejected(self) -> None:
        profile = build_profile()
        with self.assertRaises(RuntimeErrorResult):
            resolve_from_branch(profile, {"修复分支": "../escape"})


class PlanTaskWorktreesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pool = Path(self.temporary.name) / "pool"
        self.pool.mkdir()

    def test_plan_all_mounted_repositories_with_derived_branches(self) -> None:
        profile = build_profile()
        plan = plan_task_worktrees(
            pool_root=self.pool,
            profile=profile,
            issue_key="TAP-123",
            description_sections={"修复分支": "feature/x\n"},
        )
        self.assertEqual("feature/x", plan.from_branch)
        self.assertEqual(2, len(plan.entries))
        by_repo = {entry.repository: entry for entry in plan.entries}
        self.assertIn("tapdata/tapdata", by_repo)
        self.assertIn("tapdata/tapdata-web", by_repo)
        self.assertEqual("feature/x", by_repo["tapdata/tapdata"].branch)
        self.assertEqual(
            task_worktree_path(self.pool, "TAP-123", "feature/x", "tapdata/tapdata"),
            by_repo["tapdata/tapdata"].worktree_dir,
        )

    def test_plan_override_branch_derivation(self) -> None:
        profile = build_profile(
            repository_list=("tapdata/tapdata", "tapdata/connectors"),
            analysis_mount={"mode": "all"},
            branch_derivation={
                "default_branch": "main",
                "default_rule": "same_name",
                "overrides": [
                    {
                        "from_branch": "release/2.0",
                        "repo": "tapdata/connectors",
                        "branch": "v2.0.x",
                    }
                ],
            },
        )
        plan = plan_task_worktrees(
            pool_root=self.pool,
            profile=profile,
            issue_key="TAP-123",
            description_sections={"修复分支": "release/2.0\n"},
        )
        by_repo = {entry.repository: entry for entry in plan.entries}
        self.assertEqual("v2.0.x", by_repo["tapdata/connectors"].branch)
        self.assertEqual("release/2.0", by_repo["tapdata/tapdata"].branch)

    def test_plan_dev_branches_used_when_from_branch_is_primary_dev_branch(self) -> None:
        profile = build_profile(
            repository_list=(
                "tapdata/tapdata",
                "tapdata/tapdata-web",
                "tapdata/tapdata-common-lib",
            ),
            analysis_mount={"mode": "all"},
            branch_derivation={
                "default_branch": "main",
                "default_rule": "same_name",
                "dev_branches": {
                    "tapdata/tapdata": "develop",
                    "tapdata/tapdata-web": "develop",
                    "tapdata/tapdata-common-lib": "main",
                },
            },
        )
        plan = plan_task_worktrees(
            pool_root=self.pool,
            profile=profile,
            issue_key="TAP-123",
            description_sections={"修复分支": "develop\n"},
        )
        by_repo = {entry.repository: entry for entry in plan.entries}
        self.assertEqual("develop", by_repo["tapdata/tapdata"].branch)
        self.assertEqual("develop", by_repo["tapdata/tapdata-web"].branch)
        self.assertEqual("main", by_repo["tapdata/tapdata-common-lib"].branch)

    def test_plan_dev_branches_not_used_for_non_primary_dev_branch(self) -> None:
        profile = build_profile(
            repository_list=(
                "tapdata/tapdata",
                "tapdata/tapdata-web",
                "tapdata/tapdata-common-lib",
            ),
            analysis_mount={"mode": "all"},
            branch_derivation={
                "default_branch": "main",
                "default_rule": "same_name",
                "dev_branches": {
                    "tapdata/tapdata": "develop",
                    "tapdata/tapdata-web": "develop",
                    "tapdata/tapdata-common-lib": "main",
                },
            },
        )
        plan = plan_task_worktrees(
            pool_root=self.pool,
            profile=profile,
            issue_key="TAP-123",
            description_sections={"修复分支": "release-v3.8.0\n"},
        )
        by_repo = {entry.repository: entry for entry in plan.entries}
        self.assertEqual("release-v3.8.0", by_repo["tapdata/tapdata"].branch)
        self.assertEqual("release-v3.8.0", by_repo["tapdata/tapdata-web"].branch)
        self.assertEqual("release-v3.8.0", by_repo["tapdata/tapdata-common-lib"].branch)

    def test_plan_mount_exclude_skips_repository(self) -> None:
        profile = build_profile(
            analysis_mount={"mode": "exclude", "exclude": ["tapdata/tapdata-web"]}
        )
        plan = plan_task_worktrees(
            pool_root=self.pool,
            profile=profile,
            issue_key="TAP-123",
            description_sections={},
        )
        repositories = {entry.repository for entry in plan.entries}
        self.assertEqual({"tapdata/tapdata"}, repositories)

    def test_plan_mount_all_with_exclude_still_excludes(self) -> None:
        profile = build_profile(
            analysis_mount={"mode": "all", "exclude": ["tapdata/tapdata-web"]}
        )
        plan = plan_task_worktrees(
            pool_root=self.pool,
            profile=profile,
            issue_key="TAP-123",
            description_sections={},
        )
        repositories = {entry.repository for entry in plan.entries}
        self.assertEqual({"tapdata/tapdata"}, repositories)

    def test_plan_target_repo_not_mounted_blocked(self) -> None:
        profile = build_profile(
            analysis_mount={"mode": "exclude", "exclude": ["tapdata/tapdata-web"]}
        )
        with self.assertRaises(RuntimeErrorResult) as captured:
            plan_task_worktrees(
                pool_root=self.pool,
                profile=profile,
                issue_key="TAP-123",
                description_sections={"目标仓库": "tapdata/tapdata-web\n"},
            )
        self.assertEqual("target_repository_not_mounted", captured.exception.code)


class PrepareTaskWorktreesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.pool = Path(self.temporary.name) / "pool"
        self.pool.mkdir()
        for repository in ("tapdata/tapdata", "tapdata/tapdata-web"):
            member = self.pool / repository
            member.mkdir(parents=True)
            (member / ".git").mkdir()
        self.profile = build_profile()

    def _plan(self) -> TaskWorktreePlan:
        return plan_task_worktrees(
            pool_root=self.pool,
            profile=self.profile,
            issue_key="TAP-123",
            description_sections={"修复分支": "feature/x\n"},
        )

    def _run_git(self, command, *, timeout=None):
        """fake git：worktree add 创建目录；config/rev-parse 返回成功。"""
        if (
            command[0] == "-C"
            and "worktree" in command
            and len(command) > 3
            and command[3] == "add"
        ):
            target = Path(command[5])
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").write_text("gitdir: fake\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")
        if (
            command[0] == "-C"
            and "worktree" in command
            and len(command) > 3
            and command[3] == "remove"
        ):
            target = Path(command[5])
            if target.exists():
                import shutil

                shutil.rmtree(target, ignore_errors=True)
            return subprocess.CompletedProcess(command, 0, "", "")
        if "worktree" in command and "list" in command:
            return subprocess.CompletedProcess(command, 0, "", "")
        if "--show-toplevel" in command:
            return subprocess.CompletedProcess(command, 0, f"{command[1]}\n", "")
        if command[-1] == "HEAD" or "rev-parse" in command:
            return subprocess.CompletedProcess(command, 0, "HEAD\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_prepare_creates_worktrees_for_all_entries(self) -> None:
        plan = self._plan()
        prepared = prepare_task_worktrees(
            plan,
            execution_identity={
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen@example.test",
            },
            run_git=self._run_git,
        )
        self.assertEqual(2, prepared.created)
        self.assertEqual(0, prepared.adopted)
        for entry in prepared.entries:
            self.assertTrue(entry.worktree_dir.is_dir())
            self.assertTrue(entry.created)

    def test_prepare_refreshes_origin_and_uses_remote_commit_before_creating(self) -> None:
        plan = self._plan()
        calls: list[list[str]] = []

        def git_with_remote_baseline(command, *, timeout=None):
            calls.append(command)
            if "rev-parse" in command and "refs/remotes/origin/feature/x^{commit}" in command:
                return subprocess.CompletedProcess(command, 0, "remote-feature-commit\n", "")
            return self._run_git(command, timeout=timeout)

        prepare_task_worktrees(plan, run_git=git_with_remote_baseline)

        for entry in plan.entries:
            fetch_index = next(
                index
                for index, command in enumerate(calls)
                if command[2:] == ["fetch", "--prune", "origin"]
                and Path(command[1]).resolve() == (self.pool / entry.repository).resolve()
            )
            add_index = next(
                index
                for index, command in enumerate(calls)
                if command[2:] == [
                    "worktree",
                    "add",
                    "--detach",
                    str(entry.worktree_dir),
                    "remote-feature-commit",
                ]
            )
            self.assertLess(fetch_index, add_index)

    def test_prepare_cleans_empty_task_parents_when_fetch_fails(self) -> None:
        plan = self._plan()

        def fetch_failing_git(command, *, timeout=None):
            if command[2:] == ["fetch", "--prune", "origin"]:
                return subprocess.CompletedProcess(command, 1, "", "fatal: fetch failed\n")
            return self._run_git(command, timeout=timeout)

        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_task_worktrees(plan, run_git=fetch_failing_git)

        self.assertEqual("source_pool_fetch_failed", captured.exception.code)
        self.assertFalse((self.pool / ".worktree").exists())

    def test_prepare_blocks_legacy_worktree_without_creating_new_copy(self) -> None:
        plan = self._plan()
        legacy = self.pool / "TAP-123" / "develop" / "unrelated-repository"
        legacy.mkdir(parents=True)
        calls: list[list[str]] = []

        def recording_git(command, *, timeout=None):
            calls.append(command)
            return self._run_git(command, timeout=timeout)

        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_task_worktrees(plan, run_git=recording_git)

        self.assertEqual("worktree_legacy_layout_detected", captured.exception.code)
        self.assertTrue(legacy.is_dir())
        self.assertEqual(
            str((self.pool / "TAP-123").resolve()),
            captured.exception.details["legacy_task_root"],
        )
        self.assertFalse(any("worktree" in command and "add" in command for command in calls))

    def test_prepare_reuses_existing_worktree(self) -> None:
        plan = self._plan()
        first = prepare_task_worktrees(plan, run_git=self._run_git)
        self.assertEqual(2, first.created)
        second = prepare_task_worktrees(plan, run_git=self._run_git)
        self.assertEqual(2, second.adopted)
        self.assertEqual(0, second.created)

    def test_prepare_reuses_existing_worktree_when_only_origin_branch_exists(self) -> None:
        plan = self._plan()
        prepare_task_worktrees(plan, run_git=self._run_git)

        def origin_only_git(command, *, timeout=None):
            if "rev-parse" in command and command[-1] == "feature/x^{commit}":
                return subprocess.CompletedProcess(command, 1, "", "unknown revision\n")
            if (
                "rev-parse" in command
                and command[-1] == "refs/remotes/origin/feature/x^{commit}"
            ):
                return subprocess.CompletedProcess(command, 0, "HEAD\n", "")
            return self._run_git(command, timeout=timeout)

        prepared = prepare_task_worktrees(plan, run_git=origin_only_git)

        self.assertEqual(2, prepared.adopted)
        self.assertEqual(0, prepared.created)

    def test_prepare_preflights_existing_worktree_against_refreshed_remote(self) -> None:
        plan = self._plan()
        existing = plan.entries[1].worktree_dir
        existing.mkdir(parents=True)
        (existing / ".git").write_text("gitdir: fake\n", encoding="utf-8")
        calls: list[list[str]] = []

        def stale_existing_git(command, *, timeout=None):
            calls.append(command)
            if "rev-parse" in command and "refs/remotes/origin/feature/x^{commit}" in command:
                return subprocess.CompletedProcess(command, 0, "remote-feature-commit\n", "")
            if command[1] == str(existing) and command[-1] == "HEAD":
                return subprocess.CompletedProcess(command, 0, "stale-local-commit\n", "")
            return self._run_git(command, timeout=timeout)

        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_task_worktrees(plan, run_git=stale_existing_git)

        self.assertEqual("worktree_baseline_mismatch", captured.exception.code)
        self.assertFalse(any("worktree" in command and "add" in command for command in calls))
        self.assertFalse(plan.entries[0].worktree_dir.exists())

    def test_prepare_rolls_back_on_failure(self) -> None:
        plan = self._plan()
        calls: list[list[str]] = []

        def failing_git(command, *, timeout=None):
            if "rev-parse" in command and "refs/remotes/origin/feature/x^{commit}" in command:
                return subprocess.CompletedProcess(command, 0, "remote-feature-commit\n", "")
            calls.append(command)
            if (
                command[0] == "-C"
                and "worktree" in command
                and len(command) > 3
                and command[3] == "add"
            ):
                target = Path(command[5])
                if target.name == "tapdata":
                    target.mkdir(parents=True, exist_ok=True)
                    return subprocess.CompletedProcess(command, 0, "", "")
                # tapdata-web 失败，触发回滚。
                return subprocess.CompletedProcess(
                    command, 1, "", "fatal: branch not found\n"
                )
            if (
                command[0] == "-C"
                and "worktree" in command
                and len(command) > 3
                and command[3] == "remove"
            ):
                target = Path(command[5])
                if target.exists():
                    import shutil

                    shutil.rmtree(target, ignore_errors=True)
                return subprocess.CompletedProcess(command, 0, "", "")
            if "worktree" in command and "list" in command:
                # 报告第一个已创建 worktree（tapdata）使回滚能找到它。
                first_worktree = plan.entries[0].worktree_dir
                if first_worktree.exists():
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"worktree {first_worktree.resolve()}\nHEAD <hash>\n",
                        "",
                    )
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_task_worktrees(plan, run_git=failing_git)
        self.assertEqual("worktree_add_failed", captured.exception.code)
        # 第一个仓库的 worktree 应被回滚（目录不存在）。
        for entry in plan.entries:
            self.assertFalse(entry.worktree_dir.exists())

    def test_prepare_preflights_all_branches_before_creating_worktrees(self) -> None:
        plan = self._plan()
        calls: list[list[str]] = []

        def remote_branch_missing_git(command, *, timeout=None):
            calls.append(command)
            if "rev-parse" in command and "refs/remotes/origin/feature/x^{commit}" in command:
                if Path(command[1]).resolve() == (self.pool / "tapdata/tapdata-web").resolve():
                    return subprocess.CompletedProcess(command, 1, "", "unknown revision\n")
                return subprocess.CompletedProcess(command, 0, "remote-feature-commit\n", "")
            if "worktree" in command and "list" in command:
                first_worktree = plan.entries[0].worktree_dir
                if first_worktree.exists():
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        f"worktree {first_worktree.resolve()}\nHEAD <hash>\n",
                        "",
                    )
            return self._run_git(command, timeout=timeout)

        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_task_worktrees(plan, run_git=remote_branch_missing_git)

        self.assertEqual("branch_derivation_failed", captured.exception.code)
        self.assertEqual(
            {"repository": "tapdata/tapdata-web", "branch": "feature/x", "stderr_tail": "unknown revision\n"},
            captured.exception.details,
        )
        self.assertFalse(any("worktree" in command and "add" in command for command in calls))
        self.assertFalse((self.pool / ".worktree").exists())
        self.assertFalse((self.pool / "TAP-123").exists())
        for entry in plan.entries:
            self.assertFalse(entry.worktree_dir.exists())

    def test_prepare_uses_project_alignment_plan_for_actual_product_branches(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        script = self.pool / "tap_align_branches.py"
        script.write_text("# test fixture\n", encoding="utf-8")
        plan = plan_task_worktrees(
            pool_root=self.pool,
            profile=profile,
            issue_key="TAP-456",
            description_sections={
                "问题版本": (
                    "release-v3.8.0,release-v3.8-enterprise,release-v3.8-web\n"
                ),
            },
            alignment_script=script,
        )
        for entry in plan.entries:
            member = self.pool / entry.repository
            member.mkdir(parents=True, exist_ok=True)
            (member / ".git").mkdir(exist_ok=True)

        rows = []
        expected = {
            "tapdata": "release-v3.8.0",
            "tapdata-enterprise": "release-v3.8.0",
            "tapdata-web": "release-v3.8.0",
            "tapdata-license": "release-v3.8.0",
            "tapdata-common-lib": "release-v1.2.6",
            "tapdata-application": "main",
        }
        for repo, target in expected.items():
            rows.append(
                {
                    "repo": repo,
                    "current": "main",
                    "target": (
                        "KEEP_CURRENT" if repo == "tapdata-application" else target
                    ),
                    "action": "keep" if repo == "tapdata-application" else "switch",
                    "reason": "test alignment",
                    "dirty": "clean",
                }
            )
        alignment_calls: list[list[str]] = []

        def alignment(command, **kwargs):
            alignment_calls.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(rows), "")

        prepared = prepare_task_worktrees(
            plan,
            run_git=self._run_git,
            run_alignment=alignment,
        )

        self.assertEqual(1, len(alignment_calls))
        self.assertIn("--no-fetch", alignment_calls[0])
        self.assertIn("--remote-only", alignment_calls[0])
        self.assertEqual(
            "release-v3.8.0,release-v3.8-enterprise,release-v3.8-web",
            alignment_calls[0][alignment_calls[0].index("plan") + 1],
        )
        repositories = alignment_calls[0][
            alignment_calls[0].index("--repositories") + 1
        ]
        self.assertEqual(set(expected), set(repositories.split(",")))
        self.assertNotIn("tapdata-connectors", repositories)
        self.assertEqual(
            expected,
            {
                entry.repository.split("/", 1)[1]: entry.branch
                for entry in prepared.entries
            },
        )

    def test_prepare_alignment_failure_creates_no_worktree(self) -> None:
        script = self.pool / "tap_align_branches.py"
        script.write_text("# test fixture\n", encoding="utf-8")
        original = self._plan()
        plan = TaskWorktreePlan(
            issue_key=original.issue_key,
            from_branch=original.from_branch,
            pool_root=original.pool_root,
            entries=original.entries,
            target_repository="tapdata/tapdata",
            baseline_repository="tapdata/tapdata",
            alignment_script=script,
        )
        calls: list[list[str]] = []

        def recording_git(command, *, timeout=None):
            calls.append(command)
            return self._run_git(command, timeout=timeout)

        def failed_alignment(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, "", "UNRESOLVED\n")

        with self.assertRaises(RuntimeErrorResult) as captured:
            prepare_task_worktrees(
                plan,
                run_git=recording_git,
                run_alignment=failed_alignment,
            )

        self.assertEqual("branch_alignment_failed", captured.exception.code)
        self.assertFalse(
            any("worktree" in command and "add" in command for command in calls)
        )


class RunGitRealSubprocessRegressionTest(unittest.TestCase):
    """回归：_run_git 必须补 git 可执行名前缀，经真实 subprocess 验证。

    2026-08-19 实踩（AO-25）：_run_git 曾裸透传命令列表，而全部调用点都以
    ["-C", ...] 开头，缺 git 前缀导致 subprocess 把 "-C" 当可执行文件，
    FileNotFoundError → runtime_failed（task start/takeover 池模式主链路崩溃）。
    既有测试全部注入 mock runner、从不经真实 subprocess，故未拦截。

    本测试用 PATH 假 git + 真实 subprocess：若实现退化为裸透传，
    subprocess 会把 "-C" 当程序执行而失败；补前缀后假 git 执行并把收到的
    argv 打到 stdout，断言首个参数是 "-C"（即 argv[0]=git 已补上）。
    """

    def test_run_git_prepends_git_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bindir = Path(tmp) / "fakebin"
            bindir.mkdir()
            script = bindir / "git"
            script.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*"\n'
                "exit 0\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            original_path = os.environ.get("PATH", "")
            os.environ["PATH"] = f"{bindir}:{original_path}"
            try:
                result = _run_git(
                    ["-C", tmp, "rev-parse", "--abbrev-ref", "HEAD"],
                    timeout=30,
                )
            finally:
                os.environ["PATH"] = original_path
        self.assertEqual(0, result.returncode)
        recorded = result.stdout.strip()
        parts = recorded.split()
        # 首个参数必须是 -C（git 前缀已由 _run_git 补上，subprocess 实际执行 git）。
        self.assertEqual("-C", parts[0])
        self.assertIn(tmp, parts)


if __name__ == "__main__":
    unittest.main()
