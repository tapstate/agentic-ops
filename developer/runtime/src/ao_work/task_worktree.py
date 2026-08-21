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
) -> str:
    """解析任务 from_branch：描述「修复分支」section 优先，缺省 profile.branches 默认规则。

    返回规范化后的分支名（含 / 替换为 -），并做路径穿越校验。
    """
    declared = description_sections.get("修复分支", "").strip()
    if declared:
        candidate = declared.splitlines()[0].strip()
    else:
        derivation = profile.branch_derivation
        candidate = derivation.default_branch if derivation.default_branch else "main"
    return normalize_worktree_from_branch(candidate)


def plan_task_worktrees(
    *,
    pool_root: Path,
    profile: Any,
    issue_key: str,
    description_sections: dict[str, str],
) -> TaskWorktreePlan:
    """计算任务工作树集计划：目标仓库 + 分析挂载集 + 分支推导。

    挂载集 = profile.mounts_for_analysis()（all/include/exclude 策略）。
    每个仓库推导分支：derive_branch(repository, from_branch)。
    返回计划而不创建；创建由 prepare_task_worktrees 执行。
    """
    pool = validate_source_pool_root(pool_root)
    from_branch = resolve_from_branch(profile, description_sections)
    target_repository = resolve_target_repository(profile, description_sections)
    repositories = profile.mounts_for_analysis()
    if target_repository not in repositories:
        # 目标仓库必须在分析挂载集内（否则 AI 无源可改）。
        raise _blocked(
            "target_repository_not_mounted",
            f"目标仓库 {target_repository} 不在分析挂载集内",
            "请调整 profile analysis_mount 配置，确保目标仓库被挂载",
        )
    entries: list[WorktreePlanEntry] = []
    for repository in repositories:
        branch = profile.derive_branch(repository, from_branch)
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
    """创建/复用任务工作树集：逐个仓库 git worktree add --detach。

    - 已存在 → 校验路径与分支后复用（不重复创建）。
    - 缺失 → 在池成员上创建任务工作树，写入 per-worktree 身份。
    - 任一失败 → 已创建的工作树全部回滚（worktree remove --force）。
    - 池成员级并发锁：<pool_root>/.locks/<repo>.lock。
    """
    git = run_git or _run_git
    created_dirs: list[Path] = []
    adopted = 0
    created = 0
    try:
        for entry in plan.entries:
            member_dir = plan.pool_root / entry.repository
            lock_path = plan.pool_root / ".locks" / f"{entry.repository.replace('/', '__')}.lock"
            with TaskLock(lock_path, timeout=10):
                if entry.worktree_dir.is_dir():
                    _validate_existing_worktree(git, entry.worktree_dir, entry.branch, entry.repository)
                    adopted += 1
                    continue
                entry.worktree_dir.parent.mkdir(parents=True, exist_ok=True)
                result = git(
                    [
                        "-C",
                        str(member_dir),
                        "worktree",
                        "add",
                        "--detach",
                        str(entry.worktree_dir),
                        entry.branch,
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


def _validate_existing_worktree(
    git: Any,
    worktree_dir: Path,
    branch: str,
    repository: str,
) -> None:
    result = git(
        ["-C", str(worktree_dir), "rev-parse", "--abbrev-ref", "HEAD"],
        timeout=60,
    )
    if result.returncode != 0:
        raise _blocked(
            "worktree_invalid",
            f"任务工作树已存在但不是 Git 工作树：{worktree_dir}",
            "请检查并清理该目录后重试",
            details={"stderr_tail": _stderr_tail(result.stderr)},
        )
    current = result.stdout.strip()
    # detached HEAD 的 --abbrev-ref HEAD 输出 "HEAD"；复用时校验必须匹配推导分支
    # 或处于 detached 状态（worktree add --detach 的常态）。
    if current not in {"HEAD", branch}:
        raise _blocked(
            "worktree_branch_mismatch",
            f"任务工作树 {repository} 已挂出到 {current}，与推导分支 {branch} 不一致",
            "请清理该任务工作树后重试，或核对任务分支推导配置",
        )


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
