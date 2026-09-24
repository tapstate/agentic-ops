"""清理阶段的成果出口；复用原 operation/档案回执，不执行远端客户端。"""
import json
import re
from workflow import station_operation as operations, engineering_baseline as baseline
from workflow import station_source as source, station_resources as resources, archive_store

STAGES = ("admission", "archive", "source", "disposition", "resources", "release")
LABELS = dict(zip(STAGES, ("准入与范围", "停写与归档", "源码复位", "分支与 PR", "登记资源", "最终释放")))


def key(operation, stage):
    return "cleanup-stage:%s:%s:%s" % (len(operation.get("plan_revisions", [])), operation["cleanup_plan"]["digest"], stage)


def run(base, task, operation, stage, execute, verify):
    operation["phase"] = "cleanup_" + stage
    operations.save(base, operation)
    step = key(operation, stage)
    previous = operation["steps"].get(step)
    if previous and previous.get("receipt") is not None:
        if verify() is False:
            raise ValueError("阶段验收未通过：" + stage)  # 不沿旧授权重删新内容。
        return
    operations.intent(base, operation, step, {}, {"stage": stage, "plan_digest": operation["cleanup_plan"]["digest"]})
    execute()
    if verify() is False:
        raise ValueError("阶段验收未通过：" + stage)
    operations.receipt(base, operation, step, {"verification": "observed_result"})


def decisions(operation):
    return [e for e in operation["cleanup_plan"]["external"]
            if e.get("resource_type") in ("git-branch", "pull-request") and e["action"] != "retain"]


def local(entry):
    return entry.get("resource_type") == "git-branch" and entry.get("before", {}).get("location") == "local"


def local_identity(base, task, operation, entry):
    before = entry["before"]
    name, ref = before["repository"], before["ref"]
    state = operation["cleanup_plan"]["source"].get(name, {})
    binding = task.get("task_repositories", {}).get(name, {})
    if (before.get("protected") is not False or not entry.get("decision_ref")
            or not binding.get("work_branch") or ref != "refs/heads/" + binding["work_branch"]
            or binding["work_branch"] in (binding.get("target_branch"), "main", "master", "develop")
            or not state or state.get("refs", {}).get(ref) not in (None, before["sha"])):
        raise ValueError("本地分支不属于已确认任务分支：" + entry["id"])
    return source.repository_path(base, name), ref


def read_ref(repository, ref):
    observed = dict(line.split(" ", 1) for line in source.git(repository, "for-each-ref", "--format=%(refname) %(objectname)", ref).stdout.splitlines())
    return observed.get(ref)


def dispose_local(base, task, operation):
    for entry in decisions(operation):
        if not local(entry):
            continue  # 原生平台处理远端，阶段验收读取精确回执。
        repository, ref = local_identity(base, task, operation, entry)
        before = entry["before"]
        step = "external:%s:%s:%s" % (len(operation.get("plan_revisions", [])), operation["cleanup_plan"]["digest"], baseline.digest(entry["id"]))
        observed = read_ref(repository, ref)
        if observed is not None:
            if ref not in operation["cleanup_plan"]["source"][before["repository"]]["refs"]:
                raise ValueError("确认时不存在的分支重新出现，需补充确认：" + ref)
            if operation["steps"].get(step, {}).get("receipt") is not None:
                raise ValueError("已删除分支重新出现：" + ref)
            if observed != before["sha"]:
                raise ValueError("任务分支 SHA 已变化：" + ref)
            worktrees = source.git(repository, "worktree", "list", "--porcelain").stdout.splitlines()
            if "branch " + ref in worktrees:
                raise ValueError("分支仍被工作树检出：" + ref)
            operations.intent(base, operation, step, {}, entry)
            source.git(repository, "update-ref", "-d", ref, before["sha"])
        operations.intent(base, operation, step, {}, entry)
        if read_ref(repository, ref) is not None:
            raise ValueError("本地分支删除尚未回读：" + ref)
        operations.receipt(base, operation, step, {"status": "deleted", "sha": before["sha"]})


def disposition_readback(base, task, entry):
    directory = archive_store.receipts(base, task["archive_ref"], create=False)
    if not directory.exists():
        return False
    for path in directory.glob("disposition-*-readback.json"):
        if path.is_symlink():
            raise ValueError("处置回执不能为链接")
        result = json.loads(path.read_text())
        if result.get("object_id") != entry["id"]:
            continue
        identifier = result.get("disposition_id", "")
        if not re.fullmatch(r"disposition-[a-z0-9-]{8,80}", identifier) or path.name != identifier + "-readback.json" or result.get("phase") != "readback":
            raise ValueError("处置回执身份无效")
        intent_path = directory / (identifier + "-intent.json")
        if intent_path.is_symlink():
            raise ValueError("处置意图不能为链接")
        intent = json.loads(intent_path.read_text())
        expected_status = "deleted" if entry["action"] == "delete" else "closed"
        if (intent.get("phase") == "intent" and intent.get("object_id") == entry["id"]
                and intent.get("before") == entry["before"] and intent.get("action") == entry["action"]
                and intent.get("resource_type") == entry["resource_type"]
                and result.get("intent_digest") == baseline.digest(intent)
                and intent.get("confirmed_digest") == baseline.digest({k:v for k,v in intent.items() if k != "confirmed_digest"})
                and result.get("status") == expected_status and result.get("readback_ref")):
            return True
    return False


def verify_dispositions(base, task, operation):
    problems = []
    for entry in decisions(operation):
        try:
            if local(entry):
                repository, ref = local_identity(base, task, operation, entry)
                if read_ref(repository, ref) is not None:
                    raise ValueError("本地分支仍存在或回读失败")
            elif not disposition_readback(base, task, entry):
                raise ValueError("请用原生工具处置并通过 station_disposition 保存精确回读；已有意图先回读")
        except (ValueError, OSError) as error:
            problems.append(entry["id"] + ": " + str(error))
    if problems:
        raise ValueError("分支/PR 阶段未完成：\n" + "\n".join(problems))


def expected_refs(base, task, operation, name, expected, current):
    # 只允许已确认的本地任务分支缺失，不放宽其它引用检查。
    for entry in decisions(operation):
        if local(entry) and entry["before"]["repository"] == name:
            _, ref = local_identity(base, task, operation, entry)
            if ref not in current and (operation.get("steps", {}).get(key(operation, "disposition"))
                    or operation.get("phase") in ("cleanup_disposition", "cleanup_resources", "cleanup_release", "neutral")):
                expected.pop(ref, None)
    return expected


def describe(operation):
    plan = operation.get("cleanup_plan")
    return [{"stage": stage, "label": LABELS[stage],
             "verified": bool(plan and operation.get("steps", {}).get(key(operation, stage), {}).get("receipt")),
             "active": operation.get("phase") == "cleanup_" + stage} for stage in STAGES]
