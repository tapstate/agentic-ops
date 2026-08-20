from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ao_maint.io import atomic_write_json, read_json
from ao_maint.jira.config import local_root
from ao_maint.locking import TaskLock
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
WORK_AUTHORIZATION_PATTERN = re.compile(
    r"^work-authorization:(?P<issue>[A-Z][A-Z0-9_]*-[1-9][0-9]*):"
    r"(?P<run>[A-Za-z0-9][A-Za-z0-9._:-]{0,127}):(?P<digest>[0-9a-f]{64})$"
)
ROUTINE_JIRA_OPERATIONS = frozenset(
    {"jira_comment", "jira_transition", "jira_worklog"}
)


def takeover_root(source_root: Path) -> Path:
    return local_root(source_root) / "takeovers"


def state_path(source_root: Path, issue_key: str) -> Path:
    normalized = validate_issue_key(issue_key)
    return takeover_root(source_root) / f"{normalized}.json"


def load_state(source_root: Path, issue_key: str) -> dict[str, Any] | None:
    path = state_path(source_root, issue_key)
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise _blocked("maintainer_takeover_state_invalid", "接管状态文件无效")
    try:
        payload = read_json(path)
    except (OSError, ValueError) as error:
        raise _blocked(
            "maintainer_takeover_state_invalid", "接管状态文件无法读取"
        ) from error
    _validate_state(payload, issue_key)
    return payload


def save_state(source_root: Path, payload: dict[str, Any]) -> None:
    issue_key = validate_issue_key(str(payload.get("issue_key", "")))
    _validate_state(payload, issue_key)
    path = state_path(source_root, issue_key)
    with TaskLock(path.parent / f".{issue_key}.lock", timeout=5):
        atomic_write_json(path, payload)
        path.chmod(0o600)


def append_event(source_root: Path, issue_key: str, event: dict[str, Any]) -> None:
    normalized = validate_issue_key(issue_key)
    path = takeover_root(source_root) / f"{normalized}.events.ndjson"
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue_key": normalized,
        **event,
    }
    with TaskLock(path.parent / f".{normalized}.events.lock", timeout=5):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        path.chmod(0o600)


def validate_work_authorization(
    source_root: Path,
    reference: str,
    *,
    issue_key: str,
    operation: str,
) -> None:
    matched = WORK_AUTHORIZATION_PATTERN.fullmatch(reference.strip())
    if matched is None:
        raise _blocked(
            "jira_write_authorization_required",
            "工作项连续授权引用格式无效",
        )
    if operation not in ROUTINE_JIRA_OPERATIONS:
        raise _blocked(
            "jira_work_authorization_scope_forbidden",
            "工作项连续授权不能覆盖该 Jira 写操作",
        )
    if matched.group("issue") != issue_key:
        raise _blocked(
            "jira_work_authorization_mismatch", "工作项连续授权与 Jira 任务不一致"
        )
    state = load_state(source_root, issue_key)
    if state is None or state.get("authorization_status") != "active":
        raise _blocked(
            "jira_work_authorization_inactive", "工作项连续授权尚未生效或已失效"
        )
    if (
        state.get("run_id") != matched.group("run")
        or state.get("design_digest") != matched.group("digest")
    ):
        raise _blocked(
            "jira_work_authorization_mismatch", "工作项连续授权绑定事实不一致"
        )
    repository_root, branch = git_binding(source_root)
    if (
        state.get("repository_root") != repository_root
        or state.get("working_branch") != branch
    ):
        raise _blocked(
            "jira_work_authorization_binding_changed",
            "工作项连续授权绑定的仓库或分支已经变化",
        )


def work_authorization_reference(state: dict[str, Any]) -> str:
    return (
        f"work-authorization:{state['issue_key']}:{state['run_id']}:"
        f"{state['design_digest']}"
    )


def git_binding(source_root: Path) -> tuple[str, str]:
    root = _git(source_root, "rev-parse", "--show-toplevel")
    branch = _git(source_root, "branch", "--show-current")
    if not branch:
        raise _blocked(
            "maintainer_takeover_detached_head",
            "maintainer 工作项不能在 detached HEAD 上连续执行",
        )
    return str(Path(root).resolve()), branch


def validate_issue_key(value: str) -> str:
    normalized = value.strip().upper()
    if not ISSUE_KEY_PATTERN.fullmatch(normalized):
        raise _blocked("invalid_issue_key", "Jira issue key 格式无效")
    return normalized


def validate_digest(value: str) -> str:
    normalized = value.strip().lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise _blocked("invalid_confirmation_digest", "确认摘要必须是 SHA-256")
    return normalized


def _validate_state(payload: dict[str, Any], issue_key: str) -> None:
    required = {
        "schema_version",
        "issue_key",
        "jira_issue_id",
        "agent_id",
        "run_id",
        "mode",
        "jira_status",
        "repository_root",
        "working_branch",
        "authorization_status",
        "pending_gate",
        "design_digest",
        "design_content",
        "created_at",
        "updated_at",
    }
    if set(payload) != required or payload.get("schema_version") != 1:
        raise _blocked("maintainer_takeover_state_invalid", "接管状态字段无效")
    if payload.get("issue_key") != issue_key:
        raise _blocked("maintainer_takeover_state_invalid", "接管状态任务绑定无效")
    if not RUN_ID_PATTERN.fullmatch(str(payload.get("run_id", ""))):
        raise _blocked("maintainer_takeover_state_invalid", "接管运行 ID 无效")
    if payload.get("mode") not in {"new", "resume", "adopt"}:
        raise _blocked("maintainer_takeover_state_invalid", "接管模式无效")
    if payload.get("authorization_status") not in {"pending", "active"}:
        raise _blocked("maintainer_takeover_state_invalid", "连续授权状态无效")
    digest = str(payload.get("design_digest", ""))
    if digest and not SHA256_PATTERN.fullmatch(digest):
        raise _blocked("maintainer_takeover_state_invalid", "设计摘要无效")
    if not isinstance(payload.get("design_content"), str):
        raise _blocked("maintainer_takeover_state_invalid", "设计内容无效")


def _git(source_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_root), *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        raise _blocked("maintainer_takeover_git_binding_failed", "无法读取 Git 绑定事实")
    return completed.stdout.strip()


def _blocked(code: str, message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请核对 maintainer 工作项、运行状态和授权范围后重试",
    )
