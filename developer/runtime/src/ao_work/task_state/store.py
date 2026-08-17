from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.io import (
    append_ndjson,
    atomic_write_json,
    atomic_write_text,
    read_json,
    read_text,
    require_safe_regular_file,
)
from ao_work.task_state.locking import TaskLock
from ao_work.workspace_security import validate_workspace_managed_path, validate_workspace_state_root

SCHEMA_VERSION = "1"
ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SAFE_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


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
            if not SAFE_COMPONENT_PATTERN.fullmatch(value):
                raise _invalid_input(name, value)


class TaskStore:
    def __init__(
        self,
        workspace_root: Path,
        *,
        lock_timeout: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self.state_root = self.workspace_root / ".agentic-ops"
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
                atomic_write_text(staging_dir / "decisions.ndjson", "")
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
        self._validate_issue_key(issue_key)
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

    def write_report(
        self,
        issue_key: str,
        agentic_run_id: str,
        kind: str,
        content: str,
    ) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        if kind not in {"analysis", "plan"}:
            raise _invalid_input("report_kind", kind)
        if not content.strip():
            raise _invalid_input("report_content", content)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="报告运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            report_path = task_dir / "reports" / f"{kind}.md"
            self._validate_managed_path(report_path)
            atomic_write_text(report_path, content)
            event = self._journal_event(task, f"report_{kind}", "completed", retry_safe=True)
            append_ndjson(task_dir / "journal.ndjson", event)
            return {"report_kind": kind, "report_path": str(report_path)}

    def append_decision(
        self,
        issue_key: str,
        agentic_run_id: str,
        decision_type: str,
        summary: str,
        reference: str,
    ) -> bool:
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        self._validate_component("decision_type", decision_type)
        self._validate_text("decision_summary", summary)
        self._validate_text("decision_reference", reference)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="决策运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            decision_path = task_dir / "decisions.ndjson"
            for line in read_text(decision_path).splitlines():
                if not line.strip():
                    continue
                existing = json.loads(line)
                if (
                    existing.get("decision_type") == decision_type
                    and existing.get("reference") == reference
                ):
                    if existing.get("summary") != summary:
                        raise RuntimeErrorResult(
                            code="decision_reference_conflict",
                            message="相同决策引用对应了不同内容",
                            status="blocked",
                            exit_code=EXIT_BLOCKED,
                            required_human_action="请核对授权引用和决策内容，不要覆盖已有决策",
                        )
                    return False
            append_ndjson(
                decision_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "issue_key": issue_key,
                    "agentic_run_id": agentic_run_id,
                    "updated_at": self._timestamp(),
                    "content_version": 1,
                    "decision_type": decision_type,
                    "summary": summary,
                    "reference": reference,
                },
            )
            return True

    def record_gate_transition(
        self,
        issue_key: str,
        agentic_run_id: str,
        *,
        stage: str,
        next_action: str,
        operation: str,
        status: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        for field, value in (
            ("stage", stage),
            ("next_action", next_action),
            ("operation", operation),
            ("status", status),
        ):
            self._validate_component(field, value)
        normalized_evidence = self._validate_readback_evidence(evidence)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="任务门禁事件运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用当前任务绑定的 agentic_run_id",
                )
            progress_path = task_dir / "progress.json"
            progress = read_json(progress_path)
            progress.update(
                {
                    "stage": stage,
                    "agentic_next_action": next_action,
                    "terminal": False,
                    "updated_at": self._timestamp(),
                    "content_version": int(progress.get("content_version", 0)) + 1,
                }
            )
            atomic_write_json(progress_path, progress)
            event = self._journal_event(
                task,
                operation,
                status,
                retry_safe=status != "completed",
            )
            event["evidence"] = normalized_evidence
            append_ndjson(task_dir / "journal.ndjson", event)
            return {"progress": progress, "event": event}

    def record_external_readback(
        self,
        issue_key: str,
        operation: str,
        idempotency_key: str,
        external_id: str,
        status: str = "completed",
        *,
        agentic_run_id: str | None = None,
        plan_id: str | None = None,
        content_sha256: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        self._validate_component("operation", operation)
        self._validate_component("idempotency_key", idempotency_key)
        self._validate_component("external_id", external_id)
        self._validate_component("status", status)
        if agentic_run_id is not None:
            self._validate_run_id(agentic_run_id)
        if plan_id is not None:
            self._validate_component("plan_id", plan_id)
        if content_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", content_sha256
        ):
            raise _invalid_input("content_sha256", str(content_sha256))
        normalized_evidence = self._validate_readback_evidence(evidence or {})
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            bound_run_id = str(task.get("agentic_run_id", ""))
            if agentic_run_id is not None and agentic_run_id != bound_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="外部回读运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            sync_path = task_dir / "sync.json"
            sync = read_json(sync_path)
            writes = sync.setdefault("external_writes", {})
            record_key = f"{operation}:{idempotency_key}"
            existing = writes.get(record_key)
            record = {
                "operation": operation,
                "issue_key": issue_key,
                "agentic_run_id": bound_run_id,
                "idempotency_key": idempotency_key,
                "external_id": external_id,
                "status": status,
                "readback_at": self._timestamp(),
            }
            if plan_id is not None:
                record["plan_id"] = plan_id
            if content_sha256 is not None:
                record["content_sha256"] = content_sha256
            if normalized_evidence:
                record["evidence"] = normalized_evidence
            if existing and existing != record:
                stable_existing = dict(existing)
                stable_existing.pop("readback_at", None)
                stable_record = dict(record)
                stable_record.pop("readback_at", None)
                if stable_existing != stable_record:
                    raise RuntimeErrorResult(
                        code="local_state_mismatch",
                        message="外部回读结果与本地同步记录不一致",
                        status="blocked",
                        exit_code=EXIT_BLOCKED,
                        required_human_action="请人工核对 Jira 与 sync.json，不要重复写入",
                    )
            writes[record_key] = record
            sync["updated_at"] = self._timestamp()
            sync["last_readback_at"] = sync["updated_at"]
            sync["content_version"] = int(sync.get("content_version", 0)) + 1
            atomic_write_json(sync_path, sync)
            append_ndjson(
                task_dir / "journal.ndjson",
                self._journal_event(
                    task,
                    operation,
                    status,
                    retry_safe=True,
                    idempotency_key=idempotency_key,
                    external_id=external_id,
                ),
            )
            return record

    def _validate_readback_evidence(
        self,
        evidence: dict[str, Any],
    ) -> dict[str, str | int | bool | None]:
        if not isinstance(evidence, dict):
            raise _invalid_input("readback_evidence", "<invalid>")
        normalized: dict[str, str | int | bool | None] = {}
        for key, value in evidence.items():
            if not isinstance(key, str) or not SAFE_COMPONENT_PATTERN.fullmatch(key):
                raise _invalid_input("readback_evidence_key", str(key))
            if not isinstance(value, (str, int, bool)) and value is not None:
                raise _invalid_input("readback_evidence", "<invalid>")
            if isinstance(value, str) and (
                not value.strip() or "\x00" in value or len(value) > 4096
            ):
                raise _invalid_input("readback_evidence", "<invalid>")
            normalized[key] = value
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > 16384:
            raise _invalid_input("readback_evidence", "<too-large>")
        return normalized

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
        self._validate_managed_path(task_dir, must_exist=True)
        required = ("task.json", "progress.json", "sync.json", "journal.ndjson", "decisions.ndjson")
        missing = []
        for name in required:
            path = task_dir / name
            if not path.exists() and not path.is_symlink():
                missing.append(name)
                continue
            require_safe_regular_file(path)
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
        self._validate_issue_key(issue_key)
        lock_path = self.state_root / "locks" / f"{issue_key}.lock"
        self._validate_managed_path(lock_path)
        return TaskLock(lock_path, self.lock_timeout)

    def _task_dir(self, issue_key: str) -> Path:
        self._validate_issue_key(issue_key)
        task_dir = self.state_root / "tasks" / issue_key
        self._validate_managed_path(task_dir)
        return task_dir

    def _validate_issue_key(self, issue_key: str) -> None:
        if not isinstance(issue_key, str) or not ISSUE_KEY_PATTERN.fullmatch(issue_key):
            raise _invalid_input("issue_key", str(issue_key))

    def _validate_run_id(self, agentic_run_id: str) -> None:
        if not isinstance(agentic_run_id, str) or not RUN_ID_PATTERN.fullmatch(agentic_run_id):
            raise _invalid_input("agentic_run_id", str(agentic_run_id))

    def _validate_component(self, field: str, value: str) -> None:
        if not isinstance(value, str) or not SAFE_COMPONENT_PATTERN.fullmatch(value):
            raise _invalid_input(field, str(value))

    def _validate_text(self, field: str, value: str) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or "\x00" in value
            or len(value) > 4096
        ):
            raise _invalid_input(field, "<invalid>")

    def _validate_managed_path(self, path: Path, *, must_exist: bool = False) -> None:
        try:
            validate_workspace_state_root(self.workspace_root)
            validate_workspace_managed_path(self.workspace_root, path)
        except RuntimeErrorResult as error:
            raise self._unsafe_path(path) from error
        workspace = self.workspace_root.expanduser().resolve()
        state = self.state_root
        if state.parent.resolve() != workspace:
            raise self._unsafe_path(state)
        candidate = path
        try:
            candidate.resolve(strict=False).relative_to(workspace)
        except (OSError, ValueError) as error:
            raise self._unsafe_path(candidate) from error
        current = workspace
        relative = candidate.relative_to(self.workspace_root)
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise self._unsafe_path(current)
        for protected in (
            self.state_root,
            self.state_root / "tasks",
            self.state_root / "locks",
        ):
            if protected.is_symlink():
                raise self._unsafe_path(protected)
        if must_exist and not candidate.is_dir():
            raise self._unsafe_path(candidate)

    @staticmethod
    def _unsafe_path(path: Path) -> RuntimeErrorResult:
        return RuntimeErrorResult(
            code="task_state_path_unsafe",
            message=f"任务状态受管路径不安全：{path.name}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请移除越界路径或符号链接，并核对工作空间状态目录",
        )

    def _timestamp(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _journal_event(
        self,
        task: dict[str, Any],
        operation: str,
        status: str,
        *,
        retry_safe: bool,
        idempotency_key: str | None = None,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "issue_key": task["issue_key"],
            "agentic_run_id": task["agentic_run_id"],
            "updated_at": self._timestamp(),
            "content_version": 1,
            "operation": operation,
            "status": status,
            "code": None,
            "retry_safe": retry_safe,
            "idempotency_key": idempotency_key,
            "external_id": external_id,
        }


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
