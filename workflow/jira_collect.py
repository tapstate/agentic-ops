"""项目 Jira 全流程字段采集；只准备决策包和记录确认，不调用 Jira。"""
from __future__ import annotations

import copy
import json
import re
from workflow import project_rules, quality, task_store

CHECKPOINTS = ("intake", "design_review", "acceptance", "pr_review", "transition")


def field_material(base, task, logical):
    from workflow.quality_write import jira_field_body
    return quality.digest([jira_field_body(base, task, logical),
        [{k: r.get(k) for k in ("repository", "work_branch", "base_sha")} for r in task.get("repositories", [])]])


def field_value(fields, key):
    """区分明确空值与连接器仅返回字段 ID；不猜测未读取值。"""
    if key in fields:
        return True, fields[key]
    for row in fields.get("customFields", {}).values():
        if isinstance(row, dict) and row.get("id") == key and "value" in row:
            return True, row["value"]
    return False, None



def config(base, task):
    root, project = project_rules.station_context(base)
    profile = project_rules.load_profile(root=root, project=project)
    name = profile.get("jira", {}).get("transitions_config")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+\.json", name):
        raise ValueError("项目缺少 Jira 转换采集配置")
    path = project_rules.project_root(root, project) / name
    value = json.loads(path.read_text())
    default_class = profile.get("jira", {}).get("routing", {}).get("default_task_class")
    if value.get("schema_version") == 1 and default_class and task["task_class"] == default_class:
        return value, {"fields": {}, "transitions": []}
    if value.get("schema_version") != 1 or task["task_class"] not in value.get("task_classes", {}):
        raise ValueError("Jira 采集配置不支持当前任务类型")
    return value, value["task_classes"][task["task_class"]]


def get(value, path):
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def present(value):
    return value not in (None, "", [], {})


def require_safe(spec, value, label):
    """完整扫描当前对象，仅汇总有界、安全的定位诊断。"""
    findings = []
    key_number = 0

    def record(path, hits):
        for reason in dict.fromkeys(hit[2] for hit in hits):
            # 规则来自项目配置，但其说明也不能成为秘密回显渠道。
            safe_reason = reason if (len(reason) <= 120 and not
                project_rules.scan_sensitive(spec, reason)) else "命中敏感规则"
            findings.append((path, safe_reason))

    def visit(node, path):
        nonlocal key_number
        if isinstance(node, dict):
            for key, child in node.items():
                key_number += 1
                hits = project_rules.scan_sensitive(spec, str(key))
                safe_key = (key if isinstance(key, str) and len(key) <= 64 and
                            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) and not hits
                            else "<key#%d>" % key_number)
                child_path = path + "." + safe_key
                record(child_path + "（键）", hits)
                visit(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, path + "[" + str(index) + "]")
        elif isinstance(node, str):
            record(path, project_rules.scan_sensitive(spec, node))

    try:
        visit(value, "$")
        # 保留整体检查；不能用叶子扫描替代原有阻断判定。
        whole_hits = project_rules.scan_sensitive(spec, json.dumps(value, ensure_ascii=False))
    except (RecursionError, TypeError, ValueError):
        raise ValueError(label + "敏感检查未完成，仅部分覆盖；请检查输入结构后重试") from None
    if whole_hits and not findings:
        record("$（整体检查，无法定位叶子）", whole_hits)
    if findings:
        shown = findings[:50]
        lines = []
        for number, (path, reason) in enumerate(shown, 1):
            if len(path) > 240:
                path = path[:100] + "…（路径省略）…" + path[-100:]
            lines.append("%d. 位置：%s；原因：%s" % (number, path, reason))
        coverage = ("生成结果尚未检查" if label == "Jira 采集快照" else
                    "Jira 采集快照已通过检查" if label == "采集结果" else "仅覆盖当前对象")
        summary = ("%s含敏感内容；当前对象已完整扫描，共 %d 项，展示 %d 项，未展示 %d 项；%s。" %
                   (label, len(findings), len(shown), len(findings) - len(shown), coverage))
        guidance = "请提供脱敏来源引用后重新检查。"
        if len(findings) > len(shown):
            guidance += "信息量过大，修正本批后重新检查获取剩余问题。"
        raise ValueError(summary + "\n" + "\n".join(lines) + "\n" + guidance)


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
            identifiers = [k for k in ("id", "value", "name") if item.get(k) is not None]
            if identifiers and all(item[k] == choice.get(k) for k in identifiers):
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
    admission = project_rules.load_admission(station=base)
    require_safe(admission, snapshot, "Jira 采集快照")
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
        observed, current = field_value(fields, key)
        value = proposals.get(key, current if present(current) else old.get("value"))
        dependencies = {path: get({"facts": task.get("facts", {}), "jira": fields}, path) for path in rule["depends_on"]}
        owner = {"role": rule["decision_owner"], "assignee": (fields.get("assignee") or {}).get("accountId")}
        semantic = {"field": key, "value": value, "schema": schema, "options": options, "dependencies": dependencies,
                    "owner": owner, "confirm_when": rule["confirm_when"]}
        if due_field := (set(rule["logical_keys"]) & {"issue_analysis", "fix_details"}):
            semantic["material"] = {name: field_material(base, task, name) for name in sorted(due_field)}
        digest = quality.digest(semantic)
        needed = required(rule["required_when"], dict(fields, **proposals), metadata) or meta.get("required") is True
        due = CHECKPOINTS.index(rule["collect_at"]) <= CHECKPOINTS.index(checkpoint) or meta.get("required") is True
        row = dict(semantic, digest=digest, required=needed, collect_at=rule["collect_at"],
                   transitions=sorted(set(rule["transitions"] + uses.get(key, []))),
                   suggestions=[{"source": path, "value": get(task, path)} for path in rule["source"] if present(get(task, path))],
                   auto_extract=rule["auto_extract"], current_jira_value=current,
                   current_jira_observed=observed, metadata_verified=bool(meta), valid_value=valid_value(value, schema, options))
        if due and set(rule["logical_keys"]) & {"issue_analysis", "fix_details"}:
            from workflow.quality_write import jira_field_body
            for logical in rule["logical_keys"]:
                if logical in ("issue_analysis", "fix_details"):
                    proposal = jira_field_body(base, task, logical)
                    if proposal:
                        row["suggestions"] = [{"source": "plan_proposal" if logical == "issue_analysis" else "actual_verification",
                                                "value": proposal}]
        if due and set(rule["logical_keys"]) & {"issue_analysis", "fix_details"}:
            row["publication_required"] = True
            row["sync_status"] = "unread" if not observed else (
                "matches_confirmed" if old.get("digest") == digest and current == value and row["valid_value"] else "needs_review")
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
    require_safe(admission, result, "采集结果")
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
                            "source_ref": snapshot["source_ref"], "checkpoint": checkpoint, "material": row.get("material", {}), "before_observed": row["current_jira_observed"],
                            "before": copy.deepcopy(row["current_jira_value"])}
        if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(records, ensure_ascii=False)):
            raise ValueError("字段确认含敏感信息")
        jira_status.save_state(base, task, state)
        return collect(base, task, checkpoint, snapshot, proposals)


