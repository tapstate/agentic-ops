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
from ao_work.jira.client import (
    JiraClient,
    JiraTransportError,
    UrllibJiraTransport,
    with_forced_order,
)
from ao_work.jira.model import JiraComment, JiraIssue
from ao_work.jira.service import JiraService
from ao_work.jira.transition import match_transition
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult, write_diagnostic
from ao_work.task_start import record_current_task_source_context
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.task_state.takeover import (
    normalized_comment_content_sha256,
    phase_index,
)
from ao_work.workspace import Workspace


def _blocked(code: str, message: str, action: str, **details: Any) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action=action,
        details=details,
    )


def _validate_takeover_assignee(issue: JiraIssue, current_account_id: str) -> None:
    if not issue.assignee:
        raise _blocked(
            "assignee_unassigned",
            "Jira 任务未设置经办人，无法接管",
            "请先按 Jira 项目流程设置经办人，再由该研发员工作空间接管",
            issue_key=issue.key,
            current_account_id=current_account_id,
            identity_source="Jira /myself",
        )
    if issue.assignee != current_account_id:
        raise _blocked(
            "owner_mismatch",
            "当前工作空间 Jira 账户不是任务经办人，无法接管",
            "请在 Jira 按项目流程调整经办人，或切换到正确研发员工作空间",
            issue_key=issue.key,
            current_account_id=current_account_id,
            assignee_account_id=issue.assignee,
            identity_source="Jira /myself",
        )


def execute_task_takeover(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str | None,
) -> dict[str, Any]:
    """正式任务接管：稳定意图 → Comment 回读 → Status 回读 → 本地收口。

    - 无 issue_key：只读列出可接管候选（profile.task_query + 优先级排序），
      不写 Jira、不 transition；返回 selection_required 供用户确认后
      带 key 重跑。agent-id 缺省从安装身份读取。
    - 校验：Jira 当前账户 == 任务经办人；Jira 状态可映射到项目流程 entry stage。
    - 自动判断：新接管、接纳已在执行状态的存量任务，或恢复已有本地运行；后两者在评论中明文提示。
    - Jira 写：严格按 AO-50 phase 驱动，响应不明时先回读，不盲目重试。
    - 本地：第一次外部写入前持久化稳定意图；恢复复用原 run、分类和 Comment。
    - 回读：Comment、Status 和本地交叉状态证据齐全后才返回成功。
    """
    context = load_jira_context(workspace, install_root)
    if issue_key and not issue_key.startswith(f"{context.profile.project_key}-"):
        raise _blocked(
            "jira_workspace_mismatch",
            (
                f"Issue {issue_key} 不属于当前 developer 工作空间绑定的 "
                f"{context.profile.project_key} 项目"
            ),
            "请切换到对应业务项目工作空间；AO 项目任务只能使用 ao-maint",
        )
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
        install_root=install_root,
    )

    if not issue_key:
        return _takeover_candidates(context, client, account["account_id"])

    agent_id = _default_agent_id(install_root)
    issue = service.inspect_issue(issue_key)
    _validate_takeover_assignee(issue, account["account_id"])

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
        _require_existing_identity(
            task,
            connection_id=context.connection.connection_id,
            issue=issue,
        )
        agentic_run_id = str(task["agentic_run_id"])
        task_state_created = False
        progress_stage = str(progress.get("stage") or "")

    authorization_reference = _takeover_instruction_reference(
        issue.key,
        agentic_run_id,
    )

    authorization_digest = hashlib.sha256(
        authorization_reference.encode("utf-8")
    ).hexdigest()
    recovery = store.read_takeover_recovery(issue.key)
    operation = recovery.get("operation")
    if isinstance(operation, dict):
        _require_persisted_request(
            operation,
            issue_key=issue.key,
            agentic_run_id=agentic_run_id,
            agent_id=agent_id,
            authorization_digest=authorization_digest,
        )
        return _run_takeover_saga(
            workspace,
            store,
            install_root=install_root,
            context=context,
            client=client,
            service=service,
            account=account,
            operation=operation,
            task_state_created=task_state_created,
        )

    if recovery.get("migration_required"):
        return _migrate_legacy_takeover_saga(
            workspace,
            store,
            install_root=install_root,
            context=context,
            client=client,
            service=service,
            account=account,
            issue=issue,
            agentic_run_id=agentic_run_id,
            agent_id=agent_id,
            legacy_state=recovery.get("legacy_state"),
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
    transitions, matched_transition = _preflight_transition(
        context.profile,
        client,
        issue,
        target_status,
    )
    takeover_kind = _takeover_kind(
        issue_status=issue.status,
        target_status=target_status,
        progress_stage=progress_stage,
    )
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
        extra=None,
    )
    comment_content_sha256 = _comment_content_sha256(comment_body)
    _find_takeover_comment(
        client.comments(issue.key),
        issue_key=issue.key,
        agentic_run_id=agentic_run_id,
        marker=comment_marker,
        expected_author=account["account_id"],
        expected_content_sha256=comment_content_sha256,
    )
    preflight_facts_sha256 = _preflight_facts_sha256(
        context=context,
        issue=issue,
        account_id=account["account_id"],
        agentic_run_id=agentic_run_id,
        takeover_kind=takeover_kind,
        authorization_digest=authorization_digest,
        target_status=target_status,
        transition_id=matched_transition[0] if matched_transition else None,
        transitions=transitions,
    )
    persisted = store.persist_takeover_intent(
        issue.key,
        agentic_run_id,
        agent_id=agent_id,
        takeover_kind=takeover_kind,
        authorization_digest=authorization_digest,
        preflight_facts_sha256=preflight_facts_sha256,
        jira_status_before=issue.status,
        jira_status_target=target_status,
        transition_id=matched_transition[0] if matched_transition else None,
        comment_marker=comment_marker,
        comment_content_sha256=comment_content_sha256,
        comment_markdown=comment_body,
        planned_at=agentic_takeover_at,
    )
    return _run_takeover_saga(
        workspace,
        store,
        install_root=install_root,
        context=context,
        client=client,
        service=service,
        account=account,
        operation=persisted["operation"],
        task_state_created=task_state_created,
    )


