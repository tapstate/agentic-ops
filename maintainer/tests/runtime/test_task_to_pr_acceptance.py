from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_maint.cli import build_parser
from ao_maint.cli_common import ArgumentParserError
from ao_maint.integration.service import IntegrationService
from ao_maint.integration.task_to_pr import (
    validate_event,
    validate_manifest,
    validate_result_package,
)
from ao_maint.output import RuntimeErrorResult


ISSUE_KEY = "TAP-12289"
RUN_ID = "run-12289"
AUTHORIZATION = "user-confirmation:TAP-12289:run-12289:" + "8" * 64
QUALITY_CATEGORIES = [
    "automation_gap",
    "manual_friction",
    "output_quality",
    "unreasonable_process",
]
PROHIBITED_ACTIONS = [
    "merge_pr",
    "jira_done",
    "release",
    "create_tag",
    "push_protected_branch",
]


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def manifest_sha256(value: dict[str, object]) -> str:
    candidate = copy.deepcopy(value)
    authorization = candidate["authorization"]
    assert isinstance(authorization, dict)
    authorization["confirmed_manifest_sha256"] = ""
    return canonical_sha256(candidate)


def result_sha256(value: dict[str, object]) -> str:
    candidate = copy.deepcopy(value)
    candidate["result_sha256"] = ""
    return canonical_sha256(candidate)


def verification_sha256(
    command: list[str], working_directory: str, timeout_seconds: int
) -> str:
    return canonical_sha256(
        {
            "command": command,
            "working_directory": working_directory,
            "timeout_seconds": timeout_seconds,
        }
    )


