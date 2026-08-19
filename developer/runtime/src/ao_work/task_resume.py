from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ao_work.config import (
    load_jira_context,
    validate_workspace_jira_binding,
)
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.service import JiraService
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state import TaskStore
from ao_work.workspace import Workspace

RESUMABLE_STAGES = frozenset({"takeover_started", "blocked"})


def _blocked(code: str, message: str, action: str, **details: Any) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action=action,
        **details,
    )


def execute_task_resume(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    *,
    issue_key: str | None = None,
    agentic_run_id: str | None = None,
) -> dict[str, Any]:
    """恢复一个已接管的任务执行上下文（只读，不写 Jira）。

    按 resume-takeover.yaml 契约：
    - 定位：显式 --issue-key / --agentic-run-id，或本地最近可恢复记录（stage ∈
      takeover_started / blocked，按 updated_at 倒序取最新）。
    - 校验链：本地 run 存在且 workspace 匹配 → Jira 回读（issue 存在、
      assignee == currentUser、agentic_id 与 agent_id 一致、状态可映射）→
      本地 stage ∈ RESUMABLE_STAGES → 输出执行上下文。
    - side_effects：不写 Jira、不创建 PR；只允许本地事件/反馈物（本实现只读）。
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

    local = _resolve_local_context(store, issue_key=issue_key, agentic_run_id=agentic_run_id)
    task = local["task"]
    progress = local["progress"]

    issue = service.inspect_issue(task["issue_key"])
    if issue.assignee != account["account_id"]:
        raise _blocked(
            "assignee_changed",
            "任务经办人已变更，当前工作空间 Jira 账户不再是经办人",
            "请人工核对 Jira 所有权；恢复接管前必须先确认经办人归属",
        )

    agentic_field_id = _agentic_field_id(client)
    existing_agentic_id = ""
    if agentic_field_id:
        existing_agentic_id = str(issue.fields.get(agentic_field_id) or "").strip()
    agent_id = str(task.get("agent_id") or "").strip()
    if existing_agentic_id and agent_id and existing_agentic_id != agent_id:
        raise _blocked(
            "agent_ownership_conflict",
            f"Jira 任务已绑定其它 AIAgent 身份：{existing_agentic_id}",
            "请人工核对任务所有权；不得恢复其它 AIAgent 接管的执行上下文",
        )

    mapped_status = context.profile.status_mapping.get(issue.status)
    if not mapped_status:
        raise _blocked(
            "jira_status_mapping_missing",
            f"Project Profile 未映射 Jira 状态：{issue.status}",
            "请先在项目 Profile 中确认状态映射，不要让 AI 临场猜测",
        )

    current_stage = str(progress.get("stage") or "").strip()
    if current_stage not in RESUMABLE_STAGES:
        raise _blocked(
            "resume_stage_not_allowed",
            f"任务当前阶段 {current_stage or '<empty>'} 不允许恢复接管",
            "请核对本地任务状态；仅 takeover_started / blocked 阶段可恢复",
        )

    agentic_run_id = str(task["agentic_run_id"])
    return {
        "workspace": str(workspace.root),
        "issue_key": task["issue_key"],
        "agentic_run_id": agentic_run_id,
        "agent_id": agent_id or "",
        "task_class": _task_class(task),
        "project_key": str(task.get("project_key") or ""),
        "jira_status": issue.status,
        "jira_status_stage": mapped_status,
        "previous_stage": current_stage,
        "current_stage": current_stage,
        "agentic_next_action": str(progress.get("agentic_next_action") or ""),
        "credential_status": context.credential_status(),
    }


def _resolve_local_context(
    store: TaskStore,
    *,
    issue_key: str | None,
    agentic_run_id: str | None,
) -> dict[str, Any]:
    """定位本地任务上下文：显式 key / run id，或最近可恢复记录。"""
    if issue_key:
        state = store.inspect(issue_key)
        task = dict(state["task"])
        if not str(task.get("agent_id") or "").strip():
            task_dir = Path(state["task_dir"])
            agent_id = _agent_id_from_journal(task_dir)
            if agent_id:
                task["agent_id"] = agent_id
                state = {**state, "task": task}
        return state
    if agentic_run_id:
        state = _find_by_run_id(store, agentic_run_id)
        if state is None:
            raise _blocked(
                "run_not_found",
                f"本地任务状态中不存在 agentic_run_id={agentic_run_id}",
                "请核对运行编号，或改用 --issue-key 指定任务",
            )
        return state
    state = _latest_resumable(store)
    if state is None:
        raise _blocked(
            "run_not_found",
            "本地没有可恢复的已接管任务（stage ∈ takeover_started/blocked）",
            "请先用 ao-work task takeover 接管任务，或用 --issue-key 指定任务",
        )
    return state


def _find_by_run_id(store: TaskStore, agentic_run_id: str) -> dict[str, Any] | None:
    for candidate in _iter_task_states(store):
        task = candidate["task"]
        if str(task.get("agentic_run_id") or "") == agentic_run_id:
            return candidate
    return None


def _latest_resumable(store: TaskStore) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for state in _iter_task_states(store):
        stage = str(state["progress"].get("stage") or "").strip()
        if stage in RESUMABLE_STAGES:
            candidates.append(state)
    if not candidates:
        return None
    candidates.sort(
        key=lambda state: str(
            state["progress"].get("updated_at") or state["task"].get("updated_at") or ""
        ),
        reverse=True,
    )
    return candidates[0]


def _iter_task_states(store: TaskStore) -> list[dict[str, Any]]:
    """遍历本地任务目录，返回 task/progress 快照（跳过损坏项）。

    agent_id 不在 task.json 中（takeover 时写入 journal 事件的 evidence），
    从 journal.ndjson 最近 takeover_task 事件回读，供所有权校验使用。
    """
    state_root = store.state_root
    tasks_root = state_root / "tasks"
    if not tasks_root.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir():
            continue
        task_path = task_dir / "task.json"
        progress_path = task_dir / "progress.json"
        if not task_path.is_file() or not progress_path.is_file():
            continue
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(task, dict) and isinstance(progress, dict):
            agent_id = _agent_id_from_journal(task_dir)
            if agent_id:
                task = {**task, "agent_id": agent_id}
            results.append({"task": task, "progress": progress})
    return results


def _agent_id_from_journal(task_dir: Path) -> str | None:
    """从 journal.ndjson 最近 takeover_task 事件的 evidence 读取 agent_id。"""
    journal_path = task_dir / "journal.ndjson"
    if not journal_path.is_file():
        return None
    try:
        lines = journal_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    agent_id: str | None = None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if str(event.get("operation") or "") != "takeover_task":
            continue
        evidence = event.get("evidence")
        if isinstance(evidence, dict):
            candidate = str(evidence.get("agent_id") or "").strip()
            if candidate:
                agent_id = candidate
        break
    return agent_id


def _agentic_field_id(client: JiraClient) -> str | None:
    try:
        metadata = client.field_metadata()
    except Exception:
        return None
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


def _task_class(task: dict[str, Any]) -> str:
    """任务分类：issue_type 优先，缺省由本地状态推导。"""
    for key in ("issue_type", "task_type", "kind"):
        value = str(task.get(key) or "").strip()
        if value:
            return value
    return str(task.get("state") or "initialized")
