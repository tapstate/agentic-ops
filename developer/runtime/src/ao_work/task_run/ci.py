from __future__ import annotations

import base64
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from xml.etree import ElementTree

import yaml

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_run.protocol import SHA_PATTERN, digest, reject_sensitive_content
from ao_work.task_state.io import append_ndjson, atomic_write_json, atomic_write_text, read_json
from ao_work.task_state.locking import TaskLock
from ao_work.workspace import Workspace

CI_PROTOCOL = "ci_validation_v3"
RUNNER_LOG_EXCERPT_BYTES = 65_536
TextRunner = Callable[[list[str], Path, int | float], subprocess.CompletedProcess[str]]
BytesRunner = Callable[[list[str], Path, int | float, int], subprocess.CompletedProcess[bytes]]
Now = Callable[[], datetime]

_PENDING = {"PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED"}
_FAILED = {
    "FAILURE",
    "FAILED",
    "ERROR",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STALE",
    "STARTUP_FAILURE",
    "NEUTRAL",
    "SKIPPED",
}
_REDACTIONS = (
    re.compile(r"(?i)\b(?:authorization|token|password|secret)\s*[:=]\s*\S+"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
)
_RETRYABLE_CODES = {
    "ci_observation_failed",
    "ci_requirement_observation_failed",
    "ci_artifact_read_failed",
    "ci_artifact_download_failed",
}


def blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=code in _RETRYABLE_CODES,
        required_human_action=action,
        details={"current_stage": "ci_validation"},
    )


