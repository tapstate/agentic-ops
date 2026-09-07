"""汇总同步待办并导入字段回读；不发送请求、不改变执行事实或阶段。"""
import argparse
import json
import sys
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
    if project_rules.scan_sensitive(project_rules.load_admission(workspace=base), json.dumps(record, ensure_ascii=False)):
        raise ValueError("回读来源含敏感内容，请脱敏")
    records = load(base, task)
    records[key] = record
    task_store._write_json_atomic(state_path(base, task), {"issue_key": task["issue_key"], "run_id": task["run_id"], "fields": records})
    return record


def warnings(base, task, report=None):
    result = []

    def add(kind, status, reason, reference="", run_id=None):
        result.append({"kind": kind, "status": status, "reason": reason,
                       "source_ref": reference, "run_id": run_id or task["run_id"],
                       "recovery": "先回读定位原操作，禁止盲目重发" if status == "unknown"
                       else "下次阶段同步或 Jira 评论时说明；按已有授权补写并回读"})

    watermark = jira_watermark.status(base, task)
    if watermark["outcome"] != "verified":
        add("agenticops_version", "unknown" if watermark["outcome"] in ("ready", "unknown") else "deferred",
            watermark.get("reason", "水印未同步"), watermark.get("source_ref", ""))
    attempts = jira_status.load_state(base, task)["attempts"]
    from workflow import project_rules
    status_rules = project_rules.load_profile(workspace=base).get("jira", {}).get("status_sync", {})
    if task["task_class"] in status_rules.get("task_classes", []):
        due = ["takeover"] if task.get("stage") != "waiting_takeover" else []
        if task.get("stage") in ("ci_validation", "completed"):
            due.append("tests_passed")
        for node in due:
            if node not in attempts:
                add("jira_status:" + node, "deferred", "本节点尚无同步记录")
    for node, record in attempts.items():
        if record["outcome"] not in ("satisfied", "succeeded"):
            add("jira_status:" + node, "unknown" if record["outcome"] in ("ready", "unknown") else "deferred",
                record.get("reason", "状态尚未同步"), record.get("source_ref", ""))
    plan = task.get("facts", {}).get("issue_version_plan", {})
    version_receipt = load(base, task).get("problem_version", {})
    if plan.get("sync_status") == "pending" and version_receipt.get("fact_digest") != quality.digest(task.get("facts", {}).get("problem_version")):
        add("affected_versions", "deferred", "本地确认版本：%s；Jira 初始值：%s" % (
            "、".join(v["name"] for v in plan["versions"]),
            plan.get("observed", {}).get("issue", {}).get("fields", {}).get("versions")), plan.get("source_ref", ""))
    rules = quality.config(base)
    if not quality.enabled(task, rules):
        return result
    report = report or quality.report(quality.load(base, task), rules, quality.context(base, task))
    for checkpoint, view in report["checkpoints"].items():
        if view["reviewed"] and not view["published"]:
            add("checkpoint:" + checkpoint, "deferred", "检查点已确认，当前评论尚未回读验证")
    from workflow.quality_write import STATE_FILE
    for path in sorted(task_store.task_directory(base, task["issue_key"]).glob("quality-*.json")):
        if not STATE_FILE.fullmatch(path.name):
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        quality.quality_contract.validate(state, "quality-state.schema.json")
        for key, record in quality.replay(state)["publications"].items():
            if state["run_id"] != task["run_id"] and record["status"] not in ("intent", "unknown", "created"):
                continue
            if record["status"] != "verified":
                add("comment:" + key, "unknown" if record["status"] in ("intent", "unknown", "created") else "deferred",
                    record.get("reason", "评论状态：" + record["status"]), record.get("source_ref", ""), state["run_id"])
    return result


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
            task = json.loads(task_store.task_path(args.dir, issue).read_text(encoding="utf-8"))
            if args.command == "readback":
                task_store.check_expected_run(args.dir, issue, args.expected_run_id)
                if not args.input:
                    raise ValueError("readback 需要 --input")
                result = record_readback(args.dir, task, json.loads(Path(args.input).read_text(encoding="utf-8")))
            else:
                result = {"warnings": warnings(args.dir, task),
                          "fact_digests": {key: quality.digest(value) for key, value in task.get("facts", {}).items()}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
