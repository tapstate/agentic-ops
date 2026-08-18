from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ao_maint.jira.adf import markdown_to_adf
from ao_maint.jira.client import JiraClient, JiraTransportError
from ao_maint.jira.model import JiraIssue, plain_text
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")

ALLOWED_COMMENT_CATEGORIES = frozenset(
    {"analysis", "plan", "decision", "evidence", "blocked", "progress"}
)


@dataclass(frozen=True)
class WritePlan:
    operation: str
    issue_key: str
    maintainer_run_id: str
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
            "maintainer_run_id",
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
            maintainer_run_id=str(payload["maintainer_run_id"]),
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
            self.maintainer_run_id,
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


class MaintainerJiraService:
    def __init__(self, client: JiraClient) -> None:
        self.client = client

    def inspect_issue(self, issue_key: str) -> JiraIssue:
        issue = self.client.get_issue(issue_key)
        return issue

    def plan_comment(
        self,
        issue_key: str,
        idempotency_key: str,
        category: str,
        content: str,
        *,
        maintainer_run_id: str,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        _require_chinese(content, "Jira 评论")
        if category not in ALLOWED_COMMENT_CATEGORIES:
            raise _input_error("invalid_comment_category", "评论分类无效")
        marker = _marker(issue.key, maintainer_run_id, idempotency_key)
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
            maintainer_run_id,
            idempotency_key,
            {
                "category": category,
                "content": normalized_content,
                "markdown": markdown,
                "body_sha256": _text_sha256(_rendered_markdown_text(markdown)),
            },
            existing[0].comment_id if existing else "",
        )

    def apply_comment(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        self._validate_apply_plan(plan, expected_plan_id, "jira_comment")
        self._validate_comment_plan(plan)
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
            return _comment_readback(plan, existing[0], created=False)
        try:
            self.client.add_comment(plan.issue_key, str(plan.payload["markdown"]))
        except JiraTransportError as error:
            raise _unknown_write("Jira 评论", error) from error
        readback = [
            comment
            for comment in self.client.comments(plan.issue_key)
            if _has_exact_marker(comment, marker)
        ]
        _ensure_single_readback(readback, "jira_comment_readback_failed")
        _require_comment_content(readback[0], str(plan.payload["markdown"]))
        return _comment_readback(plan, readback[0], created=True)

    def readback_comment(self, plan: WritePlan) -> dict[str, Any]:
        self._validate_apply_plan(plan, plan.plan_id, "jira_comment")
        self._validate_comment_plan(plan)
        marker = _plan_marker(plan)
        found = [
            comment
            for comment in self.client.comments(plan.issue_key)
            if _has_exact_marker(comment, marker)
        ]
        _ensure_single_readback(found, "jira_comment_readback_failed")
        _require_comment_content(found[0], str(plan.payload["markdown"]))
        return _comment_readback(plan, found[0], created=plan.action != "no_op")

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
        maintainer_run_id: str,
        included_work: list[dict[str, Any]],
        excluded_waiting_categories: list[str],
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
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
        marker = _marker(issue.key, maintainer_run_id, idempotency_key)
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
            maintainer_run_id,
            idempotency_key,
            expected_worklog,
            existing[0].worklog_id if existing else "",
        )

    def apply_worklog(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        self._validate_apply_plan(plan, expected_plan_id, "jira_worklog")
        self._validate_worklog_plan(plan)
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
            return _worklog_readback(plan, existing[0], created=False)
        try:
            self.client.add_worklog(
                plan.issue_key,
                time_spent_seconds=int(plan.payload["time_spent_seconds"]),
                started=str(plan.payload["started"]),
                markdown=str(plan.payload["markdown"]),
            )
        except JiraTransportError as error:
            raise _unknown_write("Jira Worklog", error) from error
        readback = [
            worklog
            for worklog in self.client.worklogs(plan.issue_key)
            if _has_exact_marker(worklog, marker)
        ]
        _ensure_single_readback(readback, "jira_worklog_readback_failed")
        _require_worklog_content(readback[0], plan.payload)
        return _worklog_readback(plan, readback[0], created=True)

    def readback_worklog(self, plan: WritePlan) -> dict[str, Any]:
        self._validate_apply_plan(plan, plan.plan_id, "jira_worklog")
        self._validate_worklog_plan(plan)
        marker = _plan_marker(plan)
        found = [
            worklog
            for worklog in self.client.worklogs(plan.issue_key)
            if _has_exact_marker(worklog, marker)
        ]
        _ensure_single_readback(found, "jira_worklog_readback_failed")
        _require_worklog_content(found[0], plan.payload)
        return _worklog_readback(plan, found[0], created=plan.action != "no_op")

    def plan_transition(
        self,
        issue_key: str,
        idempotency_key: str,
        *,
        maintainer_run_id: str,
        workflow: dict[str, Any],
        target_status: str | None = None,
        target_transition: str | None = None,
        transition_id: str | None = None,
        comment: str | None = None,
    ) -> WritePlan:
        """计划一次 Jira 状态流转（D-037 严格匹配，禁止模糊猜测）。

        - 目标来源三选一：--target-transition（映射 key）、--target-status（目标状态名）、
          --transition-id（无映射时的显式精确路径）。
        - 匹配失败输出适配对照材料（当前状态 + Jira 可用 transitions + 已配置映射）。
        - 幂等锚点：目标状态达成即视为已执行（transition 无稳定外部 ID）。
        """
        provided = [
            value is not None
            for value in (target_status, target_transition, transition_id)
        ]
        if sum(provided) != 1:
            raise _input_error(
                "invalid_transition_target",
                "目标必须且只能指定一个：--target-status / --target-transition / --transition-id",
            )
        issue = self.inspect_issue(issue_key)
        project_workflow = (
            workflow.get("projects", {}).get(issue.project_key, {})
            if isinstance(workflow, dict)
            else {}
        )
        available = self.client.available_transitions(issue.key)
        matched = _match_transition(
            issue.status,
            available,
            project_workflow,
            target_status=target_status,
            target_key=target_transition,
            transition_id=transition_id,
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
                    "按 details 对照材料补齐工作流映射配置后重新 plan，"
                    "或改用 --transition-id 显式精确流转"
                ),
                details=_adaptation_material(
                    issue.key,
                    issue.project_key,
                    issue.status,
                    available,
                    project_workflow,
                ),
            )
        matched_id, matched_name, matched_status = matched
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
            maintainer_run_id,
            idempotency_key,
            payload,
            "",
        )

    def apply_transition(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        self._validate_apply_plan(plan, expected_plan_id, "jira_transition")
        self._validate_transition_plan(plan)
        target_status = str(plan.payload["target_status"])
        from_status = str(plan.payload["from_status"])
        transition_id = str(plan.payload["transition_id"])
        comment = str(plan.payload.get("comment", ""))
        issue = self.inspect_issue(plan.issue_key)
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
                details=_adaptation_material(
                    plan.issue_key,
                    str(plan.payload.get("project_key", "")),
                    issue.status,
                    self.client.available_transitions(plan.issue_key),
                    {},
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
                details=_adaptation_material(
                    plan.issue_key,
                    str(plan.payload.get("project_key", "")),
                    issue.status,
                    available,
                    {},
                ),
            )
        try:
            self.client.execute_transition(
                plan.issue_key,
                transition_id,
                markdown=comment or None,
            )
        except JiraTransportError as error:
            raise _unknown_write("Jira 状态流转", error) from error
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
        self._validate_apply_plan(plan, plan.plan_id, "jira_transition")
        self._validate_transition_plan(plan)
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

    def _validate_apply_plan(
        self, plan: WritePlan, expected_plan_id: str, operation: str
    ) -> None:
        plan.validate_integrity()
        _verify_plan(plan, expected_plan_id, operation)

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

    def _validate_comment_plan(self, plan: WritePlan) -> None:
        if set(plan.payload) != {"category", "content", "markdown", "body_sha256"}:
            raise _input_error("jira_write_plan_invalid", "Jira 评论计划字段无效")
        category = plan.payload.get("category")
        content = plan.payload.get("content")
        markdown = plan.payload.get("markdown")
        body_sha256 = plan.payload.get("body_sha256")
        if category not in ALLOWED_COMMENT_CATEGORIES or not isinstance(
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

    def _validate_worklog_plan(self, plan: WritePlan) -> None:
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


def _contains_text(value: Any, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, list):
        return any(_contains_text(item, secret) for item in value)
    if isinstance(value, dict):
        return any(_contains_text(item, secret) for item in value.values())
    return False


def _build_plan(
    operation: str,
    issue_key: str,
    maintainer_run_id: str,
    idempotency_key: str,
    payload: dict[str, Any],
    existing_external_id: str,
) -> WritePlan:
    if not RUN_ID_PATTERN.fullmatch(maintainer_run_id):
        raise _input_error("invalid_maintainer_run_id", "maintainer_run_id 格式无效")
    _validate_idempotency_key(idempotency_key)
    canonical = json.dumps(
        {
            "operation": operation,
            "issue_key": issue_key,
            "maintainer_run_id": maintainer_run_id,
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
        maintainer_run_id=maintainer_run_id,
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


def _marker(issue_key: str, maintainer_run_id: str, idempotency_key: str) -> str:
    if not ISSUE_KEY_PATTERN.fullmatch(issue_key):
        raise _input_error("invalid_issue_key", "issue_key 格式无效")
    if not RUN_ID_PATTERN.fullmatch(maintainer_run_id):
        raise _input_error("invalid_maintainer_run_id", "maintainer_run_id 格式无效")
    _validate_idempotency_key(idempotency_key)
    return (
        "[agentic-ops-maintainer-idempotency:"
        f"{issue_key}:{maintainer_run_id}:{idempotency_key}]"
    )


def _plan_marker(plan: WritePlan) -> str:
    return _marker(plan.issue_key, plan.maintainer_run_id, plan.idempotency_key)


def _has_exact_marker(item: Any, marker: str) -> bool:
    return marker in item.standalone_lines


def _rendered_markdown_text(markdown: str) -> str:
    return plain_text(markdown_to_adf(markdown))


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _standalone_text_lines(value: str) -> frozenset[str]:
    return frozenset(line.strip() for line in value.splitlines() if line.strip())


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


def _comment_readback(
    plan: WritePlan, item: Any, *, created: bool
) -> dict[str, Any]:
    return {
        "external_id": item.comment_id,
        "created": created,
        "issue_key": plan.issue_key,
        "plan_id": plan.plan_id,
        "agentic_next_action": "continue_from_verified_jira_comment",
    }


def _worklog_readback(
    plan: WritePlan, item: Any, *, created: bool
) -> dict[str, Any]:
    return {
        "external_id": item.worklog_id,
        "created": created,
        "issue_key": plan.issue_key,
        "plan_id": plan.plan_id,
        "agentic_next_action": "continue_from_verified_jira_worklog",
    }


def _ensure_no_duplicates(items: list[Any], code: str) -> None:
    if len(items) > 1:
        raise RuntimeErrorResult(
            code=code,
            message="Jira 上存在多个相同的幂等标记记录，禁止继续写入",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请人工核对 Jira 重复记录后清理，再重新执行",
        )


def _ensure_single_readback(items: list[Any], code: str) -> None:
    if len(items) != 1:
        raise RuntimeErrorResult(
            code=code,
            message="Jira 写后回读未找到唯一匹配记录",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=False,
            required_human_action="请人工核对 Jira 实际状态，不要盲目重试写入",
        )


def _create_precondition_changed(label: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_create_precondition_changed",
        message=f"{label} 已在 Jira 上存在但计划标记为新建",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action="请重新 plan 以对齐 Jira 当前事实，不要重复写入",
    )


def _unknown_write(label: str, error: JiraTransportError) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_write_result_unknown",
        message=f"{label} 写入结果不明确（{type(error).__name__}）",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action=f"请先回读 Jira {label}，结果确认后再继续",
    )


def _idempotency_conflict(label: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="jira_idempotency_conflict",
        message=f"Jira 上已存在相同幂等标记但内容不一致",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action=f"请人工核对 Jira 既有{label}内容，不要覆盖其它记录",
    )


def _validate_idempotency_key(value: str) -> None:
    if not IDEMPOTENCY_PATTERN.fullmatch(value):
        raise _input_error("invalid_idempotency_key", "idempotency_key 格式无效")


def _require_chinese(value: str, label: str) -> None:
    if not CHINESE_PATTERN.search(value):
        raise _input_error(
            "chinese_content_required",
            f"{label}必须包含中文，不能只写英文或代码",
        )


def _canonical_started(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _input_error(
            "invalid_worklog_started", "Worklog 开始时间必须是 ISO 8601"
        ) from error
    if parsed.tzinfo is None:
        raise _input_error(
            "invalid_worklog_started", "Worklog 开始时间必须包含时区"
        )
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_included_work(
    value: Any, total_seconds: int
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise _input_error("invalid_worklog_included_work", "耗时组成不能为空")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise _input_error("invalid_worklog_included_work", "耗时组成必须是对象数组")
        description = raw.get("description")
        seconds = raw.get("seconds")
        if (
            not isinstance(description, str)
            or not description.strip()
            or not isinstance(seconds, int)
            or isinstance(seconds, bool)
            or seconds <= 0
        ):
            raise _input_error(
                "invalid_worklog_included_work", "耗时组成缺少有效描述或秒数"
            )
        key = f"{description.strip()}:{seconds}"
        if key in seen:
            raise _input_error(
                "invalid_worklog_included_work", "耗时组成存在重复条目"
            )
        seen.add(key)
        normalized.append({"description": description.strip(), "seconds": seconds})
    if sum(int(item["seconds"]) for item in normalized) != total_seconds:
        raise _input_error(
            "invalid_worklog_included_work",
            "耗时组成秒数之和必须等于真实总耗时",
        )
    return normalized


def _validate_excluded_waiting_categories(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _input_error(
            "invalid_worklog_excluded_waiting", "排除等待类别不能为空"
        )
    normalized: list[str] = []
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            raise _input_error(
                "invalid_worklog_excluded_waiting", "排除等待类别必须是字符串"
            )
        if raw.strip() not in normalized:
            normalized.append(raw.strip())
    return normalized


def _worklog_markdown(
    title: str,
    details: str,
    included_work: list[dict[str, Any]],
    excluded_waiting_categories: list[str],
    marker: str,
) -> str:
    lines = [f"# {title}", "", details, ""]
    lines.append("## 耗时组成")
    for item in included_work:
        lines.append(f"- {item['description']}: {item['seconds']} 秒")
    lines.append("")
    lines.append("## 排除等待")
    for category in excluded_waiting_categories:
        lines.append(f"- {category}")
    lines.append("")
    lines.append(marker)
    return "\n".join(lines)


def _input_error(code: str, message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action="请修正输入后重新执行",
    )


def _from_ok(spec: dict[str, Any], current_status: str) -> bool:
    from_states = spec.get("from")
    if not isinstance(from_states, list) or not from_states:
        return True
    # 已达成目标状态也视为可计划（幂等 no_op 场景）
    if current_status == str(spec.get("to", "")).strip():
        return True
    return current_status in from_states


def _to_ok(spec: dict[str, Any], to_status: str) -> bool:
    expected = str(spec.get("to", "")).strip()
    if not expected:
        return True
    return to_status == expected


def _match_transition(
    current_status: str,
    available: list[dict[str, str]],
    mapping: dict[str, Any],
    *,
    target_status: str | None = None,
    target_key: str | None = None,
    transition_id: str | None = None,
) -> tuple[str, str, str] | None:
    """D-037 严格匹配：稳定 ID 优先，名称兜底需唯一且 from/to 匹配，禁止模糊匹配。

    返回 (transition_id, transition_name, to_status)；任何歧义、目标不符、
    当前不可用都返回 None（调用方阻断）。配置了 id 的候选若可用但 from/to
    不匹配，立即返回 None，不降级到名称兜底。
    """
    if transition_id is not None:
        exact = [item for item in available if item["id"] == transition_id]
        if len(exact) == 1:
            return exact[0]["id"], exact[0]["name"], exact[0]["to"]
        return None
    entries = mapping.get("transitions", {}) if isinstance(mapping, dict) else {}
    candidates: list[dict[str, Any]] = []
    if target_key is not None:
        spec = entries.get(target_key)
        if isinstance(spec, dict):
            candidates.append(spec)
    elif target_status is not None:
        for spec in entries.values():
            if (
                isinstance(spec, dict)
                and str(spec.get("to", "")).strip() == target_status
            ):
                candidates.append(spec)
    if not candidates:
        return None
    resolved: list[tuple[str, str, str]] = []
    for spec in candidates:
        spec_id = str(spec.get("id", "")).strip()
        spec_name = str(spec.get("name", "")).strip()
        if spec_id:
            found = [item for item in available if item["id"] == spec_id]
            if not found:
                continue
            item = found[0]
            if _from_ok(spec, current_status) and _to_ok(spec, item["to"]):
                resolved.append((item["id"], item["name"], item["to"]))
            else:
                return None
        elif spec_name:
            same = [item for item in available if item["name"] == spec_name]
            if (
                len(same) == 1
                and _from_ok(spec, current_status)
                and _to_ok(spec, same[0]["to"])
            ):
                resolved.append((same[0]["id"], same[0]["name"], same[0]["to"]))
    unique = set(resolved)
    if len(unique) == 1:
        return next(iter(unique))
    return None


def _adaptation_material(
    issue_key: str,
    project_key: str,
    current_status: str,
    available: list[dict[str, str]],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    """快速适配路径：输出可直接照抄的对照材料，适配发生在配置层。"""
    return {
        "issue_key": issue_key,
        "project_key": project_key,
        "current_status": current_status,
        "available_transitions": available,
        "configured_transitions": (
            mapping.get("transitions", {}) if isinstance(mapping, dict) else {}
        ),
        "configured_statuses": (
            mapping.get("statuses", {}) if isinstance(mapping, dict) else {}
        ),
        "guidance": (
            "请按对照材料在 maintainer/standards/connections/"
            "<connection_id>-workflow.yaml 补齐映射后重新 plan；"
            "或使用 --transition-id 显式精确流转"
        ),
    }


def _transition_readback(
    plan: WritePlan, current_status: str, *, created: bool
) -> dict[str, Any]:
    return {
        "external_id": "",
        "created": created,
        "issue_key": plan.issue_key,
        "plan_id": plan.plan_id,
        "current_status": current_status,
        "target_status": str(plan.payload["target_status"]),
        "status_matched": current_status == str(plan.payload["target_status"]),
        "agentic_next_action": "continue_from_verified_jira_transition",
    }
