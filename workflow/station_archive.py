"""正式档案原子发布与回读；保存会话与任务状态；原子发布和追加终态回执。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from workflow import station_context
from workflow import archive_store, engineering_baseline as baseline, project_rules, station_operation as operations, station_source, task_store


def _directory(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("档案目录必须为真实目录")
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def redact_evidence(rules, value, key=""):
    """复用档案脱敏规则生成副本，不改变活动事实。"""
    if any(word in key.lower() for word in ("password", "secret", "token", "credential", "private_key")):
        return "[redacted]"
    if isinstance(value, dict):
        return {name: redact_evidence(rules, item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [redact_evidence(rules, item) for item in value]
    if isinstance(value, str) and project_rules.scan_sensitive(rules, value):
        return "[redacted]"
    return value


def _evidence(base, task, overrides=None, documents_input=None):
    rules = project_rules.load_admission(station=base)
    def redact(value):
        return redact_evidence(rules, value)
    documents = {"current": redact({key: value for key, value in task.items() if key not in ("_revision", "repositories")})}
    from workflow import station_records, station_resources, quality, jira_status, jira_watermark, external_sync
    documents_input = station_records.read_documents(base, task, overrides) if documents_input is None else documents_input
    coverage = station_records.conversations(base, task, documents_input)
    for name, row in documents_input.items():
        value = row['value']
        if name.startswith('conversation'):
            documents[name] = redact(station_records.conversation_projection(value) if value.get('kind') == 'conversation' else station_records.validate_scope(value, task))
        else:
            if name == 'resources.json':
                station_resources.inventory(base, task)
            elif value is not None:
                if not isinstance(value, dict) or not isinstance(value.get('run_id'), str):
                    raise ValueError('已知控制证据结构无效：' + name)
                related = dict(task, run_id=value['run_id'])
                module = quality if name.startswith('quality-') else jira_status if name.startswith('jira-status-') else jira_watermark if name.startswith('jira-watermark-') else external_sync
                state_path = module.state_path(base, related)
                if state_path.name != name:
                    raise ValueError('控制证据文件名与 run 不匹配')
                if module in (quality, external_sync):
                    module.load(base, related)
                else:
                    module.load_state(base, related)
            documents[name] = {'source_ref': 'evidence/' + name, 'sha256': row['sha256'], 'size': row['bytes'],
                'state': redact(station_records.evidence_projection(name, value, task))}
    authorization = task_store.state_path(base) / "authorization.json"
    if authorization.is_symlink():
        raise ValueError("授权文件不能是符号链接")
    if authorization.exists():
        documents["authorization"] = redact(json.loads(authorization.read_text(encoding="utf-8")))
    from workflow import station_records
    documents["conversation_coverage"] = coverage
    data = json.dumps(documents, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(data) > station_records.MAX_TOTAL_BYTES:
        raise ValueError("任务记录超过归档总量上限")
    return data


def verify(base, reference, task):
    if not isinstance(reference, dict) or reference.get("scope") != "product" or reference.get("run_id") != task["run_id"]:
        raise ValueError("档案身份路径不一致")
    target = archive_store.from_reference(base, reference)
    for part in (archive_store.root(base), target):
        if part.is_symlink() or not part.is_dir():
            raise ValueError("档案目录缺失或为符号链接")
    record_path = target / "record.json"
    if record_path.is_symlink() or not record_path.is_file():
        raise ValueError("档案正文缺失或不是普通文件")
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("档案正文无法读取") from error
    if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
        raise ValueError("档案正文结构无效")
    try:
        binding = station_context.read_binding(base)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("工位绑定无法读取") from error
    if not isinstance(binding, dict):
        raise ValueError("工位绑定结构无效")
    if record.get("station_id") != binding.get("station_id"):
        raise ValueError("档案不属于当前工位")
    if {path.name for path in target.iterdir()} - {"record.json", "summary.md", "evidence.json", "source-artifacts.json", "runtime-evidence.json", "receipts"}:
        raise ValueError("正式档案包含清单外文件")
    receipts_path = target / "receipts"
    if receipts_path.exists() and (receipts_path.is_symlink() or not receipts_path.is_dir()):
        raise ValueError("档案回执目录无效")
    if (record.get("issue_key"), record.get("run_id")) != (task["issue_key"], task["run_id"]):
        raise ValueError("档案身份不一致")
    if baseline.digest(record) != reference.get("digest"):
        raise ValueError("档案摘要不一致")
    if set(record["files"]) != {"summary.md", "evidence.json", "source-artifacts.json", "runtime-evidence.json"}:
        raise ValueError("档案文件清单不完整")
    for name, entry in record["files"].items():
        if name not in ("summary.md", "evidence.json", "source-artifacts.json", "runtime-evidence.json"):
            raise ValueError("档案清单含未知文件")
        path = target / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("档案文件缺失")
        data = path.read_bytes()
        if not isinstance(entry, dict) or entry != {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}:
            raise ValueError("档案文件摘要不一致")
    return record


def publication_step(operation):
    return "archive-publish:" + str(len(operation.get("plan_revisions", [])))


def recover_published(base, task, operation):
    """只读回已登记的正式目标再补绑定；没有正式目录时不生成草稿。"""
    if task.get("archive_ref"):
        verify(base, task["archive_ref"], task)
        return task["archive_ref"]
    target = archive_store.run_directory(base, task["run_id"])
    if not target.exists() and not target.is_symlink():
        return None
    step_name = publication_step(operation)
    step = operation["steps"].get(step_name)
    record = operation.get("archive_record")
    if not step or not record:
        raise ValueError("正式档案目标已存在但没有对应发布意图，拒绝覆盖")
    reference = step["expected"]
    if reference.get("digest") != baseline.digest(record):
        raise ValueError("正式档案与已登记草稿不一致")
    verify(base, reference, task)
    operations.receipt(base, operation, step_name, reference)
    operation["archive_ref"] = reference
    operations.save(base, operation)
    task["archive_ref"] = reference
    task_store.write_task(base, task)
    return reference


def record_metadata(base, task, summary, reason, inventory, data, evidence, artifacts, logs):
    binding = station_context.read_binding(base)
    return {"schema_version": 1, "station_id": binding["station_id"],
                "issue_key": task["issue_key"], "run_id": task["run_id"],
                "task_result": "completed" if task.get("outcome") == "completed" and task.get("terminal_proof") else "incomplete",
                "stage": task["stage"], "reason": reason,
                "engineering_baseline_digest": task.get("engineering_baseline", {}).get("digest"),
                "source_observation": station_source.inspect(base, task["engineering_baseline"]) if task.get("engineering_baseline", {}).get("status") == "frozen" else {},
                "resource_inventory_digest": inventory.get("digest"),
                "resource_inventory": [{"id": baseline.digest(entry), "disposition": entry.get("action", "unknown")} for entry in inventory.get("entries", [])],
                "terminal_proof_digest": baseline.digest(task.get("terminal_proof")),
                "evidence_policy": "保存脱敏会话与任务状态；正文为归档截止点快照，终态由同 run/op 回执补全",
                "files": {"summary.md": {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
                          "evidence.json": {"size": len(evidence), "sha256": hashlib.sha256(evidence).hexdigest()},
                          "source-artifacts.json": {"size": len(artifacts), "sha256": hashlib.sha256(artifacts).hexdigest()},
                          "runtime-evidence.json": {"size": len(logs), "sha256": hashlib.sha256(logs).hexdigest()}}}


def prepare_material(base, task, request, inventory, overrides=None, *, require_source_choices=True):
    """正式归档与无写入预检共享同一材料准入、脱敏和实际编码预算。"""
    summary = baseline.text(request.get('summary'), '归档总结')
    reason = baseline.text(request.get('reason'), '归档原因')
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), summary + '\n' + reason):
        raise ValueError('总结或原因包含敏感内容，请先脱敏')
    from workflow import station_records, station_logs, station_resources
    station_resources.verify_known_external(base, task)
    if require_source_choices:
        station_records.verify_source_choices(inventory)
    inputs = station_records.read_documents(base, task, overrides)
    coverage = (station_records.verify_decision(base, task, request, inputs)
        if require_source_choices or request.get('require_complete_conversation') or request.get('accept_conversation_digest')
        else station_records.conversations(base, task, inputs))
    material = {'summary': summary.encode('utf-8'), 'evidence': _evidence(base, task, overrides, inputs),
                'artifacts': station_records.artifact_manifest(base, task, inventory),
                'logs': station_logs.material(base, task, inventory), 'coverage': coverage}
    material['record'] = record_metadata(base, task, summary, reason, inventory,
        *(material[key] for key in ('summary', 'evidence', 'artifacts', 'logs')))
    return material


def publish(base, task, request, inventory, operation, check_stable=None):
    if task.get("archive_ref"):
        verify(base, task["archive_ref"], task)
        return task["archive_ref"]
    summary = baseline.text(request.get("summary"), "归档总结")
    reason = baseline.text(request.get("reason"), "归档原因")
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), summary + "\n" + reason):
        raise ValueError("总结或原因包含敏感内容，请先脱敏")
    root = archive_store.root(base, create=True)
    target = archive_store.run_directory(base, task["run_id"])
    recorded = operation.get("archive_record")
    if recorded is None:
        from workflow import station_resources
        station_resources.verify_known_external(base, task)
        binding = station_context.read_binding(base)
        from workflow import station_records
        material = prepare_material(base, task, station_records.effective_request(operation), inventory,
                                    require_source_choices=operation['kind'] != 'archive')
        data, evidence, artifacts, logs = (material[key] for key in ('summary', 'evidence', 'artifacts', 'logs'))
        recorded = material['record']
        operation["archive_logs"] = logs.decode("utf-8")
        operation["archive_artifacts"] = artifacts.decode("utf-8")
        operation["archive_evidence"] = evidence.decode("utf-8")
        operation["archive_record"] = recorded
        operations.save(base, operation)
    reference = archive_store.reference(task["run_id"], baseline.digest(recorded))
    step_name = publication_step(operation)
    operations.intent(base, operation, step_name, {}, reference)
    if target.is_symlink():
        raise ValueError("正式档案目标不能是符号链接")
    if not target.exists():
        if check_stable is not None and check_stable()["digest"] != inventory["digest"]:
            raise ValueError("归档前资源现场变化，拒绝发布")
        if recorded["source_observation"] and station_source.inspect(base, task["engineering_baseline"]) != recorded["source_observation"]:
            raise ValueError("归档前源码现场变化，拒绝发布")
        temporary = root / ("." + task["run_id"] + "." + operation["operation_id"] + "." + str(len(operation.get("plan_revisions", []))))
        _directory(temporary)
        if {path.name for path in temporary.iterdir()} - {"record.json", "summary.md", "evidence.json", "source-artifacts.json", "runtime-evidence.json"}:
            raise ValueError("归档暂存目录包含未知内容")
        summary_path = temporary / "summary.md"
        if summary_path.is_symlink():
            raise ValueError("归档暂存文件不能是符号链接")
        with summary_path.open("wb") as stream:
            stream.write(summary.encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        summary_path.chmod(0o600)
        evidence_path = temporary / "evidence.json"
        if evidence_path.is_symlink():
            raise ValueError("归档暂存文件不能是符号链接")
        with evidence_path.open("wb") as stream:
            stream.write(operation["archive_evidence"].encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        evidence_path.chmod(0o600)
        artifact_path = temporary / "source-artifacts.json"
        if artifact_path.is_symlink():
            raise ValueError("源码档案不能是符号链接")
        with artifact_path.open("wb") as stream:
            stream.write(operation["archive_artifacts"].encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        artifact_path.chmod(0o600)
        log_path = temporary / "runtime-evidence.json"
        if log_path.is_symlink():
            raise ValueError("运行证据不能是符号链接")
        from workflow import station_logs
        if station_logs.material(base, task, inventory).decode("utf-8") != operation["archive_logs"]:
            raise ValueError("日志/报告在归档期间变化，请停止写入者后恢复")
        with log_path.open("wb") as stream:
            stream.write(operation["archive_logs"].encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        log_path.chmod(0o600)
        task_store._write_json_atomic(temporary / "record.json", recorded)
        os.rename(temporary, target)
        descriptor = os.open(str(root), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    verify(base, reference, task)
    archive_store.consume_reservation(base, task["run_id"])
    operations.receipt(base, operation, step_name, reference)
    operation["archive_ref"] = reference
    operations.save(base, operation)
    task["archive_ref"] = reference
    task_store.write_task(base, task)
    return reference


def receipt(base, task, operation, result, phase="resources-released"):
    verify(base, task["archive_ref"], task)
    directory = archive_store.receipts(base, task["archive_ref"], create=True)
    if phase not in ("resources-released", "done"):
        raise ValueError("未知档案回执阶段")
    path = directory / (operation["operation_id"] + "-" + phase + ".json")
    value = {"operation_id": operation["operation_id"], "run_id": task["run_id"],
             "archive_digest": task["archive_ref"]["digest"], "phase": phase, "operation_kind": operation["kind"], "result": result}
    if path.exists():
        if path.is_symlink() or json.loads(path.read_text()) != value:
            raise ValueError("归档回执不能覆盖不同内容")
    else:
        task_store._write_json_atomic(path, value)
