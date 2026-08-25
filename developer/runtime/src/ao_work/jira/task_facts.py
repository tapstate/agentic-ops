from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from ao_work.config.model import ProjectProfile
from ao_work.jira.model import JiraComment, JiraIssue, plain_text
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult


SCHEMA_VERSION = 1
MAX_FACT_VALUE_LENGTH = 1_000
_SENSITIVE_VALUE = re.compile(
    r"(?i)\b(authorization|token|password|secret|api[_-]?key|cookie|set-cookie)"
    r"\s*([:=])\s*[^\s,;]+"
)
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^\s/@:]+:[^\s/@]+@")
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|password|secret|api[_-]?key)=)[^&#\s]+"
)
_SQL = re.compile(r"(?i)\b(?:select|insert|update|delete|merge)\b[\s\S]*\b(?:from|into|set)\b")
_STACK_FRAME = re.compile(r"^\s*at\s+[\w.$]+\([^\n]*:\d+\)\s*$")
_COMMENT_PROPERTY = re.compile(r"^\s*([^:：#]{1,64})\s*[:：]\s*(.+?)\s*$")
_CORE_FACT_SECTIONS: dict[str, tuple[str, ...]] = {
    "task_goal": ("目标", "任务目标", "需求目标"),
    "problem_version": ("问题版本",),
    "exception_summary": ("问题现象", "异常摘要", "异常现象", "问题摘要"),
    "acceptance_criteria": ("验收标准", "验收线索"),
}
_REPOSITORY_HINT_SECTIONS = frozenset({"仓库分支", "候选仓库", "候选分支", "候选仓库/分支"})


def read_task_facts(
    issue: JiraIssue,
    comments: Iterable[JiraComment],
    profile: ProjectProfile,
) -> dict[str, Any]:
    """提取任务执行需要的最小 Jira 事实，绝不回传原始 Description 或评论正文。"""

    if issue.description is not None and not _supported_document(issue.description):
        raise _blocked(
            "jira_task_description_unsupported",
            "Jira Description 包含当前 Runtime 不支持的内容格式",
            "请将任务必要信息写入普通段落或标题章节后重试",
        )
    comment_list = list(comments)
    unsupported_comment = next(
        (item for item in comment_list if not getattr(item, "body_supported", True)),
        None,
    )
    if unsupported_comment is not None:
        raise _blocked(
            "jira_task_comment_unsupported",
            "Jira 评论包含当前 Runtime 不支持的内容格式",
            "请将必要任务线索补充为普通文本评论后重试",
        )

    description_sections = _sections(issue.description)
    expected_sections = _known_sections(profile)
    description_facts = _core_facts(
        description_sections,
        source="jira_description_section",
    )
    description_facts.extend(_mapped_facts(
        description_sections,
        profile,
        source="jira_description_section",
        existing_fields={str(item["field"]) for item in description_facts},
    ))
    comment_facts: list[dict[str, Any]] = []
    for comment in comment_list:
        comment_sections = _comment_sections(
            comment.body, expected_sections=expected_sections
        )
        items = _core_facts(comment_sections, source="jira_comment")
        items.extend(_mapped_facts(
            comment_sections,
            profile,
            source="jira_comment",
            existing_fields={str(item["field"]) for item in items},
        ))
        for item in items:
            comment_facts.append(
                {
                    **item,
                    "comment_id": comment.comment_id,
                    "author": _safe_metadata(comment.author),
                    "created": _safe_metadata(comment.created),
                }
            )

    repository_hints = _repository_hints(
        description_sections,
        comment_list,
        set(profile.repository_candidates()),
    )
    safe_sections = _selected_safe_sections(description_sections, profile)
    return {
        "schema_version": SCHEMA_VERSION,
        "redaction_applied": True,
        "description": {
            "status": "available" if issue.description is not None else "missing",
            "facts": description_facts,
            "sections": safe_sections,
        },
        "comments": {
            "status": "available",
            "comment_count": len(comment_list),
            "facts": comment_facts,
        },
        "repository_branch_hints": repository_hints,
    }


def description_sections_from_facts(task_facts: Mapping[str, Any]) -> dict[str, str]:
    description = task_facts.get("description")
    if not isinstance(description, Mapping):
        return {}
    sections = description.get("sections")
    if not isinstance(sections, Mapping):
        return {}
    return {
        str(title): str(value)
        for title, value in sections.items()
        if isinstance(title, str) and isinstance(value, str) and value.strip()
    }


def _supported_document(value: Any) -> bool:
    return isinstance(value, dict) and value.get("type") == "doc" and isinstance(
        value.get("content"), list
    )


