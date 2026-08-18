from __future__ import annotations

import re
import uuid
from typing import Any

from ao_maint.jira.model import plain_text

_INLINE_PATTERN = re.compile(
    r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~|"
    r"\+[^+]+\+|\^[^^]+\^|~[^~]+~|\[[^\]]+\]\([^)]+\))"
)
_ORDERED_LIST_PATTERN = re.compile(r"^(\d+)[.)]\s+(.*)$")
_TASK_LIST_PATTERN = re.compile(r"^-?\s*\[([ xX])\]\s+(.*)$")
_RULE_PATTERN = re.compile(r"^-{3,}$")


def markdown_to_adf(markdown: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    list_kind: str | None = None
    list_items: list[dict[str, Any]] = []
    list_order = 1
    code_lines: list[str] | None = None

    def flush_list() -> None:
        nonlocal list_kind, list_items
        if list_kind is not None and list_items:
            if list_kind == "ordered":
                content.append(
                    {
                        "type": "orderedList",
                        "attrs": {"order": list_order},
                        "content": list_items,
                    }
                )
            elif list_kind == "task":
                content.append(
                    {
                        "type": "taskList",
                        "attrs": {"localId": str(uuid.uuid4())},
                        "content": list_items,
                    }
                )
            else:
                content.append({"type": "bulletList", "content": list_items})
        list_kind = None
        list_items = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines is not None:
            content.append(
                {
                    "type": "codeBlock",
                    "content": [{"type": "text", "text": "\n".join(code_lines)}],
                }
            )
            code_lines = None

    for raw_line in markdown.replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_list()
            if code_lines is None:
                code_lines = []
            else:
                flush_code()
            continue
        if code_lines is not None:
            code_lines.append(line)
            continue
        if stripped.startswith("#"):
            flush_list()
            marker, _, title = stripped.partition(" ")
            level = min(max(len(marker), 1), 6) if set(marker) == {"#"} else 2
            content.append(_block_node("heading", title.strip(), attrs={"level": level}))
            continue
        if _RULE_PATTERN.fullmatch(stripped):
            flush_list()
            content.append({"type": "rule"})
            continue
        task_match = _TASK_LIST_PATTERN.match(stripped)
        if task_match:
            if list_kind != "task":
                flush_list()
                list_kind = "task"
            state = "DONE" if task_match.group(1).lower() == "x" else "TODO"
            list_items.append(
                {
                    "type": "taskItem",
                    "attrs": {"state": state, "localId": str(uuid.uuid4())},
                    "content": _inline_nodes(task_match.group(2)),
                }
            )
            continue
        ordered_match = _ORDERED_LIST_PATTERN.match(stripped)
        if ordered_match:
            if list_kind != "ordered":
                flush_list()
                list_kind = "ordered"
                list_order = int(ordered_match.group(1))
            list_items.append(
                {"type": "listItem", "content": [_block_node("paragraph", ordered_match.group(2))]}
            )
            continue
        if stripped.startswith("- ") or stripped.startswith("* "):
            if list_kind != "bullet":
                flush_list()
                list_kind = "bullet"
            text = stripped[2:].strip()
            list_items.append(
                {"type": "listItem", "content": [_block_node("paragraph", text)]}
            )
            continue
        if stripped:
            flush_list()
            content.append(_block_node("paragraph", stripped))
        else:
            flush_list()
    flush_list()
    flush_code()
    return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph"}]}


def _block_node(kind: str, text: str, attrs: dict[str, Any] | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {"type": kind}
    if attrs:
        node["attrs"] = attrs
    inline = _inline_nodes(text)
    if inline:
        node["content"] = inline
    return node


def _inline_nodes(text: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for part in _INLINE_PATTERN.split(text):
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            nodes.append({"type": "text", "text": part[1:-1], "marks": [{"type": "code"}]})
        elif part.startswith("**") and part.endswith("**") and len(part) >= 4:
            nodes.append({"type": "text", "text": part[2:-2], "marks": [{"type": "strong"}]})
        elif part.startswith("*") and part.endswith("*") and len(part) >= 2:
            nodes.append({"type": "text", "text": part[1:-1], "marks": [{"type": "em"}]})
        elif part.startswith("~~") and part.endswith("~~") and len(part) >= 4:
            nodes.append({"type": "text", "text": part[2:-2], "marks": [{"type": "strike"}]})
        elif part.startswith("+") and part.endswith("+") and len(part) >= 2:
            nodes.append(
                {"type": "text", "text": part[1:-1], "marks": [{"type": "underline"}]}
            )
        elif part.startswith("^") and part.endswith("^") and len(part) >= 2:
            nodes.append(
                {
                    "type": "text",
                    "text": part[1:-1],
                    "marks": [{"type": "subsup", "attrs": {"type": "sup"}}],
                }
            )
        elif part.startswith("~") and part.endswith("~") and len(part) >= 2:
            nodes.append(
                {
                    "type": "text",
                    "text": part[1:-1],
                    "marks": [{"type": "subsup", "attrs": {"type": "sub"}}],
                }
            )
        elif part.startswith("[") and "](" in part:
            label, _, rest = part[1:].partition("](")
            url = rest.rstrip(")") if rest.endswith(")") else rest
            nodes.append(
                {
                    "type": "text",
                    "text": label,
                    "marks": [{"type": "link", "attrs": {"href": url}}],
                }
            )
        else:
            nodes.append({"type": "text", "text": part})
    return nodes
