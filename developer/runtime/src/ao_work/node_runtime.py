"""节点注册表与准出映射（AO-39 POC）。

节点处理模式最小闭环：
- 节点注册表声明（developer/standards/nodes/registry.yaml + nodes.schema.json）
- 节点 = 语义化 ID + 准入条件 + 准出决策（next_node + 可选 jira_transition）
- 节点独立于 Jira：节点是内部执行依据；准出声明了 jira_transition 的节点，
  通过项目 profile.transitions 映射回 Jira 状态流转（复用 D-037 严格匹配）

本模块只读资产、不改写任务状态；任务状态配置（node_runtime 实例化）由
后续阶段接入 TaskStore，POC 阶段保持独立轻量。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ao_work.output import EXIT_BLOCKED, EXIT_CAPABILITY_GAP, RuntimeErrorResult

_NODES_RELATIVE = Path("developer") / "standards" / "nodes"
_REGISTRY_FILENAME = "registry.yaml"
_SCHEMA_FILENAME = "nodes.schema.json"


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


def node_registry_dir(install_root: Path) -> Path:
    return install_root / _NODES_RELATIVE


def node_registry_path(install_root: Path) -> Path:
    return node_registry_dir(install_root) / _REGISTRY_FILENAME


def node_schema_path(install_root: Path) -> Path:
    return node_registry_dir(install_root) / _SCHEMA_FILENAME


def load_node_registry(install_root: Path) -> dict[str, Any]:
    """读取并校验节点注册表；缺失或无效时阻断。"""
    registry_path = node_registry_path(install_root)
    if not registry_path.is_file():
        raise _blocked(
            "node_registry_missing",
            "缺少节点注册表",
            "请确认节点注册表已随标准资产安装（developer/standards/nodes/registry.yaml）",
            exit_code=EXIT_CAPABILITY_GAP,
        )
    try:
        payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise _blocked(
            "node_registry_invalid_yaml",
            f"节点注册表 YAML 解析失败：{error}",
            "请修复节点注册表后重试",
        ) from error
    if not isinstance(payload, dict):
        raise _blocked(
            "node_registry_invalid",
            "节点注册表内容不是对象",
            "请修复节点注册表后重试",
        )
    _validate_registry_against_schema(install_root, payload)
    _validate_registry_references(payload)
    return payload


def _validate_registry_against_schema(
    install_root: Path, payload: dict[str, Any]
) -> None:
    """用 nodes.schema.json 校验注册表结构（优先 jsonschema，缺失时 minimal 兜底）。"""
    schema_path = node_schema_path(install_root)
    if not schema_path.is_file():
        raise _blocked(
            "node_schema_missing",
            "缺少节点注册表 Schema",
            "请确认 nodes.schema.json 已随标准资产安装",
            exit_code=EXIT_CAPABILITY_GAP,
        )
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise _blocked(
            "node_schema_invalid",
            f"节点注册表 Schema 解析失败：{error}",
            "请修复 nodes.schema.json 后重试",
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
            "node_registry_schema_invalid",
            f"节点注册表未通过 Schema 校验：{error.message}",
            "请按 nodes.schema.json 修复节点注册表",
        ) from error


def _validate_registry_minimal(payload: dict[str, Any]) -> None:
    nodes = payload.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise _blocked(
            "node_registry_schema_invalid",
            "节点注册表缺少 nodes 列表",
            "请按 nodes.schema.json 修复节点注册表",
        )
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or not node.get("name"):
            raise _blocked(
                "node_registry_schema_invalid",
                "节点缺少 id/name 字段",
                "请按 nodes.schema.json 修复节点注册表",
            )
        admission = node.get("admission")
        if not isinstance(admission, list) or not admission:
            raise _blocked(
                "node_registry_schema_invalid",
                f"节点 {node.get('id')} 缺少 admission 列表",
                "请按 nodes.schema.json 修复节点注册表",
            )
        exit_spec = node.get("exit")
        if not isinstance(exit_spec, dict) or "next_node" not in exit_spec:
            raise _blocked(
                "node_registry_schema_invalid",
                f"节点 {node.get('id')} 缺少 exit.next_node",
                "请按 nodes.schema.json 修复节点注册表",
            )


def _validate_registry_references(payload: dict[str, Any]) -> None:
    """校验节点 ID 唯一性与 exit.next_node 引用存在。"""
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return
    ids: set[str] = set()
    for node in nodes:
        node_id = node.get("id") if isinstance(node, dict) else None
        if not isinstance(node_id, str) or not node_id:
            continue
        if node_id in ids:
            raise _blocked(
                "node_registry_duplicate_id",
                f"节点 ID 重复：{node_id}",
                "请保证节点 ID 全局唯一",
            )
        ids.add(node_id)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        exit_spec = node.get("exit")
        if not isinstance(exit_spec, dict):
            continue
        next_node = exit_spec.get("next_node")
        if next_node is not None and next_node not in ids:
            raise _blocked(
                "node_registry_next_node_unknown",
                f"节点 {node.get('id')} 的 exit.next_node 引用不存在的节点：{next_node}",
                "请修正 next_node 引用",
            )


def get_node(registry: dict[str, Any], node_id: str) -> dict[str, Any]:
    nodes = registry.get("nodes")
    if not isinstance(nodes, list):
        raise _blocked("node_registry_invalid", "节点注册表缺少 nodes 列表", "请修复节点注册表")
    for node in nodes:
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    raise _blocked(
        "node_unknown",
        f"节点不存在：{node_id}",
        "请使用节点注册表中已声明的节点 ID",
    )


def resolve_node_exit(
    registry: dict[str, Any],
    node_id: str,
    profile_transitions: dict[str, Any] | None,
) -> dict[str, Any]:
    """解析节点准出：返回 next_node 与（可选的）Jira 流转信息。

    返回：
    {
      "node_id": 当前节点,
      "next_node": 下一步节点或 None（终态）,
      "jira_transition": 准出声明的 profile.transitions 键或 None,
      "jira_mapping_valid": 该 transition 是否在项目 profile 中可解析,
      "mapping_detail": 解析详情（用于审计/阻断说明）,
    }

    jira_transition 声明了但项目 profile 未配置该 transition 时：
    jira_mapping_valid=False 且不抛异常——由调用方决定阻断（POC 先返回
    解析结果，自动流转门禁在后续阶段接入既有 transition 命令）。
    """
    node = get_node(registry, node_id)
    exit_spec = node.get("exit", {})
    next_node = exit_spec.get("next_node")
    transition_key = exit_spec.get("jira_transition")
    mapping_detail: dict[str, Any] = {"node_id": node_id, "next_node": next_node}
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
        "node_id": node_id,
        "next_node": next_node,
        "jira_transition": transition_key,
        "jira_mapping_valid": valid,
        "mapping_detail": mapping_detail,
    }
