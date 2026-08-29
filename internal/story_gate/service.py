from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from internal.story_gate.io import atomic_write_json, read_json
from internal.story_gate.locking import TaskLock
from internal.story_gate.errors import EXIT_BLOCKED, EXIT_CAPABILITY_GAP, StoryGateError
from internal.story_gate.branch_policy import (
    PullRequestFact,
    read_pull_request_fact,
    resolve_branch_review,
)
from internal.story_gate.git_changes import collect_changes
from internal.story_gate.model import StoryImpact, StoryRegistry
from internal.story_gate.registry import load_story_registry, path_matches

GOVERNED_PATHS = ("**",)
AUTHORIZATION_RECORD_SCHEMA_VERSION = 4
_ISSUE_KEY = r"AO-[1-9][0-9]*"
_COMMIT_CONFIRMATION_REFERENCE = re.compile(
    rf"user-confirmation:(?P<issue_key>{_ISSUE_KEY}):commit:(?P<commit_sha>[0-9a-f]{{40,64}})"
)
_PR_REVIEW_REFERENCE = re.compile(
    rf"github-pr-review:(?P<issue_key>{_ISSUE_KEY}):(?P<pr_number>[1-9][0-9]*):(?P<head_sha>[0-9a-f]{{40,64}})"
)


class StoryGateService:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def inspect(
        self,
        source: str,
        *,
        base: str | None = None,
        head: str | None = None,
        enforce: bool = True,
    ) -> dict[str, Any]:
        registry, impact = self._calculate(source, base=base, head=head)
        evidence = self._read_matching_evidence(impact)
        report = _review_report(registry, impact)
        report_digest = _digest(report)
        approval = self._read_matching_approval(impact, report_digest)
        result = self._result(registry, impact, report, report_digest, approval=approval, evidence=evidence)

        if not impact.has_impact:
            return result
        if impact.unmapped_paths:
            if enforce:
                raise self._blocked(
                    "story_mapping_missing",
                    "代码变更命中项目治理范围，但没有对应故事映射",
                    "请查阅审查报告中的未映射路径，补齐故事映射后重新检查",
                    result,
                    EXIT_CAPABILITY_GAP,
                )
            return result

        gate_stage = os.environ.get("AGENTIC_OPS_STORY_GATE_STAGE", "").strip()
        if gate_stage == "pre_commit":
            if evidence is None:
                raise self._blocked(
                    "story_acceptance_missing",
                    "候选提交尚未完成当前内容的固定验收",
                    "请运行审查报告列出的固定验收；pre-commit 不要求提前人工批准",
                    result,
                )
            return result
        if gate_stage in {"pre_push", "release"}:
            if impact.review_channel in {"protected", "special"}:
                raise self._blocked(
                    "story_review_channel_protected",
                    "当前分支不能使用普通故事审查通道推送",
                    "请使用版本化发布或 Hotfix 专用流程",
                    result,
                )
            if evidence is None:
                raise self._blocked(
                    "story_acceptance_missing",
                    "待推送范围尚未完成同一内容的固定验收",
                    "请对待推送 range 运行 story-gate verify 后重试",
                    result,
                )
            if impact.review_channel == "commit_review" and approval is None:
                raise self._blocked(
                    "story_commit_review_required",
                    "待推送提交尚未获得推送前人工确认",
                    "请审阅报告中的提交编号、确认事项、变更点和风险；确认后再推送",
                    result,
                )
            return result
        return result

    def approve(
        self,
        source: str,
        impact_id: str,
        authorization_reference: str,
        *,
        base: str | None = None,
        head: str | None = None,
    ) -> dict[str, Any]:
        registry, impact = self._calculate(source, base=base, head=head)
        report = _review_report(registry, impact)
        report_digest = _digest(report)
        evidence = self._read_matching_evidence(impact)
        result = self._result(registry, impact, report, report_digest, evidence=evidence)
        if impact.impact_id != impact_id:
            raise self._input_error(
                "story_impact_changed", "当前 Git 变更与待确认内容不一致", "请重新生成提交或 PR 审查报告，并确认新的代码事实", result
            )
        if not impact.has_impact or impact.unmapped_paths:
            raise self._input_error(
                "story_mapping_missing", "当前报告为空或仍有未映射路径，不能批准", "请先查阅报告并补齐故事映射", result
            )
        if source != "range" or not impact.commit_sha:
            raise self._input_error(
                "story_review_fact_not_ready", "尚未形成可供人工审查的 commit 或 PR", "请先完成候选验收并形成所属分支通道的代码事实", result
            )
        if evidence is None:
            raise self._input_error(
                "story_acceptance_missing", "当前审查内容尚未完成固定验收", "请先运行报告列出的固定验收", result
            )
        reference = authorization_reference.strip()
        authorization, invalid_reason = _authorization_metadata(reference, impact)
        if authorization is None:
            message = {
                "missing": "缺少人工审查事实引用",
                "review_object_mismatch": "人工审查事实没有绑定当前 commit 或 PR Head",
                "pr_review_missing": "PR 当前 Head 没有有效的独立人工批准",
            }.get(invalid_reason, "人工审查事实引用格式无效")
            raise self._input_error(
                "story_authorization_reference_invalid",
                message,
                "请由 Agent 根据当前 commit 确认或 GitHub PR Review 回读构造内部审计引用",
                result,
            )
        payload = {
            "schema_version": AUTHORIZATION_RECORD_SCHEMA_VERSION,
            **_impact_record_fields(impact),
            "review_report_digest": report_digest,
            "authorization_reference": reference,
            **authorization,
            "confirmation_items": report["confirmation_items"],
            "approved_by_role": "company_employee_instructor",
            "approved_at": _now(),
        }
        approval_path = self._approval_path(impact.impact_id)
        _ensure_record_path_safe(self.root, approval_path)
        with TaskLock(approval_path.parent / ".lock", timeout=5):
            atomic_write_json(approval_path, payload)
        return {
            **result,
            "approved": True,
            "confirmation_required": False,
            "authorization_reference": reference,
            **authorization,
            "approval_path": str(approval_path),
            "next_action": (
                "push_commit"
                if impact.review_channel == "commit_review"
                else "keep_pr_waiting_for_merge_gate"
            ),
        }

    def verify(self, source: str, *, base: str | None = None, head: str | None = None, event_sink: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
        registry, impact = self._calculate(source, base=base, head=head, read_pr_fact=False)
        report = _review_report(registry, impact)
        if not impact.has_impact or impact.unmapped_paths:
            raise self._input_error(
                "story_mapping_missing",
                "当前候选为空或仍有未映射路径，不能执行故事验收",
                "请先查阅审查报告并补齐故事映射",
                self._result(registry, impact, report, _digest(report)),
            )
        run_dir = self._run_dir(impact.impact_id)
        _ensure_run_path_safe(self.root, run_dir / "output.log")
        _ensure_run_path_safe(self.root, run_dir / "events.ndjson")
        _ensure_record_path_safe(self.root, self._evidence_path(impact.impact_id))
        run_dir.mkdir(parents=True, exist_ok=True)
        output_path = run_dir / "output.log"
        evidence_path = self._evidence_path(impact.impact_id)
        events_path = run_dir / "events.ndjson"
        with TaskLock(run_dir / ".lock", timeout=5):
            output_path.unlink(missing_ok=True)
            events_path.unlink(missing_ok=True)
            with events_path.open("a", encoding="utf-8") as events_file:
                def emit(event: dict[str, Any]) -> None:
                    payload = {"timestamp": _now(), "impact_id": impact.impact_id, **event}
                    events_file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                    events_file.flush()
                    if event_sink:
                        event_sink(payload)

                results = []
                for check_id in impact.acceptance_checks:
                    started = time.monotonic()
                    emit({"event": "check_started", "check_id": check_id})
                    with output_path.open("ab", buffering=0) as output_file:
                        start_offset = output_file.tell()
                        output_file.write(f"\n===== {check_id} started =====\n".encode())
                        process = subprocess.Popen(_check_command(self.root, check_id), cwd=self.root, stdout=output_file, stderr=subprocess.STDOUT, env=_check_environment(self.root), start_new_session=True)
                        last_progress = 0.0
                        while process.poll() is None:
                            elapsed = time.monotonic() - started
                            if elapsed > 300:
                                os.killpg(process.pid, signal.SIGKILL)
                                process.wait()
                                raise subprocess.TimeoutExpired(process.args, 300)
                            if elapsed - last_progress >= 10:
                                emit({"event": "check_progress", "check_id": check_id, "elapsed_seconds": round(elapsed, 3)})
                                last_progress = elapsed
                            time.sleep(0.1)
                        output_file.write(f"===== {check_id} finished exit={process.returncode} =====\n".encode())
                        end_offset = output_file.tell()
                    check = {"check_id": check_id, "passed": process.returncode == 0, "exit_code": process.returncode, "duration_seconds": round(time.monotonic() - started, 3), "log_path": str(output_path.relative_to(self.root)), "log_start": start_offset, "log_end": end_offset, "log_sha256": _file_segment_sha256(output_path, start_offset, end_offset)}
                    results.append(check)
                    emit({"event": "check_finished", "check_id": check_id, "passed": check["passed"], "duration_seconds": check["duration_seconds"]})
                    if not check["passed"]:
                        payload = self._write_evidence_summary(evidence_path, impact, "failed", results)
                        emit({"event": "verify_completed", "acceptance_status": "failed", "evidence_path": str(evidence_path)})
                        raise StoryGateError(code="story_acceptance_failed", message=f"项目故事验收失败：{check_id}", status="blocked", exit_code=EXIT_BLOCKED, retry_safe=True, required_human_action="请修复失败后重新生成报告并运行固定验收", details={**self._result(registry, impact, report, _digest(report), evidence=payload), "acceptance_status": "failed", "checks": results, "evidence_path": str(evidence_path)})
                payload = self._write_evidence_summary(evidence_path, impact, "passed", results)
                emit({"event": "verify_completed", "acceptance_status": "passed", "evidence_path": str(evidence_path)})
        result = {
            **self._result(registry, impact, report, _digest(report), evidence=payload),
            "checks": results,
            "evidence_path": str(evidence_path),
        }
        return result

    def _write_evidence_summary(
        self, evidence_path: Path, impact: StoryImpact, acceptance_status: str, checks: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload = {
            "schema_version": AUTHORIZATION_RECORD_SCHEMA_VERSION,
            **_impact_record_fields(impact),
            "acceptance_status": acceptance_status,
            "checks": checks,
            "verified_at": _now(),
        }
        atomic_write_json(evidence_path, payload)
        return payload

    def _calculate(
        self,
        source: str,
        *,
        base: str | None,
        head: str | None,
        read_pr_fact: bool = True,
    ) -> tuple[StoryRegistry, StoryImpact]:
        try:
            registry = load_story_registry(self.root)
            review = resolve_branch_review(self.root, head=head)
            commit_sha = ""
            if source == "range":
                commit_sha = _git(self.root, "rev-parse", f"{head or 'HEAD'}^{{commit}}")
                if base is None:
                    base = _git(
                        self.root,
                        "merge-base",
                        commit_sha,
                        f"refs/remotes/origin/{review.target_branch}",
                    )
            changes = collect_changes(self.root, source, base=base, head=head)
            gate_stage = os.environ.get("AGENTIC_OPS_STORY_GATE_STAGE", "").strip()
            pr = PullRequestFact()
            if (
                read_pr_fact
                and review.channel == "pr_review"
                and source == "range"
                and gate_stage not in {"pre_commit", "pre_push", "release"}
            ):
                pr = read_pull_request_fact(
                    self.root, review, commit_sha, require_current_head_approval=True
                )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise StoryGateError(
                code="story_review_policy_unavailable",
                message=f"项目故事质量配置或审查事实无法使用：{error}",
                status="capability_gap",
                exit_code=EXIT_CAPABILITY_GAP,
                retry_safe=True,
                required_human_action="请修复故事注册表、分支策略、Git 工作区或 GitHub 回读能力",
            ) from error

        impacted: set[str] = set()
        revisions: set[str] = set()
        categories: set[str] = set()
        checks: set[str] = set()
        registry_changed = registry.path in changes.paths
        for story in registry.stories:
            direct_revision = registry_changed or story.document in changes.paths
            path_impact = any(
                path_matches(pattern, changed_path)
                for pattern in story.protected_paths
                for changed_path in changes.paths
            )
            if direct_revision:
                revisions.add(story.story_id)
            if direct_revision or path_impact:
                impacted.add(story.story_id)
                categories.add(story.category)
                checks.update(story.acceptance_checks)

        mapped_paths = {
            changed_path
            for changed_path in changes.paths
            if changed_path == registry.path
            or any(changed_path == story.document for story in registry.stories)
            or any(
                path_matches(pattern, changed_path)
                for story in registry.stories
                for pattern in story.protected_paths
            )
        }
        governed = {
            path for path in changes.paths if any(path_matches(pattern, path) for pattern in GOVERNED_PATHS)
        }
        unmapped = tuple(sorted(governed - mapped_paths))
        material = json.dumps(
            {
                "change_fingerprint": changes.fingerprint,
                "registry_digest": registry.digest,
                "paths": changes.paths,
                "stories": sorted(impacted),
                "revisions": sorted(revisions),
                "unmapped": unmapped,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        confirmation_stage = {
            "pr_review": "pull_request_review",
            "commit_review": "pre_push_commit_review",
            "protected": "protected_workflow",
            "special": "special_workflow",
        }[review.channel]
        return registry, StoryImpact(
            impact_id=hashlib.sha256(material.encode("utf-8")).hexdigest(),
            change_source=source,
            changed_paths=changes.paths,
            impacted_story_ids=tuple(sorted(impacted)),
            impacted_categories=tuple(sorted(categories)),
            revision_story_ids=tuple(sorted(revisions)),
            unmapped_paths=unmapped,
            acceptance_checks=tuple(sorted(checks)),
            current_branch=review.branch,
            review_channel=review.channel,
            confirmation_stage=confirmation_stage,
            target_branch=review.target_branch,
            commit_sha=commit_sha,
            pr_number=pr.number,
            pr_url=pr.url,
            pr_head_sha=pr.head_sha,
            pr_review_approved=pr.approved_for_head,
        )

    def _result(
        self,
        registry: StoryRegistry,
        impact: StoryImpact,
        report: dict[str, Any],
        report_digest: str,
        *,
        approval: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        review_fact_ready = bool(
            impact.commit_sha
            and (
                impact.review_channel == "commit_review"
                or (impact.review_channel == "pr_review" and impact.pr_url and impact.pr_head_sha == impact.commit_sha)
            )
        )
        approval_ready = bool(
            impact.has_impact
            and not impact.unmapped_paths
            and evidence is not None
            and review_fact_ready
        )
        approved = approval is not None
        return {
            **impact.as_dict(),
            "approved": approved,
            "acceptance_status": (
                "passed"
                if evidence is not None
                else ("not_required" if not impact.has_impact else "not_run")
            ),
            "approval_ready": approval_ready,
            "confirmation_required": approval_ready and not approved,
            "review_report": report,
            "review_report_digest": report_digest,
            "registry_digest": registry.digest,
            "required_human_action": _required_action(impact, evidence is not None, approved),
            **({"authorization_reference": approval["authorization_reference"]} if approval else {}),
        }

    def _read_matching_approval(self, impact: StoryImpact, report_digest: str) -> dict[str, Any] | None:
        payload = _read_record(self._approval_path(impact.impact_id), self.root)
        if payload is None or payload.get("schema_version") != AUTHORIZATION_RECORD_SCHEMA_VERSION:
            return None
        if not _record_matches_impact(payload, impact) or payload.get("review_report_digest") != report_digest:
            return None
        authorization, _ = _authorization_metadata(payload.get("authorization_reference"), impact)
        if authorization is None or any(payload.get(key) != value for key, value in authorization.items()):
            return None
        return payload

    def _read_matching_evidence(self, impact: StoryImpact) -> dict[str, Any] | None:
        payload = _read_record(self._evidence_path(impact.impact_id), self.root)
        if payload is None or payload.get("schema_version") not in {3, 4}:
            return None
        if not _record_matches_impact(payload, impact):
            return None
        return payload if payload.get("acceptance_status") == "passed" else None

    def _approval_path(self, impact_id: str) -> Path:
        return self.root / ".local" / "story-gate" / "approvals" / f"{impact_id}.json"

    def _evidence_path(self, impact_id: str) -> Path:
        return self.root / ".local" / "story-gate" / "evidence" / f"{impact_id}.json"

    def _run_dir(self, impact_id: str) -> Path:
        return self.root / ".local" / "story-gate" / "runs" / impact_id

    def _blocked(
        self,
        code: str,
        message: str,
        action: str,
        details: dict[str, Any],
        exit_code: int = EXIT_BLOCKED,
    ) -> StoryGateError:
        return StoryGateError(
            code=code,
            message=message,
            status="capability_gap" if exit_code == EXIT_CAPABILITY_GAP else "blocked",
            exit_code=exit_code,
            retry_safe=True,
            required_human_action=action,
            details=details,
        )

    def _input_error(self, code: str, message: str, action: str, details: dict[str, Any]) -> StoryGateError:
        return self._blocked(code, message, action, details)


def _review_report(registry: StoryRegistry, impact: StoryImpact) -> dict[str, Any]:
    by_id = {story.story_id: story for story in registry.stories}
    stories = [
        {
            "story_id": story_id,
            "title": by_id[story_id].title,
            "document": by_id[story_id].document,
            "revised": story_id in impact.revision_story_ids,
        }
        for story_id in impact.impacted_story_ids
    ]
    if impact.pr_url:
        review_object: dict[str, Any] = {
            "type": "pull_request", "url": impact.pr_url, "number": impact.pr_number, "head_sha": impact.pr_head_sha
        }
    elif impact.commit_sha:
        review_object = {"type": "commit", "commit_sha": impact.commit_sha}
    else:
        review_object = {"type": "candidate", "change_source": impact.change_source}
    risks = [
        {
            "risk_id": "local-hook-bypass",
            "level": "residual",
            "description": "本地 Hook 是防误操作层，最终保护仍依赖受保护分支与独立人工审查。",
        }
    ]
    if impact.revision_story_ids:
        risks.append(
            {
                "risk_id": "story-contract-revision",
                "level": "high",
                "description": "本次直接修订项目质量故事，需重点审查保护行为和验收条件。",
            }
        )
    if impact.review_channel == "pr_review":
        risks.append(
            {
                "risk_id": "stale-pr-review",
                "level": "medium",
                "description": "PR Head 变化后旧 Review 失效，必须重新审查最新 Head。",
            }
        )
    return {
        "changed_paths": list(impact.changed_paths),
        "impacted_stories": stories,
        "story_revisions": list(impact.revision_story_ids),
        "unmapped_paths": list(impact.unmapped_paths),
        "acceptance_checks": list(impact.acceptance_checks),
        "branch": {"current": impact.current_branch, "type": impact.review_channel, "target": impact.target_branch},
        "review_object": review_object,
        "confirmation_items": [
            {"item_id": "scope", "description": "确认审查对象只包含报告列出的变更路径。"},
            {"item_id": "story", "description": "确认受影响故事及故事修订符合预期。"},
            {"item_id": "acceptance", "description": "确认固定验收项与当前代码事实绑定且全部通过。"},
            {"item_id": "risk", "description": "确认已逐项审阅风险及残留风险。"},
        ],
        "change_points": [
            {"path": path, "description": _change_description(path)}
            for path in impact.changed_paths
        ],
        "risks": risks,
        "allowed_next_action_after_confirmation": (
            "push_reviewed_commit" if impact.review_channel == "commit_review" else "wait_for_protected_merge_gate"
        ),
    }


def _change_description(path: str) -> str:
    if path == ".githooks/pre-commit":
        return "调整提交门禁：候选固定验收通过后允许先形成 commit。"
    if path == ".githooks/pre-push":
        return "调整推送门禁：按版本化分支通道校验 commit 批准或允许形成 PR。"
    if path.endswith("review-policy.yaml"):
        return "登记 protected、special、commit_review 和 pr_review 的唯一分支分类。"
    if "/story_gate/" in path:
        return "实现结构化审查报告、后置批准、代码事实绑定和旧批准失效。"
    if path.endswith("int-001-release-governance.md"):
        return "修订 INT-001 内部发布治理合同。"
    if path.endswith("AGENTS.md") or "/rules/" in path or "/skills/" in path:
        return "固化新会话自动继承的内部代码审查交互。"
    if "/tests/" in path or path.endswith("test-resources.sh") or path.endswith("test-release-workflow.sh"):
        return "补充候选、commit、PR、Head 失效、资源措辞和发布回归测试。"
    if path.startswith("docs/"):
        return "同步人读架构和运行文档中的故事审查顺序与事实源边界。"
    return "更新当前审查对象中的配套实现或版本化规范。"


def _required_action(impact: StoryImpact, verified: bool, approved: bool) -> str:
    if not impact.has_impact:
        return "无需故事人工确认"
    if impact.unmapped_paths:
        return "请查阅报告中的未映射路径并补齐故事合同"
    if not verified:
        return "继续整理精确候选并运行报告列出的固定验收；当前不得请求人工确认"
    if not impact.commit_sha:
        return "固定验收已通过；请继续形成所属分支通道的 commit 或 PR，不得确认裸 impact_id"
    if impact.review_channel == "pr_review" and not impact.pr_url:
        return "请在连续授权范围内推送任务分支并创建或更新 PR"
    if approved:
        return "当前审查对象已确认，继续执行报告允许的下一动作"
    if impact.review_channel == "pr_review":
        return f"请访问 {impact.pr_url}，针对当前 Head {impact.pr_head_sha} 逐项审查确认事项、变更点和风险"
    if impact.review_channel == "commit_review":
        return f"请审阅本地提交 {impact.commit_sha} 的确认事项、变更点和风险；确认前保持未推送"
    return "请使用受保护或专用工作流"


def _authorization_metadata(reference: object, impact: StoryImpact) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(reference, str) or not reference:
        return None, "missing"
    commit = _COMMIT_CONFIRMATION_REFERENCE.fullmatch(reference)
    if commit is not None and impact.review_channel == "commit_review":
        if commit.group("commit_sha") != impact.commit_sha:
            return None, "review_object_mismatch"
        return {
            "authorization_kind": "commit_confirmation",
            "authorization_issue_key": commit.group("issue_key"),
            "authorization_record_id": impact.commit_sha,
        }, None
    pr = _PR_REVIEW_REFERENCE.fullmatch(reference)
    if pr is not None and impact.review_channel == "pr_review":
        if int(pr.group("pr_number")) != impact.pr_number or pr.group("head_sha") != impact.pr_head_sha:
            return None, "review_object_mismatch"
        if not impact.pr_review_approved:
            return None, "pr_review_missing"
        return {
            "authorization_kind": "github_pr_review",
            "authorization_issue_key": pr.group("issue_key"),
            "authorization_record_id": f"{impact.pr_number}:{impact.pr_head_sha}",
        }, None
    return None, "invalid"


def _impact_record_fields(impact: StoryImpact) -> dict[str, Any]:
    return {
        "impact_id": impact.impact_id,
        "changed_paths": list(impact.changed_paths),
        "impacted_story_ids": list(impact.impacted_story_ids),
        "commit_sha": impact.commit_sha,
        "pr_number": impact.pr_number,
        "pr_head_sha": impact.pr_head_sha,
        "review_channel": impact.review_channel,
    }


def _record_matches_impact(payload: dict[str, Any], impact: StoryImpact) -> bool:
    return (
        payload.get("impact_id") == impact.impact_id
        and payload.get("changed_paths") == list(impact.changed_paths)
        and payload.get("impacted_story_ids") == list(impact.impacted_story_ids)
    )


def _read_record(path: Path, root: Path) -> dict[str, Any] | None:
    _ensure_record_path_safe(root, path)
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _ensure_record_path_safe(root: Path, path: Path) -> None:
    expected_parent = root / ".local" / "story-gate"
    try:
        path.relative_to(expected_parent)
    except ValueError as error:
        raise _unsafe_local_state("故事状态路径逃出 .local/story-gate") from error
    current = root
    for component in path.relative_to(root).parts:
        current = current / component
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink():
            raise _unsafe_local_state(f"故事状态路径包含符号链接：{current.relative_to(root)}")
        if current == path:
            if not current.is_file():
                raise _unsafe_local_state(f"故事状态叶子不是普通文件：{current.relative_to(root)}")
            if current.stat().st_nlink != 1:
                raise _unsafe_local_state(f"故事状态叶子不能是硬链接：{current.relative_to(root)}")
        elif not current.is_dir():
            raise _unsafe_local_state(f"故事状态祖先不是目录：{current.relative_to(root)}")


def _ensure_run_path_safe(root: Path, path: Path) -> None:
    expected_parent = root / ".local" / "story-gate" / "runs"
    try:
        path.relative_to(expected_parent)
    except ValueError as error:
        raise _unsafe_local_state("故事运行日志路径逃出 .local/story-gate") from error
    current = root
    for component in path.relative_to(root).parts:
        current = current / component
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink() or (current == path and not current.is_file()) or (current != path and not current.is_dir()):
            raise _unsafe_local_state(f"故事运行日志路径不安全：{current.relative_to(root)}")


def _unsafe_local_state(message: str) -> StoryGateError:
    return StoryGateError(
        code="story_gate_local_state_unsafe",
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action="请移除故事状态路径中的符号链接或特殊文件后重试",
    )


def _digest(payload: dict[str, Any]) -> str:
    material = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _file_segment_sha256(path: Path, start: int, end: int) -> str:
    digest = hashlib.sha256()
    remaining = end - start
    with path.open("rb") as source:
        source.seek(start)
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("验收日志区间超出文件范围")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "Git 命令失败")
    return completed.stdout.strip()


def _check_command(root: Path, check_id: str) -> list[str]:
    return {
        "python_runtime": [str(root / "internal" / "tests" / "test_runtime.sh")],
        "resource_contracts": [str(root / "internal" / "tests" / "test_resources.sh")],
        "release_workflow": [str(root / "internal" / "tests" / "test_release.sh")],
        "story_registry": [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "internal/tests",
            "-p",
            "test_story_gate.py",
        ],
    }[check_id]


def _check_environment(root: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONPYCACHEPREFIX"] = environment.get("PYTHONPYCACHEPREFIX", ".local/cache/pycache")
    internal_path = str(root)
    existing_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = f"{internal_path}{os.pathsep}{existing_path}" if existing_path else internal_path
    return environment


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