def prepare_fields(base, task, checkpoint, document):
    """固定已确认的完整字段值与写前值；原生写入仍由 Agent 执行。"""
    from workflow import jira_status
    packet = collect(base, task, checkpoint, document["snapshot"], document.get("proposals", {}))
    proposals = document.get("proposals", {})
    if not proposals or any(key not in packet["confirmed"] for key in proposals):
        raise ValueError("先复用有效决定确认完整字段值，再准备写入")
    state = jira_status.load_state(base, task)
    writes = state.setdefault("field_writes", {})
    key = document["operation_id"]
    if not isinstance(key, str) or not key.strip():
        raise ValueError("字段写入必须有稳定 operation_id")
    if key in writes:
        if writes[key]["fields"] != proposals:
            raise ValueError("原 operation_id 已绑定其它正文，先核对原操作")
        return dict(writes[key], repeated=True, native_request=None)
    if jira_status.pending_field_writes(base, task):
        raise ValueError("已有字段写入结果未知，先回读原操作")
    for field in proposals:
        if not packet["fields"][field]["current_jira_observed"]:
            raise ValueError("字段未完整读取，不能把省略值当空值覆盖：" + field)
    for field in proposals:
        decision = state["decisions"][field]
        if not decision.get("before_observed"):
            raise ValueError("原确认未读取完整字段；先合并人工内容并重新确认")
        if decision.get("before") != packet["fields"][field]["current_jira_value"]:
            raise ValueError("确认后远端字段已变化，先合并人工修改并重新确认")
    record = {"status": "intent", "operation_id": key, "checkpoint": checkpoint,
              "fields": copy.deepcopy(proposals), "before": {k: packet["fields"][k]["current_jira_value"] for k in proposals},
              "digests": {k: packet["fields"][k]["digest"] for k in proposals},
              "source_ref": document["snapshot"]["source_ref"]}
    require_safe(project_rules.load_admission(station=base), record, "字段发送意图")
    writes[key] = record
    jira_status.save_state(base, task, state)
    return dict(record, native_request={"issue_key": task["issue_key"], "fields": proposals})


