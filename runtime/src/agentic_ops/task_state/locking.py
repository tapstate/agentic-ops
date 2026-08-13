from __future__ import annotations

import errno
import fcntl
import os
import time
from pathlib import Path
from types import TracebackType

from agentic_ops.output import EXIT_BLOCKED, RuntimeErrorResult


class TaskLock:
    def __init__(self, path: Path, timeout: float = 5.0) -> None:
        self.path = path
        self.timeout = max(timeout, 0.0)
        self._file: object | None = None

    def __enter__(self) -> "TaskLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN}:
                    lock_file.close()
                    raise
                if time.monotonic() >= deadline:
                    lock_file.close()
                    raise RuntimeErrorResult(
                        code="task_lock_timeout",
                        message=f"任务状态锁等待超时：{self.path.name}",
                        status="blocked",
                        exit_code=EXIT_BLOCKED,
                        retry_safe=True,
                        required_human_action="请确认没有其它 AgenticOps 运行正在处理同一任务后重试",
                    ) from error
                time.sleep(min(0.05, max(deadline - time.monotonic(), 0.0)))
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()}\n")
        lock_file.flush()
        os.fsync(lock_file.fileno())
        self._file = lock_file
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._file is None:
            return
        lock_file = self._file
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
        self._file = None
