from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.locking import TaskLock
from ao_work.workspace import (
    normalize_worktree_from_branch,
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
    target_repository: str = ""
    baseline_repository: str = ""
    alignment_script: Path | None = None
    alignment_spec: str = ""
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
    allow_alignment_spec: bool = False,
) -> str:
    """解析任务问题版本：描述「问题版本」优先，兼容旧「修复分支」。

    返回领域基线仓库的 Git ref。产品域允许问题版本首行携带
    `<tapdata>,<enterprise>,<web>` 对齐规格，但路径只使用第一个 tapdata ref。
    """
    declared = _declared_branch_spec(description_sections)
    if declared:
        candidate = declared
    else:
        repository = target_repository or resolve_target_repository(
            profile,
            description_sections,
        )
        candidate = profile.baseline_branch(repository)
        if not candidate:
            raise _blocked(
                "task_baseline_unresolved",
                f"目标仓库未配置任务基线分支：{repository}",
                "请在 Project Profile 显式声明该仓库的 baseline_branches，或在 Jira 描述声明问题版本",
                details={"repository": repository},
            )
    problem_version = (
        candidate.split(",", 1)[0].strip() if allow_alignment_spec else candidate
    )
    normalize_worktree_from_branch(problem_version)
    return problem_version


def resolve_product_alignment_branch(
    description_sections: dict[str, str],
    repository: str,
) -> str:
    """从产品域三段式规格提取目标仓库的显式分支；未显式指定时返回空串。"""
    declared = _declared_branch_spec(description_sections)
    if "," not in declared:
        return ""
    parts = declared.split(",")
    explicit = {
        "tapdata/tapdata": parts[0].strip() if parts else "",
        "tapdata/tapdata-enterprise": parts[1].strip() if len(parts) > 1 else "",
        "tapdata/tapdata-web": parts[2].strip() if len(parts) > 2 else "",
    }.get(repository, "")
    if explicit:
        normalize_worktree_from_branch(explicit)
    return explicit


def _declared_branch_spec(description_sections: dict[str, str]) -> str:
    declared = description_sections.get("问题版本", "").strip() or description_sections.get(
        "修复分支",
        "",
    ).strip()
    return declared.splitlines()[0].strip() if declared else ""


def plan_task_worktrees(
    *,
    pool_root: Path,
    profile: Any,
    issue_key: str,
    description_sections: dict[str, str],
    alignment_script: Path | None = None,
) -> TaskWorktreePlan:
    """计算任务工作树集计划：目标仓库所属领域 + 问题版本分支推导。

    已声明领域的 Profile 只挂载该领域仓库；无法归类时失败关闭。
    未声明领域的旧 Profile 仅为兼容而使用 analysis_mount 回退。
    每个仓库按该领域基线推导目标分支。
    返回计划而不创建；创建由 prepare_task_worktrees 执行。
    """
    pool = validate_source_pool_root(pool_root)
    target_repository = resolve_target_repository(profile, description_sections)
    domain = profile.domain_for(target_repository)
    if domain is None:
        raise _blocked(
            "task_domain_unresolved",
            f"无法根据目标仓库判定任务领域：{target_repository}",
            "请补充可映射的目标仓库或任务领域；系统不会创建全量工作树",
            details={"target_repository": target_repository},
        )
    from_branch = resolve_from_branch(
        profile,
        description_sections,
        target_repository=domain.baseline_repository,
        allow_alignment_spec=alignment_script is not None,
    )
    alignment_spec = _declared_branch_spec(description_sections) or from_branch
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
        target_repository=target_repository,
        baseline_repository=domain.baseline_repository,
        alignment_script=alignment_script,
        alignment_spec=alignment_spec,
    )


