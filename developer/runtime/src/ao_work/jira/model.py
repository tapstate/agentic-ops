from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class JiraComment:
    comment_id: str
    body: str
    author: str = ""
    created: str = ""
    standalone_lines: frozenset[str] = field(default_factory=frozenset)
    body_supported: bool = True


@dataclass(frozen=True)
class JiraWorklog:
    worklog_id: str
    body: str
    time_spent_seconds: int
    started: str
    standalone_lines: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class JiraIssue:
    issue_id: str
    key: str
    project_key: str
    summary: str
    status: str
    issue_type: str
    assignee: str
    description: dict[str, Any] | None
    # Jira 的状态显示名可被本地化或管理员重命名；ID 才是同一站点内的
    # 稳定身份。保留名称是为了人读输出和受控的旧 Profile 兼容。
    status_id: str = ""
    status_category: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    priority: str = ""
    updated: str = ""


def plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (plain_text(item) for item in value)))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        return plain_text(value.get("content", []))
    return ""


def standalone_paragraph_lines(value: Any) -> frozenset[str]:
    """Return exact top-level paragraph lines, excluding quotes and code blocks."""
    if isinstance(value, str):
        return frozenset(line.strip() for line in value.splitlines() if line.strip())
    if not isinstance(value, dict) or value.get("type") != "doc":
        return frozenset()
    content = value.get("content")
    if not isinstance(content, list):
        return frozenset()
    lines: set[str] = set()
    for node in content:
        if not isinstance(node, dict) or node.get("type") != "paragraph":
            continue
        line = plain_text(node).strip()
        if line:
            lines.add(line)
    return frozenset(lines)


def user_identifier(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value or "")
    for key in ("accountId", "emailAddress", "displayName", "name"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def object_name(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate
    return str(value or "") if isinstance(value, str) else ""
