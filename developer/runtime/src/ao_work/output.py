from __future__ import annotations

import json
import hashlib
import sys
from dataclasses import dataclass, field
from typing import Any, Mapping

EXIT_FAILED = 1
EXIT_BLOCKED = 2
EXIT_CAPABILITY_GAP = 3

NEXT_ACTION_ACTORS = frozenset(
    {"ao_work", "ai", "human", "reviewer", "project_tool", "stop"}
)


_SUCCESS_NEXT_ACTIONS: dict[str, dict[str, Any]] = {
    "help": {
        "actor": "human",
        "action": "select_ao_work_operation",
        "allowed_operations": [],
    },
    "capability_list": {
        "actor": "ai",
        "action": "select_implemented_capability",
        "allowed_operations": ["capability_show"],
    },
    "capability_show": {
        "actor": "ai",
        "action": "follow_capability_contract",
        "allowed_operations": [],
    },
    "workspace_init": {
        "actor": "ao_work",
        "action": "verify_workspace_preflight",
        "allowed_operations": ["workspace_preflight"],
    },
    "workspace_inspect": {
        "actor": "ao_work",
        "action": "verify_workspace_preflight",
        "allowed_operations": ["workspace_preflight"],
    },
    "workspace_preflight": {
        "actor": "ao_work",
        "action": "start_explicit_jira_task",
        "required_inputs": ["issue_key"],
        "allowed_operations": ["task_start"],
    },
    "auth_jira_list": {
        "actor": "human",
        "action": "select_workspace_jira_connection",
        "allowed_operations": ["auth_jira_show", "auth_jira_set"],
    },
    "auth_jira_show": {
        "actor": "human",
        "action": "complete_or_verify_jira_authorization",
        "allowed_operations": ["auth_jira_set", "auth_jira_verify"],
    },
    "auth_jira_set": {
        "actor": "ao_work",
        "action": "verify_jira_authorization",
        "allowed_operations": ["auth_jira_verify"],
    },
    "auth_jira_remove": {
        "actor": "stop",
        "action": "jira_authorization_removed",
        "allowed_operations": [],
        "stop_workflow": True,
    },
    "auth_jira_verify": {
        "actor": "ao_work",
        "action": "verify_workspace_preflight",
        "allowed_operations": ["workspace_preflight"],
    },
    "task_start": {
        "actor": "ai",
        "action": "assess_task_intake",
        "required_inputs": [
            "issue_key",
            "agentic_run_id",
            "intake_input_file",
        ],
        "allowed_operations": ["task_intake_assess"],
    },
    "task_intake_assess": {
        "actor": "ai",
        "action": "prepare_and_classify_solution",
        "required_inputs": ["intake_digest", "solution_input_file"],
        "allowed_operations": ["task_solution_classify"],
    },
    "task_solution_classify": {
        "actor": "human",
        "action": "review_task_design_or_risk_decision",
        "required_inputs": ["solution_level", "solution_digest", "proposed_solution"],
        "allowed_operations": [],
        "requires_authorization": True,
        "stop_workflow": True,
    },
    "task_init": {
        "actor": "ai",
        "action": "analyze_task",
        "allowed_operations": ["report_write"],
    },
    "task_inspect": {
        "actor": "ai",
        "action": "resume_task_from_recorded_state",
        "allowed_operations": ["report_write", "task-run_open"],
    },
    "task_resume": {
        "actor": "ai",
        "action": "resume_task_from_recorded_state",
        "allowed_operations": ["report_write", "task-run_open"],
    },
    "jira_inspect": {
        "actor": "ai",
        "action": "analyze_verified_jira_facts",
        "allowed_operations": ["task_start"],
    },
    "jira_comment_plan": {
        "actor": "human",
        "action": "review_jira_comment_plan",
        "required_inputs": ["plan_id", "plan_file", "content_sha256"],
        "allowed_operations": ["jira_comment_apply"],
        "requires_authorization": True,
    },
    "jira_comment_apply": {
        "actor": "ao_work",
        "action": "read_back_jira_comment",
        "allowed_operations": ["jira_comment_readback"],
    },
    "jira_comment_readback": {
        "actor": "ai",
        "action": "continue_from_verified_jira_comment",
        "allowed_operations": ["task-run_probe-jira-write"],
    },
    "jira_worklog_plan": {
        "actor": "human",
        "action": "review_jira_worklog_plan",
        "required_inputs": ["plan_id", "plan_file", "content_sha256"],
        "allowed_operations": ["jira_worklog_apply"],
        "requires_authorization": True,
    },
    "jira_worklog_apply": {
        "actor": "ao_work",
        "action": "read_back_jira_worklog",
        "allowed_operations": ["jira_worklog_readback"],
    },
    "jira_worklog_readback": {
        "actor": "ai",
        "action": "continue_from_verified_jira_worklog",
        "allowed_operations": ["task-run_probe-jira-write"],
    },
    "jira_description_plan": {
        "actor": "human",
        "action": "review_jira_description_plan",
        "required_inputs": ["plan_id", "plan_file", "content_sha256"],
        "allowed_operations": ["jira_description_apply"],
        "requires_authorization": True,
    },
    "jira_description_apply": {
        "actor": "ai",
        "action": "continue_from_verified_description_update",
        "allowed_operations": ["task-run_record"],
    },
    "report_write": {
        "actor": "ai",
        "action": "continue_task_reasoning_or_open_run",
        "allowed_operations": ["report_write", "task-run_open"],
    },
    "task-run_open": {
        "actor": "ao_work",
        "action": "capture_prohibition_baseline",
        "allowed_operations": ["task-run_probe-prohibition-baseline"],
    },
    "task-run_probe-prohibition-baseline": {
        "actor": "ao_work",
        "action": "read_jira_task_facts",
        "allowed_operations": ["task-run_probe-jira"],
    },
    "task-run_probe-jira": {
        "actor": "ai",
        "action": "execute_approved_implementation_plan",
        "allowed_operations": ["task-run_record", "task-run_verify"],
    },
    "task-run_record": {
        "actor": "ai",
        "action": "continue_from_recorded_event",
        "allowed_operations": ["task-run_record", "task-run_verify"],
    },
    "task-run_verify": {
        "actor": "ai",
        "action": "resolve_verification_or_continue_delivery",
        "allowed_operations": ["task-run_record", "task-run_probe-git"],
    },
    "task-run_probe-git": {
        "actor": "ao_work",
        "action": "read_pull_request_facts",
        "allowed_operations": ["task-run_probe-pr"],
    },
    "task-run_probe-pr": {
        "actor": "ao_work",
        "action": "write_and_verify_jira_delivery_evidence",
        "allowed_operations": [
            "jira_comment_plan",
            "jira_worklog_plan",
            "task-run_probe-jira-write",
        ],
    },
    "task-run_probe-jira-write": {
        "actor": "ao_work",
        "action": "verify_prohibited_actions",
        "allowed_operations": ["task-run_probe-prohibitions"],
    },
    "task-run_probe-prohibitions": {
        "actor": "ai",
        "action": "complete_retrospective_and_finalize",
        "allowed_operations": ["task-run_record", "task-run_finalize"],
    },
    "task-run_record-unverified-prohibitions": {
        "actor": "ai",
        "action": "finalize_blocked_or_failed_run",
        "allowed_operations": ["task-run_record", "task-run_finalize"],
    },
    "task-run_finalize": {
        "actor": "stop",
        "action": "stop_at_pr_review_or_report_blocker",
        "allowed_operations": [],
        "stop_workflow": True,
    },
}