def _takeover_instruction_reference(issue_key: str, agentic_run_id: str) -> str:
    return f"takeover-instruction:{issue_key}:{agentic_run_id}"


def _run_takeover_saga(
    workspace: Workspace,
    store: TaskStore,
    *,
    install_root: Path,
    context: Any,
    client: JiraClient,
    service: JiraService,
    account: dict[str, str],
    operation: dict[str, Any],
    task_state_created: bool,
) -> dict[str, Any]:
    transition_applied = False
    expected_author = account["account_id"]
    phase = str(operation["phase"])
    if phase_index(phase) < phase_index("comment_verified"):
        operation = _ensure_takeover_comment_phase(
            store,
            context=context,
            client=client,
            service=service,
            account_id=expected_author,
            operation=operation,
        )
        phase = str(operation["phase"])
    if phase_index(phase) < phase_index("status_verified"):
        operation, transition_applied = _ensure_takeover_status_phase(
            store,
            client=client,
            service=service,
            account_id=expected_author,
            operation=operation,
        )

    readback, comment = _verify_final_external_facts(
        client,
        service,
        account_id=expected_author,
        operation=operation,
    )
    mapped_status = context.profile.status_mapping.get(readback.status)
    if not mapped_status:
        raise _takeover_error(
            "takeover_recovery_evidence_mismatch",
            f"Project Profile 未映射接管后 Jira 状态：{readback.status}",
            "请核对 Project Profile 和 Jira 当前 Status；来源快照未建立前不得继续",
            operation=operation,
        )
    try:
        source_context = record_current_task_source_context(
            workspace,
            store,
            install_root=install_root,
            context=context,
            account=account,
            issue=readback,
            agentic_run_id=str(operation["agentic_run_id"]),
            mapped_status=mapped_status,
        )
        finalized = store.finalize_takeover(
            str(operation["issue_key"]),
            str(operation["agentic_run_id"]),
            str(operation["operation_id"]),
        )
        recovery = store.read_takeover_recovery(str(operation["issue_key"]))
    except RuntimeErrorResult as error:
        raise _takeover_error(
            "takeover_local_finalize_failed",
            "Jira 接管事实已验证，但本地最终收口被 Runtime 阻断",
            "请核对本地状态错误后使用同一接管指令恢复；不要重复 Jira 副作用",
            operation=operation,
            retry_safe=error.retry_safe,
            cause_code=error.code,
        ) from error
    except OSError as error:
        raise _takeover_error(
            "takeover_local_finalize_failed",
            "Jira 接管事实已验证，但本地最终收口失败",
            "请使用同一接管指令恢复本地状态；不要重复 Jira Comment 或 transition",
            operation=operation,
            retry_safe=True,
            failure_type=type(error).__name__,
        ) from error
    completed = recovery.get("operation")
    if (
        not isinstance(completed, dict)
        or completed.get("phase") != "local_finalized"
        or completed.get("result") != "completed"
        or not recovery.get("state_consistent")
    ):
        raise _takeover_error(
            "takeover_local_finalize_failed",
            "接管本地快照、业务阶段与事件尚未完成交叉收口",
            "请使用同一接管指令恢复本地状态；不要重复 Jira 副作用",
            operation=completed if isinstance(completed, dict) else operation,
            retry_safe=True,
        )
    return _completed_takeover_result(
        workspace,
        completed,
        comment=comment,
        readback=readback,
        transition_applied=transition_applied,
        task_state_created=task_state_created,
        intake_source=source_context["intake_source"],
        state_consistent=bool(recovery["state_consistent"]),
        local_state_created=bool(finalized.get("created")),
    )


