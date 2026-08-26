from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state.io import atomic_write_json, read_json, require_safe_regular_file
from ao_work.task_state.locking import TaskLock
from ao_work.workspace_security import validate_workspace_managed_path


CONFIRMATION_ID_PATTERN = re.compile(r"^rc_[0-9a-f]{32}$")


class RepositoryConfirmationStore:
    """集中管理仓库领域确认工件，调用方只传确认 ID，绝不拼接受管路径。"""

    def __init__(
        self,
        workspace_root: Path,
        *,
        lock_timeout: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.expanduser().resolve()
        self.lock_timeout = lock_timeout
        self._now = now or (lambda: datetime.now(timezone.utc))

    def create(self, issue_key: str, agentic_run_id: str, scope: Mapping[str, Any]) -> dict[str, Any]:
        revision, digest = _scope_binding(scope)
        confirmation_id = f"rc_{uuid.uuid4().hex}"
        path = self._path(issue_key, confirmation_id)
        with self._lock(issue_key):
            self._validate_path(path, allow_missing=True)
            payload = {
                "schema_version": 1,
                "confirmation_id": confirmation_id,
                "issue_key": issue_key,
                "agentic_run_id": agentic_run_id,
                "repository_scope_revision": revision,
                "proposal_digest": digest,
                "task_domain": None,
                "status": "recorded",
                "created_at": self._timestamp(),
                "consumed_at": None,
            }
            atomic_write_json(path, payload)
        return self.reference(payload)

    def validate(
        self,
        issue_key: str,
        agentic_run_id: str,
        confirmation_id: str,
        scope: Mapping[str, Any],
        task_domain: str,
    ) -> dict[str, Any]:
        with self._lock(issue_key):
            return self._validate(issue_key, agentic_run_id, confirmation_id, scope, task_domain)

    def _validate(
        self,
        issue_key: str,
        agentic_run_id: str,
        confirmation_id: str,
        scope: Mapping[str, Any],
        task_domain: str,
    ) -> dict[str, Any]:
        record = self._read(issue_key, confirmation_id)
        revision, digest = _scope_binding(scope)
        if record.get("issue_key") != issue_key or record.get("agentic_run_id") != agentic_run_id:
            raise _blocked("repository_confirmation_identity_mismatch", "确认 ID 不属于当前任务运行")
        if record.get("repository_scope_revision") != revision or record.get("proposal_digest") != digest:
            if record.get("status") == "recorded":
                record["status"] = "superseded"
                self._write(issue_key, confirmation_id, record)
            raise _blocked("repository_confirmation_superseded", "确认 ID 对应的仓库分析建议已失效")
        if record.get("status") == "consumed" and record.get("task_domain") != task_domain:
            raise _blocked("repository_confirmation_conflict", "同一确认 ID 不能确认不同任务领域")
        if record.get("status") not in {"recorded", "consumed"}:
            raise _blocked("repository_confirmation_invalid", "确认记录状态无效")
        return record

    def consume(
        self,
        issue_key: str,
        agentic_run_id: str,
        confirmation_id: str,
        scope: Mapping[str, Any],
        task_domain: str,
    ) -> dict[str, Any]:
        with self._lock(issue_key):
            record = self._validate(issue_key, agentic_run_id, confirmation_id, scope, task_domain)
            if record["status"] == "consumed":
                return self.reference(record)
            directory = self._path(issue_key, confirmation_id).parent
            for candidate in directory.glob("rc_*.json"):
                if candidate.name == f"{confirmation_id}.json":
                    continue
                self._validate_path(candidate)
                other = read_json(candidate)
                if other.get("status") == "consumed":
                    raise _blocked(
                        "repository_confirmation_already_consumed",
                        "当前仓库分析建议已有其它确认 ID 被消费",
                    )
            record["status"] = "consumed"
            record["task_domain"] = task_domain
            record["consumed_at"] = self._timestamp()
            self._write(issue_key, confirmation_id, record)
            return self.reference(record)

    def inspect(
        self,
        issue_key: str,
        confirmation_id: str | None = None,
    ) -> dict[str, Any]:
        """返回只读审计摘要；公开 API 绝不返回或接收受管文件路径。"""
        with self._lock(issue_key):
            if confirmation_id is not None:
                record = self._read(issue_key, confirmation_id)
                if record.get("issue_key") != issue_key:
                    raise _blocked("repository_confirmation_identity_mismatch", "确认 ID 不属于当前任务")
                return {"confirmation": self.audit_summary(record)}

            directory = self._path(issue_key, f"rc_{'0' * 32}").parent
            self._validate_directory(directory)
            if not directory.exists():
                return {"confirmation_records": [], "record_count": 0}
            records: list[dict[str, Any]] = []
            for candidate in directory.iterdir():
                if not candidate.is_file() or not CONFIRMATION_ID_PATTERN.fullmatch(candidate.stem) or candidate.suffix != ".json":
                    continue
                record = self._read(issue_key, candidate.stem)
                if record.get("issue_key") != issue_key:
                    raise _blocked("repository_confirmation_identity_mismatch", "确认记录不属于当前任务")
                records.append(self.audit_summary(record))
            records.sort(key=lambda item: (str(item["created_at"]), str(item["confirmation_id"])), reverse=True)
            return {"confirmation_records": records, "record_count": len(records)}

    @staticmethod
    def reference(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "confirmation_id": record["confirmation_id"],
            "issue_key": record["issue_key"],
            "agentic_run_id": record["agentic_run_id"],
            "repository_scope_revision": record["repository_scope_revision"],
            "proposal_digest": record["proposal_digest"],
            "status": record["status"],
        }

    @staticmethod
    def audit_summary(record: Mapping[str, Any]) -> dict[str, Any]:
        status = str(record["status"])
        return {
            "confirmation_id": record["confirmation_id"],
            "issue_key": record["issue_key"],
            "agentic_run_id": record["agentic_run_id"],
            "repository_scope_revision": record["repository_scope_revision"],
            "proposal_digest": record["proposal_digest"],
            "task_domain": record["task_domain"],
            "status": status,
            "created_at": record["created_at"],
            "consumed_at": record["consumed_at"],
            "description": _audit_description(record, status),
        }

    def _read(self, issue_key: str, confirmation_id: str) -> dict[str, Any]:
        path = self._path(issue_key, confirmation_id)
        self._validate_path(path)
        try:
            record = read_json(path)
        except (OSError, ValueError) as error:
            raise _blocked("repository_confirmation_invalid", "确认记录无法安全读取") from error
        required = {
            "schema_version", "confirmation_id", "issue_key", "agentic_run_id",
            "repository_scope_revision", "proposal_digest", "task_domain", "status",
            "created_at", "consumed_at",
        }
        if set(record) != required or record.get("schema_version") != 1 or record.get("confirmation_id") != confirmation_id:
            raise _blocked("repository_confirmation_invalid", "确认记录内容无效")
        return record

    def _write(self, issue_key: str, confirmation_id: str, record: Mapping[str, Any]) -> None:
        path = self._path(issue_key, confirmation_id)
        self._validate_path(path)
        atomic_write_json(path, record)

    def _path(self, issue_key: str, confirmation_id: str) -> Path:
        if not CONFIRMATION_ID_PATTERN.fullmatch(confirmation_id):
            raise _blocked("repository_confirmation_id_invalid", "确认 ID 格式无效")
        return self.workspace_root / ".agentic-ops" / "tasks" / issue_key / "repository-confirmations" / f"{confirmation_id}.json"

    def _validate_path(self, path: Path, *, allow_missing: bool = False) -> None:
        validate_workspace_managed_path(self.workspace_root, path)
        require_safe_regular_file(path, allow_missing=allow_missing)

    def _validate_directory(self, directory: Path) -> None:
        validate_workspace_managed_path(self.workspace_root, directory)
        if directory.exists() and not directory.is_dir():
            raise _blocked("repository_confirmation_invalid", "确认记录目录无效")

    def _lock(self, issue_key: str) -> TaskLock:
        return TaskLock(self.workspace_root / ".agentic-ops" / "tasks" / issue_key / ".repository-confirmations.lock", timeout=self.lock_timeout)

    def _timestamp(self) -> str:
        return self._now().isoformat()


def _scope_binding(scope: Mapping[str, Any]) -> tuple[int, str]:
    revision = scope.get("content_version")
    if not isinstance(revision, int) or revision < 1:
        raise _blocked("repository_confirmation_scope_invalid", "当前仓库范围版本无效")
    proposal = {
        "issue_key": scope.get("issue_key"),
        "agentic_run_id": scope.get("agentic_run_id"),
        "task_domain": scope.get("task_domain"),
        "problem_version": scope.get("problem_version"),
        "problem_version_repository": scope.get("problem_version_repository"),
        "problem_version_sha": scope.get("problem_version_sha"),
        "proposed_repository_branch_map": scope.get("proposed_repository_branch_map"),
    }
    encoded = json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return revision, hashlib.sha256(encoded).hexdigest()


def _audit_description(record: Mapping[str, Any], status: str) -> str:
    domain = record.get("task_domain") or "尚未确认领域"
    return (
        f"仓库领域确认记录：状态为 {status}，任务领域为 {domain}，"
        f"范围版本为 {record['repository_scope_revision']}。"
    )


def _blocked(code: str, message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请重新执行仓库分析并使用当前返回的确认 ID",
    )
