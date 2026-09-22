#!/usr/bin/env python3
"""准备和记录一次非阻断 Jira 状态同步；实际 Jira 读写由 Agent 原生工具执行。

每个 task/run/trigger 幂等准备。外部工具由 Agent 原生权限执行，不保证强制单次调用。
prepare 接受实时 Jira issue 与可用 transitions 快照，
返回 transition 意图或跳过原因；complete 导入写后回读。任何结果都不改变本地任务阶段。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import copy
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import jira_tests, project_rules, quality, task_store, sync_recovery  # noqa: E402


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def state_path(base, task):
    suffix = hashlib.sha256(task["run_id"].encode()).hexdigest()[:24]
    return task_store.task_directory(base, task["issue_key"]) / ("jira-status-%s.json" % suffix)


def load_state(base, task):
    path = state_path(base, task)
    if not path.is_file():
        return {"schema_version": 1, "issue_key": task["issue_key"], "run_id": task["run_id"], "attempts": {}}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or document.get("issue_key") != task["issue_key"] or document.get("run_id") != task["run_id"] or not isinstance(document.get("attempts"), dict):
        raise ValueError("Jira 状态同步记录损坏或不属于当前 task/run")
    return document


def save_state(base, task, state, recovery=None):
    with task_store.task_run_lock(base, task["issue_key"]):
        current = task_store.check_expected_run(base, task["issue_key"], task["run_id"])
        if recovery is None:
            task_store.require_development(base, current)
        else:
            trigger, original = recovery
            sync_recovery.require_receipt(base, current)
            if load_state(base, current)["attempts"].get(trigger) != original:
                raise ValueError("原 Jira 意图已变化，拒绝过期回执")
            sync_recovery.validate_update(original, state["attempts"][trigger],
                {"completed_at", "readback_ref", "readback_status", "outcome", "reason", "message", "guidance"})
        if task.get("_revision") != current["_revision"]:
            raise ValueError("工位 revision 已变化，拒绝过期 Jira 状态回执")
        task_store._write_json_atomic(state_path(base, task), state)


def config(base, task):
    profile = project_rules.load_profile(station=base)
    result = profile.get("jira", {}).get("status_sync")
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise ValueError("当前 Project 未配置 Jira 状态同步")
    if task["task_class"] not in result.get("task_classes", []):
        # 可采集和人工交接不代表新增自动转换权限。
        from workflow import jira_collect
        jira_collect.config(base, task)
        return dict(result, attempts={}, field_mappings={})
    overrides = result.get("by_task_class", {})
    if not isinstance(overrides, dict):
        raise ValueError("Jira 状态同步 by_task_class 必须是对象")
    if task["task_class"] in overrides:
        override = overrides[task["task_class"]]
        if not isinstance(override, dict) or not isinstance(override.get("attempts"), dict):
            raise ValueError("Jira 任务类型状态同步配置无效")
        result = dict(result, **override)
    return result


def read_input(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("无法读取 Jira 快照：%s" % error) from error
    if not isinstance(document, dict) or not isinstance(document.get("source_ref"), str) or not document["source_ref"].strip():
        raise ValueError("Jira 快照必须包含可回查 source_ref")
    return document


def issue_from(snapshot, issue_key):
    issue = snapshot.get("issue")
    if not isinstance(issue, dict) or str(issue.get("key", "")).upper() != issue_key:
        raise ValueError("Jira 快照不是当前任务")
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Jira 快照缺少 issue.fields")
    status = fields.get("status")
    if not isinstance(status, dict) or not isinstance(status.get("name"), str) or not status["name"]:
        raise ValueError("Jira 快照缺少当前状态")
    return issue, fields, status


def strict_checkpoint_ready(base, task, checkpoint, snapshot=None):
    rules = quality.config(base, task)
    if not quality.enabled(task, rules):
        return False, ["当前任务未启用质量检查"]
    report = quality.report(quality.load(base, task), rules, jira_tests.with_snapshot(quality.context(base, task), snapshot, rules), base=base, task=task)
    view = report["checkpoints"].get(checkpoint)
    if not view or not view["reviewed"]:
        return False, ["质量检查点 %s 尚未有效确认" % checkpoint]
    if quality.checkpoint_outcome(view) not in ("accept", "not_applicable"):
        return False, ["质量检查点 %s 不是通过或明确不适用" % checkpoint]
    problems = []
    for key in view["due"]:
        item = report["items"][key]
        item_decision = (item.get("decision") or {}).get("decision", {})
        if not quality.item_accepted(item, ("accept", "not_applicable")):
            problems.append("检查项 %s 未通过或未明确不适用" % key)
    return not problems, problems


def tests_passed_ready(base, task, snapshot):
    """按项目验收检查点核对 Jira 事实，不创建或编辑 Jira Test。"""
    quality_rules = quality.config(base, task)
    checkpoint_id = jira_tests.acceptance_checkpoint(quality_rules or {})
    ready, problems = strict_checkpoint_ready(base, task, checkpoint_id, snapshot)
    if not ready:
        return False, "quality_not_verified", problems, []
    report = quality.report(quality.load(base, task), quality_rules, jira_tests.with_snapshot(quality.context(base, task), snapshot, quality_rules), base=base, task=task)
    checkpoint = report["checkpoints"][checkpoint_id]
    outcome = quality.checkpoint_outcome(checkpoint)
    if outcome != "accept":
        return False, "quality_not_verified", ["%s 关联用例验收必须由用户确认通过，不能以不适用或风险处置进入 Tests Passed" % checkpoint_id], []
    problems, tests, ignored = jira_tests.linked_tests(snapshot, task["issue_key"], quality_rules)
    problems.extend(jira_tests.confirmation_problems(report, tests, quality_rules))
    if problems:
        return False, "linked_test_facts_not_ready", problems, ignored
    return True, "", [], ignored


def transition_for(snapshot, rule):
    transitions = snapshot.get("transitions")
    if not isinstance(transitions, list):
        return None
    configured_id = str(rule.get("transition_id") or "")
    for transition in transitions:
        if not isinstance(transition, dict):
            continue
        if transition.get("isAvailable") is False:
            continue
        target = transition.get("to") or {}
        if configured_id and str(transition.get("id") or "") != configured_id:
            continue
        if not status_matches(rule, target.get("name"), "to"):
            continue
        return transition
    return None


def status_matches(rule, status, direction):
    configured = rule.get(direction, [])
    names = set(configured if isinstance(configured, list) else [configured])
    names.update(rule.get(direction + "_aliases", []))
    return status in names


def missing_fields(fields, transition):
    missing = []
    screen_fields = transition.get("fields") or {}
    if not isinstance(screen_fields, dict):
        return missing
    for key, metadata in screen_fields.items():
        if not isinstance(metadata, dict) or metadata.get("required") is not True:
            continue
        value = fields.get(key)
        if value is None or value == "" or value == []:
            missing.append({"field": key, "name": metadata.get("name") or key})
    return sorted(missing, key=lambda item: item["field"])


def local_source_available(task, source):
    value = task
    for part in source.split("."):
        if not isinstance(value, dict) or part not in value:
            return False
        value = value[part]
    return value not in (None, "", [], {})


def field_mapping(field, name, rules):
    for logical, mapping in rules.get("field_mappings", {}).items():
        if field == logical or field in mapping.get("aliases", []) or name in mapping.get("aliases", []):
            return logical, mapping
    return field, {}


def guidance_for(fields, rules, task):
    configured = rules.get("field_guidance", {})
    result = []
    for item in fields:
        field = item["field"] if isinstance(item, dict) else item
        name = item.get("name", field) if isinstance(item, dict) else field
        logical, mapping = field_mapping(field, name, rules)
        sources = mapping.get("local_sources", [])
        result.append({
            "field": field,
            "name": name,
            "mapping": logical,
            "collect_at": mapping.get("collect_at", "执行 Jira 转换前"),
            "local_sources": sources,
            "available_local_sources": [source for source in sources if local_source_available(task, source)],
            "auto_fill": mapping.get("auto_fill", False),
            "guidance": mapping.get("guidance") or configured.get(
                field, "在 Jira 转换面板补齐该必填字段并回读；没有可信来源时跳过状态流转。"),
        })
    return result


@sync_recovery.locked
def prepare(base, issue_key, trigger, snapshot, operation_id=None):
    task = task_store.read_task(base, issue_key)
    task_store.require_development(base, task)
    rules = config(base, task)
    rule = rules.get("attempts", {}).get(trigger)
    if not isinstance(rule, dict):
        if not trigger.startswith("native:"):
            raise ValueError("未知 Jira 状态同步节点：%s" % trigger)
        transition = next((t for t in snapshot.get("transitions", []) if "native:" + str(t.get("id")) == trigger), None)
        if not transition:
            raise ValueError("未回读当前原生转换，不能猜测 ID")
        from workflow import jira_collect
        packet = jira_collect.collect(base, task, "transition", snapshot)
        return {"trigger": trigger, "outcome": "handoff", "reason": "human_only_transition", "decision_packet": packet,
                "transition_id": str(transition["id"]), "guidance": [{"guidance": "本转换未获自动执行授权，完成本节点统一决策后交研发处理。"}]}
    if operation_id is not None and not re.fullmatch(r"op-[a-z0-9-]{8,80}", operation_id):
        raise ValueError("Jira operation_id 必须为稳定 op- 编号")
    issue, fields, status = issue_from(snapshot, issue_key)
    allowed_types = rules.get("issue_type_ids")
    if allowed_types is not None and str((fields.get("issuetype") or {}).get("id", "")) not in allowed_types:
        raise ValueError("Jira 工作类型与当前任务状态同步配置不匹配")
    state = load_state(base, task)
    previous = state["attempts"].get(trigger)
    if operation_id is not None:
        known = next((v for v in state.get("attempts_by_id", {}).values() if v.get("operation_id") == operation_id), None)
        if known and known.get("trigger") != trigger:
            raise ValueError("operation_id 不能复用于另一转换")
        if known and known.get("operation_id") != (previous or {}).get("operation_id"):
            return dict(known, repeated=True)
    retry_history = []
    request = None
    if operation_id is not None:
        prior_operation = previous and previous.get("operation_id")
        # 不以状态尚未改变或一次超时推断未写入；不同意图先消解前次结果。
        if previous and prior_operation != operation_id and previous.get("outcome") in ("ready", "unknown", "failed"):
            raise ValueError("前次 Jira 写入结果未确认，先回读原 operation，禁止换编号重发")
        field_keys = set()
        transition = transition_for(snapshot, rule)
        if transition:
            field_keys.update(transition.get("fields", {}))
        for logical in rule.get("field_requirements", []):
            field_keys.update(rules.get("field_mappings", {}).get(logical, {}).get("aliases", [logical]))
        request = {"run_id": task["run_id"], "trigger": trigger, "operation_id": operation_id,
                   "expected_from": previous["from_status"] if previous and prior_operation == operation_id else status["name"],
                   "inputs": {key: fields.get(key) for key in sorted(field_keys) if key in fields},
                   "form_digest": quality.digest((transition or {}).get("fields", {})),
                   "rule_digest": quality.digest(rule),
                   "decisions": {key: value["digest"] for key, value in state.get("decisions", {}).items() if key in field_keys}}
        if previous and prior_operation == operation_id and previous.get("outcome") != "skipped" and previous.get("request_digest") != quality.digest(request):
            # 到达目标后的回读不重建转换字段，原意图保持可恢复。
            if status["name"] not in previous.get("target_statuses", []):
                raise ValueError("同一 Jira operation 的确认输入已变化，请先回读原意图结果")
        if previous and prior_operation != operation_id:
            previous = None
    if previous:
        retryable = (previous.get("outcome") == "skipped" and
                     previous.get("reason") in ("quality_not_verified", "linked_test_facts_not_ready",
                                                "required_fields_missing", "transition_unavailable",
                                                "local_stage_mismatch", "jira_status_mismatch", "assignee_mismatch", "decision_inputs_pending"))
        if not retryable:
            return dict(previous, repeated=True)
        retry_history = list(previous.get("preflight_history", [])) + [{
            "at": previous.get("at"), "outcome": previous.get("outcome"),
            "reason": previous.get("reason"), "source_ref": previous.get("source_ref"),
        }]
    issue, fields, status = issue_from(snapshot, issue_key)
    record = {"trigger": trigger, "at": now(), "source_ref": snapshot["source_ref"],
              "from_status": status["name"], "target_status": rule["to"],
              "target_statuses": sorted({rule["to"], *rule.get("to_aliases", [])})}
    if retry_history:
        record["preflight_history"] = retry_history
    if operation_id is not None:
        record.update(operation_id=operation_id, request=request, request_digest=quality.digest(request),
                      attempt_id=quality.digest(request))
    record["field_plan"] = guidance_for(rule.get("field_requirements", []), rules, task)
    if task["stage"] not in rule.get("local_stages", []):
        record.update(outcome="skipped", reason="local_stage_mismatch",
                      guidance=[{"guidance": "当前本地阶段为 %s，本节点只允许在 %s 尝试。" %
                                             (task["stage"], "、".join(rule.get("local_stages", [])))}])
    elif trigger == "tests_passed":
        ready, reason, problems, ignored = tests_passed_ready(base, task, snapshot)
        ignored_view = [{"key": test["key"], "test_type": test["test_type"],
                         "guidance": test["guidance"]} for test in ignored]
        if not ready:
            record.update(outcome="skipped", reason=reason, ignored_tests=ignored_view,
                          guidance=[{"guidance": problem} for problem in problems])
        elif status_matches(rule, status["name"], "to"):
            record.update(outcome="satisfied", reason="target_already_reached",
                          ignored_tests=ignored_view, guidance=[])
        elif not status_matches(rule, status["name"], "from"):
            record.update(outcome="skipped", reason="jira_status_mismatch", ignored_tests=ignored_view,
                          guidance=[{"guidance": "Jira 当前状态为 %s，不能由本节点自动流转到 %s；PR Ready 时人工处理。" %
                                                     (status["name"], rule["to"])}])
        else:
            record = prepare_transition(record, snapshot, fields, rule, rules, task)
            record["ignored_tests"] = ignored_view
    elif status_matches(rule, status["name"], "to"):
        record.update(outcome="satisfied", reason="target_already_reached", guidance=[])
    elif not status_matches(rule, status["name"], "from"):
        record.update(outcome="skipped", reason="jira_status_mismatch",
                      guidance=[{"guidance": "Jira 当前状态为 %s，不能由本节点自动流转到 %s；PR Ready 时人工处理。" %
                                             (status["name"], rule["to"])}])
    elif rule.get("require_current_assignee"):
        assignee = fields.get("assignee") or {}
        current_user = snapshot.get("current_user") or {}
        if not assignee.get("accountId") or assignee.get("accountId") != current_user.get("accountId"):
            record.update(outcome="skipped", reason="assignee_mismatch",
                          guidance=guidance_for(["assignee"], rules, task))
        else:
            record = prepare_transition(record, snapshot, fields, rule, rules, task)
    elif rule.get("require_quality_checkpoint"):
        ready, problems = strict_checkpoint_ready(base, task, rule["require_quality_checkpoint"])
        if not ready:
            record.update(outcome="skipped", reason="quality_not_verified",
                          guidance=[{"guidance": problem} for problem in problems])
        else:
            record = prepare_transition(record, snapshot, fields, rule, rules, task)
    else:
        record = prepare_transition(record, snapshot, fields, rule, rules, task)
    if operation_id is not None:
        from workflow import jira_collect
        checkpoint = "intake" if trigger == "takeover" else "acceptance"
        packet = jira_collect.collect(base, task, checkpoint, snapshot)
        record["decision_packet"] = packet
        # 只阻塞本次外部转换，不阻塞本地研发。
        if record["outcome"] == "ready" and (packet["pending"] or any(x.get("due") for x in packet["unknown"])):
            record.update(outcome="skipped", reason="decision_inputs_pending")
        state.setdefault("attempts_by_id", {})[record["attempt_id"]] = copy.deepcopy(record)
    state["attempts"][trigger] = record
    save_state(base, task, state)
    return record


def prepare_transition(record, snapshot, fields, rule, rules, task):
    transition = transition_for(snapshot, rule)
    if transition is None:
        record.update(outcome="skipped", reason="transition_unavailable",
                      guidance=[{"guidance": "Jira 未返回到 %s 的可用转换；核对当前状态、Workflow 和权限。" % rule["to"]}])
        return record
    if not isinstance(transition.get("fields"), dict):
        record.update(outcome="skipped", reason="transition_unavailable",
                      guidance=[{"guidance": "转换表单未完整回读，请读取 transitions.fields"}])
        return record
    missing = missing_fields(fields, transition)
    if missing:
        record.update(outcome="skipped", reason="required_fields_missing",
                      missing_fields=[item["field"] for item in missing],
                      guidance=guidance_for(missing, rules, task))
        return record
    record.update(outcome="ready", reason="transition_prepared", transition_id=str(transition["id"]),
                  transition_name=transition.get("name") or rule.get("transition_name"), guidance=[])
    return record


@sync_recovery.locked
def complete(base, issue_key, trigger, outcome, snapshot, message, operation_id=None):
    if outcome not in ("failed", "unknown", "not_written"):
        raise ValueError("无效 Jira 回执结果")
    task = task_store.read_task(base, issue_key)
    state = load_state(base, task)
    record = state["attempts"].get(trigger)
    if not record or record.get("outcome") not in ("ready", "unknown", "failed"):
        raise ValueError("本节点没有待完成的 Jira 状态转换意图")
    sync_recovery.require_receipt(base, task)
    original = copy.deepcopy(record)
    if operation_id is not None and record.get("operation_id") != operation_id:
        raise ValueError("Jira 回执不是原 operation")
    if outcome == "not_written":
        evidence = snapshot.get("operation_result", {})
        if (not operation_id or evidence.get("operation_id") != operation_id or evidence.get("effect") != "not_written"
                or not evidence.get("source_ref")):
            raise ValueError("确认未写入需要原操作明确结果来源，当前状态不等于目标不能证明未写入")
    _, _, status = issue_from(snapshot, issue_key)
    reached = status["name"] in record.get("target_statuses", [record["target_status"]])
    record["completed_at"] = now()
    record["readback_ref"] = snapshot["source_ref"]
    record["readback_status"] = status["name"]
    record["outcome"] = "succeeded" if reached else outcome
    record["reason"] = "target_read_back" if reached else "transition_%s" % outcome
    if message:
        text = str(message)[:600]
        admission = project_rules.load_admission(station=base)
        record["message"] = ("外部错误信息含敏感内容，原文未保存" if project_rules.scan_sensitive(admission, text)
                             else text)
    if not reached:
        record["guidance"] = [{"guidance": "自动状态转换未确认成功；本地流程继续，PR Ready 时根据 Jira 原始提示人工处理。"}]
    if record.get("attempt_id"):
        state.setdefault("attempts_by_id", {})[record["attempt_id"]] = copy.deepcopy(record)
    save_state(base, task, state, recovery=(trigger, original))
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--issue-key", required=True)
    p.add_argument("--trigger", required=True)
    p.add_argument("--operation-id", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--dir", default=".")
    p = sub.add_parser("complete")
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--issue-key", required=True)
    p.add_argument("--trigger", required=True)
    p.add_argument("--operation-id", required=True)
    p.add_argument("--outcome", choices=("failed", "unknown", "not_written"), required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--message", default="")
    p.add_argument("--dir", default=".")
    p = sub.add_parser("status")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--dir", default=".")
    for name in ("collect", "confirm"):
        p = sub.add_parser(name)
        p.add_argument("--issue-key", required=True)
        p.add_argument("--expected-run-id", required=True)
        p.add_argument("--checkpoint", choices=("intake", "design_review", "acceptance", "pr_review", "transition"), required=True)
        p.add_argument("--input", required=True)
        p.add_argument("--dir", default=".")
    args = parser.parse_args()
    try:
        task_store.station_project(args.dir)
        issue = task_store.resolve_issue(args.dir, args.issue_key)
        with task_store.task_run_lock(args.dir, issue):
            task = task_store.read_task(args.dir, issue)
            if args.command != "status":
                task_store.check_expected_run(args.dir, issue, args.expected_run_id)
                if args.command != "complete":
                    task_store.require_development(args.dir, task)
            if args.command in ("collect", "confirm"):
                from workflow import jira_collect
                document = json.loads(Path(args.input).read_text())
                snapshot = document["snapshot"]
                proposals = document.get("proposals", {})
                if args.command == "collect":
                    result = jira_collect.collect(args.dir, task, args.checkpoint, snapshot, proposals)
                else:
                    result = jira_collect.confirm(args.dir, issue, args.expected_run_id, args.checkpoint,
                        snapshot, proposals, document["field_digests"], document["proof"])
            elif args.command == "prepare":
                result = prepare(args.dir, issue, args.trigger, read_input(args.input), args.operation_id)
            elif args.command == "complete":
                result = complete(args.dir, issue, args.trigger, args.outcome, read_input(args.input), args.message, args.operation_id)
            else:
                result = load_state(args.dir, task)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
