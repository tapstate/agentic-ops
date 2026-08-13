from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class JiraComment:
    comment_id: str
    body: str
    author: str = ""
    created: str = ""


@dataclass(frozen=True)
class JiraWorklog:
    worklog_id: str
    body: str
    time_spent_seconds: int
    started: str


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
    fields: dict[str, Any] = field(default_factory=dict)


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
