"""Jira 评论写入的本地意图/回执/回读账本；外部调用由原生工具负责。"""
from workflow import quality, task_store
import json


def snapshot(model, rules, ctx):
    return quality.digest({"items": model["items"], "checkpoints": model["checkpoints"],
                           "context": ctx, "rules": rules})


def reduce(model, command, rules, ctx):
    action, p = command["action"], command["payload"]
    records = model["publications"]
    key = p["id"]
    if action == "draft":
        previous = records.get(key)
        if previous and previous["status"] in ("intent", "unknown", "created", "verified"):
            raise ValueError("该草稿已准备发送或已发送，保留现场；不得覆盖")
        records[key] = {"body": p["body"], "status": "draft", "snapshot": snapshot(model, rules, ctx),
                        "site": rules["jira"]["site"], "issue_key": ctx["issue_key"]}
        records[key]["digest"] = quality.digest(records[key])
        return
    record = records.get(key)
    if not record:
        raise ValueError("草稿不存在")
    if action in ("confirm", "prepare_write"):
        if p["digest"] != record["digest"] or record["snapshot"] != snapshot(model, rules, ctx):
            raise ValueError("草稿正文或质量事实已变化，需要重新生成并确认")
        if action == "confirm":
            if record["status"] not in ("draft", "confirmed"):
                raise ValueError("草稿已进入发送流程")
            quality.check_proof(p["proof"])
            record["proof"] = p["proof"]
            record["status"] = "confirmed"
        else:
            if record["status"] != "confirmed":
                raise ValueError("只能准备已确认且尚未发送的草稿；未知结果不得重试")
            for other in records.values():
                if other is record:
                    continue
                if other["status"] in ("intent", "unknown", "created"):
                    raise ValueError("已有外部写入待核对，请先回读，不得并行重试")
                if other["status"] == "verified" and other["body"] == record["body"]:
                    raise ValueError("相同正文已回读确认，不重复发送")
            record["operation_id"] = quality.digest([ctx["issue_key"], ctx["run_id"], key, record["digest"]])
            record["status"] = "intent"
    elif action in ("receipt", "readback"):
        if p["operation_id"] != record.get("operation_id"):
            raise ValueError("外部操作编号不匹配")
        if record["status"] not in ("intent", "unknown", "created"):
            raise ValueError("操作未准备发送或已经回读完成")
        if action == "receipt":
            if record["status"] == "created":
                raise ValueError("已有评论回执，下一步只能回读确认")
            if p["result"] == "created":
                if not p.get("comment_id"):
                    raise ValueError("成功回执必须有远端评论 ID")
                record["comment_id"] = p["comment_id"]
            elif p.get("comment_id"):
                raise ValueError("不明回执不得声称已知评论 ID")
            record["status"] = p["result"]
        else:
            if any(p[k] != record[k] for k in ("site", "issue_key", "body")):
                raise ValueError("回读目标或正文不匹配，保持待核对，不得重发")
            if record.get("comment_id") and p["comment_id"] != record["comment_id"]:
                raise ValueError("回读评论 ID 与回执不匹配")
            record.update({"status": "verified", "comment_id": p["comment_id"], "source_ref": p["source_ref"]})
    else:
        raise ValueError("未知质量操作")


def check_unresolved_runs(base, task):
    """reset 不抹去旧 run 的不明外部写入，避免恢复后再发一次。"""
    for path in task_store.task_directory(base, task["issue_key"]).glob("quality-*.json"):
        if path == quality.state_path(base, task):
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        quality.quality_contract.validate(state, "quality-state.schema.json")
        old = quality.replay(state)
        if any(r["status"] in ("intent", "unknown", "created") for r in old["publications"].values()):
            raise ValueError("旧 run 仍有外部写入结果待核对；保留旧记录并人工核对 Jira，不能重新发送")
