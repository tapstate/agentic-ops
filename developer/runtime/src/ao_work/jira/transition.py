from __future__ import annotations

from typing import Any

# developer 面 Jira 状态流转的 D-037 匹配器（与 maintainer 面 ao_maint 独立实现，
# 不跨面导入；规则保持一致：稳定 ID 优先、名称兜底需唯一且 from/to 匹配、
# 禁止模糊匹配）。

COMPLETED_STAGE = "completed"


def from_ok(
    spec: dict[str, Any], current_status: str, current_status_id: str = ""
) -> bool:
    from_status_ids = spec.get("from_status_ids")
    if isinstance(from_status_ids, list) and from_status_ids:
        # 已达目标状态仍是幂等 no_op；ID 约束不能把这一安全路径退化掉。
        return current_status_id in from_status_ids or current_status_id == str(
            spec.get("to_status_id", "")
        ).strip()
    from_states = spec.get("from")
    if not isinstance(from_states, list) or not from_states:
        return True
    # 已达成目标状态也视为可计划（幂等 no_op 场景）
    if current_status == str(spec.get("to", "")).strip():
        return True
    return current_status in from_states


def to_ok(spec: dict[str, Any], to_status: str, to_status_id: str = "") -> bool:
    expected_id = str(spec.get("to_status_id", "")).strip()
    if expected_id:
        return to_status_id == expected_id
    expected = str(spec.get("to", "")).strip()
    if not expected:
        return True
    return to_status == expected


def match_transition(
    current_status: str,
    available: list[dict[str, str]],
    mapping: dict[str, Any],
    *,
    current_status_id: str = "",
    target_status: str | None = None,
    target_key: str | None = None,
) -> tuple[str, str, str] | None:
    """D-037 严格匹配：稳定 ID 优先，名称兜底需唯一且 from/to 匹配，禁止模糊匹配。

    返回 (transition_id, transition_name, to_status)；任何歧义、目标不符、
    当前不可用都返回 None（调用方阻断）。配置了 id 的候选若可用但 from/to
    不匹配，立即返回 None，不降级到名称兜底。
    """
    entries = mapping.get("transitions", {}) if isinstance(mapping, dict) else {}
    candidates: list[dict[str, Any]] = []
    if target_key is not None:
        spec = entries.get(target_key)
        if isinstance(spec, dict):
            candidates.append(spec)
    elif target_status is not None:
        for spec in entries.values():
            if (
                isinstance(spec, dict)
                and str(spec.get("to", "")).strip() == target_status
            ):
                candidates.append(spec)
    if not candidates:
        return None
    resolved: list[tuple[str, str, str]] = []
    for spec in candidates:
        spec_id = str(spec.get("id", "")).strip()
        spec_name = str(spec.get("name", "")).strip()
        if spec_id:
            found = [item for item in available if item["id"] == spec_id]
            if not found:
                continue
            item = found[0]
            if from_ok(spec, current_status, current_status_id) and to_ok(
                spec, item["to"], str(item.get("to_status_id", ""))
            ):
                resolved.append((item["id"], item["name"], item["to"]))
            else:
                return None
        elif spec_name:
            same = [item for item in available if item["name"] == spec_name]
            if (
                len(same) == 1
                and from_ok(spec, current_status, current_status_id)
                and to_ok(spec, same[0]["to"], str(same[0].get("to_status_id", "")))
            ):
                resolved.append((same[0]["id"], same[0]["name"], same[0]["to"]))
    unique = set(resolved)
    if len(unique) == 1:
        return next(iter(unique))
    return None


def completed_stage_for(
    status_mapping: dict[str, str],
    target_status: str,
    *,
    status_id_mapping: dict[str, str] | None = None,
    target_status_id: str = "",
) -> str | None:
    """目标状态若映射到 completed stage，返回 stage 名；否则 None。"""
    if target_status_id and isinstance(status_id_mapping, dict):
        stage = status_id_mapping.get(target_status_id, "")
        if stage == COMPLETED_STAGE:
            return stage
    stage = status_mapping.get(target_status, "") if isinstance(status_mapping, dict) else ""
    if stage == COMPLETED_STAGE:
        return stage
    return None


def adaptation_material(
    issue_key: str,
    project_key: str,
    current_status: str,
    available: list[dict[str, str]],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    """快速适配路径：输出可直接照抄的对照材料，适配发生在配置层。"""
    return {
        "issue_key": issue_key,
        "project_key": project_key,
        "current_status": current_status,
        "available_transitions": available,
        "configured_transitions": (
            mapping.get("transitions", {}) if isinstance(mapping, dict) else {}
        ),
        "configured_statuses": (
            mapping.get("statuses", {}) if isinstance(mapping, dict) else {}
        ),
        "configured_status_ids": (
            mapping.get("status_ids", {}) if isinstance(mapping, dict) else {}
        ),
        "guidance": (
            "请按对照材料在 developer/standards/projects/<project>/profile.yaml 的 "
            "transitions 节补齐映射后重新 plan；不要临场猜测 Jira 状态或 transition"
        ),
    }
