from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ao_work.config import (
    load_jira_context,
    resolve_source_pool_root,
    validate_workspace_jira_binding,
)
from ao_work.installation import load_install_identity
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.service import JiraService
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_start import _description_sections, record_current_task_source_context
from ao_work.task_state import TaskStore
from ao_work.task_state.io import read_json
from ao_work.task_state.locking import TaskLock
from ao_work.task_worktree import (
    TaskWorktreePlan,
    WorktreePlanEntry,
    _refresh_pool_member,
    _resolve_remote_baseline,
    _rollback_worktree,
    _validate_git_branch_name,
    analyze_task_worktree_plan,
    plan_task_worktrees,
    prepare_task_worktrees,
    resolve_target_repository,
)
from ao_work.workspace import Workspace, task_worktree_path


def _alignment_script_for(
    install_root: Path, problem_version_repository: str
) -> Path | None:
    """按产品仓库身份选择版本化对齐工具，而非按 Jira Profile 名称选择。"""
    if problem_version_repository != "tapdata/tapdata":
        return None
    return (
        install_root
        / "developer"
        / "standards"
        / "projects"
        / "tapdata"
        / "scripts"
        / "tap_align_branches.py"
    )


def _blocked(code: str, message: str, action: str, **details: Any) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
        details=details,
    )


def execute_repository_assess(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str,
) -> dict[str, Any]:
    """生成仓库/分支分析建议；建议没有建树或编码权限。"""
    context, account, issue = _live_context(workspace, install_root, issue_key)
    task = store.inspect(issue_key)["task"]
    agentic_run_id = str(task["agentic_run_id"])
    pool_root = resolve_source_pool_root(install_root)
    if pool_root is None:
        raise _blocked(
            "source_pool_root_invalid",
            "中央源码池未配置",
            "请先配置 source_pool_root 并重新运行 workspace init",
        )
    sections = _description_sections(issue.description)
    target_repository = resolve_target_repository(context.profile, sections)
    domain = context.profile.domain_for(target_repository)
    if domain is None:
        raise _blocked(
            "task_domain_unresolved",
            f"无法根据候选仓库判定任务领域：{target_repository}",
            "请修正 Project Profile 领域配置或在确认关系表中明确仓库",
        )
    problem_version_repository = (
        domain.problem_version_repository or domain.baseline_repository
    )
    alignment_script = _alignment_script_for(
        install_root, problem_version_repository
    )
    plan = plan_task_worktrees(
        pool_root=pool_root,
        profile=context.profile,
        issue_key=issue_key,
        description_sections=sections,
        alignment_script=alignment_script,
    )
    analyzed, baselines = analyze_task_worktree_plan(plan)
    identity = load_install_identity(install_root)
    github_actor = str(
        identity.get("execution_identity", {}).get("github_actor_login") or ""
    ).strip()
    if not github_actor:
        raise _blocked(
            "execution_identity_missing",
            "安装身份缺少 GitHub actor，无法生成计划任务分支",
            "请运行 ao-work auth 修复安装身份",
        )
    rows: list[dict[str, Any]] = []
    for entry in analyzed.entries:
        analysis_branch = context.profile.baseline_branch(entry.repository)
        if not analysis_branch:
            raise _blocked(
                "analysis_baseline_unresolved",
                f"Profile 未声明仓库分析基线：{entry.repository}",
                "请在 Profile baseline_branches 中补齐该仓库，或修正默认分支",
            )
        analysis_sha = _resolve_remote_baseline(
            subprocess_git,
            analyzed.pool_root / entry.repository,
            analysis_branch,
            entry.repository,
        )
        task_branch = _task_branch(github_actor, issue_key, entry.branch)
        rows.append(
            {
                "repository": entry.repository,
                "problem_version_repository": analyzed.baseline_repository,
                "problem_version": analyzed.from_branch,
                "derivation_rule": "tap_align_branches" if alignment_script else "profile",
                "analysis_branch": analysis_branch,
                "analysis_baseline_sha": analysis_sha,
                "proposed_from_branch": entry.branch,
                "proposed_branch_sha": baselines[entry.repository],
                "task_branch": task_branch,
                "worktree_path": str(
                    task_worktree_path(
                        analyzed.pool_root,
                        issue_key,
                        entry.branch,
                        entry.repository,
                    )
                ),
                "worktree_status": "not_created",
            }
        )
    proposal = {
        "problem_version": analyzed.from_branch,
        "problem_version_repository": analyzed.baseline_repository,
        "problem_version_sha": baselines[analyzed.baseline_repository],
        "proposed_repository_branch_map": rows,
    }
    recorded = store.record_repository_proposal(
        issue_key,
        agentic_run_id,
        proposal,
    )
    return {
        "issue_key": issue_key,
        "agentic_run_id": agentic_run_id,
        **proposal,
        "proposal_authority": "analysis_only_not_confirmed",
        "confirmation_template": {
            "issue_key": issue_key,
            "agentic_run_id": agentic_run_id,
            "problem_version": analyzed.from_branch,
            "problem_version_repository": analyzed.baseline_repository,
            "problem_version_sha": baselines[analyzed.baseline_repository],
            "repository_branch_map": [
                {
                    "repository": item["repository"],
                    "from_branch": item["proposed_from_branch"],
                    "task_branch": item["task_branch"],
                }
                for item in rows
            ],
        },
        "repository_scope_path": recorded["path"],
        "agentic_next_action": {
            "action": "review_and_confirm_repository_branch_mapping",
            "allowed_operations": ["task_repositories_confirm"],
            "requires_authorization": True,
            "stop_workflow": True,
        },
    }