@dataclass(frozen=True)
class RuntimeErrorResult(Exception):
    code: str
    message: str
    status: str = "failed"
    exit_code: int = EXIT_FAILED
    retry_safe: bool = False
    required_human_action: str = "请联系 AgenticOps 维护者处理"
    details: Mapping[str, Any] = field(default_factory=dict)


def success(operation: str, **payload: Any) -> dict[str, Any]:
    legacy_next_action = payload.pop("agentic_next_action", None)
    result: dict[str, Any] = {
        "ok": True,
        "operation": operation,
        "status": "completed",
        "retry_safe": True,
    }
    result.update(payload)
    result["agentic_next_action"] = _success_next_action(
        operation,
        legacy_next_action,
        payload,
    )
    return result


def failure(operation: str, error: RuntimeErrorResult) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "operation": operation,
        "status": error.status,
        "code": error.code,
        "retry_safe": error.retry_safe,
        "message": error.message,
        "required_human_action": error.required_human_action,
    }
    result.update(error.details)
    retry_key = hashlib.sha256(
        f"{operation}:{error.code}:{error.status}".encode("utf-8")
    ).hexdigest()
    if error.retry_safe:
        result["agentic_next_action"] = {
            "executor": "ai",
            "action": "inspect_state_and_retry_once",
            "required_inputs": ["code", "message", "required_human_action"],
            "allowed_operations": [operation],
            "requires_authorization": False,
            "stop_workflow": False,
            "ownership_effect": "none",
            "reason": error.required_human_action,
            "retry_gate": {
                "allowed": True,
                "retry_key": retry_key,
                "max_additional_attempts": 1,
                "same_input_allowed": False,
                "requires_state_readback": True,
                "requires_recorded_retry_event": True,
                "on_exhausted": "escalate_to_human",
            },
        }
    else:
        result["agentic_next_action"] = {
            "executor": "human",
            "action": "resolve_runtime_blocker",
            "required_inputs": [],
            "allowed_operations": [],
            "requires_authorization": True,
            "stop_workflow": True,
            "ownership_effect": "none",
            "reason": error.required_human_action,
            "retry_gate": {
                "allowed": False,
                "retry_key": retry_key,
                "max_additional_attempts": 0,
                "same_input_allowed": False,
                "requires_state_readback": True,
                "requires_recorded_retry_event": False,
                "on_exhausted": "escalate_to_human",
            },
        }
    return result


