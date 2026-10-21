"""工位操作日志和恢复边界；调用者必须持有 task_state_lock。"""
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path

from workflow import engineering_baseline, task_store


def path(base):
    return task_store.state_path(base) / "operation.json"


def read(base):
    target = path(base)
    if target.is_symlink():
        raise ValueError("工位操作日志不能是符号链接")
    if not target.exists():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("工位操作日志无法读取") from error
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("status") not in ("running", "failed", "done")
            or not isinstance(value.get("steps"), dict)):
        raise ValueError("工位操作日志结构无效")
    for key, reference in value.pop("payload_refs", {}).items():
        if key not in PAYLOAD_FIELDS or not re.fullmatch(r"[0-9a-f]{64}", reference):
            raise ValueError("操作材料引用无效")
        directory = task_store.state_path(base) / "operation-data"
        blob = directory / (reference + ".json")
        if directory.is_symlink() or blob.is_symlink():
            raise ValueError("操作材料不能是链接")
        item = json.loads(blob.read_text())
        if engineering_baseline.digest(item) != reference:
            raise ValueError("操作材料摘要不一致")
        value[key] = item
    required = {"operation_id", "kind", "run_id", "request", "request_digest", "expected_revision", "phase"}
    if (not required <= set(value) or not isinstance(value["operation_id"], str)
            or not re.fullmatch(r"op-[a-z0-9-]{8,80}", value["operation_id"])
            or value["kind"] not in ("takeover", "archive", "release", "clean", "scope_change", "replan", "work-clean")
            or not isinstance(value["run_id"], str) or not task_store.RUN_ID_PATTERN.fullmatch(value["run_id"])
            or not isinstance(value["request"], dict) or value["request_digest"] != engineering_baseline.digest(value["request"])
            or type(value["expected_revision"]) is not int or value["expected_revision"] < 0
            or not isinstance(value["phase"], str) or not value["phase"]):
        raise ValueError("工位操作日志身份或请求摘要无效")
    from workflow.station_clean_rules import validate_snapshot
    if "cleanup_plan" in value:
        validate_snapshot(value["cleanup_plan"])
    if "cleanup_plan" in value.get("handoff", {}):
        validate_snapshot(value["handoff"]["cleanup_plan"])
    for revision in value.get("plan_revisions", []):
        validate_snapshot(revision["plan"])
    for name, step in value["steps"].items():
        if not isinstance(name, str) or not isinstance(step, dict) or not {"before", "expected", "receipt"} <= set(step):
            raise ValueError("工位操作步骤结构无效")
        if step.get("superseded_by"):
            _verify_superseded(value, name, step)
        if value["status"] == "done" and step["receipt"] is None and not step.get("superseded_by"):
            raise ValueError("已完成操作仍有未核验步骤")
    return value


def _verify_superseded(operation, name, step):
    raise ValueError("旧清理修订记录必须由原版本退出")