def prepare_task_worktrees(
    plan: TaskWorktreePlan,
    *,
    execution_identity: dict[str, str] | None = None,
    run_git: Any | None = None,
    run_alignment: Any | None = None,
) -> TaskWorktreePlan:
    """创建/复用任务工作树集：预检后逐个仓库 git worktree add --detach。

    - 已存在 → 校验路径与分支后复用（不重复创建）。
    - 旧布局存在 → 失败关闭，避免恢复任务忽略旧工作树中的未提交修改。
    - 缺失 → 先在池成员锁内刷新 origin、解析全部远端基线；全部通过后才创建任务工作树。
    - 任一失败 → 已创建的工作树全部回滚（worktree remove --force），并清理本次留下的空父目录。
    - 池成员级并发锁：<pool_root>/.locks/<repo>.lock。
    """
    git = run_git or _run_git
    alignment = run_alignment or subprocess.run
    created_dirs: list[Path] = []
    entries = plan.entries
    adopted = 0
    created = 0
    try:
        _reject_legacy_worktrees(plan)
        baselines: dict[Path, str] = {}
        for entry in plan.entries:
            member_dir = plan.pool_root / entry.repository
            lock_path = plan.pool_root / ".locks" / f"{entry.repository.replace('/', '__')}.lock"
            with TaskLock(lock_path, timeout=10):
                _refresh_pool_member(git, member_dir, entry.repository)

        entries = _apply_alignment_plan(plan, alignment)
        for entry in entries:
            member_dir = plan.pool_root / entry.repository
            lock_path = plan.pool_root / ".locks" / f"{entry.repository.replace('/', '__')}.lock"
            with TaskLock(lock_path, timeout=10):
                baseline = _resolve_remote_baseline(
                    git, member_dir, entry.branch, entry.repository
                )
                baselines[entry.worktree_dir] = baseline
                if entry.worktree_dir.is_dir():
                    _validate_existing_worktree(
                        git,
                        entry.worktree_dir,
                        baseline,
                        entry.branch,
                        entry.repository,
                    )

        for entry in entries:
            member_dir = plan.pool_root / entry.repository
            lock_path = plan.pool_root / ".locks" / f"{entry.repository.replace('/', '__')}.lock"
            with TaskLock(lock_path, timeout=10):
                if entry.worktree_dir.is_dir():
                    _validate_existing_worktree(
                        git,
                        entry.worktree_dir,
                        baselines[entry.worktree_dir],
                        entry.branch,
                        entry.repository,
                    )
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
                tuple(entry.repository for entry in entries),
            )
        for entry in entries:
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
            for entry in entries
        ),
        target_repository=plan.target_repository,
        baseline_repository=plan.baseline_repository,
        alignment_script=plan.alignment_script,
        alignment_spec=plan.alignment_spec,
        adopted=adopted,
        created=created,
    )


