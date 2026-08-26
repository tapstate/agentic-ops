from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Mapping

from ao_work.jira.adf import markdown_to_adf
from ao_work.jira.model import plain_text
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult

TAKEOVER_SCHEMA_VERSION = 2
TAKEOVER_KINDS = frozenset(
    {"new_takeover", "accept_existing_task", "resume_takeover"}
)
TAKEOVER_PHASES = (
    "intent_persisted",
    "comment_verified",
    "status_verified",
    "local_finalized",
)
TAKEOVER_RESULTS = frozenset({"in_progress", "uncertain", "blocked", "completed"})
EXTERNAL_CERTAINTIES = frozenset(
    {"not_attempted", "verified", "absent", "uncertain", "conflict"}
)
TAKEOVER_EVENTS = frozenset(
    {
        "takeover_intent_created",
        "takeover_comment_verified",
        "takeover_status_verified",
        "takeover_recovered",
        "takeover_completed",
        "takeover_blocked",
    }
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
OPERATION_ID = re.compile(r"^takeover-[0-9a-f]{24}$")

_REQUIRED_OPERATION_FIELDS = frozenset(
    {
        "schema_version",
        "operation_id",
        "issue_key",
        "agentic_run_id",
        "agent_id",
        "takeover_kind",
        "authorization_digest",
        "preflight_facts_sha256",
        "jira_status_before",
        "jira_status_target",
        "transition_id",
        "comment_marker",
        "comment_content_sha256",
        "comment_id",
        "comment_author",
        "comment_author_verified",
        "status_after",
        "phase",
        "result",
        "external_result_certainty",
        "takeover_status",
        "human_notice",
        "agentic_next_action",
        "failure_code",
        "retry_safe",
        "recovery_action",
        "planned_at",
        "updated_at",
        "content_version",
    }
)
_OPTIONAL_OPERATION_FIELDS = frozenset({"comment_markdown"})


def takeover_error(
    code: str,
    message: str,
    action: str,
    *,
    retry_safe: bool = False,
    **details: Any,
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=retry_safe,
        required_human_action=action,
        details=details,
    )


def stable_takeover_operation_id(
    issue_key: str,
    agentic_run_id: str,
    authorization_digest: str,
) -> str:
    digest = hashlib.sha256(
        f"{issue_key}\0{agentic_run_id}\0{authorization_digest}".encode("utf-8")
    ).hexdigest()[:24]
    return f"takeover-{digest}"


def human_notice(takeover_kind: str, result: str) -> str:
    notices = {
        "new_takeover": {
            "in_progress": "正在执行新接管。",
            "uncertain": "新接管的外部结果不确定，已停止自动重试。",
            "blocked": "新接管存在事实冲突，已停止。",
            "completed": "已完成新接管。",
        },
        "accept_existing_task": {
            "in_progress": "正在接纳存量任务；这不是新接管。",
            "uncertain": "接纳存量任务的外部结果不确定；这不是新接管，已停止自动重试。",
            "blocked": "接纳存量任务存在事实冲突；这不是新接管，已停止。",
            "completed": "已接纳存量任务；这不是新接管。",
        },
        "resume_takeover": {
            "in_progress": "正在恢复既有运行；这不是新接管。",
            "uncertain": "恢复既有运行的外部结果不确定；这不是新接管，已停止自动重试。",
            "blocked": "恢复既有运行存在事实冲突；这不是新接管，已停止。",
            "completed": "已恢复当前工作空间的既有运行；这不是新接管。",
        },
    }
    by_result = notices.get(takeover_kind)
    if by_result is None:
        raise takeover_error(
            "takeover_schema_invalid",
            "接管类型不受支持",
            "请使用 Runtime 定义的接管类型，不要临场扩展",
        )
    return by_result[result]


def takeover_next_action(
    action: str,
    *,
    issue_key: str | None = None,
    executor: str = "ao_work",
    stop_workflow: bool = False,
    requires_authorization: bool = False,
    reason: str,
) -> dict[str, Any]:
    if not issue_key:
        raise ValueError("接管下一步必须绑定 issue_key")
    if action == "assess_repository_branch_mapping":
        command_argv = [
            "task",
            "repositories",
            "assess",
            "--issue-key",
            issue_key,
        ]
        return {
            "executor": executor,
            "action": action,
            "operation_id": "repository_branch_assess",
            "command_argv": command_argv,
            "command_line": f"ao-work {' '.join(command_argv)}",
            "bound_arguments": {"issue_key": issue_key},
            "required_inputs": [],
            "input_artifacts": [],
            "allowed_operations": ["repository_branch_assess"],
            "requires_authorization": requires_authorization,
            "stop_workflow": stop_workflow,
            "ownership_effect": "none",
            "reason": reason,
        }
    command_argv = ["takeover", issue_key]
    return {
        "executor": executor,
        "action": action,
        "operation_id": "takeover_task",
        "command_argv": command_argv,
        "command_line": f"ao-work {' '.join(command_argv)}",
        "bound_arguments": {"issue_key": issue_key},
        "required_inputs": [],
        "input_artifacts": [],
        "allowed_operations": ["takeover_task"] if not stop_workflow else [],
        "requires_authorization": requires_authorization,
        "stop_workflow": stop_workflow,
        "ownership_effect": "none",
        "reason": reason,
    }


def validate_takeover_operation(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise _schema_invalid("接管状态必须是对象")
    missing = sorted(_REQUIRED_OPERATION_FIELDS - set(payload))
    extra = sorted(
        set(payload) - _REQUIRED_OPERATION_FIELDS - _OPTIONAL_OPERATION_FIELDS
    )
    if missing or extra:
        raise _schema_invalid(
            "接管状态字段集合不合法",
            missing_fields=missing,
            extra_fields=extra,
        )
    value = deepcopy(dict(payload))
    if value["schema_version"] != TAKEOVER_SCHEMA_VERSION:
        raise _schema_invalid("接管状态 schema_version 必须为 2")
    if not isinstance(value["operation_id"], str) or not OPERATION_ID.fullmatch(
        value["operation_id"]
    ):
        raise _schema_invalid("operation_id 格式无效")
    for field in (
        "issue_key",
        "agentic_run_id",
        "agent_id",
        "jira_status_before",
        "jira_status_target",
        "comment_marker",
        "planned_at",
        "updated_at",
    ):
        _require_text(value[field], field)
    if "comment_markdown" in value:
        _require_text(
            value["comment_markdown"],
            "comment_markdown",
            max_length=32768,
        )
        if (
            normalized_comment_content_sha256(value["comment_markdown"])
            != value["comment_content_sha256"]
        ):
            raise _schema_invalid(
                "comment_markdown 与 comment_content_sha256 不一致"
            )
    for field in (
        "authorization_digest",
        "preflight_facts_sha256",
        "comment_content_sha256",
    ):
        if not isinstance(value[field], str) or not HEX_64.fullmatch(value[field]):
            raise _schema_invalid(f"{field} 必须是 sha256")
    if value["takeover_kind"] not in TAKEOVER_KINDS:
        raise _schema_invalid("takeover_kind 不受支持")
    if value["phase"] not in TAKEOVER_PHASES:
        raise _schema_invalid("phase 不受支持")
    if value["result"] not in TAKEOVER_RESULTS:
        raise _schema_invalid("result 不受支持")
    if value["external_result_certainty"] not in EXTERNAL_CERTAINTIES:
        raise _schema_invalid("external_result_certainty 不受支持")
    for field in ("transition_id", "comment_id", "comment_author", "status_after"):
        if value[field] is not None:
            _require_text(value[field], field)
    if not isinstance(value["comment_author_verified"], bool):
        raise _schema_invalid("comment_author_verified 必须是布尔值")
    if not isinstance(value["retry_safe"], bool):
        raise _schema_invalid("retry_safe 必须是布尔值")
    if type(value["content_version"]) is not int or value["content_version"] < 1:
        raise _schema_invalid("content_version 必须是正整数")
    for field in ("takeover_status", "human_notice", "recovery_action"):
        _require_text(value[field], field)
    if value["failure_code"] is not None:
        _require_text(value["failure_code"], "failure_code")
    _validate_next_action(value["agentic_next_action"])
    _validate_operation_combination(value)
    json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return value


def validate_takeover_event(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise _schema_invalid("接管事件必须是对象")
    required = {
        "schema_version",
        "issue_key",
        "agentic_run_id",
        "updated_at",
        "content_version",
        "operation",
        "status",
        "code",
        "retry_safe",
        "operation_id",
        "phase_before",
        "phase_after",
        "result",
        "evidence_sha256",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise _schema_invalid("接管事件字段不完整", missing_fields=missing)
    value = deepcopy(dict(payload))
    if value["schema_version"] != "1":
        raise _schema_invalid("接管事件顶层 schema_version 必须为 1")
    if not isinstance(value["operation_id"], str) or not OPERATION_ID.fullmatch(
        value["operation_id"]
    ):
        raise _schema_invalid("接管事件 operation_id 格式无效")
    for field in ("issue_key", "agentic_run_id", "updated_at"):
        _require_text(value[field], field)
    if type(value["content_version"]) is not int or value["content_version"] < 1:
        raise _schema_invalid("接管事件 content_version 必须是正整数")
    if value["operation"] not in TAKEOVER_EVENTS:
        raise _schema_invalid("接管事件 operation 不受支持")
    if value["phase_before"] is not None and value["phase_before"] not in TAKEOVER_PHASES:
        raise _schema_invalid("接管事件 phase_before 不受支持")
    if value["phase_after"] not in TAKEOVER_PHASES:
        raise _schema_invalid("接管事件 phase_after 不受支持")
    if value["result"] not in TAKEOVER_RESULTS:
        raise _schema_invalid("接管事件 result 不受支持")
    if not isinstance(value["evidence_sha256"], str) or not HEX_64.fullmatch(
        value["evidence_sha256"]
    ):
        raise _schema_invalid("接管事件 evidence_sha256 必须是 sha256")
    if value["status"] not in {"completed", "blocked", "uncertain"}:
        raise _schema_invalid("接管事件 status 不受支持")
    if not isinstance(value["retry_safe"], bool):
        raise _schema_invalid("接管事件 retry_safe 必须是布尔值")
    if value["code"] is not None:
        _require_text(value["code"], "code")
    _validate_event_transition(value)
    return value


def phase_index(phase: str) -> int:
    try:
        return TAKEOVER_PHASES.index(phase)
    except ValueError as error:
        raise _schema_invalid("接管阶段不受支持") from error


def require_phase_transition(current: str, target: str) -> None:
    current_index = phase_index(current)
    target_index = phase_index(target)
    if target_index < current_index or target_index > current_index + 1:
        raise takeover_error(
            "takeover_phase_transition_invalid",
            f"接管阶段不能从 {current} 直接进入 {target}",
            "请按 Runtime 定义的接管阶段顺序恢复，不要跳过外部回读",
        )


def evidence_sha256(evidence: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(evidence),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def normalized_comment_content_sha256(markdown: str) -> str:
    normalized = plain_text(markdown_to_adf(markdown))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def immutable_intent(payload: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "operation_id",
        "issue_key",
        "agentic_run_id",
        "agent_id",
        "takeover_kind",
        "authorization_digest",
        "preflight_facts_sha256",
        "jira_status_before",
        "jira_status_target",
        "transition_id",
        "comment_marker",
        "comment_content_sha256",
    )
    value = {field: payload[field] for field in fields}
    if "comment_markdown" in payload:
        value["comment_markdown"] = payload["comment_markdown"]
    return value


def _validate_operation_combination(value: Mapping[str, Any]) -> None:
    phase = str(value["phase"])
    result = str(value["result"])
    if result == "completed" and phase != "local_finalized":
        raise _schema_invalid("completed 只能与 local_finalized 同时出现")
    if phase == "local_finalized" and result != "completed":
        raise _schema_invalid("local_finalized 必须是 completed")
    if result == "uncertain" and value["retry_safe"] is not False:
        raise _schema_invalid("外部结果不确定时 retry_safe 必须为 false")
    if result in {"blocked", "uncertain"} and not value["failure_code"]:
        raise _schema_invalid("阻塞或不确定结果必须包含 failure_code")
    if result in {"in_progress", "completed"} and value["failure_code"] is not None:
        raise _schema_invalid("正常结果不得包含 failure_code")
    if phase_index(phase) >= phase_index("comment_verified"):
        if not value["comment_id"] or not value["comment_author_verified"]:
            raise _schema_invalid("comment_verified 之后必须包含已验证 Comment")
    if phase_index(phase) >= phase_index("status_verified"):
        if value["status_after"] != value["jira_status_target"]:
            raise _schema_invalid("status_verified 之后 Status 必须等于目标值")
    if result == "completed" and value["takeover_status"] != "completed":
        raise _schema_invalid("完成结果的 takeover_status 必须为 completed")
    if result != "completed" and value["takeover_status"] == "completed":
        raise _schema_invalid("部分完成状态不得声明 takeover_status=completed")
    if value["takeover_status"] != result:
        raise _schema_invalid("takeover_status 必须与 result 一致")
    if value["takeover_kind"] != "new_takeover" and "不是新接管" not in value[
        "human_notice"
    ]:
        raise _schema_invalid("非新接管 human_notice 必须明文提示不是新接管")


def _validate_event_transition(value: Mapping[str, Any]) -> None:
    operation = value["operation"]
    pair = (value["phase_before"], value["phase_after"])
    expected = {
        "takeover_intent_created": (None, "intent_persisted"),
        "takeover_comment_verified": ("intent_persisted", "comment_verified"),
        "takeover_status_verified": ("comment_verified", "status_verified"),
        "takeover_completed": ("status_verified", "local_finalized"),
    }
    if operation in expected and pair != expected[operation]:
        raise _schema_invalid(f"{operation} 的阶段转换无效")
    if operation == "takeover_blocked" and value["phase_before"] != value[
        "phase_after"
    ]:
        raise _schema_invalid("takeover_blocked 不得改变写入阶段")
    if operation == "takeover_recovered":
        before = value["phase_before"]
        if before is not None and phase_index(value["phase_after"]) < phase_index(before):
            raise _schema_invalid("takeover_recovered 不得回退写入阶段")


def _validate_next_action(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise _schema_invalid("agentic_next_action 必须是对象")
    required = {
        "executor",
        "action",
        "required_inputs",
        "allowed_operations",
        "requires_authorization",
        "stop_workflow",
        "ownership_effect",
        "reason",
    }
    if required - set(value):
        raise _schema_invalid("agentic_next_action 字段不完整")
    for field in ("executor", "action", "ownership_effect", "reason"):
        _require_text(value[field], f"agentic_next_action.{field}")
    for field in ("required_inputs", "allowed_operations"):
        if not isinstance(value[field], list) or not all(
            isinstance(item, str) and item for item in value[field]
        ):
            raise _schema_invalid(f"agentic_next_action.{field} 必须是字符串列表")
    for field in ("requires_authorization", "stop_workflow"):
        if not isinstance(value[field], bool):
            raise _schema_invalid(f"agentic_next_action.{field} 必须是布尔值")


def _require_text(value: Any, field: str, *, max_length: int = 4096) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > max_length
    ):
        raise _schema_invalid(f"{field} 必须是非空字符串")


def _schema_invalid(message: str, **details: Any) -> RuntimeErrorResult:
    return takeover_error(
        "takeover_schema_invalid",
        message,
        "请核对本地接管状态和 Runtime 版本，不要手工覆盖状态文件",
        **details,
    )