def execute_repository_confirm(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str,
    mapping: dict[str, Any],
    *,
    confirm: bool,
) -> dict[str, Any]:
    """验证用户修正后的完整关系表；只有 --confirm 才持久化。"""
    context, _, _ = _live_context(workspace, install_root, issue_key)
    state = store.inspect(issue_key)
    task = state["task"]
    scope = state.get("repository_scope")
    if not isinstance(scope, dict):
        raise _blocked(
            "repository_proposal_missing",
            "任务尚无仓库分支分析建议",
            "请先执行 ao-work task repositories assess",
        )
    agentic_run_id = str(task["agentic_run_id"])
    if mapping.get("issue_key") not in {None, issue_key}:
        raise _blocked(
            "repository_mapping_identity_mismatch",
            "确认文件的 issue_key 与当前任务不一致",
            "请使用当前任务生成并核对的完整关系表",
        )
    if mapping.get("agentic_run_id") not in {None, agentic_run_id}:
        raise _blocked(
            "repository_mapping_identity_mismatch",
            "确认文件的 agentic_run_id 与当前任务不一致",
            "请使用当前任务生成并核对的完整关系表",
        )
    problem_version = str(mapping.get("problem_version") or "").strip()
    if problem_version != str(scope.get("problem_version") or ""):
        raise _blocked(
            "problem_version_changed",
            "用户确认文件的问题版本与当前分析不一致",
            "请重新分析并展示问题版本与完整逐仓分支关系",
        )
    if (
        mapping.get("problem_version_repository")
        != scope.get("problem_version_repository")
        or mapping.get("problem_version_sha") != scope.get("problem_version_sha")
    ):
        raise _blocked(
            "problem_version_source_changed",
            "用户确认文件的问题版本来源仓库或固定 SHA 与分析不一致",
            "请使用 assess 输出的完整 confirmation_template 进行确认",
        )
    raw_rows = mapping.get("repository_branch_map")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise _blocked(
            "repository_mapping_invalid",
            "确认文件缺少非空 repository_branch_map",
            "请提供完整仓库分支关系表",
        )
    pool_root = resolve_source_pool_root(install_root)
    if pool_root is None:
        raise _blocked(
            "source_pool_root_invalid",
            "中央源码池未配置",
            "请先配置 source_pool_root",
        )
    problem_repository = str(scope.get("problem_version_repository") or "")
    if problem_repository not in context.profile.repository_candidates():
        raise _blocked(
            "problem_version_repository_invalid",
            "问题版本来源仓库不在当前 Profile 清单",
            "请修复 Profile 并重新执行仓库分析",
        )
    _refresh_pool_member(
        subprocess_git,
        pool_root / problem_repository,
        problem_repository,
    )
    current_problem_sha = _resolve_remote_baseline(
        subprocess_git,
        pool_root / problem_repository,
        problem_version,
        problem_repository,
    )
    if current_problem_sha != scope.get("problem_version_sha"):
        raise _blocked(
            "problem_version_baseline_changed",
            "问题版本来源分支在分析后已前移",
            "请重新执行仓库分析并展示最新固定 SHA 后再确认",
        )
    proposal_rows = {
        str(item.get("repository")): item
        for item in scope.get("proposed_repository_branch_map", [])
        if isinstance(item, dict)
    }
    allowed = set(context.profile.repository_candidates())
    identity = load_install_identity(install_root)
    github_actor = str(
        identity.get("execution_identity", {}).get("github_actor_login") or ""
    ).strip()
    if not github_actor:
        raise _blocked(
            "execution_identity_missing",
            "安装身份缺少 GitHub actor，无法固定任务分支",
            "请运行 ao-work auth 修复安装身份",
        )
    seen: set[str] = set()
    seen_worktree_paths: set[Path] = set()
    confirmed: list[dict[str, Any]] = []
    differences: list[dict[str, Any]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            raise _blocked(
                "repository_mapping_invalid",
                "repository_branch_map 条目必须是对象",
                "请修正确认文件",
            )
        repository = str(raw.get("repository") or "").strip()
        from_branch = str(raw.get("from_branch") or "").strip()
        if repository not in allowed or repository in seen:
            raise _blocked(
                "repository_mapping_invalid",
                f"仓库不在 Profile 清单或重复：{repository}",
                "请修正并重新确认完整关系表",
            )
        if context.profile.domain_for(repository) is None:
            raise _blocked(
                "task_domain_unresolved",
                f"确认仓库未映射唯一领域：{repository}",
                "请先修复 Project Profile 领域配置",
            )
        _validate_git_branch_name(from_branch)
        _refresh_pool_member(subprocess_git, pool_root / repository, repository)
        baseline_sha = _resolve_remote_baseline(
            subprocess_git,
            pool_root / repository,
            from_branch,
            repository,
        )
        proposed = proposal_rows.get(repository, {})
        proposed_branch = str(proposed.get("proposed_from_branch") or "")
        task_branch = str(raw.get("task_branch") or "").strip()
        if not task_branch:
            task_branch = _task_branch(github_actor, issue_key, from_branch)
        _validate_git_branch_name(task_branch)
        planned_worktree = task_worktree_path(
            pool_root,
            issue_key,
            from_branch,
            repository,
        )
        if planned_worktree in seen_worktree_paths:
            raise _blocked(
                "repository_worktree_path_collision",
                f"多个仓库映射到同一任务工作树路径：{planned_worktree}",
                "请修正 Profile 仓库短名冲突后重新确认",
            )
        row = {
            "repository": repository,
            "problem_version_repository": scope.get("problem_version_repository"),
            "problem_version": problem_version,
            "derivation_rule": proposed.get("derivation_rule", "user_supplied"),
            "proposed_from_branch": proposed_branch,
            "from_branch": from_branch,
            "analysis_branch": proposed.get("analysis_branch"),
            "analysis_baseline_sha": proposed.get("analysis_baseline_sha"),
            "confirmed_branch_sha": baseline_sha,
            "worktree_baseline_sha": None,
            "user_corrected": from_branch != proposed_branch or not proposed,
            "task_branch": task_branch,
            "worktree_path": str(planned_worktree),
            "worktree_status": "not_created",
        }
        confirmed.append(row)
        if not proposed or from_branch != proposed_branch:
            differences.append(
                {
                    "repository": repository,
                    "proposed_from_branch": proposed_branch or None,
                    "confirmed_from_branch": from_branch,
                }
            )
        seen.add(repository)
        seen_worktree_paths.add(planned_worktree)
    removed = sorted(set(proposal_rows) - seen)
    for repository in removed:
        differences.append(
            {
                "repository": repository,
                "proposed_from_branch": proposal_rows[repository].get(
                    "proposed_from_branch"
                ),
                "confirmed_from_branch": None,
            }
        )
    preview = {
        "issue_key": issue_key,
        "agentic_run_id": agentic_run_id,
        "problem_version": problem_version,
        "problem_version_repository": scope.get("problem_version_repository"),
        "problem_version_sha": scope.get("problem_version_sha"),
        "proposed_repository_branch_map": scope.get(
            "proposed_repository_branch_map", []
        ),
        "confirmed_repository_branch_map": confirmed,
        "confirmed_change_repositories": [item["repository"] for item in confirmed],
        "mapping_differences": differences,
    }
    if not confirm:
        return {
            **preview,
            "confirmation_required": True,
            "side_effects": [],
            "agentic_next_action": {
                "action": "confirm_repository_branch_mapping",
                "requires_authorization": True,
                "stop_workflow": True,
            },
        }
    result = store.confirm_repository_mapping(
        issue_key,
        agentic_run_id,
        confirmed,
        differences,
    )
    return {
        **preview,
        "confirmation_required": False,
        "mapping_status": "confirmed",
        "repository_scope_path": result["path"],
        "agentic_next_action": {
            "action": "prepare_confirmed_repository_worktree_when_needed",
            "allowed_operations": ["task_worktrees_prepare"],
            "requires_authorization": False,
            "stop_workflow": False,
        },
    }


def execute_worktree_prepare(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str,
    repository: str,
) -> dict[str, Any]:
    """只从用户确认关系表为一个仓库按需创建任务工作树。"""
    context, account, issue = _live_context(workspace, install_root, issue_key)
    state = store.inspect(issue_key)
    task = state["task"]
    scope = state.get("repository_scope")
    rows = scope.get("confirmed_repository_branch_map") if isinstance(scope, dict) else None
    if not isinstance(rows, list):
        raise _blocked(
            "repository_mapping_confirmation_required",
            "任务尚无用户确认的仓库分支关系",
            "请先确认完整关系表",
        )
    row = next((item for item in rows if item.get("repository") == repository), None)
    if not isinstance(row, dict):
        raise _blocked(
            "repository_outside_confirmed_mapping",
            f"仓库不在用户确认关系表中：{repository}",
            "请先按增量范围重新确认仓库分支关系",
        )
    if row.get("worktree_status") == "prepared":
        return {
            "issue_key": issue_key,
            "repository": repository,
            "worktree_path": row["worktree_path"],
            "task_branch": row["task_branch"],
            "created": False,
        }
    pool_root = resolve_source_pool_root(install_root)
    if pool_root is None:
        raise _blocked(
            "source_pool_root_invalid",
            "中央源码池未配置",
            "请先配置 source_pool_root",
        )
    from_branch = str(row["from_branch"])
    worktree_dir = task_worktree_path(pool_root, issue_key, from_branch, repository)
    plan = TaskWorktreePlan(
        issue_key=issue_key,
        from_branch=from_branch,
        pool_root=pool_root,
        entries=(WorktreePlanEntry(repository, worktree_dir, from_branch),),
        target_repository=repository,
        baseline_repository=repository,
    )
    identity = load_install_identity(install_root)
    prepared = prepare_task_worktrees(
        plan,
        execution_identity=dict(identity["execution_identity"]),
    )
    created = prepared.entries[0].created
    task_branch = str(row["task_branch"])
    current_branch = subprocess_git(
        ["-C", str(worktree_dir), "symbolic-ref", "--short", "HEAD"],
        timeout=60,
    )
    already_switched = (
        current_branch.returncode == 0 and current_branch.stdout.strip() == task_branch
    )
    if current_branch.returncode == 0 and not already_switched:
        raise _blocked(
            "task_branch_mismatch",
            f"现有任务工作树位于其它分支：{current_branch.stdout.strip()}",
            "请核对中断恢复状态；Runtime 不会覆盖现有分支",
        )
    switch = None
    if not already_switched:
        switch = subprocess.run(
            ["git", "-C", str(worktree_dir), "switch", "-c", task_branch],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    if switch is not None and switch.returncode != 0:
        if created:
            _rollback_worktree(
                subprocess_git,
                pool_root,
                worktree_dir,
                (repository,),
            )
        raise _blocked(
            "task_branch_create_failed",
            f"无法在任务工作树创建任务分支：{task_branch}",
            "请核对已确认任务分支与现有 Git refs 后重试",
            stderr_tail=switch.stderr[-400:],
        )
    head = subprocess_git(
        ["-C", str(worktree_dir), "rev-parse", "HEAD"], timeout=60
    )
    if head.returncode != 0:
        raise _blocked(
            "worktree_readback_failed",
            "任务工作树创建后无法回读 HEAD",
            "请保留工作树并人工核对，不要重复创建",
        )
    agentic_run_id = str(task["agentic_run_id"])
    store.update_repository_worktree(
        issue_key,
        agentic_run_id,
        repository,
        {
            "worktree_path": str(worktree_dir),
            "worktree_status": "prepared",
            "worktree_baseline_sha": head.stdout.strip(),
        },
    )
    mapped_status = context.profile.status_mapping.get(issue.status) or ""
    source_context = record_current_task_source_context(
        workspace,
        store,
        install_root=install_root,
        context=context,
        account=account,
        issue=issue,
        agentic_run_id=agentic_run_id,
        mapped_status=mapped_status,
        confirmed_repository=repository,
        confirmed_worktree=worktree_dir,
        confirmed_from_branch=from_branch,
        confirmed_task_branch=task_branch,
    )
    return {
        "issue_key": issue_key,
        "agentic_run_id": agentic_run_id,
        "repository": repository,
        "from_branch": from_branch,
        "task_branch": task_branch,
        "worktree_path": str(worktree_dir),
        "worktree_baseline_sha": head.stdout.strip(),
        "created": created,
        "intake_source": source_context["intake_source"],
        "agentic_next_action": {
            "action": "assess_task_intake",
            "allowed_operations": ["task_intake_assess"],
            "requires_authorization": False,
            "stop_workflow": False,
        },
    }


def collect_actual_change_repositories(
    workspace: Workspace,
    store: TaskStore,
    issue_key: str,
    agentic_run_id: str,
) -> list[dict[str, Any]]:
    """从确认工作树、远端分支及 task-run 结果回读实际变更仓库。"""
    state = store.inspect(issue_key)
    scope = state.get("repository_scope")
    rows = scope.get("confirmed_repository_branch_map") if isinstance(scope, dict) else None
    if not isinstance(rows, list):
        return []
    actual: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("worktree_status") != "prepared":
            continue
        repository = str(raw.get("repository") or "")
        worktree_dir = Path(str(raw.get("worktree_path") or "")).expanduser().resolve()
        if not worktree_dir.is_dir() or worktree_dir.is_symlink():
            raise _blocked(
                "worktree_readback_failed",
                f"任务工作树不存在或不是普通目录：{repository}",
                "请恢复任务状态登记的精确工作树后再生成 Jira 总结",
            )
        _require_clean_worktree(worktree_dir, repository)
        head = _git_text(worktree_dir, ["rev-parse", "HEAD"], "worktree_readback_failed")
        branch = _git_text(
            worktree_dir,
            ["symbolic-ref", "--short", "HEAD"],
            "worktree_branch_mismatch",
        )
        if branch != raw.get("task_branch"):
            raise _blocked(
                "worktree_branch_mismatch",
                f"任务工作树分支与确认表不一致：{repository}",
                "请停止总结并核对任务分支漂移",
            )
        baseline = str(raw.get("worktree_baseline_sha") or "")
        if not baseline:
            raise _blocked(
                "worktree_baseline_missing",
                f"任务工作树缺少固定基线：{repository}",
                "请重新准备工作树并记录固定 baseline SHA",
            )
        changed = _git_lines(
            worktree_dir,
            ["diff", "--name-only", f"{baseline}..{head}"],
            "worktree_diff_failed",
        )
        if not changed:
            continue
        remote = subprocess_git(
            ["-C", str(worktree_dir), "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
            timeout=60,
        )
        remote_sha = remote.stdout.split(maxsplit=1)[0] if remote.returncode == 0 and remote.stdout.strip() else ""
        if remote_sha != head:
            raise _blocked(
                "task_branch_remote_readback_missing",
                f"任务分支尚未由远端承接最终 Head：{repository}",
                "请推送任务分支并回读远端 SHA 后再生成 Jira 总结",
            )
        task_run = _task_run_repository_facts(
            workspace,
            issue_key,
            agentic_run_id,
            repository,
            worktree_dir,
            head,
        )
        validation_results = raw.get("validation_results") or task_run.get("verifications")
        if not validation_results:
            raise _blocked(
                "repository_validation_evidence_missing",
                f"实际变更仓库缺少验证结果：{repository}",
                "请通过 task-run verify 记录当前 Head 的验证结果后再生成 Jira 总结",
            )
        actual.append(
            {
                "repository": repository,
                "problem_branch": raw.get("from_branch"),
                "baseline_sha": baseline,
                "task_branch": branch,
                "head_sha": head,
                "changed_paths": changed,
                "validation_results": validation_results,
                "remote_sha": remote_sha,
                "pr_url": raw.get("pr_url") or task_run.get("pr_url"),
                "pr_head_sha": raw.get("pr_head_sha") or task_run.get("pr_head_sha"),
                "pr_base_branch": raw.get("pr_base_branch") or task_run.get("pr_base_branch"),
            }
        )
    return actual


def validate_repository_summary_content(
    content: str,
    repositories: list[dict[str, Any]],
) -> None:
    """完成证据评论必须显式包含实际变更仓库字段和每个仓库。"""
    if not repositories:
        raise _blocked(
            "actual_change_repositories_empty",
            "完成总结没有可验证的实际变更仓库",
            "请核对确认范围、Git diff、验证与远端/PR 回读后重试",
        )
    if "actual_change_repositories" not in content or "实际变更仓库" not in content:
        raise _blocked(
            "repository_summary_fields_missing",
            "Jira 完成证据评论缺少实际变更仓库表单字段",
            "请在完成内容中逐仓列出实际变更仓库，并在已输出表单字段中包含 actual_change_repositories",
        )
    missing = [
        str(item["repository"])
        for item in repositories
        if str(item["repository"]) not in content
    ]
    if missing:
        raise _blocked(
            "repository_summary_incomplete",
            f"Jira 完成证据评论遗漏实际变更仓库：{', '.join(missing)}",
            "请按 Runtime 回读集合补齐逐仓总结后重新 plan",
        )


def execute_worktree_cleanup(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str,
) -> dict[str, Any]:
    """完成态后整体预检并非强制移除精确登记的任务工作树。"""
    context, _, issue = _live_context(workspace, install_root, issue_key)
    if context.profile.status_mapping.get(issue.status) != "completed":
        raise _blocked(
            "worktree_cleanup_status_forbidden",
            f"Jira 任务尚未进入完成态：{issue.status}",
            "请先完成总结评论回读和 Jira 完成状态回读",
        )
    state = store.inspect(issue_key)
    task = state["task"]
    scope = state.get("repository_scope")
    rows = scope.get("confirmed_repository_branch_map") if isinstance(scope, dict) else None
    if not isinstance(rows, list):
        raise _blocked(
            "repository_mapping_confirmation_required",
            "任务尚无用户确认的仓库分支关系",
            "请先核对任务仓库状态，不要清理未知路径",
        )
    if not scope.get("completion_summary_readback"):
        raise _blocked(
            "repository_summary_readback_missing",
            "实际变更仓库完成总结尚未完成 Jira 回读",
            "请先完成 evidence 评论 plan、apply、readback",
        )
    pool_root = resolve_source_pool_root(install_root)
    if pool_root is None:
        raise _blocked("source_pool_root_invalid", "中央源码池未配置", "请恢复原源码池配置")
    prepared = [row for row in rows if row.get("worktree_status") == "prepared"]
    if not prepared:
        return {"issue_key": issue_key, "cleaned": [], "already_clean": True}
    cleanup_lock = pool_root / ".locks" / f"{issue_key}.cleanup.lock"
    preflight: list[dict[str, Any]] = []
    actual_by_repository = {
        str(item.get("repository")): item
        for item in scope.get("actual_change_repositories", [])
        if isinstance(item, dict)
    }
    with TaskLock(cleanup_lock, timeout=10):
        for row in prepared:
            repository = str(row["repository"])
            expected = task_worktree_path(
                pool_root,
                issue_key,
                str(row["from_branch"]),
                repository,
            ).resolve()
            recorded = Path(str(row["worktree_path"])).expanduser().resolve()
            if recorded != expected or not recorded.is_dir() or recorded.is_symlink():
                raise _blocked(
                    "worktree_cleanup_path_mismatch",
                    f"清理路径与确认表不一致：{repository}",
                    "请人工核对登记路径；Runtime 不会删除不确定目录",
                )
            _require_clean_worktree(recorded, repository)
            head = _git_text(recorded, ["rev-parse", "HEAD"], "worktree_readback_failed")
            branch = _git_text(
                recorded,
                ["symbolic-ref", "--short", "HEAD"],
                "worktree_branch_mismatch",
            )
            if branch != row.get("task_branch"):
                raise _blocked(
                    "worktree_branch_mismatch",
                    f"清理前任务分支发生漂移：{repository}",
                    "请核对当前工作树与完成证据",
                )
            actual = actual_by_repository.get(repository)
            audited_head = (
                str(actual.get("head_sha") or "")
                if isinstance(actual, dict)
                else str(row.get("worktree_baseline_sha") or "")
            )
            if not audited_head or audited_head != head:
                raise _blocked(
                    "worktree_cleanup_head_mismatch",
                    f"清理前 Head 与完成审计不一致：{repository}",
                    "请重新生成实际变更仓库证据并完成 Jira 回读",
                )
            member = pool_root / repository
            _refresh_pool_member(subprocess_git, member, repository)
            containing = _git_lines(
                member,
                ["branch", "-r", "--contains", head],
                "worktree_remote_carry_missing",
            )
            if not containing:
                raise _blocked(
                    "worktree_remote_carry_missing",
                    f"最终提交尚未被远端分支承接：{repository}",
                    "请推送任务分支或确认 PR/合入事实后重试",
                )
            preflight.append({"repository": repository, "path": recorded, "head": head, "member": member})
        cleaned: list[dict[str, Any]] = []
        for item in preflight:
            removal = subprocess_git(
                ["-C", str(item["member"]), "worktree", "remove", str(item["path"])],
                timeout=60,
            )
            if removal.returncode != 0:
                raise _blocked(
                    "worktree_cleanup_failed",
                    f"任务工作树非强制清理失败：{item['repository']}",
                    "请保留剩余工作树并按输出恢复清单处理",
                    stderr_tail=removal.stderr[-400:],
                    cleaned=[entry["repository"] for entry in cleaned],
                )
            if item["path"].exists() or item["path"].is_symlink():
                raise _blocked(
                    "worktree_cleanup_readback_failed",
                    f"任务工作树清理后路径仍存在：{item['repository']}",
                    "请停止后续清理并人工核对 Git worktree 状态",
                )
            worktree_list = subprocess_git(
                ["-C", str(item["member"]), "worktree", "list", "--porcelain"],
                timeout=60,
            )
            if (
                worktree_list.returncode != 0
                or str(item["path"]) in worktree_list.stdout
            ):
                raise _blocked(
                    "worktree_cleanup_readback_failed",
                    f"Git worktree 清单仍包含已清理路径：{item['repository']}",
                    "请停止后续清理并人工核对 Git worktree 状态",
                )
            store.update_repository_worktree(
                issue_key,
                str(task["agentic_run_id"]),
                str(item["repository"]),
                {"worktree_status": "cleaned", "cleaned_head_sha": item["head"]},
            )
            cleaned.append({"repository": item["repository"], "head_sha": item["head"]})
            try:
                item["path"].parent.rmdir()
            except OSError:
                pass
        for candidate in (
            pool_root / ".worktree" / issue_key,
            pool_root / ".worktree",
        ):
            try:
                candidate.rmdir()
            except OSError:
                pass
    return {
        "issue_key": issue_key,
        "cleaned": cleaned,
        "already_clean": False,
        "source_pool_preserved": True,
    }


def _require_clean_worktree(path: Path, repository: str) -> None:
    dirty = _git_lines(path, ["status", "--porcelain"], "worktree_status_failed")
    if dirty:
        raise _blocked(
            "worktree_dirty",
            f"任务工作树仍有未提交修改：{repository}",
            "请提交、回退或人工保留修改；Runtime 不会强制清理",
        )


def _git_text(path: Path, args: list[str], code: str) -> str:
    result = subprocess_git(["-C", str(path), *args], timeout=60)
    if result.returncode != 0 or not result.stdout.strip():
        raise _blocked(code, f"Git 回读失败：{' '.join(args)}", "请修复 Git 状态后重试")
    return result.stdout.strip()


def _git_lines(path: Path, args: list[str], code: str) -> list[str]:
    result = subprocess_git(["-C", str(path), *args], timeout=60)
    if result.returncode != 0:
        raise _blocked(code, f"Git 回读失败：{' '.join(args)}", "请修复 Git 状态后重试")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _task_run_repository_facts(
    workspace: Workspace,
    issue_key: str,
    agentic_run_id: str,
    repository: str,
    worktree_dir: Path,
    head_sha: str,
) -> dict[str, Any]:
    runs_root = (
        workspace.root
        / ".agentic-ops"
        / "tasks"
        / issue_key
        / "runs"
    )
    if not runs_root.is_dir() or runs_root.is_symlink():
        return {}
    run_directories = {
        child.name: child
        for child in runs_root.iterdir()
        if child.is_dir() and not child.is_symlink()
    }
    ordered_run_ids = [agentic_run_id, *sorted(run_directories)]
    seen: set[str] = set()
    for run_id in ordered_run_ids:
        if run_id in seen:
            continue
        seen.add(run_id)
        run_directory = run_directories.get(run_id)
        if run_directory is None:
            continue
        root = run_directory / "task-to-pr"
        if not root.is_dir() or root.is_symlink():
            continue
        manifest_path = root / "manifest.json"
        result_path = root / "result.json"
        if not manifest_path.is_file() or not result_path.is_file():
            continue
        manifest = read_json(manifest_path)
        result = read_json(result_path)
        manifest_repository = manifest.get("repository")
        manifest_issue = manifest.get("issue")
        if not isinstance(manifest_repository, dict) or not isinstance(manifest_issue, dict):
            continue
        manifest_root = manifest_repository.get("root")
        if not isinstance(manifest_root, str):
            continue
        if (
            manifest_issue.get("key") != issue_key
            or manifest_repository.get("slug") != repository
            or Path(manifest_root).expanduser().resolve() != worktree_dir
            or result.get("status") != "ready_for_pr_review"
        ):
            continue
        facts = result.get("facts") if isinstance(result.get("facts"), dict) else {}
        remote = facts.get("remote_branch_readback") if isinstance(facts, dict) else None
        if (
            not isinstance(remote, dict)
            or remote.get("repository_slug") != repository
            or remote.get("status") != "exists"
            or remote.get("sha") != head_sha
            or remote.get("head_sha") != head_sha
        ):
            continue
        pr = facts.get("pr_readback") if isinstance(facts.get("pr_readback"), dict) else {}
        pr_head_sha = pr.get("head_sha") or pr.get("headRefOid")
        if pr and pr_head_sha != head_sha:
            continue
        return {
            "verifications": facts.get("verifications", []),
            "pr_url": pr.get("url"),
            "pr_head_sha": pr_head_sha,
            "pr_base_branch": pr.get("base_branch") or pr.get("baseRefName"),
        }
    return {}


def _live_context(
    workspace: Workspace,
    install_root: Path,
    issue_key: str,
) -> tuple[Any, dict[str, Any], Any]:
    context = load_jira_context(workspace, install_root)
    email, token = context.require_credentials()
    client = JiraClient(
        context.profile,
        UrllibJiraTransport(context.connection, email, token),
    )
    account = client.current_user_details()
    validate_workspace_jira_binding(
        workspace,
        context.connection,
        account_id=account["account_id"],
        install_root=install_root,
    )
    issue = JiraService(context.profile, client).inspect_issue(issue_key)
    if issue.assignee != account["account_id"]:
        raise _blocked(
            "assignee_changed",
            "当前工作空间 Jira 账户不是任务经办人",
            "请先按 Jira 项目流程核对任务所有权",
        )
    return context, account, issue


def _task_branch(actor: str, issue_key: str, from_branch: str) -> str:
    normalized = from_branch.replace("/", "-")
    branch = f"{actor}/{issue_key}/{normalized}"
    _validate_git_branch_name(branch)
    return branch


def subprocess_git(
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
