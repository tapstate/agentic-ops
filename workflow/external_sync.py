"""汇总同步待办并导入字段回读；不发送请求、不改变执行事实或阶段。"""

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))

import argparse
import json
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import jira_status, jira_watermark, quality, task_store


def state_path(base, task):
    return task_store.task_directory(base, task["issue_key"]) / ("jira-fields-%s.json" % quality.digest(task["run_id"])[:24])


def load(base, task):
    path = state_path(base, task)
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("run_id") != task["run_id"] or document.get("issue_key") != task["issue_key"] or not isinstance(document.get("fields"), dict):
        raise ValueError("字段同步记录不属于当前 task/run 或已损坏")
    return document["fields"]


def record_readback(base, task, payload):
    quality.quality_contract.validate(payload, "jira-field-readback.schema.json")
    key = payload["fact_key"]
    value = task.get("facts", {}).get(key)
    if value is None or payload.get("expected_fact_digest") != quality.digest(value):
        raise ValueError("本地有效事实已变化或摘要不匹配，先重新核对")
    issue = payload.get("issue", {})
    if issue.get("key") != task["issue_key"] or not isinstance(payload.get("source_ref"), str) or not payload["source_ref"].strip():
        raise ValueError("字段回读必须绑定当前 Jira 任务与来源")
    actual = issue.get("fields", {}).get(payload["jira_field"])
    expected = value
    if key == "problem_version":
        plan = task["facts"]["issue_version_plan"]
        from workflow import issue_versions
        if payload["jira_field"] != issue_versions.rules(base, task)["field"]:
            raise ValueError("问题版本只能核对 Jira 影响版本字段")
        expected = sorted(v["name"] for v in plan["versions"])
        actual = sorted(v["name"] for v in actual) if isinstance(actual, list) and all(isinstance(v, dict) and isinstance(v.get("name"), str) for v in actual) else None
    if actual != expected:
        raise ValueError("Jira 字段与本地有效事实不一致，保留待办")
    record = {"status": "verified", "fact_digest": quality.digest(value), "jira_field": payload["jira_field"],
              "source_ref": payload["source_ref"]}
    from workflow import project_rules
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(record, ensure_ascii=False)):
        raise ValueError("回读来源含敏感内容，请脱敏")
    records = load(base, task)
    records[key] = record
    with task_store.task_run_lock(base, task["issue_key"]):
        current = task_store.check_expected_run(base, task["issue_key"], task["run_id"])
        task_store.require_development(base, current)
        if task.get("_revision") != current["_revision"]:
            raise ValueError("工位 revision 已变化，拒绝过期字段回读")
        if current.get("facts") != task.get("facts"):
            raise ValueError("本地事实已变化，拒绝过期回读")
        task_store._write_json_atomic(state_path(base, task), {"issue_key": task["issue_key"], "run_id": task["run_id"], "fields": records})
    return record


def existing_warnings(base, task):
    """只核验已登记外部意图/回读；清理不重新判断当前质量准入。"""
    result = []

    def add(kind, status, reason, reference="", run_id=None):
        result.append({"kind": kind, "status": status, "reason": reason,
                       "source_ref": reference, "run_id": run_id or task["run_id"],
                       "recovery": "先回读定位原操作，禁止盲目重发" if status == "unknown"
                       else "下次阶段同步或 Jira 评论时说明；按已有授权补写并回读"})

    watermark = jira_watermark.status(base, task)
    if watermark["outcome"] != "verified":
        unresolved = (watermark["outcome"] in ("ready", "unknown", "failed") or
                      (watermark["outcome"] == "stale" and watermark.get("readback_value") != watermark.get("version")))
        add("agenticops_version", "unknown" if unresolved else "deferred",
            watermark.get("reason", "水印未同步"), watermark.get("source_ref", ""))
    attempts = jira_status.load_state(base, task)["attempts"]
    for node, record in attempts.items():
        if (not isinstance(record, dict) or record.get("outcome") not in
                ("ready", "unknown", "failed", "not_written", "skipped", "satisfied", "succeeded") or
                record.get("run_id", task["run_id"]) != task["run_id"] or
                record.get("issue_key", task["issue_key"]) != task["issue_key"]):
            raise ValueError("Jira 状态同步意图损坏或不属于当前 task/run")
        if record["outcome"] not in ("satisfied", "succeeded"):
            add("jira_status:" + node, "unknown" if record["outcome"] in ("ready", "unknown", "failed") else "deferred",
                record.get("reason", "状态尚未同步"), record.get("source_ref", ""))
    from workflow.quality_write import STATE_FILE
    for path in sorted(task_store.task_directory(base, task["issue_key"]).glob("quality-*.json")):
        if not STATE_FILE.fullmatch(path.name):
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        quality.quality_contract.validate(state, "quality-state.schema.json")
        old_task = dict(task, run_id=state["run_id"])
        if path != quality.state_path(base, old_task):
            raise ValueError("质量日志文件名与 run 不匹配")
        state = quality.load(base, old_task)
        for key, record in quality.replay(state)["publications"].items():
            if state["run_id"] != task["run_id"] and record["status"] not in ("intent", "unknown", "created"):
                continue
            if record["status"] != "verified":
                add("comment:" + key, "unknown" if record["status"] in ("intent", "unknown", "created") else "deferred",
                    record.get("reason", "评论状态：" + record["status"]), record.get("source_ref", ""), state["run_id"])
    return result


