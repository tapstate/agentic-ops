from __future__ import annotations

import time
from pathlib import Path

from internal.story_gate.errors import EXIT_BLOCKED, StoryGateError


class TaskLock:
    def __init__(self, path: Path, timeout: float = 5.0) -> None:
        self.path = path
        self.timeout = timeout

    def __enter__(self) -> "TaskLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir()
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise StoryGateError(
                        code="story_gate_state_lock_timeout",
                        message="故事门禁状态正被另一操作更新",
                        status="blocked",
                        exit_code=EXIT_BLOCKED,
                        required_human_action="请等待当前故事门禁操作完成后重试",
                    )
                time.sleep(0.05)

    def __exit__(self, *_args: object) -> None:
        self.path.rmdir()
