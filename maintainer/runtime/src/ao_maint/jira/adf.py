from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from ao_maint.jira.model import plain_text

_INLINE_PATTERN = re.compile(
    r"(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~|"
    r"\+[^+]+\+|\^[^^]+\^|~[^~]+~|\[[^\]]+\]\([^)]+\))"
)
_ORDERED_LIST_PATTERN = re.compile(r"^(\d+)[.)]\s+(.*)$")
_TASK_LIST_PATTERN = re.compile(r"^-?\s*\[([ xX])\]\s+(.*)$")
_RULE_PATTERN = re.compile(r"^-{3,}$")


@dataclass(frozen=True)
class AdfRender:
    """可审查的 ADF 回读结果；未知结构绝不静默降级。"""

    markdown: str
    plain_text: str
    complete: bool
    unsupported_node_types: tuple[str, ...]


def adf_to_markdown(value: dict[str, Any] | None) -> AdfRender:
    """严格转换 Jira ADF，为 Description 展示提供可解释的读模型。

    原始 ADF 由调用方一并保留。这里遇到未知节点或 mark 时保留已知文本、
    插入醒目占位，并将 ``complete`` 置为 False，以阻止调用方把结果当作
    可安全覆盖的完整 Description。
    """

    if value is None:
        return AdfRender("", "", True, ())
    unknown: set[str] = set()

    def unsupported(kind: str, value_type: Any) -> str:
        label = str(value_type or "missing")
        unknown.add(f"{kind}:{label}")
        return f"[不支持的 ADF {kind}: {label}]"

    def inline(node: Any) -> str:
        if not isinstance(node, dict):
            return unsupported("node", "invalid")
        node_type = node.get("type")
        if node_type == "text":
            text = node.get("text")
            if not isinstance(text, str):
                return unsupported("node", "text-without-text")
            marks = node.get("marks", [])
            if not isinstance(marks, list):
                return unsupported("mark", "invalid") + text
            for mark in reversed(marks):
                if not isinstance(mark, dict):
                    text = unsupported("mark", "invalid") + text
                    continue
                mark_type = mark.get("type")
                if mark_type == "strong":
                    text = f"**{text}**"
                elif mark_type == "em":
                    text = f"*{text}*"
                elif mark_type == "code":
                    text = f"`{text}`"
                elif mark_type == "strike":
                    text = f"~~{text}~~"
                elif mark_type == "underline":
                    text = f"+{text}+"
                elif mark_type == "subsup":
                    subtype = (
                        mark.get("attrs", {}).get("type")
                        if isinstance(mark.get("attrs"), dict)
                        else None
                    )
                    if subtype == "sup":
                        text = f"^{text}^"
                    elif subtype == "sub":
                        text = f"~{text}~"
                    else:
                        text = unsupported("mark", "subsup") + text
                elif mark_type == "link":
                    href = (
                        mark.get("attrs", {}).get("href")
                        if isinstance(mark.get("attrs"), dict)
                        else None
                    )
                    if isinstance(href, str) and href:
                        text = f"[{text}]({href})"
                    else:
                        text = unsupported("mark", "link") + text
                else:
                    text = unsupported("mark", mark_type) + text
            return text
        if node_type == "hardBreak":
            return "\n"
        return unsupported("node", node_type)

    def children_inline(node: dict[str, Any]) -> str:
        content = node.get("content", [])
        if not isinstance(content, list):
            return unsupported("node", f"{node.get('type')}-content")
        return "".join(inline(item) for item in content)

    def block(node: Any, indent: str = "") -> str:
        if not isinstance(node, dict):
            return unsupported("node", "invalid")
        node_type = node.get("type")
        if node_type == "paragraph":
            return children_inline(node)
        if node_type == "heading":
            attrs = node.get("attrs", {})
            level = attrs.get("level", 2) if isinstance(attrs, dict) else 2
            level = level if isinstance(level, int) and 1 <= level <= 6 else 2
            return f"{'#' * level} {children_inline(node)}".rstrip()
        if node_type == "rule":
            return "---"
        if node_type == "codeBlock":
            return f"```\n{children_inline(node)}\n```"
        if node_type in {"bulletList", "orderedList", "taskList"}:
            content = node.get("content", [])
            if not isinstance(content, list):
                return unsupported("node", f"{node_type}-content")
            order = 1
            if node_type == "orderedList":
                attrs = node.get("attrs", {})
                if isinstance(attrs, dict) and isinstance(attrs.get("order"), int):
                    order = attrs["order"]
            lines: list[str] = []
            for index, item in enumerate(content):
                if not isinstance(item, dict):
                    lines.append(indent + unsupported("node", "invalid"))
                    continue
                if node_type == "taskList":
                    if item.get("type") != "taskItem":
                        lines.append(indent + unsupported("node", item.get("type")))
                        continue
                    attrs = item.get("attrs", {})
                    state = attrs.get("state") if isinstance(attrs, dict) else None
                    if state not in {"TODO", "DONE"}:
                        lines.append(indent + unsupported("node", "taskItem-state"))
                    marker = "x" if state == "DONE" else " "
                    lines.append(f"{indent}- [{marker}] {children_inline(item)}".rstrip())
                    continue
                if item.get("type") != "listItem":
                    lines.append(indent + unsupported("node", item.get("type")))
                    continue
                item_content = item.get("content", [])
                if not isinstance(item_content, list):
                    lines.append(indent + unsupported("node", "listItem-content"))
                    continue
                first = block(item_content[0], indent + "  ") if item_content else ""
                prefix = f"{order + index}. " if node_type == "orderedList" else "- "
                lines.append(f"{indent}{prefix}{first}".rstrip())
                for nested in item_content[1:]:
                    lines.append(block(nested, indent + "  "))
            return "\n".join(lines)
        return unsupported("node", node_type)

    if not isinstance(value, dict) or value.get("type") != "doc":
        markdown = unsupported("node", value.get("type") if isinstance(value, dict) else "invalid")
    else:
        content = value.get("content", [])
        if not isinstance(content, list):
            markdown = unsupported("node", "doc-content")
        else:
            markdown = "\n\n".join(block(node) for node in content).strip()
    return AdfRender(
        markdown=markdown,
        plain_text=plain_text(value),
        complete=not unknown,
        unsupported_node_types=tuple(sorted(unknown)),
    )


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
