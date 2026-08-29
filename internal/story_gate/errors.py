from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

EXIT_BLOCKED = 2
EXIT_CAPABILITY_GAP = 3


@dataclass
class StoryGateError(Exception):
    code: str
    message: str
    status: str = "failed"
    exit_code: int = 1
    retry_safe: bool = False
    required_human_action: str = ""
    details: dict[str, Any] = field(default_factory=dict)
