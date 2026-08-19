from __future__ import annotations

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
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
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


def _find_agentic_id_field(metadata: list[dict[str, Any]]) -> str | None:
    """按常见字段名探测 agentic_id 自定义字段 ID。

    优先精确匹配 agentic id / agentic_id；再匹配包含 agentic 的字段名。
    找不到返回 None（调用方按未配置处理，不阻断本地接管）。
    """
    for item in metadata:
        name = str(item.get("name", "")).strip().lower()
        field_id = str(item.get("id", "")).strip()
        if not name or not field_id:
            continue
        normalized = name.replace("-", "").replace("_", "").replace(" ", "")
        if normalized in {"agenticid", "agenticidfield", "aiagentid", "agentid"}:
            return field_id
    for item in metadata:
        name = str(item.get("name", "")).strip().lower()
        field_id = str(item.get("id", "")).strip()
        if field_id and "agentic" in name:
            return field_id
    return None


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
    """正式任务接管：校验所有权 → transition 到执行状态 → agentic_id 字段写入 → 本地接管记录。

    - 无 issue_key：只读列出可接管候选（profile.task_query + 优先级排序），
      不写 Jira、不 transition、不改字段；返回 selection_required 供用户确认后
      带 key 重跑。agent-id 缺省从安装身份读取。
    - 校验：Jira 当前账户 == 任务经办人；agentic_id 字段为空或与当前 agent_id 一致；
      Jira 状态可映射到项目流程 entry stage。
    - Jira 写：若状态未在 entry stage，执行 transition（id 从可用 transition 列表按目标状态名匹配）；
      agentic_id 字段写入 agent_id（字段 ID 从元数据探测，未配置时跳过并记录）。
    - 本地：写入 takeover 记录（agentic_run_id 由 task_state 初始化生成）。
    - 回读：重新读取 issue，确认状态与 agentic_id 已按预期写入。
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

    agentic_field_id = _find_agentic_id_field(client.field_metadata())
    existing_agentic_id = ""
    if agentic_field_id:
        existing_agentic_id = str(
            issue.fields.get(agentic_field_id) or ""
        ).strip()
    if existing_agentic_id and existing_agentic_id != agent_id:
        raise _blocked(
            "agent_ownership_conflict",
            f"Jira 任务已绑定其它 AIAgent 身份：{existing_agentic_id}",
            "请人工核对任务所有权；不得覆盖其它 AIAgent 的接管",
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
    if issue.status != target_status:
        transitions = client.available_transitions(issue_key)
        target_key = _transition_key_for(context.profile, issue.status)
        match = None
        if target_key:
            match = match_transition(
                issue.status,
                transitions,
                {"transitions": context.profile.transition_mapping},
                target_key=target_key,
            )
        if match is None:
            raise _blocked(
                "jira_transition_mapping_gap",
                f"Jira 可用 transition 中没有目标 transition {target_key or target_status}",
                "请人工在 Jira 执行状态流转，或核对 Project Profile 状态映射",
            )
        client.execute_transition(
            issue_key,
            match[0],
            comment=transition_comment,
        )

    agentic_written = False
    if agentic_field_id:
        client.update_issue_fields(issue_key, {agentic_field_id: agent_id})
        agentic_written = True

    readback = service.inspect_issue(issue_key)
    status_verified = readback.status == target_status
    if not status_verified:
        raise _blocked(
            "jira_takeover_readback_mismatch",
            "接管后 Jira 状态回读与目标状态不一致",
            "请人工核对 Jira 状态流转结果",
        )
    if agentic_written:
        readback_agentic_id = str(
            readback.fields.get(agentic_field_id, "") or ""
        ).strip()
        if readback_agentic_id != agent_id:
            raise _blocked(
                "jira_takeover_readback_mismatch",
                "接管后 agentic_id 字段回读与写入值不一致",
                "请人工核对 Jira 字段写入结果",
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
        agentic_run_id = str(existing["agentic_run_id"])
        task_state_created = False
    store.record_gate_transition(
        issue.key,
        agentic_run_id,
        stage="takeover_started",
        next_action="run_development",
        operation="takeover_task",
        status="completed",
        evidence={
            "agent_id": agent_id,
            "jira_status_before": issue.status,
            "jira_status_after": readback.status,
            "transition_used": bool(transitions),
            "agentic_field_id": agentic_field_id,
            "agentic_id_written": agentic_written,
            "authorization_reference": authorization_reference,
        },
    )
    return {
        "workspace": str(workspace.root),
        "issue_key": issue.key,
        "agentic_run_id": agentic_run_id,
        "agent_id": agent_id,
        "task_state_created": task_state_created,
        "jira_status_before": issue.status,
        "jira_status_after": readback.status,
        "transition_applied": bool(transitions),
        "agentic_field_id": agentic_field_id,
        "agentic_id_written": agentic_written,
        "current_stage": "takeover_started",
        "agentic_next_action": "run_development",
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


def _existing_task(store: TaskStore, issue_key: str) -> dict[str, Any] | None:
    try:
        state = store.inspect(issue_key)
    except Exception:
        return None
    return state if isinstance(state, dict) else None


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
        "selection_required": True,
        "candidate_count": len(tasks),
        "candidates": tasks,
        "credential_status": context.credential_status(),
        "note": "未提供 issue_key；请从候选列表确认目标任务后，带 issue_key 与授权引用重新执行 takeover",
    }


def _new_run_id(issue_key: str) -> str:
    return f"run-{issue_key}-{secrets.token_hex(3)}"