def _ensure_takeover_comment_phase(
    store: TaskStore,
    *,
    context: Any,
    client: JiraClient,
    service: JiraService,
    account_id: str,
    operation: dict[str, Any],
) -> dict[str, Any]:
    if operation.get("result") == "blocked":
        raise _takeover_error(
            str(operation.get("failure_code") or "takeover_comment_evidence_conflict"),
            "接管意图已因事实冲突停止，不能自动恢复 Jira 写入",
            "请先完成逐项风险决策；原接管意图不会被覆盖",
            operation=operation,
        )
    markdown = operation.get("comment_markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise _takeover_error(
            "takeover_comment_material_missing",
            "未完成接管意图缺少原始 Comment 正文，无法安全重发",
            "请人工核对本地接管状态；不得根据当前 Jira Status 猜测原正文",
            operation=operation,
        )
    try:
        comments = client.comments(str(operation["issue_key"]))
        existing = _find_takeover_comment(
            comments,
            issue_key=str(operation["issue_key"]),
            agentic_run_id=str(operation["agentic_run_id"]),
            marker=str(operation["comment_marker"]),
            expected_author=account_id,
            expected_content_sha256=str(operation["comment_content_sha256"]),
        )
    except RuntimeErrorResult as error:
        if error.code in {
            "takeover_comment_duplicate",
            "takeover_comment_evidence_conflict",
        }:
            _block_and_raise(store, operation, error)
        raise
    if existing is not None:
        return store.verify_takeover_comment(
            str(operation["issue_key"]),
            str(operation["agentic_run_id"]),
            str(operation["operation_id"]),
            comment_id=existing.comment_id,
            comment_author=existing.author,
            expected_author=account_id,
            comment_marker=str(operation["comment_marker"]),
            comment_content_sha256=_comment_body_sha256(existing.body),
        )["operation"]

    if operation.get("result") != "in_progress":
        raise _takeover_error(
            str(operation.get("failure_code") or "takeover_comment_result_uncertain"),
            "接管 Comment 尚未验证，既有不确定或阻塞结果禁止自动重写",
            "请先核对 Jira Comment；确认外部事实后再恢复原接管意图",
            operation=operation,
        )
    try:
        _verify_pre_comment_facts(
            context,
            client,
            service,
            account_id=account_id,
            operation=operation,
        )
    except RuntimeErrorResult as error:
        if error.code == "takeover_preflight_facts_changed":
            _block_and_raise(store, operation, error)
        raise
    write_error: Exception | None = None
    comment_id = ""
    try:
        comment_id = client.add_comment(str(operation["issue_key"]), markdown)
    except (JiraTransportError, RuntimeErrorResult) as error:
        write_error = error
        if isinstance(error, RuntimeErrorResult) and error.code in {
            "jira_authorization_failed",
            "jira_issue_not_found",
        }:
            raise
    try:
        comments = client.comments(str(operation["issue_key"]))
        existing = _find_takeover_comment(
            comments,
            issue_key=str(operation["issue_key"]),
            agentic_run_id=str(operation["agentic_run_id"]),
            marker=str(operation["comment_marker"]),
            expected_author=account_id,
            expected_content_sha256=str(operation["comment_content_sha256"]),
            expected_comment_id=comment_id or None,
        )
    except RuntimeErrorResult as error:
        if error.code in {
            "takeover_comment_duplicate",
            "takeover_comment_evidence_conflict",
        }:
            _block_and_raise(store, operation, error)
        _uncertain_and_raise(
            store,
            operation,
            "takeover_comment_result_uncertain",
            "Jira Comment 写入后无法可靠完成回读验证",
            "请先只读核对 Jira Comment，不要重复接管",
            recovery_action="readback_takeover_comment",
        )
    if existing is None:
        if write_error is not None:
            raise _takeover_error(
                "takeover_comment_retryable_absent",
                "Jira Comment 写入响应不明确，但可靠回读确认标记不存在",
                "请使用同一接管指令和授权引用安全重试",
                operation=operation,
                retry_safe=True,
            ) from write_error
        _uncertain_and_raise(
            store,
            operation,
            "takeover_comment_result_uncertain",
            "Jira Comment 写入返回后无法回读到唯一受管记录",
            "请先只读核对 Jira Comment，不要重复接管",
            recovery_action="readback_takeover_comment",
        )
    return store.verify_takeover_comment(
        str(operation["issue_key"]),
        str(operation["agentic_run_id"]),
        str(operation["operation_id"]),
        comment_id=existing.comment_id,
        comment_author=existing.author,
        expected_author=account_id,
        comment_marker=str(operation["comment_marker"]),
        comment_content_sha256=_comment_body_sha256(existing.body),
    )["operation"]


