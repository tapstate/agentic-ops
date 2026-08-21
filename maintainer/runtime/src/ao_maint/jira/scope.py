from __future__ import annotations

import re
from typing import Any

from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

MAINTAINER_JIRA_PROJECT_KEY = "AO"

_ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
_PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


def validate_maintainer_issue_key(value: str) -> str:
    normalized = str(value).strip().upper()
    if not _ISSUE_KEY_PATTERN.fullmatch(normalized):
        raise _invalid_issue_key()
    project_key = normalized.partition("-")[0]
    validate_maintainer_project_key(project_key)
    return normalized


def validate_maintainer_project_key(value: str) -> str:
    normalized = str(value).strip().upper()
    if not _PROJECT_KEY_PATTERN.fullmatch(normalized):
        raise RuntimeErrorResult(
            code="invalid_project_key",
            message="project_key 必须是 Jira 项目 Key（大写字母开头）",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action="请提供合法的 Jira 项目 Key",
        )
    if normalized != MAINTAINER_JIRA_PROJECT_KEY:
        raise _scope_mismatch(normalized)
    return normalized


def validate_issue_readback(
    expected_issue_key: str,
    actual_issue_key: str,
    actual_project_key: str,
) -> str:
    expected = validate_maintainer_issue_key(expected_issue_key)
    actual = validate_maintainer_issue_key(actual_issue_key)
    project = validate_maintainer_project_key(actual_project_key)
    if actual != expected or actual.partition("-")[0] != project:
        raise _scope_mismatch(
            project,
            expected_issue_key=expected,
            actual_issue_key=actual,
        )
    return actual


def validate_write_plan_scope(plan: Any) -> None:
    operation = str(getattr(plan, "operation", ""))
    issue_key = str(getattr(plan, "issue_key", ""))
    payload = getattr(plan, "payload", None)
    if not isinstance(payload, dict):
        raise RuntimeErrorResult(
            code="jira_write_plan_invalid",
            message="Jira 写入计划缺少有效 payload",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请丢弃计划文件并重新执行 plan",
        )

    if operation == "jira_create":
        validate_maintainer_project_key(issue_key)
        validate_maintainer_project_key(str(payload.get("project_key", "")))
        parent = payload.get("parent", {})
        if not isinstance(parent, dict):
            raise RuntimeErrorResult(
                code="jira_write_plan_invalid",
                message="Jira 建卡计划 parent 结构无效",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请丢弃计划文件并重新执行 plan",
            )
        if parent:
            validate_maintainer_issue_key(str(parent.get("key", "")))
            validate_maintainer_project_key(str(parent.get("project_key", "")))
            relation = payload.get("parent_relation", {})
            if not isinstance(relation, dict):
                raise RuntimeErrorResult(
                    code="jira_write_plan_invalid",
                    message="Jira 建卡计划 parent_relation 结构无效",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    required_human_action="请丢弃计划文件并重新执行 plan",
                )
            validate_maintainer_issue_key(
                str(relation.get("requested_parent_key", ""))
            )
            validate_maintainer_issue_key(
                str(relation.get("effective_parent_key", ""))
            )
        existing_external_id = str(
            getattr(plan, "existing_external_id", "") or ""
        )
        if existing_external_id:
            validate_maintainer_issue_key(existing_external_id)
        return

    validate_maintainer_issue_key(issue_key)
    project_key = payload.get("project_key")
    if project_key is not None:
        validate_maintainer_project_key(str(project_key))


def _invalid_issue_key() -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code="invalid_issue_key",
        message="Jira issue key 格式无效",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action="请提供形如 AO-123 的维护任务编号",
    )


def _scope_mismatch(
    actual_project_key: str,
    *,
    expected_issue_key: str = "",
    actual_issue_key: str = "",
) -> RuntimeErrorResult:
    details = {
        "expected_project_key": MAINTAINER_JIRA_PROJECT_KEY,
        "actual_project_key": actual_project_key,
    }
    if expected_issue_key:
        details["expected_issue_key"] = expected_issue_key
    if actual_issue_key:
        details["actual_issue_key"] = actual_issue_key
    return RuntimeErrorResult(
        code="maintainer_jira_project_scope_mismatch",
        message=(
            "maintainer Runtime 只能处理 AO 项目维护任务，"
            f"当前项目为 {actual_project_key or '<empty>'}"
        ),
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action=(
            "AO 维护任务请使用 ao-maint；TAP 等业务项目任务必须在对应 "
            "developer 工作空间使用 ao-work"
        ),
        details=details,
    )
