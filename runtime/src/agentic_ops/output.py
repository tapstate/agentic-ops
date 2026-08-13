from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any, Mapping

EXIT_FAILED = 1
EXIT_BLOCKED = 2
EXIT_CAPABILITY_GAP = 3


@dataclass(frozen=True)
class RuntimeErrorResult(Exception):
    code: str
    message: str
    status: str = "failed"
    exit_code: int = EXIT_FAILED
    retry_safe: bool = False
    required_human_action: str = "请联系 AgenticOps 维护者处理"


def success(operation: str, **payload: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "operation": operation,
        "status": "completed",
        "retry_safe": True,
    }
    result.update(payload)
    return result


def failure(operation: str, error: RuntimeErrorResult) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "status": error.status,
        "code": error.code,
        "retry_safe": error.retry_safe,
        "message": error.message,
        "required_human_action": error.required_human_action,
    }


def write_json(result: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def write_diagnostic(message: str) -> None:
    sys.stderr.write(f"AgenticOps：{message}\n")
    sys.stderr.flush()