class ResultBuilder:
    def __init__(self, manifest: dict[str, object], *, run_id: str = RUN_ID) -> None:
        self.manifest = manifest
        self.run_id = run_id
        self.timeline: list[dict[str, object]] = []

    def record(
        self,
        step_id: str,
        action: str,
        action_data: dict[str, object],
        *,
        actor: str = "runtime",
        authorization_reference: str | None = AUTHORIZATION,
        terminal_status: str = "completed",
    ) -> str:
        self._append(
            self._event(
                f"event-{step_id}-start",
                step_id,
                "started",
                "step",
                {},
                actor=actor,
                authorization_reference=authorization_reference,
            )
        )
        event_id = f"event-{step_id}-done"
        self._append(
            self._event(
                event_id,
                step_id,
                terminal_status,
                action,
                action_data,
                actor=actor,
                authorization_reference=authorization_reference,
            )
        )
        return event_id

    def ready_result(
        self,
        *,
        jira_issue_key: str = ISSUE_KEY,
        branch: str = "codex/TAP-12289/task-to-pr",
        pr_head_sha: str = "a" * 40,
        command_sha256: str | None = None,
        observed_prohibition: str | None = None,
        external_authorization: str = AUTHORIZATION,
        retrospective_categories: list[str] | None = None,
        include_git_commit: bool = True,
        include_agentic_gap: bool = True,
        include_waiting: bool = False,
        include_jira_writes: bool = True,
    ) -> dict[str, object]:
        baseline_id = "event-prohibition-baseline-done"
        for step, action, target in (
            ("baseline-jira-read", "jira_read", f"jira:{ISSUE_KEY}:prohibition-baseline"),
            (
                "baseline-git-read",
                "git_remote_read",
                "git:tapdata/tapdata:prohibition-baseline",
            ),
            (
                "baseline-pr-read",
                "github_pr_read",
                "github:tapdata/tapdata:prohibition-baseline",
            ),
        ):
            self.record(
                step,
                "external_action",
                {
                    "action": action,
                    "target": target,
                    "status": "applied",
                    "readback_event_id": baseline_id,
                },
                actor="project_tool",
                authorization_reference=external_authorization,
            )
        self.record(
            "prohibition-baseline",
            "prohibition_baseline",
            {
                "issue_key": ISSUE_KEY,
                "repository_slug": "tapdata/tapdata",
                "remote_name": "origin",
                "jira_status": "进行中",
                "jira_status_category": "indeterminate",
                "tag_refs": [],
                "release_records": [],
                "protected_heads": [
                    {"branch": "develop", "sha": "b" * 40},
                    {"branch": "main", "sha": "c" * 40},
                ],
                "local_head_sha": "b" * 40,
                "task_branch_remote_sha": None,
                "task_open_pr": None,
                "observed_at": "2026-08-13T02:05:00+00:00",
                "reference": f"runtime-prohibition-baseline:{ISSUE_KEY}:{RUN_ID}",
            },
            actor="runtime",
        )
        jira_readback_id = "event-jira-readback-done"
        self.record(
            "jira-read",
            "external_action",
            {
                "action": "jira_read",
                "target": f"jira:{ISSUE_KEY}",
                "status": "applied",
                "readback_event_id": jira_readback_id,
            },
            actor="project_tool",
            authorization_reference=external_authorization,
        )
        configured_agentic_field = self.manifest["jira"]["agentic_id_field"]
        formal_takeover_verified = configured_agentic_field is not None
        self.record(
            "jira-readback",
            "jira_readback",
            {
                "provider": "jira",
                "issue_key": jira_issue_key,
                "issue_id": "12289",
                "project_key": "TAP",
                "url": f"https://example.atlassian.net/browse/{jira_issue_key}",
                "status": "进行中",
                "assignee": "account-123",
                "account_id": "account-123",
                "assignee_account_id": "account-123",
                "status_category": "indeterminate",
                "mapped_status": "in_progress",
                "agentic_id_field": configured_agentic_field,
                "agentic_id_value": (
                    "harsen-mini-test-bot" if formal_takeover_verified else None
                ),
                "agentic_id_mapping_status": (
                    "active" if formal_takeover_verified else "not_configured"
                ),
                "formal_takeover_verified": formal_takeover_verified,
                "issue_content_sha256": "9" * 64,
                "approved_plan_sha256": "8" * 64,
                "observed_at": "2026-08-13T02:10:00+00:00",
                "reference": f"jira:{jira_issue_key}:readback:1",
            },
            actor="runtime",
        )
        if include_jira_writes:
            jira_write_facts = (
                (
                    "jira-comment",
                    "jira_comment",
                    "9001",
                    {
                        "title": None,
                        "details_sha256": None,
                        "time_spent_seconds": None,
                        "started": None,
                        "excludes_waiting": None,
                        "included_work": None,
                        "excluded_waiting_categories": None,
                    },
                ),
                (
                    "jira-worklog",
                    "jira_worklog",
                    "9002",
                    {
                        "title": "实现与验证",
                        "details_sha256": "e" * 64,
                        "time_spent_seconds": 1800,
                        "started": "2026-08-13T04:00:00.000+00:00",
                        "excludes_waiting": True,
                        "included_work": [
                            {"description": "完成代码实现", "seconds": 1200},
                            {"description": "完成验证", "seconds": 600},
                        ],
                        "excluded_waiting_categories": ["等待人工确认", "等待 CI"],
                    },
                ),
            )
            for step, operation, external_id, extra in jira_write_facts:
                readback_id = f"event-{step}-readback-done"
                self.record(
                    step,
                    "external_action",
                    {
                        "action": operation,
                        "target": f"jira:{ISSUE_KEY}:{operation}:{external_id}",
                        "status": "applied",
                        "readback_event_id": readback_id,
                    },
                    actor="project_tool",
                    authorization_reference=external_authorization,
                )
                self.record(
                    f"{step}-readback",
                    "jira_write_readback",
                    {
                        "provider": "jira",
                        "issue_key": ISSUE_KEY,
                        "agentic_run_id": RUN_ID,
                        "operation": operation,
                        "plan_file": (
                            f".agentic-ops/tasks/{ISSUE_KEY}/runs/{RUN_ID}/"
                            f"jira-plans/{step}.json"
                        ),
                        "attempt_file": (
                            f".agentic-ops/tasks/{ISSUE_KEY}/runs/{RUN_ID}/"
                            f"jira-plans/{step}.json.attempt.json"
                        ),
                        "plan_id": f"plan-{step}",
                        "idempotency_key": f"task:{step}:1",
                        "external_id": external_id,
                        "created": True,
                        "write_precondition": "absent",
                        "write_attempt_id": f"attempt-{step}",
                        "write_attempt_started_at": "2026-08-13T02:14:00+00:00",
                        "content_sha256": "f" * 64,
                        "body_sha256": "a" * 64,
                        **extra,
                        "observed_at": "2026-08-13T02:15:00+00:00",
                        "reference": f"jira:{ISSUE_KEY}:{operation}:{external_id}:readback",
                    },
                    actor="runtime",
                )
        quality_finding_ids: list[str] = []
        if not formal_takeover_verified and include_agentic_gap:
            quality_finding_ids.append(
                self.record(
                    "agentic-id-gap",
                    "quality_finding",
                    {
                        "category": "automation_gap",
                        "detail": "Project Profile 尚未配置稳定 agentic_id 字段",
                        "evidence_reference": jira_readback_id,
                        "impact": "只证明当前账户等于经办人，不声称正式接管",
                        "root_cause_hypothesis": "Jira Custom Field 尚未完成专题适配",
                        "reproduction": "使用 agentic_id_field=null 的 manifest 执行 Jira probe",
                        "sanitized_example": "formal_takeover_verified=false",
                        "improvement_candidate": "专题适配并验证 agentic_id Custom Field",
                        "suggested_asset": "profile",
                        "benefit": "可确定性核对正式任务绑定",
                        "risk": "错误映射可能误认所有权",
                        "frequency": "每次未适配 Profile 的真实测试",
                    },
                    actor="ai",
                )
            )

        self.record(
            "verification-unit",
            "verification",
            {
                "id": "unit",
                "status": "passed",
                "command_sha256": command_sha256
                or verification_sha256(
                    ["python3", "-m", "unittest"], ".", 300
                ),
                "evidence_reference": "local-verification:unit:passed",
                "exit_code": 0,
                "duration_seconds": 1.5,
                "stdout_sha256": "c" * 64,
                "stderr_sha256": "d" * 64,
                "output_summary": "单元测试全部通过",
                "head_sha": "a" * 40,
            },
            actor="runtime",
        )

        branch_readback_id = "event-branch-readback-done"
        if include_git_commit:
            self.record(
                "git-commit",
                "external_action",
                {
                "action": "git_commit",
                    "target": f"git:tapdata/tapdata:{branch}@{'a' * 40}",
                    "status": "applied",
                    "readback_event_id": branch_readback_id,
                },
                actor="project_tool",
                authorization_reference=external_authorization,
            )
        self.record(
            "push-branch",
            "external_action",
            {
                "action": "git_push_task_branch",
                "target": f"git:tapdata/tapdata:{branch}@{'a' * 40}",
                "status": "applied",
                "readback_event_id": branch_readback_id,
            },
            actor="project_tool",
            authorization_reference=external_authorization,
        )
        self.record(
            "branch-readback",
            "remote_branch_readback",
            {
                "provider": "git",
                "url": f"https://github.com/tapdata/tapdata/tree/{branch}",
                "repository_slug": "tapdata/tapdata",
                "remote_name": "origin",
                "branch": branch,
                "sha": "a" * 40,
                "status": "exists",
                "protected": False,
                "observed_at": "2026-08-13T02:20:00+00:00",
                "reference": f"git:origin/{branch}@{'a' * 40}",
                "origin_url": "git@github.com:tapdata/tapdata.git",
                "base_sha": "b" * 40,
                "head_sha": "a" * 40,
                "baseline_event_id": baseline_id,
                "baseline_local_head_sha": "b" * 40,
                "baseline_remote_sha": None,
                "baseline_local_is_ancestor": True,
                "baseline_remote_is_ancestor": None,
                "attributed_actions": [
                    *(["git_commit"] if include_git_commit else []),
                    "git_push_task_branch",
                ],
                "verification_event_ids": ["event-verification-unit-done"],
                "changed_paths": ["src/example.py"],
                "worktree_clean": True,
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen-test-bot@example.com",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen-test-bot@example.com",
                "commit_count": 1,
                "commit_identity_sha256": "7" * 64,
                "approved_plan_sha256": "8" * 64,
            },
            actor="runtime",
        )

        pr_readback_id = "event-pr-readback-done"
        self.record(
            "create-pr",
            "external_action",
            {
                "action": "github_pr_create_or_update",
                "target": "https://github.com/tapdata/tapdata/pull/321",
                "status": "applied",
                "readback_event_id": pr_readback_id,
            },
            actor="project_tool",
            authorization_reference=external_authorization,
        )
        self.record(
            "pr-readback",
            "pr_readback",
            {
                "provider": "github",
                "repository_slug": "tapdata/tapdata",
                "number": 321,
                "url": "https://github.com/tapdata/tapdata/pull/321",
                "status": "open",
                "merged": False,
                "draft": False,
                "head_branch": "codex/TAP-12289/task-to-pr",
                "head_sha": pr_head_sha,
                "base_branch": "develop",
                "review_state": "awaiting_review",
                "ci_status": "passed",
                "github_actor_login": "harsen-mini-test-bot",
                "approved_plan_sha256": "8" * 64,
                "baseline_event_id": baseline_id,
                "git_readback_event_id": branch_readback_id,
                "attributed_actions": ["github_pr_create_or_update"],
                "creation_proof": True,
                "observed_at": "2026-08-13T02:30:00+00:00",
                "reference": "github:tapdata/tapdata:pull:321",
            },
            actor="runtime",
        )

        waiting_ids: list[str] = []
        if include_waiting:
            waiting_ids.append(
                self.record(
                    "ci-waiting",
                    "waiting",
                    {
                        "reason": "等待 CI 完成",
                        "started_at": "2026-08-13T02:25:00+00:00",
                        "ended_at": "2026-08-13T02:26:30+00:00",
                        "duration_seconds": 90.0,
                    },
                    actor="runtime",
                )
            )
        self.record(
            "retrospective",
            "retrospective",
            {
                "reviewed_categories": retrospective_categories
                if retrospective_categories is not None
                else QUALITY_CATEGORIES,
                "category_reviews": [
                    {
                        "category": category,
                        "outcome": (
                            "finding"
                            if (
                                any(
                                    self._event_by_id(event_id)["action_data"]["category"]
                                    == category
                                    for event_id in quality_finding_ids
                                )
                                or (category == "manual_friction" and waiting_ids)
                            )
                            else "no_finding"
                        ),
                        "rationale": (
                            "已记录该分类的具体问题"
                            if (
                                any(
                                    self._event_by_id(event_id)["action_data"]["category"]
                                    == category
                                    for event_id in quality_finding_ids
                                )
                                or (category == "manual_friction" and waiting_ids)
                            )
                            else "已检查完整时间线，未发现该分类问题"
                        ),
                        "evidence_references": (
                            [
                                event_id
                                for event_id in quality_finding_ids
                                if self._event_by_id(event_id)["action_data"]["category"]
                                == category
                            ]
                            + (waiting_ids if category == "manual_friction" else [])
                            or ["event-verification-unit-done"]
                        ),
                        "source_event_ids": (
                            [
                                event_id
                                for event_id in quality_finding_ids
                                if self._event_by_id(event_id)["action_data"]["category"]
                                == category
                            ]
                            + (waiting_ids if category == "manual_friction" else [])
                        ),
                    }
                    for category in QUALITY_CATEGORIES
                ],
                "quality_finding_event_ids": quality_finding_ids,
                "human_intervention_event_ids": [],
                "failure_event_ids": [],
                "retry_event_ids": [],
                "waiting_event_ids": waiting_ids,
                "ordered_improvement_event_ids": quality_finding_ids,
                "residual_risks": (
                    ["agentic_id 未适配，不能声称正式 takeover"]
                    if quality_finding_ids
                    else []
                ),
                "summary": "已逐项检查四类流程质量，本结果包未记录额外问题",
            },
            actor="ai",
        )
        self._record_prohibitions(observed_prohibition)
        return self._result("ready_for_pr_review", "请研发工程师审查 PR")

    def non_delivery_result(
        self, status: str, *, observed_prohibition: str | None = None
    ) -> dict[str, object]:
        failure_id = self.record(
            "environment-failure",
            "failure",
            {
                "code": "required_project_tool_missing",
                "detail": "缺少项目指定工具，未执行外部写入",
                "retry_safe": False,
            },
            actor="project_tool",
        )
        if status == "blocked":
            self.record(
                "task-execution",
                "step",
                {},
                terminal_status="blocked",
            )
        self.record(
            "retrospective",
            "retrospective",
            {
                "reviewed_categories": QUALITY_CATEGORIES,
                "category_reviews": [
                    {
                        "category": category,
                        "outcome": "finding" if category == "automation_gap" else "no_finding",
                        "rationale": (
                            "项目工具缺失导致任务阻塞，已记录明确能力缺口"
                            if category == "automation_gap"
                            else "已检查阻塞前时间线，未发现该分类问题"
                        ),
                        "evidence_references": [failure_id],
                        "source_event_ids": (
                            [failure_id] if category == "automation_gap" else []
                        ),
                    }
                    for category in QUALITY_CATEGORIES
                ],
                "quality_finding_event_ids": [],
                "human_intervention_event_ids": [],
                "failure_event_ids": [failure_id],
                "retry_event_ids": [],
                "waiting_event_ids": [],
                "ordered_improvement_event_ids": [],
                "residual_risks": ["项目指定工具仍需人工安装"],
                "summary": "已完成阻塞结果的四类质量复盘",
            },
            actor="ai",
        )
        self._record_prohibitions(observed_prohibition)
        return self._result(status, "请补齐项目工具后使用新的运行重试")

    def _record_prohibitions(self, observed: str | None) -> None:
        for action in PROHIBITED_ACTIONS:
            self.record(
                f"prohibition-{action}",
                "prohibition_check",
                {
                    "action": action,
                    "observed": action == observed,
                    "evidence_reference": (
                        f"runtime-prohibition:{action}:"
                        "baseline=event-prohibition-baseline-done:head="
                        f"{'a' * 40}"
                    ),
                },
                actor="runtime",
            )

    def _event_by_id(self, event_id: str) -> dict[str, object]:
        for envelope in self.timeline:
            event = envelope["event"]
            if isinstance(event, dict) and event.get("event_id") == event_id:
                return event
        raise AssertionError(f"event not found: {event_id}")

    def _result(self, status: str, next_action: str) -> dict[str, object]:
        completed = [
            envelope["event"]
            for envelope in self.timeline
            if envelope["event"]["status"] == "completed"
        ]
        by_action: dict[str, list[dict[str, object]]] = {}
        for event in completed:
            by_action.setdefault(str(event["action"]), []).append(event)

        def envelopes(action: str) -> list[dict[str, object]]:
            ids = {event["event_id"] for event in by_action.get(action, [])}
            return [
                envelope
                for envelope in self.timeline
                if envelope["event"]["event_id"] in ids
            ]

        def latest_data(action: str) -> dict[str, object] | None:
            values = by_action.get(action, [])
            return values[-1]["action_data"] if values else None

        retrospective = envelopes("retrospective")
        self.assert_one(retrospective, "retrospective")
        result: dict[str, object] = {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "status": status,
            "delivery_passed": status == "ready_for_pr_review",
            "manifest_sha256": manifest_sha256(self.manifest),
            "generated_at": "2026-08-13T03:00:00+00:00",
            "facts": {
                "jira_readback": latest_data("jira_readback"),
                "remote_branch_readback": latest_data("remote_branch_readback"),
                "pr_readback": latest_data("pr_readback"),
                "verifications": [
                    event["action_data"]
                    for event in by_action.get("verification", [])
                ],
                "external_actions": [
                    event["action_data"]
                    for event in by_action.get("external_action", [])
                ],
            },
            "timeline": self.timeline,
            "human_interventions": envelopes("human_intervention"),
            "waitings": envelopes("waiting"),
            "failures": envelopes("failure"),
            "quality_findings": envelopes("quality_finding"),
            "retrospective": retrospective[0],
            "prohibitions": envelopes("prohibition_check"),
            "next_action": next_action,
            "result_sha256": "",
        }
        result["result_sha256"] = result_sha256(result)
        return result

    def _event(
        self,
        event_id: str,
        step_id: str,
        status: str,
        action: str,
        action_data: dict[str, object],
        *,
        actor: str,
        authorization_reference: str | None,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "event_id": event_id,
            "agentic_run_id": self.run_id,
            "step_id": step_id,
            "recorded_at": "2026-08-13T02:00:00+00:00",
            "status": status,
            "actor": actor,
            "action": action,
            "duration_seconds": 0.1,
            "summary": f"记录步骤 {step_id}",
            "authorization_reference": authorization_reference,
            "action_data": action_data,
            "evidence_origin": "runtime_probe" if actor == "runtime" else "imported",
        }

    def _append(self, event: dict[str, object]) -> None:
        previous = self.timeline[-1]["event_sha256"] if self.timeline else None
        base: dict[str, object] = {
            "sequence": len(self.timeline) + 1,
            "previous_event_sha256": previous,
            "event": event,
        }
        self.timeline.append({**base, "event_sha256": canonical_sha256(base)})

    @staticmethod
    def assert_one(values: list[object], label: str) -> None:
        if len(values) != 1:
            raise AssertionError(f"fixture {label} count={len(values)}")


class TaskToPRAcceptanceTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]

    def test_prepare_task_to_pr_is_required_template_without_host_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "task-to-pr.json"
            fake_home = root / "home"
            fake_home.mkdir()
            (fake_home / "business-secret.txt").write_text(
                "must-not-leak", encoding="utf-8"
            )
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(fake_home),
                        "TAPDATA_JIRA_API_TOKEN": "must-not-leak-token",
                    },
                    clear=False,
                ),
                mock.patch(
                    "subprocess.run",
                    side_effect=AssertionError("prepare-task-to-pr 不得运行子进程"),
                ),
            ):
                result = IntegrationService(root).prepare_task_to_pr(
                    ISSUE_KEY, output=str(output)
                )
            payload = json.loads(output.read_text(encoding="utf-8"))
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual("task_to_pr_review", payload["protocol"])
            self.assertEqual(ISSUE_KEY, payload["issue"]["key"])
            self.assertEqual("TAP", payload["issue"]["project_key"])
            self.assertEqual("REQUIRED", payload["workspace"]["root"])
            self.assertEqual("REQUIRED", payload["authorization"]["reference"])
            self.assertFalse(result["host_state_read"])
            self.assertFalse(result["business_workspace_read"])
            self.assertFalse(result["credentials_read"])
            self.assertNotIn("must-not-leak", serialized)
            self.assertNotIn(str(fake_home), serialized)

    def test_accept_ready_runtime_probe_package_is_read_only_and_passes_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result(include_waiting=True)
            manifest_path = self._write(root / "manifest.json", manifest)
            result_path = self._write(root / "result.json", result)
            with (
                mock.patch(
                    "subprocess.run",
                    side_effect=AssertionError("accept-task-to-pr 不得运行子进程"),
                ),
                mock.patch(
                    "ao_maint.integration.service.atomic_write_json",
                    side_effect=AssertionError("accept-task-to-pr 不得写文件"),
                ),
                mock.patch("os.replace", side_effect=AssertionError("不得替换文件")),
            ):
                accepted = IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY, str(manifest_path), str(result_path)
                )
            self.assertEqual("accepted", accepted["package_status"])
            self.assertEqual(
                "developer_runtime_probe_result_package", accepted["evidence_basis"]
            )
            self.assertFalse(accepted["independent_external_readback"])
            self.assertFalse(accepted["independent_human_approval_verified"])
            self.assertEqual(
                "conversation_user_confirmation_manifest_attestation",
                accepted["authorization_basis"],
            )
            self.assertFalse(accepted["cryptographic_remote_attestation"])
            self.assertEqual(
                "ready_for_pr_review", accepted["reported_result_status"]
            )
            self.assertEqual("ready_for_pr_review", accepted["delivery_status"])
            self.assertTrue(accepted["delivery_passed"])
            self.assertTrue(accepted["formal_takeover_verified"])
            self.assertIn("未独立访问", accepted["acceptance_next_action"])
            self.assertEqual(5, accepted["evidence_counts"]["prohibitions"])
            self.assertEqual(1, accepted["evidence_counts"]["waitings"])
            self.assertEqual([], accepted["observed_prohibitions"])

    def test_blocked_and_failed_packages_are_accepted_without_success_claim(self) -> None:
        for status in ("blocked", "failed"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = self._manifest(root)
                result = ResultBuilder(manifest).non_delivery_result(status)
                manifest_path = self._write(root / "manifest.json", manifest)
                result_path = self._write(root / "result.json", result)
                accepted = IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY, str(manifest_path), str(result_path)
                )
                self.assertEqual("accepted", accepted["package_status"])
                self.assertEqual(status, accepted["reported_result_status"])
                self.assertEqual(status, accepted["delivery_status"])
                self.assertFalse(accepted["delivery_passed"])
                self.assertGreaterEqual(accepted["evidence_counts"]["failures"], 1)

    def test_accept_binds_specialized_jira_comment_and_worklog_runtime_readbacks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result(include_jira_writes=True)
            accepted = IntegrationService(root).accept_task_to_pr(
                ISSUE_KEY,
                str(self._write(root / "manifest.json", manifest)),
                str(self._write(root / "result.json", result)),
            )
            self.assertTrue(accepted["delivery_passed"])

            tampered = copy.deepcopy(result)
            for envelope in tampered["timeline"]:
                event = envelope["event"]
                if event["action"] == "jira_write_readback":
                    event["action_data"]["plan_file"] = (
                        ".agentic-ops/tasks/TAP-999/runs/run-other/jira-plans/comment.json"
                    )
                    break
            self._rehash_result(tampered)
            with self.assertRaises(RuntimeErrorResult):
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(root / "manifest.json"),
                    str(self._write(root / "tampered-result.json", tampered)),
                )

            not_created = copy.deepcopy(result)
            for envelope in not_created["timeline"]:
                event = envelope["event"]
                if event["action"] == "jira_write_readback":
                    event["action_data"]["created"] = False
                    event["action_data"]["write_precondition"] = "preexisting"
                    event["action_data"]["attempt_file"] = None
                    event["action_data"]["write_attempt_id"] = None
                    event["action_data"]["write_attempt_started_at"] = None
                    break
            self._rehash_result(not_created)
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(root / "manifest.json"),
                    str(self._write(root / "not-created-result.json", not_created)),
                )
            self.assertEqual("integration_result_evidence_invalid", captured.exception.code)

    def test_retrospective_must_classify_each_waiting_event_as_a_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result(include_waiting=True)
            retrospective = result["retrospective"]["event"]["action_data"]
            manual_review = next(
                review
                for review in retrospective["category_reviews"]
                if review["category"] == "manual_friction"
            )
            manual_review["outcome"] = "no_finding"
            manual_review["source_event_ids"] = []
            self._rehash_result(result)
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(self._write(root / "manifest.json", manifest)),
                    str(self._write(root / "unclassified-waiting.json", result)),
                )
            self.assertEqual("integration_result_evidence_invalid", captured.exception.code)

    def test_observed_prohibition_requires_failed_incident_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            failed = ResultBuilder(manifest).non_delivery_result(
                "failed", observed_prohibition="merge_pr"
            )
            accepted = IntegrationService(root).accept_task_to_pr(
                ISSUE_KEY,
                str(self._write(root / "manifest.json", manifest)),
                str(self._write(root / "failed.json", failed)),
            )
            self.assertFalse(accepted["delivery_passed"])
            self.assertEqual(["merge_pr"], accepted["observed_prohibitions"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            blocked = ResultBuilder(manifest).non_delivery_result(
                "blocked", observed_prohibition="merge_pr"
            )
            with self.assertRaises(RuntimeErrorResult):
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(self._write(root / "manifest.json", manifest)),
                    str(self._write(root / "blocked.json", blocked)),
                )

    def test_critical_facts_must_be_runtime_probe_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result()
            for envelope in result["timeline"]:
                event = envelope["event"]
                if event["action"] == "jira_readback":
                    event["actor"] = "project_tool"
                    event["evidence_origin"] = "imported"
                    break
            self._rehash_result(result)
            with self.assertRaises(RuntimeErrorResult):
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(self._write(root / "manifest.json", manifest)),
                    str(self._write(root / "result.json", result)),
                )

    def test_unadapted_agentic_id_requires_gap_and_never_claims_formal_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root, agentic_id_field=None)
            result = ResultBuilder(manifest).ready_result()
            accepted = IntegrationService(root).accept_task_to_pr(
                ISSUE_KEY,
                str(self._write(root / "manifest.json", manifest)),
                str(self._write(root / "result.json", result)),
            )
            self.assertTrue(accepted["delivery_passed"])
            self.assertFalse(accepted["formal_takeover_verified"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root, agentic_id_field=None)
            result = ResultBuilder(manifest).ready_result(include_agentic_gap=False)
            with self.assertRaises(RuntimeErrorResult):
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(self._write(root / "manifest.json", manifest)),
                    str(self._write(root / "result.json", result)),
                )

    def test_confirmation_and_result_digests_are_canonical_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result()

            changed_manifest = copy.deepcopy(manifest)
            changed_manifest["scope"]["included"].append("docs/**")
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(self._write(root / "manifest-changed.json", changed_manifest)),
                    str(self._write(root / "result-original.json", result)),
                )
            self.assertEqual(
                "integration_manifest_confirmation_mismatch", captured.exception.code
            )

            arbitrary_authorization = copy.deepcopy(manifest)
            arbitrary_authorization["authorization"]["reference"] = (  # type: ignore[index]
                "arbitrary-nonempty-reference"
            )
            arbitrary_authorization["authorization"][  # type: ignore[index]
                "confirmed_manifest_sha256"
            ] = manifest_sha256(arbitrary_authorization)
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(
                        self._write(
                            root / "manifest-arbitrary-authorization.json",
                            arbitrary_authorization,
                        )
                    ),
                    str(root / "result-original.json"),
                )
            self.assertEqual("integration_protocol_schema_invalid", captured.exception.code)

            changed_result = copy.deepcopy(result)
            changed_result["next_action"] = "被摘要保护的改写"
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY,
                    str(self._write(root / "manifest-original.json", manifest)),
                    str(self._write(root / "result-changed.json", changed_result)),
                )
            self.assertEqual("integration_result_digest_mismatch", captured.exception.code)

    def test_ready_package_rejects_each_missing_or_mismatched_evidence_binding(self) -> None:
        cases = {
            "issue": {"jira_issue_key": "TAP-12290"},
            "run": {"run_id": "other-run"},
            "branch": {"branch": "codex/TAP-12290/other"},
            "pr_sha": {"pr_head_sha": "b" * 40},
            "verification": {"command_sha256": "b" * 64},
            "authorization": {"external_authorization": "other-confirmation"},
            "retrospective": {
                "retrospective_categories": QUALITY_CATEGORIES[:-1]
            },
            "prohibition": {"observed_prohibition": "merge_pr"},
            "missing_commit": {"include_git_commit": False},
        }
        for name, options in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = self._manifest(root)
                result_options = dict(options)
                run_id = str(result_options.pop("run_id", RUN_ID))
                result = ResultBuilder(manifest, run_id=run_id).ready_result(
                    **result_options
                )
                with self.assertRaises(RuntimeErrorResult):
                    IntegrationService(root).accept_task_to_pr(
                        ISSUE_KEY,
                        str(self._write(root / "manifest.json", manifest)),
                        str(self._write(root / "result.json", result)),
                    )

    def test_action_attribution_rejects_tampered_baseline_and_existing_pr_update(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result()
            baseline = next(
                envelope["event"]["action_data"]
                for envelope in result["timeline"]
                if envelope["event"]["action"] == "prohibition_baseline"
            )
            branch = next(
                envelope["event"]["action_data"]
                for envelope in result["timeline"]
                if envelope["event"]["action"] == "remote_branch_readback"
            )
            baseline["local_head_sha"] = "d" * 40
            branch["baseline_local_head_sha"] = "d" * 40
            self._rehash_result(result)
            with self.assertRaises(RuntimeErrorResult) as captured:
                validate_result_package(manifest, result)
            self.assertEqual(
                "integration_result_evidence_invalid", captured.exception.code
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result()
            branch = next(
                envelope["event"]["action_data"]
                for envelope in result["timeline"]
                if envelope["event"]["action"] == "remote_branch_readback"
            )
            branch["baseline_local_head_sha"] = "a" * 40
            self._rehash_result(result)
            with self.assertRaises(RuntimeErrorResult) as captured:
                validate_result_package(manifest, result)
            self.assertEqual(
                "integration_result_evidence_invalid", captured.exception.code
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result()
            baseline = next(
                envelope["event"]["action_data"]
                for envelope in result["timeline"]
                if envelope["event"]["action"] == "prohibition_baseline"
            )
            baseline["task_branch_remote_sha"] = "b" * 40
            baseline["task_open_pr"] = {
                "number": 99,
                "url": "https://github.com/tapdata/tapdata/pull/99",
                "head_sha": "b" * 40,
                "base_branch": "develop",
            }
            self._rehash_result(result)
            with self.assertRaises(RuntimeErrorResult) as captured:
                validate_result_package(manifest, result)
            self.assertEqual(
                "integration_result_evidence_invalid", captured.exception.code
            )

    def test_json_duplicate_keys_and_non_finite_numbers_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = self._manifest(root)
            result = ResultBuilder(manifest).ready_result()
            manifest_path = self._write(root / "manifest.json", manifest)
            result_path = self._write(root / "result.json", result)

            duplicate = root / "duplicate.json"
            duplicate.write_text(
                result_path.read_text(encoding="utf-8").replace(
                    '"schema_version": 1,',
                    '"schema_version": 1, "schema_version": 1,',
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY, str(manifest_path), str(duplicate)
                )
            self.assertEqual("integration_protocol_json_invalid", captured.exception.code)

            non_finite = root / "non-finite.json"
            non_finite.write_text(
                result_path.read_text(encoding="utf-8").replace(
                    '"duration_seconds": 0.1', '"duration_seconds": NaN', 1
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY, str(manifest_path), str(non_finite)
                )
            self.assertEqual("integration_protocol_json_invalid", captured.exception.code)

            invalid_utf = root / "invalid-utf.json"
            invalid_utf.write_bytes(b"\xff")
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY, str(manifest_path), str(invalid_utf)
                )
            self.assertEqual("integration_protocol_json_invalid", captured.exception.code)

            too_large = root / "too-large.json"
            too_large.write_bytes(b" " * 1_048_577)
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(root).accept_task_to_pr(
                    ISSUE_KEY, str(manifest_path), str(too_large)
                )
            self.assertEqual("integration_protocol_json_too_large", captured.exception.code)

            event = copy.deepcopy(result["timeline"][0]["event"])
            event["duration_seconds"] = float("inf")
            with self.assertRaises(RuntimeErrorResult):
                validate_event(event)

    def test_cli_exposes_only_unambiguous_integration_commands(self) -> None:
        parser = build_parser()
        expected = (
            "prepare-task-to-pr",
            "accept-task-to-pr",
            "prepare-offline",
            "run-offline",
        )
        for command in expected:
            arguments = ["integration", command, ISSUE_KEY]
            if command in {"accept-task-to-pr", "run-offline"}:
                arguments.extend(["--manifest", "manifest.json"])
            if command == "accept-task-to-pr":
                arguments.extend(["--result", "result.json"])
            parsed = parser.parse_args(arguments)
            self.assertEqual(command, parsed.command)
        for legacy in ("prepare", "run"):
            with self.assertRaises(ArgumentParserError):
                parser.parse_args(["integration", legacy, ISSUE_KEY])

    def test_maintainer_rejects_side_effect_verification_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for command in (
                ["bash", "-c", "touch /tmp/side-effect"],
                ["python3", "-c", "raise SystemExit(0)"],
                ["git", "push", "origin", "main"],
                ["gh", "pr", "merge", "1"],
                ["curl", "https://example.com"],
                ["ao-work", "jira", "inspect", "--issue-key", ISSUE_KEY],
                ["npm", "install", "example-package"],
                ["eslint", "--fix-dry-run", "src"],
                ["pytest", "--snapshot-update"],
                ["pytest", ".agentic-ops/tasks/TAP-12289"],
            ):
                with self.subTest(command=command):
                    manifest = self._manifest(root)
                    manifest["verification"][0]["command"] = command  # type: ignore[index]
                    manifest["authorization"][  # type: ignore[index]
                        "confirmed_manifest_sha256"
                    ] = manifest_sha256(manifest)
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        validate_manifest(manifest, ISSUE_KEY)
                    self.assertEqual(
                        "integration_verification_command_forbidden",
                        captured.exception.code,
                    )

    def _manifest(
        self, root: Path, *, agentic_id_field: str | None = "customfield_10001"
    ) -> dict[str, object]:
        manifest: dict[str, object] = {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "workspace": {"root": str(root / "nonexistent-business-workspace")},
            "issue": {"key": ISSUE_KEY, "id": "12289", "project_key": "TAP"},
            "jira": {
                "base_url": "https://example.atlassian.net",
                "account_id": "account-123",
                "assignee_account_id": "account-123",
                "status_mapping": {"进行中": "in_progress"},
                "allowed_status_categories": ["indeterminate"],
                "agentic_id_field": agentic_id_field,
            },
            "agent": {
                "agent_id": "harsen-mini-test-bot",
                "project_profile": "tapdata",
                "agentic_run_id": RUN_ID,
            },
            "task_binding": {
                "issue_content_sha256": "9" * 64,
                "approved_plan_file": "inputs/approved-plan.md",
                "approved_plan_sha256": "8" * 64,
            },
            "execution_identity": {
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen-test-bot@example.com",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen-test-bot@example.com",
                "github_actor_login": "harsen-mini-test-bot",
            },
            "repository": {
                "root": str(root / "nonexistent-business-repository"),
                "slug": "tapdata/tapdata",
                "remote_name": "origin",
                "base_branch": "develop",
                "task_branch": "codex/TAP-12289/task-to-pr",
                "target_branch": "develop",
                "protected_branches": ["main", "develop"],
            },
            "scope": {"included": ["src/**"], "excluded": []},
            "verification": [
                {
                    "id": "unit",
                    "command": ["python3", "-m", "unittest"],
                    "working_directory": ".",
                    "timeout_seconds": 300,
                }
            ],
            "pr_endpoint": {
                "provider": "github",
                "repository_slug": "tapdata/tapdata",
                "target_branch": "develop",
                "ci_policy": "require_passed",
            },
            "permitted_external_actions": [
                "jira_read",
                "jira_comment",
                "jira_worklog",
                "git_commit",
                "git_remote_read",
                "git_push_task_branch",
                "github_pr_create_or_update",
                "github_pr_read",
            ],
            "authorization": {
                "reference": AUTHORIZATION,
                "confirmed_by": "harsen",
                "confirmed_at": "2026-08-13T01:00:00+00:00",
                "confirmed_manifest_sha256": "",
            },
        }
        manifest["authorization"]["confirmed_manifest_sha256"] = manifest_sha256(
            manifest
        )
        return manifest

    @staticmethod
    def _write(path: Path, payload: dict[str, object]) -> Path:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _rehash_result(result: dict[str, object]) -> None:
        previous: str | None = None
        timeline = result["timeline"]
        assert isinstance(timeline, list)
        for sequence, envelope in enumerate(timeline, start=1):
            envelope["sequence"] = sequence
            envelope["previous_event_sha256"] = previous
            base = {
                "sequence": sequence,
                "previous_event_sha256": previous,
                "event": envelope["event"],
            }
            envelope["event_sha256"] = canonical_sha256(base)
            previous = envelope["event_sha256"]
        result["result_sha256"] = result_sha256(result)


if __name__ == "__main__":
    unittest.main()