def warnings(base, task, report=None):
    result = existing_warnings(base, task)

    def add(kind, status, reason, reference=""):
        result.append({"kind": kind, "status": status, "reason": reason,
                       "source_ref": reference, "run_id": task["run_id"],
                       "recovery": "下次阶段同步或 Jira 评论时说明；按已有授权补写并回读"})

    from workflow import project_rules
    attempts = jira_status.load_state(base, task)["attempts"]
    status_rules = project_rules.load_profile(station=base).get("jira", {}).get("status_sync", {})
    if task["task_class"] in status_rules.get("task_classes", []):
        due = ["takeover"] if task.get("stage") != "waiting_takeover" else []
        if task.get("stage") in ("ci_validation", "completed"):
            due.append("tests_passed")
        for node in due:
            if node not in attempts:
                add("jira_status:" + node, "deferred", "本节点尚无同步记录")
    plan = task.get("facts", {}).get("issue_version_plan", {})
    version_receipt = load(base, task).get("problem_version", {})
    if plan.get("sync_status") == "pending" and version_receipt.get("fact_digest") != quality.digest(task.get("facts", {}).get("problem_version")):
        add("affected_versions", "deferred", "本地确认版本：%s；Jira 初始值：%s" % (
            "、".join(v["name"] for v in plan["versions"]),
            plan.get("observed", {}).get("issue", {}).get("fields", {}).get("versions")), plan.get("source_ref", ""))
    rules = quality.config(base, task)
    if not quality.enabled(task, rules):
        return result
    report = report or quality.report(quality.load(base, task), rules, quality.context(base, task), base=base, task=task)
    for checkpoint, view in report["checkpoints"].items():
        if view["reviewed"] and not view["published"]:
            add("checkpoint:" + checkpoint, "deferred", "检查点已确认，当前评论尚未回读验证")
    return result


def takeover_body(task, publication_id):
    """只从已保存的接管事实生成摘要，不推断方案、修改仓或验收结果。"""
    fields = task.get("facts", {}).get("jira_snapshot", {}).get("issue", {}).get("fields", {})
    lines = ["%s：已接管" % task["issue_key"]]
    if fields.get("summary"):
        lines.append("任务：%s" % fields["summary"])
    assignee = fields.get("assignee") or {}
    if assignee.get("displayName"):
        lines.append("接管时 Jira 负责人：%s" % assignee["displayName"])
    lines.extend(["运行：%s" % task["run_id"], "任务类型：%s" % task["task_class"]])
    baseline = task.get("engineering_baseline", {})
    if baseline.get("profile", {}).get("id"):
        lines.append("工程 Profile：%s" % baseline["profile"]["id"])
    version = baseline.get("resolution_input", {}).get("version")
    if version:
        lines.append("接管版本／主仓分支：%s" % version)
    repositories = baseline.get("repositories", {})
    lines.append("源码准备：已完成")
    if repositories:
        lines.append("冻结仓库数：%s" % len(repositories))
    lines.extend(["接管仅证明本地准备完成，不代表 Q1、实施方案或验收已通过。",
                  "记录：%s" % publication_id])
    return "\n\n".join(lines)


