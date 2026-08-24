from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from ao_maint.jira.adf import markdown_to_adf
from ao_maint.jira.config import select_maintainer_workflow
from ao_maint.jira.client import JiraClient, JiraTransportError
from ao_maint.jira.model import (
    JiraIssue,
    object_name,
    plain_text,
    standalone_paragraph_lines,
    user_identifier,
)
from ao_maint.jira.scope import (
    validate_issue_readback,
    validate_maintainer_issue_key,
    validate_maintainer_project_key,
    validate_write_plan_scope,
)
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")

ALLOWED_COMMENT_CATEGORIES = frozenset(
    {"analysis", "plan", "decision", "evidence", "blocked", "progress"}
)

TEMPLATE_CATEGORIES = frozenset({"progress", "evidence"})

COMMENT_TEMPLATE_SCHEMA_RELATIVE = (
    "shared/standards/jira-comment-template.schema.json"
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
        normalized = validate_maintainer_issue_key(issue_key)
        issue = self.client.get_issue(normalized)
        validate_issue_readback(normalized, issue.key, issue.project_key)
        return issue

    def plan_create_issue(
        self,
        project_key: str,
        idempotency_key: str,
        *,
        maintainer_run_id: str,
        issuetype_name: str,
        summary: str,
        description: str,
        assignee: str | None = None,
        parent_key: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> WritePlan:
        """计划一次 Jira 建卡。

        - 通过 createmeta 校验事务类型与必填字段（禁止 AI 临场猜测字段）。
        - 幂等锚点：description 写入 idempotency marker，JQL 复查项目内是否
          已存在相同 marker 的任务。
        - 新任务 issue_key 未知，plan 的 issue_key 字段使用 project_key 占位，
          apply 返回真实 issue key，readback 使用真实 key。
        """
        normalized_project = validate_maintainer_project_key(project_key)
        normalized_parent = (
            validate_maintainer_issue_key(parent_key) if parent_key else None
        )
        _require_chinese(summary, "任务摘要")
        if not issuetype_name.strip():
            raise _input_error("invalid_issuetype_name", "事务类型名称不能为空")
        supplied_fields = set(extra_fields or {})
        if "parent" in supplied_fields:
            raise RuntimeErrorResult(
                code="jira_create_parent_requires_typed_argument",
                message="父任务必须通过 --parent 提供，禁止使用通用 --field 猜测结构",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请删除 --field parent=... 并改用 --parent <ISSUE-KEY>",
            )
        parent, relation = self._resolve_create_parent(
            normalized_project, normalized_parent
        )
        meta = self._select_create_meta(
            normalized_project, issuetype_name.strip(), has_parent=bool(parent)
        )
        if parent:
            supplied_fields.add("parent")
        missing_required = [
            field_id
            for field_id in meta["required"]
            if field_id
            not in {
                "project",
                "issuetype",
                "summary",
                "reporter",
                "description",
            }
            and field_id not in supplied_fields
        ]
        if missing_required:
            names = ", ".join(
                f"{field_id}({meta['required'][field_id]})"
                for field_id in sorted(missing_required)
            )
            raise RuntimeErrorResult(
                code="jira_create_required_fields_missing",
                message=(
                    f"Jira 事务类型「{issuetype_name.strip()}」缺少必填字段："
                    f"{names}；禁止猜测字段默认值"
                ),
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action=(
                    "请通过 --field 提供缺失必填字段，或核对事务类型与项目"
                ),
                details={
                    "project_key": normalized_project,
                    "issuetype_name": issuetype_name.strip(),
                    "required_fields": meta["required"],
                },
            )
        normalized_description = description.rstrip()
        if relation.get("uplifted"):
            normalized_description = _append_related_issue(
                normalized_description, str(relation["requested_parent_key"])
            )
        marker = _create_marker(
            normalized_project, maintainer_run_id, idempotency_key
        )
        markdown = (
            f"{normalized_description}\n\n{marker}\n"
            if normalized_description
            else f"{marker}\n"
        )
        normalized_fields = _validate_extra_fields(extra_fields or {}, meta)
        if parent:
            normalized_fields["parent"] = {"key": parent["key"]}
        normalized_assignee = (assignee or "").strip()
        if normalized_assignee:
            if not re.fullmatch(r"[A-Za-z0-9:_-]{3,200}", normalized_assignee):
                raise _input_error(
                    "invalid_assignee", "assignee 必须是 Jira accountId"
                )
        payload = {
            "project_key": normalized_project,
            "issuetype_name": str(meta["issuetype_name"]),
            "issuetype_id": meta["issuetype_id"],
            "summary": summary.strip(),
            "description": normalized_description,
            "markdown": markdown,
            "body_sha256": _text_sha256(_rendered_markdown_text(markdown)),
            "assignee": normalized_assignee,
            "parent": parent,
            "parent_relation": relation,
            "fields": normalized_fields,
            "marker": marker,
            "required_fields": sorted(meta["required"]),
        }
        existing = self._find_existing_by_marker(
            normalized_project, marker, "jira_create_duplicate"
        )
        return _build_plan(
            "jira_create",
            normalized_project,
            maintainer_run_id,
            idempotency_key,
            payload,
            existing.key if existing else "",
        )

    def _select_create_meta(
        self, project_key: str, requested_issuetype: str, *, has_parent: bool
    ) -> dict[str, Any]:
        if not has_parent:
            return self.client.create_meta(project_key, requested_issuetype)
        candidates = [
            item
            for item in self.client.create_metas(project_key)
            if item.get("is_subtask") and "parent" in item.get("fields", {})
        ]
        exact = [
            item for item in candidates if item.get("issuetype_name") == requested_issuetype
        ]
        if exact:
            return exact[0]
        preferred = [item for item in candidates if item.get("issuetype_name") == "子任务"]
        if len(preferred) == 1:
            return preferred[0]
        if len(candidates) == 1:
            return candidates[0]
        raise RuntimeErrorResult(
            code="jira_create_subtask_type_ambiguous",
            message="当前项目存在多个可用子任务类型，无法自动选择",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请通过 --issuetype 明确提供 createmeta 中的子任务类型名称",
            details={"subtask_issuetypes": [item["issuetype_name"] for item in candidates]},
        )

    def _resolve_create_parent(
        self,
        project_key: str,
        parent_key: str | None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        normalized_parent = (
            validate_maintainer_issue_key(parent_key) if parent_key else ""
        )
        if not normalized_parent:
            return {}, {}
        parent = self.inspect_issue(normalized_parent)
        if parent.project_key != project_key:
            raise RuntimeErrorResult(
                code="jira_create_parent_project_mismatch",
                message="父任务与待创建任务不属于同一 Jira 项目",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请提供当前项目中的有效父任务",
            )
        effective_parent = parent
        uplifted = False
        raw_type = parent.fields.get("issuetype", {})
        if isinstance(raw_type, dict) and raw_type.get("subtask") is True:
            raw_parent = parent.fields.get("parent", {})
            effective_key = (
                str(raw_parent.get("key", "")).strip()
                if isinstance(raw_parent, dict)
                else ""
            )
            if not effective_key:
                raise RuntimeErrorResult(
                    code="jira_create_parent_hierarchy_invalid",
                    message="被关联的 Jira 子任务缺少可用父任务，无法建立新的子任务",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请在 Jira 修复该子任务的父级关系后重新执行",
                )
            effective_parent = self.inspect_issue(effective_key)
            if effective_parent.project_key != project_key:
                raise RuntimeErrorResult(
                    code="jira_create_parent_project_mismatch",
                    message="被关联子任务的实际父任务不属于当前 Jira 项目",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请核对 Jira 父子层级关系",
                )
            effective_type = effective_parent.fields.get("issuetype", {})
            if isinstance(effective_type, dict) and effective_type.get("subtask") is True:
                raise RuntimeErrorResult(
                    code="jira_create_parent_hierarchy_invalid",
                    message="Jira 父子层级异常：子任务的实际父任务仍是子任务",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请在 Jira 修复层级后重新执行",
                )
            uplifted = True
        resolved = {
            "key": effective_parent.key,
            "issue_id": effective_parent.issue_id,
            "project_key": effective_parent.project_key,
            "issue_type": effective_parent.issue_type,
        }
        return resolved, {
            "requested_parent_key": normalized_parent,
            "effective_parent_key": effective_parent.key,
            "uplifted": uplifted,
            "reason": "requested_parent_is_subtask" if uplifted else "direct_parent",
        }

    def apply_create_issue(
        self, plan: WritePlan, expected_plan_id: str
    ) -> dict[str, Any]:
        self._validate_apply_plan(plan, expected_plan_id, "jira_create")
        self._validate_create_plan(plan)
        project_key = str(plan.payload["project_key"])
        marker = str(plan.payload["marker"])
        existing = self._find_existing_by_marker(
            project_key, marker, "jira_create_duplicate"
        )
        if existing:
            _require_create_content(existing, plan)
            if plan.action == "create_or_update":
                raise _create_precondition_changed("Jira 任务")
            return _create_readback(plan, existing.key, created=False)
        fields = _create_fields(plan)
        try:
            created = self.client.create_issue(fields)
        except JiraTransportError as error:
            raise _unknown_write("Jira 任务", error) from error
        issue_key = created["key"]
        readback = self.inspect_issue(issue_key)
        _require_create_content(readback, plan)
        return _create_readback(plan, issue_key, created=True)

    def readback_create_issue(
        self, plan: WritePlan, issue_key: str
    ) -> dict[str, Any]:
        self._validate_apply_plan(plan, plan.plan_id, "jira_create")
        self._validate_create_plan(plan)
        readback = self.inspect_issue(issue_key)
        _require_create_content(readback, plan)
        return _create_readback(plan, issue_key, created=True)

    def _validate_create_plan(self, plan: WritePlan) -> None:
        expected = {
            "project_key",
            "issuetype_name",
            "issuetype_id",
            "summary",
            "description",
            "markdown",
            "body_sha256",
            "assignee",
            "parent",
            "parent_relation",
            "fields",
            "marker",
            "required_fields",
        }
        if set(plan.payload) != expected:
            raise _input_error("jira_write_plan_invalid", "Jira 建卡计划字段无效")
        project_key = plan.payload.get("project_key")
        issuetype_name = plan.payload.get("issuetype_name")
        summary = plan.payload.get("summary")
        markdown = plan.payload.get("markdown")
        body_sha256 = plan.payload.get("body_sha256")
        assignee = plan.payload.get("assignee")
        marker = plan.payload.get("marker")
        fields = plan.payload.get("fields")
        parent = plan.payload.get("parent")
        relation = plan.payload.get("parent_relation")
        if (
            not isinstance(project_key, str)
            or not isinstance(issuetype_name, str)
            or not issuetype_name.strip()
            or not isinstance(summary, str)
            or not summary.strip()
            or not isinstance(markdown, str)
            or not _is_sha256(body_sha256)
            or not isinstance(assignee, str)
            or not isinstance(marker, str)
            or not isinstance(fields, dict)
            or not isinstance(parent, dict)
            or not isinstance(relation, dict)
        ):
            raise _input_error("jira_write_plan_invalid", "Jira 建卡计划内容无效")
        _require_chinese(summary, "任务摘要")
        if marker != _create_marker(
            project_key, plan.maintainer_run_id, plan.idempotency_key
        ):
            raise _input_error("jira_write_plan_invalid", "Jira 建卡幂等标记无效")
        expected_markdown = (
            f"{str(plan.payload.get('description', '')).rstrip()}\n\n{marker}\n"
            if str(plan.payload.get("description", "")).strip()
            else f"{marker}\n"
        )
        if markdown != expected_markdown:
            raise _input_error("jira_write_plan_invalid", "Jira 建卡正文或幂等标记无效")
        if body_sha256 != _text_sha256(_rendered_markdown_text(markdown)):
            raise _input_error("jira_write_plan_invalid", "Jira 建卡正文摘要不一致")
        if not isinstance(plan.payload.get("issuetype_id"), str) or not isinstance(
            plan.payload.get("required_fields"), list
        ):
            raise _input_error("jira_write_plan_invalid", "Jira 建卡元数据无效")
        meta = self._select_create_meta(project_key, issuetype_name, has_parent=bool(parent))
        if str(meta.get("issuetype_id", "")) != str(plan.payload["issuetype_id"]):
            raise RuntimeErrorResult(
                code="jira_create_meta_changed",
                message="Jira 事务类型已变化，计划与当前 createmeta 不一致；请重新 plan",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action="请重新执行 plan 对齐 Jira 当前事实后再 apply",
            )
        if parent:
            expected_parent_keys = {"key", "issue_id", "project_key", "issue_type"}
            if set(parent) != expected_parent_keys or fields.get("parent") != {
                "key": parent.get("key")
            }:
                raise _input_error("jira_write_plan_invalid", "Jira 父任务计划字段无效")
            expected_relation_keys = {
                "requested_parent_key",
                "effective_parent_key",
                "uplifted",
                "reason",
            }
            if (
                set(relation) != expected_relation_keys
                or not isinstance(relation.get("uplifted"), bool)
                or relation.get("reason")
                not in {"direct_parent", "requested_parent_is_subtask"}
                or bool(relation.get("uplifted"))
                != (relation.get("reason") == "requested_parent_is_subtask")
                or str(relation.get("effective_parent_key", "")) != str(parent.get("key", ""))
            ):
                raise _input_error("jira_write_plan_invalid", "Jira 父任务关联计划字段无效")
            requested_parent = str(relation.get("requested_parent_key", ""))
            current_parent, current_relation = self._resolve_create_parent(project_key, requested_parent)
            if current_parent != parent:
                raise RuntimeErrorResult(
                    code="jira_create_parent_changed",
                    message="Jira 父任务事实已变化，原建卡计划失效",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请重新执行 plan 对齐父任务当前事实后再 apply",
                )
            if current_relation != relation:
                raise RuntimeErrorResult(
                    code="jira_create_parent_changed",
                    message="Jira 父任务层级事实已变化，原建卡计划失效",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请重新执行 plan 对齐父任务当前事实后再 apply",
                )
        elif relation:
            raise _input_error("jira_write_plan_invalid", "无父任务的 Jira 建卡计划不能包含父级关联")
        missing_required = [
            field_id
            for field_id in meta["required"]
            if field_id
            not in {
                "project",
                "issuetype",
                "summary",
                "reporter",
                "description",
            }
            and field_id not in fields
        ]
        if missing_required:
            raise RuntimeErrorResult(
                code="jira_create_required_fields_missing",
                message="Jira 建卡计划缺少当前必填字段；请重新 plan",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action="请重新执行 plan 补齐必填字段",
                details={"missing": missing_required},
            )
        # plan 时已归一化字段值；apply 只需确认字段键仍被 createmeta 声明
        declared = set(meta["fields"])
        unknown = [key for key in fields if key not in declared]
        if unknown:
            raise RuntimeErrorResult(
                code="jira_create_meta_changed",
                message="Jira 建卡计划包含当前 createmeta 未声明的字段；请重新 plan",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action="请重新执行 plan 对齐 Jira 当前事实后再 apply",
                details={"unknown_fields": unknown},
            )

    def _find_existing_by_marker(
        self, project_key: str, marker: str, duplicate_code: str
    ) -> JiraIssue | None:
        normalized_project = validate_maintainer_project_key(project_key)
        # JQL 全文搜索对 []: 等特殊字符分词不可靠，搜索稳定词后再本地精确匹配 marker
        jql = (
            f'project = "{_escape_jql(normalized_project)}" '
            'AND description ~ "agentic-ops-maintainer-idempotency" '
            "ORDER BY created ASC"
        )
        matches: list[JiraIssue] = []
        for item in self.client.search_issues(
            jql, fields=["summary", "description", "project", "key"]
        ):
            if not isinstance(item, dict):
                continue
            raw_fields = item.get("fields", {}) if isinstance(item, dict) else {}
            description = raw_fields.get("description")
            standalone = standalone_paragraph_lines(description)
            if marker not in standalone:
                continue
            issue_key = validate_maintainer_issue_key(str(item.get("key", "")))
            raw_project = raw_fields.get("project", {})
            actual_project = (
                str(raw_project.get("key", ""))
                if isinstance(raw_project, dict)
                else ""
            )
            validate_issue_readback(issue_key, issue_key, actual_project)
            matches.append(
                JiraIssue(
                    issue_id=str(item.get("id", "")),
                    key=issue_key,
                    project_key=normalized_project,
                    summary=str(raw_fields.get("summary", "")),
                    status=object_name(raw_fields.get("status")),
                    issue_type=object_name(raw_fields.get("issuetype")),
                    assignee=user_identifier(raw_fields.get("assignee")),
                    description=description if isinstance(description, dict) else None,
                    fields=raw_fields,
                )
            )
        _ensure_no_duplicates(matches, duplicate_code)
        return matches[0] if matches else None

    def plan_description(
        self,
        issue_key: str,
        idempotency_key: str,
        content: str,
        *,
        maintainer_run_id: str,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        _require_chinese(content, "Jira 任务描述")
        normalized = content.rstrip()
        markdown = f"{normalized}\n"
        target_adf = markdown_to_adf(markdown)
        unchanged = _description_semantic_sha256(issue.description) == _description_semantic_sha256(
            target_adf
        )
        return _build_plan(
            "jira_description",
            issue.key,
            maintainer_run_id,
            idempotency_key,
            {
                "markdown": markdown,
                "body_sha256": _text_sha256(_rendered_markdown_text(markdown)),
                "expected_previous_description_sha256": _description_sha256(
                    issue.description
                ),
                "target_description_sha256": _description_semantic_sha256(target_adf),
            },
            "description" if unchanged else "",
        )

    def apply_description(
        self, plan: WritePlan, expected_plan_id: str
    ) -> dict[str, Any]:
        self._validate_apply_plan(plan, expected_plan_id, "jira_description")
        self._validate_description_plan(plan)
        issue = self.inspect_issue(plan.issue_key)
        markdown = str(plan.payload["markdown"])
        expected_previous = str(plan.payload["expected_previous_description_sha256"])
        target = str(plan.payload["target_description_sha256"])
        current = _description_sha256(issue.description)
        if current != expected_previous:
            raise RuntimeErrorResult(
                code="jira_description_precondition_changed",
                message="Jira 任务描述在计划确认后已发生变化",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请重新回读完整 Description、生成新计划并重新确认",
            )
        if _description_semantic_sha256(issue.description) == target:
            return {"external_id": "description", "created": False}
        try:
            self.client.update_description(
                plan.issue_key, markdown_to_adf(markdown)
            )
        except JiraTransportError as error:
            raise _unknown_write("Jira 任务描述", error) from error
        readback = self.inspect_issue(plan.issue_key)
        if _description_semantic_sha256(readback.description) != target:
            raise RuntimeErrorResult(
                code="jira_description_readback_failed",
                message="Jira 任务描述写入后回读不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请人工核对任务描述，不要重复写入",
            )
        return {"external_id": "description", "created": True}

    def readback_description(self, plan: WritePlan) -> dict[str, Any]:
        self._validate_apply_plan(plan, plan.plan_id, "jira_description")
        self._validate_description_plan(plan)
        issue = self.inspect_issue(plan.issue_key)
        matched = (
            _description_semantic_sha256(issue.description)
            == str(plan.payload["target_description_sha256"])
        )
        return {
            "external_id": "description",
            "created": matched,
            "agentic_next_action": "continue_from_verified_jira_description"
            if matched
            else "review_jira_description_readback",
        }

    def plan_summary(
        self,
        issue_key: str,
        idempotency_key: str,
        summary: str,
        *,
        maintainer_run_id: str,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        _require_chinese(summary, "Jira 任务标题")
        target = summary.strip()
        return _build_plan(
            "jira_summary",
            issue.key,
            maintainer_run_id,
            idempotency_key,
            {
                "summary": target,
                "expected_previous_summary_sha256": _text_sha256(issue.summary),
                "target_summary_sha256": _text_sha256(target),
            },
            "summary" if issue.summary == target else "",
        )

    def apply_summary(self, plan: WritePlan, expected_plan_id: str) -> dict[str, Any]:
        self._validate_apply_plan(plan, expected_plan_id, "jira_summary")
        self._validate_summary_plan(plan)
        issue = self.inspect_issue(plan.issue_key)
        expected_previous = str(plan.payload["expected_previous_summary_sha256"])
        target = str(plan.payload["summary"])
        if _text_sha256(issue.summary) != expected_previous:
            raise RuntimeErrorResult(
                code="jira_summary_precondition_changed",
                message="Jira 任务标题在计划确认后已发生变化",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请重新回读任务标题、生成新计划并重新确认",
            )
        if issue.summary == target:
            return {"external_id": "summary", "created": False}
        try:
            self.client.update_summary(plan.issue_key, target)
        except JiraTransportError as error:
            raise _unknown_write("Jira 任务标题", error) from error
        readback = self.inspect_issue(plan.issue_key)
        if readback.summary != target:
            raise RuntimeErrorResult(
                code="jira_summary_readback_failed",
                message="Jira 任务标题写入后回读不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=False,
                required_human_action="请人工核对任务标题，不要重复写入",
            )
        return {"external_id": "summary", "created": True}

    def readback_summary(self, plan: WritePlan) -> dict[str, Any]:
        self._validate_apply_plan(plan, plan.plan_id, "jira_summary")
        self._validate_summary_plan(plan)
        matched = self.inspect_issue(plan.issue_key).summary == str(plan.payload["summary"])
        return {
            "external_id": "summary",
            "created": matched,
            "agentic_next_action": "continue_from_verified_jira_summary"
            if matched
            else "review_jira_summary_readback",
        }

    def plan_comment(
        self,
        issue_key: str,
        idempotency_key: str,
        category: str,
        content: str,
        *,
        maintainer_run_id: str,
        comment_template_schema: dict[str, Any] | None = None,
    ) -> WritePlan:
        issue = self.inspect_issue(issue_key)
        _require_chinese(content, "Jira 评论")
        if category not in ALLOWED_COMMENT_CATEGORIES:
            raise _input_error("invalid_comment_category", "评论分类无效")
        if category in TEMPLATE_CATEGORIES:
            _validate_comment_template(
                category,
                content,
                comment_template_schema or {},
            )
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
        self.inspect_issue(plan.issue_key)
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
        self.inspect_issue(plan.issue_key)
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
        self.inspect_issue(plan.issue_key)
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
        self.inspect_issue(plan.issue_key)
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
        project_workflow = select_maintainer_workflow(
            workflow, issue.project_key, issue.issue_type
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
                    issue.issue_type,
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
            "issue_type": issue.issue_type,
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
        self._require_transition_issue_binding(plan, issue, remote_write_completed=False)
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
                    str(plan.payload.get("issue_type", "")),
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
                    str(plan.payload.get("issue_type", "")),
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
        self._require_transition_issue_binding(plan, readback, remote_write_completed=True)
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
        self._require_transition_issue_binding(plan, issue, remote_write_completed=False)
        matched = issue.status == str(plan.payload["target_status"])
        return _transition_readback(plan, issue.status, created=matched)

    def _validate_transition_plan(self, plan: WritePlan) -> None:
        expected = {
            "project_key",
            "issue_type",
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
        issue_type = plan.payload.get("issue_type")
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
            or not isinstance(issue_type, str)
            or not issue_type.strip()
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

    @staticmethod
    def _require_transition_issue_binding(
        plan: WritePlan,
        issue: JiraIssue,
        *,
        remote_write_completed: bool,
    ) -> None:
        planned_project_key = str(plan.payload["project_key"])
        planned_issue_type = str(plan.payload["issue_type"])
        if (
            issue.project_key == planned_project_key
            and issue.issue_type == planned_issue_type
        ):
            return
        if remote_write_completed:
            code = "jira_transition_readback_mismatch"
            message = (
                "状态流转后回读的 Jira 项目或任务类型与计划不一致；"
                "远端写入结果不明确，禁止盲目重试"
            )
            required_human_action = (
                "请人工核对 Jira 当前项目、任务类型和状态，不要重复执行流转"
            )
        else:
            code = "jira_transition_mapping_gap"
            message = (
                "Jira 当前项目或任务类型与计划时不一致，工作流映射前置条件已变化；"
                "禁止执行原计划，请重新 plan"
            )
            required_human_action = (
                "请重新执行 plan，对齐 Jira 当前项目和任务类型后再 apply"
            )
        raise RuntimeErrorResult(
            code=code,
            message=message,
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=not remote_write_completed,
            required_human_action=required_human_action,
            details={
                "issue_key": plan.issue_key,
                "planned_project_key": planned_project_key,
                "current_project_key": issue.project_key,
                "planned_issue_type": planned_issue_type,
                "current_issue_type": issue.issue_type,
                "current_status": issue.status,
                "remote_write_completed": remote_write_completed,
            },
        )

    def _validate_apply_plan(
        self, plan: WritePlan, expected_plan_id: str, operation: str
    ) -> None:
        plan.validate_integrity()
        validate_write_plan_scope(plan)
        _verify_plan(plan, expected_plan_id, operation)

    @staticmethod
    def validate_no_credentials(plan: WritePlan, email: str, token: str) -> None:
        plan.validate_integrity()
        validate_write_plan_scope(plan)
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

    def _validate_description_plan(self, plan: WritePlan) -> None:
        expected = {
            "markdown",
            "body_sha256",
            "expected_previous_description_sha256",
            "target_description_sha256",
        }
        if set(plan.payload) != expected:
            raise _input_error("jira_write_plan_invalid", "Jira 任务描述计划字段无效")
        markdown = plan.payload.get("markdown")
        body_sha256 = plan.payload.get("body_sha256")
        expected_previous = plan.payload.get("expected_previous_description_sha256")
        target = plan.payload.get("target_description_sha256")
        if (
            not isinstance(markdown, str)
            or not markdown.strip()
            or not _is_sha256(body_sha256)
            or not _is_sha256(expected_previous)
            or not _is_sha256(target)
        ):
            raise _input_error("jira_write_plan_invalid", "Jira 任务描述计划内容无效")
        _require_chinese(markdown, "Jira 任务描述")
        if body_sha256 != _text_sha256(_rendered_markdown_text(markdown)):
            raise _input_error("jira_write_plan_invalid", "Jira 任务描述正文摘要不一致")
        if target != _description_semantic_sha256(markdown_to_adf(markdown)):
            raise _input_error("jira_write_plan_invalid", "Jira 任务描述目标摘要不一致")

    def _validate_summary_plan(self, plan: WritePlan) -> None:
        expected = {
            "summary",
            "expected_previous_summary_sha256",
            "target_summary_sha256",
        }
        if set(plan.payload) != expected:
            raise _input_error("jira_write_plan_invalid", "Jira 任务标题计划字段无效")
        summary = plan.payload.get("summary")
        expected_previous = plan.payload.get("expected_previous_summary_sha256")
        target = plan.payload.get("target_summary_sha256")
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or not _is_sha256(expected_previous)
            or not _is_sha256(target)
        ):
            raise _input_error("jira_write_plan_invalid", "Jira 任务标题计划内容无效")
        _require_chinese(summary, "Jira 任务标题")
        if target != _text_sha256(summary):
            raise _input_error("jira_write_plan_invalid", "Jira 任务标题目标摘要不一致")

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


def _plain_text_of_markdown(markdown: str) -> str:
    """markdown → ADF → plain text，用于描述幂等/回读的语义比较。"""
    return plain_text(markdown_to_adf(markdown))


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
    issue_key = validate_maintainer_issue_key(issue_key)
    if not RUN_ID_PATTERN.fullmatch(maintainer_run_id):
        raise _input_error("invalid_maintainer_run_id", "maintainer_run_id 格式无效")
    _validate_idempotency_key(idempotency_key)
    return (
        "[agentic-ops-maintainer-idempotency:"
        f"{issue_key}:{maintainer_run_id}:{idempotency_key}]"
    )


def _plan_marker(plan: WritePlan) -> str:
    return _marker(plan.issue_key, plan.maintainer_run_id, plan.idempotency_key)


def _create_marker(
    project_key: str, maintainer_run_id: str, idempotency_key: str
) -> str:
    project_key = validate_maintainer_project_key(project_key)
    if not RUN_ID_PATTERN.fullmatch(maintainer_run_id):
        raise _input_error("invalid_maintainer_run_id", "maintainer_run_id 格式无效")
    _validate_idempotency_key(idempotency_key)
    return (
        "[agentic-ops-maintainer-idempotency:create:"
        f"{project_key}:{maintainer_run_id}:{idempotency_key}]"
    )

def _escape_jql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _create_fields(plan: WritePlan) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "project": {"key": str(plan.payload["project_key"])},
        "issuetype": {"name": str(plan.payload["issuetype_name"])},
        "summary": str(plan.payload["summary"]),
        "description": markdown_to_adf(str(plan.payload["markdown"])),
    }
    assignee = str(plan.payload.get("assignee", "")).strip()
    if assignee:
        fields["assignee"] = {"accountId": assignee}
    for key, value in plan.payload.get("fields", {}).items():
        fields[key] = value
    return fields


def _append_related_issue(description: str, issue_key: str) -> str:
    relation = f"关联任务：{issue_key}"
    if relation in description.splitlines():
        return description
    return f"{description}\n\n{relation}" if description else relation


def _validate_extra_fields(
    extra_fields: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    """校验并归一化建卡自定义字段。

    - 只允许 createmeta 中声明过的字段（禁止猜测字段 id）。
    - option/array 类型字段只接受字符串，自动转 Jira 选项结构。
    - user 类型字段接受 accountId 字符串。
    - 其余类型直接透传，但必须是非 dict 标量。
    """
    if not isinstance(extra_fields, dict):
        raise _input_error("invalid_extra_fields", "额外字段必须是映射")
    normalized: dict[str, Any] = {}
    schemas = meta.get("fields", {})
    for key, value in extra_fields.items():
        if not isinstance(key, str) or not key.strip():
            raise _input_error("invalid_extra_fields", "额外字段名必须是字符串")
        schema = schemas.get(key)
        if schema is None:
            raise RuntimeErrorResult(
                code="jira_create_unknown_field",
                message=f"额外字段 {key} 不在 createmeta 声明中，禁止猜测字段",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请核对字段 id 或改用 createmeta 声明的字段",
                details={"field_id": key, "declared_fields": sorted(schemas)},
            )
        field_type = schema.get("schema", {}).get("type", "") if isinstance(
            schema.get("schema"), dict
        ) else ""
        if field_type in {"option", "array"}:
            if not isinstance(value, str) or not value.strip():
                raise _input_error(
                    "invalid_extra_fields",
                    f"字段 {key}（{field_type}）需要字符串选项值",
                )
            option = {"value": value.strip()}
            normalized[key] = (
                [option] if field_type == "array" else option
            )
        elif field_type == "user":
            if not isinstance(value, str) or not value.strip():
                raise _input_error(
                    "invalid_extra_fields", f"字段 {key}（user）需要 accountId"
                )
            normalized[key] = {"accountId": value.strip()}
        elif field_type in {"string", "number", "date", "datetime", "textarea"}:
            if isinstance(value, dict) or isinstance(value, list):
                raise _input_error(
                    "invalid_extra_fields",
                    f"字段 {key}（{field_type}）不接受对象或数组值",
                )
            normalized[key] = value
        else:
            raise RuntimeErrorResult(
                code="jira_create_unsupported_field",
                message=f"字段 {key} 的 schema 类型 {field_type!r} 暂不支持自动归一化",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请确认该字段取值结构后决定是否纳入建卡能力",
                details={"field_id": key, "schema_type": field_type},
            )
    return normalized


def _require_create_content(item: Any, plan: WritePlan) -> None:
    marker = str(plan.payload["marker"])
    expected_summary = str(plan.payload["summary"])
    standalone = standalone_paragraph_lines(item.description)
    if marker not in standalone:
        raise _idempotency_conflict("Jira 任务")
    if item.summary != expected_summary:
        raise _idempotency_conflict("Jira 任务")
    expected_parent = plan.payload.get("parent", {})
    if expected_parent:
        raw_parent = item.fields.get("parent", {})
        actual_parent_key = (
            str(raw_parent.get("key", "")) if isinstance(raw_parent, dict) else ""
        )
        if actual_parent_key != str(expected_parent.get("key", "")):
            raise RuntimeErrorResult(
                code="jira_create_parent_readback_mismatch",
                message="Jira 子任务父级回读与建卡计划不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请人工核对 Jira 父子关系，不要重复创建任务",
                details={
                    "expected_parent_key": str(expected_parent.get("key", "")),
                    "actual_parent_key": actual_parent_key,
                },
            )


def _create_readback(
    plan: WritePlan, issue_key: str, *, created: bool
) -> dict[str, Any]:
    return {
        "external_id": issue_key,
        "created": created,
        "issue_key": issue_key,
        "plan_id": plan.plan_id,
        "agentic_next_action": "continue_from_verified_jira_create",
    }


def _has_exact_marker(item: Any, marker: str) -> bool:
    return marker in item.standalone_lines


def _rendered_markdown_text(markdown: str) -> str:
    return plain_text(markdown_to_adf(markdown))


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _description_sha256(value: dict[str, Any] | None) -> str:
    """摘要原始 Jira ADF，作为写前事实绑定，不忽略任何字段。"""
    return _json_sha256(value)


def _description_semantic_sha256(value: dict[str, Any] | None) -> str:
    """摘要 Description 语义，忽略 Jira 生成的 non-semantic localId。"""
    return _json_sha256(_normalize_description_adf(value))


def _normalize_description_adf(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize_description_adf(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if key == "attrs" and isinstance(item, dict):
            semantic_attrs = {
                attr_key: _normalize_description_adf(attr_value)
                for attr_key, attr_value in item.items()
                if attr_key != "localId"
            }
            if semantic_attrs:
                normalized[key] = semantic_attrs
        else:
            normalized[key] = _normalize_description_adf(item)
    return normalized


def _json_sha256(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    if error.http_status == 400:
        diagnostics = dict(error.diagnostics)
        messages = diagnostics.get("error_messages", [])
        field_errors = diagnostics.get("field_errors", {})
        hints: list[str] = []
        if isinstance(messages, list):
            hints.extend(str(item) for item in messages)
        if isinstance(field_errors, dict):
            hints.extend(f"{field}：{message}" for field, message in field_errors.items())
        suffix = f"：{'；'.join(hints)}" if hints else ""
        return RuntimeErrorResult(
            code="jira_write_rejected",
            message=f"{label}被 Jira 拒绝（HTTP 400）{suffix}",
            status="failed",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action="请修正列出的字段后重新生成建卡计划；不要复用原计划直接重试",
            details={"http_status": 400, **diagnostics},
        )
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
    issue_type: str,
    current_status: str,
    available: list[dict[str, str]],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    """快速适配路径：输出可直接照抄的对照材料，适配发生在配置层。"""
    return {
        "issue_key": issue_key,
        "project_key": project_key,
        "issue_type": issue_type,
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
