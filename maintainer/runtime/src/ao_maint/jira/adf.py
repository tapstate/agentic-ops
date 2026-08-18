from __future__ import annotations

from typing import Any

from ao_maint.jira.model import plain_text


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


def _text_node(kind: str, text: str, attrs: dict[str, Any] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": kind}
    if attrs:
        node["attrs"] = attrs
    if text:
        node["content"] = [{"type": "text", "text": text}]
    return node
