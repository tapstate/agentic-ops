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
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.service import JiraService
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
    issue_key: str,
    *,
    agent_id: str,
    authorization_reference: str,
    transition_comment: str | None = None,
) -> dict[str, Any]:
    """正式任务接管：校验所有权 → transition 到执行状态 → agentic_id 字段写入 → 本地接管记录。

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
        target_name = _transition_name_for(context.profile, issue.status)
        match = None
        if target_name:
            match = next(
                (item for item in transitions if item["name"] == target_name),
                None,
            )
        if match is None:
            raise _blocked(
                "jira_transition_mapping_gap",
                f"Jira 可用 transition 中没有目标 transition {target_name or target_status}",
                "请人工在 Jira 执行状态流转，或核对 Project Profile 状态映射",
            )
        client.execute_transition(
            issue_key,
            match["id"],
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


def _transition_name_for(profile: Any, current_status: str) -> str | None:
    """从 Project Profile transition_mapping 推导当前状态 → implementation 的 transition 名。

    transition_mapping 形如 {transition_key: {"name": "Start Progress", "from": [...]}}。
    取第一个 from 包含当前状态且目标为 implementation 的 transition；否则回退到
    key 为 start_progress / start 的条目。
    """
    raw = getattr(profile, "transition_mapping", None) or {}
    candidates: list[tuple[str, dict[str, str]]] = []
    for key, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        from_states = spec.get("from")
        candidates.append((key, {"name": name, "from": from_states}))
    for key, spec in candidates:
        from_states = spec["from"]
        if isinstance(from_states, (list, tuple)) and current_status in from_states:
            return spec["name"]
    for key, spec in candidates:
        if key in ("start_progress", "start", "begin"):
            return spec["name"]
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


def _new_run_id(issue_key: str) -> str:
    return f"run-{issue_key}-{secrets.token_hex(3)}"
