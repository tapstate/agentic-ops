#!/usr/bin/env python3
"""接管版本水印的确定性意图、回读与单次结果记录。

实际 Jira 写入由 Agent 原生工具执行。本模块只准备精确字段载荷，并在回读确认
前保留同步待办；归属未通过时停止依赖归属的写入，通知和业务状态另行记录。
"""
from __future__ import annotations

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))


import argparse
import copy
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bootstrap import product_version  # noqa: E402
from workflow import project_rules, task_store, sync_recovery, station_context, jira_ownership, station_takeover  # noqa: E402


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def payload_digest(field_id, value):
    document = json.dumps(
        {"field_id": field_id, "value": value}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


def state_path(base, task):
    suffix = hashlib.sha256(task["run_id"].encode("utf-8")).hexdigest()[:24]
    return task_store.task_directory(base, task["issue_key"]) / ("jira-watermark-%s.json" % suffix)


def marker_digest(fields):
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def field_observations(snapshot, rules):
    """读取原生字段及连接器映射；补查保留原响应，不把省略解释为空。"""
    fields, _ = issue_from(snapshot, snapshot["issue"]["key"])
    ids = [rules["field_id"], *rules["marker_fields"].values()]
    result = {}
    for key in ids:
        candidates = []
        if key in fields:
            candidates.append((fields[key], snapshot.get("source_ref", "")))
        mapped = fields.get("customFields", {})
        entries = mapped.values() if isinstance(mapped, dict) else mapped if isinstance(mapped, list) else []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == key:
                candidates.append((entry, snapshot.get("source_ref", "")))
        observations = []
        for value, source in candidates:
            if isinstance(value, dict) and value.get("id") == key:
                if "value" not in value:
                    observations.append({"state": "unreturned", "source_ref": source})
                    continue
                value = value["value"]
            observations.append({"state": "empty" if value is None else "value", "value": value, "source_ref": source})
        known = [row for row in observations if "value" in row]
        if known and any(row["value"] != known[0]["value"] for row in known):
            raise ValueError("Jira 字段原生值与映射值冲突：" + key)
        result[key] = known[0] if known else {"state": "unreturned", "source_ref": snapshot.get("source_ref", "")}
    known_values = {key: row["value"] for key, row in result.items() if "value" in row}
    # 补查只补缺项；冲突不能靠输入顺序覆盖，更不能混成其它工位的旧快照。
    for read in snapshot.get("field_readbacks", []):
        if (not isinstance(read, dict) or not isinstance(read.get("source_ref"), str)
                or not read["source_ref"].strip() or read.get("issue_key") != snapshot["issue"]["key"]):
            raise ValueError("字段补查必须绑定同一任务和原生 source_ref")
        key = read.get("field_id")
        if key not in result:
            raise ValueError("字段补查不属于接管标记")
        if read.get("http_status") in (401, 403):
            result[key] = {"state": "forbidden", "source_ref": read["source_ref"]}
            continue
        issue = read.get("issue", {})
        if (read.get("http_status") != 200 or issue.get("key") != read["issue_key"]
                or not isinstance(issue.get("fields"), dict)):
            raise ValueError("字段补查需要原生 HTTP 200 issue/fields 或明确 401/403")
        if key not in issue["fields"]:
            result[key] = {"state": "unreturned", "source_ref": read["source_ref"]}
            continue
        value = issue["fields"][key]
        if isinstance(value, dict):
            raise ValueError("接管文本字段补查必须保留原生值，不能用 metadata 证明空值")
        if key in known_values and known_values[key] != value:
            raise ValueError("Jira 字段补查与已有观察冲突；重新完整回读四项字段：" + key)
        known_values[key] = value
        result[key] = {"state": "empty" if value is None else "value", "value": value, "source_ref": read["source_ref"]}
    return result


class FieldReadError(ValueError):
    def __init__(self, observations):
        self.observations = observations
        super().__init__("Jira 四项接管字段回读不完整；缺字段或无权限不能视为空值")


def marker_values(fields, rules, snapshot=None):
    snapshot = snapshot or {"issue": {"key": "observation", "fields": dict(fields, issuetype={"id": "observation"})}}
    observations = field_observations(snapshot, rules)
    if any(row["state"] not in ("value", "empty") for row in observations.values()):
        raise FieldReadError(observations)
    values = {key: row["value"] for key, row in observations.items()}
    if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in values.values()):
        raise ValueError("Jira 接管字段类型或内容无效")
    value = values[rules["marker_fields"]["begin_at"]]
    if value is not None:
        marker_time(value)
    return values


