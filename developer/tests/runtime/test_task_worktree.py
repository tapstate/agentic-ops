from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        if not branch_derivation:
            return BranchDerivation()
        return BranchDerivation(
            derive_from=branch_derivation.get("derive_from", "default"),
            default_branch=branch_derivation.get("default_branch", "main"),
            default_rule=branch_derivation.get("default_rule", "same_name"),
            overrides=tuple(
                RepositoryBranchRule(
                    from_branch=item["from_branch"],
                    repo=item["repo"],
                    branch=item["branch"],
                )
                for item in branch_derivation.get("overrides", [])
            ),
        )

    return ProjectProfile(
        profile_id="tapdata",
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
    def test_declared_section_wins(self) -> None:
        profile = build_profile()
        self.assertEqual(
            "feature-x",
            resolve_from_branch(profile, {"修复分支": "feature/x\n"}),
        )

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
        self.assertEqual("feature-x", plan.from_branch)
        self.assertEqual(2, len(plan.entries))
        by_repo = {entry.repository: entry for entry in plan.entries}
        self.assertIn("tapdata/tapdata", by_repo)
        self.assertIn("tapdata/tapdata-web", by_repo)
        self.assertEqual("feature-x", by_repo["tapdata/tapdata"].branch)
        self.assertEqual(
            task_worktree_path(self.pool, "TAP-123", "feature-x", "tapdata/tapdata"),
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
                        "from_branch": "release-2.0",
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
        self.assertEqual("release-2.0", by_repo["tapdata/tapdata"].branch)

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

    def test_prepare_reuses_existing_worktree(self) -> None:
        plan = self._plan()
        first = prepare_task_worktrees(plan, run_git=self._run_git)
        self.assertEqual(2, first.created)
        second = prepare_task_worktrees(plan, run_git=self._run_git)
        self.assertEqual(2, second.adopted)
        self.assertEqual(0, second.created)

    def test_prepare_rolls_back_on_failure(self) -> None:
        plan = self._plan()
        calls: list[list[str]] = []

        def failing_git(command, *, timeout=None):
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
