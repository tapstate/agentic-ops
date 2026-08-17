from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ao_work.config import load_jira_context, validate_workspace_jira_binding
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.model import plain_text
from ao_work.jira.service import JiraService
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_gate import record_task_start_context
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.task_state.io import read_json
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
    workspace_defaults = {
        "agent_id": agent_config.get("agent_id"),
        "project_profile": context.profile.profile_id,
        "connection_id": context.connection.connection_id,
        "jira_base_url": context.connection.base_url,
        "jira_account_id": account["account_id"],
        "repository": context.profile.default_repository,
        "source_root": agent_config.get("source_root"),
        "execution_identity": agent_config.get("execution_identity"),
    }
    profile_snapshot = _profile_snapshot(context.profile, issue)
    intake_source = record_task_start_context(
        workspace,
        store,
        issue_key=issue.key,
        agentic_run_id=agentic_run_id,
        issue=issue_payload,
        workspace_defaults=workspace_defaults,
        project_profile=profile_snapshot,
    )
    return {
        "issue": issue_payload,
        "workspace_defaults": workspace_defaults,
        "agentic_run_id": agentic_run_id,
        "task_state_created": task_state_created,
        "intake_source": intake_source,
        "configuration_sources": {
            "workspace": ["agent_id", "project_profile", "Jira 账户", "源码仓库", "执行身份"],
            "project_profile": ["Jira 站点", "Project", "状态/字段映射", "默认仓库"],
            "jira_issue": ["Issue ID", "经办人", "状态", "标题", "描述", "任务类型"],
            "runtime": ["agentic_run_id", "issue_content_sha256"],
        },
        "review_required": [
            "信息分析、自动补全来源与仍未解决的缺项",
            "AI 提议的实施计划与非范围",
            "任务分支与目标分支",
            "验证命令与超时",
            "本次 Jira、Git、GitHub 外部动作权限",
            "与批准计划摘要绑定的任务级授权",
        ],
        "intake_gate": {
            "stage": "information_analysis",
            "required_sequence": [
                "analyze_jira_and_project_context",
                "identify_missing_information",
                "auto_fill_from_verified_sources",
                "present_filled_intake_for_user_confirmation",
            ],
            "auto_fill_source_priority": [
                "jira_issue",
                "project_profile",
                "business_source_code",
                "runtime_readback",
            ],
            "auto_fill_requires_evidence": True,
            "unresolved_required_information_blocks": True,
            "user_confirmation_required_before_solution": True,
        },
        "solution_gate": {
            "levels": {
                "L1": {
                    "action": "start_implementation",
                    "meaning": "信息完整、范围明确、沿用既有设计且风险在已授权边界内",
                },
                "L2": {
                    "action": "request_confirmation",
                    "meaning": "方案可执行，但包含需要用户选择、外部副作用或非平凡风险",
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
            "reason": "先由 Runtime 校验缺项、证据化补全和摘要 digest；确认完整准入摘要后才能形成方案",
        },
    }


def _profile_snapshot(profile: Any, issue: Any) -> dict[str, Any]:
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
            value = profile.default_repository
            reference = "workspace_defaults.repository"
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


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )
