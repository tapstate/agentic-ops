from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.locking import TaskLock
from ao_work.workspace import (
    normalize_worktree_from_branch,
    repository_short_name,
    task_worktree_path,
    validate_source_pool_root,
)


@dataclass(frozen=True)
class WorktreePlanEntry:
    repository: str
    worktree_dir: Path
    branch: str
    created: bool = False


@dataclass(frozen=True)
class TaskWorktreePlan:
    issue_key: str
    from_branch: str
    pool_root: Path
    entries: tuple[WorktreePlanEntry, ...]
    adopted: int = 0
    created: int = 0


def resolve_target_repository(
    profile: Any,
    description_sections: dict[str, str],
) -> str:
    """从 Jira 描述「目标仓库」section 解析目标仓库，缺省用 profile.default_repository。

    解析结果必须在 repositories.list 内，否则阻断（target_repository_unknown）。
    """
    declared = description_sections.get("目标仓库", "").strip()
    if declared:
        repository = declared.splitlines()[0].strip()
        if repository != profile.default_repository and repository not in profile.repository_candidates():
            raise _blocked(
                "target_repository_unknown",
                f"Jira 描述声明的目标仓库不在 Project Profile 仓库清单内：{repository}",
                "请修正 Jira 描述的目标仓库，或先在 profile repositories.list 登记该仓库",
            )
        return repository
    return profile.default_repository


def resolve_from_branch(
    profile: Any,
    description_sections: dict[str, str],
    *,
    target_repository: str | None = None,
) -> str:
    """解析任务问题版本：描述「问题版本」优先，兼容旧「修复分支」。

    返回原始 Git ref；仅在计算本地目录时规范化路径片段。
    """
    declared = description_sections.get("问题版本", "").strip() or description_sections.get("修复分支", "").strip()
    if declared:
        candidate = declared.splitlines()[0].strip()
    else:
        repository = target_repository or resolve_target_repository(profile, description_sections)
        candidate = profile.baseline_branch(repository)
        if not candidate:
            raise _blocked(
                "task_baseline_unresolved",
                f"目标仓库未配置任务基线分支：{repository}",
                "请在 Project Profile 显式声明该仓库的 baseline_branches，或在 Jira 描述声明问题版本",
                details={"repository": repository},
            )
    normalize_worktree_from_branch(candidate)
    return candidate.strip()


def plan_task_worktrees(
    *,
    pool_root: Path,
    profile: Any,
    issue_key: str,
    description_sections: dict[str, str],
) -> TaskWorktreePlan:
    """计算任务工作树集计划：目标仓库所属领域 + 问题版本分支推导。

    已声明领域的 Profile 只挂载该领域仓库；无法归类时失败关闭。
    未声明领域的旧 Profile 仅为兼容而使用 analysis_mount 回退。
    每个仓库按该领域基线推导目标分支。
    返回计划而不创建；创建由 prepare_task_worktrees 执行。
    """
    pool = validate_source_pool_root(pool_root)
    target_repository = resolve_target_repository(profile, description_sections)
    from_branch = resolve_from_branch(
        profile, description_sections, target_repository=target_repository
    )
    domain = profile.domain_for(target_repository)
    if domain is None:
        raise _blocked(
            "task_domain_unresolved",
            f"无法根据目标仓库判定任务领域：{target_repository}",
            "请补充可映射的目标仓库或任务领域；系统不会创建全量工作树",
            details={"target_repository": target_repository},
        )
    repositories = domain.repositories
    if target_repository not in repositories:
        # 防御性校验：领域配置必须包含触发该领域的目标仓库。
        raise _blocked(
            "target_repository_not_mounted",
            f"目标仓库 {target_repository} 不在所属领域仓库集内",
            "请调整 profile worktree_domains 配置，确保目标仓库被该领域覆盖",
        )
    entries: list[WorktreePlanEntry] = []
    for repository in repositories:
        branch = profile.derive_branch(repository, from_branch, primary_repository=domain.baseline_repository)
        if not branch:
            raise _blocked(
                "branch_derivation_failed",
                f"无法对齐领域仓库分支：{repository}",
                "请补充该领域的问题版本分支映射",
                details={"repository": repository, "problem_version": from_branch},
            )
        worktree_dir = task_worktree_path(pool, issue_key, from_branch, repository)
        entries.append(
            WorktreePlanEntry(
                repository=repository,
                worktree_dir=worktree_dir,
                branch=branch,
            )
        )
    return TaskWorktreePlan(
        issue_key=issue_key,
        from_branch=from_branch,
        pool_root=pool,
        entries=tuple(entries),
    )


