from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ao_work.config import (
    load_jira_context,
    resolve_source_pool_root,
    validate_workspace_jira_binding,
)
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.model import plain_text
from ao_work.jira.service import JiraService
from ao_work.installation import load_install_identity
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_gate import record_task_start_context
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.task_state.io import read_json
from ao_work.task_worktree import (
    resolve_from_branch,
    resolve_product_alignment_branch,
    resolve_target_repository,
)
from ao_work.workspace import Workspace


def execute_task_start(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str,
) -> dict[str, Any]:
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
            "jira_assignee_mismatch",
            "当前业务工作空间 Jira 账户不是任务经办人",
            "请在 Jira 按项目流程调整经办人，或切换到正确研发员工作空间",
        )
    mapped_status = context.profile.status_mapping.get(issue.status)
    if not mapped_status:
        raise _blocked(
            "jira_status_mapping_missing",
            f"Project Profile 未映射 Jira 状态：{issue.status}",
            "请先在项目 Profile 中确认状态映射，不要让 AI 临场猜测",
        )
    if mapped_status == "completed":
        raise _blocked(
            "jira_task_already_completed",
            "Jira 任务已处于完成分类，不能启动新的研发运行",
            "请核对任务状态；需要重新处理时先按 Jira 流程恢复任务",
        )

    existing = _existing_task(store, issue.key)
    if existing is None:
        agentic_run_id = _new_run_id(issue.key)
        initialized = store.initialize(
            TaskIdentity(
                connection_id=context.connection.connection_id,
                jira_issue_id=issue.issue_id,
                issue_key=issue.key,
                project_key=issue.project_key,
                agentic_run_id=agentic_run_id,
            )
        )
        task_state_created = bool(initialized["created"])
    else:
        _validate_existing_task(existing, context.connection.connection_id, issue)
        agentic_run_id = str(existing["agentic_run_id"])
        task_state_created = False

    source_context = record_current_task_source_context(
        workspace,
        store,
        install_root=install_root,
        context=context,
        account=account,
        issue=issue,
        agentic_run_id=agentic_run_id,
        mapped_status=mapped_status,
    )
    issue_payload = source_context["issue"]
    workspace_defaults = source_context["workspace_defaults"]
    agent_config = source_context["agent_config"]
    task_worktrees = source_context.get("task_worktrees")
    intake_source = source_context["intake_source"]
    return {
        "issue": issue_payload,
        "workspace_defaults": workspace_defaults,
        "agentic_run_id": agentic_run_id,
        "task_state_created": task_state_created,
        "intake_source": intake_source,
        "task_worktrees": task_worktrees,
        "configuration_sources": {
            "installation": ["agent_id", "Jira 账户", "执行身份"],
            "workspace": ["project_profile", "源码仓库"],
            "project_profile": ["Jira 站点", "Project", "状态/字段映射", "默认仓库"],
            "jira_issue": ["Issue ID", "经办人", "状态", "标题", "描述", "任务类型"],
            "runtime": ["agentic_run_id", "issue_content_sha256"],
        },
        "review_required": [
            "设计审查中的实施方案、范围、任务分支和验证方式",
            "逐项风险决策与本次允许的外部动作",
            "代码审查中的 commit 或 PR 当前 Head",
        ],
        "intake_gate": {
            "stage": "information_analysis",
            "required_sequence": [
                "analyze_jira_and_project_context",
                "identify_missing_information",
                "auto_fill_from_verified_sources",
                "prepare_and_classify_solution",
            ],
            "auto_fill_source_priority": [
                "jira_issue",
                "project_profile",
                "business_source_code",
                "runtime_readback",
            ],
            "auto_fill_requires_evidence": True,
            "unresolved_required_information_blocks": True,
            "user_confirmation_required_before_solution": False,
        },
        "solution_gate": {
            "levels": {
                "L1": {
                    "action": "review_task_design",
                    "meaning": "信息完整且没有额外风险标志，进入设计审查",
                },
                "L2": {
                    "action": "decide_solution_risk",
                    "meaning": "方案包含用户选择、外部副作用或非平凡风险，逐项决策",
                },
                "L3": {
                    "action": "revise_design_and_reassess",
                    "meaning": "触及架构、公共合同、安全边界、数据迁移或已确认设计，需要先改设计",
                },
                "L4": {
                    "action": "stop_and_escalate",
                    "meaning": "事实冲突、必要信息无法补齐、权限或能力不足，当前无法推进",
                },
            },
            "classification_requires_evidence": True,
            "classification_must_be_recomputed_after_change": True,
        },
        "formal_takeover_verified": False,
        "task_ownership": {
            "task_owner": agent_config.get("agent_id"),
            "continuity": "same_owner_until_pr_review",
            "transfer_capability": "capability_gap",
            "transfer_decision_authority": "human_only",
        },
        "agentic_next_action": {
            "executor": "ai",
            "action": "assess_task_intake",
            "required_inputs": [
                "issue_key",
                "agentic_run_id",
                "intake_input_file",
            ],
            "allowed_operations": ["task_intake_assess"],
            "requires_authorization": False,
            "stop_workflow": False,
            "ownership_effect": "none",
            "reason": "由 Runtime 校验缺项和证据化补全；事实完整后自动形成方案并进入设计审查或风险决策",
        },
    }


