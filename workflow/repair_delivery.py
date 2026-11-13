"""逐线交付的版本及回读检查。Agent 执行原生 Jira 和 GitHub 操作。"""

from workflow import engineering_baseline, issue_versions


def enabled(task):
    return task.get("facts", {}).get(issue_versions.FACT, {}).get("sequential_delivery") is True


def previous_line(task):
    return (task.get("facts", {}).get("handoff_resume") or {}).get("repair_line")


def ids(values):
    if (not isinstance(values, list) or any(not isinstance(value, str) or not value.isascii()
            or not value.isdecimal() for value in values) or len(values) != len(set(values))):
        raise ValueError("Jira 版本必须使用已回读且不重复的版本 ID")
    return set(values)


def field_ids(issue, task, field):
    if not isinstance(issue, dict) or issue.get("key") != task["issue_key"]:
        raise ValueError("版本回读必须绑定当前 Jira 任务")
    fields = issue.get("fields")
    rows = fields.get(field) if isinstance(fields, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("缺少 Jira %s 的完整版本字段" % field)
    return ids([row.get("id") for row in rows])


def snapshot(task, payload):
    if not enabled(task):
        raise ValueError("当前 run 未启用逐线修复交付")
    plan = task["facts"][issue_versions.FACT]
    return {
        "branch": plan["primary_branch"],
        "plan_digest": engineering_baseline.digest(plan),
        "source_ref": engineering_baseline.text(payload.get("source_ref"), "Jira 回读来源"),
        "summary_ref": engineering_baseline.text(payload.get("summary_ref"), "Jira 评论回读来源"),
    }


def impact_receipt(task, payload):
    result = snapshot(task, payload)
    expected = ids(payload.get("affected_version_ids"))
    if not expected:
        engineering_baseline.text(payload.get("no_affected_version_reason"), "无已证实 Jira 影响版本的原因")
    actual = field_ids(payload.get("issue"), task, "versions")
    if not expected <= actual:
        raise ValueError("Jira versions 未回读本轮已证实的全部版本 ID")
    return dict(result, affected_version_ids=sorted(expected))


def impact_problems(base, task, issue=None):
    if not enabled(task):
        return []
    from workflow import external_sync
    record = external_sync.load(base, task).get("repair_impact", {})
    if record.get("plan_digest") != engineering_baseline.digest(task["facts"][issue_versions.FACT]):
        return ["开 PR 前完成有边界的影响分析、Jira versions 和评论回读，再执行 impact-readback"]
    if issue is not None:
        try:
            if not ids(record["affected_version_ids"]) <= field_ids(issue, task, "versions"):
                return ["Jira versions 已变化，重新核对本轮影响版本"]
        except ValueError as error:
            return [str(error)]
    return []


def merge_proof(base, task):
    from workflow import station
    result = station.evaluate_completion(base, task)
    if not result["ready"] or not result["proof"]["deliveries"]:
        raise ValueError("本轮修复线尚未验收并合并：" + "；".join(result["problems"] or ["缺少合并 PR"]))
    return result["proof"]


def line_receipt(base, task, payload):
    result = snapshot(task, payload)
    issue = payload.get("issue")
    if not isinstance(issue, dict):
        raise ValueError("逐线交付需要当前 Jira 快照")
    problems = impact_problems(base, task, issue)
    if problems:
        raise ValueError("；".join(problems))
    from workflow import jira_status
    if any(set(row["fields"]) & {"versions", "fixVersions"} for row in jira_status.pending_field_writes(base, task)):
        raise ValueError("先回读并收尾原版本字段操作，再结算当前修复线")
    proof = merge_proof(base, task)
    fixes = ids(payload.get("fix_version_ids"))
    preferred = result["branch"] == issue_versions.rules(base, task)["preferred_branch"]
    if not preferred and not fixes:
        raise ValueError("版本分支需要记录实际交付的 Jira 修复版本 ID")
    if preferred and fixes:
        raise ValueError("开发线的计划版本记录在评论，待对应版本分支合并后登记修复版本")
    if fixes and not fixes <= field_ids(issue, task, "fixVersions"):
        raise ValueError("对应版本分支已合并，但 Jira fixVersions 尚未回读全部目标版本")
    if "next_branch" not in payload:
        raise ValueError("需明确下一条待修分支，或已核对整单可收尾（null）")
    next_branch = payload["next_branch"]
    if next_branch is not None:
        engineering_baseline.ref_name(next_branch)
        if next_branch == result["branch"]:
            raise ValueError("同一修复线继续原 run，不换线")
    return dict(result, run_id=task["run_id"], next_branch=next_branch,
                fix_version_ids=sorted(fixes), proof_digest=engineering_baseline.digest(proof))


def close_problems(base, task, proof, issue=None):
    if not enabled(task):
        return []
    from workflow import external_sync
    record = external_sync.load(base, task).get("repair_line", {})
    if (record.get("proof_digest") != engineering_baseline.digest(proof)
            or record.get("plan_digest") != engineering_baseline.digest(task["facts"][issue_versions.FACT])):
        return ["本轮合并后先回读 Jira 版本和交付评论，再执行 line-readback"]
    if record["next_branch"] is not None:
        return [next_line_guidance(record["next_branch"])]
    if issue is not None:
        try:
            if record["fix_version_ids"] and not ids(record["fix_version_ids"]) <= field_ids(issue, task, "fixVersions"):
                return ["Jira fixVersions 已变化，重新核对合并版本"]
        except ValueError as error:
            return [str(error)]
        return impact_problems(base, task, issue)
    return []


def next_line_guidance(branch):
    return "本轮已合并；归档并清理本轮，再接管下一条修复线 " + branch


def archive_line(base, task):
    if not enabled(task):
        return None, None
    from workflow import external_sync
    record = external_sync.load(base, task).get("repair_line")
    if not record:
        return None, None
    proof = task.get("terminal_proof") if task["stage"] == "completed" else merge_proof(base, task)
    if (record.get("proof_digest") != engineering_baseline.digest(proof)
            or record.get("plan_digest") != engineering_baseline.digest(task["facts"][issue_versions.FACT])):
        raise ValueError("修复线交付证据已变化，先重新核对本轮合并")
    return record, proof
