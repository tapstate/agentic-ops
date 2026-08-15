from __future__ import annotations

import json
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
