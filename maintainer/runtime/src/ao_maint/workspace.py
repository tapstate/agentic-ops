from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Final

from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

MAINTAINER: Final = "maintainer"
OFFICIAL_REPOSITORY: Final = "tapstate/agentic-ops"


@dataclass(frozen=True)
class Workspace:
    root: Path
    workplane: str


def resolve_maintainer_workspace(root: str) -> Workspace:
    expanded = Path(root).expanduser()
    lexical_root = Path(os.path.abspath(expanded))
    if lexical_root.is_symlink():
        raise _blocked(
            "workplane_mismatch",
            "ao-maint 源头工作区不得是符号链接",
        )
    source_root = lexical_root.resolve()
    if not source_root.is_dir():
        raise _blocked("workspace_not_found", f"源头工作区不存在：{source_root}")
    source_marker = source_root / ".agentic-ops-source"
    maintainer_directory = source_root / "maintainer"
    maintainer_entry = maintainer_directory / "AGENTS.md"
    marker_value = (
        source_marker.read_text(encoding="utf-8").strip()
        if not source_marker.is_symlink() and source_marker.is_file()
        else ""
    )
    if (
        marker_value != MAINTAINER
        or maintainer_directory.is_symlink()
        or not maintainer_directory.is_dir()
        or maintainer_entry.is_symlink()
        or not maintainer_entry.is_file()
    ):
        raise _blocked(
            "workplane_mismatch",
            "ao-maint 只能在 AgenticOps 源头仓库或其 worktree 中运行",
        )
    top_level = _git(source_root, "rev-parse", "--show-toplevel")
    if Path(top_level).expanduser().resolve() != source_root:
        raise _blocked(
            "maintainer_repository_root_required",
            "ao-maint 必须从 AgenticOps Git 仓库或 worktree 根目录运行",
        )
    origins = _git(source_root, "config", "--get-all", "remote.origin.url").splitlines()
    fetch_urls = _git(source_root, "remote", "get-url", "--all", "origin").splitlines()
    push_urls = _git(
        source_root, "remote", "get-url", "--push", "--all", "origin"
    ).splitlines()
    if (
        len(origins) != 1
        or len(fetch_urls) != 1
        or len(push_urls) != 1
        or not _official_repository(origins[0])
        or not _official_repository(fetch_urls[0])
        or not _official_repository(push_urls[0])
        or _normalize_url(origins[0]) != _normalize_url(fetch_urls[0])
        or _normalize_url(origins[0]) != _normalize_url(push_urls[0])
    ):
        raise _blocked(
            "maintainer_repository_identity_invalid",
            "AgenticOps 源头 origin 或实际 fetch/push 地址不是固定官方仓库",
        )
    return Workspace(root=source_root, workplane=MAINTAINER)


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise _blocked(
            "maintainer_repository_identity_invalid",
            f"无法校验 AgenticOps 源头 Git 身份：{type(error).__name__}",
        ) from error
    if completed.returncode != 0:
        raise _blocked(
            "maintainer_repository_identity_invalid",
            "无法校验 AgenticOps 源头 Git 身份",
        )
    return completed.stdout.strip()


def _normalize_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized


def _official_repository(value: str) -> bool:
    return _normalize_url(value) in {
        f"git@github.com:{OFFICIAL_REPOSITORY}",
        f"ssh://git@github.com/{OFFICIAL_REPOSITORY}",
        f"https://github.com/{OFFICIAL_REPOSITORY}",
    }


def _blocked(code: str, message: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action=(
            "请切换到包含源头标记、maintainer AI 入口和固定官方 origin 的 "
            "AgenticOps Git 仓库或 worktree 根目录"
        ),
    )