def begin(base, kind, operation_id, expected_revision, request, run_id=None, before_create=None):
    if kind not in ("takeover", "archive", "release", "clean", "scope_change", "replan", "work-clean"):
        raise ValueError("未知工位操作")
    if not isinstance(operation_id, str) or not re.fullmatch(r"op-[a-z0-9-]{8,80}", operation_id):
        raise ValueError("operation_id 必须为 op- 前缀的稳定操作编号")
    request_digest = engineering_baseline.digest(request)
    previous = read(base)
    if previous and previous.get("handoff"):
        raise ValueError("接管已交接清理，只能恢复 handoff 指定的 clean 操作")
    if previous and previous["operation_id"] == operation_id:
        if (previous["kind"] != kind or previous["request_digest"] != request_digest
                or previous["expected_revision"] != expected_revision
                or (run_id is not None and previous["run_id"] != run_id)):
            raise ValueError("相同 operation_id 不能用于不同请求")
        return previous
    if previous and previous["status"] != "done":
        raise ValueError("存在未完成工位操作，只能恢复原操作")
    current = task_store.read_current(base)
    if current["revision"] != expected_revision:
        raise ValueError("工位 revision 已变化：expected=%s actual=%s；请读取 task.py status，不使用质量日志 revision" % (expected_revision, current["revision"]))
    if kind == "takeover":
        if current["current"] is not None:
            raise ValueError("工位仍有当前任务，不能接管新任务")
        issue_key = task_store.validate_issue_key(request.get("issue_key"))
        if before_create is not None:
            before_create()
        from workflow import archive_store
        for attempt in range(2):
            run_id = task_store.new_run_id(issue_key)
            try:
                archive_store.reserve(base, issue_key, run_id)
                break
            except FileExistsError as error:
                if attempt == 0:
                    time.sleep(2)
                    continue
                raise ValueError(
                    "执行编号 %s 已存在；等待 2 秒自动重试一次仍冲突，请核验后稍后重试" % run_id
                ) from error
    elif kind != "work-clean" and (current["current"] is None or current["current"]["run_id"] != run_id):
        raise ValueError("工位 run 已变化")
    value = {
        "schema_version": 1, "operation_id": operation_id, "kind": kind,
        "run_id": run_id, "request_digest": request_digest,
        "expected_revision": expected_revision, "phase": "intent", "status": "running",
        "request": copy.deepcopy(request), "steps": {},
    }
    if previous and previous.get('protected_paths'):
        value['protected_paths'] = previous['protected_paths']
    inherited = list(previous.get("retained_results", [])) if previous else []
    if previous and previous.get("cleanup_result"):
        result = previous["cleanup_result"]
        if result.get("residuals") or result.get("resources"):
            inherited.append({"operation_id": previous["operation_id"], "run_id": previous["run_id"], "result": result})
    if inherited:
        value["retained_results"] = inherited
    try:
        save(base, value)
    except Exception:
        if kind == "takeover":
            observed = read(base)
            if observed and observed.get('operation_id') == operation_id and observed.get('run_id') == run_id:
                # 原子写入可能已完成而持久化回执失败；保留编号供原操作恢复。
                raise
            archive_store.consume_reservation(base, run_id)
        raise
    residuals = collect_payloads(base) or []
    if residuals:
        value['maintenance_residuals'] = residuals
        save(base, value)
    return value


PAYLOAD_FIELDS = {"archive_payload", "retained_results", "exit_task", "cleanup_plan", "archive_record", "archive_evidence", "archive_artifacts", "archive_logs", "archive_drafts", "plan_revisions", "amendment_receipts", "previous_operation"}


def save(base, operation):
    value = dict(operation)
    references = {}
    directory = task_store.state_path(base) / "operation-data"
    if directory.is_symlink():
        raise ValueError("操作材料目录不能是链接")
    for key in PAYLOAD_FIELDS & value.keys():
        item = value.pop(key)
        reference = engineering_baseline.digest(item)
        blob = directory / (reference + ".json")
        if blob.is_symlink():
            raise ValueError("操作材料不能是链接")
        if not blob.exists():
            task_store._write_json_atomic(blob, item)
        references[key] = reference
    if references:
        value["payload_refs"] = references
    task_store._write_json_atomic(path(base), value)