def record_current_task_source_context(
    workspace: Workspace,
    store: TaskStore,
    *,
    install_root: Path,
    context: Any,
    account: dict[str, Any],
    issue: Any,
    agentic_run_id: str,
    mapped_status: str,
    confirmed_repository: str | None = None,
    confirmed_worktree: Path | None = None,
    confirmed_from_branch: str = "",
    confirmed_task_branch: str = "",
) -> dict[str, Any]:
    description_text = plain_text(issue.description).strip()
    issue_content_sha256 = hashlib.sha256(
        json.dumps(
            {
                "issue_id": issue.issue_id,
                "key": issue.key,
                "project_key": issue.project_key,
                "summary": issue.summary,
                "status": issue.status,
                "issue_type": issue.issue_type,
                "assignee_account_id": issue.assignee,
                "description": issue.description,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert workspace.config_path is not None
    agent_config = read_json(workspace.config_path)
    install_identity = load_install_identity(install_root)
    effective_config = {
        **agent_config,
        "agent_id": install_identity["agent_id"],
        "execution_identity": install_identity["execution_identity"],
    }
    if confirmed_repository is not None and confirmed_worktree is not None:
        pool_root = resolve_source_pool_root(install_root)
        task_worktrees = {
            "issue_key": issue.key,
            "repository": confirmed_repository,
            "problem_version": confirmed_from_branch,
            "target_branch": confirmed_task_branch,
            "baseline_branch": confirmed_from_branch,
            "expected_worktree": str(confirmed_worktree),
            "checked_path": str(confirmed_worktree),
            "pool_root": str(pool_root) if pool_root is not None else "",
            "adopted": 0,
            "created": 1,
        }
        bound_source_root = confirmed_worktree
        bound_repository = confirmed_repository
    else:
        task_worktrees, bound_source_root, bound_repository = _prepare_pool_task_worktrees(
            install_root=install_root,
            profile=context.profile,
            issue=issue,
            agent_config=effective_config,
        )
    issue_payload = {
        "id": issue.issue_id,
        "key": issue.key,
        "project_key": issue.project_key,
        "summary": issue.summary,
        "status": issue.status,
        "mapped_status": mapped_status,
        "issue_type": issue.issue_type,
        "assignee_account_id": issue.assignee,
        "description": description_text,
        "issue_content_sha256": issue_content_sha256,
    }
    if task_worktrees is not None:
        problem_version = str(task_worktrees.get("problem_version") or "")
        target_branch = str(task_worktrees.get("target_branch") or "")
    else:
        problem_version, target_branch = _resolve_non_pool_branch_context(
            context.profile,
            issue,
            bound_source_root,
            bound_repository,
        )
    workspace_defaults = {
        "agent_id": effective_config.get("agent_id"),
        "project_profile": context.profile.profile_id,
        "connection_id": context.connection.connection_id,
        "jira_base_url": context.connection.base_url,
        "jira_account_id": account["account_id"],
        "repository": bound_repository,
        "problem_version": problem_version,
        "target_branch": target_branch,
        "source_root": str(bound_source_root),
        "execution_identity": effective_config.get("execution_identity"),
    }
    intake_source = record_task_start_context(
        workspace,
        store,
        issue_key=issue.key,
        agentic_run_id=agentic_run_id,
        issue=issue_payload,
        workspace_defaults=workspace_defaults,
        project_profile=_profile_snapshot(
            context.profile,
            issue,
            task_worktrees=task_worktrees,
            target_repository=bound_repository,
            problem_version=problem_version,
            target_branch=target_branch,
        ),
    )
    return {
        "issue": issue_payload,
        "workspace_defaults": workspace_defaults,
        "agent_config": effective_config,
        "intake_source": intake_source,
        "task_worktrees": task_worktrees,
    }


def _prepare_pool_task_worktrees(
    *, install_root: Path, profile: Any, issue: Any, agent_config: dict[str, Any]
) -> tuple[dict[str, Any] | None, Path, str]:
    """接管阶段只绑定源码池分析根；用户确认关系前不创建任务工作树。"""
    raw_source_root = str(agent_config.get("source_root") or "")
    if not raw_source_root:
        raise _blocked("task_source_root_missing", "任务缺少业务源码目录", "请重新初始化工作空间后重试")
    source_root = Path(raw_source_root).expanduser().resolve()
    sections = _description_sections(issue.description)
    target_repository = resolve_target_repository(profile, sections)
    domain = profile.domain_for(target_repository)
    if domain is None and (
        profile.worktree_domains
        or "problem_version" in profile.fields
        or "target_branch" in profile.fields
    ):
        raise _blocked(
            "task_domain_unresolved",
            f"无法根据目标仓库判定任务领域：{target_repository}",
            "请补充可映射的目标仓库或任务领域；系统不会创建或绑定未知领域的工作树",
            details={"target_repository": target_repository},
        )
    pool_root = resolve_source_pool_root(install_root)
    if pool_root is None or source_root != pool_root:
        configured_repository = str(
            agent_config.get("repository") or profile.default_repository or ""
        )
        if configured_repository != target_repository:
            raise _blocked(
                "task_source_repository_mismatch",
                f"独立源码目录绑定仓库 {configured_repository}，与任务目标仓库 {target_repository} 不一致",
                "请改用目标仓库对应的独立业务工作空间，或初始化中央源码池后重试",
            )
        return None, source_root, target_repository
    if domain is None:
        raise _blocked(
            "task_domain_unresolved",
            f"无法根据目标仓库判定任务领域：{target_repository}",
            "请补充可映射的目标仓库或任务领域；系统不会创建未知领域的任务工作树",
            details={"target_repository": target_repository},
        )
    return None, source_root, target_repository


def _resolve_non_pool_branch_context(
    profile: Any,
    issue: Any,
    source_root: Path,
    repository: str,
) -> tuple[str, str]:
    """为独立 checkout 解析有效问题版本和 PR 基线，最后回读当前分支。"""
    sections = _description_sections(issue.description)
    domain = profile.domain_for(repository)
    problem_version = ""
    if domain is not None:
        product_alignment = (
            profile.profile_id == "tapdata"
            and domain.baseline_repository == "tapdata/tapdata"
        )
        problem_version = resolve_from_branch(
            profile,
            sections,
            target_repository=domain.baseline_repository,
            allow_alignment_spec=product_alignment,
        )
        if "target_branch" in profile.fields:
            target = (
                resolve_product_alignment_branch(sections, repository)
                if product_alignment
                else ""
            )
            if not target:
                target = profile.derive_branch(
                    repository,
                    problem_version,
                    primary_repository=domain.baseline_repository,
                )
            if target:
                return problem_version, target
    if "target_branch" not in profile.fields:
        return problem_version, ""
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return problem_version, ""
    branch = result.stdout.strip() if result.returncode == 0 else ""
    return problem_version, "" if branch == "HEAD" else branch


def _profile_snapshot(
    profile: Any,
    issue: Any,
    *,
    task_worktrees: dict[str, Any] | None = None,
    target_repository: str = "",
    problem_version: str = "",
    target_branch: str = "",
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    resolved: dict[str, Any] = {}
    sections = _description_sections(issue.description)
    for logical_name, mapping in sorted(profile.fields.items()):
        declaration = {
            "source": mapping.source,
            "jira_field": mapping.jira_field,
            "section": mapping.section,
            "state": mapping.state,
            "writable": mapping.writable,
            "required": mapping.required,
        }
        fields[logical_name] = declaration
        value: Any = None
        reference = ""
        if mapping.source == "workspace_repo_mapping":
            value = (
                task_worktrees.get("repository")
                if task_worktrees is not None
                else target_repository or profile.default_repository
            )
            reference = "workspace_defaults.repository"
        elif mapping.source == "task_worktree_mapping":
            reference = f"task_worktrees.{logical_name}"
            if task_worktrees is not None:
                value = task_worktrees.get(logical_name)
            elif logical_name == "target_branch":
                reference = "workspace_defaults.target_branch"
                value = target_branch
        elif mapping.source == "jira_field" and mapping.jira_field:
            reference = f"issue.fields.{mapping.jira_field}"
            if mapping.jira_field == "assignee":
                value = issue.assignee
            elif mapping.jira_field == "summary":
                value = issue.summary
            else:
                value = _plain_field_value(issue.fields.get(mapping.jira_field))
        elif mapping.source == "jira_description_section" and mapping.section:
            reference = f"issue.description_sections.{mapping.section}"
            value = sections.get(mapping.section)
            if logical_name == "problem_version" and task_worktrees is not None:
                reference = "task_worktrees.problem_version"
                value = task_worktrees.get("problem_version")
            elif logical_name == "problem_version" and problem_version:
                reference = "workspace_defaults.problem_version"
                value = problem_version
        resolved[logical_name] = {
            **declaration,
            "reference": reference,
            "value": value,
        }
    return {
        "profile_id": profile.profile_id,
        "connection_id": profile.connection_id,
        "project_key": profile.project_key,
        "issue_types": list(profile.issue_types),
        "default_repository": profile.default_repository,
        "status_mapping": dict(sorted(profile.status_mapping.items())),
        "fields": fields,
        "resolved_fields": resolved,
    }


def _description_sections(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("type") != "doc":
        return {}
    content = value.get("content")
    if not isinstance(content, list):
        return {}
    result: dict[str, list[str]] = {}
    current = ""
    for node in content:
        if not isinstance(node, dict):
            continue
        text = plain_text(node).strip()
        if node.get("type") == "heading":
            current = text
            if current:
                result.setdefault(current, [])
            continue
        if current and text:
            result[current].append(text)
    return {
        title: "\n".join(lines).strip()
        for title, lines in result.items()
        if "\n".join(lines).strip()
    }


def _plain_field_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_plain_field_value(item) for item in value]
    if isinstance(value, dict):
        if value.get("type") == "doc":
            return plain_text(value).strip()
        for key in ("accountId", "value", "name", "key"):
            candidate = value.get(key)
            if isinstance(candidate, (str, int, float, bool)):
                return candidate
        return {
            str(key): _plain_field_value(item)
            for key, item in sorted(value.items())
            if isinstance(key, str)
        }
    return str(value)


def _existing_task(store: TaskStore, issue_key: str) -> dict[str, Any] | None:
    try:
        return dict(store.inspect(issue_key)["task"])
    except RuntimeErrorResult as error:
        if error.code == "task_state_not_found":
            return None
        raise


def _validate_existing_task(
    task: dict[str, Any], connection_id: str, issue: Any
) -> None:
    expected = {
        "connection_id": connection_id,
        "jira_issue_id": issue.issue_id,
        "issue_key": issue.key,
        "project_key": issue.project_key,
    }
    if any(task.get(key) != value for key, value in expected.items()):
        raise _blocked(
            "task_identity_mismatch",
            "现有本地任务状态与当前 Jira 卡片身份不一致",
            "请停止执行并核对工作空间、Jira 卡片和本地任务状态",
        )


def _new_run_id(issue_key: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{issue_key}-{timestamp}-{secrets.token_hex(4)}"


def _blocked(
    code: str,
    message: str,
    action: str,
    *,
    details: dict[str, Any] | None = None,
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
        details=details or {},
    )