def field_matches(expected, observed, schema, field):
    """只接纳已知 Jira 字段的原生表示；不剥离任意富文本结构。"""
    if expected == observed:
        return True
    kind = schema.get("type")
    if kind == "string" and isinstance(expected, str) and isinstance(observed, dict):
        if set(observed) != {"type", "version", "content"} or observed.get("type") != "doc" or observed.get("version") != 1:
            return False
        if not isinstance(observed["content"], list):
            return False
        paragraphs = []
        for paragraph in observed.get("content", []):
            if not isinstance(paragraph, dict) or set(paragraph) - {"type", "content"} or paragraph.get("type") != "paragraph" or not isinstance(paragraph.get("content", []), list):
                return False
            parts = []
            for node in paragraph.get("content", []):
                if not isinstance(node, dict):
                    return False
                if node.get("type") == "text" and set(node) == {"type", "text"} and isinstance(node["text"], str):
                    parts.append(node["text"])
                elif node == {"type": "hardBreak"}:
                    parts.append("\n")
                else:
                    return False
            paragraphs.append("".join(parts))
        return expected == "\n\n".join(paragraphs)
    if kind == "array" and isinstance(expected, list) and isinstance(observed, list):
        item = schema.get("items")
        if field in ("fixVersions", "versions"):
            item = "version"
        if item not in ("user", "version", "option") or len(expected) != len(observed):
            return False
        return all(field_matches(a, b, {"type": item}, field) for a, b in zip(expected, observed))
    if kind not in ("user", "version", "option", "option-with-child"):
        return False
    identity = "accountId" if kind == "user" else "id"
    if not isinstance(expected, dict) or not isinstance(observed, dict) or not expected.get(identity):
        return False
    if expected.get(identity) != observed.get(identity):
        return False
    # 仅忽略回读补充的展示信息；拟写内容中任何非展示约束仍须一致。
    display = {"self", "name", "value", "displayName", "emailAddress", "active", "avatarUrls", "timeZone", "accountType", "archived", "released"}
    if any(key not in display | {identity, "child"} for key in expected):
        return False
    if any(key in expected and expected[key] != observed.get(key) for key in display):
        return False
    if kind == "option-with-child":
        if expected.get("child") is None or observed.get("child") is None:
            return expected.get("child") == observed.get("child")
        return field_matches(expected["child"], observed["child"], {"type": "option"}, field)
    return "child" not in expected and "child" not in observed


def readback_fields(base, task, document):
    from workflow import jira_status
    state = jira_status.load_state(base, task)
    record = state.get("field_writes", {}).get(document["operation_id"])
    if not record:
        raise ValueError("没有原字段写入意图")
    original = copy.deepcopy(record)
    snapshot = document["snapshot"]
    _, fields, _ = jira_status.issue_from(snapshot, task["issue_key"])
    if not snapshot.get("source_ref"):
        raise ValueError("字段回读缺少来源")
    require_safe(project_rules.load_admission(station=base), snapshot, "字段回读")
    if document.get("result") == "not_written":
        evidence = snapshot.get("operation_result", {})
        if (record["status"] != "intent" or evidence.get("operation_id") != document["operation_id"]
                or evidence.get("effect") != "not_written" or not evidence.get("source_ref")
                or any(not field_value(fields, key)[0] or field_value(fields, key)[1] != value
                       for key, value in record["before"].items())):
            raise ValueError("未写入必须有原操作明确拒绝依据及未变化的写前值，不能由未查到推断")
        record.update(status="not_written", readback_ref=snapshot["source_ref"])
    else:
        packet = collect(base, task, record["checkpoint"], snapshot, record["fields"])
        if any(not field_value(fields, key)[0] or not field_matches(value, field_value(fields, key)[1],
                   packet["fields"][key]["schema"], key)
               for key, value in record["fields"].items()):
            raise ValueError("字段回读不完整或正文不匹配；保留原意图，禁止盲目重发")
        record.update(status="verified", readback_ref=snapshot["source_ref"])
    jira_status.save_state(base, task, state, recovery=("field:" + document["operation_id"], original))
    return record


def field_sync_actions(base, task):
    """只读投影：确认不是远端写入；代码或方案变化后重新整理全文。"""
    from workflow import jira_status
    _, spec = config(base, task)
    state = jira_status.load_state(base, task)
    writes = state.get("field_writes", {})
    result = [{"id": "fields:" + key, "kind": "jira_fields", "status": "unknown",
               "next_action": "readback_fields", "operation_id": key, "fields": row["fields"]}
              for key, row in writes.items() if row["status"] == "intent"]
    for logical in ("issue_analysis", "fix_details"):
        rule = spec["fields"].get(logical)
        if not rule:
            continue
        if logical == "issue_analysis":
            due = bool(task.get("facts", {}).get("implementation_plan") or task.get("facts", {}).get("fix_plan"))
        else:
            due = task.get("stage") in ("pr_review", "ci_validation", "completed")
            if task.get("stage") == "implementation":
                model = quality.replay(quality.load(base, task))
                due = any(item["plan"]["timing"] == "after_fix" and item["executions"]
                          for item in model["items"].values()) or any(
                              "local" in entries for entries in model.get("verification", {}).values())
        if not due:
            continue
        key = rule["jira_id"]
        decision = state.get("decisions", {}).get(key, {})
        verified = decision.get("material", {}).get(logical) == field_material(base, task, logical) and any(row["status"] == "verified" and row["digests"].get(key) == decision.get("digest")
                       and row["fields"].get(key) == decision.get("value") for row in writes.values())
        if not verified and not any(key in row.get("fields", {}) for row in result):
            result.append({"id": "field:" + key, "kind": "jira_field", "status": "deferred",
                           "next_action": "collect", "checkpoint": rule["collect_at"], "field": key,
                           "reason": logical + " 尚无已确认全文的写后回读；合并人工内容后同步"})
    return result
