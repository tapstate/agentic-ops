from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from ao_work.config.model import ProjectProfile
from ao_work.jira.adf import (
    extract_description_section,
    markdown_to_adf,
    merge_description_sections,
    normalize_title,
)
from ao_work.jira.client import JiraClient, JiraTransportError
from ao_work.jira.model import JiraIssue, plain_text
from ao_work.jira.transition import (
    adaptation_material,
    completed_stage_for,
    match_transition,
)
from ao_work.output import EXIT_BLOCKED, EXIT_CAPABILITY_GAP, RuntimeErrorResult

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class WritePlan:
    operation: str
    issue_key: str
    agentic_run_id: str
    idempotency_key: str
    plan_id: str
    action: str
    content_sha256: str
    payload: dict[str, Any]
    existing_external_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WritePlan":
        expected = {
            "operation",
            "issue_key",
            "agentic_run_id",
            "idempotency_key",
            "plan_id",
            "action",
            "content_sha256",
            "payload",
            "existing_external_id",
        }
        if set(payload) != expected or not isinstance(payload.get("payload"), dict):
            raise ValueError("write plan fields do not match the protocol")
        return cls(
            operation=str(payload["operation"]),
            issue_key=str(payload["issue_key"]),
            agentic_run_id=str(payload["agentic_run_id"]),
            idempotency_key=str(payload["idempotency_key"]),
            plan_id=str(payload["plan_id"]),
            action=str(payload["action"]),
            content_sha256=str(payload["content_sha256"]),
            payload=dict(payload["payload"]),
            existing_external_id=str(payload.get("existing_external_id", "")),
        )

    def validate_integrity(self) -> None:
        rebuilt = _build_plan(
            self.operation,
            self.issue_key,
            self.agentic_run_id,
            self.idempotency_key,
            self.payload,
            self.existing_external_id,
        )
        if (
            rebuilt.plan_id != self.plan_id
            or rebuilt.content_sha256 != self.content_sha256
            or rebuilt.action != self.action
        ):
            raise RuntimeErrorResult(
                code="jira_write_plan_tampered",
                message="Jira 写入计划文件内容或摘要不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请丢弃计划文件，重新执行 plan 并确认新计划",
            )


@dataclass(frozen=True)
class WriteAttempt:
    schema_version: int
    attempt_id: str
    operation: str
    issue_key: str
    agentic_run_id: str
    plan_id: str
    content_sha256: str
    authorization_reference: str
    plan_precondition: str
    request_started_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WriteAttempt":
        expected = {
            "schema_version",
            "attempt_id",
            "operation",
            "issue_key",
            "agentic_run_id",
            "plan_id",
            "content_sha256",
            "authorization_reference",
            "plan_precondition",
            "request_started_at",
        }
        if set(payload) != expected:
            raise ValueError("write attempt fields do not match the protocol")
        return cls(
            schema_version=int(payload["schema_version"]),
            attempt_id=str(payload["attempt_id"]),
            operation=str(payload["operation"]),
            issue_key=str(payload["issue_key"]),
            agentic_run_id=str(payload["agentic_run_id"]),
            plan_id=str(payload["plan_id"]),
            content_sha256=str(payload["content_sha256"]),
            authorization_reference=str(payload["authorization_reference"]),
            plan_precondition=str(payload["plan_precondition"]),
            request_started_at=str(payload["request_started_at"]),
        )

    def validate_integrity(self, plan: WritePlan) -> None:
        rebuilt = build_write_attempt(
            plan,
            self.authorization_reference,
            request_started_at=self.request_started_at,
        )
        if rebuilt != self:
            raise RuntimeErrorResult(
                code="jira_write_attempt_tampered",
                message="Jira 写入尝试文件与当前计划不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请停止写入并核对原始计划与尝试记录",
            )


def build_write_attempt(
    plan: WritePlan,
    authorization_reference: str,
    *,
    request_started_at: str | None = None,
) -> WriteAttempt:
    plan.validate_integrity()
    if (
        plan.operation not in {"jira_comment", "jira_worklog"}
        or plan.action != "create_or_update"
        or plan.existing_external_id
    ):
        raise RuntimeErrorResult(
            code="jira_write_attempt_not_allowed",
            message="只有首次计划确认标记不存在时才能建立 Jira create 尝试",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请重新 plan；既有 Jira 记录不能伪装为本运行创建",
        )
    if not isinstance(authorization_reference, str) or not authorization_reference.strip():
        raise _input_error(
            "jira_write_authorization_required", "Jira 写入尝试缺少授权引用"
        )
    started = request_started_at or datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError as error:
        raise _input_error(
            "jira_write_attempt_started_invalid", "Jira 写入尝试时间必须是 ISO 8601"
        ) from error
    if parsed.tzinfo is None:
        raise _input_error(
            "jira_write_attempt_started_invalid", "Jira 写入尝试时间必须包含时区"
        )
    canonical = {
        "schema_version": 1,
        "operation": plan.operation,
        "issue_key": plan.issue_key,
        "agentic_run_id": plan.agentic_run_id,
        "plan_id": plan.plan_id,
        "content_sha256": plan.content_sha256,
        "authorization_reference": authorization_reference,
        "plan_precondition": "marker_absent",
        "request_started_at": started,
    }
    attempt_id = "attempt-" + hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    return WriteAttempt(attempt_id=attempt_id, **canonical)


