"""工位操作日志和恢复边界；调用者必须持有 task_state_lock。"""
from __future__ import annotations

import copy
import json
import re
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
            or value["kind"] not in ("takeover", "archive", "release", "clean", "scope_change", "replan")
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
    history = operation.get("plan_revisions", [])
    current = operation.get("cleanup_plan", {})
    if not isinstance(history, list) or not isinstance(current, dict) or not name.startswith(("resource:", "source-reset:", "station-source-reset:", "external:", "clear-active:", "archive-publish:", "cleanup-stage:")):
        raise ValueError("只有清理资源步骤可被已确认的新计划替代")
    for index, revision in enumerate(history):
        if not isinstance(revision, dict) or not isinstance(revision.get("plan"), dict):
            raise ValueError("清理计划修订记录无效")
        plan = revision.get("plan", {})
        digest = plan.get("digest")
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or engineering_baseline.digest({k: v for k, v in plan.items() if k != "digest"}) != digest
                or (revision.get("confirmation_digest") != digest and not
                    (operation.get("kind") == "archive" and index == 0 and revision.get("confirmation_digest") is None))
                or revision.get("revision") != index or revision.get("superseded_revision") != index + 1
                or not isinstance(revision.get("superseded_by"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", revision["superseded_by"])):
            raise ValueError("清理计划修订链无效")
        successor = history[index + 1].get("plan", {}) if index + 1 < len(history) and isinstance(history[index + 1], dict) else current
        if revision["superseded_by"] != successor.get("digest"):
            raise ValueError("清理计划修订链未指向下一版权威计划")
    original = step.get("superseded_plan_digest")
    if not isinstance(original, str):
        raise ValueError("替代步骤缺少原计划摘要")
    original_revision = step.get("superseded_plan_revision")
    if type(original_revision) is not int or not 0 <= original_revision < len(history):
        raise ValueError("替代步骤缺少原计划修订编号")
    revision = history[original_revision]
    if (revision["plan"]["digest"] != original or revision.get("superseded_by") != step.get("superseded_by")
            or step.get("superseded_revision") != original_revision + 1):
        raise ValueError("步骤替代缺少原计划和精确新确认")
    if not isinstance(step["expected"], dict):
        raise ValueError("清理步骤目标结构无效")
    if operation.get("kind") == "archive" and not name.startswith("archive-publish:"):
        raise ValueError("归档草稿刷新不能替代或执行删除步骤")
    if name.startswith("archive-publish:"):
        drafts = operation.get("archive_drafts", [])
        draft = next((item for item in drafts if isinstance(item, dict) and item.get("plan_revision") == original_revision), None)
        if (name != "archive-publish:" + str(original_revision) or not draft
                or engineering_baseline.digest(draft.get("record")) != step["expected"].get("digest")
                or (draft.get("publication_intent") or {}).get("expected") != step["expected"]):
            raise ValueError("发布步骤替代缺少未发布草稿及原意图")
    elif name.startswith("clear-active:"):
        if name != "clear-active:" + str(original_revision) + ":" + original or not isinstance(step["expected"].get("files"), dict):
            raise ValueError("活动材料清理步骤不属于原计划")
    elif name.startswith("station-source-reset:"):
        if (revision["plan"].get("schema_version") != 6
                or name != "station-source-reset:" + str(original_revision) + ":" + original
                or step["expected"] != {"plan_digest": original}):
            raise ValueError("工位源码复位步骤不属于原计划")
    elif name.startswith("cleanup-stage:"):
        from workflow.station_cleanup_stages import STAGES
        stage = step["expected"].get("stage")
        if stage not in STAGES or name != "cleanup-stage:" + str(original_revision) + ":" + original + ":" + stage or step["expected"].get("plan_digest") != original:
            raise ValueError("阶段回执不属于原计划")
    elif name.startswith("source-reset:"):
        repository = step["expected"].get("repository")
        entries = [entry for entry in revision["plan"].get("entries", []) if entry.get("repository") == repository]
        if step["expected"].get("entries_digest") != engineering_baseline.digest(entries):
            raise ValueError("源码回收步骤不属于原计划")
    elif name.startswith("resource:"):
        if name != "resource:" + str(original_revision) + ":" + original + ":" + str(step["expected"].get("path")) or step["expected"] not in revision["plan"].get("entries", []):
            raise ValueError("被替代步骤不属于原计划")
    else:
        identifier = step["expected"].get("id")
        if (name != "external:" + str(original_revision) + ":" + original + ":" + engineering_baseline.digest(identifier)
                or not any(entry.get("id") == identifier for entry in revision["plan"].get("external", []))):
            raise ValueError("被替代外部资源不属于原计划")
    if (engineering_baseline.digest({k: v for k, v in current.items() if k != "digest"}) != current.get("digest")
            or operation.get("confirmation_digest") != current.get("digest")):
        raise ValueError("当前清理计划没有有效精确确认")


def begin(base, kind, operation_id, expected_revision, request, run_id=None):
    if kind not in ("takeover", "archive", "release", "clean", "scope_change", "replan"):
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
        from workflow import archive_store
        run_id = task_store.new_run_id(issue_key)
        try:
            archive_store.reserve(base, issue_key, run_id)
        except FileExistsError as error:
            raise ValueError(
                "执行编号 %s 已存在；同一 Jira 在同一秒只能发起一次接管，请研发稍后重试" % run_id
            ) from error
    elif current["current"] is None or current["current"]["run_id"] != run_id:
        raise ValueError("工位 run 已变化")
    value = {
        "schema_version": 1, "operation_id": operation_id, "kind": kind,
        "run_id": run_id, "request_digest": request_digest,
        "expected_revision": expected_revision, "phase": "intent", "status": "running",
        "request": copy.deepcopy(request), "steps": {},
    }
    try:
        save(base, value)
    except Exception:
        if kind == "takeover":
            archive_store.consume_reservation(base, run_id)
        raise
    collect_payloads(base)
    return value


PAYLOAD_FIELDS = {"cleanup_plan", "archive_record", "archive_evidence", "archive_artifacts", "archive_logs", "archive_drafts", "plan_revisions", "amendment_receipts", "previous_operation"}


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


def handoff_clean(base, operation_id, revision, request, run_id, cleanup_plan):
    """先在原接管记录固化完整交接，再替换；任何中断点都有唯一后继。"""
    previous = read(base)
    if previous is None or previous["kind"] != "takeover" or previous["status"] == "done":
        raise ValueError("只有未完成接管可以交接清理")
    if not re.fullmatch(r"op-[a-z0-9-]{8,80}", operation_id) or operation_id == previous["operation_id"]:
        raise ValueError("清理交接需要新的有效操作编号")
    current = task_store.read_current(base)
    if not current["current"] or current["current"]["run_id"] != run_id or previous["run_id"] != run_id:
        raise ValueError("清理交接 run 已变化")
    target = {"operation_id": operation_id, "expected_revision": revision,
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
    value = {"schema_version": 1, "operation_id": operation_id, "kind": "clean", "run_id": run_id,
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
    collect_payloads(base)


def collect_payloads(base):
    """调用者持锁；只回收不再被原子发布的当前 operation 引用的内容寻址材料。"""
    directory = task_store.state_path(base) / "operation-data"
    if directory.is_symlink():
        raise ValueError("操作材料目录不能是链接")
    if not directory.exists():
        return
    current = json.loads(path(base).read_text())
    retained = set(current.get("payload_refs", {}).values())
    for blob in directory.iterdir():
        if blob.is_symlink() or not blob.is_file() or blob.suffix != ".json":
            raise ValueError("操作材料含未知对象，拒绝回收")
        if engineering_baseline.digest(json.loads(blob.read_text())) != blob.stem:
            raise ValueError("操作材料摘要损坏，拒绝回收")
    for blob in directory.iterdir():
        if blob.stem not in retained:
            blob.unlink()
    from workflow.station_directories import sync
    sync(directory)
