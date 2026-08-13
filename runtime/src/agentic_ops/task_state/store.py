from __future__ import annotations

import re
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agentic_ops.output import EXIT_BLOCKED, RuntimeErrorResult
from agentic_ops.task_state.io import append_ndjson, atomic_write_json, read_json
from agentic_ops.task_state.locking import TaskLock

SCHEMA_VERSION = "1"
ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class TaskIdentity:
    connection_id: str
    jira_issue_id: str
    issue_key: str
    project_key: str
    agentic_run_id: str

    def validate(self) -> None:
        if not ISSUE_KEY_PATTERN.fullmatch(self.issue_key):
            raise _invalid_input("issue_key", self.issue_key)
        if not RUN_ID_PATTERN.fullmatch(self.agentic_run_id):
            raise _invalid_input("agentic_run_id", self.agentic_run_id)
        for name in ("connection_id", "jira_issue_id", "project_key"):
            value = getattr(self, name)
            if not value or any(character in value for character in ("/", "\\", "\x00")):
                raise _invalid_input(name, value)


class TaskStore:
    def __init__(
        self,
        workspace_root: Path,
        *,
        lock_timeout: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.state_root = workspace_root / ".agentic-ops"
        self.lock_timeout = lock_timeout
        self._now = now or (lambda: datetime.now(timezone.utc))

    def initialize(self, identity: TaskIdentity) -> dict[str, Any]:
        identity.validate()
        task_dir = self._task_dir(identity.issue_key)
        with self._lock(identity.issue_key):
            task_path = task_dir / "task.json"
            if task_path.exists():
                self._require_complete_task_dir(task_dir)
                existing = read_json(task_path)
                self._verify_identity(existing, identity)
                return {"created": False, "task": existing, "task_dir": str(task_dir)}

            updated_at = self._timestamp()
            common = {
                "schema_version": SCHEMA_VERSION,
                "issue_key": identity.issue_key,
                "agentic_run_id": identity.agentic_run_id,
                "updated_at": updated_at,
                "content_version": 1,
            }
            task = {**common, **asdict(identity), "state": "initialized"}
            progress = {
                **common,
                "stage": "initialized",
                "agentic_next_action": "analyze_task",
                "terminal": False,
            }
            sync = {**common, "external_writes": {}, "last_readback_at": None}
            tasks_root = task_dir.parent
            tasks_root.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(tempfile.mkdtemp(prefix=f".{identity.issue_key}.", dir=tasks_root))
            try:
                (staging_dir / "reports").mkdir()
                (staging_dir / "feedback").mkdir()
                (staging_dir / "runs" / identity.agentic_run_id / "evidence").mkdir(parents=True)
                atomic_write_json(staging_dir / "task.json", task)
                atomic_write_json(staging_dir / "progress.json", progress)
                atomic_write_json(staging_dir / "sync.json", sync)
                (staging_dir / "decisions.ndjson").touch()
                append_ndjson(
                    staging_dir / "journal.ndjson",
                    {
                        **common,
                        "operation": "task_init",
                        "status": "completed",
                        "code": None,
                        "retry_safe": True,
                    },
                )
                os.replace(staging_dir, task_dir)
                _fsync_directory(tasks_root)
            except BaseException:
                shutil.rmtree(staging_dir, ignore_errors=True)
                raise
            return {"created": True, "task": task, "task_dir": str(task_dir)}

    def inspect(self, issue_key: str) -> dict[str, Any]:
        if not ISSUE_KEY_PATTERN.fullmatch(issue_key):
            raise _invalid_input("issue_key", issue_key)
        task_dir = self._task_dir(issue_key)
        with self._lock(issue_key):
            required = ("task.json", "progress.json", "sync.json", "journal.ndjson", "decisions.ndjson")
            missing = [name for name in required if not (task_dir / name).is_file()]
            if missing:
                raise RuntimeErrorResult(
                    code="task_state_not_found",
                    message=f"任务状态不存在或不完整：{', '.join(missing)}",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请先初始化任务，或按恢复流程修复任务状态",
                )
            return {
                "task": read_json(task_dir / "task.json"),
                "progress": read_json(task_dir / "progress.json"),
                "sync": read_json(task_dir / "sync.json"),
                "task_dir": str(task_dir),
            }

    def _verify_identity(self, existing: dict[str, Any], identity: TaskIdentity) -> None:
        for field, expected in asdict(identity).items():
            if existing.get(field) != expected:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message=f"现有任务状态的 {field} 与本次请求不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请确认 Jira 工作空间和运行绑定，不要覆盖现有任务身份",
                )

    def _require_complete_task_dir(self, task_dir: Path) -> None:
        required = ("task.json", "progress.json", "sync.json", "journal.ndjson", "decisions.ndjson")
        missing = [name for name in required if not (task_dir / name).is_file()]
        if missing:
            raise RuntimeErrorResult(
                code="task_state_incomplete",
                message=f"现有任务状态不完整：{', '.join(missing)}",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请按恢复流程核对 journal 和外部事实，不要覆盖现有目录",
            )

    def _lock(self, issue_key: str) -> TaskLock:
        return TaskLock(self.state_root / "locks" / f"{issue_key}.lock", self.lock_timeout)

    def _task_dir(self, issue_key: str) -> Path:
        return self.state_root / "tasks" / issue_key

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _invalid_input(field: str, value: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="invalid_task_identity",
        message=f"任务身份字段 {field} 无效：{value!r}",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请修正任务身份后重试",
    )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