def _apply_alignment_plan(
    plan: TaskWorktreePlan,
    run_alignment: Any,
) -> tuple[WorktreePlanEntry, ...]:
    """用项目只读 plan 计算领域内各仓库的实际目标分支。"""
    script = plan.alignment_script
    if script is None:
        return plan.entries
    if not script.is_file():
        raise _blocked(
            "branch_alignment_tool_missing",
            f"分支对齐脚本不存在：{script}",
            "请修复当前 Project Profile 的标准资产安装后重试；系统不会按同名分支猜测",
        )

    owners = {entry.repository.split("/", 1)[0] for entry in plan.entries}
    if len(owners) != 1:
        raise _blocked(
            "branch_alignment_failed",
            "同一任务领域包含不同 owner，无法调用项目分支对齐脚本",
            "请修正 worktree_domains，使项目对齐脚本只接收同一源码根下的仓库",
            details={"repositories": [entry.repository for entry in plan.entries]},
        )
    owner = owners.pop()
    short_names = [entry.repository.split("/", 1)[1] for entry in plan.entries]
    command = [
        sys.executable,
        str(script),
        "--root",
        str(plan.pool_root / owner),
        "plan",
        plan.alignment_spec or plan.from_branch,
        "--no-fetch",
        "--remote-only",
        "--repositories",
        ",".join(short_names),
        "--json",
    ]
    try:
        result = run_alignment(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _blocked(
            "branch_alignment_failed",
            f"问题版本分支对齐脚本执行失败：{plan.from_branch}",
            "请检查项目标准资产与本机 Python 环境后重试；系统尚未创建任何工作树",
            details={"error": str(exc)},
        ) from exc
    if result.returncode != 0:
        raise _blocked(
            "branch_alignment_failed",
            f"问题版本分支对齐失败：{plan.from_branch}",
            "请根据对齐脚本错误补充分支信息或修复仓库状态后重试；系统尚未创建任何工作树",
            details={"stderr_tail": _stderr_tail(result.stderr)},
        )
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _blocked(
            "branch_alignment_failed",
            "分支对齐脚本未返回有效 JSON 计划",
            "请检查安装的项目标准资产版本后重试；系统尚未创建任何工作树",
            details={"error": str(exc), "stderr_tail": _stderr_tail(result.stderr)},
        ) from exc
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise _blocked(
            "branch_alignment_failed",
            "分支对齐脚本返回的计划结构无效",
            "请检查安装的项目标准资产版本后重试；系统尚未创建任何工作树",
        )

    rows = {str(row.get("repo", "")): row for row in payload}
    expected_rows = set(short_names)
    if len(rows) != len(payload) or set(rows) != expected_rows:
        raise _blocked(
            "branch_alignment_failed",
            "分支对齐脚本返回的仓库集合与任务领域不一致",
            "请检查领域配置与分支对齐脚本版本；系统不会扩大挂载范围",
            details={
                "expected_repositories": sorted(expected_rows),
                "actual_repositories": sorted(rows),
            },
        )
    aligned: list[WorktreePlanEntry] = []
    for entry, short_name in zip(plan.entries, short_names, strict=True):
        row = rows.get(short_name)
        if row is None:
            raise _blocked(
                "branch_alignment_failed",
                f"分支对齐计划缺少仓库：{entry.repository}",
                "请检查领域配置与分支对齐脚本支持的仓库清单",
            )
        target = str(row.get("target", "")).strip()
        if target == "KEEP_CURRENT":
            target = str(row.get("current", "")).strip()
        if (
            row.get("action") == "blocked"
            or not target
            or target in {"HEAD", "UNRESOLVED"}
        ):
            raise _blocked(
                "branch_alignment_failed",
                f"无法对齐领域仓库分支：{entry.repository}",
                "请补充明确的关联仓库分支后重试；系统尚未创建任何工作树",
                details={
                    "repository": entry.repository,
                    "problem_version": plan.from_branch,
                    "reason": str(row.get("reason", "")),
                },
            )
        aligned.append(
            WorktreePlanEntry(
                repository=entry.repository,
                worktree_dir=entry.worktree_dir,
                branch=target,
            )
        )
    return tuple(aligned)


def _reject_legacy_worktrees(plan: TaskWorktreePlan) -> None:
    """旧布局不得被静默忽略或覆盖，必须由人工决定迁移或清理。"""
    legacy_root = plan.pool_root / plan.issue_key
    if legacy_root.exists() or legacy_root.is_symlink():
        raise _blocked(
            "worktree_legacy_layout_detected",
            f"检测到旧布局任务根：{legacy_root}",
            "请先人工确认该 Jira 任务根下全部工作树中的修改并迁移或清理；系统不会创建新的 .worktree 副本",
            details={"legacy_task_root": str(legacy_root), "issue_key": plan.issue_key},
        )


def _validate_existing_worktree(
    git: Any,
    worktree_dir: Path,
    expected_commit: str,
    branch: str,
    repository: str,
) -> None:
    root = git(["-C", str(worktree_dir), "rev-parse", "--show-toplevel"], timeout=60)
    actual = git(["-C", str(worktree_dir), "rev-parse", "HEAD"], timeout=60)
    if root.returncode != 0 or actual.returncode != 0:
        raise _blocked(
            "worktree_invalid",
            f"任务工作树已存在但不是 Git 工作树：{worktree_dir}",
            "请检查并清理该目录后重试",
            details={"stderr_tail": _stderr_tail(root.stderr or actual.stderr)},
        )
    if Path(root.stdout.strip()).resolve() != worktree_dir.resolve() or actual.stdout.strip() != expected_commit:
        raise _blocked(
            "worktree_baseline_mismatch",
            f"任务工作树 {repository} 不是当前基线 {branch} 的精确工作树",
            "请清理该任务工作树后重试，或核对目标仓库和基线分支",
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