def _sections(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not _supported_document(value):
        return {}
    result: dict[str, list[str]] = {}
    current = ""
    for node in value["content"]:
        if not isinstance(node, dict):
            continue
        text = plain_text(node).strip()
        if node.get("type") == "heading":
            current = text
            if current:
                result.setdefault(current, [])
            continue
        if current and text:
            result[current].append(text)
        elif text:
            result.setdefault("__overview__", []).append(text)
    return {
        title: "\n".join(lines).strip()
        for title, lines in result.items()
        if "\n".join(lines).strip()
    }


def _comment_sections(
    value: str, *, expected_sections: set[str] | None = None
) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            current = line.lstrip("#").strip().rstrip("：:").strip()
            if current:
                sections.setdefault(current, [])
            continue
        matched = _COMMENT_PROPERTY.match(line)
        if matched:
            key = matched.group(1).strip()
            item = matched.group(2).strip()
            if current and expected_sections is not None and key not in expected_sections:
                sections[current].append(line)
                continue
            sections.setdefault(key, []).append(item)
            current = key
            continue
        if current:
            sections[current].append(line)
    return {
        title: "\n".join(lines).strip()
        for title, lines in sections.items()
        if "\n".join(lines).strip()
    }


def _mapped_facts(
    sections: Mapping[str, str],
    profile: ProjectProfile,
    *,
    source: str,
    existing_fields: set[str],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for logical_name, mapping in sorted(profile.fields.items()):
        if (
            mapping.source != "jira_description_section"
            or mapping.state != "active"
            or not mapping.section
            or logical_name in existing_fields
        ):
            continue
        value = sections.get(mapping.section, "")
        if not value:
            continue
        sanitized = _sanitize(value)
        if sanitized:
            result.append(
                {
                    "field": logical_name,
                    "value": sanitized,
                    "source": source,
                    "section": mapping.section,
                }
            )
    return result


def _core_facts(sections: Mapping[str, str], *, source: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for field, candidates in _CORE_FACT_SECTIONS.items():
        for section in candidates:
            value = _sanitize(sections.get(section, ""))
            if value:
                facts.append(
                    {
                        "field": field,
                        "value": value,
                        "source": source,
                        "section": section,
                    }
                )
                break
    if not any(item["field"] == "task_goal" for item in facts):
        overview = _sanitize(sections.get("__overview__", ""))
        if overview:
            facts.append(
                {
                    "field": "task_goal",
                    "value": overview,
                    "source": source,
                    "section": "__overview__",
                }
            )
    return facts


def _selected_safe_sections(
    sections: Mapping[str, str], profile: ProjectProfile
) -> dict[str, str]:
    selected = {"__overview__", *_REPOSITORY_HINT_SECTIONS}
    for aliases in _CORE_FACT_SECTIONS.values():
        selected.update(aliases)
    selected.update(
        mapping.section
        for mapping in profile.fields.values()
        if mapping.source == "jira_description_section"
        and mapping.state == "active"
        and mapping.section
    )
    return {
        title: sanitized
        for title, value in sections.items()
        if title in selected and (sanitized := _sanitize(value))
    }


def _known_sections(profile: ProjectProfile) -> set[str]:
    known = {title for aliases in _CORE_FACT_SECTIONS.values() for title in aliases}
    known.update(_REPOSITORY_HINT_SECTIONS)
    known.update(
        mapping.section
        for mapping in profile.fields.values()
        if mapping.source == "jira_description_section" and mapping.section
    )
    return known


def _repository_hints(
    description_sections: Mapping[str, str],
    comments: Iterable[JiraComment],
    allowed_repositories: set[str],
) -> list[dict[str, Any]]:
    sources: list[tuple[str, str, str, str, str]] = []
    for section, value in description_sections.items():
        if section in _REPOSITORY_HINT_SECTIONS:
            sources.append(("jira_description_section", section, value, "", ""))
    for comment in comments:
        for section, value in _comment_sections(
            comment.body, expected_sections=set(_REPOSITORY_HINT_SECTIONS)
        ).items():
            if section in _REPOSITORY_HINT_SECTIONS:
                sources.append(
                    ("jira_comment", section, value, comment.comment_id, comment.created)
                )
    hints: list[dict[str, Any]] = []
    for source, section, value, comment_id, created in sources:
        for raw_line in value.splitlines():
            candidate = raw_line.strip().lstrip("-* ").strip()
            repository, separator, branch = candidate.partition(":")
            repository, branch = repository.strip(), branch.strip()
            if not separator or repository not in allowed_repositories or not branch:
                continue
            entry: dict[str, Any] = {
                "repository": repository,
                "branch": _sanitize(branch),
                "source": source,
                "section": section,
                "confirmation_status": "proposal_only",
            }
            if comment_id:
                entry["comment_id"] = comment_id
                entry["created"] = _safe_metadata(created)
            hints.append(entry)
    return hints


def _sanitize(value: str) -> str:
    compact = "\n".join(line.strip() for line in value.replace("\x00", " ").splitlines())
    if _SQL.search(compact):
        return "[REDACTED_SQL]"
    lines = []
    for line in compact.splitlines():
        if _STACK_FRAME.fullmatch(line):
            continue
        line = _SENSITIVE_VALUE.sub(r"\1\2[REDACTED]", line)
        line = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", line)
        line = _QUERY_SECRET.sub(r"\1[REDACTED]", line)
        if line:
            lines.append(line)
    return "\n".join(lines)[:MAX_FACT_VALUE_LENGTH].strip()


def _safe_metadata(value: str) -> str:
    return _sanitize(value)[:256]


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )
