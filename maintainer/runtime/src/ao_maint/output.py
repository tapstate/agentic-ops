from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

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


def success(operation: str, **payload: Any) -> dict[str, Any]:
    return {"ok": True, "operation": operation, "status": "completed", **payload}


def failure(operation: str, error: RuntimeErrorResult) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "status": error.status,
        "code": error.code,
        "retry_safe": error.retry_safe,
        "message": error.message,
        "required_human_action": error.required_human_action,
        **error.details,
    }


def write_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def write_diagnostic(message: str) -> None:
    sys.stderr.write(f"AgenticOps：{message}\n")