class JiraService:
    def __init__(self, profile: ProjectProfile, client: JiraClient) -> None:
        self.profile = profile
        self.client = client

    def inspect_issue(self, issue_key: str) -> JiraIssue:
        self.validate_profile_fields()
        issue = self.client.get_issue(issue_key)
        self._validate_issue(issue)
        return issue

    def validate_profile_fields(self) -> None:
        required = self.profile.active_custom_field_ids()
        if not required:
            return
        available = {
            str(item.get("id")) for item in self.client.field_metadata() if isinstance(item.get("id"), str)
        }
        missing = sorted(required - available)
        if missing:
            raise RuntimeErrorResult(
                code="jira_field_mapping_missing",
                message=f"项目 Profile 映射的 Jira 字段不存在：{', '.join(missing)}",
                status="capability_gap",
                exit_code=EXIT_CAPABILITY_GAP,
                required_human_action=(
                    "请先核对 field ID；若涉及字段元数据、Context、Screen、权限或跨项目语义，"
                    "另开 Jira Custom Field 专题"
                ),
            )

    def validate_apply(
        self,
        plan: WritePlan,
        expected_plan_id: str,
        operation: str,
    ) -> None:
        plan.validate_integrity()
        _verify_plan(plan, expected_plan_id, operation)
        if operation == "jira_comment":
            self._validate_comment_plan(plan)
        elif operation == "jira_worklog":
            self._validate_worklog_plan(plan)
        elif operation == "jira_description":
            sections = plan.payload.get("sections")
            if not isinstance(sections, dict) or not all(
                isinstance(title, str) and isinstance(content, str)
                for title, content in sections.items()
            ):
                raise _input_error(
                    "invalid_description_section",
                    "Description 受管章节结构无效",
                )
            self._validate_description_sections(sections)
        elif operation == "jira_transition":
            self._validate_transition_plan(plan)
        else:
            raise _input_error("jira_write_plan_mismatch", "Jira 写入计划操作无效")
        self._validate_owner(self.inspect_issue(plan.issue_key))

    @staticmethod
    def validate_no_credentials(plan: WritePlan, email: str, token: str) -> None:
        plan.validate_integrity()
        for label, secret in (("Jira email", email), ("Jira token", token)):
            if secret and _contains_text(plan.payload, secret):
                raise RuntimeErrorResult(
                    code="jira_credential_exposure_forbidden",
                    message=f"Jira 写入计划包含当前 {label} 凭证值，已阻断外发",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请移除凭证内容，重新生成 Jira 写入计划",
                )

    def validate_authorization_comment(
        self,
        issue_key: str,
        comment_id: str,
        required_marker: str,
    ) -> None:
        try:
            comment = self.client.comment(issue_key, comment_id)
        except RuntimeErrorResult as error:
            if error.code != "jira_issue_not_found":
                raise
            raise RuntimeErrorResult(
                code="jira_authorization_reference_not_found",
                message="当前 Jira 任务中不存在授权引用指定的评论",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action="请核对当前任务的 Jira 评论 ID 后重试",
            ) from error
        if (
            comment.comment_id != comment_id
            or required_marker not in comment.standalone_lines
        ):
            raise RuntimeErrorResult(
                code="jira_authorization_reference_not_found",
                message="Jira 人工确认评论不存在，或未绑定当前任务运行与写入计划",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action=(
                    "请在当前 Jira 任务留下以独立完整行包含 plan 输出 authorization_comment_marker 的人工确认评论，"
                    "并使用该评论的正整数 ID"
                ),
            )

    def plan_comment(
        self,
        issue_key: str,
        idempotency_key: str,
        category: str,
        content: str,
        *,
        agentic_run_id: str,
        comment_template_schema: dict[str, Any] | None = None,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        self._validate_owner(issue)
        _require_chinese(content, "Jira 评论")
        if category not in {"analysis", "plan", "decision", "evidence", "blocked", "progress"}:
            raise _input_error("invalid_comment_category", "评论分类无效")
        if category in {"progress", "evidence"}:
            _validate_comment_template(
                category,
                content,
                comment_template_schema or {},
            )
        marker = _marker(issue.key, agentic_run_id, idempotency_key)
        normalized_content = content.rstrip()
        markdown = f"{normalized_content}\n\n{marker}\n"
        existing = [
            comment
            for comment in self.client.comments(issue.key)
            if _has_exact_marker(comment, marker)
        ]
        _ensure_no_duplicates(existing, "jira_comment_duplicate")
        if existing:
            _require_comment_content(existing[0], markdown)
        return _build_plan(
            "jira_comment",
            issue.key,
            agentic_run_id,
            idempotency_key,
            {
                "category": category,
                "content": normalized_content,
                "markdown": markdown,
                "body_sha256": _text_sha256(_rendered_markdown_text(markdown)),
            },
            existing[0].comment_id if existing else "",
        )

    def apply_comment(
        self,
        plan: WritePlan,
        expected_plan_id: str,
        *,
        begin_create: Callable[[WritePlan], WriteAttempt] | None = None,
    ) -> dict[str, Any]:
        self.validate_apply(plan, expected_plan_id, "jira_comment")
        marker = _plan_marker(plan)
        existing = [
            comment
            for comment in self.client.comments(plan.issue_key)
            if _has_exact_marker(comment, marker)
        ]
        _ensure_no_duplicates(existing, "jira_comment_duplicate")
        if existing:
            _require_comment_content(existing[0], str(plan.payload["markdown"]))
            if plan.action == "create_or_update":
                raise _create_precondition_changed("Jira 评论")
            return _comment_readback(
                plan,
                existing[0],
                created=False,
                attempt=None,
            )
        attempt = _begin_create_attempt(plan, begin_create)
        try:
            self.client.add_comment(plan.issue_key, str(plan.payload["markdown"]))
        except JiraTransportError as error:
            raise _unknown_write("Jira 评论", error, attempt) from error
        readback = [
            comment
            for comment in self.client.comments(plan.issue_key)
            if _has_exact_marker(comment, marker)
        ]
        _ensure_single_readback(readback, "jira_comment_readback_failed")
        _require_comment_content(readback[0], str(plan.payload["markdown"]))
        return _comment_readback(
            plan,
            readback[0],
            created=True,
            attempt=attempt,
        )

    def readback_comment(
        self, plan: WritePlan, *, attempt: WriteAttempt | None = None
    ) -> dict[str, Any]:
        self.validate_apply(plan, plan.plan_id, "jira_comment")
        marker = _plan_marker(plan)
        found = [
            comment
            for comment in self.client.comments(plan.issue_key)
            if _has_exact_marker(comment, marker)
        ]
        _ensure_single_readback(found, "jira_comment_readback_failed")
        _require_comment_content(found[0], str(plan.payload["markdown"]))
        created = _readback_creation_status(plan, attempt)
        return _comment_readback(
            plan,
            found[0],
            created=created,
            attempt=attempt,
        )

    def plan_transition(
        self,
        issue_key: str,
        idempotency_key: str,
        *,
        agentic_run_id: str,
        target_status: str | None = None,
        target_transition: str | None = None,
        comment: str | None = None,
    ) -> WritePlan:
        """计划一次 Jira 状态流转（D-037 严格匹配，禁止模糊猜测）。

        - 目标来源二选一：--target-transition（profile 映射 key）、--target-status（目标状态名）。
        - AIAgent 默认禁止推进 completed stage（无合入权，D-049）；例外需 profile 显式声明。
        - 匹配失败输出适配对照材料（当前状态 + Jira 可用 transitions + 已配置映射）。
        - 幂等锚点：目标状态达成即视为已执行（transition 无稳定外部 ID）。
        """
        provided = [
            value is not None for value in (target_status, target_transition)
        ]
        if sum(provided) != 1:
            raise _input_error(
                "invalid_transition_target",
                "目标必须且只能指定一个：--target-status / --target-transition",
            )
        issue = self.inspect_issue(issue_key)
        self._validate_owner(issue)
        mapping = {
            "transitions": self.profile.transition_mapping,
            "statuses": self.profile.status_mapping,
        }
        # 安全边界前置拦截：目标状态若映射到 completed stage，不依赖 transition
        # 条目是否存在（D-049：AIAgent 无合入权，默认禁止推进完成态）
        if target_status is not None:
            completed = completed_stage_for(self.profile.status_mapping, target_status)
            if completed is not None:
                raise RuntimeErrorResult(
                    code="jira_transition_completed_forbidden",
                    message=(
                        f"目标状态 {target_status!r} 属于完成态（{completed}），"
                        "AIAgent 无合入权，默认禁止推进完成态"
                    ),
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action=(
                        "请由研发工程师在 Jira 处理完成态；确需 AIAgent 推进时，"
                        "先确认 profile 显式例外"
                    ),
                    details={
                        "issue_key": issue.key,
                        "target_status": target_status,
                        "completed_stage": completed,
                    },
                )
        available = self.client.available_transitions(issue.key)
        matched = match_transition(
            issue.status,
            available,
            mapping,
            target_status=target_status,
            target_key=target_transition,
        )
        if matched is None:
            raise RuntimeErrorResult(
                code="jira_transition_mapping_gap",
                message=(
                    "无法按 D-037 规则匹配 Jira 状态流转目标，已停止连续自动化；"
                    "details 提供可直接照抄的适配对照材料"
                ),
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action=(
                    "按 details 对照材料补齐 profile transitions 配置后重新 plan，"
                    "不要临场猜测 Jira 状态"
                ),
                details=adaptation_material(
                    issue.key,
                    issue.project_key,
                    issue.status,
                    available,
                    mapping,
                ),
            )
        matched_id, matched_name, matched_status = matched
        completed = completed_stage_for(self.profile.status_mapping, matched_status)
        if completed is not None:
            raise RuntimeErrorResult(
                code="jira_transition_completed_forbidden",
                message=(
                    f"目标状态 {matched_status!r} 属于完成态（{completed}），"
                    "AIAgent 无合入权，默认禁止推进完成态"
                ),
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action=(
                    "请由研发工程师在 Jira 处理完成态；确需 AIAgent 推进时，"
                    "先确认 profile 显式例外"
                ),
                details={
                    "issue_key": issue.key,
                    "target_status": matched_status,
                    "completed_stage": completed,
                },
            )
        normalized_comment = comment.strip() if comment else ""
        if normalized_comment:
            _require_chinese(normalized_comment, "状态流转说明评论")
        payload = {
            "project_key": issue.project_key,
            "from_status": issue.status,
            "target_status": matched_status,
            "transition_id": matched_id,
            "transition_name": matched_name,
            "comment": normalized_comment,
            "body_sha256": (
                _text_sha256(_rendered_markdown_text(normalized_comment))
                if normalized_comment
                else ""
            ),
            "available": available,
        }
        return _build_plan(
            "jira_transition",
            issue.key,
            agentic_run_id,
            idempotency_key,
            payload,
            "",
        )

    def apply_transition(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        self.validate_apply(plan, expected_plan_id, "jira_transition")
        target_status = str(plan.payload["target_status"])
        from_status = str(plan.payload["from_status"])
        transition_id = str(plan.payload["transition_id"])
        comment = str(plan.payload.get("comment", ""))
        issue = self.inspect_issue(plan.issue_key)
        self._validate_owner(issue)
        if issue.status == target_status:
            return _transition_readback(plan, issue.status, created=False)
        if issue.status != from_status:
            raise RuntimeErrorResult(
                code="jira_transition_mapping_gap",
                message=(
                    "Jira 当前状态与计划时不一致，状态流转前置条件已变化；"
                    "禁止跨状态执行计划，请重新 plan"
                ),
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action="请重新执行 plan 对齐 Jira 当前事实后再 apply",
                details=adaptation_material(
                    plan.issue_key,
                    str(plan.payload.get("project_key", "")),
                    issue.status,
                    self.client.available_transitions(plan.issue_key),
                    {
                        "transitions": self.profile.transition_mapping,
                        "statuses": self.profile.status_mapping,
                    },
                ),
            )
        available = self.client.available_transitions(plan.issue_key)
        if not any(item["id"] == transition_id for item in available):
            raise RuntimeErrorResult(
                code="jira_transition_mapping_gap",
                message="Jira 可用 transition 已变化，计划引用的 transition 不再可用；请重新 plan",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action="请重新 plan 获取最新可用 transition 列表",
                details=adaptation_material(
                    plan.issue_key,
                    str(plan.payload.get("project_key", "")),
                    issue.status,
                    available,
                    {
                        "transitions": self.profile.transition_mapping,
                        "statuses": self.profile.status_mapping,
                    },
                ),
            )
        try:
            self.client.execute_transition(
                plan.issue_key,
                transition_id,
                comment=comment or None,
            )
        except JiraTransportError as error:
            raise RuntimeErrorResult(
                code="jira_transition_failed",
                message="Jira 状态流转执行失败（响应不明确）",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请先回读 Jira 实际状态，结果确认后再继续",
            ) from error
        readback = self.inspect_issue(plan.issue_key)
        if readback.status != target_status:
            raise RuntimeErrorResult(
                code="jira_transition_readback_mismatch",
                message=(
                    f"状态流转后回读状态 {readback.status!r} 与目标状态 "
                    f"{target_status!r} 不一致"
                ),
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请人工核对 Jira 实际状态，不要盲目重试流转",
                details={
                    "issue_key": plan.issue_key,
                    "current_status": readback.status,
                    "target_status": target_status,
                },
            )
        return _transition_readback(plan, readback.status, created=True)

    def readback_transition(self, plan: WritePlan) -> dict[str, Any]:
        self.validate_apply(plan, plan.plan_id, "jira_transition")
        issue = self.inspect_issue(plan.issue_key)
        matched = issue.status == str(plan.payload["target_status"])
        return _transition_readback(plan, issue.status, created=matched)

    def _validate_transition_plan(self, plan: WritePlan) -> None:
        expected = {
            "project_key",
            "from_status",
            "target_status",
            "transition_id",
            "transition_name",
            "comment",
            "body_sha256",
            "available",
        }
        if set(plan.payload) != expected:
            raise _input_error("jira_write_plan_invalid", "Jira 状态流转计划字段无效")
        project_key = plan.payload.get("project_key")
        from_status = plan.payload.get("from_status")
        target_status = plan.payload.get("target_status")
        transition_id = plan.payload.get("transition_id")
        transition_name = plan.payload.get("transition_name")
        comment = plan.payload.get("comment")
        body_sha256 = plan.payload.get("body_sha256")
        available = plan.payload.get("available")
        if (
            not isinstance(project_key, str)
            or not project_key.strip()
            or not isinstance(from_status, str)
            or not from_status.strip()
            or not isinstance(target_status, str)
            or not target_status.strip()
            or not isinstance(transition_id, str)
            or not transition_id.isdigit()
            or not isinstance(transition_name, str)
            or not transition_name.strip()
            or not isinstance(comment, str)
            or not isinstance(available, list)
        ):
            raise _input_error("jira_write_plan_invalid", "Jira 状态流转计划内容无效")
        if comment:
            if not _is_sha256(body_sha256):
                raise _input_error("jira_write_plan_invalid", "Jira 状态流转评论摘要无效")
            if body_sha256 != _text_sha256(_rendered_markdown_text(comment)):
                raise _input_error("jira_write_plan_invalid", "Jira 状态流转评论摘要不一致")
            _require_chinese(comment, "状态流转说明评论")
        elif body_sha256:
            raise _input_error("jira_write_plan_invalid", "Jira 状态流转评论摘要无效")

    def plan_worklog(
        self,
        issue_key: str,
        idempotency_key: str,
        title: str,
        details: str,
        time_spent_seconds: int,
        started: str,
        excludes_waiting: bool,
        *,
        agentic_run_id: str,
        included_work: list[dict[str, Any]],
        excluded_waiting_categories: list[str],
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        self._validate_owner(issue)
        _require_chinese(title, "Worklog 标题")
        _require_chinese(details, "Worklog 内容")
        if time_spent_seconds <= 0:
            raise _input_error("invalid_worklog_duration", "Worklog 耗时必须大于 0")
        canonical_started = _canonical_started(started)
        if not excludes_waiting:
            raise _input_error(
                "worklog_waiting_exclusion_required",
                "必须明确确认 Worklog 不包含等待时间",
            )
        normalized_included_work = _validate_included_work(
            included_work, time_spent_seconds
        )
        normalized_excluded_waiting = _validate_excluded_waiting_categories(
            excluded_waiting_categories
        )
        marker = _marker(issue.key, agentic_run_id, idempotency_key)
        normalized_details = details.rstrip()
        markdown = _worklog_markdown(
            title.strip(),
            normalized_details,
            normalized_included_work,
            normalized_excluded_waiting,
            marker,
        )
        existing = [
            worklog
            for worklog in self.client.worklogs(issue.key)
            if _has_exact_marker(worklog, marker)
        ]
        _ensure_no_duplicates(existing, "jira_worklog_duplicate")
        expected_worklog = {
            "title": title.strip(),
            "details": normalized_details,
            "markdown": markdown,
            "body_sha256": _text_sha256(_rendered_markdown_text(markdown)),
            "details_sha256": _text_sha256(normalized_details),
            "time_spent_seconds": time_spent_seconds,
            "started": canonical_started,
            "excludes_waiting": True,
            "included_work": normalized_included_work,
            "excluded_waiting_categories": normalized_excluded_waiting,
        }
        if existing:
            _require_worklog_content(existing[0], expected_worklog)
        return _build_plan(
            "jira_worklog",
            issue.key,
            agentic_run_id,
            idempotency_key,
            expected_worklog,
            existing[0].worklog_id if existing else "",
        )

    def apply_worklog(
        self,
        plan: WritePlan,
        expected_plan_id: str,
        *,
        begin_create: Callable[[WritePlan], WriteAttempt] | None = None,
    ) -> dict[str, Any]:
        self.validate_apply(plan, expected_plan_id, "jira_worklog")
        marker = _plan_marker(plan)
        existing = [
            worklog
            for worklog in self.client.worklogs(plan.issue_key)
            if _has_exact_marker(worklog, marker)
        ]
        _ensure_no_duplicates(existing, "jira_worklog_duplicate")
        if existing:
            _require_worklog_content(existing[0], plan.payload)
            if plan.action == "create_or_update":
                raise _create_precondition_changed("Jira Worklog")
            return _worklog_readback(
                plan,
                existing[0],
                created=False,
                attempt=None,
            )
        attempt = _begin_create_attempt(plan, begin_create)
        try:
            self.client.add_worklog(
                plan.issue_key,
                time_spent_seconds=int(plan.payload["time_spent_seconds"]),
                started=str(plan.payload["started"]),
                markdown=str(plan.payload["markdown"]),
            )
        except JiraTransportError as error:
            raise _unknown_write("Jira Worklog", error, attempt) from error
        readback = [
            worklog
            for worklog in self.client.worklogs(plan.issue_key)
            if _has_exact_marker(worklog, marker)
        ]
        _ensure_single_readback(readback, "jira_worklog_readback_failed")
        _require_worklog_content(readback[0], plan.payload)
        return _worklog_readback(
            plan,
            readback[0],
            created=True,
            attempt=attempt,
        )

    def readback_worklog(
        self, plan: WritePlan, *, attempt: WriteAttempt | None = None
    ) -> dict[str, Any]:
        self.validate_apply(plan, plan.plan_id, "jira_worklog")
        marker = _plan_marker(plan)
        found = [
            worklog
            for worklog in self.client.worklogs(plan.issue_key)
            if _has_exact_marker(worklog, marker)
        ]
        _ensure_single_readback(found, "jira_worklog_readback_failed")
        _require_worklog_content(found[0], plan.payload)
        created = _readback_creation_status(plan, attempt)
        return _worklog_readback(
            plan,
            found[0],
            created=created,
            attempt=attempt,
        )

    def plan_description(
        self,
        issue_key: str,
        idempotency_key: str,
        sections: dict[str, str],
        *,
        agentic_run_id: str,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        self._validate_owner(issue)
        if not sections:
            raise _input_error("empty_description_sections", "Description 受管章节不能为空")
        self._validate_description_sections(sections)
        for title, content in sections.items():
            _require_chinese(title, "Description 章节标题")
            _require_chinese(content, f"Description 章节 {title}")
        merged = merge_description_sections(issue.description, sections)
        unchanged = all(
            extract_description_section(issue.description, title) == content.strip()
            for title, content in sections.items()
        )
        return _build_plan(
            "jira_description",
            issue.key,
            agentic_run_id,
            idempotency_key,
            {"sections": sections, "description": merged},
            "description" if unchanged else "",
        )

    def apply_description(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        self.validate_apply(plan, expected_plan_id, "jira_description")
        issue = self.inspect_issue(plan.issue_key)
        sections = dict(plan.payload["sections"])
        if all(
            extract_description_section(issue.description, title) == str(content).strip()
            for title, content in sections.items()
        ):
            return {"external_id": "description", "created": False}
        merged = merge_description_sections(issue.description, sections)
        try:
            self.client.update_description(plan.issue_key, merged)
        except JiraTransportError as error:
            raise _unknown_write("Jira Description", error) from error
        readback = self.inspect_issue(plan.issue_key)
        if not all(
            extract_description_section(readback.description, title) == str(content).strip()
            for title, content in sections.items()
        ):
            raise RuntimeErrorResult(
                code="jira_description_readback_failed",
                message="Jira Description 写入后回读不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请人工核对 Description，不要重复写入",
            )
        return {"external_id": "description", "created": True}

    def _validate_description_sections(self, sections: dict[str, str]) -> None:
        allowed: dict[str, str] = {}
        for mapping in self.profile.fields.values():
            if (
                mapping.source != "jira_description_section"
                or mapping.state != "active"
                or not mapping.writable
                or not mapping.section
            ):
                continue
            normalized = normalize_title(mapping.section)
            if not normalized or normalized in allowed:
                raise RuntimeErrorResult(
                    code="jira_description_mapping_invalid",
                    message="Project Profile 的可写 Description 章节映射重复或无效",
                    status="capability_gap",
                    exit_code=EXIT_CAPABILITY_GAP,
                    required_human_action="请修复 Project Profile 的可写章节白名单后重试",
                )
            allowed[normalized] = mapping.section.strip()

        requested: set[str] = set()
        for title in sections:
            normalized = normalize_title(title)
            canonical = allowed.get(normalized)
            if (
                not normalized
                or normalized in requested
                or canonical is None
                or title.strip() != canonical
            ):
                raise _input_error(
                    "description_section_not_writable",
                    f"Description 章节未在 effective Project Profile 中显式声明可写：{title}",
                )
            requested.add(normalized)

    @staticmethod
    def _validate_comment_plan(plan: WritePlan) -> None:
        if set(plan.payload) != {"category", "content", "markdown", "body_sha256"}:
            raise _input_error("jira_write_plan_invalid", "Jira 评论计划字段无效")
        category = plan.payload.get("category")
        content = plan.payload.get("content")
        markdown = plan.payload.get("markdown")
        body_sha256 = plan.payload.get("body_sha256")
        if category not in {"analysis", "plan", "decision", "evidence", "blocked"} or not isinstance(
            markdown, str
        ) or not isinstance(content, str) or not _is_sha256(body_sha256):
            raise _input_error("jira_write_plan_invalid", "Jira 评论计划内容无效")
        _require_chinese(content, "Jira 评论")
        marker = _plan_marker(plan)
        expected_markdown = f"{content.rstrip()}\n\n{marker}\n"
        if markdown != expected_markdown or marker not in _standalone_text_lines(markdown):
            raise _input_error("jira_write_plan_invalid", "Jira 评论计划正文或幂等标记无效")
        if body_sha256 != _text_sha256(_rendered_markdown_text(markdown)):
            raise _input_error("jira_write_plan_invalid", "Jira 评论正文摘要不一致")

    @staticmethod
    def _validate_worklog_plan(plan: WritePlan) -> None:
        expected = {
            "title",
            "details",
            "markdown",
            "body_sha256",
            "details_sha256",
            "time_spent_seconds",
            "started",
            "excludes_waiting",
            "included_work",
            "excluded_waiting_categories",
        }
        if set(plan.payload) != expected:
            raise _input_error("jira_write_plan_invalid", "Jira Worklog 计划字段无效")
        title = plan.payload.get("title")
        details = plan.payload.get("details")
        markdown = plan.payload.get("markdown")
        duration = plan.payload.get("time_spent_seconds")
        started = plan.payload.get("started")
        body_sha256 = plan.payload.get("body_sha256")
        details_sha256 = plan.payload.get("details_sha256")
        if (
            not isinstance(title, str)
            or not isinstance(details, str)
            or not isinstance(markdown, str)
            or not isinstance(duration, int)
            or isinstance(duration, bool)
            or duration <= 0
            or not isinstance(started, str)
            or not _is_sha256(body_sha256)
            or not _is_sha256(details_sha256)
            or plan.payload.get("excludes_waiting") is not True
        ):
            raise _input_error("jira_write_plan_invalid", "Jira Worklog 计划内容无效")
        _require_chinese(title, "Worklog 标题")
        _require_chinese(details, "Worklog 内容")
        _canonical_started(started)
        included_work = _validate_included_work(
            plan.payload.get("included_work"), duration
        )
        excluded_waiting_categories = _validate_excluded_waiting_categories(
            plan.payload.get("excluded_waiting_categories")
        )
        expected_markdown = _worklog_markdown(
            title.strip(),
            details.rstrip(),
            included_work,
            excluded_waiting_categories,
            _plan_marker(plan),
        )
        if markdown != expected_markdown:
            raise _input_error("jira_write_plan_invalid", "Jira Worklog 正文或幂等标记无效")
        if body_sha256 != _text_sha256(_rendered_markdown_text(markdown)):
            raise _input_error("jira_write_plan_invalid", "Jira Worklog 正文摘要不一致")
        if details_sha256 != _text_sha256(details.rstrip()):
            raise _input_error("jira_write_plan_invalid", "Jira Worklog 详情摘要不一致")

    def _validate_issue(self, issue: JiraIssue) -> None:
        if issue.project_key != self.profile.project_key or not issue.key.startswith(
            f"{self.profile.project_key}-"
        ):
            raise RuntimeErrorResult(
                code="jira_workspace_mismatch",
                message=(
                    f"Issue {issue.key} 属于项目 {issue.project_key}，"
                    f"但当前 Profile 绑定 {self.profile.project_key}"
                ),
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请切换到正确 Jira Connection 和 Project Profile",
            )
        if self.profile.issue_types and issue.issue_type not in self.profile.issue_types:
            raise RuntimeErrorResult(
                code="jira_issue_type_unsupported",
                message=f"当前 Profile 不支持 Jira 类型：{issue.issue_type}",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请核对任务类型；需要新增类型时先确认标准流程和 Profile",
            )

    def _validate_owner(self, issue: JiraIssue) -> None:
        current_user = self.client.current_user()
        if not issue.assignee or issue.assignee != current_user:
            raise RuntimeErrorResult(
                code="jira_assignee_mismatch",
                message="当前 Jira 用户不是任务经办人，禁止执行写入",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请由任务经办人执行，或先按 Jira 流程调整经办人",
            )


def _build_plan(
    operation: str,
    issue_key: str,
    agentic_run_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
    existing_external_id: str,
) -> WritePlan:
    if not RUN_ID_PATTERN.fullmatch(agentic_run_id):
        raise _input_error("invalid_agentic_run_id", "agentic_run_id 格式无效")
    _validate_idempotency_key(idempotency_key)
    canonical = json.dumps(
        {
            "operation": operation,
            "issue_key": issue_key,
            "agentic_run_id": agentic_run_id,
            "idempotency_key": idempotency_key,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()
    plan_id = f"plan-{content_hash[:24]}"
    return WritePlan(
        operation=operation,
        issue_key=issue_key,
        agentic_run_id=agentic_run_id,
        idempotency_key=idempotency_key,
        plan_id=plan_id,
        action="no_op" if existing_external_id else "create_or_update",
        content_sha256=content_hash,
        payload=payload,
        existing_external_id=existing_external_id,
    )


def _verify_plan(plan: WritePlan, expected_plan_id: str, operation: str) -> None:
    if plan.operation != operation or plan.plan_id != expected_plan_id:
        raise RuntimeErrorResult(
            code="jira_write_plan_mismatch",
            message="Jira 写入计划引用与当前输入不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新执行 plan，确认其内容后再 apply",
        )


def _marker(issue_key: str, agentic_run_id: str, idempotency_key: str) -> str:
    if not re.fullmatch(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$", issue_key):
        raise _input_error("invalid_issue_key", "issue_key 格式无效")
    if not RUN_ID_PATTERN.fullmatch(agentic_run_id):
        raise _input_error("invalid_agentic_run_id", "agentic_run_id 格式无效")
    _validate_idempotency_key(idempotency_key)
    return (
        "[agentic-ops-idempotency:"
        f"{issue_key}:{agentic_run_id}:{idempotency_key}]"
    )


def _plan_marker(plan: WritePlan) -> str:
    return _marker(plan.issue_key, plan.agentic_run_id, plan.idempotency_key)


def _has_exact_marker(item: Any, marker: str) -> bool:
    return marker in item.standalone_lines


def _rendered_markdown_text(markdown: str) -> str:
    return plain_text(markdown_to_adf(markdown))


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _require_comment_content(item: Any, expected_markdown: str) -> None:
    expected_body = _rendered_markdown_text(expected_markdown)
    if item.body != expected_body:
        raise _idempotency_conflict("Jira 评论")


def _require_worklog_content(item: Any, expected: dict[str, Any]) -> None:
    expected_body = _rendered_markdown_text(str(expected.get("markdown", "")))
    if (
        item.body != expected_body
        or item.time_spent_seconds != expected.get("time_spent_seconds")
        or _canonical_started(item.started) != _canonical_started(
            str(expected.get("started", ""))
        )
    ):
        raise _idempotency_conflict("Jira Worklog")


def _begin_create_attempt(
    plan: WritePlan,
    begin_create: Callable[[WritePlan], WriteAttempt] | None,
) -> WriteAttempt:
    if plan.action != "create_or_update" or plan.existing_external_id:
        raise _create_precondition_changed("Jira 写入")
    if begin_create is None:
        raise RuntimeErrorResult(
            code="jira_write_attempt_required",
            message="Jira create 缺少 Runtime 管理的写入尝试记录",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请通过 ao-work jira apply 执行，不得直接调用未留痕写入",
        )
    attempt = begin_create(plan)
    if not isinstance(attempt, WriteAttempt):
        raise RuntimeErrorResult(
            code="jira_write_attempt_invalid",
            message="Jira 写入尝试记录类型无效",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请停止写入并检查 Runtime 版本",
        )
    attempt.validate_integrity(plan)
    return attempt


def _readback_creation_status(
    plan: WritePlan, attempt: WriteAttempt | None
) -> bool:
    if plan.action == "no_op":
        if not plan.existing_external_id or attempt is not None:
            raise RuntimeErrorResult(
                code="jira_write_precondition_invalid",
                message="Jira no-op 计划的既有事实或写入尝试声明不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请重新 plan 并核对既有 Jira 记录",
            )
        return False
    if attempt is None:
        raise RuntimeErrorResult(
            code="jira_write_attempt_missing",
            message="Jira 回读缺少同一 create 尝试记录，不能证明由本运行创建",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请只使用原 apply 留下的尝试文件回读；不得补造 created=true",
        )
    attempt.validate_integrity(plan)
    return True


def _write_attempt_fields(
    plan: WritePlan, attempt: WriteAttempt | None
) -> dict[str, Any]:
    if attempt is None:
        return {
            "write_precondition": "preexisting",
            "write_attempt_id": None,
            "write_attempt_started_at": None,
        }
    attempt.validate_integrity(plan)
    return {
        "write_precondition": "absent",
        "write_attempt_id": attempt.attempt_id,
        "write_attempt_started_at": attempt.request_started_at,
    }


def _comment_readback(
    plan: WritePlan,
    item: Any,
    *,
    created: bool,
    attempt: WriteAttempt | None,
) -> dict[str, Any]:
    return {
        "issue_key": plan.issue_key,
        "agentic_run_id": plan.agentic_run_id,
        "idempotency_key": plan.idempotency_key,
        "plan_id": plan.plan_id,
        "content_sha256": plan.content_sha256,
        "external_id": item.comment_id,
        "body_sha256": _text_sha256(item.body),
        "created": created,
        **_write_attempt_fields(plan, attempt),
    }


def _worklog_readback(
    plan: WritePlan,
    item: Any,
    *,
    created: bool,
    attempt: WriteAttempt | None,
) -> dict[str, Any]:
    return {
        "issue_key": plan.issue_key,
        "agentic_run_id": plan.agentic_run_id,
        "idempotency_key": plan.idempotency_key,
        "plan_id": plan.plan_id,
        "content_sha256": plan.content_sha256,
        "external_id": item.worklog_id,
        "body_sha256": _text_sha256(item.body),
        "title": str(plan.payload["title"]),
        "details_sha256": str(plan.payload["details_sha256"]),
        "time_spent_seconds": item.time_spent_seconds,
        "started": item.started,
        "excludes_waiting": True,
        "included_work": [dict(entry) for entry in plan.payload["included_work"]],
        "excluded_waiting_categories": list(
            plan.payload["excluded_waiting_categories"]
        ),
        "created": created,
        **_write_attempt_fields(plan, attempt),
    }


def _transition_readback(
    plan: WritePlan, current_status: str, *, created: bool
) -> dict[str, Any]:
    return {
        "issue_key": plan.issue_key,
        "agentic_run_id": plan.agentic_run_id,
        "idempotency_key": plan.idempotency_key,
        "plan_id": plan.plan_id,
        "content_sha256": plan.content_sha256,
        "external_id": str(plan.payload["transition_id"]),
        "created": created,
        "current_status": current_status,
        "target_status": str(plan.payload["target_status"]),
        "status_matched": current_status == str(plan.payload["target_status"]),
        "agentic_next_action": "continue_from_verified_jira_transition",
    }


def _validate_included_work(
    value: Any, time_spent_seconds: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise _input_error(
            "worklog_included_work_invalid",
            "Worklog 必须逐项列出实际处理内容和秒数",
        )
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, dict) or set(entry) != {"description", "seconds"}:
            raise _input_error(
                "worklog_included_work_invalid",
                f"Worklog 耗时组成第 {index} 项字段无效",
            )
        description = entry.get("description")
        seconds = entry.get("seconds")
        if not isinstance(description, str):
            raise _input_error(
                "worklog_included_work_invalid",
                f"Worklog 耗时组成第 {index} 项说明无效",
            )
        _require_chinese(description, f"Worklog 耗时组成第 {index} 项说明")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1:
            raise _input_error(
                "worklog_included_work_invalid",
                f"Worklog 耗时组成第 {index} 项秒数必须是正整数",
            )
        normalized.append(
            {"description": description.strip(), "seconds": seconds}
        )
    if sum(entry["seconds"] for entry in normalized) != time_spent_seconds:
        raise _input_error(
            "worklog_duration_sum_mismatch",
            "Worklog 耗时组成秒数之和必须等于 time_spent_seconds",
        )
    return normalized


def _validate_excluded_waiting_categories(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _input_error(
            "worklog_waiting_categories_required",
            "Worklog 必须明确列出排除的等待类别",
        )
    normalized: list[str] = []
    for index, category in enumerate(value, start=1):
        if not isinstance(category, str):
            raise _input_error(
                "worklog_waiting_categories_required",
                f"Worklog 排除等待类别第 {index} 项无效",
            )
        _require_chinese(category, f"Worklog 排除等待类别第 {index} 项")
        normalized.append(category.strip())
    if len(set(normalized)) != len(normalized):
        raise _input_error(
            "worklog_waiting_categories_required",
            "Worklog 排除等待类别不能重复",
        )
    return normalized


def _worklog_markdown(
    title: str,
    details: str,
    included_work: list[dict[str, Any]],
    excluded_waiting_categories: list[str],
    marker: str,
) -> str:
    included_lines = "\n".join(
        f"- {entry['description']}：{entry['seconds']} 秒" for entry in included_work
    )
    excluded_lines = "\n".join(
        f"- {category}" for category in excluded_waiting_categories
    )
    return (
        f"## {title}\n\n{details}\n\n"
        f"### 计入耗时的处理\n\n{included_lines}\n\n"
        f"### 排除的等待类别\n\n{excluded_lines}\n\n{marker}\n"
    )


def _standalone_text_lines(value: str) -> set[str]:
    return {line.strip() for line in value.splitlines() if line.strip()}


def _contains_text(value: Any, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(
            _contains_text(key, secret) or _contains_text(item, secret)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_text(item, secret) for item in value)
    return False


def _validate_idempotency_key(value: str) -> None:
    if not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise _input_error("invalid_idempotency_key", "幂等键格式无效")


def _validate_comment_template(
    category: str,
    content: str,
    schema: dict[str, Any],
) -> None:
    """按 shared 评论模板 schema 校验 progress/evidence 评论必填键。

    schema 缺失时视为模板未启用（不阻断，兼容旧安装）；模板存在时
    必须覆盖全部必填键，缺失即阻断，防止评论漏掉 Agent 审计信息。
    键按「行首 - 键: 值」或「键: 值」形式匹配。
    """
    templates = schema.get("templates", {})
    spec = templates.get(category)
    if not isinstance(spec, dict):
        return
    required = spec.get("required_fields")
    field_keys = spec.get("field_keys")
    if not isinstance(required, list) or not isinstance(field_keys, dict):
        raise RuntimeErrorResult(
            code="comment_template_schema_invalid",
            message=f"评论模板 schema 的 {category} 定义无效",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复 shared/standards/jira-comment-template.schema.json",
        )
    missing = []
    for field_id in required:
        key_label = str(field_keys.get(field_id, field_id))
        if not _comment_has_field(content, key_label):
            missing.append(f"{field_id}({key_label})")
    if missing:
        raise RuntimeErrorResult(
            code="jira_comment_template_fields_missing",
            message=(
                f"{category} 评论缺少模板必填键：{', '.join(missing)}；"
                "评论必须按公共评论模板记录 Agent 审计信息"
            ),
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action=(
                "请按 shared/standards/jira-comment-template.schema.json "
                f"的 {category} 模板补齐必填键后重新 plan"
            ),
            details={"missing_fields": missing},
        )


def _comment_has_field(content: str, key_label: str) -> bool:
    """检查评论正文是否包含「键: 值」独立行（支持 - 列表前缀与中英文冒号）。"""
    pattern = re.compile(
        rf"^\s*(?:-\s+|\*\s+)?{re.escape(key_label)}\s*[:：]",
        re.MULTILINE,
    )
    return pattern.search(content) is not None


def _require_chinese(value: str, label: str) -> None:
    if not value.strip() or CHINESE_PATTERN.search(value) is None:
        raise _input_error("jira_visible_content_not_chinese", f"{label} 必须包含中文内容")


def _canonical_started(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _input_error("invalid_worklog_started", "Worklog started 必须是 ISO 8601 时间") from error
    if parsed.tzinfo is None:
        raise _input_error("invalid_worklog_started", "Worklog started 必须包含时区")
    normalized = parsed.astimezone(timezone.utc)
    milliseconds = normalized.microsecond // 1000
    return f"{normalized.strftime('%Y-%m-%dT%H:%M:%S')}.{milliseconds:03d}+0000"


def _ensure_no_duplicates(items: list[Any], code: str) -> None:
    if len(items) > 1:
        raise RuntimeErrorResult(
            code=code,
            message="Jira 中发现重复幂等标记，无法安全继续",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请人工核对重复记录并保留唯一事实后重试",
        )


def _ensure_single_readback(items: list[Any], code: str) -> None:
    _ensure_no_duplicates(items, code)
    if len(items) != 1:
        raise RuntimeErrorResult(
            code=code,
            message="Jira 写入结果无法通过回读确认",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请先回读 Jira 当前事实，不要直接重复 apply",
        )


def _create_precondition_changed(label: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_write_precondition_changed",
        message=f"{label} 的精确幂等标记在首次计划后已出现，当前调用不能证明创建归因",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请人工核对既有记录；不得把并发或预存记录标记为本运行创建",
    )


def _unknown_write(
    label: str, error: JiraTransportError, attempt: WriteAttempt
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_write_result_unknown",
        message=f"{label} 写入响应不明确",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请使用原计划及原 write_attempt_id 只执行 readback；不得重复 apply",
        details={
            "write_attempt_id": attempt.attempt_id,
            "write_attempt_started_at": attempt.request_started_at,
        },
    )


def _idempotency_conflict(label: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_idempotency_content_conflict",
        message=f"{label} 已存在同一幂等标记，但正文或关键字段与当前计划不一致",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请人工核对既有 Jira 记录与当前计划；不得复用冲突幂等键",
    )


def _input_error(code: str, message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action="请修正输入后重新执行 plan",
    )
