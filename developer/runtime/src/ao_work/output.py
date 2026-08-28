from __future__ import annotations

import json
import hashlib
import re
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

EXIT_FAILED = 1
EXIT_BLOCKED = 2
EXIT_CAPABILITY_GAP = 3

NEXT_STEP_EXECUTORS = frozenset(
    {"ao_work", "ai", "human", "reviewer", "project_tool", "stop"}
)

NEXT_STEP_KINDS = frozenset({"action", "decision", "input", "wait", "none"})
NEXT_STEP_SCOPES = frozenset({"local", "flow"})
NEXT_STEP_MODES = frozenset({"auto", "timed_auto", "manual"})


_SUCCESS_NEXT_STEPS: dict[str, dict[str, Any]] = {
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
        "actor": "human",
        "action": "follow_capability_contract",
        "allowed_operations": [],
    },
    "auth": {
        "actor": "ai",
        "action": "initialize_or_inspect_workspace",
        "allowed_operations": ["workspace_init", "workspace_inspect"],
    },
    "workspace_init": {
        "actor": "ai",
        "action": "takeover_explicit_jira_task",
        "required_inputs": ["issue_key"],
        "allowed_operations": ["takeover"],
    },
    "workspace_inspect": {
        "actor": "ai",
        "action": "takeover_explicit_jira_task",
        "required_inputs": ["issue_key"],
        "allowed_operations": ["takeover"],
    },
    "workspace_preflight": {
        "actor": "ao_work",
        "action": "takeover_explicit_jira_task",
        "required_inputs": ["issue_key"],
        "allowed_operations": ["takeover"],
    },
    "workflow_query": {
        "actor": "stop",
        "action": "workflow_query_complete",
        "allowed_operations": [],
        "stop_workflow": True,
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
        "actor": "ai",
        "action": "takeover_explicit_jira_task",
        "required_inputs": ["issue_key"],
        "allowed_operations": ["takeover"],
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
        "action": "takeover_verified_jira_task",
        "allowed_operations": ["takeover"],
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
    "task-run_probe-ci": {
        "actor": "ao_work",
        "action": "continue_ci_state_machine",
        "allowed_operations": [
            "task-run_probe-ci",
            "task-run_fetch-ci-artifact",
            "task-run_fetch-ci-runner-log",
            "jira_comment_plan",
            "jira_worklog_plan",
            "task-run_probe-jira-write",
        ],
    },
    "task-run_fetch-ci-artifact": {
        "actor": "ao_work",
        "action": "collect_runner_log_for_ci_failure",
        "allowed_operations": ["task-run_fetch-ci-runner-log"],
    },
    "task-run_fetch-ci-runner-log": {
        "actor": "ao_work",
        "action": "parse_combined_ci_evidence",
        "allowed_operations": ["task-run_parse-ci-report"],
    },
    "task-run_parse-ci-report": {
        "actor": "ai",
        "action": "analyze_ci_failure_and_request_user_decision",
        "allowed_operations": ["task-run_authorize-ci-remediation"],
        "requires_authorization": True,
        "stop_workflow": True,
    },
    "task-run_authorize-ci-remediation": {
        "actor": "ai",
        "action": "repair_confirmed_ci_code_and_return_to_pr",
        "allowed_operations": [
            "task-run_record",
            "task-run_verify",
            "task-run_execute-git-commit",
            "task-run_execute-git-push-task-branch",
            "task-run_probe-git",
            "task-run_record-ci-remediation",
        ],
    },
    "task-run_record-ci-remediation": {
        "actor": "ao_work",
        "action": "observe_new_pr_head_ci",
        "allowed_operations": ["task-run_probe-ci"],
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
    next_step: Mapping[str, Any] | None = None


_SUCCESS_RESERVED_FIELDS = frozenset(
    {"schema_version", "ok", "operation", "result", "next_step"}
)
_FAILURE_RESERVED_FIELDS = _SUCCESS_RESERVED_FIELDS | frozenset(
    {"code", "message", "required_human_action"}
)


def _reject_reserved_payload_fields(payload: Mapping[str, Any], *, failure: bool) -> None:
    reserved = _FAILURE_RESERVED_FIELDS if failure else _SUCCESS_RESERVED_FIELDS
    collisions = sorted(reserved & set(payload))
    if collisions:
        raise ValueError(
            "StepResult 业务 payload 不得覆盖保留字段：" + ", ".join(collisions)
        )


def success(operation: str, **payload: Any) -> dict[str, Any]:
    provided_next_step = payload.pop("next_step", None)
    _reject_reserved_payload_fields(payload, failure=False)
    retry_safe = payload.pop("retry_safe", True)
    if not isinstance(retry_safe, bool):
        raise ValueError("StepResult.retry_safe 必须是布尔值")
    operation_status = payload.pop("status", None)
    if operation_status is not None:
        payload["operation_status"] = operation_status
    result: dict[str, Any] = {
        "schema_version": "step-result/v2",
        "ok": True,
        "operation": operation,
        "status": "completed",
        "retry_safe": retry_safe,
    }
    result.update(payload)
    result["result"] = _result_section(operation, result, payload)
    result["next_step"] = _success_next_step(
        operation,
        provided_next_step,
        payload,
    )
    _validate_effect_safety(result["result"], result["next_step"])
    return result


def failure(operation: str, error: RuntimeErrorResult) -> dict[str, Any]:
    details = dict(error.details)
    _reject_reserved_payload_fields(details, failure=True)
    details_retry_safe = details.pop("retry_safe", error.retry_safe)
    if not isinstance(details_retry_safe, bool):
        raise ValueError("StepResult.retry_safe 必须是布尔值")
    operation_status = details.pop("status", None)
    if operation_status is not None:
        details["operation_status"] = operation_status
    result: dict[str, Any] = {
        "schema_version": "step-result/v2",
        "ok": False,
        "operation": operation,
        "status": error.status,
        "code": error.code,
        "retry_safe": details_retry_safe,
        "message": error.message,
        "required_human_action": error.required_human_action,
    }
    result.update(details)
    result["result"] = _result_section(operation, result, details)
    retry_key = hashlib.sha256(
        f"{operation}:{error.code}:{error.status}".encode("utf-8")
    ).hexdigest()
    if error.next_step is not None:
        result["next_step"] = _normalize_next_step(
            error.next_step,
            operation=operation,
            payload=result,
        )
    elif error.retry_safe:
        result["next_step"] = _normalize_next_step({
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
        }, operation=operation, payload=result)
    else:
        result["next_step"] = _normalize_next_step({
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
        }, operation=operation, payload=result)
    _validate_effect_safety(result["result"], result["next_step"])
    return result


def normalize_next_step(
    value: Mapping[str, Any], *, operation: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """供持久化状态使用的 v2 下一步骤归一化入口。"""
    return _normalize_next_step(value, operation=operation, payload=payload)


def _success_next_step(
    operation: str,
    provided_next_step: object,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if provided_next_step is not None and not isinstance(provided_next_step, Mapping):
        raise ValueError("next_step 必须是结构化 Step，不能使用字符串动作")
    if isinstance(provided_next_step, Mapping):
        return _normalize_next_step(
            provided_next_step, operation=operation, payload=payload
        )
    configured = dict(
        _SUCCESS_NEXT_STEPS.get(
            operation,
            {
                "actor": "human",
                "action": "review_operation_result",
                "allowed_operations": [],
                "requires_authorization": True,
                "stop_workflow": True,
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
    if operation == "task-run_probe-ci" and payload.get("ci_status") in {
        "start_timeout",
        "completion_timeout",
    }:
        configured = {
            "actor": "ai",
            "action": "analyze_ci_timeout_and_request_user_decision",
            "required_inputs": [
                "ci_status",
                "pr_url",
                "required_checks",
                "workflow_runs",
            ],
            "allowed_operations": [],
            "requires_authorization": True,
            "stop_workflow": True,
        }
    if operation == "task-run_probe-ci" and payload.get("ci_status") in {
        "passed",
        "not_required",
    }:
        configured = {
            "actor": "stop",
            "action": "ci_completed",
            "allowed_operations": [],
            "stop_workflow": True,
            "kind": "none",
        }
    executor = configured.get("actor")
    if executor not in NEXT_STEP_EXECUTORS:
        raise ValueError(f"unsupported next step executor: {executor}")
    next_step: dict[str, Any] = {
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
    return _normalize_next_step(next_step, operation=operation, payload=payload)


@dataclass(frozen=True)
class Step(ABC):
    """步骤结果中唯一的、可判别的下一步骤。

    所有 Runtime 入口都通过此类生成 `next_step`，不能再手工拼装对外 JSON。
    """

    kind: str
    scope: str
    mode: str
    executor: str
    action: str
    call: Mapping[str, Any] | None
    _data: Mapping[str, Any]

    @property
    def can_auto_execute(self) -> bool:
        return self.kind == "action" and self.mode == "auto"

    @property
    def requires_human_input(self) -> bool:
        return self.kind in {"decision", "input"} or self.mode == "manual"

    @property
    def is_timed_auto(self) -> bool:
        return self.mode == "timed_auto"

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    @abstractmethod
    def _validate_variant(self) -> None:
        """校验具体步骤类型特有的不变量。"""

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        operation: str,
        payload: Mapping[str, Any],
    ) -> "Step":
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
            raise ValueError("next_step 缺少固定控制字段")
        executor = value["executor"]
        if executor not in NEXT_STEP_EXECUTORS:
            raise ValueError(f"unsupported next step executor: {executor}")
        if value["ownership_effect"] != "none":
            raise ValueError("当前版本不允许下一动作改变任务负责人")
        if not isinstance(value["action"], str) or not value["action"].strip():
            raise ValueError("next_step.action 无效")
        if not all(
            isinstance(value[field], list)
            and all(isinstance(item, str) and item for item in value[field])
            for field in ("required_inputs", "allowed_operations")
        ):
            raise ValueError("next_step 列表字段无效")
        normalized = dict(value)
        operation_id = normalized.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.strip():
            allowed = normalized["allowed_operations"]
            operation_id = allowed[0] if allowed else "human_decision"
        command_argv = normalized.get("command_argv")
        if not isinstance(command_argv, list) or not all(
            isinstance(item, str) and item for item in command_argv
        ):
            command_argv = _command_argv(str(operation_id), payload)
        input_artifacts = normalized.get("input_artifacts")
        if not isinstance(input_artifacts, list):
            input_artifacts = _input_artifacts(normalized["required_inputs"], payload)
        command_line = normalized.get("command_line")
        if not isinstance(command_line, str) or not command_line.strip():
            command_line = (
                f"ao-work {' '.join(command_argv)}"
                if command_argv
                else "无需命令；请按 required_inputs 完成人工确认或选择"
            )
        bound_arguments = normalized.get("bound_arguments")
        if not isinstance(bound_arguments, dict):
            bound_arguments = {
                key: str(payload[key])
                for key in ("issue_key", "agentic_run_id")
                if payload.get(key) is not None
            }
        normalized.update(
            {
                "operation_id": operation_id,
                "command_argv": command_argv,
                "command_line": command_line,
                "bound_arguments": bound_arguments,
                "input_artifacts": input_artifacts,
                "reason": str(normalized.get("reason") or normalized["action"]),
            }
        )
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
        missing_inputs = [
            item for item in normalized["required_inputs"] if item not in payload
        ]
        kind = normalized.get("kind")
        if kind is None:
            if normalized["executor"] == "stop":
                kind = "none"
            elif "wait" in str(normalized["action"]):
                kind = "wait"
            elif missing_inputs:
                kind = "input"
            elif normalized["requires_authorization"] or normalized["executor"] in {"human", "reviewer"}:
                kind = "decision"
            elif normalized["stop_workflow"]:
                kind = "none"
            else:
                kind = "action"
        if kind not in NEXT_STEP_KINDS:
            raise ValueError("next_step.kind 不受支持")
        scope = normalized.get("scope") or _next_step_scope(operation, normalized)
        if scope not in NEXT_STEP_SCOPES:
            raise ValueError("next_step.scope 不受支持")
        mode = normalized.get("mode") or (
            "manual" if kind in {"decision", "input", "none"} else "auto"
        )
        if mode not in NEXT_STEP_MODES:
            raise ValueError("next_step.mode 不受支持")
        if mode == "auto" and normalized["requires_authorization"]:
            raise ValueError("需要授权的 next_step 不得使用 auto")
        call: Mapping[str, Any] | None = {
            "operation": operation_id,
            "argv": command_argv,
            "cwd": normalized.get("cwd", "workspace"),
            "needs": input_artifacts,
            "bind": bound_arguments,
        }
        normalized.update({"kind": kind, "scope": scope, "mode": mode, "call": call})
        if kind == "input":
            normalized["inputs"] = [
                {"id": item, "label": item, "required": True}
                for item in missing_inputs
            ]
        if kind == "decision":
            _add_decision_fields(normalized)
        if kind == "decision" and mode == "timed_auto":
            transitions = normalized.get("transitions")
            timed_data = normalized.get("timed")
            if isinstance(transitions, Mapping):
                normalized["transitions"] = {
                    str(choice_id): Step.from_mapping(
                        transition,
                        operation=(
                            "timed_transition_"
                            f"{timed_data.get('decision_id', 'unknown') if isinstance(timed_data, Mapping) else 'unknown'}"
                            f"_{choice_id}"
                        ),
                        payload={},
                    ).to_dict()
                    for choice_id, transition in transitions.items()
                    if isinstance(transition, Mapping)
                }
        if kind == "wait":
            wait_for = normalized.get("wait_for")
            if not isinstance(wait_for, str) or not wait_for.strip():
                raise ValueError("wait 类型 next_step 必须提供 wait_for")
        if kind == "none":
            normalized.update(
                {
                    "executor": "stop",
                    "mode": "manual",
                    "stop_workflow": True,
                    "allowed_operations": [],
                    "operation_id": "none",
                    "command_argv": [],
                    "command_line": "无需命令；当前步骤为终态",
                    "call": None,
                }
            )
            call = None
        step_type: type[Step]
        if kind == "action":
            step_type = ActionStep
        elif kind == "decision" and mode == "timed_auto":
            step_type = TimedDecisionStep
        elif kind == "decision":
            step_type = DecisionStep
        elif kind == "input":
            step_type = InputStep
        elif kind == "wait":
            step_type = WaitStep
        else:
            step_type = TerminalStep
        step = step_type(
            kind=kind,
            scope=scope,
            mode=mode,
            executor=str(normalized["executor"]),
            action=str(normalized["action"]),
            call=call,
            _data=normalized,
        )
        step._validate_variant()
        return step


@dataclass(frozen=True)
class ActionStep(Step):
    def _validate_variant(self) -> None:
        if self.mode != "auto" or not isinstance(self.call, Mapping):
            raise ValueError("ActionStep 必须使用 auto 并提供可执行 call")
        operation = self.call.get("operation")
        if operation not in self._data["allowed_operations"]:
            raise ValueError("ActionStep.call.operation 必须在 allowed_operations 中")


@dataclass(frozen=True)
class DecisionStep(Step):
    def _validate_variant(self) -> None:
        if self.mode != "manual":
            raise ValueError("DecisionStep 必须使用 manual")
        _add_decision_fields(dict(self._data))


@dataclass(frozen=True)
class TimedDecisionStep(Step):
    def _validate_variant(self) -> None:
        _validate_timed_auto(self._data)


@dataclass(frozen=True)
class InputStep(Step):
    def _validate_variant(self) -> None:
        if self.mode != "manual" or not self._data.get("inputs"):
            raise ValueError("InputStep 必须使用 manual 并声明 inputs")


@dataclass(frozen=True)
class WaitStep(Step):
    def _validate_variant(self) -> None:
        if not isinstance(self._data.get("wait_for"), str):
            raise ValueError("WaitStep 必须声明 wait_for")


@dataclass(frozen=True)
class TerminalStep(Step):
    def _validate_variant(self) -> None:
        if self.executor != "stop" or self.mode != "manual" or self.call is not None:
            raise ValueError("TerminalStep 必须由 stop 以 manual 终止，且没有 call")


def _normalize_next_step(
    value: Mapping[str, Any], *, operation: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    return Step.from_mapping(value, operation=operation, payload=payload).to_dict()


def _next_step_scope(operation: str, step: Mapping[str, Any]) -> str:
    operation_id = str(step["operation_id"])
    if operation_id.startswith(("auth_", "workspace_", "task_state_")):
        return "local"
    if operation.startswith(("auth_", "workspace_", "task_inspect", "task_resume")):
        return "local"
    return "flow"


def _add_decision_fields(step: dict[str, Any]) -> None:
    question = step.get("question")
    if not isinstance(question, str) or not question.strip():
        question = str(step["reason"])
    choices = step.get("choices")
    if not isinstance(choices, list) or not choices:
        choices = [
            {
                "id": "continue",
                "label": "确认并继续",
                "description": str(step["action"]),
                "impact": "进入已声明的下一步，不直接执行后续业务动作",
                "risk": "请在提交前核对当前事实、授权范围和副作用",
                "recommended": True,
            }
        ]
    if not all(
        isinstance(choice, Mapping)
        and all(isinstance(choice.get(field), str) and choice[field].strip() for field in ("id", "label", "description", "impact", "risk"))
        and isinstance(choice.get("recommended"), bool)
        for choice in choices
    ):
        raise ValueError("decision 类型 next_step.choices 无效")
    if sum(bool(choice["recommended"]) for choice in choices) != 1:
        raise ValueError("decision 类型 next_step 必须有且仅有一个推荐选项")
    step["question"] = question
    step["choices"] = [dict(choice) for choice in choices]
    step["submit"] = {"operation": "submit_decision", "effect": "record_only"}


def _validate_timed_auto(step: Mapping[str, Any]) -> None:
    timed = step.get("timed")
    if not isinstance(timed, Mapping):
        raise ValueError("timed_auto 类型 next_step 必须提供 timed")
    required = {
        "decision_id",
        "deadline",
        "default_choice",
        "cancel_if",
        "fact_bind",
        "policy",
    }
    if required - set(timed):
        raise ValueError("timed_auto.next_step 缺少超时解析字段")
    if not all(isinstance(timed[field], str) and timed[field].strip() for field in required):
        raise ValueError("timed_auto.next_step 超时解析字段无效")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", timed["decision_id"]):
        raise ValueError("timed_auto.next_step.decision_id 无效")
    if timed["cancel_if"] != "fact_binding_changed":
        raise ValueError("timed_auto.next_step.cancel_if 当前仅支持 fact_binding_changed")
    if step.get("kind") != "decision":
        raise ValueError("timed_auto.next_step 必须是 decision 类型")
    choices = step.get("choices")
    if not isinstance(choices, list) or timed["default_choice"] not in {
        choice.get("id") for choice in choices if isinstance(choice, Mapping)
    }:
        raise ValueError("timed_auto.next_step.default_choice 必须引用已有决策选项")
    transitions = step.get("transitions")
    if not isinstance(transitions, Mapping):
        raise ValueError("timed_auto.next_step 必须声明每个选项的后续 ActionStep")
    choice_ids = {choice.get("id") for choice in choices if isinstance(choice, Mapping)}
    if set(transitions) != choice_ids:
        raise ValueError("timed_auto.next_step.transitions 必须覆盖且仅覆盖全部决策选项")
    for choice_id, transition in transitions.items():
        if not isinstance(transition, Mapping):
            raise ValueError(f"timed_auto.next_step.transitions.{choice_id} 无效")
        target = Step.from_mapping(
            transition,
            operation=f"timed_transition_{timed['decision_id']}_{choice_id}",
            payload={},
        )
        if not target.can_auto_execute:
            raise ValueError("timed_auto 选项只能转换到 auto ActionStep")


def _result_section(
    operation: str, result: Mapping[str, Any], details: Mapping[str, Any]
) -> dict[str, Any]:
    facts = details.get("facts")
    if not isinstance(facts, Mapping):
        facts = {
            key: value
            for key, value in details.items()
            if key not in {"evidence", "effects", "remaining"}
        }
    evidence = details.get("evidence", [])
    effects = details.get("effects", [])
    remaining = details.get("remaining", [])
    if not isinstance(evidence, list) or not isinstance(effects, list) or not isinstance(remaining, list):
        raise ValueError("StepResult 的 evidence、effects 和 remaining 必须是列表")
    if not all(
        isinstance(effect, Mapping)
        and all(
            isinstance(effect.get(field), str) and effect[field].strip()
            for field in ("kind", "target", "state", "evidence")
        )
        and effect["state"] in {"verified", "failed", "uncertain"}
        for effect in effects
    ):
        raise ValueError("StepResult.effects 必须包含 kind、target、state 和 evidence")
    summary = result.get("message")
    if not isinstance(summary, str) or not summary.strip():
        summary = f"{operation} 已{'完成' if result.get('ok') else '返回结果'}"
    return {
        "status": _result_status(result),
        "summary": summary,
        "facts": dict(facts),
        "evidence": evidence,
        "effects": effects,
        "remaining": remaining,
    }


def _validate_effect_safety(
    result: Mapping[str, Any], step: Mapping[str, Any]
) -> None:
    """外部副作用不确定时，只允许本地核验/恢复或显式人工处理。"""
    uncertain = any(
        isinstance(effect, Mapping) and effect.get("state") == "uncertain"
        for effect in result["effects"]
    )
    if uncertain and step["mode"] == "auto":
        if step["scope"] != "local" or step["kind"] != "action":
            raise ValueError("副作用不确定时不得自动推进业务流程")


def workflow_query(
    workflow_id: str, *, current_step_id: str, steps: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """返回只读流程导航；其中任何节点都不能作为 Runtime 调用指令。"""
    if not isinstance(workflow_id, str) or not workflow_id.strip():
        raise ValueError("WorkflowQuery.workflow_id 无效")
    if not isinstance(current_step_id, str) or not current_step_id.strip():
        raise ValueError("WorkflowQuery.current_step_id 无效")
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for step in steps:
        if not isinstance(step, Mapping):
            raise ValueError("WorkflowQuery.steps 必须是对象列表")
        value = dict(step)
        if any(field in value for field in ("call", "command_argv", "allowed_operations")):
            raise ValueError("WorkflowQuery 不能携带可执行调用信息")
        if not all(
            isinstance(value.get(field), str) and value[field].strip()
            for field in ("id", "label", "kind")
        ):
            raise ValueError("WorkflowQuery.steps 缺少 id、label 或 kind")
        if value["id"] in ids:
            raise ValueError("WorkflowQuery.steps.id 不得重复")
        ids.add(value["id"])
        normalized.append(value)
    if current_step_id not in ids:
        raise ValueError("WorkflowQuery.current_step_id 必须指向已有步骤")
    return {
        "schema_version": "workflow-query/v1",
        "workflow_id": workflow_id,
        "current_step_id": current_step_id,
        "executable": False,
        "steps": normalized,
    }


def _result_status(result: Mapping[str, Any]) -> str:
    status = result.get("status")
    if result.get("ok") is True:
        return "succeeded"
    if status in {"partial", "blocked", "waiting", "uncertain", "terminal"}:
        return str(status)
    return "failed"


def _command_argv(operation_id: str, payload: Mapping[str, Any]) -> list[str]:
    """只从已版本化的命令命名规则生成下一步入口，未知或需人工选择时不猜测。"""
    aliases = {
        "takeover_task": ("takeover",),
        "takeover": ("takeover",),
        "repository_branch_assess": ("task", "repositories", "assess"),
        "repository_branch_confirm": ("task", "repositories", "confirm"),
        "task_repositories_confirm": ("task", "repositories", "confirm"),
        "task_worktree_prepare": ("task", "worktrees", "prepare"),
        "task_worktrees_prepare": ("task", "worktrees", "prepare"),
        "task_worktree_cleanup": ("task", "worktrees", "cleanup"),
        "task_worktree_recover": ("task", "worktrees", "recover"),
        "task_intake_assess": ("task", "intake", "assess"),
        "task_solution_classify": ("task", "solution", "classify"),
        "task_run_manifest": ("task-run", "prepare"),
        "task_run_authorize": ("task-run", "authorize"),
        "task_start": ("task", "start"),
        "task_state_inspect": ("task", "inspect"),
        "resume_takeover": ("task", "resume"),
        "read_task_facts": ("task", "facts"),
        "workspace_init": ("workspace", "init"),
        "workspace_inspect": ("workspace", "inspect"),
        "workspace_preflight": ("workspace", "preflight"),
        "capability_show": ("capability", "show"),
        "report_write": ("report", "write"),
    }
    path = aliases.get(operation_id)
    if path is None and operation_id.startswith("task-run_"):
        path = ("task-run", operation_id.removeprefix("task-run_").replace("_", "-"))
    if path is None and operation_id.startswith("jira_"):
        parts = operation_id.split("_")
        path = tuple(["jira", *parts[1:]])
    if path is None:
        return []
    argv = list(path)
    issue_key = payload.get("issue_key")
    if path == ("takeover",):
        argv.append(str(issue_key) if issue_key else "<issue-key>")
    elif path[:1] == ("task",) and path[1:] in {
        ("repositories", "assess"),
        ("repositories", "confirm"),
        ("worktrees", "prepare"),
        ("worktrees", "cleanup"),
        ("intake", "assess"),
        ("solution", "classify"),
        ("facts",),
        ("inspect",),
    }:
        argv.extend(("--issue-key", str(issue_key) if issue_key else "<issue-key>"))
    elif path == ("task-run", "prepare"):
        argv.extend(("--issue-key", str(issue_key) if issue_key else "<issue-key>"))
    return argv


def _input_artifacts(required_inputs: list[str], payload: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "kind": item,
            "source": f"result.{item}" if item in payload else f"user_input.{item}",
        }
        for item in required_inputs
    ]


def write_json(result: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def write_diagnostic(message: str) -> None:
    sys.stderr.write(f"AgenticOps：{message}\n")
    sys.stderr.flush()
