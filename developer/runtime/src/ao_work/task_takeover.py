from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ao_work.config import (
    load_jira_context,
    validate_workspace_jira_binding,
)
from ao_work.jira.client import JiraClient, UrllibJiraTransport, with_forced_order
from ao_work.jira.service import JiraService
from ao_work.jira.transition import match_transition
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult, write_diagnostic
from ao_work.task_start import record_current_task_source_context
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.workspace import Workspace


def _blocked(code: str, message: str, action: str, **details: Any) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action=action,
        **details,
    )


def execute_task_takeover(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str | None,
    *,
    agent_id: str | None,
    authorization_reference: str,
    transition_comment: str | None = None,
) -> dict[str, Any]:
    """正式任务接管：校验负责人 → 留下接管评论 → transition → 本地接管记录。

    - 无 issue_key：只读列出可接管候选（profile.task_query + 优先级排序），
      不写 Jira、不 transition；返回 selection_required 供用户确认后
      带 key 重跑。agent-id 缺省从安装身份读取。
    - 校验：Jira 当前账户 == 任务经办人；Jira 状态可映射到项目流程 entry stage。
    - 自动判断：新接管、接纳已在执行状态的存量任务，或恢复已有本地运行；后两者在评论中明文提示。
    - Jira 写：先写并回读结构化接管评论；若状态未在 entry stage，再执行映射后的 transition。
    - 本地：生成或复用 agentic_run_id，写入 takeover 记录。
    - 回读：重新读取 issue，确认状态已按预期推进。Agentic 自定义字段不参与 developer 接管。
    """
    context = load_jira_context(workspace, install_root)
    email, token = context.require_credentials()
    client = JiraClient(
        context.profile,
        UrllibJiraTransport(context.connection, email, token),
    )
    service = JiraService(context.profile, client)
    account = client.current_user_details()
    validate_workspace_jira_binding(
        workspace,
        context.connection,
        account_id=account["account_id"],
    )

    if not issue_key:
        return _takeover_candidates(context, client, account["account_id"])

    if not authorization_reference:
        raise _blocked(
            "authorization_reference_required",
            "正式接管必须提供授权引用（--authorization-reference）",
            "请先确认目标任务并取得研发工程师授权后，以 user-confirmation:<KEY>:<plan_id> 重试",
        )

    if not agent_id:
        agent_id = _default_agent_id(install_root)
    issue = service.inspect_issue(issue_key)
    if issue.assignee != account["account_id"]:
        raise _blocked(
            "owner_mismatch",
            "当前工作空间 Jira 账户不是任务经办人，无法接管",
            "请在 Jira 按项目流程调整经办人，或切换到正确研发员工作空间",
        )

    mapped_status = context.profile.status_mapping.get(issue.status)
    if not mapped_status:
        raise _blocked(
            "jira_status_mapping_missing",
            f"Project Profile 未映射 Jira 状态：{issue.status}",
            "请先在项目 Profile 中确认状态映射，不要让 AI 临场猜测",
        )
    target_status = _entry_status(context.profile)
    if not target_status:
        raise _blocked(
            "standard_process_mapping_gap",
            "Project Profile 无法推导 entry stage 的目标状态",
            "请在 Project Profile 中确认状态映射包含 implementation 等执行阶段",
        )

    transitions: list[dict[str, str]] = []
    matched_transition: tuple[str, str] | None = None
    if issue.status != target_status:
        transitions = client.available_transitions(issue_key)
        target_key = _transition_key_for(context.profile, issue.status)
        if target_key:
            matched_transition = match_transition(
                issue.status,
                transitions,
                {"transitions": context.profile.transition_mapping},
                target_key=target_key,
            )
        if matched_transition is None:
            raise _blocked(
                "jira_transition_mapping_gap",
                f"Jira 可用 transition 中没有目标 transition {target_key or target_status}",
                "请人工在 Jira 执行状态流转，或核对 Project Profile 状态映射",
            )

    existing_state = _existing_state(store, issue.key)
    if existing_state is None:
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
        progress_stage = "initialized"
    else:
        task = existing_state["task"]
        progress = existing_state["progress"]
        agentic_run_id = str(task["agentic_run_id"])
        task_state_created = False
        progress_stage = str(progress.get("stage") or "")

    takeover_kind = _takeover_kind(
        issue_status=issue.status,
        target_status=target_status,
        progress_stage=progress_stage,
    )
    human_notice = _takeover_human_notice(takeover_kind)
    agentic_takeover_at = datetime.now(timezone.utc).isoformat()
    comment_marker = _takeover_comment_marker(
        issue.key,
        agentic_run_id,
        takeover_kind,
        authorization_reference,
    )
    comment_body = _takeover_comment(
        issue_key=issue.key,
        agentic_run_id=agentic_run_id,
        agent_id=agent_id,
        workspace_name=workspace.root.name,
        takeover_kind=takeover_kind,
        takeover_at=agentic_takeover_at,
        current_status=issue.status,
        target_status=target_status,
        marker=comment_marker,
        extra=transition_comment,
    )
    takeover_comment_id = _ensure_takeover_comment(
        client,
        issue.key,
        comment_marker,
        comment_body,
        expected_author=account["account_id"],
    )

    if matched_transition is not None:
        client.execute_transition(issue_key, matched_transition[0])

    readback = service.inspect_issue(issue_key)
    status_verified = readback.status == target_status
    if not status_verified:
        raise _blocked(
            "jira_takeover_readback_mismatch",
            "接管后 Jira 状态回读与目标状态不一致",
            "请人工核对 Jira 状态流转结果",
        )
    mapped_readback_status = context.profile.status_mapping.get(readback.status)
    if not mapped_readback_status:
        raise _blocked(
            "jira_status_mapping_missing",
            f"Project Profile 未映射接管后 Jira 状态：{readback.status}",
            "请核对 Project Profile 状态映射；来源快照未建立前不得继续分析",
        )
    source_context = record_current_task_source_context(
        workspace,
        store,
        context=context,
        account=account,
        issue=readback,
        agentic_run_id=agentic_run_id,
        mapped_status=mapped_readback_status,
    )
    store.record_gate_transition(
        issue.key,
        agentic_run_id,
        stage="takeover_started",
        next_action="assess_task_intake",
        operation="takeover_task",
        status="completed",
        evidence={
            "agent_id": agent_id,
            "jira_status_before": issue.status,
            "jira_status_after": readback.status,
            "transition_used": bool(transitions),
            "takeover_kind": takeover_kind,
            "takeover_comment_id": takeover_comment_id,
            "takeover_comment_marker": comment_marker,
            "agentic_takeover_at": agentic_takeover_at,
            "authorization_reference": authorization_reference,
        },
    )
    authorization_digest = hashlib.sha256(
        authorization_reference.encode("utf-8")
    ).hexdigest()
    preflight_facts_sha256 = hashlib.sha256(
        json.dumps(
            {
                "issue_key": issue.key,
                "jira_issue_id": issue.issue_id,
                "assignee": issue.assignee,
                "jira_status_before": issue.status,
                "jira_status_target": target_status,
                "transition_id": matched_transition[0] if matched_transition else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    takeover_recovery = store.migrate_legacy_takeover(
        issue.key,
        agentic_run_id,
        {
            "agent_id": agent_id,
            "takeover_kind": takeover_kind,
            "authorization_digest": authorization_digest,
            "preflight_facts_sha256": preflight_facts_sha256,
            "jira_status_before": issue.status,
            "jira_status_target": target_status,
            "jira_status_after": readback.status,
            "transition_id": matched_transition[0] if matched_transition else None,
            "comment_marker": comment_marker,
            "comment_content_sha256": hashlib.sha256(
                comment_body.encode("utf-8")
            ).hexdigest(),
            "comment_id": takeover_comment_id,
            "comment_author": account["account_id"],
            "expected_comment_author": account["account_id"],
            "assignee": readback.assignee,
            "expected_assignee": account["account_id"],
        },
    )
    takeover_operation = takeover_recovery["operation"]
    return {
        "workspace": str(workspace.root),
        "issue_key": issue.key,
        "agentic_run_id": agentic_run_id,
        "agent_id": agent_id,
        "task_state_created": task_state_created,
        "jira_status_before": issue.status,
        "jira_status_after": readback.status,
        "transition_applied": bool(transitions),
        "takeover_status": "completed",
        "takeover_kind": takeover_kind,
        "human_notice": human_notice,
        "takeover_comment_id": takeover_comment_id,
        "takeover_comment_verified": True,
        "takeover_phase": takeover_operation["phase"],
        "takeover_result": takeover_operation["result"],
        "external_result_certainty": takeover_operation[
            "external_result_certainty"
        ],
        "retry_safe": takeover_operation["retry_safe"],
        "recovery_action": takeover_operation["recovery_action"],
        "agentic_takeover_at": agentic_takeover_at,
        "current_stage": "takeover_started",
        "intake_source": source_context["intake_source"],
        "agentic_next_action": {
            "executor": "ai",
            "action": "assess_task_intake",
            "required_inputs": ["issue_key", "agentic_run_id", "intake_input_file"],
            "allowed_operations": ["task_intake_assess"],
            "requires_authorization": False,
            "stop_workflow": False,
            "ownership_effect": "none",
            "reason": "接管与来源快照均已回读，继续执行信息分析和证据化补全",
        },
    }


def _transition_key_for(profile: Any, current_status: str) -> str | None:
    """从 Project Profile transition_mapping 推导当前状态 → implementation 的 transition key。

    transition_mapping 形如 {transition_key: {"name": ..., "id": ..., "from": [...], "to": ...}}。
    优先取 from 包含当前状态的条目 key；否则回退到 key 为 start_progress / start / begin 的条目。
    key 交给共享 D-037 匹配器（match_transition）做严格匹配。
    """
    raw = getattr(profile, "transition_mapping", None) or {}
    candidates: list[tuple[str, dict[str, Any]]] = []
    for key, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        from_states = spec.get("from")
        candidates.append((key, {"name": str(spec.get("name") or "").strip(), "from": from_states}))
    for key, spec in candidates:
        from_states = spec["from"]
        if isinstance(from_states, (list, tuple)) and current_status in from_states:
            return key
    for key, spec in candidates:
        if key in ("start_progress", "start", "begin"):
            return key
    return None


def _entry_status(profile: Any) -> str:
    """entry stage 的目标 Jira 状态：取映射中第一个非 completed 的 stage 对应状态。"""
    inverted: dict[str, str] = {}
    for status, stage in profile.status_mapping.items():
        inverted.setdefault(stage, status)
    for stage in ("implementation", "in_progress", "started"):
        if stage in inverted:
            return inverted[stage]
    return inverted.get("entry", "")


def _existing_state(store: TaskStore, issue_key: str) -> dict[str, Any] | None:
    try:
        state = store.inspect(issue_key)
    except RuntimeErrorResult as error:
        if error.code == "task_state_not_found":
            # 任务尚未初始化：可预期降级，留痕一次（首次完整信息）。
            write_diagnostic(f"任务 {issue_key} 尚无本地状态，将新建接管记录")
            return None
        raise
    if not isinstance(state, dict):
        return None
    task = state.get("task")
    progress = state.get("progress")
    if not isinstance(task, dict) or not isinstance(progress, dict):
        return None
    return {
        "task": task,
        "progress": progress,
        "takeover_recovery": state.get("takeover_recovery"),
    }


def _takeover_kind(
    *,
    issue_status: str,
    target_status: str,
    progress_stage: str,
) -> str:
    if progress_stage in {"takeover_started", "blocked"}:
        return "resume_takeover"
    if issue_status == target_status:
        return "accept_existing_task"
    return "new_takeover"


def _takeover_human_notice(takeover_kind: str) -> str:
    notices = {
        "new_takeover": "已完成新接管。",
        "accept_existing_task": "已接纳存量任务；这不是新接管。",
        "resume_takeover": "已恢复当前工作空间的既有运行；这不是新接管。",
    }
    return notices[takeover_kind]


def _takeover_comment_marker(
    issue_key: str,
    agentic_run_id: str,
    takeover_kind: str,
    authorization_reference: str,
) -> str:
    authorization_digest = hashlib.sha256(
        authorization_reference.encode("utf-8")
    ).hexdigest()[:16]
    return (
        f"[agentic-ops-takeover:{issue_key}:{agentic_run_id}:"
        f"{takeover_kind}:{authorization_digest}]"
    )


def _takeover_comment(
    *,
    issue_key: str,
    agentic_run_id: str,
    agent_id: str,
    workspace_name: str,
    takeover_kind: str,
    takeover_at: str,
    current_status: str,
    target_status: str,
    marker: str,
    extra: str | None,
) -> str:
    action_names = {
        "new_takeover": "新接管",
        "accept_existing_task": "接纳存量任务（不是新接管）",
        "resume_takeover": "恢复既有运行（不是新接管）",
    }
    lines = [
        "## AgenticOps 任务接管",
        "",
        f"- 事项: `{issue_key}`",
        f"- 操作类型: {action_names[takeover_kind]}",
        f"- 运行 ID: `{agentic_run_id}`",
        f"- AIAgent: `{agent_id}`",
        f"- 工作空间: `{workspace_name}`",
        f"- 操作时间: `{takeover_at}`",
        f"- Jira 状态: `{current_status}` → `{target_status}`",
        "- 当前阶段: `takeover_started`",
        "- 下一步动作: `assess_task_intake`",
    ]
    if extra and extra.strip():
        lines.extend(["- 补充说明:", extra.strip()])
    lines.extend(["", marker])
    return "\n".join(lines)


def _ensure_takeover_comment(
    client: JiraClient,
    issue_key: str,
    marker: str,
    markdown: str,
    *,
    expected_author: str,
) -> str:
    existing = next(
        (
            comment
            for comment in client.comments(issue_key)
            if marker in comment.body and comment.author == expected_author
        ),
        None,
    )
    if existing is not None and existing.comment_id:
        return existing.comment_id
    comment_id = client.add_comment(issue_key, markdown)
    if not comment_id:
        raise _blocked(
            "jira_takeover_comment_write_failed",
            "Jira 接管评论写入后未返回评论 ID",
            "请回读 Jira 评论，确认是否已写入；结果不明确时不要重复接管",
        )
    readback = client.comment(issue_key, comment_id)
    if (
        readback.comment_id != comment_id
        or readback.author != expected_author
        or marker not in readback.body
    ):
        raise _blocked(
            "jira_takeover_comment_readback_mismatch",
            "Jira 接管评论回读与写入内容不一致",
            "请人工核对 Jira 评论；确认留痕前不得继续状态流转",
        )
    return comment_id


def _default_agent_id(install_root: Path) -> str:
    """从安装目录身份读取 agent_id（D-048 阶段二）。缺失时阻断。"""
    from ao_work.installation import load_install_identity

    try:
        identity = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        raise _blocked(
            "agent_identity_missing",
            "未提供 --agent-id 且安装目录缺少研发员身份，无法确定接管身份",
            "请运行 ao-work install identity set 配置 agent_id，或显式传 --agent-id",
        ) from error
    agent_id = str(identity.get("agent_id") or "").strip()
    if not agent_id:
        raise _blocked(
            "agent_identity_missing",
            "安装目录研发员身份缺少 agent_id，无法确定接管身份",
            "请运行 ao-work install identity set 重新配置 agent_id",
        )
    return agent_id


_PRIORITY_WEIGHTS: dict[str, int] = {
    "Highest": 5,
    "High": 4,
    "Medium": 3,
    "Low": 2,
    "Lowest": 1,
}


def _takeover_candidates(context: Any, client: JiraClient, account_id: str) -> dict[str, Any]:
    """无 issue_key 时的只读候选列表：profile.task_query + 优先级排序。

    不写 Jira、不 transition、不改字段。排序：Jira 标准优先级权重降序，
    同优先级按 updated 倒序（D6：profile.task_priority 配置留待渐进补充，
    本期用内置 Jira 标准序）。返回 selection_required 供用户确认后带 key 重跑。
    """
    base = (context.profile.task_query or "").strip() or (
        "assignee = currentUser() AND resolution = Unresolved"
    )
    jql = with_forced_order(base, "priority DESC, updated DESC")
    result = client.search_jql(jql, max_results=50)
    tasks = [
        {
            "issue_key": issue.key,
            "summary": issue.summary,
            "status": issue.status,
            "issue_type": issue.issue_type,
            "priority": issue.priority,
            "updated": issue.updated,
        }
        for issue in result.issues
    ]
    tasks.sort(
        key=lambda task: (
            _PRIORITY_WEIGHTS.get(str(task["priority"]), 0),
            str(task["updated"]),
        ),
        reverse=True,
    )
    return {
        "takeover_status": "selection_required",
        "selection_required": True,
        "candidate_count": len(tasks),
        "candidates": tasks,
        "credential_status": context.credential_status(),
        "note": "未提供 issue_key；请从候选列表确认目标任务后，带 issue_key 与授权引用重新执行 takeover",
        "agentic_next_action": {
            "executor": "human",
            "action": "select_takeover_candidate",
            "required_inputs": ["issue_key"],
            "allowed_operations": ["takeover_task"],
            "requires_authorization": True,
            "stop_workflow": True,
            "ownership_effect": "none",
            "reason": "未指定任务编号；请从候选列表选择一个任务",
        },
    }


def _new_run_id(issue_key: str) -> str:
    return f"run-{issue_key}-{secrets.token_hex(3)}"
