from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from agentic_ops.config.model import ProjectProfile
from agentic_ops.jira.adf import extract_description_section, merge_description_sections
from agentic_ops.jira.client import JiraClient, JiraTransportError
from agentic_ops.jira.model import JiraIssue
from agentic_ops.output import EXIT_BLOCKED, EXIT_CAPABILITY_GAP, RuntimeErrorResult

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")


@dataclass(frozen=True)
class WritePlan:
    operation: str
    issue_key: str
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
        return cls(
            operation=str(payload["operation"]),
            issue_key=str(payload["issue_key"]),
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

    def plan_comment(
        self,
        issue_key: str,
        idempotency_key: str,
        category: str,
        content: str,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        self._validate_owner(issue)
        _require_chinese(content, "Jira 评论")
        if category not in {"analysis", "plan", "decision", "evidence", "blocked"}:
            raise _input_error("invalid_comment_category", "评论分类无效")
        marker = _marker(idempotency_key)
        existing = [comment for comment in self.client.comments(issue.key) if marker in comment.body]
        _ensure_no_duplicates(existing, "jira_comment_duplicate")
        markdown = f"{content.rstrip()}\n\n{marker}\n"
        return _build_plan(
            "jira_comment",
            issue.key,
            idempotency_key,
            {"category": category, "markdown": markdown},
            existing[0].comment_id if existing else "",
        )

    def apply_comment(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        plan.validate_integrity()
        _verify_plan(plan, expected_plan_id, "jira_comment")
        self._validate_owner(self.inspect_issue(plan.issue_key))
        marker = _marker(plan.idempotency_key)
        existing = [comment for comment in self.client.comments(plan.issue_key) if marker in comment.body]
        _ensure_no_duplicates(existing, "jira_comment_duplicate")
        if existing:
            return {"external_id": existing[0].comment_id, "created": False}
        try:
            self.client.add_comment(plan.issue_key, str(plan.payload["markdown"]))
        except JiraTransportError as error:
            raise _unknown_write("Jira 评论", error) from error
        readback = [comment for comment in self.client.comments(plan.issue_key) if marker in comment.body]
        _ensure_single_readback(readback, "jira_comment_readback_failed")
        return {"external_id": readback[0].comment_id, "created": True}

    def readback_comment(self, issue_key: str, idempotency_key: str) -> dict[str, Any]:
        marker = _marker(idempotency_key)
        found = [comment for comment in self.client.comments(issue_key) if marker in comment.body]
        _ensure_single_readback(found, "jira_comment_readback_failed")
        return {"external_id": found[0].comment_id, "body": found[0].body}

    def plan_worklog(
        self,
        issue_key: str,
        idempotency_key: str,
        title: str,
        details: str,
        time_spent_seconds: int,
        started: str,
        excludes_waiting: bool,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        self._validate_owner(issue)
        _require_chinese(title, "Worklog 标题")
        _require_chinese(details, "Worklog 内容")
        if time_spent_seconds <= 0:
            raise _input_error("invalid_worklog_duration", "Worklog 耗时必须大于 0")
        _validate_started(started)
        if not excludes_waiting:
            raise _input_error(
                "worklog_waiting_exclusion_required",
                "必须明确确认 Worklog 不包含等待时间",
            )
        marker = _marker(idempotency_key)
        existing = [worklog for worklog in self.client.worklogs(issue.key) if marker in worklog.body]
        _ensure_no_duplicates(existing, "jira_worklog_duplicate")
        markdown = f"## {title.strip()}\n\n{details.rstrip()}\n\n{marker}\n"
        return _build_plan(
            "jira_worklog",
            issue.key,
            idempotency_key,
            {
                "title": title.strip(),
                "markdown": markdown,
                "time_spent_seconds": time_spent_seconds,
                "started": started,
                "excludes_waiting": True,
            },
            existing[0].worklog_id if existing else "",
        )

    def apply_worklog(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        plan.validate_integrity()
        _verify_plan(plan, expected_plan_id, "jira_worklog")
        self._validate_owner(self.inspect_issue(plan.issue_key))
        marker = _marker(plan.idempotency_key)
        existing = [worklog for worklog in self.client.worklogs(plan.issue_key) if marker in worklog.body]
        _ensure_no_duplicates(existing, "jira_worklog_duplicate")
        if existing:
            return {"external_id": existing[0].worklog_id, "created": False}
        try:
            self.client.add_worklog(
                plan.issue_key,
                time_spent_seconds=int(plan.payload["time_spent_seconds"]),
                started=str(plan.payload["started"]),
                markdown=str(plan.payload["markdown"]),
            )
        except JiraTransportError as error:
            raise _unknown_write("Jira Worklog", error) from error
        readback = [worklog for worklog in self.client.worklogs(plan.issue_key) if marker in worklog.body]
        _ensure_single_readback(readback, "jira_worklog_readback_failed")
        return {"external_id": readback[0].worklog_id, "created": True}

    def readback_worklog(self, issue_key: str, idempotency_key: str) -> dict[str, Any]:
        marker = _marker(idempotency_key)
        found = [worklog for worklog in self.client.worklogs(issue_key) if marker in worklog.body]
        _ensure_single_readback(found, "jira_worklog_readback_failed")
        return {
            "external_id": found[0].worklog_id,
            "time_spent_seconds": found[0].time_spent_seconds,
            "started": found[0].started,
        }

    def plan_description(
        self,
        issue_key: str,
        idempotency_key: str,
        sections: dict[str, str],
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        self._validate_owner(issue)
        if not sections:
            raise _input_error("empty_description_sections", "Description 受管章节不能为空")
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
            idempotency_key,
            {"sections": sections, "description": merged},
            "description" if unchanged else "",
        )

    def apply_description(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        plan.validate_integrity()
        _verify_plan(plan, expected_plan_id, "jira_description")
        issue = self.inspect_issue(plan.issue_key)
        self._validate_owner(issue)
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
    idempotency_key: str,
    payload: dict[str, Any],
    existing_external_id: str,
) -> WritePlan:
    _validate_idempotency_key(idempotency_key)
    canonical = json.dumps(
        {
            "operation": operation,
            "issue_key": issue_key,
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


def _marker(idempotency_key: str) -> str:
    _validate_idempotency_key(idempotency_key)
    return f"[agentic-ops-idempotency:{idempotency_key}]"


def _validate_idempotency_key(value: str) -> None:
    if not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise _input_error("invalid_idempotency_key", "幂等键格式无效")


def _require_chinese(value: str, label: str) -> None:
    if not value.strip() or CHINESE_PATTERN.search(value) is None:
        raise _input_error("jira_visible_content_not_chinese", f"{label} 必须包含中文内容")


def _validate_started(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _input_error("invalid_worklog_started", "Worklog started 必须是 ISO 8601 时间") from error
    if parsed.tzinfo is None:
        raise _input_error("invalid_worklog_started", "Worklog started 必须包含时区")


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


def _unknown_write(label: str, error: JiraTransportError) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_write_result_unknown",
        message=f"{label} 写入响应不明确",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请先执行 readback；只有确认不存在对应幂等记录后才能重新 plan",
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
