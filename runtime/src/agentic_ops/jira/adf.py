from __future__ import annotations

from typing import Any

from agentic_ops.jira.model import plain_text


def markdown_to_adf(markdown: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    list_items: list[dict[str, Any]] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            content.append({"type": "bulletList", "content": list_items})
            list_items = []

    for raw_line in markdown.replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("#"):
            flush_list()
            marker, _, title = stripped.partition(" ")
            level = min(max(len(marker), 1), 6) if set(marker) == {"#"} else 2
            content.append(_text_node("heading", title.strip(), attrs={"level": level}))
        elif stripped.startswith("- ") or stripped.startswith("* "):
            text = stripped[2:].strip()
            list_items.append({"type": "listItem", "content": [_text_node("paragraph", text)]})
        else:
            flush_list()
            content.append(_text_node("paragraph", stripped))
    flush_list()
    return {"type": "doc", "version": 1, "content": content or [_text_node("paragraph", "")]}


def merge_description_sections(
    description: dict[str, Any] | None, sections: dict[str, str]
) -> dict[str, Any]:
    document = description or {"type": "doc", "version": 1, "content": []}
    if document.get("type") != "doc" or not isinstance(document.get("content"), list):
        raise ValueError("unsupported jira description payload")
    normalized: dict[str, tuple[str, str]] = {}
    for title, value in sections.items():
        key = normalize_title(title)
        if not key or key in normalized:
            raise ValueError(f"invalid or duplicate description section: {title}")
        normalized[key] = (title.strip(), value)

    counts = {key: 0 for key in normalized}
    for node in document["content"]:
        title = _heading_title(node)
        key = normalize_title(title) if title is not None else ""
        if key in counts:
            counts[key] += 1
    duplicates = [key for key, count in counts.items() if count > 1]
    if duplicates:
        raise ValueError(f"duplicate description section: {', '.join(duplicates)}")

    merged: list[Any] = []
    replaced: set[str] = set()
    index = 0
    existing = document["content"]
    while index < len(existing):
        node = existing[index]
        title = _heading_title(node)
        key = normalize_title(title) if title is not None else ""
        if key not in normalized:
            merged.append(node)
            index += 1
            continue
        canonical_title, value = normalized[key]
        merged.append(_text_node("heading", canonical_title, attrs={"level": 2}))
        merged.extend(_paragraphs(value))
        replaced.add(key)
        index += 1
        while index < len(existing) and _heading_title(existing[index]) is None:
            index += 1

    for key in sorted(normalized):
        if key in replaced:
            continue
        title, value = normalized[key]
        merged.append(_text_node("heading", title, attrs={"level": 2}))
        merged.extend(_paragraphs(value))
    result = dict(document)
    result["content"] = merged
    return result


def extract_description_section(description: dict[str, Any] | None, title: str) -> str:
    if not description or not isinstance(description.get("content"), list):
        return ""
    target = normalize_title(title)
    collected: list[str] = []
    in_section = False
    for node in description["content"]:
        heading = _heading_title(node)
        if heading is not None:
            if in_section:
                break
            in_section = normalize_title(heading) == target
            continue
        if in_section:
            text = plain_text(node).strip()
            if text:
                collected.append(text)
    return "\n".join(collected)


def normalize_title(value: str) -> str:
    return value.strip().lstrip("#").strip().rstrip("：:").strip()


def _heading_title(node: Any) -> str | None:
    if isinstance(node, dict) and node.get("type") == "heading":
        return plain_text(node)
    return None


def _paragraphs(value: str) -> list[dict[str, Any]]:
    return [_text_node("paragraph", line) for line in value.replace("\r\n", "\n").split("\n")]


def _text_node(kind: str, text: str, attrs: dict[str, Any] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": kind}
    if attrs:
        node["attrs"] = attrs
    if text:
        node["content"] = [{"type": "text", "text": text}]
    return node
