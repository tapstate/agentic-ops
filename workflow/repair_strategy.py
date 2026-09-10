"""缺陷修复方案调优：解析通用目录、项目默认值与任务覆盖。

本模块只返回 advisory 方案上下文。任何配置错误都降级为 warning，不得成为
Workflow、Quality、Gate 或 Authorization 的阻断条件。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from workflow import project_rules, task_store


TASK_CLASS = "defect_fix"
OVERRIDE_FACT = "repair_strategy_override"
CATALOG_PATH = Path("policies/defect-repair-strategies.json")
PROJECT_PATH = "planning.json"
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _catalog(root):
    value = _read_json(Path(root) / CATALOG_PATH)
    if set(value) != {"schema_version", "kind", "enforcement", "task_class", "default", "strategies"}:
        raise ValueError("通用修复策略目录字段无效")
    if (value["schema_version"] != 1 or value["kind"] != "planning_tuning"
            or value["enforcement"] != "advisory" or value["task_class"] != TASK_CLASS):
        raise ValueError("通用修复策略目录版本或任务类型无效")
    strategies = value["strategies"]
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("通用修复策略目录不能为空")
    indexed = {}
    for item in strategies:
        if not isinstance(item, dict) or set(item) != {"id", "label", "description", "guidance"}:
            raise ValueError("通用修复策略条目字段无效")
        strategy_id = item.get("id")
        guidance = item.get("guidance")
        if (not isinstance(strategy_id, str) or not ID_PATTERN.fullmatch(strategy_id)
                or strategy_id in indexed or not isinstance(item.get("label"), str)
                or not item["label"].strip() or not isinstance(item.get("description"), str)
                or not item["description"].strip() or not isinstance(guidance, list)
                or not guidance or any(not isinstance(line, str) or not line.strip() for line in guidance)):
            raise ValueError("通用修复策略条目内容无效")
        indexed[strategy_id] = item
    if value["default"] not in indexed:
        raise ValueError("通用修复策略默认值不存在")
    return value, indexed


def _project_default(root, project, indexed):
    path = project_rules.project_root(root, project) / PROJECT_PATH
    if not path.is_file():
        return None, None
    try:
        value = _read_json(path)
        if set(value) != {"schema_version", "repair_strategy"} or value["schema_version"] != 1:
            raise ValueError("项目规划配置字段无效")
        section = value["repair_strategy"]
        if not isinstance(section, dict) or set(section) != {"default"} or section["default"] not in indexed:
            raise ValueError("项目修复策略默认值无效")
        return section["default"], None
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return None, "项目修复策略配置不可用，已使用公司默认值：%s" % error


def resolve(base, task):
    """返回当前任务的修复策略上下文；从不抛出配置异常。"""
    if task.get("task_class") != TASK_CLASS:
        return {"applicable": False}
    warnings = []
    try:
        root = project_rules.product_root_from_workspace(base)
        project = project_rules.project_from_workspace(base)
        catalog, indexed = _catalog(root)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {"applicable": True, "available": False,
                "warnings": ["修复策略配置不可用，原缺陷流程继续：%s" % error]}
    selected, source = catalog["default"], "company_default"
    project_selected, warning = _project_default(root, project, indexed)
    if warning:
        warnings.append(warning)
    elif project_selected:
        selected, source = project_selected, "project_default"
    override = (task.get("facts") or {}).get(OVERRIDE_FACT)
    if override is not None:
        if isinstance(override, dict) and override.get("id") in indexed:
            selected, source = override["id"], "user_override"
        else:
            warnings.append("任务修复策略覆盖无效，已回退到当前默认值")
    strategy = indexed[selected]
    return {
        "applicable": True,
        "available": True,
        "effective": {"id": selected, "label": strategy["label"], "source": source},
        "planning_guidance": list(strategy["guidance"]),
        "warnings": warnings,
    }


def list_strategies(base):
    root = project_rules.product_root_from_workspace(base)
    catalog, _ = _catalog(root)
    return catalog


def can_change(base, task):
    """策略只在 Q2 尚未确认时直接改变待生成方案。"""
    if task.get("stage") not in ("waiting_takeover", "task_intake", "design_review"):
        return False, "Q2 后调整策略需要重新规划并确认方案；当前状态未改变"
    authorization_path = task_store.authorization_path(base, task["issue_key"])
    if authorization_path.is_file():
        try:
            authorization = _read_json(authorization_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False, "当前授权状态无法核验，未修改策略；原任务流程可继续"
        if authorization.get("status") == "active":
            return False, "当前方案已签发授权；需要重新规划并确认方案后才能采用新策略"
    if task.get("stage") == "design_review":
        try:
            from workflow import quality
            rules = quality.config(base)
            if quality.enabled(task, rules):
                report = quality.report(quality.load(base, task), rules, quality.context(base, task))
                checkpoint = rules["selection_checkpoint"]
                if report["checkpoints"][checkpoint]["reviewed"]:
                    return False, "Q2 已确认；需要重新规划并确认方案后才能采用新策略"
        except (OSError, ValueError, KeyError, TypeError):
            # 调优入口不得把质量读取异常扩散为主流程门禁；状态仍保持不变。
            return False, "无法确认 Q2 是否已完成，未修改策略；原任务流程可继续"
    return True, None
