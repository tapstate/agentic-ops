from __future__ import annotations

import errno
import fcntl
import os
import stat
import time
from pathlib import Path
from types import TracebackType

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult


class TaskLock:
    def __init__(self, path: Path, timeout: float = 5.0) -> None:
        self.path = path
        self.timeout = max(timeout, 0.0)
        self._file: object | None = None

    def __enter__(self) -> "TaskLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise _unsafe_lock(self.path, error) from error
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise _unsafe_lock(self.path)
            lock_file = os.fdopen(descriptor, "r+", encoding="utf-8")
        except BaseException:
            os.close(descriptor)
            raise
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


def _unsafe_lock(path: Path, error: OSError | None = None) -> RuntimeErrorResult:
    detail = f"（{type(error).__name__}）" if error is not None else ""
    return RuntimeErrorResult(
        code="task_lock_path_invalid",
        message=f"任务状态锁必须是当前工作空间内的普通文件：{path.name}{detail}",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请移除锁路径符号链接或异常文件，确认未发生跨工作空间写入后重试",
    )
