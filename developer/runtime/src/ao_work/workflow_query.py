from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from ao_work.output import RuntimeErrorResult, workflow_query

_PROCESS_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_PROCESS_ROOT = Path("developer/standards/contracts/processes")


def execute_workflow_query(
    install_root: Path,
    *,
    process_id: str,
    current_step_id: str,
) -> dict[str, Any]:
    """从已安装的流程标准生成不可执行的流程导航。"""
    if not _PROCESS_ID.fullmatch(process_id):
        raise _blocked(
            "workflow_query_process_invalid",
            "process_id 格式无效",
            "请使用已安装流程标准中的 process_id，不要传入路径或临时名称",
        )
    root = install_root.resolve()
    process_root = (root / _PROCESS_ROOT).resolve()
    path = (process_root / f"{process_id.replace('_', '-')}.yaml").resolve()
    try:
        path.relative_to(process_root)
        if path.is_symlink():
            raise OSError("symlink")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise _blocked(
            "workflow_query_process_not_found",
            f"未安装流程标准：{process_id}",
            "请先确认 Project Profile 使用的 process_id，或更新 developer 安装后重试",
        ) from error
    except (OSError, yaml.YAMLError) as error:
        raise _blocked(
            "workflow_query_process_invalid",
            f"流程标准无法读取：{type(error).__name__}",
            "请停止使用该流程并联系 AgenticOps 维护者修复已安装标准资产",
        ) from error

    if not isinstance(payload, dict) or payload.get("process_id") != process_id:
        raise _blocked(
            "workflow_query_process_invalid",
            "流程标准的 process_id 与请求不一致",
            "请更新 developer 安装或使用流程标准中声明的 process_id",
        )
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise _blocked(
            "workflow_query_stages_invalid",
            "流程标准未声明有效 stages",
            "请联系 AgenticOps 维护者修复流程标准",
        )
    steps: list[dict[str, str]] = []
    for stage in stages:
        if not isinstance(stage, dict):
            raise _blocked(
                "workflow_query_stages_invalid",
                "流程标准包含非对象 stage",
                "请联系 AgenticOps 维护者修复流程标准",
            )
        stage_id = stage.get("id")
        if not isinstance(stage_id, str) or not stage_id.strip():
            raise _blocked(
                "workflow_query_stages_invalid",
                "流程标准 stage 缺少 id",
                "请联系 AgenticOps 维护者修复流程标准",
            )
        steps.append(
            {
                "id": stage_id,
                "label": stage_id,
                "kind": "decision" if stage.get("review_gate") else "action",
            }
        )
    return workflow_query(
        process_id,
        current_step_id=current_step_id,
        steps=steps,
    )


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=2,
        required_human_action=action,
    )
