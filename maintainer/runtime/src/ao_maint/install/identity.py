"""maintainer 面 Agent 身份机制（AO-40）。

与 developer 面 install identity 同构但简化为维护者场景：
- 身份文件：maintainer/.local/identity.yaml（0600，gitignored）
- 字段：agent_id（必填）、note（可选，例如 AIAgent 名称或备注）
- 评论/证据的执行者字段必须来自该身份，缺失时阻断并提示先配置
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import yaml

from ao_maint.jira.config import local_root
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

MAINTAINER_IDENTITY_FILE = "identity.yaml"

AGENT_ID_PATTERN = "[A-Za-z0-9][A-Za-z0-9._-]{2,127}"


def identity_file_path(source_root: Path) -> Path:
    return local_root(source_root) / MAINTAINER_IDENTITY_FILE


def load_maintainer_identity(source_root: Path) -> dict[str, str]:
    """读取维护者 Agent 身份；缺失或无效时阻断。

    返回 {agent_id, agent_type, model, environment, note}。
    """
    identity_path = identity_file_path(source_root)
    if identity_path.is_symlink() or not identity_path.is_file():
        raise RuntimeErrorResult(
            code="maintainer_identity_missing",
            message="maintainer 工作面尚未配置 Agent 身份",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                "请运行 ao-maint install identity set 配置 agent_id 后重试"
            ),
        )
    try:
        payload = yaml.safe_load(identity_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise RuntimeErrorResult(
            code="maintainer_identity_invalid",
            message=f"维护者身份配置无法解析：{identity_path}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请修复 identity.yaml 后重试",
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeErrorResult(
            code="maintainer_identity_invalid",
            message="维护者身份配置结构无效",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请运行 ao-maint install identity set 重新配置",
        )
    agent_id = str(payload.get("agent_id") or "").strip()
    if not agent_id:
        raise RuntimeErrorResult(
            code="maintainer_identity_invalid",
            message="维护者身份配置缺少 agent_id",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请运行 ao-maint install identity set 重新配置",
        )
    return {
        "agent_id": agent_id,
        "agent_type": str(payload.get("agent_type") or "").strip(),
        "model": str(payload.get("model") or "").strip(),
        "environment": str(payload.get("environment") or "").strip(),
        "note": str(payload.get("note") or "").strip(),
    }


def save_maintainer_identity(
    source_root: Path,
    agent_id: str,
    note: str = "",
    *,
    agent_type: str = "",
    model: str = "",
    environment: str = "",
) -> dict[str, str]:
    """原子写入维护者 Agent 身份（0600）。"""
    agent_id = agent_id.strip()
    if not agent_id:
        raise RuntimeErrorResult(
            code="invalid_agent_id",
            message="agent_id 不能为空",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请提供非空 agent_id",
        )
    identity_path = identity_file_path(source_root)
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent_id": agent_id,
        "agent_type": agent_type.strip(),
        "model": model.strip(),
        "environment": environment.strip(),
        "note": note.strip(),
    }
    content = yaml.safe_dump(
        payload, allow_unicode=True, sort_keys=False
    )
    try:
        identity_path.write_text(content, encoding="utf-8")
        identity_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError as error:
        raise RuntimeErrorResult(
            code="maintainer_identity_write_failed",
            message=f"无法写入维护者身份配置：{identity_path}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请检查 .local 目录权限后重试",
        ) from error
    return {
        "agent_id": agent_id,
        "agent_type": agent_type.strip(),
        "model": model.strip(),
        "environment": environment.strip(),
        "note": note.strip(),
    }
