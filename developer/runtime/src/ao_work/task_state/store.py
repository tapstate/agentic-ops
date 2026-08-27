from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult, normalize_next_step
from ao_work.task_state.io import (
    append_ndjson,
    atomic_write_json,
    atomic_write_text,
    read_json,
    read_text,
    require_safe_regular_file,
)
from ao_work.task_state.locking import TaskLock
from ao_work.task_state.takeover import (
    TAKEOVER_SCHEMA_VERSION,
    evidence_sha256,
    human_notice,
    immutable_intent,
    phase_index,
    require_phase_transition,
    stable_takeover_operation_id,
    takeover_error,
    takeover_next_step,
    validate_takeover_event,
    validate_takeover_operation,
)
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
                "next_action": "analyze_task",
                "terminal": False,
            }
            sync = {**common, "external_writes": {}, "last_readback_at": None}
            tasks_root = task_dir.parent
            tasks_root.mkdir(parents=True, exist_ok=True)
            staging_dir = Path(tempfile.mkdtemp(prefix=f".{identity.issue_key}.", dir=tasks_root))
            try:
                (staging_dir / "reports").mkdir()
                (staging_dir / "feedback").mkdir()
                (staging_dir / "proposals").mkdir()
                (staging_dir / "confirmations").mkdir()
                (staging_dir / "timed-steps").mkdir()
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
            task = read_json(task_dir / "task.json")
            progress = read_json(task_dir / "progress.json")
            sync = read_json(task_dir / "sync.json")
            repository_scope_path = self._repository_scope_path(task_dir)
            repository_scope = (
                read_json(repository_scope_path)
                if repository_scope_path.is_file()
                else None
            )
            return {
                "task": task,
                "progress": progress,
                "sync": sync,
                "task_dir": str(task_dir),
                "repository_scope": repository_scope,
                "takeover_recovery": self._read_takeover_recovery_locked(
                    task_dir,
                    task,
                    progress,
                    sync,
                ),
            }

    def record_repository_proposal(
        self,
        issue_key: str,
        agentic_run_id: str,
        proposal: dict[str, Any],
    ) -> dict[str, Any]:
        """保存仓库/分支分析建议；建议本身没有建树或编码权限。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="仓库分析运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            path = self._repository_scope_path(task_dir)
            self._validate_managed_path(path)
            existing = read_json(path) if path.is_file() else None
            if isinstance(existing, dict) and isinstance(
                existing.get("confirmed_repository_branch_map"), list
            ):
                if (
                    existing.get("task_domain") == proposal.get("task_domain")
                    and existing.get("proposed_repository_branch_map")
                    == proposal.get("proposed_repository_branch_map")
                ):
                    return {"created": False, "repository_scope": existing, "path": str(path)}
                raise RuntimeErrorResult(
                    code="repository_mapping_confirmation_required",
                    message="仓库分支建议已变化，现有确认不能被分析结果覆盖",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请重新展示建议与现有确认的完整差异并由用户确认",
                )
            now = self._timestamp()
            scope = {
                "schema_version": 1,
                "content_version": 1,
                "issue_key": issue_key,
                "agentic_run_id": agentic_run_id,
                "phase": "proposal_recorded",
                "problem_version": proposal.get("problem_version"),
                "problem_version_repository": proposal.get(
                    "problem_version_repository"
                ),
                "problem_version_sha": proposal.get("problem_version_sha"),
                "task_domain": proposal.get("task_domain"),
                "task_domain_source": proposal.get("task_domain_source"),
                "proposed_repository_branch_map": proposal.get(
                    "proposed_repository_branch_map", []
                ),
                "confirmed_repository_branch_map": None,
                "confirmed_change_repositories": [],
                "mapping_differences": [],
                "actual_change_repositories": [],
                "updated_at": now,
            }
            atomic_write_json(path, scope)
            append_ndjson(
                task_dir / "journal.ndjson",
                {
                    **self._journal_event(
                        task,
                        "repository_branch_proposal_recorded",
                        "completed",
                        retry_safe=True,
                    ),
                    "evidence": {
                        "problem_version": scope["problem_version"],
                        "repositories": [
                            item.get("repository")
                            for item in scope["proposed_repository_branch_map"]
                            if isinstance(item, dict)
                        ],
                    },
                },
            )
            return {"created": True, "repository_scope": scope, "path": str(path)}

    def confirm_repository_mapping(
        self,
        issue_key: str,
        agentic_run_id: str,
        confirmed: list[dict[str, Any]],
        differences: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """保存用户确认的最终仓库/分支关系；后续工作只能消费该列表。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="仓库确认运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            path = self._repository_scope_path(task_dir)
            self._validate_managed_path(path)
            require_safe_regular_file(path)
            scope = read_json(path)
            existing = scope.get("confirmed_repository_branch_map")
            if existing is not None:
                if existing == confirmed:
                    return {"created": False, "repository_scope": scope, "path": str(path)}
                if not isinstance(existing, list) or any(
                    item.get("worktree_status") != "not_created"
                    for item in existing
                    if isinstance(item, dict)
                ):
                    raise RuntimeErrorResult(
                        code="repository_mapping_changed_after_code_facts",
                        message="已有工作树或代码事实后，仓库分支关系不能被直接替换",
                        status="blocked",
                        exit_code=EXIT_BLOCKED,
                        required_human_action="请使旧设计确认失效并重新进入完整仓库范围风险审查",
                    )
            scope.update(
                {
                    "phase": "mapping_confirmed",
                    "task_domain_source": "user_confirmed",
                    "confirmed_repository_branch_map": confirmed,
                    "confirmed_change_repositories": [
                        item["repository"] for item in confirmed
                    ],
                    "mapping_differences": differences,
                    "updated_at": self._timestamp(),
                }
            )
            scope["content_version"] = int(scope.get("content_version") or 0) + 1
            atomic_write_json(path, scope)
            confirmation_path = task_dir / "confirmations" / "repository-branch.json"
            self._validate_managed_path(confirmation_path)
            atomic_write_json(
                confirmation_path,
                {
                    "schema_version": 1,
                    "issue_key": issue_key,
                    "agentic_run_id": agentic_run_id,
                    "task_domain": scope.get("task_domain"),
                    "repository_scope_content_version": scope["content_version"],
                    "confirmed_repository_branch_map": confirmed,
                    "mapping_differences": differences,
                    "confirmed_at": scope["updated_at"],
                },
            )
            append_ndjson(
                task_dir / "journal.ndjson",
                {
                    **self._journal_event(
                        task,
                        "repository_branch_mapping_confirmed",
                        "completed",
                        retry_safe=True,
                    ),
                    "evidence": {
                        "task_domain": scope.get("task_domain"),
                        "repositories": [item["repository"] for item in confirmed],
                        "differences": differences,
                    },
                },
            )
            return {
                "created": True,
                "repository_scope": scope,
                "path": str(path),
                "confirmation_path": str(confirmation_path),
            }

    def update_repository_worktree(
        self,
        issue_key: str,
        agentic_run_id: str,
        repository: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """回写已确认仓库的按需工作树事实。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="工作树运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            path = self._repository_scope_path(task_dir)
            self._validate_managed_path(path)
            require_safe_regular_file(path)
            scope = read_json(path)
            rows = scope.get("confirmed_repository_branch_map")
            if not isinstance(rows, list):
                raise RuntimeErrorResult(
                    code="repository_mapping_confirmation_required",
                    message="任务尚无用户确认的仓库分支关系",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请先确认任务领域及 Runtime 推导的仓库分支计划",
                )
            matched = False
            updated_rows: list[dict[str, Any]] = []
            for item in rows:
                row = dict(item)
                if row.get("repository") == repository:
                    row.update(updates)
                    matched = True
                updated_rows.append(row)
            if not matched:
                raise RuntimeErrorResult(
                    code="repository_outside_confirmed_mapping",
                    message=f"仓库不在用户确认关系表中：{repository}",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请先按增量范围重新确认仓库分支关系",
                )
            scope["confirmed_repository_branch_map"] = updated_rows
            if updated_rows and all(
                row.get("worktree_status") in {"not_created", "cleaned"}
                for row in updated_rows
            ) and any(row.get("worktree_status") == "cleaned" for row in updated_rows):
                scope["phase"] = "worktrees_cleaned"
            else:
                scope["phase"] = "worktrees_active"
            scope["updated_at"] = self._timestamp()
            scope["content_version"] = int(scope.get("content_version") or 0) + 1
            atomic_write_json(path, scope)
            append_ndjson(
                task_dir / "journal.ndjson",
                {
                    **self._journal_event(
                        task,
                        "repository_worktree_updated",
                        "completed",
                        retry_safe=True,
                    ),
                    "evidence": {"repository": repository, **updates},
                },
            )
            return {"repository_scope": scope, "path": str(path)}

    def record_actual_change_repositories(
        self,
        issue_key: str,
        agentic_run_id: str,
        repositories: list[dict[str, Any]],
        *,
        summary_plan_id: str | None = None,
        summary_content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """保存逐仓 Git 回读形成的实际变更集合及完成总结计划绑定。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="实际变更仓库运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            path = self._repository_scope_path(task_dir)
            self._validate_managed_path(path)
            require_safe_regular_file(path)
            scope = read_json(path)
            confirmed = scope.get("confirmed_repository_branch_map")
            if not isinstance(confirmed, list):
                raise RuntimeErrorResult(
                    code="repository_mapping_confirmation_required",
                    message="任务尚无用户确认的仓库分支关系",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请先确认任务领域及 Runtime 推导的仓库分支计划",
                )
            allowed = {str(item.get("repository")) for item in confirmed}
            actual = [str(item.get("repository")) for item in repositories]
            if len(actual) != len(set(actual)) or not set(actual).issubset(allowed):
                raise RuntimeErrorResult(
                    code="actual_repositories_outside_confirmed_mapping",
                    message="实际变更仓库集合重复或越出用户确认范围",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请停止提交完成总结并重新确认仓库范围",
                )
            scope["actual_change_repositories"] = repositories
            if summary_plan_id is not None:
                scope["completion_summary_plan_id"] = summary_plan_id
            if summary_content_sha256 is not None:
                scope["completion_summary_content_sha256"] = summary_content_sha256
            scope["updated_at"] = self._timestamp()
            scope["content_version"] = int(scope.get("content_version") or 0) + 1
            atomic_write_json(path, scope)
            event = self._journal_event(
                task,
                "actual_change_repositories_recorded",
                "completed",
                retry_safe=True,
            )
            event["evidence"] = {
                "repositories": actual,
                "summary_plan_id": summary_plan_id,
                "summary_content_sha256": summary_content_sha256,
            }
            append_ndjson(task_dir / "journal.ndjson", event)
            return {"repository_scope": scope, "path": str(path)}

    def record_repository_summary_readback(
        self,
        issue_key: str,
        agentic_run_id: str,
        *,
        plan_id: str,
        content_sha256: str,
        external_id: str,
    ) -> dict[str, Any]:
        """把完成总结的 Jira 回读与实际变更仓库清单绑定。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="完成总结回读运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用任务当前绑定的 agentic_run_id",
                )
            path = self._repository_scope_path(task_dir)
            self._validate_managed_path(path)
            require_safe_regular_file(path)
            scope = read_json(path)
            if (
                scope.get("completion_summary_plan_id") != plan_id
                or scope.get("completion_summary_content_sha256") != content_sha256
            ):
                raise RuntimeErrorResult(
                    code="repository_summary_readback_mismatch",
                    message="Jira 评论回读与实际变更仓库总结计划不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请核对完成总结计划与 Jira 回读，不要开始清理",
                )
            scope["completion_summary_readback"] = {
                "plan_id": plan_id,
                "content_sha256": content_sha256,
                "external_id": external_id,
                "readback_at": self._timestamp(),
            }
            scope["phase"] = "completion_evidence_readback"
            scope["updated_at"] = self._timestamp()
            scope["content_version"] = int(scope.get("content_version") or 0) + 1
            atomic_write_json(path, scope)
            return {"repository_scope": scope, "path": str(path)}

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

    def schedule_timed_step(
        self,
        issue_key: str,
        agentic_run_id: str,
        next_step: dict[str, Any],
    ) -> dict[str, Any]:
        """持久化 timed_auto 决策及其已批准的 ActionStep 转换。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        normalized = normalize_next_step(
            next_step,
            operation="timed_step_schedule",
            payload={"issue_key": issue_key, "agentic_run_id": agentic_run_id},
        )
        if normalized["mode"] != "timed_auto":
            raise _invalid_input("next_step.mode", str(normalized["mode"]))
        timed = normalized["timed"]
        deadline = _parse_timed_deadline(str(timed["deadline"]))
        decision_id = str(timed["decision_id"])
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            self._require_task_identity(task, agentic_run_id, "定时决策")
            path = self._timed_step_path(task_dir, decision_id)
            existing = read_json(path) if path.is_file() else None
            intent = {
                "agentic_run_id": agentic_run_id,
                "decision_id": decision_id,
                "deadline": _format_timestamp(deadline),
                "default_choice": timed["default_choice"],
                "cancel_if": timed["cancel_if"],
                "fact_bind": timed["fact_bind"],
                "policy": timed["policy"],
                "next_step": normalized,
            }
            if existing is not None:
                if all(existing.get(field) == value for field, value in intent.items()):
                    self._repair_timed_step_journal(task_dir, path, existing)
                    return {"created": False, "timed_step": existing}
                raise RuntimeErrorResult(
                    code="timed_step_conflict",
                    message="相同定时决策编号对应了不同的解析意图",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请新建决策编号，或先人工核对并取消已有定时决策",
                )
            record = {
                "schema_version": "timed-step/v2",
                "issue_key": issue_key,
                **intent,
                "state": "pending",
                "created_at": self._timestamp(),
                "updated_at": self._timestamp(),
                "content_version": 1,
            }
            event = self._journal_event(task, "timed_step_schedule", "completed", retry_safe=True)
            event.update({
                "event_id": self._timed_step_event_id(decision_id, 1),
                "decision_id": decision_id,
                "deadline": record["deadline"],
                "policy": timed["policy"],
            })
            record["journal_event"] = event
            atomic_write_json(path, record)
            self._repair_timed_step_journal(task_dir, path, record)
            return {"created": True, "timed_step": record}

    def resolve_timed_step(
        self,
        issue_key: str,
        agentic_run_id: str,
        decision_id: str,
        current_fact_bind: str,
    ) -> dict[str, Any]:
        """在任务锁内依据可信时钟和事实绑定解析到期决策，结果仅记录。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        self._validate_component("decision_id", decision_id)
        self._validate_text("current_fact_bind", current_fact_bind)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            self._require_task_identity(task, agentic_run_id, "定时决策")
            path = self._timed_step_path(task_dir, decision_id)
            if not path.is_file():
                raise RuntimeErrorResult(
                    code="timed_step_not_found",
                    message="未找到指定的定时决策",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请先读取当前任务状态，确认决策编号和任务绑定",
                )
            record = read_json(path)
            self._validate_timed_step_record(record, issue_key, agentic_run_id, decision_id)
            if record["state"] != "pending":
                self._repair_timed_step_journal(task_dir, path, record)
                return {"resolved": True, "timed_step": record}
            if record["fact_bind"] != current_fact_bind:
                record.update(
                    {
                        "state": "cancelled",
                        "resolution": {"reason": "fact_binding_changed", "effect": "cancelled"},
                        "updated_at": self._timestamp(),
                        "content_version": int(record["content_version"]) + 1,
                    }
                )
                event = self._journal_event(task, "timed_step_cancel", "completed", retry_safe=True)
                event.update({
                    "event_id": self._timed_step_event_id(decision_id, record["content_version"]),
                    "decision_id": decision_id,
                    "reason": "fact_binding_changed",
                })
                record["journal_event"] = event
                atomic_write_json(path, record)
                self._repair_timed_step_journal(task_dir, path, record)
                return {"resolved": True, "timed_step": record}
            if self._now().astimezone(timezone.utc) < _parse_timed_deadline(record["deadline"]):
                return {"resolved": False, "timed_step": record}
            record.update(
                {
                    "state": "resolved",
                    "resolution": {
                        "choice": record["default_choice"],
                        "reason": "deadline_reached",
                        "effect": "auto_transition_ready",
                        "next_step": record["next_step"]["transitions"][record["default_choice"]],
                    },
                    "updated_at": self._timestamp(),
                    "content_version": int(record["content_version"]) + 1,
                }
            )
            event = self._journal_event(task, "timed_step_resolve", "completed", retry_safe=True)
            event.update({
                "event_id": self._timed_step_event_id(decision_id, record["content_version"]),
                "decision_id": decision_id,
                "choice": record["default_choice"],
                "next_step_operation": record["resolution"]["next_step"]["operation_id"],
            })
            record["journal_event"] = event
            atomic_write_json(path, record)
            self._repair_timed_step_journal(task_dir, path, record)
            return {"resolved": True, "timed_step": record}

    def cancel_timed_step(
        self,
        issue_key: str,
        agentic_run_id: str,
        decision_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """供人工或上游策略明确撤销尚未解析的定时决策。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        self._validate_component("decision_id", decision_id)
        self._validate_text("reason", reason)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            self._require_task_identity(task, agentic_run_id, "定时决策")
            path = self._timed_step_path(task_dir, decision_id)
            if not path.is_file():
                raise RuntimeErrorResult(
                    code="timed_step_not_found",
                    message="未找到指定的定时决策",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请先读取当前任务状态，确认决策编号和任务绑定",
                )
            record = read_json(path)
            self._validate_timed_step_record(record, issue_key, agentic_run_id, decision_id)
            if record["state"] != "pending":
                self._repair_timed_step_journal(task_dir, path, record)
                return {"cancelled": record["state"] == "cancelled", "timed_step": record}
            record.update(
                {
                    "state": "cancelled",
                    "resolution": {"reason": reason, "effect": "cancelled"},
                    "updated_at": self._timestamp(),
                    "content_version": int(record["content_version"]) + 1,
                }
            )
            event = self._journal_event(task, "timed_step_cancel", "completed", retry_safe=True)
            event.update({
                "event_id": self._timed_step_event_id(decision_id, record["content_version"]),
                "decision_id": decision_id,
                "reason": reason,
            })
            record["journal_event"] = event
            atomic_write_json(path, record)
            self._repair_timed_step_journal(task_dir, path, record)
            return {"cancelled": True, "timed_step": record}

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
        code: str | None = None,
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
        if code is not None:
            self._validate_component("code", code)
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
                    "next_action": next_action,
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
            event["code"] = code
            event["evidence"] = normalized_evidence
            append_ndjson(task_dir / "journal.ndjson", event)
            return {"progress": progress, "event": event}

    def update_stage_timeline(
        self,
        issue_key: str,
        agentic_run_id: str,
        sequence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """更新 progress.json 的 stage_timeline（AO-42）。

        进入 AI 阶段时传入追加后的序列；准出时传入闭合 end 后的序列。
        只更新 stage_timeline 字段，不动其它 progress 字段（向后兼容）。
        """
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        if not isinstance(sequence, list):
            raise _invalid_input("stage_timeline", repr(sequence))
        for item in sequence:
            if not isinstance(item, dict) or not isinstance(item.get("stage_id"), str):
                raise _invalid_input("stage_timeline_item", repr(item))
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            if task.get("agentic_run_id") != agentic_run_id:
                raise RuntimeErrorResult(
                    code="task_identity_mismatch",
                    message="阶段序列运行编号与任务绑定不一致",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请使用当前任务绑定的 agentic_run_id",
                )
            progress_path = task_dir / "progress.json"
            progress = read_json(progress_path)
            progress.update(
                {
                    "stage_timeline": sequence,
                    "updated_at": self._timestamp(),
                    "content_version": int(progress.get("content_version", 0)) + 1,
                }
            )
            atomic_write_json(progress_path, progress)
            event = self._journal_event(
                task,
                "stage_timeline_update",
                "completed",
                retry_safe=True,
            )
            event["sequence"] = sequence
            append_ndjson(task_dir / "journal.ndjson", event)
            return {"progress": progress}

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

    def persist_takeover_intent(
        self,
        issue_key: str,
        agentic_run_id: str,
        *,
        agent_id: str,
        takeover_kind: str,
        authorization_digest: str,
        preflight_facts_sha256: str,
        jira_status_before: str,
        jira_status_target: str,
        transition_id: str | None,
        comment_marker: str,
        comment_content_sha256: str,
        comment_markdown: str | None = None,
        planned_at: str | None = None,
    ) -> dict[str, Any]:
        """在第一次外部写入前持久化稳定接管意图。"""
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        operation_id = stable_takeover_operation_id(
            issue_key,
            agentic_run_id,
            authorization_digest,
        )
        timestamp = planned_at or self._timestamp()
        operation_payload = {
            "schema_version": TAKEOVER_SCHEMA_VERSION,
            "operation_id": operation_id,
            "issue_key": issue_key,
            "agentic_run_id": agentic_run_id,
            "agent_id": agent_id,
            "takeover_kind": takeover_kind,
            "authorization_digest": authorization_digest,
            "preflight_facts_sha256": preflight_facts_sha256,
            "jira_status_before": jira_status_before,
            "jira_status_target": jira_status_target,
            "transition_id": transition_id,
            "comment_marker": comment_marker,
            "comment_content_sha256": comment_content_sha256,
            "comment_id": None,
            "comment_author": None,
            "comment_author_verified": False,
            "status_after": None,
            "phase": "intent_persisted",
            "result": "in_progress",
            "external_result_certainty": "not_attempted",
            "takeover_status": "in_progress",
            "human_notice": human_notice(takeover_kind, "in_progress"),
            "next_step": takeover_next_step(
                "ensure_takeover_comment",
                issue_key=issue_key,
                reason="稳定接管意图已落盘，继续确保受管 Comment 存在并回读",
            ),
            "failure_code": None,
            "retry_safe": True,
            "recovery_action": "ensure_takeover_comment",
            "planned_at": timestamp,
            "updated_at": timestamp,
            "content_version": 1,
        }
        if comment_markdown is not None:
            operation_payload["comment_markdown"] = comment_markdown
        operation = validate_takeover_operation(operation_payload)
        with self._lock(issue_key):
            task_dir, task, progress, sync = self._load_takeover_files(
                issue_key, agentic_run_id
            )
            existing = sync.get("takeover_operation")
            if existing is not None:
                existing = validate_takeover_operation(existing)
                if immutable_intent(existing) != immutable_intent(operation):
                    raise takeover_error(
                        "takeover_intent_conflict",
                        "现有接管意图与本次请求不一致",
                        "请核对已有运行、授权和 Jira 写前事实，不要覆盖稳定接管意图",
                        existing_operation_id=existing["operation_id"],
                    )
                event = self._ensure_takeover_checkpoint_event(
                    task_dir,
                    task,
                    progress,
                    existing,
                    phase_before=None,
                )
                return {
                    "created": event is not None,
                    "operation": existing,
                    "event": event,
                    "state_file": str(task_dir / "sync.json"),
                }
            sync["takeover_operation"] = operation
            self._write_takeover_sync(task_dir, sync)
            event = self._takeover_event(
                task,
                operation,
                "takeover_intent_created",
                phase_before=None,
                evidence={"preflight_facts_sha256": preflight_facts_sha256},
            )
            append_ndjson(task_dir / "journal.ndjson", event)
            return {
                "created": True,
                "operation": operation,
                "event": event,
                "state_file": str(task_dir / "sync.json"),
            }

    def verify_takeover_comment(
        self,
        issue_key: str,
        agentic_run_id: str,
        operation_id: str,
        *,
        comment_id: str,
        comment_author: str,
        expected_author: str,
        comment_marker: str,
        comment_content_sha256: str,
    ) -> dict[str, Any]:
        with self._lock(issue_key):
            task_dir, task, progress, sync = self._load_takeover_files(
                issue_key, agentic_run_id
            )
            operation = self._bound_takeover_operation(sync, operation_id)
            if (
                comment_author != expected_author
                or comment_marker != operation["comment_marker"]
                or comment_content_sha256 != operation["comment_content_sha256"]
            ):
                raise takeover_error(
                    "takeover_comment_evidence_conflict",
                    "Jira Comment 回读证据与稳定接管意图不一致",
                    "请核对 Comment ID、作者、稳定标记和内容摘要，不要复用可复制文本",
                )
            if phase_index(operation["phase"]) >= phase_index("comment_verified"):
                if (
                    operation["comment_id"] != comment_id
                    or operation["comment_author"] != comment_author
                ):
                    raise takeover_error(
                        "takeover_comment_evidence_conflict",
                        "已记录的 Comment 证据与本次回读不一致",
                        "请人工核对 Jira Comment，不要覆盖已验证外部事实",
                    )
                event = self._ensure_takeover_checkpoint_event(
                    task_dir,
                    task,
                    progress,
                    operation,
                    phase_before="intent_persisted",
                )
                return {
                    "created": event is not None,
                    "operation": operation,
                    "event": event,
                }
            require_phase_transition(operation["phase"], "comment_verified")
            phase_before = operation["phase"]
            updated = dict(operation)
            updated.update(
                {
                    "comment_id": comment_id,
                    "comment_author": comment_author,
                    "comment_author_verified": True,
                    "phase": "comment_verified",
                    "result": "in_progress",
                    "external_result_certainty": "verified",
                    "takeover_status": "in_progress",
                    "human_notice": human_notice(operation["takeover_kind"], "in_progress"),
                    "next_step": takeover_next_step(
                        "verify_takeover_status",
                        issue_key=issue_key,
                        reason="受管 Comment 已回读验证，继续执行或回读目标 Status",
                    ),
                    "failure_code": None,
                    "retry_safe": True,
                    "recovery_action": "verify_takeover_status",
                }
            )
            updated = self._versioned_takeover(updated)
            event = self._persist_takeover_transition(
                task_dir,
                task,
                sync,
                updated,
                "takeover_comment_verified",
                phase_before=phase_before,
                evidence={
                    "comment_id": comment_id,
                    "comment_author": comment_author,
                    "comment_content_sha256": comment_content_sha256,
                },
            )
            return {"created": True, "operation": updated, "event": event}

    def verify_takeover_status(
        self,
        issue_key: str,
        agentic_run_id: str,
        operation_id: str,
        *,
        status_after: str,
        transition_applied: bool,
    ) -> dict[str, Any]:
        with self._lock(issue_key):
            task_dir, task, progress, sync = self._load_takeover_files(
                issue_key, agentic_run_id
            )
            operation = self._bound_takeover_operation(sync, operation_id)
            if status_after != operation["jira_status_target"]:
                raise takeover_error(
                    "takeover_status_evidence_conflict",
                    "Jira Status 回读值与稳定接管意图的目标值不一致",
                    "请核对 Jira 当前 Status 和 transition 结果，不要盲目重试",
                )
            if phase_index(operation["phase"]) >= phase_index("status_verified"):
                if operation["status_after"] != status_after:
                    raise takeover_error(
                        "takeover_status_evidence_conflict",
                        "已记录的 Status 证据与本次回读不一致",
                        "请人工核对 Jira Status，不要覆盖已验证外部事实",
                    )
                event = self._ensure_takeover_checkpoint_event(
                    task_dir,
                    task,
                    progress,
                    operation,
                    phase_before="comment_verified",
                )
                return {
                    "created": event is not None,
                    "operation": operation,
                    "event": event,
                }
            require_phase_transition(operation["phase"], "status_verified")
            phase_before = operation["phase"]
            updated = dict(operation)
            updated.update(
                {
                    "status_after": status_after,
                    "phase": "status_verified",
                    "result": "in_progress",
                    "external_result_certainty": "verified",
                    "takeover_status": "in_progress",
                    "human_notice": human_notice(operation["takeover_kind"], "in_progress"),
                    "next_step": takeover_next_step(
                        "finalize_takeover_locally",
                        issue_key=issue_key,
                        reason="Jira Comment 和 Status 已回读验证，继续完成本地收口",
                    ),
                    "failure_code": None,
                    "retry_safe": True,
                    "recovery_action": "finalize_takeover_locally",
                }
            )
            updated = self._versioned_takeover(updated)
            event = self._persist_takeover_transition(
                task_dir,
                task,
                sync,
                updated,
                "takeover_status_verified",
                phase_before=phase_before,
                evidence={
                    "status_after": status_after,
                    "transition_applied": transition_applied,
                },
            )
            return {"created": True, "operation": updated, "event": event}

    def mark_takeover_uncertain(
        self,
        issue_key: str,
        agentic_run_id: str,
        operation_id: str,
        *,
        failure_code: str,
        recovery_action: str,
    ) -> dict[str, Any]:
        return self._record_takeover_stop(
            issue_key,
            agentic_run_id,
            operation_id,
            failure_code=failure_code,
            recovery_action=recovery_action,
            result="uncertain",
            certainty="uncertain",
        )

    def block_takeover(
        self,
        issue_key: str,
        agentic_run_id: str,
        operation_id: str,
        *,
        failure_code: str,
        recovery_action: str,
    ) -> dict[str, Any]:
        return self._record_takeover_stop(
            issue_key,
            agentic_run_id,
            operation_id,
            failure_code=failure_code,
            recovery_action=recovery_action,
            result="blocked",
            certainty="conflict",
        )

    def finalize_takeover(
        self,
        issue_key: str,
        agentic_run_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """在 Comment/Status 均验证后完成本地逻辑事务。"""
        with self._lock(issue_key):
            task_dir, task, progress, sync = self._load_takeover_files(
                issue_key, agentic_run_id
            )
            operation = self._bound_takeover_operation(sync, operation_id)
            if operation["phase"] == "local_finalized":
                recovery = self._read_takeover_recovery_locked(
                    task_dir, task, progress, sync
                )
                if recovery["state_consistent"]:
                    return {"created": False, **recovery}
                progress.update(
                    {
                        "stage": "takeover_started",
                        "next_action": "assess_repository_branch_mapping",
                        "terminal": False,
                        "updated_at": self._timestamp(),
                        "content_version": int(progress.get("content_version", 0)) + 1,
                    }
                )
                atomic_write_json(task_dir / "progress.json", progress)
                event = self._takeover_event(
                    task,
                    operation,
                    "takeover_recovered",
                    phase_before="status_verified",
                    evidence={"recovery_action": "recover_local_takeover_state"},
                )
                append_ndjson(task_dir / "journal.ndjson", event)
                return {
                    "created": True,
                    "operation": operation,
                    "event": event,
                    "state_consistent": True,
                    "migration_required": False,
                    "state_file": str(task_dir / "sync.json"),
                }
            require_phase_transition(operation["phase"], "local_finalized")
            phase_before = operation["phase"]
            updated = dict(operation)
            updated.update(
                {
                    "phase": "local_finalized",
                    "result": "completed",
                    "external_result_certainty": "verified",
                    "takeover_status": "completed",
                    "human_notice": human_notice(operation["takeover_kind"], "completed"),
                    "next_step": takeover_next_step(
                        "assess_repository_branch_mapping",
                        issue_key=issue_key,
                        executor="ai",
                        reason="接管本地状态已最终收口，先分析并由用户确认仓库分支关系",
                    ),
                    "failure_code": None,
                    "retry_safe": True,
                    "recovery_action": "none",
                }
            )
            updated = self._versioned_takeover(updated)
            sync["takeover_operation"] = validate_takeover_operation(updated)
            self._write_takeover_sync(task_dir, sync)

            progress.update(
                {
                    "stage": "takeover_started",
                    "next_action": "assess_repository_branch_mapping",
                    "terminal": False,
                    "updated_at": self._timestamp(),
                    "content_version": int(progress.get("content_version", 0)) + 1,
                }
            )
            atomic_write_json(task_dir / "progress.json", progress)
            event = self._takeover_event(
                task,
                updated,
                "takeover_completed",
                phase_before=phase_before,
                evidence={
                    "comment_id": updated["comment_id"],
                    "status_after": updated["status_after"],
                },
            )
            append_ndjson(task_dir / "journal.ndjson", event)
            return {
                "created": True,
                "operation": updated,
                "event": event,
                "state_consistent": True,
                "migration_required": False,
                "state_file": str(task_dir / "sync.json"),
            }

    def read_takeover_recovery(self, issue_key: str) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        with self._lock(issue_key):
            task_dir = self._task_dir(issue_key)
            self._require_complete_task_dir(task_dir)
            task = read_json(task_dir / "task.json")
            progress = read_json(task_dir / "progress.json")
            sync = read_json(task_dir / "sync.json")
            return self._read_takeover_recovery_locked(task_dir, task, progress, sync)

    def migrate_legacy_takeover(
        self,
        issue_key: str,
        agentic_run_id: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        """验证 legacy v1 的 Jira/本地事实后合成 v2 检查点。"""
        required = {
            "agent_id",
            "takeover_kind",
            "authorization_digest",
            "preflight_facts_sha256",
            "jira_status_before",
            "jira_status_target",
            "jira_status_after",
            "transition_id",
            "comment_marker",
            "comment_content_sha256",
            "comment_id",
            "comment_author",
            "expected_comment_author",
            "assignee",
            "expected_assignee",
        }
        missing = sorted(required - set(evidence))
        if missing:
            raise takeover_error(
                "takeover_legacy_state_unverified",
                "legacy 接管迁移证据不完整",
                "请先通过 AO-49 回读 Jira Comment、负责人和 Status，再执行迁移",
                missing_fields=missing,
            )
        with self._lock(issue_key):
            task_dir, task, progress, sync = self._load_takeover_files(
                issue_key, agentic_run_id
            )
            if sync.get("takeover_operation") is not None:
                recovery = self._read_takeover_recovery_locked(
                    task_dir, task, progress, sync
                )
                operation = validate_takeover_operation(
                    sync["takeover_operation"]
                )
                if recovery["state_consistent"] or operation[
                    "phase"
                ] != "local_finalized":
                    return recovery
                progress.update(
                    {
                        "stage": "takeover_started",
                        "next_action": "assess_repository_branch_mapping",
                        "terminal": False,
                        "updated_at": self._timestamp(),
                        "content_version": int(progress.get("content_version", 0)) + 1,
                    }
                )
                atomic_write_json(task_dir / "progress.json", progress)
                event = self._ensure_takeover_checkpoint_event(
                    task_dir,
                    task,
                    progress,
                    operation,
                    phase_before="status_verified",
                )
                return {
                    "operation": operation,
                    "event": event,
                    "migration_required": False,
                    "state_consistent": True,
                    "state_file": str(task_dir / "sync.json"),
                }
            legacy_event = self._latest_legacy_takeover_event(task_dir)
            legacy_evidence = legacy_event.get("evidence") if legacy_event else None
            matches = (
                legacy_event is not None
                and legacy_event.get("status") == "completed"
                and legacy_event.get("agentic_run_id") == agentic_run_id
                and progress.get("stage") == "takeover_started"
                and evidence["comment_author"] == evidence["expected_comment_author"]
                and evidence["assignee"] == evidence["expected_assignee"]
                and evidence["jira_status_after"] == evidence["jira_status_target"]
                and isinstance(legacy_evidence, dict)
                and legacy_evidence.get("takeover_kind") == evidence["takeover_kind"]
                and legacy_evidence.get("takeover_comment_id") == evidence["comment_id"]
                and legacy_evidence.get("takeover_comment_marker")
                == evidence["comment_marker"]
            )
            if not matches:
                raise takeover_error(
                    "takeover_legacy_state_unverified",
                    "legacy 接管状态与 Jira 回读证据不一致",
                    "请人工核对 Comment 作者/标记、运行编号、负责人和 Status；原状态未被覆盖",
                )
            timestamp = str(
                legacy_evidence.get("agentic_takeover_at")
                or legacy_event.get("updated_at")
                or self._timestamp()
            )
            operation = validate_takeover_operation(
                {
                    "schema_version": TAKEOVER_SCHEMA_VERSION,
                    "operation_id": stable_takeover_operation_id(
                        issue_key,
                        agentic_run_id,
                        str(evidence["authorization_digest"]),
                    ),
                    "issue_key": issue_key,
                    "agentic_run_id": agentic_run_id,
                    "agent_id": evidence["agent_id"],
                    "takeover_kind": evidence["takeover_kind"],
                    "authorization_digest": evidence["authorization_digest"],
                    "preflight_facts_sha256": evidence["preflight_facts_sha256"],
                    "jira_status_before": evidence["jira_status_before"],
                    "jira_status_target": evidence["jira_status_target"],
                    "transition_id": evidence["transition_id"],
                    "comment_marker": evidence["comment_marker"],
                    "comment_content_sha256": evidence["comment_content_sha256"],
                    "comment_id": evidence["comment_id"],
                    "comment_author": evidence["comment_author"],
                    "comment_author_verified": True,
                    "status_after": evidence["jira_status_after"],
                    "phase": "local_finalized",
                    "result": "completed",
                    "external_result_certainty": "verified",
                    "takeover_status": "completed",
                    "human_notice": human_notice(
                        str(evidence["takeover_kind"]), "completed"
                    ),
                    "next_step": takeover_next_step(
                        "assess_repository_branch_mapping",
                        issue_key=issue_key,
                        executor="ai",
                        reason="legacy 接管事实已验证并迁移，先分析并由用户确认仓库分支关系",
                    ),
                    "failure_code": None,
                    "retry_safe": True,
                    "recovery_action": "none",
                    "planned_at": timestamp,
                    "updated_at": self._timestamp(),
                    "content_version": 1,
                }
            )
            event = self._takeover_event(
                task,
                operation,
                "takeover_recovered",
                phase_before="status_verified",
                evidence={
                    "migration_source_schema": "1",
                    "comment_id": evidence["comment_id"],
                    "jira_status_after": evidence["jira_status_after"],
                },
            )
            sync["takeover_operation"] = operation
            self._write_takeover_sync(task_dir, sync)
            append_ndjson(task_dir / "journal.ndjson", event)
            return {
                "operation": operation,
                "event": event,
                "migration_required": False,
                "state_consistent": True,
                "state_file": str(task_dir / "sync.json"),
            }

    def _record_takeover_stop(
        self,
        issue_key: str,
        agentic_run_id: str,
        operation_id: str,
        *,
        failure_code: str,
        recovery_action: str,
        result: str,
        certainty: str,
    ) -> dict[str, Any]:
        with self._lock(issue_key):
            task_dir, task, _, sync = self._load_takeover_files(issue_key, agentic_run_id)
            operation = self._bound_takeover_operation(sync, operation_id)
            updated = dict(operation)
            updated.update(
                {
                    "result": result,
                    "external_result_certainty": certainty,
                    "takeover_status": result,
                    "human_notice": human_notice(operation["takeover_kind"], result),
                    "next_step": takeover_next_step(
                        recovery_action,
                        issue_key=issue_key,
                        executor="human" if result == "blocked" else "ao_work",
                        stop_workflow=True,
                        requires_authorization=True,
                        reason=(
                            "接管事实存在冲突，需要人工核对"
                            if result == "blocked"
                            else "外部结果不确定，需要先回读再决定是否继续"
                        ),
                    ),
                    "failure_code": failure_code,
                    "retry_safe": False,
                    "recovery_action": recovery_action,
                }
            )
            updated = self._versioned_takeover(updated)
            event = self._persist_takeover_transition(
                task_dir,
                task,
                sync,
                updated,
                "takeover_blocked",
                phase_before=operation["phase"],
                evidence={"failure_code": failure_code, "result": result},
            )
            return {"operation": updated, "event": event}

    def _load_takeover_files(
        self, issue_key: str, agentic_run_id: str
    ) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
        self._validate_issue_key(issue_key)
        self._validate_run_id(agentic_run_id)
        task_dir = self._task_dir(issue_key)
        self._require_complete_task_dir(task_dir)
        task = read_json(task_dir / "task.json")
        if task.get("agentic_run_id") != agentic_run_id:
            raise takeover_error(
                "task_identity_mismatch",
                "接管状态运行编号与任务绑定不一致",
                "请使用任务当前绑定的 agentic_run_id",
            )
        return (
            task_dir,
            task,
            read_json(task_dir / "progress.json"),
            read_json(task_dir / "sync.json"),
        )

    @staticmethod
    def _bound_takeover_operation(
        sync: dict[str, Any], operation_id: str
    ) -> dict[str, Any]:
        raw = sync.get("takeover_operation")
        if raw is None:
            raise takeover_error(
                "takeover_intent_missing",
                "本地没有稳定接管意图",
                "请先持久化接管意图，再执行外部写入或恢复",
            )
        operation = validate_takeover_operation(raw)
        if operation["operation_id"] != operation_id:
            raise takeover_error(
                "takeover_intent_conflict",
                "接管 operation_id 与本地稳定意图不一致",
                "请使用本地已记录的接管意图，不要创建第二个运行",
            )
        return operation

    def _versioned_takeover(self, operation: dict[str, Any]) -> dict[str, Any]:
        operation["updated_at"] = self._timestamp()
        operation["content_version"] = int(operation["content_version"]) + 1
        return validate_takeover_operation(operation)

    def _persist_takeover_transition(
        self,
        task_dir: Path,
        task: dict[str, Any],
        sync: dict[str, Any],
        operation: dict[str, Any],
        event_name: str,
        *,
        phase_before: str | None,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        sync["takeover_operation"] = validate_takeover_operation(operation)
        event = self._takeover_event(
            task,
            operation,
            event_name,
            phase_before=phase_before,
            evidence=evidence,
        )
        self._write_takeover_sync(task_dir, sync)
        append_ndjson(task_dir / "journal.ndjson", event)
        return event

    def _write_takeover_sync(self, task_dir: Path, sync: dict[str, Any]) -> None:
        sync["updated_at"] = self._timestamp()
        sync["content_version"] = int(sync.get("content_version", 0)) + 1
        atomic_write_json(task_dir / "sync.json", sync)

    def _takeover_event(
        self,
        task: dict[str, Any],
        operation: dict[str, Any],
        event_name: str,
        *,
        phase_before: str | None,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        status = (
            "uncertain"
            if operation["result"] == "uncertain"
            else "blocked"
            if operation["result"] == "blocked"
            else "completed"
        )
        event = {
            **self._journal_event(
                task,
                event_name,
                status,
                retry_safe=bool(operation["retry_safe"]),
            ),
            "code": operation["failure_code"],
            "operation_id": operation["operation_id"],
            "phase_before": phase_before,
            "phase_after": operation["phase"],
            "result": operation["result"],
            "evidence_sha256": evidence_sha256(evidence),
        }
        return validate_takeover_event(event)

    def _ensure_takeover_checkpoint_event(
        self,
        task_dir: Path,
        task: dict[str, Any],
        progress: dict[str, Any],
        operation: dict[str, Any],
        *,
        phase_before: str | None,
    ) -> dict[str, Any] | None:
        latest = self._latest_takeover_event(task_dir, operation["operation_id"])
        if (
            latest
            and latest.get("phase_after") == operation["phase"]
            and latest.get("result") == operation["result"]
        ):
            return None
        if (
            operation["phase"] != "local_finalized"
            and progress.get("stage") == "takeover_started"
        ):
            raise takeover_error(
                "takeover_local_state_incomplete",
                "业务阶段早于接管本地最终收口",
                "请核对 progress.json 与接管快照，不要追加恢复事件掩盖冲突",
            )
        event = self._takeover_event(
            task,
            operation,
            "takeover_recovered",
            phase_before=phase_before,
            evidence={"recovery_action": "repair_takeover_checkpoint_event"},
        )
        append_ndjson(task_dir / "journal.ndjson", event)
        return event

    def _read_takeover_recovery_locked(
        self,
        task_dir: Path,
        task: dict[str, Any],
        progress: dict[str, Any],
        sync: dict[str, Any],
    ) -> dict[str, Any]:
        raw = sync.get("takeover_operation")
        if raw is None:
            legacy_event = self._latest_legacy_takeover_event(task_dir)
            legacy_evidence = (
                legacy_event.get("evidence")
                if isinstance(legacy_event, dict)
                and isinstance(legacy_event.get("evidence"), dict)
                else {}
            )
            authorization_reference = str(
                legacy_evidence.get("authorization_reference") or ""
            )
            recoverable_evidence = {
                key: legacy_evidence.get(key)
                for key in (
                    "agent_id",
                    "takeover_kind",
                    "takeover_comment_id",
                    "takeover_comment_marker",
                    "agentic_takeover_at",
                    "jira_status_before",
                    "jira_status_after",
                )
                if legacy_evidence.get(key) is not None
            }
            if authorization_reference:
                recoverable_evidence["authorization_digest"] = hashlib.sha256(
                    authorization_reference.encode("utf-8")
                ).hexdigest()
            return {
                "operation": None,
                "legacy_state": {
                    "schema_version": "1",
                    "progress_stage": progress.get("stage"),
                    "legacy_takeover_event_found": legacy_event is not None,
                    "agentic_run_id": task.get("agentic_run_id"),
                    "evidence": recoverable_evidence,
                },
                "migration_required": legacy_event is not None,
                "state_consistent": legacy_event is None,
                "state_file": str(task_dir / "sync.json"),
            }
        operation = validate_takeover_operation(raw)
        if (
            operation["issue_key"] != task.get("issue_key")
            or operation["agentic_run_id"] != task.get("agentic_run_id")
        ):
            raise takeover_error(
                "takeover_state_identity_mismatch",
                "接管状态与 task.json 身份不一致",
                "请人工核对本地任务状态，不要覆盖冲突文件",
            )
        latest_event = self._latest_takeover_event(task_dir, operation["operation_id"])
        event_matches_snapshot = bool(
            latest_event
            and latest_event.get("phase_after") == operation["phase"]
            and latest_event.get("result") == operation["result"]
        )
        progress_started = progress.get("stage") == "takeover_started"
        event_finalized = bool(
            latest_event
            and latest_event.get("operation")
            in {"takeover_completed", "takeover_recovered"}
            and latest_event.get("phase_after") == "local_finalized"
        )
        state_consistent = True
        effective = operation
        if operation["phase"] == "local_finalized":
            # 接管完成后业务阶段可以继续推进到准入、方案或执行门禁；这些
            # 后续阶段不应反向否定已经持久化且有完成事件的接管事实。
            state_consistent = event_finalized
        elif progress_started:
            state_consistent = False
        if not event_matches_snapshot:
            state_consistent = False
        if not state_consistent:
            effective = dict(operation)
            if effective["phase"] == "local_finalized":
                effective["phase"] = "status_verified"
            effective.update(
                {
                    "result": "uncertain",
                    "external_result_certainty": operation[
                        "external_result_certainty"
                    ],
                    "takeover_status": "uncertain",
                    "human_notice": human_notice(
                        effective["takeover_kind"], "uncertain"
                    ),
                    "next_step": takeover_next_step(
                        "recover_local_takeover_state",
                        issue_key=str(operation["issue_key"]),
                        stop_workflow=True,
                        requires_authorization=True,
                        reason="接管快照、业务阶段和事件未完成交叉收口，需要先恢复本地状态",
                    ),
                    "failure_code": "takeover_local_state_incomplete",
                    "retry_safe": False,
                    "recovery_action": "recover_local_takeover_state",
                }
            )
            effective = validate_takeover_operation(effective)
        return {
            "operation": effective,
            "persisted_phase": operation["phase"],
            "migration_required": False,
            "state_consistent": state_consistent,
            "state_file": str(task_dir / "sync.json"),
        }

    @staticmethod
    def _latest_legacy_takeover_event(task_dir: Path) -> dict[str, Any] | None:
        journal_path = task_dir / "journal.ndjson"
        for line in reversed(read_text(journal_path).splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                isinstance(event, dict)
                and event.get("operation") == "takeover_task"
            ):
                return event
        return None

    @staticmethod
    def _latest_takeover_event(
        task_dir: Path, operation_id: str
    ) -> dict[str, Any] | None:
        journal_path = task_dir / "journal.ndjson"
        for line in reversed(read_text(journal_path).splitlines()):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("operation_id") == operation_id:
                return event
        return None

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

    def _repository_scope_path(self, task_dir: Path) -> Path:
        """新任务把分析建议放入 proposals，旧任务仅读取原位置。"""
        proposal_path = task_dir / "proposals" / "repository-scope.json"
        legacy_path = task_dir / "repository-scope.json"
        path = proposal_path if proposal_path.exists() or not legacy_path.exists() else legacy_path
        self._validate_managed_path(path)
        return path

    def _timed_step_path(self, task_dir: Path, decision_id: str) -> Path:
        path = task_dir / "timed-steps" / f"{decision_id}.json"
        self._validate_managed_path(path)
        return path

    @staticmethod
    def _timed_step_event_id(decision_id: str, content_version: int) -> str:
        return f"timed-step:{decision_id}:{content_version}"

    def _repair_timed_step_journal(
        self, task_dir: Path, path: Path, record: dict[str, Any]
    ) -> None:
        """补写已持久化 timed 状态对应的审计事件。

        状态文件先落盘能够避免超时解析重复执行；若紧随其后的 journal
        追加失败，下一次相同操作会用固定 event_id 补写，绝不再次变更状态。
        """
        event = record.get("journal_event")
        if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
            raise RuntimeErrorResult(
                code="timed_step_state_invalid",
                message="定时决策状态缺少可恢复的审计事件",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请按任务状态恢复流程核对定时决策记录，不要覆盖原文件",
            )
        journal_path = task_dir / "journal.ndjson"
        self._validate_managed_path(journal_path)
        require_safe_regular_file(journal_path)
        event_id = event["event_id"]
        journal = read_text(journal_path)
        present = False
        for line in journal.splitlines():
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeErrorResult(
                    code="task_state_incomplete",
                    message="任务审计日志不是有效 JSON，不能继续补写定时决策事件",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=False,
                    required_human_action="请按恢复流程核对 journal 和外部事实，不要覆盖现有目录",
                ) from error
            if isinstance(candidate, dict) and candidate.get("event_id") == event_id:
                present = True
                break
        if not present:
            append_ndjson(journal_path, event)

    def _require_task_identity(
        self, task: dict[str, Any], agentic_run_id: str, subject: str
    ) -> None:
        if task.get("agentic_run_id") != agentic_run_id:
            raise RuntimeErrorResult(
                code="task_identity_mismatch",
                message=f"{subject}运行编号与任务绑定不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请使用任务当前绑定的 agentic_run_id",
            )

    def _validate_timed_step_record(
        self,
        record: dict[str, Any],
        issue_key: str,
        agentic_run_id: str,
        decision_id: str,
    ) -> None:
        required = {
            "schema_version",
            "issue_key",
            "agentic_run_id",
            "decision_id",
            "deadline",
            "default_choice",
            "cancel_if",
            "fact_bind",
            "policy",
            "next_step",
            "state",
            "content_version",
            "journal_event",
        }
        if required - set(record):
            raise RuntimeErrorResult(
                code="timed_step_state_invalid",
                message="定时决策状态缺少必要字段",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请按任务状态恢复流程核对定时决策记录，不要覆盖原文件",
            )
        if (
            record["schema_version"] != "timed-step/v2"
            or record["issue_key"] != issue_key
            or record["agentic_run_id"] != agentic_run_id
            or record["decision_id"] != decision_id
            or record["state"] not in {"pending", "resolved", "cancelled"}
        ):
            raise RuntimeErrorResult(
                code="timed_step_state_invalid",
                message="定时决策状态与当前任务绑定不一致或不受支持",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请按任务状态恢复流程核对定时决策记录，不要覆盖原文件",
            )
        try:
            _parse_timed_deadline(str(record["deadline"]))
            normalize_next_step(
                record["next_step"],
                operation="timed_step_state",
                payload={"issue_key": issue_key, "agentic_run_id": agentic_run_id},
            )
        except ValueError as error:
            raise RuntimeErrorResult(
                code="timed_step_state_invalid",
                message="定时决策状态的 deadline 无效",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请按任务状态恢复流程核对定时决策记录，不要覆盖原文件",
            ) from error

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


def _parse_timed_deadline(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid timed deadline") from error
    if parsed.tzinfo is None:
        raise ValueError("timed deadline must include timezone")
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
