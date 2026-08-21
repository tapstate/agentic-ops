"""阶段注册表与准出映射（AO-39/AO-41）。

阶段处理模式：
- 阶段（stage）注册表声明（developer/standards/stages/stages.yaml + stages.schema.json）
- 阶段 = 语义化 ID + 准入条件 + 准出决策（next_stage + 可选 jira_transition）
- 阶段内步骤（step）= 处理步骤规范声明（可无实体：访问 LLM 拿结果、
  执行脚本拿结果等），不重复声明准入（继承所属阶段的准入）
- 阶段独立于 Jira：阶段是内部执行依据；准出声明了 jira_transition 的阶段，
  通过项目 profile.transitions 映射回 Jira 状态流转（复用 D-037 严格匹配）

本模块只读资产、不改写任务状态；任务状态配置（stage 运行时实例化）由
后续阶段接入 TaskStore，当前保持独立轻量。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ao_work.output import EXIT_BLOCKED, EXIT_CAPABILITY_GAP, RuntimeErrorResult

_STAGES_RELATIVE = Path("developer") / "standards" / "stages"
_REGISTRY_FILENAME = "stages.yaml"
_SCHEMA_FILENAME = "stages.schema.json"


def _blocked(
    code: str, message: str, action: str, *, exit_code: int = EXIT_BLOCKED
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=exit_code,
        required_human_action=action,
    )


def stage_registry_dir(install_root: Path) -> Path:
    return install_root / _STAGES_RELATIVE


def stage_registry_path(install_root: Path) -> Path:
    return stage_registry_dir(install_root) / _REGISTRY_FILENAME


def stage_schema_path(install_root: Path) -> Path:
    return stage_registry_dir(install_root) / _SCHEMA_FILENAME


def load_stage_registry(install_root: Path) -> dict[str, Any]:
    """读取并校验阶段注册表；缺失或无效时阻断。"""
    registry_path = stage_registry_path(install_root)
    if not registry_path.is_file():
        raise _blocked(
            "stage_registry_missing",
            "缺少阶段注册表",
            "请确认阶段注册表已随标准资产安装（developer/standards/stages/stages.yaml）",
            exit_code=EXIT_CAPABILITY_GAP,
        )
    try:
        payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise _blocked(
            "stage_registry_invalid_yaml",
            f"阶段注册表 YAML 解析失败：{error}",
            "请修复阶段注册表后重试",
        ) from error
    if not isinstance(payload, dict):
        raise _blocked(
            "stage_registry_invalid",
            "阶段注册表内容不是对象",
            "请修复阶段注册表后重试",
        )
    _validate_registry_against_schema(install_root, payload)
    _validate_registry_references(payload)
    return payload


def _validate_registry_against_schema(
    install_root: Path, payload: dict[str, Any]
) -> None:
    """用 stages.schema.json 校验注册表结构（优先 jsonschema，缺失时 minimal 兜底）。"""
    schema_path = stage_schema_path(install_root)
    if not schema_path.is_file():
        raise _blocked(
            "stage_schema_missing",
            "缺少阶段注册表 Schema",
            "请确认 stages.schema.json 已随标准资产安装",
            exit_code=EXIT_CAPABILITY_GAP,
        )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise _blocked(
            "stage_schema_invalid",
            f"阶段注册表 Schema 解析失败：{error}",
            "请修复 stages.schema.json 后重试",
        ) from error
    try:
        import jsonschema
    except ImportError:
        _validate_registry_minimal(payload)
        return
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as error:
        raise _blocked(
            "stage_registry_schema_invalid",
            f"阶段注册表未通过 Schema 校验：{error.message}",
            "请按 stages.schema.json 修复阶段注册表",
        ) from error


def _validate_registry_minimal(payload: dict[str, Any]) -> None:
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise _blocked(
            "stage_registry_schema_invalid",
            "阶段注册表缺少 stages 列表",
            "请按 stages.schema.json 修复阶段注册表",
        )
    for stage in stages:
        if not isinstance(stage, dict) or not stage.get("id") or not stage.get("name"):
            raise _blocked(
                "stage_registry_schema_invalid",
                "阶段缺少 id/name 字段",
                "请按 stages.schema.json 修复阶段注册表",
            )
        admission = stage.get("admission")
        if not isinstance(admission, list) or not admission:
            raise _blocked(
                "stage_registry_schema_invalid",
                f"阶段 {stage.get('id')} 缺少 admission 列表",
                "请按 stages.schema.json 修复阶段注册表",
            )
        exit_spec = stage.get("exit")
        if not isinstance(exit_spec, dict) or "next_stage" not in exit_spec:
            raise _blocked(
                "stage_registry_schema_invalid",
                f"阶段 {stage.get('id')} 缺少 exit.next_stage",
                "请按 stages.schema.json 修复阶段注册表",
            )


def _validate_registry_references(payload: dict[str, Any]) -> None:
    """校验阶段 ID 唯一性、next_stage/next_step 引用存在。"""
    stages = payload.get("stages")
    if not isinstance(stages, list):
        return
    ids: set[str] = set()
    for stage in stages:
        stage_id = stage.get("id") if isinstance(stage, dict) else None
        if not isinstance(stage_id, str) or not stage_id:
            continue
        if stage_id in ids:
            raise _blocked(
                "stage_registry_duplicate_id",
                f"阶段 ID 重复：{stage_id}",
                "请保证阶段 ID 全局唯一",
            )
        ids.add(stage_id)
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        exit_spec = stage.get("exit")
        if isinstance(exit_spec, dict):
            next_stage = exit_spec.get("next_stage")
            if next_stage is not None and next_stage not in ids:
                raise _blocked(
                    "stage_registry_next_stage_unknown",
                    f"阶段 {stage.get('id')} 的 exit.next_stage 引用不存在的阶段：{next_stage}",
                    "请修正 next_stage 引用",
                )
        steps = stage.get("steps")
        if not isinstance(steps, list):
            continue
        step_ids: set[str] = set()
        for step in steps:
            if not isinstance(step, dict) or not isinstance(step.get("id"), str):
                continue
            step_id = step["id"]
            if step_id in step_ids:
                raise _blocked(
                    "stage_registry_duplicate_step_id",
                    f"阶段 {stage.get('id')} 内步骤 ID 重复：{step_id}",
                    "请保证阶段内步骤 ID 唯一",
                )
            step_ids.add(step_id)
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_exit = step.get("exit")
            if isinstance(step_exit, dict):
                next_step = step_exit.get("next_step")
                if next_step is not None and next_step not in step_ids:
                    raise _blocked(
                        "stage_registry_next_step_unknown",
                        f"步骤 {stage.get('id')}/{step.get('id')} 的 exit.next_step 引用不存在的步骤：{next_step}",
                        "请修正 next_step 引用",
                    )


def get_stage(registry: dict[str, Any], stage_id: str) -> dict[str, Any]:
    stages = registry.get("stages")
    if not isinstance(stages, list):
        raise _blocked("stage_registry_invalid", "阶段注册表缺少 stages 列表", "请修复阶段注册表")
    for stage in stages:
        if isinstance(stage, dict) and stage.get("id") == stage_id:
            return stage
    raise _blocked(
        "stage_unknown",
        f"阶段不存在：{stage_id}",
        "请使用阶段注册表中已声明的阶段 ID",
    )


def get_stage_steps(registry: dict[str, Any], stage_id: str) -> list[dict[str, Any]]:
    """返回阶段的步骤列表（无 steps 时返回空列表）。"""
    stage = get_stage(registry, stage_id)
    steps = stage.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict)]


def get_step(
    registry: dict[str, Any], stage_id: str, step_id: str
) -> dict[str, Any]:
    """在阶段内查找步骤；不存在时阻断。"""
    steps = get_stage_steps(registry, stage_id)
    for step in steps:
        if step.get("id") == step_id:
            return step
    raise _blocked(
        "stage_step_unknown",
        f"步骤不存在：{stage_id}/{step_id}",
        "请使用阶段注册表中已声明的步骤 ID",
    )


def validate_admission(
    registry: dict[str, Any],
    stage_id: str,
    available: dict[str, Any] | None,
) -> list[str]:
    """校验阶段准入条件。

    返回缺失的准入键列表（空列表 = 全部满足）。
    阶段声明的 admission 键必须在 available 中已具备；
    available 缺省视为空（未提供任务上下文时全部缺失）。
    """
    stage = get_stage(registry, stage_id)
    admission = stage.get("admission")
    if not isinstance(admission, list):
        return []
    available_keys = set(available or {})
    missing = [key for key in admission if key not in available_keys]
    return missing


def resolve_stage_exit(
    registry: dict[str, Any],
    stage_id: str,
    profile_transitions: dict[str, Any] | None,
) -> dict[str, Any]:
    """解析阶段准出：返回 next_stage 与（可选的）Jira 流转信息。

    返回：
    {
      "stage_id": 当前阶段,
      "next_stage": 下一阶段或 None（终态）,
      "jira_transition": 准出声明的 profile.transitions 键或 None,
      "jira_mapping_valid": 该 transition 是否在项目 profile 中可解析,
      "mapping_detail": 解析详情（用于审计/阻断说明）,
    }

    jira_transition 声明了但项目 profile 未配置该 transition 时：
    jira_mapping_valid=False 且不抛异常——由调用方决定阻断。
    """
    stage = get_stage(registry, stage_id)
    exit_spec = stage.get("exit", {})
    next_stage = exit_spec.get("next_stage")
    transition_key = exit_spec.get("jira_transition")
    mapping_detail: dict[str, Any] = {"stage_id": stage_id, "next_stage": next_stage}
    valid = True
    if transition_key is not None:
        mapping_detail["jira_transition"] = transition_key
        if not isinstance(profile_transitions, dict) or transition_key not in profile_transitions:
            valid = False
            mapping_detail["reason"] = (
                "项目 profile.transitions 未配置该 transition 键"
            )
        else:
            mapping_detail["configured"] = profile_transitions[transition_key]
    return {
        "stage_id": stage_id,
        "next_stage": next_stage,
        "jira_transition": transition_key,
        "jira_mapping_valid": valid,
        "mapping_detail": mapping_detail,
    }


def resolve_step_exit(
    registry: dict[str, Any],
    stage_id: str,
    step_id: str,
) -> dict[str, Any]:
    """解析步骤准出（步骤内推进）。

    步骤准出 next_step 为该阶段内的下一步骤；null 表示步骤级终态
    （返回所属阶段的准出，由调用方继续 resolve_stage_exit）。
    """
    step = get_step(registry, stage_id, step_id)
    exit_spec = step.get("exit", {})
    next_step = exit_spec.get("next_step")
    return {
        "stage_id": stage_id,
        "step_id": step_id,
        "next_step": next_step,
        "step_terminal": next_step is None,
    }


def advance_stage(
    registry: dict[str, Any],
    current_stage: str,
    profile_transitions: dict[str, Any] | None,
    *,
    current_step: str | None = None,
    available: dict[str, Any] | None = None,
    require_admission: bool = True,
    stage_timeline: list[dict[str, Any]] | None = None,
    loop_limit: int = 2,
) -> dict[str, Any]:
    """阶段推进（AO-41/AO-42）：准入校验 → 步骤/阶段准出 → 自动流转意图 + 回环门禁。

    返回：
    {
      "admission_ok": 准入是否满足,
      "missing_admission": 缺失的准入键列表,
      "current_stage": 当前阶段,
      "current_step": 当前步骤（若在步骤内）,
      "next_stage": 准出后的下一阶段或 None,
      "next_step": 步骤内下一步或 None,
      "jira_transition": 需要自动流转的 transition 键或 None,
      "jira_mapping_valid": transition 在项目 profile 中是否可解析,
      "terminal": 是否到达终态,
      "loop_blocked": 是否触发回环门禁,
      "next_stage_enter_count": 目标阶段在序列中的出现次数,
      "message": 面向 AI/人工的说明,
    }

    准入不满足且 require_admission=True 时阻断（抛 RuntimeErrorResult）。
    目标下一阶段已出现在 stage_timeline（只记录 AI 处理阶段）中
    ≥ loop_limit 次时，抛 stage_loop_requires_human 阻断转人工决策
    （AO-42 回环门禁）。
    """
    if require_admission:
        missing = validate_admission(registry, current_stage, available)
        if missing:
            raise _blocked(
                "stage_admission_not_met",
                f"阶段 {current_stage} 准入条件不满足，缺失：{', '.join(missing)}",
                "请补齐缺失的任务信息/授权/证据后重新推进",
            )
    else:
        missing = validate_admission(registry, current_stage, available)

    steps = get_stage_steps(registry, current_stage)
    if steps and current_step is not None:
        step_result = resolve_step_exit(registry, current_stage, current_step)
        if not step_result["step_terminal"]:
            return {
                "admission_ok": not missing,
                "missing_admission": missing,
                "current_stage": current_stage,
                "current_step": current_step,
                "next_stage": None,
                "next_step": step_result["next_step"],
                "jira_transition": None,
                "jira_mapping_valid": True,
                "terminal": False,
                "message": f"步骤 {current_stage}/{current_step} 完成，下一步骤：{step_result['next_step']}",
            }
        # 步骤级终态：回到阶段准出
        current_step = None

    exit_result = resolve_stage_exit(registry, current_stage, profile_transitions)
    terminal = exit_result["next_stage"] is None

    # 回环门禁（AO-42）：stage_timeline 只记录 AI 处理阶段（进入序列即
    # 证明是 AI 阶段）。目标下一阶段若已在该序列中出现 ≥ loop_limit 次，
    # 说明该 AI 阶段被反复进入（疑似回环），阻断转人工决策。
    loop_blocked = False
    stage_enter_count = 0
    next_stage = exit_result["next_stage"]
    if next_stage is not None and not terminal:
        stage_enter_count = count_stage_in_timeline(
            stage_timeline or [], next_stage
        )
        if stage_enter_count >= loop_limit:
            loop_blocked = True
            raise _blocked(
                "stage_loop_requires_human",
                (
                    f"阶段 {current_stage} 准出目标 {next_stage} 为自动处理阶段，"
                    f"但该阶段在本任务处理周期已进入 {stage_enter_count} 次"
                    f"（上限 {loop_limit}），疑似回环，禁止自动进入"
                ),
                "请人工决策：确认继续进入 / 调整方案 / 修改流程定义",
            )

    return {
        "admission_ok": not missing,
        "missing_admission": missing,
        "current_stage": current_stage,
        "current_step": current_step,
        "next_stage": exit_result["next_stage"],
        "next_step": None,
        "jira_transition": exit_result["jira_transition"],
        "jira_mapping_valid": exit_result["jira_mapping_valid"],
        "terminal": terminal,
        "loop_blocked": loop_blocked,
        "next_stage_enter_count": stage_enter_count,
        "message": (
            f"阶段 {current_stage} 完成，下一阶段：{exit_result['next_stage']}"
            if exit_result["next_stage"]
            else f"阶段 {current_stage} 完成，到达终态"
        ),
    }


def count_stage_in_timeline(
    sequence: list[dict[str, Any]], stage_id: str
) -> int:
    """统计 stage_id 在 AI 阶段有序序列中出现的次数。"""
    return sum(1 for item in sequence if isinstance(item, dict) and item.get("stage_id") == stage_id)


def append_stage_timeline(
    sequence: list[dict[str, Any]],
    stage_id: str,
    begin: str,
) -> list[dict[str, Any]]:
    """进入 AI 阶段：追加 {stage_id, begin, end: None} 到序列尾部。"""
    return [*sequence, {"stage_id": stage_id, "begin": begin, "end": None}]


def close_stage_timeline(
    sequence: list[dict[str, Any]],
    stage_id: str,
    end: str,
) -> list[dict[str, Any]]:
    """准出 AI 阶段：更新序列中该阶段最后一条（end 为 None 的）的 end 时间。"""
    result = list(sequence)
    for index in range(len(result) - 1, -1, -1):
        item = result[index]
        if (
            isinstance(item, dict)
            and item.get("stage_id") == stage_id
            and item.get("end") is None
        ):
            result[index] = {**item, "end": end}
            break
    return result