def prepare_task_worktrees(
    plan: TaskWorktreePlan,
    *,
    execution_identity: dict[str, str] | None = None,
    run_git: Any | None = None,
) -> TaskWorktreePlan:
    """创建/复用任务工作树集：预检后逐个仓库 git worktree add --detach。

    - 已存在 → 校验路径与分支后复用（不重复创建）。
    - 旧布局存在 → 失败关闭，避免恢复任务忽略旧工作树中的未提交修改。
    - 缺失 → 先在池成员锁内刷新 origin、解析全部远端基线；全部通过后才创建任务工作树。
    - 任一失败 → 已创建的工作树全部回滚（worktree remove --force），并清理本次留下的空父目录。
    - 池成员级并发锁：<pool_root>/.locks/<repo>.lock。
    """
    git = run_git or _run_git
    created_dirs: list[Path] = []
    adopted = 0
    created = 0
    try:
        _reject_legacy_worktrees(plan)
        baselines: dict[Path, str] = {}
        for entry in plan.entries:
            if entry.worktree_dir.is_dir():
                continue
            member_dir = plan.pool_root / entry.repository
            lock_path = plan.pool_root / ".locks" / f"{entry.repository.replace('/', '__')}.lock"
            with TaskLock(lock_path, timeout=10):
                # 并发恢复可能已创建同一任务工作树；此时创建阶段会改为复用。
                if entry.worktree_dir.is_dir():
                    continue
                _refresh_pool_member(git, member_dir, entry.repository)
                baselines[entry.worktree_dir] = _resolve_remote_baseline(
                    git, member_dir, entry.branch, entry.repository
                )

        for entry in plan.entries:
            member_dir = plan.pool_root / entry.repository
            lock_path = plan.pool_root / ".locks" / f"{entry.repository.replace('/', '__')}.lock"
            with TaskLock(lock_path, timeout=10):
                if entry.worktree_dir.is_dir():
                    _validate_existing_worktree(git, entry.worktree_dir, member_dir, entry.branch, entry.repository)
                    adopted += 1
                    continue
                entry.worktree_dir.parent.mkdir(parents=True, exist_ok=True)
                baseline_ref = baselines[entry.worktree_dir]
                result = git(
                    [
                        "-C",
                        str(member_dir),
                        "worktree",
                        "add",
                        "--detach",
                        str(entry.worktree_dir),
                        baseline_ref,
                    ],
                    timeout=120,
                )
                if result.returncode != 0:
                    raise _blocked(
                        "worktree_add_failed",
                        f"任务工作树创建失败：{entry.repository} @ {entry.branch}",
                        "请检查分支是否存在、池成员是否可写；本次已创建的工作树将回滚",
                        details={"stderr_tail": _stderr_tail(result.stderr)},
                    )
                created_dirs.append(entry.worktree_dir)
                _write_worktree_identity(git, entry.worktree_dir, execution_identity)
                created += 1
    except Exception:
        for worktree_dir in created_dirs:
            _rollback_worktree(
                git,
                plan.pool_root,
                worktree_dir,
                tuple(entry.repository for entry in plan.entries),
            )
        for entry in plan.entries:
            if not entry.worktree_dir.exists():
                _remove_empty_worktree_parents(entry.worktree_dir, plan.pool_root)
        raise
    return TaskWorktreePlan(
        issue_key=plan.issue_key,
        from_branch=plan.from_branch,
        pool_root=plan.pool_root,
        entries=tuple(
            WorktreePlanEntry(
                repository=entry.repository,
                worktree_dir=entry.worktree_dir,
                branch=entry.branch,
                created=entry.worktree_dir in created_dirs,
            )
            for entry in plan.entries
        ),
        adopted=adopted,
        created=created,
    )


def _reject_legacy_worktrees(plan: TaskWorktreePlan) -> None:
    """旧布局不得被静默忽略或覆盖，必须由人工决定迁移或清理。"""
    for entry in plan.entries:
        legacy = (
            plan.pool_root
            / plan.issue_key
            / normalize_worktree_from_branch(plan.from_branch)
            / repository_short_name(entry.repository)
        )
        if legacy.is_dir():
            raise _blocked(
                "worktree_legacy_layout_detected",
                f"检测到旧布局任务工作树：{legacy}",
                "请先人工确认旧工作树中的修改并迁移或清理；系统不会创建新的 .worktree 副本",
                details={"legacy_worktree": str(legacy), "repository": entry.repository},
            )


