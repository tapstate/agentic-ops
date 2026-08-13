from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from agentic_ops.output import EXIT_BLOCKED, RuntimeErrorResult

SOURCE_MAINTENANCE: Final = "source_maintenance"
PROJECT_EXECUTION: Final = "project_execution"
VALID_MODES: Final = frozenset({SOURCE_MAINTENANCE, PROJECT_EXECUTION})


@dataclass(frozen=True)
class Workspace:
    root: Path
    mode: str
    config_path: Path | None


def resolve_workspace(root: str, requested_mode: str | None = None) -> Workspace:
    workspace_root = Path(root).expanduser().resolve()
    if not workspace_root.is_dir():
        raise RuntimeErrorResult(
            code="workspace_not_found",
            message=f"工作空间不存在：{workspace_root}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供存在且可访问的工作空间目录",
        )

    config_path = workspace_root / ".agentic-ops" / "agent.json"
    configured_mode = _configured_mode(config_path)
    source_marker = workspace_root / "docs" / "strategy" / "project-goals.md"
    detected_mode = configured_mode
    if detected_mode is None and source_marker.is_file():
        detected_mode = SOURCE_MAINTENANCE

    if detected_mode is None:
        raise RuntimeErrorResult(
            code="workspace_mode_unknown",
            message="无法从工作空间配置或源头仓库标记识别运行模式",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请在 .agentic-ops/agent.json 中明确配置 mode",
        )
    if detected_mode not in VALID_MODES:
        raise RuntimeErrorResult(
            code="workspace_mode_invalid",
            message=f"工作空间配置了不支持的运行模式：{detected_mode}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请将 mode 设置为 source_maintenance 或 project_execution",
        )
    if requested_mode is not None and requested_mode != detected_mode:
        raise RuntimeErrorResult(
            code="workspace_mode_mismatch",
            message=f"请求模式 {requested_mode} 与工作空间模式 {detected_mode} 不一致",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请切换到匹配的工作空间，或修正明确配置后重试",
        )
    if detected_mode == PROJECT_EXECUTION and source_marker.is_file():
        raise RuntimeErrorResult(
            code="workspace_mode_mismatch",
            message="AgenticOps 源头仓库不能作为业务项目执行工作空间",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请在独立业务项目 AI 工作空间中执行任务",
        )

    return Workspace(
        root=workspace_root,
        mode=detected_mode,
        config_path=config_path if config_path.is_file() else None,
    )


def require_mode(workspace: Workspace, allowed_modes: frozenset[str]) -> None:
    if workspace.mode in allowed_modes:
        return
    allowed = "、".join(sorted(allowed_modes))
    raise RuntimeErrorResult(
        code="workspace_mode_mismatch",
        message=f"当前操作只允许在 {allowed} 模式执行",
        status="blocked",
        exit_code=EXIT_BLOCKED,
        required_human_action="请切换到与操作匹配的工作空间模式",
    )


def _configured_mode(config_path: Path) -> str | None:
    if not config_path.is_file():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeErrorResult(
            code="workspace_config_invalid",
            message=f"工作空间配置无法读取：{error}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复 .agentic-ops/agent.json 后重试",
        ) from error
    mode = payload.get("mode")
    return mode if isinstance(mode, str) and mode else None