class CiRuntime:
    def __init__(
        self,
        workspace: Workspace,
        *,
        lock_timeout: float,
        run_text: TextRunner,
        run_bytes: BytesRunner,
        now: Now | None = None,
    ) -> None:
        self.workspace = workspace
        self.lock_timeout = lock_timeout
        self.run_text = run_text
        self.run_bytes = run_bytes
        self.now = now or (lambda: datetime.now(timezone.utc))

    def probe(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        config = self._config(manifest)
        repository = manifest["repository"]
        root = Path(repository["root"])
        pr_result = self.run_text(
            [
                "gh",
                "pr",
                "view",
                repository["task_branch"],
                "--repo",
                repository["slug"],
                "--json",
                "number,url,state,isDraft,mergedAt,headRefName,headRefOid,baseRefName,baseRefOid,statusCheckRollup",
            ],
            root,
            60,
        )
        if pr_result.returncode != 0:
            raise blocked(
                "ci_observation_failed",
                "GitHub PR/CI 只读回读失败",
                "请检查 GitHub 授权和服务状态后安全重试",
            )
        payload = self._json(pr_result.stdout, "GitHub PR/CI")
        head_sha = payload.get("headRefOid")
        base_sha = payload.get("baseRefOid")
        if (
            payload.get("state") != "OPEN"
            or payload.get("isDraft") is True
            or payload.get("mergedAt")
            or payload.get("headRefName") != repository["task_branch"]
            or payload.get("baseRefName") != repository["target_branch"]
            or not isinstance(head_sha, str)
            or not SHA_PATTERN.fullmatch(head_sha)
            or not isinstance(base_sha, str)
            or not SHA_PATTERN.fullmatch(base_sha)
        ):
            raise blocked(
                "ci_pr_binding_mismatch",
                "PR 的状态、Base、Head 分支或 Head SHA 与 manifest 不一致",
                "请停止自动化并重新确认 PR 与任务绑定",
            )
        checks, missing_checks = self._required_checks(
            payload.get("statusCheckRollup"), config
        )
        requirement = self._github_pr_ci_requirement(
            manifest, config, base_sha, root
        )
        workflow_runs = (
            self._workflow_runs(manifest, config, head_sha, root)
            if requirement["status"] == "required"
            else []
        )
        execution_observed = bool(checks or workflow_runs)
        now = self._utc_now()
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            state = self._load_or_create_state(paths, manifest, config)
            attempts = state["attempts"]
            attempt = attempts.get(head_sha)
            if attempt is None:
                previous_head = state.get("current_head_sha")
                if previous_head is not None and not any(
                    record.get("new_head_sha") == head_sha
                    for record in state["remediations"].values()
                    if isinstance(record, dict)
                ):
                    raise blocked(
                        "ci_head_changed_externally",
                        "PR Head 已变化，但没有当前授权内修复提交与远端回读记录",
                        "请停止自动化并重新核对所有权、PR 和任务授权",
                    )
                started = now
                attempt = {
                    "attempt_id": hashlib.sha256(
                        f"{manifest['issue']['key']}:{manifest['agent']['agentic_run_id']}:{head_sha}".encode()
                    ).hexdigest()[:24],
                    "head_sha": head_sha,
                    "started_at": started.isoformat(),
                    "start_deadline_at": (
                        started + timedelta(seconds=config["start_timeout_seconds"])
                    ).isoformat()
                    if requirement["status"] == "required"
                    else None,
                    "execution_started_at": None,
                    "completion_deadline_at": None,
                    "last_observed_at": now.isoformat(),
                    "ci_status": "waiting_to_start",
                    "ci_requirement": requirement,
                    "required_checks": [],
                    "missing_required_checks": list(config["required_checks"]),
                    "workflow_runs": workflow_runs,
                    "failure_event_id": None,
                    "artifact": None,
                    "report": None,
                }
                attempts[head_sha] = attempt
            elif attempt.get("ci_requirement") != requirement:
                raise blocked(
                    "ci_requirement_changed",
                    "同一 PR Base 与 Head 的 GitHub CI 要求事实发生变化",
                    "请停止自动化并人工核对 PR、Base 提交与 Workflow 配置",
                )
            if (
                requirement["status"] == "required"
                and attempt["execution_started_at"] is None
                and execution_observed
            ):
                attempt["execution_started_at"] = now.isoformat()
                attempt["completion_deadline_at"] = (
                    now + timedelta(seconds=config["completion_timeout_seconds"])
                ).isoformat()
            execution_started = attempt["execution_started_at"] is not None
            if requirement["status"] == "not_required":
                ci_status = "not_required"
            elif not execution_started:
                ci_status = "waiting_to_start"
            elif missing_checks:
                ci_status = "pending"
            else:
                ci_status = self._aggregate(checks)
            if (
                ci_status == "waiting_to_start"
                and attempt["start_deadline_at"] is not None
                and now >= self._timestamp(attempt["start_deadline_at"])
            ):
                ci_status = "start_timeout"
            if (
                ci_status == "pending"
                and attempt["completion_deadline_at"] is not None
                and now >= self._timestamp(attempt["completion_deadline_at"])
            ):
                ci_status = "completion_timeout"
            attempt["last_observed_at"] = now.isoformat()
            attempt["ci_status"] = ci_status
            attempt["required_checks"] = checks
            attempt["missing_required_checks"] = missing_checks
            attempt["workflow_runs"] = workflow_runs
            if ci_status == "failed":
                failed_runs = [
                    run
                    for run in workflow_runs
                    if run["conclusion"] not in {"", "SUCCESS"}
                ]
                if len(failed_runs) != 1:
                    code = "ci_workflow_run_missing" if not failed_runs else "ci_workflow_run_ambiguous"
                    raise blocked(
                        code,
                        "无法把当前 Head 的失败唯一绑定到一个 Workflow Run",
                        "请收紧 Profile workflows 映射并核对当前 Head 的 GitHub Actions 运行",
                    )
                attempt["workflow_run_id"] = failed_runs[0]["database_id"]
                attempt["workflow_run_url"] = failed_runs[0]["url"]
                if state["remediation_attempts_used"] >= config["max_remediation_attempts"]:
                    self._write_observation(paths, state, attempt, now)
                    raise blocked(
                        "ci_retry_exhausted",
                        "CI 修复预算已耗尽，当前 Head 仍失败",
                        "请进入风险决策并由研发工程师决定人工处理或拆分任务",
                    )
            self._write_observation(paths, state, attempt, now)
            next_action = {
                "waiting_to_start": "probe_ci",
                "pending": "probe_ci",
                "failed": "fetch_ci_artifact",
                "start_timeout": "analyze_ci_timeout",
                "completion_timeout": "analyze_ci_timeout",
                "passed": "none",
                "not_required": "none",
            }[ci_status]
            return {
                "current_stage": (
                    "completed"
                    if ci_status in {"passed", "not_required"}
                    else "ci_decision"
                    if ci_status in {"start_timeout", "completion_timeout"}
                    else "ci_validation"
                ),
                "ci_status": ci_status,
                "ci_requirement": requirement,
                "head_sha": head_sha,
                "pr_number": payload["number"],
                "pr_url": payload["url"],
                "required_checks": checks,
                "workflow_runs": workflow_runs,
                "attempt_id": attempt["attempt_id"],
                "started_at": attempt["started_at"],
                "start_deadline_at": attempt["start_deadline_at"],
                "execution_started_at": attempt["execution_started_at"],
                "completion_deadline_at": attempt["completion_deadline_at"],
                "missing_required_checks": missing_checks,
                "poll_interval_seconds": config["poll_interval_seconds"],
                "remediation_attempts_used": state["remediation_attempts_used"],
                "remediation_attempts_remaining": (
                    config["max_remediation_attempts"] - state["remediation_attempts_used"]
                ),
                "decision_required": ci_status
                in {"start_timeout", "completion_timeout"},
                "agentic_next_action": next_action,
            }

    def fetch_runner_log(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        """采集唯一失败 Run 的脱敏失败日志；超时则返回已知的不可用原因。"""

        paths = self._paths(manifest)
        repository = manifest["repository"]
        root = Path(repository["root"])
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            state = self._state(paths, manifest)
            attempt = self._current_attempt(state)
            existing = attempt.get("runner_log")
            if isinstance(existing, dict):
                return {
                    **existing,
                    "downloaded": False,
                    "agentic_next_action": self._runner_log_next_action(attempt),
                }
            if attempt["ci_status"] in {"start_timeout", "completion_timeout"}:
                runner_log = {
                    "available": False,
                    "reason": (
                        "workflow_run_not_observed"
                        if attempt["ci_status"] == "start_timeout"
                        else "workflow_run_not_completed"
                    ),
                    "workflow_run_id": attempt.get("workflow_run_id"),
                    "size_bytes": 0,
                    "sha256": None,
                    "excerpt": "",
                    "truncated": False,
                }
            elif attempt["ci_status"] != "failed" or not isinstance(
                attempt.get("workflow_run_id"), int
            ):
                raise blocked(
                    "ci_runner_log_not_ready",
                    "当前 CI Attempt 没有可唯一绑定的失败 Workflow Run",
                    "请先对当前 Head 执行 task-run probe-ci",
                )
            else:
                run_id = attempt["workflow_run_id"]
                result = self.run_text(
                    [
                        "gh",
                        "run",
                        "view",
                        str(run_id),
                        "--repo",
                        repository["slug"],
                        "--log-failed",
                    ],
                    root,
                    120,
                )
                if result.returncode != 0:
                    runner_log = {
                        "available": False,
                        "reason": "github_runner_log_read_failed",
                        "workflow_run_id": run_id,
                        "size_bytes": 0,
                        "sha256": None,
                        "excerpt": "",
                        "truncated": False,
                    }
                else:
                    raw = result.stdout
                    excerpt, truncated = _redact_runner_log(raw)
                    runner_log = {
                        "available": bool(raw.strip()),
                        "reason": "ok" if raw.strip() else "github_runner_log_empty",
                        "workflow_run_id": run_id,
                        "size_bytes": len(raw.encode("utf-8")),
                        "sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                        "excerpt": excerpt,
                        "truncated": truncated,
                    }
            reject_sensitive_content(runner_log)
            attempt["runner_log"] = runner_log
            atomic_write_json(paths["state"], state)
            return {
                **runner_log,
                "downloaded": True,
                "agentic_next_action": self._runner_log_next_action(attempt),
            }

    def fetch_artifact(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        config = self._config(manifest)
        paths = self._paths(manifest)
        repository = manifest["repository"]
        root = Path(repository["root"])
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            state = self._state(paths, manifest)
            attempt = self._current_attempt(state)
            if attempt["ci_status"] != "failed" or not attempt.get("workflow_run_id"):
                raise blocked(
                    "ci_artifact_not_ready",
                    "当前 CI Attempt 没有唯一失败 Workflow Run",
                    "请先对当前 Head 执行 task-run probe-ci",
                )
            run_id = attempt["workflow_run_id"]
            list_result = self.run_text(
                [
                    "gh",
                    "api",
                    f"repos/{repository['slug']}/actions/runs/{run_id}/artifacts",
                ],
                root,
                60,
            )
            if list_result.returncode != 0:
                raise blocked(
                    "ci_artifact_read_failed",
                    "GitHub Artifact 元数据读取失败",
                    "请检查 GitHub 授权后安全重试，不得猜测 Artifact",
                )
            listing = self._json(list_result.stdout, "GitHub Artifact")
            artifacts = listing.get("artifacts")
            if not isinstance(artifacts, list):
                raise blocked(
                    "ci_artifact_read_failed",
                    "GitHub Artifact 响应缺少 artifacts 数组",
                    "请升级 GitHub 适配器或人工核对 API 响应",
                )
            matched = []
            for raw in artifacts:
                if not isinstance(raw, dict) or raw.get("expired") is True:
                    continue
                name = raw.get("name")
                artifact_id = raw.get("id")
                if (
                    isinstance(name, str)
                    and isinstance(artifact_id, int)
                    and any(fnmatch.fnmatchcase(name, pattern) for pattern in config["artifact_name_patterns"])
                ):
                    matched.append(raw)
            if len(matched) != 1:
                code = "ci_artifact_missing" if not matched else "ci_artifact_ambiguous"
                raise blocked(
                    code,
                    "当前 Workflow Run 的 Artifact 不能唯一匹配 Profile",
                    "请核对 Artifact 是否过期并收紧 artifact_name_patterns",
                )
            metadata = matched[0]
            artifact_id = metadata["id"]
            existing = attempt.get("artifact")
            if isinstance(existing, dict) and existing.get("id") == artifact_id:
                return {
                    **existing,
                    "downloaded": False,
                    "agentic_next_action": "fetch_ci_runner_log",
                }
            result = self.run_bytes(
                ["gh", "api", f"repos/{repository['slug']}/actions/artifacts/{artifact_id}/zip"],
                root,
                120,
                config["limits"]["max_archive_bytes"],
            )
            if result.returncode != 0:
                raise blocked(
                    "ci_artifact_download_failed",
                    "GitHub Artifact 下载失败",
                    "请检查 Artifact 有效期和 GitHub 授权后安全重试",
                )
            archive = bytes(result.stdout)
            if not archive or len(archive) > config["limits"]["max_archive_bytes"]:
                raise blocked(
                    "ci_artifact_limit_exceeded",
                    "Artifact 为空或超过 Profile 压缩包上限",
                    "请由维护者核对安全上限，不得直接放宽后重试",
                )
            archive_sha = hashlib.sha256(archive).hexdigest()
            declared_digest = metadata.get("digest")
            if isinstance(declared_digest, str) and declared_digest.startswith("sha256:"):
                if declared_digest.removeprefix("sha256:") != archive_sha:
                    raise blocked(
                        "ci_artifact_digest_mismatch",
                        "Artifact 下载内容与 GitHub 声明摘要不一致",
                        "请停止使用该 Artifact 并人工核对 GitHub 运行",
                    )
            attempt_dir = paths["attempts"] / attempt["attempt_id"]
            self._ensure_managed_directory(attempt_dir)
            archive_path = attempt_dir / "artifact" / "artifact.bin"
            extracted_path = attempt_dir / "extracted"
            self._ensure_managed_directory(archive_path.parent)
            self._atomic_bytes(archive_path, archive)
            staging_root = Path(
                tempfile.mkdtemp(dir=attempt_dir, prefix=".artifact-extract-")
            )
            try:
                staging_path = staging_root / "content"
                extracted = extract_archive(archive, staging_path, config["limits"])
                if os.path.lexists(extracted_path):
                    raise blocked(
                        "ci_artifact_state_conflict",
                        "Artifact 展开证据已经存在，不能覆盖",
                        "请保留现场并使用新的 agentic_run_id 重建 CI Attempt",
                    )
                os.replace(staging_path, extracted_path)
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)
            artifact = {
                "id": artifact_id,
                "name": metadata["name"],
                "workflow_run_id": run_id,
                "sha256": archive_sha,
                "size_bytes": len(archive),
                "created_at": metadata.get("created_at"),
                "expires_at": metadata.get("expires_at"),
                "files": extracted,
                "archive_path": str(archive_path.relative_to(self.workspace.root)),
                "extracted_path": str(extracted_path.relative_to(self.workspace.root)),
            }
            reject_sensitive_content(artifact)
            attempt["artifact"] = artifact
            atomic_write_json(paths["state"], state)
            atomic_write_json(attempt_dir / "metadata.json", artifact)
            return {
                **artifact,
                "downloaded": True,
                "agentic_next_action": "fetch_ci_runner_log",
            }

    def parse_report(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        config = self._config(manifest)
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            state = self._state(paths, manifest)
            attempt = self._current_attempt(state)
            artifact = attempt.get("artifact")
            if not isinstance(artifact, dict):
                raise blocked(
                    "ci_artifact_not_ready",
                    "当前 Attempt 尚无经过 Runtime 校验的 Artifact",
                    "请先执行 task-run fetch-ci-artifact",
                )
            existing = attempt.get("report")
            if not isinstance(attempt.get("runner_log"), dict):
                raise blocked(
                    "ci_runner_log_not_ready",
                    "失败报告解析前必须先采集当前 Workflow Run 的 Runner 日志事实",
                    "请执行 task-run fetch-ci-runner-log；日志不可读时也会保留不可用原因",
                )
            expected_extracted = paths["attempts"] / attempt["attempt_id"] / "extracted"
            expected_relative = str(expected_extracted.relative_to(self.workspace.root))
            if artifact.get("extracted_path") != expected_relative:
                raise blocked(
                    "ci_artifact_state_conflict",
                    "Artifact 展开路径与当前 Attempt 的固定受管路径不一致",
                    "请停止并由 AgenticOps 维护者检查受管 CI 状态",
                )
            extracted = expected_extracted
            verify_extracted_artifact(extracted, artifact.get("files"), config["limits"])
            report = parse_maven_failsafe(extracted, artifact["sha256"])
            failure_event_id = "ci-failure-" + digest(
                {
                    "head_sha": attempt["head_sha"],
                    "artifact_sha256": artifact["sha256"],
                    "report_sha256": report["report_sha256"],
                }
            )[:24]
            report.update(
                {
                    "failure_event_id": failure_event_id,
                    "head_sha": attempt["head_sha"],
                    "artifact_id": artifact["id"],
                    "artifact_sha256": artifact["sha256"],
                    "parser": config["report_parser"],
                }
            )
            reject_sensitive_content(report)
            if isinstance(existing, dict):
                if existing != report:
                    raise blocked(
                        "ci_artifact_state_conflict",
                        "既有结构化报告与重新校验的 Artifact 内容不一致",
                        "请保留现场并由 AgenticOps 维护者检查受管 CI 状态",
                    )
                return {
                    **existing,
                    "runner_log": attempt["runner_log"],
                    "parsed": False,
                    "agentic_next_action": "present_ci_failure_decision",
                }
            attempt["failure_event_id"] = failure_event_id
            attempt["report"] = report
            attempt_dir = paths["attempts"] / attempt["attempt_id"]
            atomic_write_json(attempt_dir / "normalized-report.json", report)
            atomic_write_text(attempt_dir / "summary.md", report_summary(report))
            atomic_write_json(paths["state"], state)
            return {
                **report,
                "runner_log": attempt["runner_log"],
                "parsed": True,
                "agentic_next_action": "present_ci_failure_decision",
            }

    def authorize_remediation(
        self,
        manifest: Mapping[str, Any],
        *,
        failure_event_id: str,
        confirmed_by: str,
    ) -> dict[str, Any]:
        """记录用户针对当前失败证据作出的“修复”决定，不执行代码或外部写入。"""

        if not confirmed_by.strip() or len(confirmed_by) > 128:
            raise blocked(
                "ci_remediation_confirmation_invalid",
                "CI 修复决策缺少有效确认人",
                "请由当前研发工程师明确确认是否修复当前失败",
            )
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            state = self._state(paths, manifest)
            attempt = self._current_attempt(state)
            report = attempt.get("report")
            runner_log = attempt.get("runner_log")
            if (
                attempt["ci_status"] != "failed"
                or not isinstance(report, dict)
                or not isinstance(runner_log, dict)
                or attempt.get("failure_event_id") != failure_event_id
            ):
                raise blocked(
                    "ci_remediation_decision_not_ready",
                    "当前失败尚未形成绑定 Artifact 报告和 Runner 日志的决策包",
                    "请依次完成失败 Artifact、Runner 日志和结构化报告采集",
                )
            decisions = state["remediation_decisions"]
            candidate = {
                "failure_event_id": failure_event_id,
                "decision": "repair",
                "confirmed_by": confirmed_by.strip(),
                "confirmed_at": self._utc_now().isoformat(),
                "authorization_reference": manifest["authorization"]["reference"],
                "report_sha256": report["report_sha256"],
                "artifact_sha256": report["artifact_sha256"],
                "runner_log_sha256": runner_log.get("sha256"),
            }
            existing = decisions.get(failure_event_id)
            if isinstance(existing, dict):
                if {
                    key: existing.get(key)
                    for key in candidate
                    if key != "confirmed_at"
                } != {
                    key: candidate.get(key)
                    for key in candidate
                    if key != "confirmed_at"
                }:
                    raise blocked(
                        "ci_remediation_decision_conflict",
                        "同一 CI 失败已经绑定不同的用户修复决策",
                        "请保留现有决策；证据或范围变化时必须重新生成 CI Attempt",
                    )
                return {**existing, "authorized": False, "agentic_next_action": "repair_ci_code"}
            decisions[failure_event_id] = candidate
            atomic_write_json(paths["state"], state)
            return {**candidate, "authorized": True, "agentic_next_action": "repair_ci_code"}

    def record_remediation(
        self,
        manifest: Mapping[str, Any],
        *,
        failure_event_id: str,
        commit_sha: str,
        new_head_sha: str,
        authorization_reference: str,
        completed_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        config = self._config(manifest)
        if authorization_reference != manifest["authorization"]["reference"]:
            raise blocked(
                "ci_remediation_authorization_mismatch",
                "修复记录未绑定当前 manifest 授权",
                "请使用与 task-run open 相同的授权引用",
            )
        if not SHA_PATTERN.fullmatch(commit_sha) or not SHA_PATTERN.fullmatch(new_head_sha):
            raise blocked(
                "ci_remediation_sha_invalid",
                "修复 Commit 或新 Head 不是有效 Git SHA",
                "请完成受控提交、推送和 Git probe 后重试",
            )
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            state = self._state(paths, manifest)
            records = state["remediations"]
            existing = records.get(failure_event_id)
            candidate = {
                "failure_event_id": failure_event_id,
                "commit_sha": commit_sha,
                "new_head_sha": new_head_sha,
                "recorded_at": self._utc_now().isoformat(),
            }
            if isinstance(existing, dict):
                if existing["commit_sha"] != commit_sha or existing["new_head_sha"] != new_head_sha:
                    raise blocked(
                        "ci_remediation_event_conflict",
                        "同一失败事件已绑定不同修复提交",
                        "请保留原修复记录并核对外部 Head 变化",
                    )
                return self._remediation_result(state, config, existing, recorded=False)
            attempt = self._current_attempt(state)
            if attempt.get("failure_event_id") != failure_event_id:
                raise blocked(
                    "ci_failure_event_mismatch",
                    "失败事件与当前解析报告不一致",
                    "请使用当前 Attempt 返回的 failure_event_id",
                )
            if state["remediation_attempts_used"] >= config["max_remediation_attempts"]:
                raise blocked(
                    "ci_retry_exhausted",
                    "CI 修复预算已耗尽",
                    "请进入风险决策并由研发工程师决定后续处理",
                )
            decision = state["remediation_decisions"].get(failure_event_id)
            if (
                not isinstance(decision, dict)
                or decision.get("decision") != "repair"
                or decision.get("authorization_reference")
                != manifest["authorization"]["reference"]
            ):
                raise blocked(
                    "ci_remediation_decision_missing",
                    "当前 CI 失败尚无用户确认的修复决策",
                    "请先基于 Artifact 和 Runner 日志向用户给出决策包；仅在用户明确决定修复后记录修复授权",
                )
            classifications = [
                (index, event)
                for index, event in enumerate(completed_events)
                if event.get("event_id") == failure_event_id
                and event.get("action") == "failure"
            ]
            if (
                len(classifications) != 1
                or classifications[0][1].get("action_data", {}).get("code")
                != "ci_code_defect"
                or classifications[0][1].get("action_data", {}).get("retry_safe")
                is not True
            ):
                raise blocked(
                    "ci_failure_requires_human",
                    "当前 CI 失败没有被唯一、明确地归类为可重试的代码缺陷",
                    "依赖、环境、Runner、Workflow、配置、报告不可信或未知原因必须由研发工程师人工介入",
                )
            classification_index = classifications[0][0]
            branch_events = [
                event
                for index, event in enumerate(completed_events)
                if index > classification_index
                and event["action"] == "remote_branch_readback"
            ]
            if not branch_events:
                raise blocked(
                    "ci_remediation_readback_missing",
                    "修复后缺少 Runtime 可信远端任务分支回读",
                    "请依次完成验证、受控提交、推送和 probe-git",
                )
            latest = branch_events[-1]["action_data"]
            if (
                commit_sha != new_head_sha
                or latest["head_sha"] != new_head_sha
                or latest["sha"] != new_head_sha
                or latest["worktree_clean"] is not True
                or not {"git_commit", "git_push_task_branch"} <= set(latest["attributed_actions"])
            ):
                raise blocked(
                    "ci_remediation_readback_mismatch",
                    "修复提交、新 Head 或远端分支回读不一致",
                    "请停止并核对本地、远端与 PR Head，不得重复推送",
                )
            records[failure_event_id] = candidate
            state["remediation_attempts_used"] += 1
            atomic_write_json(paths["state"], state)
            return self._remediation_result(state, config, candidate, recorded=True)

    def current_state(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            return self._state(paths, manifest)

    def validate_completion(
        self, manifest: Mapping[str, Any], expected_head_sha: str
    ) -> dict[str, Any]:
        """在 task-run 已持锁的 finalize 路径中核对不可猜测的 CI 终态。"""
        state = self._state(self._paths(manifest), manifest)
        attempt = self._current_attempt(state)
        checks = attempt.get("required_checks")
        requirement = attempt.get("ci_requirement")
        ci_status = attempt.get("ci_status")
        passed = (
            ci_status == "passed"
            and isinstance(checks, list)
            and bool(checks)
            and all(item.get("conclusion") == "SUCCESS" for item in checks)
            and isinstance(requirement, dict)
            and requirement.get("status") == "required"
        )
        not_required = (
            ci_status == "not_required"
            and checks == []
            and isinstance(requirement, dict)
            and requirement.get("status") == "not_required"
        )
        if (
            state.get("current_head_sha") != expected_head_sha
            or attempt.get("head_sha") != expected_head_sha
            or not (passed or not_required)
        ):
            raise blocked(
                "ci_completion_not_verified",
                "development_change_v2 尚无绑定最终 PR Head 的 CI 要求判定或严格通过证据",
                "请对最终 Head 执行 task-run probe-ci；无需 CI 必须由 GitHub PR/Base Workflow 事实证明，需要 CI 时必须全部必需检查 SUCCESS",
            )
        return {
            "provider": "github-actions",
            "head_sha": expected_head_sha,
            "attempt_id": attempt["attempt_id"],
            "ci_status": ci_status,
            "ci_requirement": requirement,
            "started_at": attempt["started_at"],
            "execution_started_at": attempt["execution_started_at"],
            "finished_at": attempt["last_observed_at"],
            "start_deadline_at": attempt["start_deadline_at"],
            "completion_deadline_at": attempt["completion_deadline_at"],
            "required_checks": checks,
            "workflow_runs": attempt.get("workflow_runs", []),
            "artifact": attempt.get("artifact"),
            "report": attempt.get("report"),
            "remediations": sorted(
                state["remediations"].values(), key=lambda item: item["recorded_at"]
            ),
            "remediation_attempts_used": state["remediation_attempts_used"],
            "remediation_attempts_remaining": (
                self._config(manifest)["max_remediation_attempts"]
                - state["remediation_attempts_used"]
            ),
        }

    @staticmethod
    def _config(manifest: Mapping[str, Any]) -> dict[str, Any]:
        if manifest.get("process_id") != "development_change_v2":
            raise blocked(
                "ci_process_not_enabled",
                "当前 manifest 未显式启用 development_change_v2",
                "请保持 v1 行为，或基于已确认的 v2 Profile 重新生成 manifest",
            )
        return dict(manifest["pr_endpoint"]["ci"])

    def _paths(self, manifest: Mapping[str, Any]) -> dict[str, Path]:
        root = (
            self.workspace.root
            / ".agentic-ops"
            / "tasks"
            / manifest["issue"]["key"]
            / "runs"
            / manifest["agent"]["agentic_run_id"]
        )
        ci = root / "ci"
        paths = {
            "root": ci,
            "lock": root / ".ci.lock",
            "state": ci / "state.json",
            "observations": ci / "observations.ndjson",
            "attempts": ci / "attempts",
        }
        for candidate in paths.values():
            try:
                candidate.resolve().relative_to(self.workspace.root)
            except ValueError as error:
                raise blocked(
                    "workspace_path_escape",
                    "CI 受管路径越出业务工作空间",
                    "请停止并交给 AgenticOps 维护者调查",
                ) from error
        return paths

    def _load_or_create_state(
        self,
        paths: Mapping[str, Path],
        manifest: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        if paths["state"].exists():
            return self._state(paths, manifest)
        state = {
            "schema_version": 3,
            "protocol": CI_PROTOCOL,
            "issue_key": manifest["issue"]["key"],
            "agentic_run_id": manifest["agent"]["agentic_run_id"],
            "config_sha256": digest(config),
            "current_head_sha": None,
            "attempts": {},
            "remediation_attempts_used": 0,
            "remediations": {},
            "remediation_decisions": {},
        }
        atomic_write_json(paths["state"], state)
        return state

    def _state(self, paths: Mapping[str, Path], manifest: Mapping[str, Any]) -> dict[str, Any]:
        if not paths["state"].is_file():
            raise blocked(
                "ci_state_missing",
                "当前 task-run 尚未建立 CI 状态",
                "请先执行 task-run probe-ci",
            )
        state = read_json(paths["state"])
        if (
            state.get("schema_version") != 3
            or state.get("protocol") != CI_PROTOCOL
            or state.get("issue_key") != manifest["issue"]["key"]
            or state.get("agentic_run_id") != manifest["agent"]["agentic_run_id"]
            or state.get("config_sha256") != digest(self._config(manifest))
            or not isinstance(state.get("attempts"), dict)
            or not isinstance(state.get("remediations"), dict)
            or not isinstance(state.get("remediation_decisions"), dict)
            or isinstance(state.get("remediation_attempts_used"), bool)
            or not isinstance(state.get("remediation_attempts_used"), int)
        ):
            raise blocked(
                "ci_state_invalid",
                "CI 状态与当前 Issue、运行或 Profile 配置不一致",
                "请停止使用该运行并交给 AgenticOps 维护者调查",
            )
        return state

    @staticmethod
    def _current_attempt(state: Mapping[str, Any]) -> dict[str, Any]:
        head = state.get("current_head_sha")
        attempt = state["attempts"].get(head) if isinstance(head, str) else None
        if not isinstance(attempt, dict):
            raise blocked(
                "ci_attempt_missing",
                "CI 状态缺少当前 Head Attempt",
                "请先对当前 PR Head 执行 task-run probe-ci",
            )
        return attempt

    def _write_observation(
        self,
        paths: Mapping[str, Path],
        state: dict[str, Any],
        attempt: Mapping[str, Any],
        now: datetime,
    ) -> None:
        state["current_head_sha"] = attempt["head_sha"]
        atomic_write_json(paths["state"], state)
        append_ndjson(
            paths["observations"],
            {
                "observed_at": now.isoformat(),
                "attempt_id": attempt["attempt_id"],
                "head_sha": attempt["head_sha"],
                "ci_status": attempt["ci_status"],
                "ci_requirement": attempt["ci_requirement"],
                "required_checks": attempt["required_checks"],
                "workflow_run_ids": [run["database_id"] for run in attempt["workflow_runs"]],
            },
        )

    @staticmethod
    def _runner_log_next_action(attempt: Mapping[str, Any]) -> str:
        return (
            "parse_ci_report"
            if attempt.get("ci_status") == "failed"
            else "analyze_ci_timeout"
        )

    def _workflow_runs(
        self,
        manifest: Mapping[str, Any],
        config: Mapping[str, Any],
        head_sha: str,
        root: Path,
    ) -> list[dict[str, Any]]:
        repository = manifest["repository"]
        result = self.run_text(
            [
                "gh",
                "run",
                "list",
                "--repo",
                repository["slug"],
                "--commit",
                head_sha,
                "--limit",
                "100",
                "--json",
                "databaseId,workflowName,headSha,status,conclusion,url,createdAt,updatedAt",
            ],
            root,
            60,
        )
        if result.returncode != 0:
            raise blocked(
                "ci_observation_failed",
                "GitHub Workflow Run 只读回读失败",
                "请检查 GitHub 授权和服务状态后安全重试",
            )
        payload = self._json_value(result.stdout, "GitHub Workflow Run")
        if not isinstance(payload, list):
            raise blocked(
                "ci_observation_failed",
                "GitHub Workflow Run 响应不是数组",
                "请升级 GitHub 适配器后重试",
            )
        selected: list[dict[str, Any]] = []
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            if raw.get("headSha") != head_sha or raw.get("workflowName") not in config["workflows"]:
                continue
            database_id = raw.get("databaseId")
            if not isinstance(database_id, int) or database_id < 1:
                raise blocked(
                    "ci_status_unknown",
                    "Workflow Run 缺少稳定 databaseId",
                    "请升级 GitHub 适配器后重试",
                )
            selected.append(
                {
                    "database_id": database_id,
                    "workflow": raw["workflowName"],
                    "head_sha": head_sha,
                    "status": str(raw.get("status") or "").upper(),
                    "conclusion": str(raw.get("conclusion") or "").upper(),
                    "url": str(raw.get("url") or ""),
                    "created_at": raw.get("createdAt"),
                    "updated_at": raw.get("updatedAt"),
                }
            )
        return sorted(selected, key=lambda item: item["database_id"])

    def _github_pr_ci_requirement(
        self,
        manifest: Mapping[str, Any],
        config: Mapping[str, Any],
        base_sha: str,
        root: Path,
    ) -> dict[str, Any]:
        repository = manifest["repository"]
        tree_result = self.run_text(
            [
                "gh",
                "api",
                f"repos/{repository['slug']}/git/trees/{base_sha}?recursive=1",
            ],
            root,
            60,
        )
        if tree_result.returncode != 0:
            raise blocked(
                "ci_requirement_observation_failed",
                "无法从 GitHub 读取 PR Base 提交的 Workflow 树",
                "请检查 GitHub 授权和服务状态；CI 要求未知时不得跳过验证",
            )
        tree_payload = self._json(tree_result.stdout, "GitHub PR Base Workflow 树")
        if tree_payload.get("truncated") is True or not isinstance(tree_payload.get("tree"), list):
            raise blocked(
                "ci_requirement_unknown",
                "GitHub PR Base Workflow 树不完整",
                "请人工核对 PR Base 上的 GitHub Actions Workflow，不能把未知状态当作无需 CI",
            )
        workflow_blobs: list[tuple[str, str]] = []
        for item in tree_payload["tree"]:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            blob_sha = item.get("sha")
            if not isinstance(path, str) or re.fullmatch(
                r"\.github/workflows/[^/]+\.ya?ml", path
            ) is None:
                continue
            if (
                item.get("type") != "blob"
                or not isinstance(blob_sha, str)
                or SHA_PATTERN.fullmatch(blob_sha) is None
            ):
                raise blocked(
                    "ci_requirement_unknown",
                    f"GitHub Workflow 树节点无效：{path}",
                    "请停止自动判定并人工核对 GitHub 返回事实",
                )
            workflow_blobs.append((path, blob_sha))
        workflow_blobs.sort()
        if not workflow_blobs:
            return {
                "status": "not_required",
                "source": "github_pr",
                "base_sha": base_sha,
                "reason": "base_has_no_github_actions_workflows",
                "workflow_files": [],
            }
        if len(workflow_blobs) > 100:
            raise blocked(
                "ci_requirement_unknown",
                "PR Base 的 Workflow 文件数量超过自动判定上限",
                "请人工核对 CI 要求并由维护者评估是否调整版本化上限",
            )
        workflow_files = [
            self._github_workflow_blob(repository["slug"], path, blob_sha, root)
            for path, blob_sha in workflow_blobs
        ]
        expected = set(config["workflows"])
        matched = [item for item in workflow_files if item["name"] in expected]
        matched_names = {item["name"] for item in matched}
        if matched_names != expected or len(matched) != len(expected):
            raise blocked(
                "ci_requirement_unknown",
                "GitHub PR Base 的 Workflow 与已确认 CI 配置不能精确匹配",
                "请更新 Project Profile/manifest 或人工确认项目 CI 规则；不得静默跳过",
            )
        if any(item["conditional_head_trigger"] for item in matched):
            raise blocked(
                "ci_requirement_unknown",
                "已配置 Workflow 的 PR/Head 触发器包含条件，当前版本不能等价执行 GitHub 过滤语义",
                "请人工核对 PR changed files、目标分支与 Workflow paths/branches 条件",
            )
        applicable = [item for item in matched if item["head_trigger"]]
        if applicable and len(applicable) != len(matched):
            raise blocked(
                "ci_requirement_unknown",
                "已配置 Workflow 对当前 PR Head 的触发语义不一致",
                "请拆分或收紧 CI Workflow 配置后重新确认",
            )
        public_matched = [
            {key: value for key, value in item.items() if key != "conditional_head_trigger"}
            for item in matched
        ]
        return {
            "status": "required" if applicable else "not_required",
            "source": "github_pr",
            "base_sha": base_sha,
            "reason": (
                "configured_workflows_trigger_for_pr_head"
                if applicable
                else "configured_workflows_do_not_trigger_for_pr_head"
            ),
            "workflow_files": public_matched,
        }

    def _github_workflow_blob(
        self, repository_slug: str, path: str, blob_sha: str, root: Path
    ) -> dict[str, Any]:
        result = self.run_text(
            ["gh", "api", f"repos/{repository_slug}/git/blobs/{blob_sha}"],
            root,
            60,
        )
        if result.returncode != 0:
            raise blocked(
                "ci_requirement_observation_failed",
                f"无法从 GitHub 读取 PR Base Workflow：{path}",
                "请检查 GitHub 授权和服务状态；CI 要求未知时不得跳过验证",
            )
        payload = self._json(result.stdout, f"GitHub Workflow {path}")
        if payload.get("sha") != blob_sha or payload.get("encoding") != "base64":
            raise blocked(
                "ci_requirement_unknown",
                f"GitHub Workflow 内容未绑定预期 Blob：{path}",
                "请停止自动判定并人工核对 GitHub 返回事实",
            )
        encoded = payload.get("content")
        if not isinstance(encoded, str) or len(encoded) > 1_400_000:
            raise blocked(
                "ci_requirement_unknown",
                f"GitHub Workflow 缺少内容或超过 1 MiB 判定上限：{path}",
                "请停止自动判定并人工核对 GitHub 返回事实",
            )
        try:
            content = base64.b64decode("".join(encoded.split()), validate=True)
            text = content.decode("utf-8")
            document = yaml.load(text, Loader=yaml.BaseLoader)
        except (ValueError, UnicodeDecodeError, yaml.YAMLError) as error:
            raise blocked(
                "ci_requirement_unknown",
                f"GitHub Workflow 不是可判定的 UTF-8 YAML：{path}",
                "请修复 Workflow 格式或人工确认 CI 要求",
            ) from error
        if not isinstance(document, dict):
            raise blocked(
                "ci_requirement_unknown",
                f"GitHub Workflow 顶层不是对象：{path}",
                "请修复 Workflow 格式或人工确认 CI 要求",
            )
        name = document.get("name")
        trigger = document.get("on")
        if not isinstance(name, str) or not name.strip():
            name = Path(path).name
        events: set[str] = set()
        conditional_head_trigger = False
        if isinstance(trigger, str):
            events.add(trigger)
        elif isinstance(trigger, list):
            events.update(item for item in trigger if isinstance(item, str))
        elif isinstance(trigger, dict):
            events.update(str(item) for item in trigger)
            conditional_head_trigger = any(
                event in {"pull_request", "push"}
                and settings not in (None, "", {})
                for event, settings in trigger.items()
            )
        else:
            raise blocked(
                "ci_requirement_unknown",
                f"GitHub Workflow 缺少可判定的 on 触发器：{path}",
                "请修复 Workflow 触发配置或人工确认 CI 要求",
            )
        normalized_events = sorted(item.strip() for item in events if item.strip())
        return {
            "path": path,
            "blob_sha": blob_sha,
            "name": name.strip(),
            "triggers": normalized_events,
            "head_trigger": bool({"pull_request", "push"}.intersection(normalized_events)),
            "conditional_head_trigger": conditional_head_trigger,
        }

    @staticmethod
    def _required_checks(
        raw: object, config: Mapping[str, Any]
    ) -> tuple[list[dict[str, str]], list[str]]:
        if not isinstance(raw, list):
            raise blocked(
                "ci_status_unknown",
                "PR CI 响应缺少 statusCheckRollup 数组",
                "请升级 GitHub 适配器后重试",
            )
        by_name: dict[str, list[dict[str, Any]]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("context")
            if isinstance(name, str) and name.strip():
                by_name.setdefault(name.strip(), []).append(item)
        checks: list[dict[str, str]] = []
        missing: list[str] = []
        for required in config["required_checks"]:
            matches = by_name.get(required, [])
            if not matches:
                missing.append(required)
                continue
            if len(matches) != 1:
                raise blocked(
                    "ci_required_check_duplicate",
                    f"当前 Head 的必需检查不唯一：{required}",
                    "请修复 Workflow 命名或收紧 required_checks 映射",
                )
            item = matches[0]
            checks.append(
                {
                    "name": required,
                    "status": str(item.get("status") or "").upper(),
                    "conclusion": str(item.get("conclusion") or item.get("state") or "").upper(),
                }
            )
        return checks, missing

    @staticmethod
    def _aggregate(checks: list[Mapping[str, str]]) -> str:
        values = []
        for check in checks:
            conclusion = check["conclusion"]
            status = check["status"]
            value = conclusion or status
            if value == "SUCCESS":
                values.append("passed")
            elif value in _PENDING or (not conclusion and status in _PENDING):
                values.append("pending")
            elif value in _FAILED:
                values.append("failed")
            else:
                raise blocked(
                    "ci_status_unknown",
                    f"必需检查出现未知状态：{check['name']}={value or '<empty>'}",
                    "请升级状态映射或人工核对，未知状态不得降级为 pending/passed",
                )
        if all(value == "passed" for value in values):
            return "passed"
        if any(value == "failed" for value in values):
            return "failed"
        return "pending"

    @staticmethod
    def _json(content: str, label: str) -> dict[str, Any]:
        value = CiRuntime._json_value(content, label)
        if not isinstance(value, dict):
            raise blocked(
                "ci_observation_failed",
                f"{label} 响应不是 JSON 对象",
                "请升级 GitHub 适配器后重试",
            )
        return value

    @staticmethod
    def _json_value(content: str, label: str) -> object:
        try:
            return json.loads(content)
        except json.JSONDecodeError as error:
            raise blocked(
                "ci_observation_failed",
                f"{label} 响应不是有效 JSON",
                "请升级 GitHub 适配器后重试",
            ) from error

    def _utc_now(self) -> datetime:
        value = self.now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _atomic_bytes(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(path):
            raise blocked(
                "ci_artifact_state_conflict",
                "Artifact 归档证据已经存在，不能覆盖",
                "请保留现场并使用新的 agentic_run_id 重建 CI Attempt",
            )
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        temporary = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _ensure_managed_directory(self, directory: Path) -> None:
        try:
            relative = directory.relative_to(self.workspace.root)
        except ValueError as error:
            raise blocked(
                "workspace_path_escape",
                "Artifact 受管目录越出业务工作空间",
                "请停止并由 AgenticOps 维护者检查路径绑定",
            ) from error
        current = self.workspace.root
        for part in relative.parts:
            current = current / part
            if os.path.lexists(current):
                if current.is_symlink() or not current.is_dir():
                    raise blocked(
                        "ci_artifact_unsafe",
                        "Artifact 受管目录包含链接或非目录节点",
                        "请停止并由 AgenticOps 维护者检查工作空间受管目录",
                    )
                continue
            current.mkdir(mode=0o700)

    @staticmethod
    def _remediation_result(
        state: Mapping[str, Any],
        config: Mapping[str, Any],
        record: Mapping[str, Any],
        *,
        recorded: bool,
    ) -> dict[str, Any]:
        used = state["remediation_attempts_used"]
        return {
            **record,
            "recorded": recorded,
            "remediation_attempts_used": used,
            "remediation_attempts_remaining": config["max_remediation_attempts"] - used,
            "agentic_next_action": "probe_ci",
        }


def extract_archive(content: bytes, destination: Path, limits: Mapping[str, int]) -> list[dict[str, Any]]:
    if destination.exists():
        raise blocked(
            "ci_artifact_state_conflict",
            "Artifact 展开目录已经存在，不能覆盖既有受管证据",
            "请保留现场并使用新的 agentic_run_id 重建 CI Attempt",
        )
    destination.mkdir(parents=True, mode=0o700)
    entries: list[tuple[str, int, Callable[[], Any]]] = []
    archive: Any
    if zipfile.is_zipfile(io.BytesIO(content)):
        archive = zipfile.ZipFile(io.BytesIO(content))
        for info in archive.infolist():
            mode = (info.external_attr >> 16) & 0o170000
            if info.is_dir():
                continue
            if mode and mode != stat.S_IFREG:
                raise _unsafe_archive("ZIP 包含链接或特殊文件")
            entries.append((info.filename, info.file_size, lambda item=info: archive.open(item, "r")))
    else:
        try:
            archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")
        except tarfile.TarError as error:
            raise blocked(
                "ci_artifact_format_unsupported",
                "Artifact 不是受支持的 ZIP、TAR 或 TAR.GZ",
                "请调整 Workflow Artifact 格式或补充版本化解析能力",
            ) from error
        for member in archive.getmembers():
            if member.isdir():
                continue
            if not member.isfile():
                raise _unsafe_archive("TAR 包含链接或特殊文件")
            entries.append((member.name, member.size, lambda item=member: archive.extractfile(item)))
    if len(entries) > limits["max_files"]:
        raise _limit_archive("文件数量超过上限")
    total = sum(size for _, size, _ in entries)
    if total > limits["max_extracted_bytes"]:
        raise _limit_archive("展开总量超过上限")
    extracted: list[dict[str, Any]] = []
    try:
        for name, size, opener in entries:
            relative = _safe_archive_path(name, limits["max_depth"])
            if size > limits["max_file_bytes"]:
                raise _limit_archive(f"单文件超过上限：{relative}")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = opener()
            if source is None:
                raise _unsafe_archive(f"无法读取普通文件：{relative}")
            hasher = hashlib.sha256()
            written = 0
            with source, target.open("xb") as output:
                os.chmod(target, 0o600)
                while True:
                    chunk = source.read(65_536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > size or written > limits["max_file_bytes"]:
                        raise _limit_archive(f"展开内容超过声明大小：{relative}")
                    hasher.update(chunk)
                    output.write(chunk)
            if written != size:
                raise _unsafe_archive(f"展开大小与声明不一致：{relative}")
            extracted.append({"path": str(relative), "size_bytes": written, "sha256": hasher.hexdigest()})
    finally:
        archive.close()
    return sorted(extracted, key=lambda item: item["path"])


def verify_extracted_artifact(
    root: Path, expected_files: object, limits: Mapping[str, int]
) -> None:
    if root.is_symlink() or not root.is_dir() or not isinstance(expected_files, list):
        raise _unsafe_archive("Artifact 展开证据或文件清单不安全")
    observed: list[dict[str, Any]] = []
    total = 0
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in directory_names:
            child = parent / name
            if child.is_symlink() or not child.is_dir():
                raise _unsafe_archive("Artifact 展开证据包含链接或特殊目录")
        for name in file_names:
            path = parent / name
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise _unsafe_archive("Artifact 展开证据包含链接、特殊文件或多链接文件")
            relative = path.relative_to(root)
            _safe_archive_path(relative.as_posix(), limits["max_depth"])
            if before.st_size > limits["max_file_bytes"]:
                raise _limit_archive(f"单文件超过上限：{relative}")
            hasher = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                opened = os.fstat(stream.fileno())
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise _unsafe_archive("Artifact 文件在校验期间发生替换")
                while True:
                    chunk = stream.read(65_536)
                    if not chunk:
                        break
                    size += len(chunk)
                    total += len(chunk)
                    if size > limits["max_file_bytes"] or total > limits["max_extracted_bytes"]:
                        raise _limit_archive("Artifact 展开内容超过上限")
                    hasher.update(chunk)
            observed.append(
                {
                    "path": relative.as_posix(),
                    "size_bytes": size,
                    "sha256": hasher.hexdigest(),
                }
            )
            if len(observed) > limits["max_files"]:
                raise _limit_archive("文件数量超过上限")
    if sorted(observed, key=lambda item: item["path"]) != expected_files:
        raise blocked(
            "ci_artifact_digest_mismatch",
            "Artifact 展开证据与下载时记录的文件清单或摘要不一致",
            "请停止解析并由研发工程师人工介入，不得基于已变化的报告自动修复",
        )


def parse_maven_failsafe(root: Path, artifact_sha256: str) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise _unsafe_archive("Failsafe 报告根目录不安全")
    xml_files = sorted(path for path in root.rglob("TEST-*.xml") if path.is_file())
    if not xml_files:
        raise blocked(
            "ci_report_unsupported",
            "Artifact 中没有 Maven Failsafe TEST-*.xml",
            "请核对 report_parser 与 Workflow Artifact 内容",
        )
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    failures: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    for path in xml_files:
        if path.is_symlink() or path.stat().st_nlink != 1:
            raise _unsafe_archive("Failsafe 报告包含链接或非单链接文件")
        data = path.read_bytes()
        upper = data.upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise blocked(
                "ci_report_parse_failed",
                "Failsafe XML 包含 DTD 或实体声明",
                "请停止解析并修复测试报告生成方式",
            )
        try:
            document = ElementTree.fromstring(data)
        except ElementTree.ParseError as error:
            raise blocked(
                "ci_report_parse_failed",
                "Failsafe XML 已损坏或格式不完整",
                "请人工核对报告并修复 Workflow",
            ) from error
        suites = [document] if document.tag.endswith("testsuite") else [item for item in document.iter() if item.tag.endswith("testsuite")]
        if not suites:
            raise blocked(
                "ci_report_parse_failed",
                "Failsafe XML 缺少 testsuite",
                "请核对 Maven Failsafe 报告版本",
            )
        for suite in suites:
            for field in totals:
                raw = suite.attrib.get(field, "0")
                try:
                    value = int(raw)
                except ValueError as error:
                    raise blocked(
                        "ci_report_parse_failed",
                        f"Failsafe 统计字段不是整数：{field}",
                        "请核对报告完整性",
                    ) from error
                if value < 0:
                    raise blocked("ci_report_parse_failed", "Failsafe 统计字段为负数", "请核对报告完整性")
                totals[field] += value
            for case in (item for item in suite if item.tag.endswith("testcase")):
                for child in case:
                    kind = child.tag.rsplit("}", 1)[-1]
                    if kind not in {"failure", "error"}:
                        continue
                    failures.append(
                        {
                            "suite": _redact(str(suite.attrib.get("name") or "unknown")),
                            "test": _redact(str(case.attrib.get("name") or "unknown")),
                            "class": _redact(str(case.attrib.get("classname") or "unknown")),
                            "kind": kind,
                            "exception": _redact(str(child.attrib.get("type") or "unknown")),
                            "message": _redact(str(child.attrib.get("message") or child.text or "no message")),
                            "classification": "unknown",
                        }
                    )
        relative = path.relative_to(root)
        files.append({"path": str(relative), "sha256": hashlib.sha256(data).hexdigest()})
    if totals["failures"] + totals["errors"] != len(failures):
        raise blocked(
            "ci_report_parse_failed",
            "Failsafe 汇总统计与失败 Test Case 数量冲突",
            "请人工核对报告，不得由 AI 猜测补全",
        )
    for item in failures:
        item["failure_fingerprint"] = digest(item)[:24]

    summary_files = sorted(path for path in root.rglob("failsafe-summary.xml") if path.is_file())
    if len(summary_files) > 1:
        raise blocked(
            "ci_report_parse_failed",
            "Artifact 包含多个 failsafe-summary.xml",
            "请修复 Workflow Artifact 内容，不能猜测采用哪一份汇总",
        )
    if summary_files:
        summary_path = summary_files[0]
        _validate_report_file(summary_path)
        data = summary_path.read_bytes()
        if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
            raise blocked(
                "ci_report_parse_failed",
                "Failsafe summary 包含 DTD 或实体声明",
                "请停止解析并修复测试报告生成方式",
            )
        try:
            summary = ElementTree.fromstring(data)
        except ElementTree.ParseError as error:
            raise blocked(
                "ci_report_parse_failed",
                "failsafe-summary.xml 已损坏",
                "请人工核对报告并修复 Workflow",
            ) from error
        summary_counts: dict[str, int] = {}
        for field in ("failures", "errors", "skipped"):
            elements = [element for element in summary if element.tag.rsplit("}", 1)[-1] == field]
            if len(elements) != 1 or elements[0].text is None:
                raise blocked(
                    "ci_report_parse_failed",
                    f"failsafe-summary.xml 缺少唯一 {field}",
                    "请核对 Maven Failsafe 报告版本",
                )
            try:
                summary_counts[field] = int(elements[0].text.strip())
            except ValueError as error:
                raise blocked(
                    "ci_report_parse_failed",
                    f"failsafe-summary.xml 的 {field} 不是整数",
                    "请核对报告完整性",
                ) from error
        if any(summary_counts[field] != totals[field] for field in summary_counts):
            raise blocked(
                "ci_report_parse_failed",
                "failsafe-summary.xml 与 TEST-*.xml 汇总冲突",
                "请人工核对报告，不得由 AI 猜测补全",
            )
        files.append(
            {
                "path": str(summary_path.relative_to(root)),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )

    text_files = sorted(path for path in root.rglob("*.txt") if path.is_file())
    for path in text_files:
        _validate_report_file(path)
        data = path.read_bytes()
        files.append(
            {
                "path": str(path.relative_to(root)),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    files.sort(key=lambda item: item["path"])
    report = {
        **totals,
        "failed_tests": failures,
        "report_files": files,
        "artifact_sha256": artifact_sha256,
        "report_sha256": digest({"totals": totals, "failures": failures, "files": files}),
        "requires_failure_classification": bool(failures),
        "failure_classifications": sorted({item["classification"] for item in failures}),
    }
    reject_sensitive_content(report)
    return report


def _validate_report_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise _unsafe_archive("Failsafe 报告包含链接或非单链接文件")


def report_summary(report: Mapping[str, Any]) -> str:
    lines = [
        "# Maven Failsafe 脱敏失败摘要",
        "",
        f"- tests: {report['tests']}",
        f"- failures: {report['failures']}",
        f"- errors: {report['errors']}",
        f"- skipped: {report['skipped']}",
        f"- report_sha256: `{report['report_sha256']}`",
        "",
        "## 失败用例",
    ]
    for item in report["failed_tests"]:
        lines.append(
            f"- `{item['class']}#{item['test']}`（`{item['failure_fingerprint']}`）："
            f"{item['exception']} — {item['message']}"
        )
    return "\n".join(lines)


def _safe_archive_path(name: str, max_depth: int) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(path.parts) > max_depth
    ):
        raise _unsafe_archive(f"Artifact 路径不安全：{name}")
    return path


def _redact(value: str) -> str:
    text = " ".join(value.replace("\x00", " ").split())[:1000]
    for pattern in _REDACTIONS:
        text = pattern.sub("[REDACTED]", text)
    return text or "empty"


def _redact_runner_log(value: str) -> tuple[str, bool]:
    """保留可诊断的失败日志片段，但绝不保存原始 Runner 输出。"""

    remaining = RUNNER_LOG_EXCERPT_BYTES
    lines: list[str] = []
    truncated = False
    for raw_line in value.replace("\x00", " ").splitlines():
        line = raw_line.rstrip()
        for pattern in _REDACTIONS:
            line = pattern.sub("[REDACTED]", line)
        encoded = line.encode("utf-8", errors="replace")
        separator = 1 if lines else 0
        if len(encoded) + separator > remaining:
            truncated = True
            break
        lines.append(line)
        remaining -= len(encoded) + separator
    if not truncated and len("\n".join(lines).encode("utf-8")) < len(
        value.replace("\x00", " ").encode("utf-8")
    ):
        truncated = True
    return "\n".join(lines), truncated


def _unsafe_archive(detail: str) -> Exception:
    return blocked(
        "ci_artifact_unsafe",
        detail,
        "请停止使用该 Artifact 并由维护者检查 Workflow 与安全边界",
    )


def _limit_archive(detail: str) -> Exception:
    return blocked(
        "ci_artifact_limit_exceeded",
        detail,
        "请由维护者核对版本化安全上限，不得临时绕过",
    )