def _validate_existing_worktree(
    git: Any,
    worktree_dir: Path,
    member_dir: Path,
    branch: str,
    repository: str,
) -> None:
    root = git(["-C", str(worktree_dir), "rev-parse", "--show-toplevel"], timeout=60)
    actual = git(["-C", str(worktree_dir), "rev-parse", "HEAD"], timeout=60)
    baseline = _resolve_existing_baseline(git, member_dir, branch)
    if root.returncode != 0 or actual.returncode != 0 or baseline.returncode != 0:
        raise _blocked(
            "worktree_invalid",
            f"任务工作树已存在但不是 Git 工作树：{worktree_dir}",
            "请检查并清理该目录后重试",
            details={"stderr_tail": _stderr_tail(root.stderr or actual.stderr or baseline.stderr)},
        )
    if Path(root.stdout.strip()).resolve() != worktree_dir.resolve() or actual.stdout.strip() != baseline.stdout.strip():
        raise _blocked(
            "worktree_baseline_mismatch",
            f"任务工作树 {repository} 不是当前基线 {branch} 的精确工作树",
            "请清理该任务工作树后重试，或核对目标仓库和基线分支",
        )


def _resolve_existing_baseline(git: Any, member_dir: Path, branch: str) -> Any:
    """复用已有工作树时优先本地基线，缺失则使用已记录的 origin 引用。"""
    local = git(
        ["-C", str(member_dir), "rev-parse", f"{branch}^{{commit}}"], timeout=60
    )
    if local.returncode == 0:
        return local
    return git(
        [
            "-C",
            str(member_dir),
            "rev-parse",
            f"refs/remotes/origin/{branch}^{{commit}}",
        ],
        timeout=60,
    )


def _refresh_pool_member(git: Any, member_dir: Path, repository: str) -> None:
    """在池成员锁内刷新 origin，确保后续基线解析不是陈旧远端引用。"""
    result = git(
        ["-C", str(member_dir), "fetch", "--prune", "origin"], timeout=120
    )
    if result.returncode != 0:
        raise _blocked(
            "source_pool_fetch_failed",
            f"刷新池成员远端分支失败：{repository}",
            "请检查网络、远端访问权限和池成员 origin 后重试",
            details={"stderr_tail": _stderr_tail(result.stderr)},
        )


def _resolve_remote_baseline(
    git: Any, member_dir: Path, branch: str, repository: str
) -> str:
    """将任务分支解析为刷新后的 origin commit，避免依赖本地同名分支。"""
    remote_ref = f"refs/remotes/origin/{branch}^{{commit}}"
    result = git(["-C", str(member_dir), "rev-parse", "--verify", remote_ref], timeout=60)
    if result.returncode != 0:
        details = {"repository": repository, "branch": branch}
        if stderr_tail := _stderr_tail(result.stderr):
            details["stderr_tail"] = stderr_tail
        raise _blocked(
            "branch_derivation_failed",
            f"池成员刷新后未找到任务基线分支：{repository} @ {branch}",
            "请确认 Project Profile 的分支推导及远端 origin 分支后重试",
            details=details,
        )
    baseline = result.stdout.strip()
    if not baseline:
        raise _blocked(
            "branch_derivation_failed",
            f"池成员刷新后无法解析任务基线分支：{repository} @ {branch}",
            "请确认 Project Profile 的分支推导及远端 origin 分支后重试",
            details={"repository": repository, "branch": branch},
        )
    return baseline


def _remove_empty_worktree_parents(worktree_dir: Path, pool_root: Path) -> None:
    """仅移除本次失败留下的空任务目录，绝不越过池根。"""
    current = worktree_dir.parent
    pool = pool_root.resolve()
    while current != pool and current.is_relative_to(pool):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _write_worktree_identity(
    git: Any,
    worktree_dir: Path,
    execution_identity: dict[str, str] | None,
) -> None:
    """per-worktree 身份：启用 worktreeConfig 并写入 user.name/user.email。"""
    if not execution_identity:
        return
    git(["-C", str(worktree_dir), "config", "extensions.worktreeConfig", "true"], timeout=60)
    name = execution_identity.get("git_author_name")
    email = execution_identity.get("git_author_email")
    if name:
        git(["-C", str(worktree_dir), "config", "--worktree", "user.name", name], timeout=60)
    if email:
        git(["-C", str(worktree_dir), "config", "--worktree", "user.email", email], timeout=60)


def _rollback_worktree(
    git: Any,
    pool_root: Path,
    worktree_dir: Path,
    repositories: tuple[str, ...],
) -> None:
    for repository in repositories:
        result = git(
            ["-C", str(pool_root / repository), "worktree", "list", "--porcelain"],
            timeout=60,
        )
        if result.returncode != 0:
            continue
        if str(worktree_dir.resolve()) in result.stdout:
            git(
                ["-C", str(pool_root / repository), "worktree", "remove", "--force", str(worktree_dir)],
                timeout=60,
            )
            return


def _run_git(
    command: list[str],
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *command],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _stderr_tail(stderr: str | None, limit: int = 400) -> str:
    return (stderr or "")[-limit:]


def _blocked(
    code: str, message: str, action: str, *, details: dict[str, Any] | None = None
) -> RuntimeErrorResult:
    kwargs: dict[str, Any] = {}
    if details is not None:
        kwargs["details"] = details
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
        **kwargs,
    )