def handoff_clean(base, operation_id, revision, request, run_id, cleanup_plan, kind="clean"):
    """先在原任务操作记录固化完整交接，再替换；任何中断点都有唯一后继。"""
    previous = read(base)
    if previous and previous.get('archive_result', {}).get('status') == 'awaiting_decision':
        raise ValueError('归档仍待用户决定，先恢复原操作处理归档，不能以 clean 绕过')
    if previous is None or previous["kind"] not in ("takeover", "scope_change", "replan", "archive") or previous["status"] == "done":
        raise ValueError("只有未完成的非清理任务操作可以交接退出")
    if not re.fullmatch(r"op-[a-z0-9-]{8,80}", operation_id) or operation_id == previous["operation_id"]:
        raise ValueError("清理交接需要新的有效操作编号")
    current = task_store.read_current(base)
    if not current["current"] or current["current"]["run_id"] != run_id or previous["run_id"] != run_id:
        raise ValueError("清理交接 run 已变化")
    target = {"operation_id": operation_id, "kind": kind, "expected_revision": revision,
              "request": copy.deepcopy(request), "request_digest": engineering_baseline.digest(request),
              "cleanup_plan": copy.deepcopy(cleanup_plan), "run_id": run_id}
    if previous.get("handoff"):
        if previous["handoff"] != target:
            raise ValueError("必须恢复原清理交接请求")
    else:
        if current["revision"] != revision:
            raise ValueError("清理交接 revision 已变化：expected=%s actual=%s" % (revision, current["revision"]))
        previous["handoff"] = target
        save(base, previous)
    value = {"schema_version": 1, "operation_id": operation_id, "kind": kind, "run_id": run_id,
             "request_digest": target["request_digest"], "expected_revision": revision,
             "phase": "intent", "status": "running", "request": copy.deepcopy(request),
             "steps": {}, "cleanup_plan": copy.deepcopy(cleanup_plan),
             "supersedes": {"operation_id": previous["operation_id"], "digest": engineering_baseline.digest(previous)},
             "previous_operation": {key: item for key, item in previous.items() if key != "handoff"}}
    save(base, value)
    return value


def intent(base, operation, name, before, expected):
    recorded = operation["steps"].get(name)
    if recorded:
        if recorded["expected"] != expected:
            raise ValueError("操作步骤目标发生变化")
        return recorded
    recorded = {"before": copy.deepcopy(before), "expected": copy.deepcopy(expected), "receipt": None}
    operation["steps"][name] = recorded
    save(base, operation)
    return recorded


def receipt(base, operation, name, value):
    step = operation["steps"][name]
    if step["receipt"] is not None and step["receipt"] != value:
        raise ValueError("同一操作步骤回执不一致")
    step["receipt"] = copy.deepcopy(value)
    save(base, operation)


def finish(base, operation):
    for name, step in operation["steps"].items():
        if step.get("superseded_by"):
            _verify_superseded(operation, name, step)
        elif step["receipt"] is None:
            raise ValueError("工位操作仍有未核验步骤")
    operation.update(status="done", phase="done")
    # 正式档案已持有正文，不在当前操作缓存中跨任务保留另一份大字节副本。
    for key in ("archive_artifacts", "archive_logs", "archive_evidence", "archive_record"):
        operation.pop(key, None)
    for draft in operation.get("archive_drafts", []):
        for key in ("evidence", "artifacts", "logs"):
            draft.pop(key, None)
    save(base, operation)
    residuals = collect_payloads(base) or []
    if residuals:
        operation['maintenance_residuals'] = residuals
        if 'cleanup_result' in operation:
            existing = {row['path'] for row in operation['cleanup_result']['residuals']}
            operation['cleanup_result']['residuals'].extend(row for row in residuals if row['path'] not in existing)
            operation['cleanup_result']['complete'] = False
        save(base, operation)


def collect_payloads(base):
    """调用者持锁；只回收不再被原子发布的当前 operation 引用的内容寻址材料。"""
    directory = task_store.state_path(base) / "operation-data"
    if directory.is_symlink():
        raise ValueError("操作材料目录不能是链接")
    if not directory.exists():
        return
    current = json.loads(path(base).read_text())
    retained = set(current.get("payload_refs", {}).values())
    residuals = []
    for blob in directory.iterdir():
        try:
            if (blob.is_symlink() or not blob.is_file() or blob.suffix != '.json'
                    or engineering_baseline.digest(json.loads(blob.read_text())) != blob.stem):
                raise ValueError('未知操作材料，保留')
            if blob.stem not in retained:
                blob.unlink()
        except (ValueError, OSError) as error:
            residuals.append({'path': blob.relative_to(Path(base).resolve()).as_posix(), 'reason': str(error)})
    from workflow.station_directories import sync
    sync(directory)
    return residuals
