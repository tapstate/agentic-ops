#!/usr/bin/env python3
"""按任务 run 记录失败归因、修复轮次与具体人工处置；不执行修复。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import quality, task_store


def text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("缺少 %s" % name)
    return value


def problem_id(repository, check_id):
    return hashlib.sha256(json.dumps([repository, check_id]).encode()).hexdigest()[:24]


def user_proof(event):
    proof = event.get("proof") or {}
    for field in ("actor", "source", "reference", "at"):
        text(proof.get(field), "人工决定 " + field)
    if proof["source"] != "user_message":
        raise ValueError("需要研发明确决定的消息来源")
    quality.check_proof(proof)
    text(event.get("reason"), "决定原因")


def reduce(problems, event):
    action = event.get("action")
    if action == "observe":
        repository = text(event.get("repository"), "repository")
        check_id = text(event.get("check_id"), "稳定检查项 check_id")
        key = problem_id(repository, check_id)
        attribution = event.get("attribution")
        if attribution not in ("current_change", "preexisting", "environment", "unknown"):
            raise ValueError("失败归因无效")
        text(event.get("evidence"), "归因证据")
        prior = problems.get(key)
        if prior and prior["status"] == "running":
            raise ValueError("先记录当前修复轮次结果，不能重开问题")
        p = problems.setdefault(key, {"repository": repository, "check_id": check_id,
                                     "attempts": 0, "limit": 3, "decisions": [], "rounds": []})
        p.update(label=text(event.get("label"), "问题说明"), attribution=attribution,
                 evidence=event["evidence"], status="unresolved", permission=None)
        return key
    key = text(event.get("problem_id"), "problem_id")
    if key not in problems:
        raise ValueError("问题未登记")
    p = problems[key]
    if action == "start":
        if p["status"] != "unresolved":
            raise ValueError("问题不处于待修复状态")
        if p["attribution"] != "current_change" and p.get("permission") != "continue":
            raise ValueError("非当前变更或归因不明，必须由研发决定")
        if p["attempts"] >= p["limit"]:
            raise ValueError("累计修复轮数已用尽，需要研发追加具体轮数")
        stage = event.get("stage")
        if stage not in ("local", "ci", "review"):
            raise ValueError("修复来源无效")
        p["attempts"] += 1
        p["status"] = "running"
        p["rounds"].append({"number": p["attempts"], "stage": stage, "result": "pending"})
    elif action in ("finish", "revalidate", "manual_result"):
        if action == "finish" and p["status"] != "running":
            raise ValueError("没有进行中的修复轮次")
        if action == "revalidate" and p["status"] != "resolved":
            raise ValueError("仅已解决问题允许无修改重验")
        if action == "manual_result":
            if p["status"] != "unresolved":
                raise ValueError("先保留进行中轮次的实际结果，再记录人工处理")
            user_proof(event)
        result = event.get("result")
        if result not in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN", "SKIPPED"):
            raise ValueError("修复结果无效")
        revision = text(event.get("source_revision"), "源码版本")
        if not (quality.exact_commit(revision) or quality.exact_worktree(revision)):
            raise ValueError("修复结果必须绑定精确代码版本")
        evidence = text(event.get("evidence"), "重验报告或未执行原因")
        if action == "finish":
            p["rounds"][-1].update(result=result, source_revision=revision, evidence=evidence)
        p["latest_result"] = dict(result=result, source_revision=revision, evidence=evidence)
        p["status"] = "resolved" if result == "PASS" else "unresolved"
    elif action == "decide":
        if p["status"] != "unresolved":
            raise ValueError("先记录当前结果，再对未解决问题决策")
        user_proof(event)
        decision = event.get("decision")
        if decision == "continue":
            additional = event.get("additional_rounds")
            if type(additional) is not int or additional < 1:
                raise ValueError("追加轮数必须为正整数")
            # 授权从当前累计次数开始，不把未用预算悄悄叠加到用户决定上。
            p["limit"] = p["attempts"] + additional
            p["permission"] = "continue"
        elif decision == "accept_gap":
            text(event.get("uncovered"), "接受的未覆盖范围")
            text(event.get("follow_up"), "后续处理")
            p["status"] = "accepted_gap"
        else:
            raise ValueError("决定必须是具体续修或接受缺口")
        p["decisions"].append(event)
    else:
        raise ValueError("未知失败操作")
    return key


def path(base, task):
    suffix = hashlib.sha256(task["run_id"].encode()).hexdigest()[:24]
    return task_store.task_directory(base, task["issue_key"]) / ("failures-%s.json" % suffix)


def load(base, task):
    file = path(base, task)
    identity = {"schema_version": 1, "issue_key": task["issue_key"], "run_id": task["run_id"]}
    state = json.loads(file.read_text()) if file.exists() else dict(identity, revision=0, events=[])
    if any(state.get(k) != v for k, v in identity.items()) or not isinstance(state.get("events"), list):
        raise ValueError("失败记录身份或格式不一致")
    if type(state.get("revision")) is not int or state["revision"] != len(state["events"]):
        raise ValueError("失败记录 revision 损坏")
    problems = {}
    for event in state["events"]:
        if not isinstance(event, dict):
            raise ValueError("失败事件损坏")
        reduce(problems, event)
    return state, problems


def apply(base, issue, run, revision, event):
    with task_store.task_run_lock(base, issue):
        task_store.resolve_active_issue(base, issue)
        task = task_store.check_expected_run(base, issue, run)
        state, problems = load(base, task)
        if type(revision) is not int or revision != state["revision"]:
            raise ValueError("失败记录已变化，请回读后处理")
        if event.get("action") == "observe" and event.get("repository") not in {
                r["repository"] for r in task.get("repositories", [])}:
            raise ValueError("失败仓库不属于当前任务")
        key = reduce(problems, event)
        from workflow import project_rules
        if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(event, ensure_ascii=False)):
            raise ValueError("失败记录含敏感内容，请脱敏")
        state["events"].append(event)
        state["revision"] += 1
        task_store._write_json_atomic(path(base, task), state)
        return dict(state, problems=problems, problem_id=key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "apply"))
    parser.add_argument("--dir", default=".")
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--expected-run-id")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--input")
    args = parser.parse_args()
    try:
        issue = task_store.resolve_issue(args.dir, args.issue_key)
        if args.command == "apply":
            if not args.input:
                raise ValueError("apply 需要 --input")
            result = apply(args.dir, issue, args.expected_run_id, args.revision,
                           json.loads(Path(args.input).read_text()))
        else:
            task = task_store.read_task(args.dir, issue)
            state, problems = load(args.dir, task)
            result = dict(state, problems=problems)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
