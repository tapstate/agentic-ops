from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

FIELD_STATES: Final = frozenset(
    {"active", "read_only", "pending_validation", "unsupported", "deprecated"}
)


@dataclass(frozen=True)
class JiraConnection:
    connection_id: str
    base_url: str
    email_env: str
    token_env: str
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class FieldMapping:
    logical_name: str
    source: str
    jira_field: str | None = None
    section: str | None = None
    state: str = "active"
    writable: bool = False
    required: bool = False


@dataclass(frozen=True)
class ProjectProfile:
    profile_id: str
    connection_id: str
    project_key: str
    task_query: str
    issue_types: tuple[str, ...] = ()
    fields: dict[str, FieldMapping] = field(default_factory=dict)
    status_mapping: dict[str, str] = field(default_factory=dict)
    transition_mapping: dict[str, dict[str, str]] = field(default_factory=dict)
    default_repository: str | None = None

    def requested_jira_fields(self) -> list[str]:
        requested = {
            "summary",
            "description",
            "status",
            "issuetype",
            "assignee",
            "comment",
            "labels",
            "components",
            "project",
        }
        for mapping in self.fields.values():
            if mapping.jira_field and mapping.state in {"active", "read_only"}:
                requested.add(mapping.jira_field)
        return sorted(requested)

    def active_custom_field_ids(self) -> set[str]:
        return {
            mapping.jira_field
            for mapping in self.fields.values()
            if mapping.jira_field
            and mapping.jira_field.startswith("customfield_")
            and mapping.state in {"active", "read_only"}
        }


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value