def marker_time(value):
    try:
        result = datetime.fromisoformat(re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00")))
        if result.tzinfo is None:
            raise ValueError()
        return result
    except (TypeError, AttributeError, ValueError):
        raise ValueError("AgenticOps Begin At 必须是带时区的日期时间")


def markers_equal(actual, expected, time_field):
    """回读按 UTC 分钟核验接管时间，不改写原时间或写入载荷。"""
    if set(actual) != set(expected):
        return False
    return all((marker_time(actual[k]).astimezone(timezone.utc).replace(second=0, microsecond=0)
                == marker_time(v).astimezone(timezone.utc).replace(second=0, microsecond=0)
                if k == time_field and actual[k] else actual[k] == v)
               for k, v in expected.items())


def validate_record(record):
    """只接受可证明一次精确写入或回读结果的水印记录。"""
    required = {
        "at", "source_ref", "issue_key", "field_id", "field_name", "logical_key", "issue_type_id",
        "version", "write_mode", "payload_digest", "outcome", "reason",
    }
    if not isinstance(record, dict) or not required.issubset(record):
        raise ValueError("Jira 接管水印记录缺少必要字段")
    if (not all(isinstance(record[key], str) and record[key] for key in required) or
            record["logical_key"] != "agenticops_version" or
            record["write_mode"] != "overwrite" or
            not record["field_id"].startswith("customfield_") or
            (marker_digest(record.get("expected_fields", {})) if record.get("marker_version") == 2
             else payload_digest(record["field_id"], record["version"])) != record["payload_digest"]):
        raise ValueError("Jira 接管水印记录内容无效")
    if record.get("marker_version") == 2:
        fields = record.get("expected_fields", {})
        mapping = record.get("marker_fields", {})
        actor = record.get("actor", {})
        if (set(mapping) != {"begin_at", "station", "run_id"} or len(fields) != 4
                or set(fields) != {record["field_id"], *mapping.values()}
                or any(not isinstance(v, str) or not v.strip() for v in fields.values())
                or fields[record["field_id"]] != record["version"]
                or not all(isinstance(actor.get(k), str) and actor[k].strip() for k in ("accountId", "displayName", "source_ref"))):
            raise ValueError("Jira 四项接管标记或登录用户记录无效")
        marker_time(fields[mapping["begin_at"]])
    if record.get("verified_ref") is not None and (not isinstance(record["verified_ref"], str) or not record["verified_ref"].strip()):
        raise ValueError("Jira 标记首次核验来源无效")
    if 'ownership' in record:
        owner = record['ownership']
        if (not isinstance(owner, dict) or set(owner) != {'site', 'issue_id', 'session_id'}
                or not isinstance(owner['site'], str) or not owner['site'].startswith('https://')
                or not isinstance(owner['issue_id'], str) or not owner['issue_id'].isdigit()
                or not isinstance(owner['session_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', owner['session_id'])):
            raise ValueError('四项标记归属回读身份无效')
    outcome = record["outcome"]
    if outcome not in ("ready", "verified", "unknown", "failed", "stale"):
        raise ValueError("Jira 接管水印记录 outcome 无效")
    if outcome == "ready":
        if record.get("verified_ref"):
            raise ValueError("已核验过的标记不能重新成为待写意图")
        expected = {"issue_key": record.get("issue_key"), "fields": record.get("expected_fields", {record["field_id"]: record["version"]})}
        native_request = record.get("native_request")
        if (not isinstance(native_request, dict) or
                native_request.get("fields") != expected["fields"] or
                not isinstance(native_request.get("issue_key"), str) or
                not native_request["issue_key"]):
            raise ValueError("Jira 接管水印待写意图无效")
    else:
        if (not isinstance(record.get("completed_at"), str) or not record["completed_at"] or
                not isinstance(record.get("readback_ref"), str) or not record["readback_ref"]):
            raise ValueError("Jira 接管水印回读记录无效")
        if outcome == "verified" and (not markers_equal(record.get("readback_fields", {}), record["expected_fields"], record["marker_fields"]["begin_at"])
                if record.get("marker_version") == 2 else record.get("readback_value") != record["version"]):
            raise ValueError("Jira 接管水印 verified 回读值不一致")


def load_state(base, task):
    path = state_path(base, task)
    if not path.is_file():
        return {"schema_version": 1, "issue_key": task["issue_key"], "run_id": task["run_id"]}
    document = json.loads(path.read_text(encoding="utf-8"))
    if (document.get("schema_version") != 1 or document.get("issue_key") != task["issue_key"] or
            document.get("run_id") != task["run_id"]):
        raise ValueError("Jira 接管水印记录损坏或不属于当前 task/run")
    if "legacy_watermark" in document:
        validate_record(document["legacy_watermark"])
    if "watermark" in document:
        validate_record(document["watermark"])
        if document["watermark"]["issue_key"] != task["issue_key"]:
            raise ValueError("Jira 接管水印记录任务号不一致")
    return document


def save_state(base, task, state, recovery=None):
    with task_store.task_run_lock(base, task["issue_key"], ownership=False):
        current = task_store.check_expected_run(base, task["issue_key"], task["run_id"])
        if recovery is None:
            station_takeover.require_external(base, current)
        else:
            sync_recovery.require_receipt(base, current)
            if load_state(base, current).get("watermark") != recovery:
                raise ValueError("原水印意图已变化，拒绝过期回执")
            if recovery.get("verified_ref") and state["watermark"].get("verified_ref") != recovery["verified_ref"]:
                raise ValueError("不得撤销或改写首次核验来源")
            sync_recovery.validate_update(recovery, state["watermark"],
                {"outcome", "reason", "completed_at", "readback_ref", "readback_value", "guidance", "native_request", "message", "readback_fields", "verified_ref", "ownership"})
        if task.get("_revision") != current["_revision"]:
            raise ValueError("工位 revision 已变化，拒绝过期 Jira 水印回执")
        task_store._write_json_atomic(state_path(base, task), state)


def read_input(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("无法读取 Jira 水印回读快照：%s" % error) from error
    if not isinstance(document, dict) or not isinstance(document.get("source_ref"), str) or not document["source_ref"].strip():
        raise ValueError("Jira 水印快照必须包含可回查 source_ref")
    return document


def issue_from(snapshot, issue_key):
    issue = snapshot.get("issue")
    if not isinstance(issue, dict) or str(issue.get("key", "")).upper() != issue_key:
        raise ValueError("Jira 水印快照不是当前任务")
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Jira 水印快照缺少 issue.fields")
    issue_type = fields.get("issuetype")
    if not isinstance(issue_type, dict) or not str(issue_type.get("id") or "").strip():
        raise ValueError("Jira 水印快照缺少 issue.fields.issuetype.id")
    return fields, str(issue_type["id"])


def config(base):
    profile = project_rules.load_profile(station=base)
    return profile["jira"]["takeover_watermark"]


@sync_recovery.local_locked
def prepare(base, issue_key, snapshot):
    task = task_store.read_task(base, issue_key)
    station_takeover.require_external(base, task)
    state = load_state(base, task)
    previous = state.get("watermark")
    if config(base).get("schema_version") == 2:
        if previous and previous.get("marker_version") != 2:
            if previous["outcome"] != "verified" and previous.get("readback_value") != previous["version"]:
                raise ValueError("先 complete 回读旧版水印原意图，再补齐四项标记")
            state["legacy_watermark"] = state.pop("watermark")
        return prepare_markers(base, task, state, snapshot)
    if previous:
        # 旧版意图保留原回读路径，不能替换在途载荷或冒充四项标记成功。
        return dict(previous, repeated=True)
    fields, issue_type_id = issue_from(snapshot, issue_key)
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(snapshot, ensure_ascii=False)):
        raise ValueError("Jira 初始快照含敏感内容，请先脱敏")
    product_root = project_rules.product_root_from_station(base)
    version = product_version.describe(product_root)
    facts = task.setdefault("facts", {})
    facts.setdefault("jira_snapshot", snapshot)
    facts.setdefault("agenticops_version", version)
    task_store.write_task(base, task)
    rules = config(base)
    if issue_type_id not in rules["issue_type_ids"]:
        raise ValueError("Jira 事务类型 %s 未配置接管版本水印" % issue_type_id)
    field_id = rules["field_id"]
    current = fields.get(field_id)
    if current is not None and not isinstance(current, str):
        raise ValueError("Jira 水印字段 %s 的回读类型不是 string" % field_id)
    record = {
        "at": now(), "source_ref": snapshot["source_ref"], "issue_key": issue_key, "field_id": field_id,
        "field_name": rules["field_name"], "logical_key": rules["logical_key"],
        "issue_type_id": issue_type_id,
        "version": version, "write_mode": rules["write_mode"],
        "payload_digest": payload_digest(field_id, version),
    }
    if current == version:
        record.update(
            outcome="verified", reason="already_current", completed_at=now(),
            readback_ref=snapshot["source_ref"], readback_value=current,
        )
    else:
        record.update(
            outcome="ready", reason="watermark_prepared", previous_value=current,
            native_request={"issue_key": issue_key, "fields": {field_id: version}},
        )
    state["watermark"] = record
    save_state(base, task, state)
    return record


@sync_recovery.local_locked
def complete(base, issue_key, outcome, snapshot, message=""):
    if outcome not in ("failed", "unknown"):
        raise ValueError("无效水印回执结果")
    task = task_store.read_task(base, issue_key)
    state = load_state(base, task)
    record = state.get("watermark")
    if not record or record.get("outcome") not in ("ready", "unknown", "failed", "stale", "verified"):
        raise ValueError("当前 task/run 没有待回读的 Jira 接管水印意图")
    sync_recovery.require_receipt(base, task)
    original = copy.deepcopy(record)
    if record.get("marker_version") == 2:
        return complete_markers(base, task, state, snapshot, outcome, message)
    fields, issue_type_id = issue_from(snapshot, issue_key)
    rules = config(base)
    if issue_type_id != record["issue_type_id"] or issue_type_id not in rules["issue_type_ids"]:
        raise ValueError("Jira 工作项类型已变化或不再适用接管版本水印")
    product_root = project_rules.product_root_from_station(base)
    current_version = product_version.describe(product_root)
    if current_version != record["version"]:
        record.update(
            outcome="stale", reason="product_version_changed", completed_at=now(),
            readback_ref=snapshot["source_ref"], readback_value=fields.get(record["field_id"]),
            guidance=[{"guidance": "Product Root 版本已变化；不得将旧水印确认成功。保留当前 run、原版本与 Jira 回读，列入同步警告。"}],
        )
        save_state(base, task, state, recovery=original)
        return record
    actual = fields.get(record["field_id"])
    record["completed_at"] = now()
    record["readback_ref"] = snapshot["source_ref"]
    record["readback_value"] = actual
    if actual == record["version"]:
        record.pop("native_request", None)
        record.pop("guidance", None)
        record.update(outcome="verified", reason="watermark_read_back")
    else:
        record.update(outcome=outcome, reason="watermark_%s" % outcome)
        if message:
            text = str(message)[:600]
            admission = project_rules.load_admission(station=base)
            record["message"] = (
                "外部错误信息含敏感内容，原文未保存"
                if project_rules.scan_sensitive(admission, text) else text
            )
        record["guidance"] = [
            {"guidance": "水印写入未通过回读确认；本地流程继续，重复写入前先用新的 Jira 只读快照回读。"}
        ]
    save_state(base, task, state, recovery=original)
    return record


def prepare_markers(base, task, state, snapshot):
    rules = config(base)
    fields, issue_type = issue_from(snapshot, task["issue_key"])
    from workflow import issue_routing
    profile = project_rules.load_profile(station=base)
    issue_routing.check_task(profile, task, fields)
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(snapshot, ensure_ascii=False)):
        raise ValueError("Jira 初始快照含敏感内容，请先脱敏")
    previous = state.get("watermark")
    if previous:
        # 每次恢复都读远端；未知写入只回读原意图，不重新生成写请求。
        result = complete_markers(base, task, state, snapshot, "unknown", "")
        actual = marker_values(fields, rules, snapshot)
        if result["outcome"] != "verified" and snapshot.get("not_applied_ref"):
            if "verified_ref" not in result:
                raise ValueError("旧标记缺少核验历史，不能证明原操作从未成功；仅回读或交接，不重复写入")
            if result.get("verified_ref"):
                raise ValueError("原标记曾经核验成功；当前变化不能解释为原写入未生效，禁止重复写入")
            jira_ownership.inspect(base, task["issue_key"], snapshot, task)
            if (not isinstance(snapshot["not_applied_ref"], str) or not snapshot["not_applied_ref"].strip()
                    or actual != previous["previous_fields"]):
                raise ValueError("重试必须确认原写入未生效，且当前四项仍等于原写前观察")
            result.update(outcome="ready", reason="original_write_not_applied",
                          native_request={"issue_key": task["issue_key"], "fields": result["expected_fields"]})
            save_state(base, task, state)
        return dict(result, repeated=True)
    if not issue_routing.marker_type_allowed(profile, task, issue_type):
        raise ValueError("Jira 事务类型未配置接管标记")
    ownership = jira_ownership.inspect(base, task["issue_key"], snapshot, task)
    actual = marker_values(fields, rules, snapshot)
    actor = snapshot.get("current_user", {})
    if (not isinstance(actor, dict) or actor.get("active") is not True or
            not all(isinstance(actor.get(k), str) and actor[k].strip() for k in ("accountId", "displayName", "source_ref"))):
        raise ValueError("需要同一 Jira 连接的当前登录用户 accountId/displayName/active/source_ref")
    actor = {k: actor[k] for k in ("accountId", "displayName", "source_ref")}
    mapping = rules["marker_fields"]
    station = station_context.read_binding(base)["station_id"]
    same_run = actual[mapping["station"]] == station and actual[mapping["run_id"]] == task["run_id"]
    if same_run and all(actual.values()):
        expected = actual.copy()
    else:
        if any(actual.values()):
            resolution = snapshot.get("previous_takeover", {})
            if (resolution.get("observed_fields") != actual or
                    not isinstance(resolution.get("evidence_ref"), str) or not resolution["evidence_ref"].strip()):
                raise ValueError("已有或不完整的接管标记；先核实旧 run 归档/清理或交接，提供绑定本次回读的 previous_takeover 证据")
        legacy = state.get("legacy_watermark")
        version = legacy["version"] if legacy else product_version.describe(project_rules.product_root_from_station(base))
        expected = {rules["field_id"]: version,
                    mapping["begin_at"]: legacy["at"] if legacy else now(),
                    mapping["station"]: station, mapping["run_id"]: task["run_id"]}
        if same_run:
            for key in (rules["field_id"], mapping["begin_at"]):
                if actual[key]:
                    expected[key] = actual[key]
    record = {"at": now(), "source_ref": snapshot["source_ref"], "issue_key": task["issue_key"],
              "field_id": rules["field_id"], "field_name": rules["field_name"], "logical_key": rules["logical_key"],
              "issue_type_id": issue_type, "version": expected[rules["field_id"]], "write_mode": "overwrite",
              "marker_version": 2, "marker_fields": mapping, "expected_fields": expected, "actor": actor,
              "previous_fields": actual, "previous_takeover": snapshot.get("previous_takeover"),
              "payload_digest": marker_digest(expected), "verified_ref": None, "ownership": ownership}
    if markers_equal(actual, expected, mapping["begin_at"]):
        record.update(outcome="verified", reason="already_current", completed_at=now(),
                      readback_ref=snapshot["source_ref"], readback_fields=actual, readback_value=record["version"],
                      verified_ref=snapshot["source_ref"])
    else:
        record.update(outcome="ready", reason="markers_prepared", native_request={"issue_key": task["issue_key"], "fields": expected})
    facts = task.setdefault("facts", {})
    facts.setdefault("jira_snapshot", snapshot)
    facts.setdefault("agenticops_version", record["version"])
    task_store.write_task(base, task)
    state["watermark"] = record
    save_state(base, task, state)
    return record


def complete_markers(base, task, state, snapshot, outcome, message):
    record = state["watermark"]
    original = copy.deepcopy(record)
    # 兼容旧 verified 记录：在覆盖最新观察前保全已发生的核验事实。
    if original["outcome"] == "verified" and not record.get("verified_ref"):
        record["verified_ref"] = original["readback_ref"]
    try:
        fields, issue_type = issue_from(snapshot, task["issue_key"])
        from workflow import issue_routing
        if (issue_type != record["issue_type_id"] or not issue_routing.marker_type_allowed(
                project_rules.load_profile(station=base), task, issue_type)):
            raise ValueError("Jira 工作项类型已变化或不再适用接管标记")
        ownership = jira_ownership.inspect(base, task["issue_key"], snapshot, task, readback=True)
        previous_identity = record.get('ownership', {})
        if previous_identity and any(previous_identity.get(k) != ownership[k] for k in ('site', 'issue_id')):
            raise ValueError('Jira 归属回读站点或任务 ID 已变化')
        actual = marker_values(fields, record, snapshot)
    except ValueError:
        record.update(outcome="unknown", reason="markers_unreadable", completed_at=now(),
                      readback_ref=snapshot.get("source_ref") or "unreadable:ownership-readback", readback_fields={}, readback_value=None)
        record.pop("native_request", None)
        save_state(base, task, state, recovery=original)
        raise
    record.update(completed_at=now(), readback_ref=snapshot["source_ref"], readback_fields=actual,
                  readback_value=actual[record["field_id"]])
    record.pop("native_request", None)
    if markers_equal(actual, record["expected_fields"], record["marker_fields"]["begin_at"]):
        if not record.get("verified_ref"):
            record["verified_ref"] = snapshot["source_ref"]
        record.update(outcome="verified", reason="markers_read_back", ownership=ownership)
        record.pop("guidance", None)
    else:
        record.update(outcome=outcome, reason="markers_" + outcome)
        record["guidance"] = [{"guidance": "四项标记未一致，暂停依赖归属的写入及接管摘要；先核实原写入及接管归属，不能盲目重发。无依赖研发可继续。"}]
        if message:
            record["message"] = ("外部错误信息含敏感内容，原文未保存" if project_rules.scan_sensitive(
                project_rules.load_admission(station=base), str(message)) else str(message)[:600])
    save_state(base, task, state, recovery=original)
    return record


def summary_ready(base, task):
    record = status(base, task)
    if record.get("marker_version") != 2 or record.get("outcome") != "verified":
        return False
    expected, mapping = record["expected_fields"], record["marker_fields"]
    return (expected[mapping["station"]] == station_context.read_binding(base)["station_id"]
            and expected[mapping["run_id"]] == task["run_id"])


def status(base, task):
    record = load_state(base, task).get("watermark")
    return record or {"outcome": "missing", "reason": "watermark_not_prepared"}


def takeover_warnings(base, task):
    record = status(base, task)
    if record.get("outcome") == "verified":
        if record.get("marker_version") == 2:
            return []
        if config(base).get("schema_version") == 2:
            return ["旧版单字段水印已回读；需 prepare 补齐四项接管标记后再发送摘要"]
        try:
            current_version = product_version.describe(project_rules.product_root_from_station(base))
        except ValueError as error:
            return ["无法确认当前 Product Root 版本：%s" % error]
        if current_version != record.get("version"):
            return ["接管版本水印与当前 Product Root 版本不一致；保留同步待办"]
        return []
    if record.get("outcome") == "missing":
        return ["接管版本水印尚未准备；先读取 Jira 快照并执行 jira_watermark.py prepare"]
    return ["接管版本水印未回读验证（当前 %s）；依赖归属的写入暂停，独立只读核验可继续" % record.get("outcome")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--expected-run-id", required=True)
    prepare_parser.add_argument("--issue-key", required=True)
    prepare_parser.add_argument("--input", required=True)
    prepare_parser.add_argument("--dir", default=".")
    complete_parser = sub.add_parser("complete")
    complete_parser.add_argument("--expected-run-id", required=True)
    complete_parser.add_argument("--issue-key", required=True)
    complete_parser.add_argument("--outcome", choices=("failed", "unknown"), required=True)
    complete_parser.add_argument("--input", required=True)
    complete_parser.add_argument("--message", default="")
    complete_parser.add_argument("--dir", default=".")
    status_parser = sub.add_parser("status")
    status_parser.add_argument("--issue-key", required=True)
    status_parser.add_argument("--dir", default=".")
    args = parser.parse_args()
    try:
        task_store.station_project(args.dir)
        issue_key = task_store.resolve_issue(args.dir, args.issue_key)
        with task_store.task_run_lock(args.dir, issue_key, execution=args.command != "status", ownership=False):
            task = task_store.read_task(args.dir, issue_key)
            if args.command != "status":
                task_store.check_expected_run(args.dir, issue_key, args.expected_run_id)
                if args.command != "complete":
                    station_takeover.require_external(args.dir, task)
            if args.command == "prepare":
                result = prepare(args.dir, issue_key, read_input(args.input))
            elif args.command == "complete":
                result = complete(args.dir, issue_key, args.outcome, read_input(args.input), args.message)
            else:
                result = status(args.dir, task)
        from workflow import external_sync
        current = task_store.read_task(args.dir, issue_key)
        result = dict(result, **external_sync.safe_actions(args.dir, current))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except FieldReadError as error:
        from workflow import external_sync
        result = external_sync.safe_actions(args.dir, task_store.read_task(args.dir, issue_key))
        result.update(field_observations=error.observations, error=str(error),
                      recovery="用同一连接显式读取所列字段；连接器仍省略时提供 field_readbacks 原生补查，禁止猜 null")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
