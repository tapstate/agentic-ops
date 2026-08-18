from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.managed_io import read_managed_json
from ao_work.workspace_security import (
    validate_workspace_managed_path,
    validate_workspace_state_root,
)

DEVELOPER: Final = "developer"

JIRA_KEY_PATTERN = re.compile(r"^[0-9A-Za-z]+-[1-9][0-9]*$")
REPOSITORY_SHORT_NAME_PATTERN = re.compile(r"^[0-9A-Za-z_.-]+$")
WORKTREE_PATH_MAX_LENGTH = 240


@dataclass(frozen=True)
class Workspace:
    root: Path
    workplane: str
    config_path: Path


def validate_workspace_source_separation(workspace_root: Path, source_root: Path) -> None:
    root = workspace_root.expanduser().resolve()
    source = source_root.expanduser().resolve()
    if source == root or source in root.parents or root in source.parents:
        raise _blocked(
            "workspace_source_boundary_invalid",
            "业务项目工作空间与源码目录不能相同，也不能互相嵌套",
            "请把 AI 工作空间与业务源码仓库放在两个独立目录",
        )


def validate_business_source_root(workspace_root: Path, source_root: Path) -> Path:
    source = source_root.expanduser().resolve()
    validate_workspace_source_separation(workspace_root, source)
    source_ancestor = _source_ancestor(source)
    if source_ancestor is not None:
        raise _blocked(
            "workplane_mismatch",
            f"业务源码目录不能指向 AgenticOps 源头仓库或其子目录：{source_ancestor}",
            "请把 source_root 指向业务项目代码仓库",
        )
    return source


def validate_source_pool_root(pool_root: Path, *, allow_missing: bool = False) -> Path:
    """校验中央克隆池根：必配、存在或可创建、可写、不得为安装目录/源头仓库或其后代。

    - `allow_missing=False`（默认）：池根必须已存在（任务工作树等运行时场景）。
    - `allow_missing=True`：池根不存在时允许由 init 自动创建（workspace init 场景），
      但仍校验父路径可创建与最终可写性，安全边界校验不放松。
    """
    pool = pool_root.expanduser().resolve()
    if pool == Path.home() or pool == Path.home().resolve():
        raise _blocked(
            "source_pool_root_invalid",
            "中央克隆池根不能是用户主目录",
            "请把 source_pool_root 指向独立的源码池目录（如 ~/github）",
        )
    install_root = pool / ".agentic-ops"
    source_ancestor = _source_ancestor(pool)
    if (
        install_root in pool.parents
        or (pool / ".agentic-ops" / "agent.json").exists()
        or source_ancestor is not None
    ):
        raise _blocked(
            "source_pool_root_invalid",
            "中央克隆池根不能是 AgenticOps 安装目录、业务工作空间或源头仓库（或其后代）",
            "请把 source_pool_root 指向独立的源码池目录",
        )
    if pool.exists() and not pool.is_dir():
        raise _blocked(
            "source_pool_root_invalid",
            f"中央克隆池根不是目录：{pool}",
            "请把 source_pool_root 指向目录",
        )
    if not allow_missing:
        if not pool.is_dir():
            raise _blocked(
                "source_pool_root_invalid",
                f"中央克隆池根不存在或不是目录：{pool}",
                "请先创建池根目录并配置 source_pool_root",
            )
        if not os.access(pool, os.W_OK | os.X_OK):
            raise _blocked(
                "source_pool_root_invalid",
                f"中央克隆池根不可写：{pool}",
                "请修复池根目录权限后重试",
            )
        return pool
    # allow_missing：允许 init 创建池根。定位最近的已存在祖先并校验其可创建性。
    ancestor = pool
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    if not ancestor.is_dir() or not os.access(ancestor, os.W_OK | os.X_OK):
        raise _blocked(
            "source_pool_root_invalid",
            f"无法创建中央克隆池根：{pool}",
            "请修复池根父目录权限或指定其它 source_pool_root",
        )
    if pool.is_dir() and not os.access(pool, os.W_OK | os.X_OK):
        raise _blocked(
            "source_pool_root_invalid",
            f"中央克隆池根不可写：{pool}",
            "请修复池根目录权限后重试",
        )
    return pool


def normalize_worktree_from_branch(from_branch: str) -> str:
    """任务工作树 from_branch 规范化：含 / 替换为 -（feature/x → feature-x）。"""
    normalized = from_branch.strip().replace("/", "-")
    _validate_worktree_segment(normalized, "from_branch")
    return normalized


def repository_short_name(repository: str) -> str:
    """owner/repository → repository 短名（复用既有校验语义）。"""
    if repository.count("/") != 1:
        raise _blocked(
            "repository_slug_invalid",
            f"仓库标识必须使用 owner/repository 格式：{repository}",
            "请修正 profile repositories 配置",
        )
    short_name = repository.split("/", 1)[1]
    if not REPOSITORY_SHORT_NAME_PATTERN.fullmatch(short_name):
        raise _blocked(
            "repository_short_name_invalid",
            f"仓库短名含非法字符：{short_name}",
            "请修正 profile repositories 配置",
        )
    return short_name


