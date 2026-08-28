from __future__ import annotations

import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

EXIT_BLOCKED = 2
EXIT_CAPABILITY_GAP = 3


@dataclass
class RuntimeErrorResult(Exception):
    code: str
    message: str
    status: str = "failed"
    exit_code: int = 1
    retry_safe: bool = False
    required_human_action: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Step(ABC):
    """维护工作面独立实现的 StepResult v2 下一步骤模型。"""

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

    @abstractmethod
    def _validate_variant(self) -> None:
        """校验子类不变量。"""

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Step":
        kind = value.get("kind")
        scope = value.get("scope", "flow")
        mode = value.get("mode")
        executor = value.get("executor")
        action = value.get("action")
        if kind not in {"action", "decision", "input", "wait", "none"}:
            raise ValueError("next_step.kind 不受支持")
        if scope not in {"local", "flow"}:
            raise ValueError("next_step.scope 不受支持")
        if mode not in {"auto", "timed_auto", "manual"}:
            raise ValueError("next_step.mode 不受支持")
        if executor not in {"ao_maint", "ao_work", "ai", "human", "reviewer", "project_tool", "stop"}:
            raise ValueError("next_step.executor 不受支持")
        if not isinstance(action, str) or not action.strip():
            raise ValueError("next_step.action 无效")
        data = dict(value)
        call = data.get("call")
        if kind == "none":
            data.update({"executor": "stop", "mode": "manual", "call": None})
            step_type: type[Step] = TerminalStep
            call = None
        elif kind == "action":
            if not isinstance(call, Mapping):
                raise ValueError("ActionStep 必须提供可执行 call")
            step_type = ActionStep
        elif kind == "decision" and mode == "timed_auto":
            step_type = TimedDecisionStep
        elif kind == "decision":
            step_type = DecisionStep
        elif kind == "input":
            step_type = InputStep
        else:
            step_type = WaitStep
        step = step_type(kind, scope, mode, data["executor"], action, call, data)
        step._validate_variant()
        return step


@dataclass(frozen=True)
class ActionStep(Step):
    def _validate_variant(self) -> None:
        if self.mode != "auto" or not isinstance(self.call, Mapping):
            raise ValueError("ActionStep 必须使用 auto 并提供可执行 call")


@dataclass(frozen=True)
class DecisionStep(Step):
    def _validate_variant(self) -> None:
        choices = self._data.get("choices")
        if self.mode != "manual" or not isinstance(choices, list) or not choices:
            raise ValueError("DecisionStep 必须使用 manual 并声明 choices")
        if sum(bool(choice.get("recommended")) for choice in choices if isinstance(choice, Mapping)) != 1:
            raise ValueError("DecisionStep 必须有且仅有一个推荐选项")


@dataclass(frozen=True)
class TimedDecisionStep(Step):
    def _validate_variant(self) -> None:
        if not isinstance(self._data.get("timed"), Mapping) or not isinstance(self._data.get("transitions"), Mapping):
            raise ValueError("TimedDecisionStep 必须声明 timed 与 transitions")


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
            raise ValueError("TerminalStep 必须由 stop 以 manual 终止")


def success(operation: str, **payload: Any) -> dict[str, Any]:
    if "agentic_next_action" in payload:
        raise ValueError("agentic_next_action 已淘汰；请改用结构化 next_step")
    next_step = payload.pop("next_step", _review_step(operation))
    if not isinstance(next_step, Mapping):
        raise ValueError("next_step 必须是对象")
    return {
        "schema_version": "step-result/v2",
        "ok": True,
        "operation": operation,
        "status": "completed",
        "retry_safe": True,
        "result": _result("succeeded", f"{operation} 已完成", payload),
        "next_step": Step.from_mapping(next_step).to_dict(),
        **payload,
    }


def failure(operation: str, error: RuntimeErrorResult) -> dict[str, Any]:
    if "agentic_next_action" in error.details:
        raise ValueError("agentic_next_action 已淘汰；请改用结构化 next_step")
    details = dict(error.details)
    next_step = details.pop("next_step", _blocked_step(operation, error))
    if not isinstance(next_step, Mapping):
        raise ValueError("next_step 必须是对象")
    return {
        "schema_version": "step-result/v2",
        "ok": False,
        "operation": operation,
        "status": error.status,
        "code": error.code,
        "retry_safe": error.retry_safe,
        "message": error.message,
        "required_human_action": error.required_human_action,
        "result": _result("failed", error.message, details),
        "next_step": Step.from_mapping(next_step).to_dict(),
        **details,
    }


def _result(status: str, summary: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "summary": summary,
        "facts": {key: value for key, value in payload.items() if key not in {"evidence", "effects", "remaining"}},
        "evidence": list(payload.get("evidence", [])),
        "effects": list(payload.get("effects", [])),
        "remaining": list(payload.get("remaining", [])),
    }


def _review_step(operation: str) -> dict[str, Any]:
    return {
        "kind": "decision", "scope": "flow", "mode": "manual", "executor": "reviewer",
        "action": "review_operation_result", "question": f"请审阅 {operation} 的结果并决定后续动作",
        "choices": [{"id": "review", "label": "审阅结果", "recommended": True}],
        "submit": {"operation": "submit_decision", "effect": "record_only"},
        "call": {"operation": "submit_decision", "argv": []},
    }


def _blocked_step(operation: str, error: RuntimeErrorResult) -> dict[str, Any]:
    return {
        "kind": "decision", "scope": "flow", "mode": "manual", "executor": "human",
        "action": "resolve_blocked_operation", "question": error.required_human_action or f"请处理 {operation} 的阻断结果",
        "choices": [{"id": "resolve", "label": "处理阻断", "recommended": True}],
        "submit": {"operation": "submit_decision", "effect": "record_only"},
        "call": {"operation": "submit_decision", "argv": []},
    }


def manual_decision_step(action: str, question: str | None = None) -> dict[str, Any]:
    """把已到达人工门禁的业务动作表达为规范化 DecisionStep。"""
    return {
        "kind": "decision", "scope": "flow", "mode": "manual", "executor": "human",
        "action": action, "question": question or f"请确认是否执行 {action}",
        "choices": [{"id": "continue", "label": "确认并继续", "recommended": True}],
        "submit": {"operation": "submit_decision", "effect": "record_only"},
        "call": {"operation": "submit_decision", "argv": []},
    }


def write_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def write_diagnostic(message: str) -> None:
    sys.stderr.write(f"AgenticOps：{message}\n")
