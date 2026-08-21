from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ao_maint.install.identity import load_maintainer_identity
from ao_maint.jira.cli import _append_decision, _write_new_plan
from ao_maint.jira.client import JiraClient, UrllibJiraTransport
from ao_maint.jira.config import (
    load_comment_template_schema,
    load_maintainer_jira_config,
    load_maintainer_workflow,
    select_maintainer_workflow,
    plans_dir,
)
from ao_maint.jira.service import MaintainerJiraService, WritePlan
from ao_maint.jira.scope import validate_issue_readback
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_maint.takeover.state import (
    append_event,
    git_binding,
    load_state,
    save_state,
    validate_digest,
    validate_issue_key,
    work_authorization_reference,
)


def configure_takeover_parser(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("takeover")
    parser.add_argument("issue_key")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--design-file")
    action.add_argument("--confirm")


def execute_takeover(args: argparse.Namespace, source_root: Path) -> dict[str, Any]:
    issue_key = validate_issue_key(args.issue_key)
    identity = load_maintainer_identity(source_root)
    config = load_maintainer_jira_config(source_root)
    email, token = config.require_credentials()
    client = JiraClient(
        config.connection,
        UrllibJiraTransport(config.connection, email, token),
    )
    service = MaintainerJiraService(client)
    if args.design_file:
        return _record_design(
            source_root,
            service,
            issue_key,
            identity,
            args.design_file,
            config.connection.connection_id,
        )
    if args.confirm:
        return _confirm_pending_gate(
            source_root,
            service,
            issue_key,
            identity,
            validate_digest(args.confirm),
            config.connection.connection_id,
        )
    return _takeover(
        source_root,
        service,
        issue_key,
        identity,
        config.connection.connection_id,
    )


def _takeover(
    source_root: Path,
    service: MaintainerJiraService,
    issue_key: str,
    identity: dict[str, str],
    connection_id: str,
) -> dict[str, Any]:
    issue = service.inspect_issue(issue_key)
    validate_issue_readback(issue_key, issue.key, issue.project_key)
    stage = _workflow_stage(
        source_root, connection_id, issue.project_key, issue.issue_type, issue.status
    )
    if stage == "completed":
        raise _blocked(
            "maintainer_takeover_completed",
            "Jira 任务已经完成，不能再次接管",
            "请确认是否需要新建后续任务",
        )
    if stage not in {"waiting_takeover", "implementation"}:
        raise _blocked(
            "maintainer_takeover_status_unsupported",
            f"Jira 状态 {issue.status!r} 未映射为可接管阶段",
            "请补齐 maintainer Jira workflow 状态映射并进行风险决策",
        )
    repository_root, branch = git_binding(source_root)
    state = load_state(source_root, issue_key)
    if state is not None:
        _require_same_owner_and_issue(state, identity, issue.issue_id)
        _require_same_binding(state, repository_root, branch)
        if stage == "waiting_takeover":
            return _execute_new_takeover(
                source_root,
                service,
                issue,
                identity,
                state,
                connection_id,
            )
        pending_gate = str(state.get("pending_gate", ""))
        state["mode"] = "resume"
        state["jira_status"] = issue.status
        state["updated_at"] = _now()
        save_state(source_root, state)
        append_event(
            source_root,
            issue_key,
            {"event": "takeover_resumed", "run_id": state["run_id"]},
        )
        if pending_gate == "adopt":
            state["mode"] = "adopt"
            save_state(source_root, state)
            return _result(
                state,
                mode="adopt",
                takeover_status="waiting_confirmation",
                human_notice="检测到待确认的存量任务接纳，尚未获得接纳确认。",
                agentic_next_action=f"confirm_takeover:{state['design_digest']}",
            )
        if pending_gate == "design_review" and state.get("design_digest"):
            return _result(
                state,
                mode="resume",
                takeover_status="waiting_confirmation",
                human_notice="已恢复任务，当前设计方案仍在等待审查确认。",
                agentic_next_action=f"confirm_takeover:{state['design_digest']}",
            )
        if (
            pending_gate == "precommit"
            and state.get("authorization_status") == "active"
        ):
            return _result(
                state,
                mode="resume",
                takeover_status="completed",
                human_notice="检测到当前 maintainer 工作空间的已有运行，已恢复任务并留下审计记录。",
                agentic_next_action="implement_until_precommit_gate",
                work_authorization=work_authorization_reference(state),
            )
        return _result(
            state,
            mode="resume",
            takeover_status="completed",
            human_notice="检测到当前 maintainer 工作空间的已有运行，已恢复任务并留下审计记录。",
            agentic_next_action="prepare_design_review",
        )
    legacy_run = _legacy_run_id(source_root, issue_key)
    if stage == "implementation" and legacy_run:
        state = _new_state(
            issue,
            identity,
            legacy_run,
            "resume",
            repository_root,
            branch,
        )
        save_state(source_root, state)
        append_event(
            source_root,
            issue_key,
            {
                "event": "legacy_takeover_resumed",
                "run_id": legacy_run,
                "source": "maintainer_decisions",
            },
        )
        return _result(
            state,
            mode="resume",
            takeover_status="completed",
            human_notice="检测到本工作空间此前已开始处理该任务，已恢复并补建本地接管状态。",
            agentic_next_action="prepare_design_review",
        )
    if stage == "implementation":
        run_id = _generate_run_id(issue_key)
        state = _new_state(
            issue,
            identity,
            run_id,
            "adopt",
            repository_root,
            branch,
        )
        digest = _gate_digest(issue, identity["agent_id"], run_id, "adopt")
        state["pending_gate"] = "adopt"
        state["design_digest"] = digest
        save_state(source_root, state)
        append_event(
            source_root,
            issue_key,
            {"event": "takeover_adopt_confirmation_requested", "digest": digest},
        )
        return _result(
            state,
            mode="adopt",
            takeover_status="waiting_confirmation",
            human_notice="任务已在处理中但本工作空间没有可验证运行，需要确认接纳存量任务。",
            agentic_next_action=f"confirm_takeover:{digest}",
        )
    state = _new_state(
        issue,
        identity,
        _generate_run_id(issue_key),
        "new",
        repository_root,
        branch,
    )
    save_state(source_root, state)
    return _execute_new_takeover(
        source_root,
        service,
        issue,
        identity,
        state,
        connection_id,
    )


def _execute_new_takeover(
    source_root: Path,
    service: MaintainerJiraService,
    issue: Any,
    identity: dict[str, str],
    state: dict[str, Any],
    connection_id: str,
) -> dict[str, Any]:
    run_id = str(state["run_id"])
    content = _takeover_comment(issue, identity, run_id, adopted=False)
    comment_plan = service.plan_comment(
        issue.key,
        "takeover-start",
        "progress",
        content,
        maintainer_run_id=run_id,
        comment_template_schema=load_comment_template_schema(source_root),
    )
    _persist_and_apply(
        source_root,
        service,
        comment_plan,
        f"{issue.key.lower()}-takeover-comment.json",
        "用户指令授权 maintainer 接管任务",
    )
    workflow = load_maintainer_workflow(source_root, connection_id)
    transition_plan = service.plan_transition(
        issue.key,
        "takeover-start-transition",
        maintainer_run_id=run_id,
        workflow=workflow,
        target_transition="start_progress",
    )
    _persist_and_apply(
        source_root,
        service,
        transition_plan,
        f"{issue.key.lower()}-takeover-transition.json",
        "用户指令授权 maintainer 接管任务",
    )
    readback = service.inspect_issue(issue.key)
    validate_issue_readback(issue.key, readback.key, readback.project_key)
    if _workflow_stage(
        source_root,
        connection_id,
        issue.project_key,
        issue.issue_type,
        readback.status,
    ) != "implementation":
        raise _blocked(
            "maintainer_takeover_readback_mismatch",
            "接管后 Jira 状态回读未进入实施阶段",
            "请人工核对 Jira 状态，不要重复执行接管",
        )
    state["jira_status"] = readback.status
    state["pending_gate"] = "design_review"
    state["updated_at"] = _now()
    save_state(source_root, state)
    append_event(
        source_root,
        issue.key,
        {"event": "takeover_completed", "mode": "new", "run_id": run_id},
    )
    return _result(
        state,
        mode="new",
        takeover_status="completed",
        human_notice="已完成新接管：开始评论、状态流转和写后回读均已验证。",
        agentic_next_action="prepare_design_review",
    )


def _record_design(
    source_root: Path,
    service: MaintainerJiraService,
    issue_key: str,
    identity: dict[str, str],
    design_file: str,
    connection_id: str,
) -> dict[str, Any]:
    state = _required_state(
        source_root, service, issue_key, identity, connection_id
    )
    content = _read_design(source_root, design_file)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    state["pending_gate"] = "design_review"
    state["design_digest"] = digest
    state["design_content"] = content
    state["authorization_status"] = "pending"
    state["updated_at"] = _now()
    save_state(source_root, state)
    append_event(
        source_root,
        issue_key,
        {"event": "design_review_requested", "design_digest": digest},
    )
    return _result(
        state,
        mode=str(state["mode"]),
        takeover_status="waiting_confirmation",
        human_notice="设计方案已绑定当前任务运行，等待设计审查确认。",
        agentic_next_action=f"confirm_takeover:{digest}",
    )


def _confirm_pending_gate(
    source_root: Path,
    service: MaintainerJiraService,
    issue_key: str,
    identity: dict[str, str],
    digest: str,
    connection_id: str,
) -> dict[str, Any]:
    state = _required_state(
        source_root, service, issue_key, identity, connection_id
    )
    if state.get("design_digest") != digest:
        raise _blocked(
            "maintainer_takeover_confirmation_mismatch",
            "确认摘要与当前待确认内容不一致",
            "请使用 takeover 输出的当前摘要重新确认",
        )
    pending_gate = str(state.get("pending_gate", ""))
    if pending_gate == "adopt":
        issue = service.inspect_issue(issue_key)
        validate_issue_readback(issue_key, issue.key, issue.project_key)
        content = _takeover_comment(
            issue, identity, str(state["run_id"]), adopted=True
        )
        plan = service.plan_comment(
            issue_key,
            "takeover-adopt",
            "progress",
            content,
            maintainer_run_id=str(state["run_id"]),
            comment_template_schema=load_comment_template_schema(source_root),
        )
        _persist_and_apply(
            source_root,
            service,
            plan,
            f"{issue_key.lower()}-takeover-adopt.json",
            "公司员工指导员确认接纳存量任务",
        )
        state["pending_gate"] = "design_review"
        state["design_digest"] = ""
        state["design_content"] = ""
        state["updated_at"] = _now()
        save_state(source_root, state)
        append_event(
            source_root,
            issue_key,
            {"event": "takeover_adopted", "confirmation_digest": digest},
        )
        return _result(
            state,
            mode="adopt",
            takeover_status="completed",
            human_notice="已确认接纳存量任务并写入审计评论。",
            agentic_next_action="prepare_design_review",
        )
    if pending_gate != "design_review" or not state.get("design_digest"):
        raise _blocked(
            "maintainer_takeover_confirmation_not_pending",
            "当前没有可确认的设计审查摘要",
            "请先通过 --design-file 绑定设计方案",
        )
    authorization = work_authorization_reference(state)
    decision_content = "\n".join(
        [
            "## 设计审查已确认",
            "",
            f"- 运行 ID: `{state['run_id']}`",
            f"- 设计摘要: `{digest}`",
            f"- 工作项连续授权: `{authorization}`",
            "- 授权范围: 已确认设计内的实现、验证及必要 Jira 进度回写；提交前仍需确认精确 staged 内容。",
            "- 独立门禁: 风险或范围变化、main、合并、发布、Tag、强推和历史改写。",
            "",
            "### 已确认设计",
            "",
            str(state["design_content"]),
        ]
    )
    decision_plan = service.plan_comment(
        issue_key,
        f"design-confirm-{digest[:16]}",
        "decision",
        decision_content,
        maintainer_run_id=str(state["run_id"]),
    )
    _persist_and_apply(
        source_root,
        service,
        decision_plan,
        f"{issue_key.lower()}-design-confirm-{digest[:16]}.json",
        "公司员工指导员确认 maintainer 设计并授予工作项连续执行授权",
    )
    state["authorization_status"] = "active"
    state["pending_gate"] = "precommit"
    state["updated_at"] = _now()
    save_state(source_root, state)
    reference = authorization
    append_event(
        source_root,
        issue_key,
        {
            "event": "design_approved",
            "design_digest": digest,
            "work_authorization": reference,
        },
    )
    return _result(
        state,
        mode=str(state["mode"]),
        takeover_status="completed",
        human_notice="设计审查已确认，工作项级连续执行授权已生效。",
        agentic_next_action="implement_until_precommit_gate",
        work_authorization=reference,
    )


def _persist_and_apply(
    source_root: Path,
    service: MaintainerJiraService,
    plan: WritePlan,
    filename: str,
    decision_summary: str,
) -> dict[str, Any]:
    path = plans_dir(source_root) / filename
    _write_new_plan(path, plan.to_dict())
    authorization = f"task-directive:{plan.issue_key}:{plan.maintainer_run_id}"
    _append_decision(source_root, plan, decision_summary, authorization)
    if plan.operation == "jira_comment":
        result = service.apply_comment(plan, plan.plan_id)
        service.readback_comment(plan)
        return result
    if plan.operation == "jira_transition":
        result = service.apply_transition(plan, plan.plan_id)
        service.readback_transition(plan)
        return result
    raise _blocked(
        "maintainer_takeover_operation_invalid",
        "接管编排包含未允许的 Jira 操作",
        "请修复 maintainer takeover Runtime",
    )


def _required_state(
    source_root: Path,
    service: MaintainerJiraService,
    issue_key: str,
    identity: dict[str, str],
    connection_id: str,
) -> dict[str, Any]:
    state = load_state(source_root, issue_key)
    if state is None:
        raise _blocked(
            "maintainer_takeover_state_missing",
            "尚未建立 maintainer 接管状态",
            f"请先运行 ao-maint takeover {issue_key}",
        )
    issue = service.inspect_issue(issue_key)
    validate_issue_readback(issue_key, issue.key, issue.project_key)
    _require_same_owner_and_issue(state, identity, issue.issue_id)
    if _workflow_stage(
        source_root, connection_id, issue.project_key, issue.issue_type, issue.status
    ) != "implementation":
        raise _blocked(
            "maintainer_takeover_stage_changed",
            "Jira 任务已不在实施阶段，不能沿用当前接管门禁",
            "请核对任务状态并进行风险决策",
        )
    repository_root, branch = git_binding(source_root)
    _require_same_binding(state, repository_root, branch)
    return state


def _new_state(
    issue: Any,
    identity: dict[str, str],
    run_id: str,
    mode: str,
    repository_root: str,
    branch: str,
) -> dict[str, Any]:
    now = _now()
    return {
        "schema_version": 1,
        "issue_key": issue.key,
        "jira_issue_id": issue.issue_id,
        "agent_id": identity["agent_id"],
        "run_id": run_id,
        "mode": mode,
        "jira_status": issue.status,
        "repository_root": repository_root,
        "working_branch": branch,
        "authorization_status": "pending",
        "pending_gate": "design_review",
        "design_digest": "",
        "design_content": "",
        "created_at": now,
        "updated_at": now,
    }


def _require_same_owner_and_issue(
    state: dict[str, Any], identity: dict[str, str], issue_id: str
) -> None:
    if state.get("agent_id") != identity["agent_id"]:
        raise _blocked(
            "maintainer_takeover_owner_conflict",
            "接管状态属于另一个 maintainer Agent",
            "请进行风险决策并明确是否接纳存量任务",
        )
    if state.get("jira_issue_id") != issue_id:
        raise _blocked(
            "maintainer_takeover_issue_replaced",
            "Jira issue ID 与本地接管状态不一致",
            "请核对 Jira 任务是否被替换，不要继续执行",
        )


def _require_same_binding(
    state: dict[str, Any], repository_root: str, branch: str
) -> None:
    if (
        state.get("repository_root") != repository_root
        or state.get("working_branch") != branch
    ):
        raise _blocked(
            "maintainer_takeover_binding_changed",
            "接管状态绑定的仓库或分支已经变化",
            "请进行风险决策，不要沿用原连续执行授权",
        )


def _workflow_stage(
    source_root: Path,
    connection_id: str,
    project_key: str,
    issue_type: str,
    status: str,
) -> str:
    workflow = load_maintainer_workflow(source_root, connection_id)
    selected = select_maintainer_workflow(workflow, project_key, issue_type)
    statuses = selected.get("statuses", {}) if isinstance(selected, dict) else {}
    stage = statuses.get(status) if isinstance(statuses, dict) else None
    return str(stage or "")


def _takeover_comment(
    issue: Any,
    identity: dict[str, str],
    run_id: str,
    *,
    adopted: bool,
) -> str:
    action = "接纳存量任务" if adopted else "新接管任务"
    return "\n".join(
        [
            f"- 运行 ID: `{run_id}`",
            f"- 当前阶段: {action}，进入设计审查准备阶段。",
            f"- 已完成动作: 已通过 maintainer Runtime 回读 {issue.key} 的任务、负责人和状态事实。",
            "- 执行计划: 先完成设计审查；确认后在绑定范围内连续实现和验证，并在提交前再次确认。",
            "- 风险: 所有权、范围、分支、验证或外部事实变化时立即停止并进入风险决策。",
            f"- 执行者 agent_id: `{identity['agent_id']}`",
            f"- Agent 类型: `{identity.get('agent_type', '')}`",
            f"- 模型: `{identity.get('model', '')}`",
            f"- 接管环境: {identity.get('environment', '') or 'maintainer 工作空间'}",
        ]
    )


def _legacy_run_id(source_root: Path, issue_key: str) -> str:
    path = plans_dir(source_root).parent / "decisions.ndjson"
    if not path.is_file() or path.is_symlink():
        return ""
    found = ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            if (
                isinstance(payload, dict)
                and payload.get("issue_key") == issue_key
                and payload.get("operation") in {"jira_comment", "jira_transition"}
            ):
                found = str(payload.get("maintainer_run_id", ""))
    except (OSError, json.JSONDecodeError):
        return ""
    return found


def _read_design(source_root: Path, value: str) -> str:
    supplied = Path(value).expanduser()
    candidate = supplied if supplied.is_absolute() else source_root / supplied
    candidate = candidate.absolute()
    try:
        info = candidate.lstat()
    except OSError as error:
        raise _blocked(
            "maintainer_design_file_missing",
            "设计文件无法读取",
            "请提供当前源头工作区中的 UTF-8 普通文件",
        ) from error
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or candidate.is_symlink()
        or info.st_size > 1024 * 1024
    ):
        raise _blocked(
            "maintainer_design_file_invalid",
            "设计文件必须是大小合规的单链接普通文件",
            "请提供当前源头工作区中的 UTF-8 普通文件",
        )
    try:
        path = candidate.resolve(strict=True)
        path.relative_to(source_root.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise _blocked(
            "maintainer_design_file_outside_source",
            "设计文件必须位于当前源头工作区内",
            "请把设计文件放入当前源头工作区的忽略目录后重试",
        ) from error
    try:
        content = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise _blocked(
            "maintainer_design_file_invalid",
            "设计文件不是可读取的 UTF-8 文本",
            "请提供当前源头工作区中的 UTF-8 普通文件",
        ) from error
    if not content:
        raise _blocked(
            "maintainer_design_file_invalid", "设计文件不能为空", "请补充设计内容"
        )
    return content


def _gate_digest(issue: Any, agent_id: str, run_id: str, gate: str) -> str:
    payload = {
        "issue_key": issue.key,
        "jira_issue_id": issue.issue_id,
        "status": issue.status,
        "agent_id": agent_id,
        "run_id": run_id,
        "gate": gate,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _generate_run_id(issue_key: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    random_part = hashlib.sha256(os.urandom(16)).hexdigest()[:8]
    return f"maint-{issue_key}-{stamp}-{random_part}"


def _result(
    state: dict[str, Any],
    *,
    mode: str,
    takeover_status: str,
    human_notice: str,
    agentic_next_action: str,
    work_authorization: str = "",
) -> dict[str, Any]:
    result = {
        "issue_key": state["issue_key"],
        "jira_issue_id": state["jira_issue_id"],
        "run_id": state["run_id"],
        "mode": mode,
        "takeover_status": takeover_status,
        "jira_status": state["jira_status"],
        "human_notice": human_notice,
        "authorization_status": state["authorization_status"],
        "pending_gate": state["pending_gate"],
        "design_digest": state["design_digest"],
        "agentic_next_action": agentic_next_action,
    }
    if work_authorization:
        result["work_authorization"] = work_authorization
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blocked(
    code: str, message: str, required_human_action: str
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action=required_human_action,
    )
