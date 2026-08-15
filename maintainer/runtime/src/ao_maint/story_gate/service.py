from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ao_maint.io import atomic_write_json, read_json
from ao_maint.locking import TaskLock
from ao_maint.output import EXIT_BLOCKED, EXIT_CAPABILITY_GAP, RuntimeErrorResult
from ao_maint.story_gate.git_changes import collect_changes
from ao_maint.story_gate.model import StoryImpact, StoryRegistry
from ao_maint.story_gate.registry import load_story_registry, path_matches

GOVERNED_PATHS = (
    "**",
)

AUTHORIZATION_RECORD_SCHEMA_VERSION = 3
_ISSUE_KEY = r"[A-Z][A-Z0-9_]*-[1-9][0-9]*"
_USER_CONFIRMATION_REFERENCE = re.compile(
    rf"user-confirmation:(?P<issue_key>{_ISSUE_KEY}):"
    r"(?P<record_id>[0-9a-f]{64})"
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
        result = impact.as_dict()
        if not impact.has_impact:
            return {**result, "approved": False, "acceptance_status": "not_required"}
        if impact.unmapped_paths:
            if enforce:
                raise self._blocked(
                    "maintenance_story_mapping_missing",
                    "代码变更命中项目治理范围，但没有对应故事映射",
                    "请由公司员工指导员补充故事影响映射后重新检查",
                    impact,
                    EXIT_CAPABILITY_GAP,
                )
            return {**result, "approved": False, "acceptance_status": "mapping_missing"}

        approval = self._read_matching_approval(impact)
        evidence = self._read_matching_evidence(impact)
        if approval is not None and evidence is not None:
            if evidence["authorization_reference"] != approval["authorization_reference"]:
                evidence = None
        if approval is None:
            code = (
                "maintenance_story_revision_required"
                if impact.requires_revision_confirmation
                else "maintenance_story_impacted"
            )
            if enforce:
                raise self._blocked(
                    code,
                    "代码变更影响项目质量故事，连续自动化已停止",
                    "请公司员工指导员确认影响报告，再执行 story approve",
                    impact,
                )
            return {**result, "approved": False, "acceptance_status": "not_run"}
        if evidence is None:
            if enforce:
                raise self._blocked(
                    "maintenance_story_acceptance_failed",
                    "受影响故事尚未完成当前变更的固定验收",
                    "请执行 ao-maint story verify；验收通过后才能继续提交",
                    impact,
                    acceptance_status="not_run",
                )
            return {
                **result,
                "approved": True,
                "authorization_reference": approval["authorization_reference"],
                "authorization_kind": approval["authorization_kind"],
                "authorization_issue_key": approval["authorization_issue_key"],
                "authorization_record_id": approval["authorization_record_id"],
                "acceptance_status": "not_run",
            }
        return {
            **result,
            "approved": True,
            "authorization_reference": approval["authorization_reference"],
            "authorization_kind": approval["authorization_kind"],
            "authorization_issue_key": approval["authorization_issue_key"],
            "authorization_record_id": approval["authorization_record_id"],
            "acceptance_status": "passed",
            "evidence_path": str(self._evidence_path(impact.impact_id)),
            "registry_digest": registry.digest,
        }

    def approve(
        self,
        source: str,
        impact_id: str,
        authorization_reference: str,
        *,
        base: str | None = None,
        head: str | None = None,
    ) -> dict[str, Any]:
        _, impact = self._calculate(source, base=base, head=head)
        if impact.impact_id != impact_id:
            raise self._input_error(
                "story_impact_changed",
                "当前 Git 变更与待确认 impact_id 不一致",
                "请重新执行 story impact，并让公司员工指导员确认新的影响报告",
                impact,
            )
        if not impact.has_impact or impact.unmapped_paths:
            raise self._input_error(
                "maintenance_story_mapping_missing",
                "当前影响报告为空或仍有未映射路径，不能确认",
                "请先补齐故事映射",
                impact,
            )
        reference = authorization_reference.strip()
        authorization, invalid_reason = _authorization_metadata(reference, impact)
        if authorization is None:
            if invalid_reason == "missing":
                code = "story_authorization_reference_missing"
                message = "缺少公司员工指导员确认引用"
            elif invalid_reason == "impact_mismatch":
                code = "story_authorization_impact_mismatch"
                message = "人工确认引用没有绑定当前 impact_id"
            else:
                code = "story_authorization_reference_invalid"
                message = "公司员工指导员确认引用格式无效"
            raise self._input_error(
                code,
                message,
                "请提供与当前影响一致的 user-confirmation:<KEY>:<impact-id>；"
                "maintainer 没有 Jira 评论回读能力，不接受 jira-comment 引用",
                impact,
            )
        payload = {
            "schema_version": AUTHORIZATION_RECORD_SCHEMA_VERSION,
            **impact.as_dict(),
            "authorization_reference": reference,
            **authorization,
            "approved_by_role": "company_employee_instructor",
            "approved_at": _now(),
        }
        approval_path = self._approval_path(impact.impact_id)
        with TaskLock(approval_path.parent / ".lock", timeout=5):
            atomic_write_json(approval_path, payload)
        return {
            **impact.as_dict(),
            "approved": True,
            "authorization_reference": reference,
            **authorization,
            "approval_path": str(approval_path),
            "next_action": "story_verify",
        }

    def verify(
        self,
        source: str,
        *,
        base: str | None = None,
        head: str | None = None,
    ) -> dict[str, Any]:
        _, impact = self._calculate(source, base=base, head=head)
        approval = self._read_matching_approval(impact)
        if not impact.has_impact or impact.unmapped_paths or approval is None:
            raise self._input_error(
                "maintenance_story_impacted",
                "受影响故事尚未获得公司员工指导员确认",
                "请先执行 story impact 和 story approve",
                impact,
            )
        results = []
        for check_id in impact.acceptance_checks:
            started = time.monotonic()
            completed = subprocess.run(
                _check_command(self.root, check_id),
                cwd=self.root,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=300,
                env=_check_environment(self.root),
            )
            output = completed.stdout.decode("utf-8", errors="replace")[-4000:]
            result = {
                "check_id": check_id,
                "passed": completed.returncode == 0,
                "exit_code": completed.returncode,
                "duration_seconds": round(time.monotonic() - started, 3),
                "output_tail": output,
            }
            results.append(result)
            if completed.returncode != 0:
                raise RuntimeErrorResult(
                    code="maintenance_story_acceptance_failed",
                    message=f"项目故事验收失败：{check_id}",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请修复失败后重新生成 impact、确认并验收",
                    details={**impact.as_dict(), "acceptance_status": "failed", "checks": results},
                )
        payload = {
            "schema_version": AUTHORIZATION_RECORD_SCHEMA_VERSION,
            **impact.as_dict(),
            "authorization_reference": approval["authorization_reference"],
            "authorization_kind": approval["authorization_kind"],
            "authorization_issue_key": approval["authorization_issue_key"],
            "authorization_record_id": approval["authorization_record_id"],
            "acceptance_status": "passed",
            "checks": results,
            "verified_at": _now(),
        }
        evidence_path = self._evidence_path(impact.impact_id)
        with TaskLock(evidence_path.parent / ".lock", timeout=5):
            atomic_write_json(evidence_path, payload)
        return {**payload, "evidence_path": str(evidence_path)}

    def _calculate(
        self,
        source: str,
        *,
        base: str | None,
        head: str | None,
    ) -> tuple[StoryRegistry, StoryImpact]:
        try:
            registry = load_story_registry(self.root)
            changes = collect_changes(self.root, source, base=base, head=head)
        except (OSError, ValueError) as error:
            raise RuntimeErrorResult(
                code="maintenance_story_mapping_missing",
                message=f"项目故事质量配置无法使用：{error}",
                status="capability_gap",
                exit_code=EXIT_CAPABILITY_GAP,
                retry_safe=True,
                required_human_action="请由公司员工指导员修复故事注册表或 Git 工作区",
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
            path
            for path in changes.paths
            if any(path_matches(pattern, path) for pattern in GOVERNED_PATHS)
        }
        unmapped = tuple(sorted(governed - mapped_paths))
        impact_material = json.dumps(
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
        impact = StoryImpact(
            impact_id=hashlib.sha256(impact_material.encode("utf-8")).hexdigest(),
            change_source=source,
            changed_paths=changes.paths,
            impacted_story_ids=tuple(sorted(impacted)),
            impacted_categories=tuple(sorted(categories)),
            revision_story_ids=tuple(sorted(revisions)),
            unmapped_paths=unmapped,
            acceptance_checks=tuple(sorted(checks)),
        )
        return registry, impact

    def _read_matching_approval(self, impact: StoryImpact) -> dict[str, Any] | None:
        return _matching_record(self._approval_path(impact.impact_id), impact, "approved")

    def _read_matching_evidence(self, impact: StoryImpact) -> dict[str, Any] | None:
        return _matching_record(self._evidence_path(impact.impact_id), impact, "acceptance")

    def _approval_path(self, impact_id: str) -> Path:
        return self.root / "maintainer" / ".local" / "story-approvals" / f"{impact_id}.json"

    def _evidence_path(self, impact_id: str) -> Path:
        return self.root / "maintainer" / ".local" / "story-evidence" / f"{impact_id}.json"

    def _blocked(
        self,
        code: str,
        message: str,
        action: str,
        impact: StoryImpact,
        exit_code: int = EXIT_BLOCKED,
        **extra: Any,
    ) -> RuntimeErrorResult:
        return RuntimeErrorResult(
            code=code,
            message=message,
            status="capability_gap" if exit_code == EXIT_CAPABILITY_GAP else "blocked",
            exit_code=exit_code,
            retry_safe=True,
            required_human_action=action,
            details={**impact.as_dict(), **extra},
        )

    def _input_error(
        self,
        code: str,
        message: str,
        action: str,
        impact: StoryImpact,
    ) -> RuntimeErrorResult:
        return self._blocked(code, message, action, impact)


def _matching_record(path: Path, impact: StoryImpact, kind: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if payload.get("impact_id") != impact.impact_id:
        return None
    if payload.get("changed_paths") != list(impact.changed_paths):
        return None
    if payload.get("impacted_story_ids") != list(impact.impacted_story_ids):
        return None
    if payload.get("schema_version") != AUTHORIZATION_RECORD_SCHEMA_VERSION:
        return None
    authorization, _ = _authorization_metadata(
        payload.get("authorization_reference"), impact
    )
    if authorization is None:
        return None
    if any(payload.get(key) != value for key, value in authorization.items()):
        return None
    if kind == "acceptance" and payload.get("acceptance_status") != "passed":
        return None
    return payload


def _authorization_metadata(
    reference: object,
    impact: StoryImpact,
) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(reference, str) or not reference:
        return None, "missing"

    user_confirmation = _USER_CONFIRMATION_REFERENCE.fullmatch(reference)
    if user_confirmation is not None:
        record_id = user_confirmation.group("record_id")
        if record_id != impact.impact_id:
            return None, "impact_mismatch"
        return {
            "authorization_kind": "user_confirmation",
            "authorization_issue_key": user_confirmation.group("issue_key"),
            "authorization_record_id": record_id,
        }, None

    return None, "invalid"


def _check_command(root: Path, check_id: str) -> list[str]:
    commands = {
        "python_runtime": [
            str(root / "maintainer" / "scripts" / "test-python-runtime.sh"),
        ],
        "resource_contracts": [str(root / "maintainer" / "scripts" / "test-resources.sh")],
        "release_workflow": [str(root / "maintainer" / "scripts" / "test-release-workflow.sh")],
        "story_registry": [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "maintainer/tests/runtime",
            "-p",
            "test_story_gate.py",
        ],
    }
    return commands[check_id]


def _check_environment(root: Path) -> dict[str, str]:
    import os

    environment = dict(os.environ)
    environment["PYTHONPYCACHEPREFIX"] = environment.get(
        "PYTHONPYCACHEPREFIX", ".local/pycache"
    )
    maintainer_path = str(root / "maintainer" / "runtime" / "src")
    existing_path = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        f"{maintainer_path}{os.pathsep}{existing_path}"
        if existing_path
        else maintainer_path
    )
    return environment


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
