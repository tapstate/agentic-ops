#!/usr/bin/env python3
"""接管版本水印的确定性意图、回读与单次结果记录。

实际 Jira 写入由 Agent 原生工具执行。本模块只准备精确字段载荷，并在回读确认
前阻止任务离开 ``waiting_takeover``；它不把普通 Jira 字段编辑变成任务授权。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bootstrap import product_version  # noqa: E402
from workflow import project_rules, task_store  # noqa: E402


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
            payload_digest(record["field_id"], record["version"]) != record["payload_digest"]):
        raise ValueError("Jira 接管水印记录内容无效")
    outcome = record["outcome"]
    if outcome not in ("ready", "verified", "unknown", "failed", "stale"):
        raise ValueError("Jira 接管水印记录 outcome 无效")
    if outcome == "ready":
        expected = {"issue_key": record.get("issue_key"), "fields": {record["field_id"]: record["version"]}}
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
        if outcome == "verified" and record.get("readback_value") != record["version"]:
            raise ValueError("Jira 接管水印 verified 回读值不一致")


def load_state(base, task):
    path = state_path(base, task)
    if not path.is_file():
        return {"schema_version": 1, "issue_key": task["issue_key"], "run_id": task["run_id"]}
    document = json.loads(path.read_text(encoding="utf-8"))
    if (document.get("schema_version") != 1 or document.get("issue_key") != task["issue_key"] or
            document.get("run_id") != task["run_id"]):
        raise ValueError("Jira 接管水印记录损坏或不属于当前 task/run")
    if "watermark" in document:
        validate_record(document["watermark"])
        if document["watermark"]["issue_key"] != task["issue_key"]:
            raise ValueError("Jira 接管水印记录任务号不一致")
    return document


def save_state(base, task, state):
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
    profile = project_rules.load_profile(workspace=base)
    return profile["jira"]["takeover_watermark"]


def prepare(base, issue_key, snapshot):
    task = json.loads(task_store.task_path(base, issue_key).read_text(encoding="utf-8"))
    if task.get("stage") != "waiting_takeover":
        raise ValueError("接管版本水印只允许在 waiting_takeover 阶段准备")
    state = load_state(base, task)
    previous = state.get("watermark")
    if previous:
        return dict(previous, repeated=True)
    fields, issue_type_id = issue_from(snapshot, issue_key)
    rules = config(base)
    if issue_type_id not in rules["issue_type_ids"]:
        raise ValueError("Jira 事务类型 %s 未配置接管版本水印" % issue_type_id)
    product_root = project_rules.product_root_from_workspace(base)
    version = product_version.describe(product_root)
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


def complete(base, issue_key, outcome, snapshot, message=""):
    task = json.loads(task_store.task_path(base, issue_key).read_text(encoding="utf-8"))
    state = load_state(base, task)
    record = state.get("watermark")
    if not record or record.get("outcome") not in ("ready", "unknown", "failed"):
        raise ValueError("当前 task/run 没有待回读的 Jira 接管水印意图")
    fields, issue_type_id = issue_from(snapshot, issue_key)
    rules = config(base)
    if issue_type_id != record["issue_type_id"] or issue_type_id not in rules["issue_type_ids"]:
        raise ValueError("Jira 工作项类型已变化或不再适用接管版本水印")
    product_root = project_rules.product_root_from_workspace(base)
    current_version = product_version.describe(product_root)
    if current_version != record["version"]:
        record.update(
            outcome="stale", reason="product_version_changed", completed_at=now(),
            readback_ref=snapshot["source_ref"], readback_value=fields.get(record["field_id"]),
            guidance=[{"guidance": "Product Root 版本已变化；不得将旧水印确认成功。请保留 Jira 回读并重新接管。"}],
        )
        save_state(base, task, state)
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
            admission = project_rules.load_admission(workspace=base)
            record["message"] = (
                "外部错误信息含敏感内容，原文未保存"
                if project_rules.scan_sensitive(admission, text) else text
            )
        record["guidance"] = [
            {"guidance": "水印写入未通过回读确认；不得自动重试或推进接管。请用新的 Jira 只读快照再次回读。"}
        ]
    save_state(base, task, state)
    return record


def status(base, task):
    record = load_state(base, task).get("watermark")
    return record or {"outcome": "missing", "reason": "watermark_not_prepared"}


def takeover_problems(base, task):
    record = status(base, task)
    if record.get("outcome") == "verified":
        try:
            current_version = product_version.describe(project_rules.product_root_from_workspace(base))
        except ValueError as error:
            return ["无法确认当前 Product Root 版本：%s" % error]
        if current_version != record.get("version"):
            return ["接管版本水印与当前 Product Root 版本不一致；不得进入 task_intake"]
        return []
    if record.get("outcome") == "missing":
        return ["接管版本水印尚未准备；先读取 Jira 快照并执行 jira_watermark.py prepare"]
    return ["接管版本水印未回读验证（当前 %s）；不得进入 task_intake" % record.get("outcome")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--issue-key", required=True)
    prepare_parser.add_argument("--input", required=True)
    prepare_parser.add_argument("--dir", default=".")
    complete_parser = sub.add_parser("complete")
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
        task_store.workspace_project(args.dir)
        issue_key = task_store.resolve_active_issue(args.dir, args.issue_key)
        with task_store.task_run_lock(args.dir, issue_key):
            task = json.loads(task_store.task_path(args.dir, issue_key).read_text(encoding="utf-8"))
            if args.command == "prepare":
                result = prepare(args.dir, issue_key, read_input(args.input))
            elif args.command == "complete":
                result = complete(args.dir, issue_key, args.outcome, read_input(args.input), args.message)
            else:
                result = status(args.dir, task)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
