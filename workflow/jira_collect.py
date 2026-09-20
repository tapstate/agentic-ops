"""项目 Jira 全流程字段采集；只准备决策包和记录确认，不调用 Jira。"""
from __future__ import annotations

import copy
import json
import re
from workflow import project_rules, quality, task_store

CHECKPOINTS = ("intake", "design_review", "acceptance", "pr_review", "transition")


def config(base, task):
    root, project = project_rules.station_context(base)
    profile = project_rules.load_profile(root=root, project=project)
    name = profile.get("jira", {}).get("transitions_config")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+\.json", name):
        raise ValueError("项目缺少 Jira 转换采集配置")
    path = project_rules.project_root(root, project) / name
    value = json.loads(path.read_text())
    if value.get("schema_version") != 1 or task["task_class"] not in value.get("task_classes", {}):
        raise ValueError("Jira 采集配置不支持当前任务类型")
    return value, value["task_classes"][task["task_class"]]


def get(value, path):
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def present(value):
    return value not in (None, "", [], {})


def required(condition, fields, metadata=None):
    if type(condition) is bool:
        return condition
    if not isinstance(condition, dict) or set(condition) != {"field", "equals"}:
        raise ValueError("字段 required_when 只支持布尔或明确字段等值条件")
    value = fields.get(condition["field"])
    if isinstance(value, dict):
        if "value" in value:
            value = value["value"]
        else:
            value = next((v.get("value", v.get("name", v.get("id"))) for v in
                (metadata or {}).get(condition["field"], {}).get("allowedValues", []) if v.get("id") == value.get("id")), value.get("id"))
    return value == condition["equals"]


def valid_value(value, schema, options):
    kind = schema.get("type")
    valid = {"string": isinstance(value, str), "date": isinstance(value, str),
             "number": type(value) in (int, float), "array": isinstance(value, list),
             "user": isinstance(value, dict) and bool(value.get("accountId")),
             "option": isinstance(value, dict), "option-with-child": isinstance(value, dict)}.get(kind, False)
    if not valid or not present(value):
        return False
    if not options:
        return True
    def match(item, choices):
        for choice in choices:
            if not isinstance(item, dict) or not isinstance(choice, dict):
                continue
            if any(item.get(k) is not None and item.get(k) == choice.get(k) for k in ("id", "value", "name")):
                return not item.get("child") or match(item["child"], choice.get("children", []))
        return False
    return all(match(item, options) for item in (value if isinstance(value, list) else [value]))


def collect(base, task, checkpoint, snapshot, proposals=None):
    from workflow import jira_status
    if checkpoint not in CHECKPOINTS:
        raise ValueError("未知 Jira 采集阶段")
    _, fields, status = jira_status.issue_from(snapshot, task["issue_key"])
    if not present(snapshot.get("source_ref")):
        raise ValueError("采集快照缺少来源")
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(snapshot, ensure_ascii=False)):
        raise ValueError("Jira 采集快照含敏感内容，请仅提供必要脱敏字段")
    configuration, spec = config(base, task)
    proposals = proposals or {}
    if not isinstance(proposals, dict):
        raise ValueError("proposals 必须为字段值对象")
    decisions = jira_status.load_state(base, task).get("decisions", {})
    metadata, uses, unknown = {}, {}, []
    transitions = snapshot.get("transitions")
    if not isinstance(transitions, list):
        transitions = []
        unknown.append({"kind": "current_forms", "reason": "未读取当前 transitions.fields"})
    configured = spec["fields"]
    for transition in transitions:
        tid = str(transition.get("id", ""))
        screen = transition.get("fields")
        if not isinstance(screen, dict):
            unknown.append({"transition": tid, "reason": "当前转换表单未回读"})
            continue
        for field, row in screen.items():
            if not isinstance(row, dict):
                raise ValueError("Jira 字段元数据无效")
            existing = metadata.get(field)
            if existing and any(existing.get(k) != row.get(k) for k in ("schema", "allowedValues")):
                unknown.append({"field": field, "due": True, "reason": "不同转换的字段选项或类型冲突，请统一回读"})
            metadata[field] = dict(row, required=bool(row.get("required") or (existing or {}).get("required")))
            uses.setdefault(field, []).append("native:" + tid)
    rows = {}
    for logical, rule in configured.items():
        candidates = [key for key, row in metadata.items() if key == rule.get("jira_id") or row.get("name") in rule["aliases"]]
        key = rule.get("jira_id") or (candidates[0] if len(candidates) == 1 else "unresolved:" + logical)
        if len(candidates) > 1:
            unknown.append({"field": logical, "due": True, "reason": "字段别名不唯一"})
        row = rows.setdefault(key, dict(rule, logical_keys=[], transitions=[]))
        row["logical_keys"].append(logical)
        row["transitions"] += [t["key"] for t in spec["transitions"] if logical in t.get("fields", [])]
    for key, meta in metadata.items():
        if key not in rows:
            rows[key] = {"logical_keys": [], "transitions": [], "collect_at": "transition", "source": [],
                         "required_when": False, "decision_owner": "Engineering DRI", "confirm_when": "value-options-dependencies-owner-change",
                         "depends_on": [], "auto_extract": False, "value_type": (meta.get("schema") or {}).get("type")}
    unknown_keys = set(proposals) - set(rows)
    if unknown_keys:
        raise ValueError("提议值包含未声明字段：" + "、".join(sorted(unknown_keys)))
    result = {"schema_version": 1, "issue_key": task["issue_key"], "run_id": task["run_id"], "checkpoint": checkpoint,
              "source_ref": snapshot["source_ref"], "current_status": status, "existing": [], "confirmed": [],
              "pending": [], "future": [], "unknown": unknown, "transitions": spec["transitions"],
              "coverage": configuration["coverage"], "fields": {}}
    for key, rule in sorted(rows.items()):
        meta = metadata.get(key, {})
        schema = meta.get("schema") or {"type": rule.get("value_type")}
        options = sorted(meta.get("allowedValues", []), key=lambda item: json.dumps(item, sort_keys=True))
        old = decisions.get(key, {})
        current = fields.get(key)
        value = proposals.get(key, current if present(current) else old.get("value"))
        dependencies = {path: get({"facts": task.get("facts", {}), "jira": fields}, path) for path in rule["depends_on"]}
        owner = {"role": rule["decision_owner"], "assignee": (fields.get("assignee") or {}).get("accountId")}
        semantic = {"field": key, "value": value, "schema": schema, "options": options, "dependencies": dependencies,
                    "owner": owner, "confirm_when": rule["confirm_when"]}
        digest = quality.digest(semantic)
        needed = required(rule["required_when"], dict(fields, **proposals), metadata) or meta.get("required") is True
        due = CHECKPOINTS.index(rule["collect_at"]) <= CHECKPOINTS.index(checkpoint) or meta.get("required") is True
        row = dict(semantic, digest=digest, required=needed, collect_at=rule["collect_at"],
                   transitions=sorted(set(rule["transitions"] + uses.get(key, []))),
                   suggestions=[{"source": path, "value": get(task, path)} for path in rule["source"] if present(get(task, path))],
                   auto_extract=rule["auto_extract"], current_jira_value=current,
                   metadata_verified=bool(meta), valid_value=valid_value(value, schema, options))
        result["fields"][key] = row
        if key.startswith("unresolved:"):
            result["unknown"].append({"field": key, "due": due and needed, "reason": "字段 ID 未核验，不猜填；取得元数据后再确认值"})
        elif not due:
            result["future"].append(key)
        elif old.get("digest") == digest and row["valid_value"]:
            result["confirmed"].append(key)
        elif needed or present(value):
            result["pending"].append(key)
        if present(current):
            result["existing"].append(key)
    result["unknown"].append({"kind": "future_forms", "reason": "未来转换表单及服务端 Validator 在实际到达时重新核对；本清单不宣称完整管理员工作流导出。"})
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(result, ensure_ascii=False)):
        raise ValueError("采集结果含敏感内容，请使用脱敏来源引用")
    result["digest"] = quality.digest(result)
    return result


def confirm(base, issue, run_id, checkpoint, snapshot, proposals, field_digests, proof):
    from workflow import jira_status
    quality.check_proof(proof)
    with task_store.task_run_lock(base, issue):
        task = task_store.check_expected_run(base, issue, run_id)
        task_store.require_development(base, task)
        packet = collect(base, task, checkpoint, snapshot, proposals)
        if not isinstance(field_digests, dict) or set(proposals) != set(field_digests):
            raise ValueError("每个确认值必须携带原字段摘要")
        if any(row.get("due") for row in packet["unknown"]):
            raise ValueError("本节点字段元数据未明确，请一次补齐未知项再确认")
        missing = set(packet["pending"]) - set(proposals)
        if missing:
            raise ValueError("本节点需要一次确认全部待决字段：" + "、".join(sorted(missing)))
        state = jira_status.load_state(base, task)
        records = state.setdefault("decisions", {})
        for key, value in proposals.items():
            row = packet["fields"][key]
            if key.startswith("unresolved:") or key in packet["future"] or not row["valid_value"] or field_digests[key] != row["digest"]:
                raise ValueError("字段值、选项、依赖或责任人变化，请重新收集：" + key)
            records[key] = {"digest": row["digest"], "value": copy.deepcopy(value), "proof": proof,
                            "source_ref": snapshot["source_ref"], "checkpoint": checkpoint}
        if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(records, ensure_ascii=False)):
            raise ValueError("字段确认含敏感信息")
        jira_status.save_state(base, task, state)
        return collect(base, task, checkpoint, snapshot, proposals)