def task_worktree_path(
    pool_root: Path,
    jira_key: str,
    from_branch: str,
    repository: str,
) -> Path:
    """任务级子工作树路径：<pool_root>/<jira_id>/<from_branch>/<repo>。"""
    pool = validate_source_pool_root(pool_root)
    if not JIRA_KEY_PATTERN.fullmatch(jira_key):
        raise _blocked(
            "worktree_path_invalid",
            f"Jira 编号格式无效：{jira_key}",
            "请提供形如 TAP-123 的 Jira 编号",
        )
    normalized_branch = normalize_worktree_from_branch(from_branch)
    short_name = repository_short_name(repository)
    path = pool / jira_key / normalized_branch / short_name
    if len(str(path)) > WORKTREE_PATH_MAX_LENGTH:
        raise _blocked(
            "worktree_path_invalid",
            f"任务工作树路径超过 {WORKTREE_PATH_MAX_LENGTH} 字符限制：{path}",
            "请缩短分支名或仓库名后重试",
        )
    return path


def _validate_worktree_segment(value: str, label: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or value.startswith("-")
        or "/" in value
        or "\\" in value
        or "@{" in value
        or value.startswith(".")
        or ".." in value
    ):
        raise _blocked(
            "worktree_path_invalid",
            f"任务工作树 {label} 含非法字符或路径穿越：{value}",
            "请修正分支名后重试",
        )
    if not re.fullmatch(r"[0-9A-Za-z_.-]+", value):
        raise _blocked(
            "worktree_path_invalid",
            f"任务工作树 {label} 只能包含安全字符：{value}",
            "请修正分支名后重试",
        )


def resolve_developer_workspace(root: str) -> Workspace:
    workspace_root = Path(root).expanduser().resolve()
    if not workspace_root.is_dir():
        raise _blocked(
            "workspace_not_found",
            f"工作空间不存在：{workspace_root}",
            "请提供存在且可访问的业务项目工作空间目录",
        )
    source_ancestor = _source_ancestor(workspace_root)
    if source_ancestor is not None:
        raise _blocked(
            "workplane_mismatch",
            f"ao-work 不能在 AgenticOps 源头仓库或其子目录中执行：{source_ancestor}",
            "请切换到不位于 AgenticOps 源头目录树中的独立业务项目 AI 工作空间",
        )

    state_root = validate_workspace_state_root(workspace_root)
    config_path = validate_workspace_managed_path(
        workspace_root, state_root / "agent.json"
    )
    payload = _read_config(config_path)
    if "mode" in payload:
        raise _blocked(
            "workspace_schema_upgrade_required",
            "工作空间仍使用已废弃的 mode 字段",
            "请使用 ao-work workspace init 明确迁移为 workplane=developer",
        )
    if payload.get("workplane") != DEVELOPER:
        raise _blocked(
            "workplane_mismatch",
            "当前工作空间没有声明 developer 工作面",
            "请使用 ao-work workspace init 初始化业务项目工作空间",
        )
    workspace = Workspace(root=workspace_root, workplane=DEVELOPER, config_path=config_path)
    source_root = payload.get("source_root")
    if isinstance(source_root, str) and source_root.strip():
        validate_business_source_root(workspace_root, Path(source_root))
    return workspace


def _source_ancestor(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        marker = candidate / ".agentic-ops-source"
        maintainer_entry = candidate / "maintainer" / "AGENTS.md"
        # maintainer 入口本身就是源头事实；删除 marker 不能把真实源头
        # 降级成 developer 工作空间。异常 marker 与 maintainer 入口并存也
        # 必须 fail closed。
        if maintainer_entry.exists() or maintainer_entry.is_symlink():
            return candidate
        if not marker.exists() and not marker.is_symlink():
            continue
        if marker.is_symlink() or not marker.is_file():
            return candidate
        try:
            value = marker.read_text(encoding="utf-8").strip()
        except OSError:
            return candidate
        if value == "maintainer":
            return candidate
    return None


def _read_config(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = read_managed_json(path, label="工作空间 agent.json")
    except (OSError, json.JSONDecodeError) as error:
        raise _blocked(
            "workspace_config_invalid",
            f"工作空间配置无法读取：{error}",
            "请修复 .agentic-ops/agent.json 后重试",
        ) from error
    if not isinstance(payload, dict):
        raise _blocked(
            "workspace_config_invalid",
            "工作空间配置必须是 JSON 对象",
            "请修复 .agentic-ops/agent.json 后重试",
        )
    return payload


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action=action,
    )