def _ensure_takeover_status_phase(
    store: TaskStore,
    *,
    client: JiraClient,
    service: JiraService,
    account_id: str,
    operation: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    if operation.get("result") == "blocked":
        raise _takeover_error(
            str(operation.get("failure_code") or "takeover_status_external_conflict"),
            "接管 Status 已因事实冲突停止，不能自动恢复 transition",
            "请先完成逐项风险决策；Runtime 不会覆盖第三方状态变化",
            operation=operation,
        )
    try:
        comment = _find_takeover_comment(
            client.comments(str(operation["issue_key"])),
            issue_key=str(operation["issue_key"]),
            agentic_run_id=str(operation["agentic_run_id"]),
            marker=str(operation["comment_marker"]),
            expected_author=account_id,
            expected_content_sha256=str(operation["comment_content_sha256"]),
            expected_comment_id=str(operation.get("comment_id") or "") or None,
        )
    except RuntimeErrorResult as error:
        if error.code in {
            "takeover_comment_duplicate",
            "takeover_comment_evidence_conflict",
        }:
            _block_and_raise(store, operation, error)
        raise
    if comment is None:
        error = _takeover_error(
            "takeover_recovery_evidence_mismatch",
            "Status transition 前找不到已验证的受管 Comment",
            "请核对 Jira Comment；审计证据恢复前不得执行 transition",
            operation=operation,
        )
        _block_and_raise(store, operation, error)
    issue = _read_issue_for_operation(service, operation, account_id=account_id)
    target = str(operation["jira_status_target"])
    original = str(operation["jira_status_before"])
    if issue.status == target:
        verified = store.verify_takeover_status(
            str(operation["issue_key"]),
            str(operation["agentic_run_id"]),
            str(operation["operation_id"]),
            status_after=issue.status,
            transition_applied=False,
        )
        return verified["operation"], False
    if issue.status != original:
        _status_conflict(store, operation, issue.status)
    if operation.get("result") != "in_progress":
        raise _takeover_error(
            str(operation.get("failure_code") or "takeover_transition_result_uncertain"),
            "接管 transition 尚未验证，既有不确定或阻塞结果禁止自动重写",
            "请先核对 Jira 当前 Status；确认外部事实后再恢复原接管意图",
            operation=operation,
        )
    transition_id = operation.get("transition_id")
    if not isinstance(transition_id, str) or not transition_id:
        _status_conflict(store, operation, issue.status)
    available = client.available_transitions(str(operation["issue_key"]))
    matching = [item for item in available if item.get("id") == transition_id]
    if (
        len(matching) != 1
        or (
            str(matching[0].get("to") or "")
            and str(matching[0].get("to")) != target
        )
    ):
        error = _takeover_error(
            "takeover_preflight_facts_changed",
            "稳定意图引用的 Jira transition 已不可用",
            "请核对 Jira 当前 Status 和 Project Profile，当前意图不会被覆盖",
            operation=operation,
            available_transitions=available,
        )
        _block_and_raise(store, operation, error)

    write_error: Exception | None = None
    try:
        client.execute_transition(str(operation["issue_key"]), transition_id)
    except (JiraTransportError, RuntimeErrorResult) as error:
        write_error = error
        if isinstance(error, RuntimeErrorResult) and error.code in {
            "jira_authorization_failed",
            "jira_issue_not_found",
        }:
            raise
    try:
        readback = _read_issue_for_operation(
            service,
            operation,
            account_id=account_id,
        )
    except RuntimeErrorResult:
        _uncertain_and_raise(
            store,
            operation,
            "takeover_transition_result_uncertain",
            "Jira transition 写入后无法可靠回读 Status",
            "请先只读核对 Jira 当前 Status，不要盲目重试 transition",
            recovery_action="readback_takeover_status",
        )
    if readback.status == target:
        verified = store.verify_takeover_status(
            str(operation["issue_key"]),
            str(operation["agentic_run_id"]),
            str(operation["operation_id"]),
            status_after=readback.status,
            transition_applied=True,
        )
        return verified["operation"], True
    if readback.status == original:
        raise _takeover_error(
            "takeover_transition_retryable_original",
            "Jira transition 后可靠回读仍为原 Status",
            "请使用同一接管指令恢复；Runtime 将复用原意图并重新验证 transition",
            operation=operation,
            retry_safe=True,
            write_error=type(write_error).__name__ if write_error else None,
        )
    _status_conflict(store, operation, readback.status)
    raise AssertionError("unreachable")


def _verify_final_external_facts(
    client: JiraClient,
    service: JiraService,
    *,
    account_id: str,
    operation: dict[str, Any],
) -> tuple[JiraIssue, JiraComment]:
    issue = _read_issue_for_operation(service, operation, account_id=account_id)
    if issue.status != operation["jira_status_target"]:
        raise _takeover_error(
            "takeover_recovery_evidence_mismatch",
            "本地最终收口前 Jira Status 与稳定目标不一致",
            "请核对 Jira 当前 Status；不得用本地成功状态覆盖外部冲突",
            operation=operation,
            jira_status_after=issue.status,
        )
    comments = client.comments(str(operation["issue_key"]))
    comment = _find_takeover_comment(
        comments,
        issue_key=str(operation["issue_key"]),
        agentic_run_id=str(operation["agentic_run_id"]),
        marker=str(operation["comment_marker"]),
        expected_author=account_id,
        expected_content_sha256=str(operation["comment_content_sha256"]),
        expected_comment_id=str(operation.get("comment_id") or "") or None,
        allow_legacy_digest=operation.get("comment_markdown") is None
        and operation.get("phase") == "local_finalized",
    )
    if comment is None:
        raise _takeover_error(
            "takeover_recovery_evidence_mismatch",
            "本地最终收口前找不到已记录的受管 Comment",
            "请核对 Jira Comment，不要重复写入",
            operation=operation,
        )
    return issue, comment


def _completed_takeover_result(
    workspace: Workspace,
    operation: dict[str, Any],
    *,
    comment: JiraComment,
    readback: JiraIssue,
    transition_applied: bool,
    task_state_created: bool,
    intake_source: dict[str, Any],
    state_consistent: bool,
    local_state_created: bool,
) -> dict[str, Any]:
    return {
        "workspace": str(workspace.root),
        "issue_key": operation["issue_key"],
        "agentic_run_id": operation["agentic_run_id"],
        "agent_id": operation["agent_id"],
        "task_state_created": task_state_created,
        "jira_status_before": operation["jira_status_before"],
        "jira_status_target": operation["jira_status_target"],
        "jira_status_after": readback.status,
        "transition_applied": transition_applied,
        "takeover_status": operation["takeover_status"],
        "takeover_kind": operation["takeover_kind"],
        "human_notice": operation["human_notice"],
        "takeover_comment_id": comment.comment_id,
        "takeover_comment_author": comment.author,
        "takeover_comment_author_verified": comment.author
        == operation["comment_author"],
        "takeover_comment_verified": True,
        "takeover_phase": operation["phase"],
        "takeover_result": operation["result"],
        "external_result_certainty": operation["external_result_certainty"],
        "retry_safe": operation["retry_safe"],
        "recovery_action": operation["recovery_action"],
        "agentic_takeover_at": operation["planned_at"],
        "current_stage": "takeover_started",
        "state_consistent": state_consistent,
        "local_state_created": local_state_created,
        "intake_source": intake_source,
        "agentic_next_action": operation["agentic_next_action"],
    }


def _preflight_transition(
    profile: Any,
    client: JiraClient,
    issue: JiraIssue,
    target_status: str,
) -> tuple[list[dict[str, str]], tuple[str, str, str] | None]:
    if issue.status == target_status:
        return [], None
    transitions = client.available_transitions(issue.key)
    target_key = _transition_key_for(profile, issue.status)
    matched = None
    if target_key:
        matched = match_transition(
            issue.status,
            transitions,
            {"transitions": profile.transition_mapping},
            target_key=target_key,
        )
    if matched is None:
        raise _blocked(
            "jira_transition_mapping_gap",
            f"Jira 可用 transition 中没有目标 transition {target_key or target_status}",
            "请核对 Project Profile 状态与 transition 映射；任何 Comment 尚未写入",
        )
    return transitions, matched


def _preflight_facts_sha256(
    *,
    context: Any,
    issue: JiraIssue,
    account_id: str,
    agentic_run_id: str,
    takeover_kind: str,
    authorization_digest: str,
    target_status: str,
    transition_id: str | None,
    transitions: list[dict[str, str]],
) -> str:
    facts = {
        "connection_id": context.connection.connection_id,
        "jira_issue_id": issue.issue_id,
        "issue_key": issue.key,
        "project_key": issue.project_key,
        "assignee": issue.assignee,
        "expected_assignee": account_id,
        "jira_status_before": issue.status,
        "jira_status_target": target_status,
        "transition_id": transition_id,
        "available_transitions": sorted(
            (
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                    "to": str(item.get("to") or ""),
                }
                for item in transitions
            ),
            key=lambda item: (item["id"], item["name"], item["to"]),
        ),
        "agentic_run_id": agentic_run_id,
        "takeover_kind": takeover_kind,
        "authorization_digest": authorization_digest,
    }
    return hashlib.sha256(
        json.dumps(
            facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _verify_pre_comment_facts(
    context: Any,
    client: JiraClient,
    service: JiraService,
    *,
    account_id: str,
    operation: dict[str, Any],
) -> None:
    issue = _read_issue_for_operation(service, operation, account_id=account_id)
    if issue.status != operation["jira_status_before"]:
        raise _takeover_error(
            "takeover_preflight_facts_changed",
            "稳定接管意图落盘后 Jira 原 Status 已变化",
            "请核对 Jira 当前事实；旧意图不会被覆盖或重新分类",
            operation=operation,
            jira_status_after=issue.status,
        )
    transition_id = operation.get("transition_id")
    transitions = (
        client.available_transitions(str(operation["issue_key"]))
        if transition_id
        else []
    )
    if transition_id and not any(
        item.get("id") == transition_id for item in transitions
    ):
        raise _takeover_error(
            "takeover_preflight_facts_changed",
            "稳定接管意图落盘后目标 transition 已不可用",
            "请核对 Jira 当前事实和 Project Profile；旧意图不会被覆盖",
            operation=operation,
            available_transitions=transitions,
        )
    actual = _preflight_facts_sha256(
        context=context,
        issue=issue,
        account_id=account_id,
        agentic_run_id=str(operation["agentic_run_id"]),
        takeover_kind=str(operation["takeover_kind"]),
        authorization_digest=str(operation["authorization_digest"]),
        target_status=str(operation["jira_status_target"]),
        transition_id=str(transition_id) if transition_id else None,
        transitions=transitions,
    )
    if actual != operation["preflight_facts_sha256"]:
        raise _takeover_error(
            "takeover_preflight_facts_changed",
            "首次 Jira 写入前的事实摘要与稳定接管意图不一致",
            "请核对 Jira、工作空间和 Project Profile；旧意图不会被覆盖",
            operation=operation,
            actual_preflight_facts_sha256=actual,
        )


def _find_takeover_comment(
    comments: list[JiraComment],
    *,
    issue_key: str,
    agentic_run_id: str,
    marker: str,
    expected_author: str,
    expected_content_sha256: str,
    expected_comment_id: str | None = None,
    allow_legacy_digest: bool = False,
) -> JiraComment | None:
    issue_prefix = f"[agentic-ops-takeover:{issue_key}:"
    run_prefix = f"[agentic-ops-takeover:{issue_key}:{agentic_run_id}"
    foreign_run = [
        comment
        for comment in comments
        if any(
            line.startswith(issue_prefix) and not line.startswith(run_prefix)
            for line in comment.standalone_lines
        )
    ]
    if foreign_run:
        raise _takeover_error(
            "external_task_state_conflict",
            "Jira 中存在属于其它运行的受管接管 Comment",
            "请人工核对外来运行与当前工作空间；不得创建第二条接管记录",
            conflicting_comment_ids=[
                comment.comment_id for comment in foreign_run if comment.comment_id
            ],
        )
    scoped = [
        comment
        for comment in comments
        if any(
            line.startswith(run_prefix)
            and len(line) > len(run_prefix)
            and line[len(run_prefix)] in {":", "]"}
            for line in comment.standalone_lines
        )
    ]
    matches = [comment for comment in scoped if marker in comment.standalone_lines]
    if len(matches) > 1:
        raise _takeover_error(
            "takeover_comment_duplicate",
            "Jira 中存在多条相同稳定标记的接管 Comment",
            "请人工核对重复 Comment；不得继续 transition",
        )
    if scoped and len(matches) != len(scoped):
        raise _takeover_error(
            "takeover_comment_evidence_conflict",
            "当前运行存在不同稳定标记的受管接管 Comment",
            "请人工核对 Jira Comment 与本地运行，不得创建第二条记录",
        )
    if not matches:
        return None
    comment = matches[0]
    content_matches = _comment_body_sha256(comment.body) == expected_content_sha256
    if (
        not comment.comment_id
        or comment.author != expected_author
        or (expected_comment_id is not None and comment.comment_id != expected_comment_id)
        or (not content_matches and not allow_legacy_digest)
    ):
        raise _takeover_error(
            "takeover_comment_evidence_conflict",
            "Jira Comment 的 ID、作者、稳定标记或正文摘要与接管意图不一致",
            "请人工核对 Jira Comment；不得复用可复制文本或覆盖原证据",
        )
    return comment


def _comment_content_sha256(markdown: str) -> str:
    return normalized_comment_content_sha256(markdown)


def _comment_body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _read_issue_for_operation(
    service: JiraService,
    operation: dict[str, Any],
    *,
    account_id: str,
) -> JiraIssue:
    issue = service.inspect_issue(str(operation["issue_key"]))
    if issue.key != operation["issue_key"]:
        raise _takeover_error(
            "takeover_recovery_evidence_mismatch",
            "Jira 回读 Issue Key 与稳定接管意图不一致",
            "请核对 Jira 任务与本地状态，不要继续副作用",
            operation=operation,
            actual_issue_key=issue.key,
        )
    if issue.assignee != account_id:
        raise _takeover_error(
            "owner_mismatch",
            "当前工作空间 Jira 账户已不是任务经办人",
            "请核对 Jira Assignee；恢复所有权前不得继续接管",
            operation=operation,
            actual_assignee=issue.assignee,
        )
    return issue


def _require_existing_identity(
    task: dict[str, Any],
    *,
    connection_id: str,
    issue: JiraIssue,
) -> None:
    expected = {
        "connection_id": connection_id,
        "jira_issue_id": issue.issue_id,
        "issue_key": issue.key,
        "project_key": issue.project_key,
    }
    actual = {key: task.get(key) for key in expected}
    if actual != expected:
        raise _takeover_error(
            "takeover_state_identity_mismatch",
            "本地任务身份与当前 Jira/工作空间事实不一致",
            "请人工核对本地任务状态；不得覆盖现有 run",
            expected_identity=expected,
            actual_identity=actual,
        )


def _require_persisted_request(
    operation: dict[str, Any],
    *,
    issue_key: str,
    agentic_run_id: str,
    agent_id: str,
    authorization_digest: str,
) -> None:
    expected = {
        "issue_key": issue_key,
        "agentic_run_id": agentic_run_id,
        "agent_id": agent_id,
        "authorization_digest": authorization_digest,
    }
    actual = {key: operation.get(key) for key in expected}
    if actual != expected:
        raise _takeover_error(
            "takeover_intent_conflict",
            "本次接管请求与已持久化稳定意图不一致",
            "请使用原 agent、授权引用和运行恢复，不得创建第二个接管意图",
            operation=operation,
            expected_request=expected,
            actual_request=actual,
        )


def _status_conflict(
    store: TaskStore,
    operation: dict[str, Any],
    actual_status: str,
) -> None:
    error = _takeover_error(
        "takeover_status_external_conflict",
        "Jira 当前 Status 既不是稳定原值，也不是接管目标值",
        "请逐项确认第三方状态变化；Runtime 不会盲目重试 transition",
        operation=operation,
        jira_status_after=actual_status,
    )
    _block_and_raise(store, operation, error)


def _block_and_raise(
    store: TaskStore,
    operation: dict[str, Any],
    error: RuntimeErrorResult,
) -> None:
    stopped = store.block_takeover(
        str(operation["issue_key"]),
        str(operation["agentic_run_id"]),
        str(operation["operation_id"]),
        failure_code=error.code,
        recovery_action="review_takeover_risk",
    )["operation"]
    raise _takeover_error(
        error.code,
        error.message,
        error.required_human_action,
        operation=stopped,
        **dict(error.details),
    )


def _uncertain_and_raise(
    store: TaskStore,
    operation: dict[str, Any],
    code: str,
    message: str,
    action: str,
    *,
    recovery_action: str,
) -> None:
    stopped = store.mark_takeover_uncertain(
        str(operation["issue_key"]),
        str(operation["agentic_run_id"]),
        str(operation["operation_id"]),
        failure_code=code,
        recovery_action=recovery_action,
    )["operation"]
    raise _takeover_error(
        code,
        message,
        action,
        operation=stopped,
    )


def _takeover_error(
    code: str,
    message: str,
    action: str,
    *,
    operation: dict[str, Any] | None = None,
    retry_safe: bool = False,
    **details: Any,
) -> RuntimeErrorResult:
    evidence = dict(details)
    if operation is not None:
        evidence.update(
            {
                "operation_id": operation.get("operation_id"),
                "takeover_phase": operation.get("phase"),
                "takeover_result": operation.get("result"),
                "takeover_kind": operation.get("takeover_kind"),
                "recovery_action": operation.get("recovery_action"),
            }
        )
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=retry_safe,
        required_human_action=action,
        details=evidence,
    )


def _migrate_legacy_takeover_saga(
    workspace: Workspace,
    store: TaskStore,
    *,
    install_root: Path,
    context: Any,
    client: JiraClient,
    service: JiraService,
    account: dict[str, str],
    issue: JiraIssue,
    agentic_run_id: str,
    agent_id: str,
    legacy_state: Any,
) -> dict[str, Any]:
    legacy = legacy_state if isinstance(legacy_state, dict) else {}
    evidence = legacy.get("evidence")
    if not isinstance(evidence, dict):
        raise _takeover_error(
            "takeover_legacy_state_unverified",
            "legacy 接管状态缺少可恢复证据",
            "请人工核对原 Comment、负责人、Status 和运行编号",
        )
    comment_id = str(evidence.get("takeover_comment_id") or "")
    marker = str(evidence.get("takeover_comment_marker") or "")
    takeover_kind = str(evidence.get("takeover_kind") or "")
    before = str(evidence.get("jira_status_before") or "")
    target = str(evidence.get("jira_status_after") or "")
    if not all((comment_id, marker, takeover_kind, before, target)):
        raise _takeover_error(
            "takeover_legacy_state_unverified",
            "legacy 接管事件证据不完整",
            "请人工核对原 Comment、Status 和运行编号，原状态不会被覆盖",
        )
    comment = client.comment(issue.key, comment_id)
    if (
        comment.author != account["account_id"]
        or marker not in comment.standalone_lines
        or issue.assignee != account["account_id"]
        or issue.status != target
    ):
        raise _takeover_error(
            "takeover_legacy_state_unverified",
            "legacy 接管状态与 Jira 回读证据不一致",
            "请人工核对 Comment 作者/标记、负责人和 Status；原状态不会被覆盖",
        )
    legacy_authorization_digest = str(evidence.get("authorization_digest") or "")
    if not legacy_authorization_digest:
        raise _takeover_error(
            "takeover_legacy_state_unverified",
            "legacy 接管事件缺少原授权摘要",
            "请人工核对原接管授权；不得用本次输入替换原稳定意图",
        )
    preflight_digest = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    migrated = store.migrate_legacy_takeover(
        issue.key,
        agentic_run_id,
        {
            "agent_id": str(evidence.get("agent_id") or agent_id),
            "takeover_kind": takeover_kind,
            "authorization_digest": legacy_authorization_digest,
            "preflight_facts_sha256": preflight_digest,
            "jira_status_before": before,
            "jira_status_target": target,
            "jira_status_after": issue.status,
            "transition_id": None,
            "comment_marker": marker,
            "comment_content_sha256": _comment_body_sha256(comment.body),
            "comment_id": comment.comment_id,
            "comment_author": comment.author,
            "expected_comment_author": account["account_id"],
            "assignee": issue.assignee,
            "expected_assignee": account["account_id"],
        },
    )
    operation = migrated["operation"]
    return _run_takeover_saga(
        workspace,
        store,
        install_root=install_root,
        context=context,
        client=client,
        service=service,
        account=account,
        operation=operation,
        task_state_created=False,
    )


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


def _default_agent_id(install_root: Path) -> str:
    """从安装目录身份读取 agent_id（D-048 阶段二）。缺失时阻断。"""
    from ao_work.installation import load_install_identity

    try:
        identity = load_install_identity(install_root)
    except RuntimeErrorResult as error:
        raise _blocked(
            "agent_identity_missing",
            "安装目录缺少研发员身份，无法确定接管身份",
            "请运行 ao-work auth 配置安装级 agent_id",
        ) from error
    agent_id = str(identity.get("agent_id") or "").strip()
    if not agent_id:
        raise _blocked(
            "agent_identity_missing",
            "安装目录研发员身份缺少 agent_id，无法确定接管身份",
            "请运行 ao-work auth 重新配置安装级 agent_id",
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
        "human_notice": "未执行接管：请从候选列表选择目标任务后运行 ao-work takeover <KEY>。",
        "note": "未提供 issue_key；请从候选列表确认目标任务后，带 issue_key 重新执行 takeover",
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