def actions(base, task, report=None):
    """从既有事实派生工作清单；查询不写草稿、意图或第二份队列。"""
    from workflow import sync_recovery
    rules = quality.config(base, task)
    enabled = quality.enabled(task, rules)
    if report is None:
        report = quality.report(quality.load(base, task), rules, quality.context(base, task), base=base, task=task) if enabled else {}
    pending = warnings(base, task, report)
    result = []
    try:
        task_store.require_development(base, task)
        active = True
    except ValueError:
        active = False
    try:
        sync_recovery.require_receipt(base, task)
        recovery = True
    except ValueError:
        recovery = False

    def add(key, kind, state, body=None, record=None, **extra):
        next_action = ("readback" if recovery else "maintenance_handoff") if state == "unknown" else (
            "prepare_with_authorization" if active else "manual_handoff")
        row = {"id": key, "kind": kind, "status": state, "issue_key": task["issue_key"],
               "run_id": task["run_id"], "next_action": next_action,
               "authorization": "复用有效授权；缺少时先取得对应动作授权，不得直接发送", **extra}
        if body is not None:
            row["body"] = body
        if record:
            row["record"] = record
        result.append(row)

    publications = report.get("publications", {})
    covered = set()
    for key, record in publications.items():
        if record["status"] == "verified":
            continue
        unknown = record["status"] in ("intent", "unknown", "created")
        cp = record.get("checkpoint")
        # 已发送或不明记录永远沿用原正文；失效且尚未发送的检查点等待新确认。
        if cp:
            covered.add(cp)
        add(key, "comment", "unknown" if unknown else "deferred", record=record,
            checkpoint=cp, refresh_required=not record.get("snapshot_current", False))
        if not unknown and (not record.get("snapshot_current", False) or record["status"] == "deferred"):
            result[-1]["next_action"] = "refresh_and_confirm" if active else "manual_handoff"
            if cp and report.get("checkpoints", {}).get(cp, {}).get("reviewed"):
                result[-1]["body"] = report["checkpoints"][cp]["publication_body"]
    if enabled and rules.get("publication_mode") == "checkpoint":
        intake_id = "takeover-" + quality.digest([task["issue_key"], task["run_id"]])[:20]
        if intake_id not in publications:
            if task.get("source_prepared"):
                add(intake_id, "takeover_comment", "not_attempted", body=takeover_body(task, intake_id))
        for cp, view in report.get("checkpoints", {}).items():
            if view["reviewed"] and not view["published"] and cp not in covered:
                key = cp + "-" + quality.digest([task["run_id"], view["publication_body"]])[:20]
                add(key, "checkpoint_comment", "not_attempted", body=view["publication_body"], checkpoint=cp)
                if any(record["status"] == "verified" and record["body"] == view["publication_body"] for record in publications.values()):
                    result[-1].update(next_action="manual_handoff", reason="相同正文已回读，但当前确认绑定变化；核对原记录，不重复发送")
    for warning in pending:
        kind = warning["kind"]
        if kind.startswith("comment:") and warning["run_id"] != task["run_id"]:
            key = kind.split(":", 1)[1]
            old = quality.replay(quality.load(base, dict(task, run_id=warning["run_id"])))
            add("previous-run:" + warning["run_id"] + ":" + key, "comment", "unknown", record=old["publications"][key])
            result[-1].update(run_id=warning["run_id"], next_action="maintenance_handoff",
                             reason="旧 run 外部结果未知；仅核对原操作，不借当前 run 发送或保存回执")
            continue
        if kind.startswith(("checkpoint:", "comment:")):
            continue
        add(kind, kind, warning["status"], reason=warning["reason"])
        if kind.startswith("jira_status:"):
            trigger = kind.split(":", 1)[1]
            record = jira_status.load_state(base, task)["attempts"].get(trigger)
            result[-1]["record"] = record
            if record is None:
                result[-1]["status"] = "not_attempted"
            rule = jira_status.config(base, task).get("attempts", {}).get(trigger, {})
            if warning["status"] != "unknown" and task.get("stage") not in rule.get("local_stages", []):
                result[-1]["next_action"] = "manual_handoff"
        elif kind == "agenticops_version":
            record = jira_watermark.status(base, task)
            result[-1]["record"] = record
            if record["outcome"] == "missing":
                result[-1]["status"] = "not_attempted"
            result[-1]["original_target_observed"] = bool(record.get("readback_ref") and record.get("readback_value") == record.get("version"))
            result[-1]["current_version_sync"] = "需按当前产品版本另行核验；本回执不改变原目标"
    # 评论来源及正文仍在 prepare_write 检查；查询不把本地绝对路径加入对外正文。
    return {"sync_actions": result, "warnings": pending, "sync_diagnostics": [],
            "sync_boundary": "只读待办不是写入成功；按授权原生执行并回读，未知结果不重发"}


def safe_actions(base, task, report=None):
    """展示失败不覆盖已成功的本地变更；安全判定不得使用此容错出口。"""
    try:
        return actions(base, task, report)
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        return {"sync_actions": [], "sync_diagnostics": ["同步待办生成失败（%s），请运行 external_sync.py status 核查；不得重试已成功的本地变更" % type(error).__name__]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "readback"))
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--dir", default=".")
    parser.add_argument("--expected-run-id")
    parser.add_argument("--input")
    args = parser.parse_args()
    try:
        issue = task_store.resolve_issue(args.dir, args.issue_key)
        with task_store.task_run_lock(args.dir, issue):
            task = task_store.read_task(args.dir, issue)
            if args.command == "readback":
                task_store.check_expected_run(args.dir, issue, args.expected_run_id)
                task_store.require_development(args.dir, task)
                if not args.input:
                    raise ValueError("readback 需要 --input")
                result = record_readback(args.dir, task, json.loads(Path(args.input).read_text(encoding="utf-8")))
            else:
                result = {**actions(args.dir, task),
                          "fact_digests": {key: quality.digest(value) for key, value in task.get("facts", {}).items()}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