def _success_next_action(
    operation: str,
    legacy_next_action: object,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if isinstance(legacy_next_action, Mapping):
        return _normalize_next_action(legacy_next_action)
    configured = dict(
        _SUCCESS_NEXT_ACTIONS.get(
            operation,
            {
                "actor": "ai",
                "action": "inspect_operation_result",
                "allowed_operations": [],
            },
        )
    )
    if operation == "auth_jira_set" and payload.get("ready") is not True:
        configured = {
            "actor": "human",
            "action": "complete_jira_authorization",
            "allowed_operations": ["auth_jira_set"],
        }
    if operation == "task-run_verify":
        verification_status = payload.get("verification_status")
        if verification_status in {"failed", "blocked"}:
            configured = {
                "actor": "ai",
                "action": "record_failure_fix_and_retry_verification",
                "required_inputs": [
                    "verification_status",
                    "exit_code",
                    "event_id",
                ],
                "allowed_operations": ["task-run_record", "task-run_verify"],
                "retry_gate": {
                    "allowed": True,
                    "max_additional_attempts": 1,
                    "same_input_allowed": False,
                    "requires_state_readback": True,
                    "requires_recorded_retry_event": True,
                    "on_exhausted": "escalate_to_human",
                },
            }
    executor = configured.get("actor")
    if executor not in NEXT_ACTION_ACTORS:
        raise ValueError(f"unsupported next action executor: {executor}")
    next_action: dict[str, Any] = {
        "executor": executor,
        "action": configured["action"],
        "required_inputs": list(configured.get("required_inputs", [])),
        "allowed_operations": list(configured.get("allowed_operations", [])),
        "requires_authorization": bool(
            configured.get("requires_authorization", False)
        ),
        "stop_workflow": bool(configured.get("stop_workflow", False)),
        "ownership_effect": "none",
        "retry_gate": dict(
            configured.get(
                "retry_gate",
                {
                    "allowed": False,
                    "max_additional_attempts": 0,
                    "same_input_allowed": False,
                    "requires_state_readback": False,
                    "requires_recorded_retry_event": False,
                    "on_exhausted": "not_applicable",
                },
            )
        ),
    }
    if isinstance(legacy_next_action, str) and legacy_next_action.strip():
        next_action["reason"] = legacy_next_action.strip()
    return next_action


def _normalize_next_action(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "executor",
        "action",
        "required_inputs",
        "allowed_operations",
        "requires_authorization",
        "stop_workflow",
        "ownership_effect",
    }
    if not required <= set(value):
        raise ValueError("agentic_next_action 缺少固定控制字段")
    executor = value["executor"]
    if executor not in NEXT_ACTION_ACTORS:
        raise ValueError(f"unsupported next action executor: {executor}")
    if value["ownership_effect"] != "none":
        raise ValueError("当前版本不允许下一动作改变任务负责人")
    if not isinstance(value["action"], str) or not value["action"].strip():
        raise ValueError("agentic_next_action.action 无效")
    if not all(
        isinstance(value[field], list)
        and all(isinstance(item, str) and item for item in value[field])
        for field in ("required_inputs", "allowed_operations")
    ):
        raise ValueError("agentic_next_action 列表字段无效")
    normalized = dict(value)
    normalized.setdefault(
        "retry_gate",
        {
            "allowed": False,
            "max_additional_attempts": 0,
            "same_input_allowed": False,
            "requires_state_readback": False,
            "requires_recorded_retry_event": False,
            "on_exhausted": "not_applicable",
        },
    )
    return normalized


def write_json(result: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def write_diagnostic(message: str) -> None:
    sys.stderr.write(f"AgenticOps：{message}\n")
    sys.stderr.flush()
