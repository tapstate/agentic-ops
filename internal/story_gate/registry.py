from __future__ import annotations

import hashlib
import json
import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from internal.story_gate.model import (
    ACCEPTANCE_CHECKS,
    STORY_CATEGORIES,
    StoryContract,
    StoryRegistry,
)

REGISTRY_PATH = "internal/story_gate/stories.yaml"
REQUIRED_STORY_SECTIONS = ("### 验收标准", "### 保护行为", "### 验收证据")


def load_story_registry(root: Path) -> StoryRegistry:
    registry_path = root / REGISTRY_PATH
    if not registry_path.is_file():
        raise ValueError(f"故事注册表不存在：{REGISTRY_PATH}")
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("故事注册表必须是 YAML mapping")
    if payload.get("schema_version") != 1:
        raise ValueError("故事注册表 schema_version 必须为 1")
    categories = payload.get("story_categories")
    if not isinstance(categories, list) or set(categories) != STORY_CATEGORIES:
        raise ValueError("故事注册表必须且只能声明 internal、product")
    raw_stories = payload.get("stories")
    if not isinstance(raw_stories, list) or not raw_stories:
        raise ValueError("故事注册表 stories 不能为空")

    stories: list[StoryContract] = []
    used_ids: set[str] = set()
    used_documents: set[str] = set()
    for index, raw_story in enumerate(raw_stories):
        if not isinstance(raw_story, dict):
            raise ValueError(f"stories[{index}] 必须是 mapping")
        story = _parse_story(root, raw_story, index)
        if story.story_id in used_ids:
            raise ValueError(f"故事编号重复：{story.story_id}")
        if story.document in used_documents:
            raise ValueError(f"故事文档重复绑定：{story.document}")
        used_ids.add(story.story_id)
        used_documents.add(story.document)
        stories.append(story)

    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return StoryRegistry(
        schema_version=1,
        path=REGISTRY_PATH,
        stories=tuple(stories),
        digest=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    )


def path_matches(pattern: str, path: str) -> bool:
    return fnmatch.fnmatchcase(path, pattern)


def _parse_story(root: Path, raw: dict[str, Any], index: int) -> StoryContract:
    story_id = _required_text(raw, "story_id", index)
    category = _required_text(raw, "category", index)
    if category not in STORY_CATEGORIES:
        raise ValueError(f"故事 {story_id} category 无效：{category}")
    expected_prefix = "INT-" if category == "internal" else "PROD-"
    if not story_id.startswith(expected_prefix):
        raise ValueError(f"故事 {story_id} 与 category 前缀不一致")
    document = _safe_relative_path(_required_text(raw, "document", index), story_id)
    document_path = root / document
    if not document_path.is_file():
        raise ValueError(f"故事 {story_id} 文档不存在：{document}")
    content = document_path.read_text(encoding="utf-8")
    missing_sections = [section for section in REQUIRED_STORY_SECTIONS if section not in content]
    if missing_sections:
        raise ValueError(f"故事 {story_id} 缺少章节：{', '.join(missing_sections)}")

    protected_paths = tuple(
        _safe_relative_pattern(value, story_id)
        for value in _required_text_list(raw, "protected_paths", story_id)
    )
    acceptance_checks = tuple(_required_text_list(raw, "acceptance_checks", story_id))
    unknown_checks = sorted(set(acceptance_checks) - ACCEPTANCE_CHECKS)
    if unknown_checks:
        raise ValueError(f"故事 {story_id} 使用未知验收检查：{', '.join(unknown_checks)}")
    return StoryContract(
        story_id=story_id,
        category=category,
        title=_required_text(raw, "title", index),
        document=document,
        protected_paths=protected_paths,
        acceptance_checks=acceptance_checks,
        evidence_requirements=tuple(
            _required_text_list(raw, "evidence_requirements", story_id)
        ),
    )


def _required_text(raw: dict[str, Any], key: str, label: object) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 缺少 {key}")
    return value.strip()


def _required_text_list(raw: dict[str, Any], key: str, label: object) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} 的 {key} 必须是非空列表")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{label} 的 {key} 包含空值")
        result.append(item.strip())
    return result


def _safe_relative_path(value: str, story_id: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"故事 {story_id} 使用不安全路径：{value}")
    return path.as_posix()


def _safe_relative_pattern(value: str, story_id: str) -> str:
    if value.startswith("/") or ".." in PurePosixPath(value).parts:
        raise ValueError(f"故事 {story_id} 使用不安全匹配：{value}")
    return value
