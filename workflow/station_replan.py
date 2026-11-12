"""同一 run 的方案修订事务；保留源码与交付历史，不签发新授权。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from workflow import engineering_baseline as baseline, git_refs, project_rules, quality, repository_delivery
from workflow import station_operation as operations, station_source as source, task_store

STAGES = ("design_review", "implementation", "pr_review", "ci_validation")


def raw(task):
    return {key: copy.deepcopy(value) for key, value in task.items() if key not in ("_revision", "repositories")}


def sources(base, value):
    rows = source.inspect(base, value)
    for name, row in rows.items():
        row["fingerprint"] = quality.git_revision(source.repository_path(base, name))
    return rows


def remote_head(path, origin, branch):
    head = baseline.ref_name(branch)
    output = source.git(path, "ls-remote", "--refs", origin, "refs/heads/" + head).stdout
    return git_refs.parse_head_response(output, (head,)).get(head)


def project(base):
    product, project_id = project_rules.station_context(base)
    profile = project_rules.load_profile(root=product, project=project_id)
    root = project_rules.project_root(product, project_id)
    return profile, root, profile["repositories"]["repositories"]


def prepare(base, issue, run_id, request):
    """只读准备，调用方原生保存输出并展示给研发确认。"""
    task = task_store.check_expected_run(base, issue, run_id)
    task_store.require_development(base, task)
    if task.get("stage") not in STAGES or task.get("outcome") != "in_progress":
        raise ValueError("只有进行中的设计、实现、PR 审查或 CI 阶段可以返工")
    required = {"reason", "facts", "repositories", "additions", "impact"}
    if not isinstance(request, dict) or not required <= set(request) <= required | {"follow_up"}:
        raise ValueError("返工输入需要 reason/facts/repositories/additions/impact")
    baseline.text(request["reason"], "reason")
    rules = quality.config(base, task)
    permitted = set(rules.get("plan_fact_keys", [])) or {"fix_plan"}
    if not isinstance(request["facts"], dict) or not set(request["facts"]) <= permitted:
        raise ValueError("返工只能更新项目声明的方案事实，不能修改运行身份或其它受管事实")
    if not all(isinstance(request[key], dict) for key in ("repositories", "additions", "impact")):
        raise ValueError("返工范围、增仓和影响分析必须为对象")
    follow_up = request.get("follow_up", {})
    if (not isinstance(follow_up, dict) or not set(follow_up) <= set(task["task_repositories"])
            or any(mode not in ("reuse", "new_branch") for mode in follow_up.values())):
        raise ValueError("补修必须引用已登记仓库并声明 reuse 或 new_branch")
    if not request["facts"] and not request["repositories"] and not request["additions"] and not follow_up:
        raise ValueError("返工请求没有任何变更")
    if not set(request["repositories"]) <= set(task["task_repositories"]):
        raise ValueError("范围修订必须引用已登记仓库，不允许删除登记规避交付")
    _, root, catalog = project(base)
    profile = json.loads((root / "engineering-profiles.json").read_text())["profiles"][task["engineering_baseline"]["profile"]["id"]]
    if baseline.digest(profile) != task["engineering_baseline"]["profile"]["digest"]:
        raise ValueError("工程 Profile 已变化，不能在线猜测旧任务规则")
    additions = request["additions"]
    if not set(additions) <= set(profile.get("optional_repositories", [])) or set(additions) & set(task["engineering_baseline"]["repositories"]):
        raise ValueError("只能增加 Profile 明确允许且尚未冻结的可选仓")
    all_names = set(task["task_repositories"]) | set(additions)
    impact = request["impact"]
    impact_fields = {"repositories", "items", "rationale"}
    if not impact_fields <= set(impact) <= impact_fields | {"cross_repository_checks"}:
        raise ValueError("impact 需要 repositories/items/rationale")
    baseline.text(impact["rationale"], "impact.rationale")
    for key in ("repositories", "items"):
        if not isinstance(impact[key], list) or any(not isinstance(item, str) for item in impact[key]) or len(impact[key]) != len(set(impact[key])):
            raise ValueError("impact.%s 必须为不重复的引用数组" % key)
    if not set(impact["repositories"]) <= all_names or not set(request["repositories"]) | set(additions) | set(follow_up) <= set(impact["repositories"]):
        raise ValueError("影响分析必须覆盖变更仓库且不能引用未知仓库")
    model = quality.replay(quality.load(base, task))
    if not set(impact["items"]) <= set(model["items"]):
        raise ValueError("影响分析引用了未知检查项")
    cross_checks = impact.get("cross_repository_checks", {})
    if not isinstance(cross_checks, dict) or not set(cross_checks) <= set(impact["items"]):
        raise ValueError("跨仓验证须引用本次受影响的已登记检查项")
    for key, names in cross_checks.items():
        plan = model["items"][key]["plan"]
        if (not isinstance(names, list) or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names) or len(names) < 2
                or plan["repository"] not in names or not set(names) <= set(impact["repositories"])
                or plan["timing"] != "after_fix"
                or not rules.get("methods", {}).get(plan["method"], {}).get("origins")):
            raise ValueError("跨仓验收须列出受影响仓库，并采用可回读实际执行的修复后检查项：" + key)
    expected_items = {key for key, item in model["items"].items() if item["plan"]["repository"] in impact["repositories"]}
    if not expected_items <= set(impact["items"]):
        raise ValueError("影响分析遗漏受影响仓库的检查项：" + "、".join(sorted(expected_items - set(impact["items"]))))
    if request["facts"] and not impact["repositories"]:
        raise ValueError("方案变化必须明确影响仓库，不能用空映射放行")
    before = sources(base, task["engineering_baseline"])
    remote = {}
    for name, binding in task["task_repositories"].items():
        if before[name]["branch"] != binding["work_branch"]:
            raise ValueError("现有仓库不在登记分支：" + name)
        remote[name] = remote_head(source.repository_path(base, name), "origin", binding["work_branch"])
    entries = {}
    generated = task_store.generated_work_branch(base, task)
    for name, row in list(request["repositories"].items()) + list(additions.items()):
        expected = {"scope", "verification"} | ({"ref_name", "commit_sha"} if name in additions else set())
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError("仓库修订字段无效：" + name)
        if name in additions:
            branch = baseline.ref_name(row["ref_name"])
            if not isinstance(row["commit_sha"], str) or not baseline.SHA.fullmatch(row["commit_sha"]):
                raise ValueError("增仓必须声明完整 SHA")
            if name not in catalog or remote_head(base, catalog[name]["origin"], branch) != row["commit_sha"]:
                raise ValueError("新增仓目标引用与预期 SHA 不一致：" + name)
            if remote_head(base, catalog[name]["origin"], generated):
                raise ValueError("新增仓的远端同名工作分支已存在")
            path = source.repository_path(base, name)
            if path.exists():
                source.identity(path, catalog[name]["origin"])
                source.require_clean(path)
                if source.git(path, "show-ref", "--verify", "--quiet", "refs/heads/" + generated, check=False).returncode == 0:
                    raise ValueError("新增仓的本地同名工作分支已存在")
            value = baseline.freeze({"id": profile["id"], "revision": profile["revision"], "repositories": [name]}, catalog,
                {name: {"verification": "verified", "ref_kind": "branch", "ref_name": branch,
                        "commit_sha": row["commit_sha"], "resolution_source": "explicit_replan", "rule_version": "replan-v1"}},
                {"replan_run": run_id})
            entries[name] = value["repositories"][name]
            baseline.task_repository(value, name, generated, branch, row["scope"], row["verification"])
        else:
            old = task["task_repositories"][name]
            baseline.task_repository(task["engineering_baseline"], name, old["work_branch"], old["target_branch"], row["scope"], row["verification"])
    follow_ups = {}
    for name, mode in follow_up.items():
        binding = task["task_repositories"][name]
        observed = before[name]
        delivery = repository_delivery.current(binding, observed["head"])
        if not delivery or remote[name] not in (None, observed["head"]):
            raise ValueError("补修前须核对已合并候选及远端未交付成果：" + name)
        next_branch = None
        if mode == "new_branch":
            if observed["dirty"]:
                raise ValueError("更换工作分支前须保全未交付修改：" + name)
            # abort 保留已有分支；新的准备 revision 使用另一名称。
            next_branch = "%s-follow-up-%s-%s" % (generated, delivery["pr"], task["_revision"])
            path = source.repository_path(base, name)
            if (source.git(path, "show-ref", "--verify", "--quiet", "refs/heads/" + next_branch,
                           check=False).returncode == 0 or remote_head(path, "origin", next_branch)):
                raise ValueError("补修工作分支已存在，不能自动采用：" + name)
        follow_ups[name] = {"pr": delivery["pr"], "branch": next_branch, "head": observed["head"]}
    if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(request, ensure_ascii=False)):
        raise ValueError("返工输入包含敏感内容")
    result = {"schema_version": 1, "issue_key": issue, "run_id": run_id, "revision": task["_revision"],
              "task_digest": baseline.digest(raw(task)), "request": copy.deepcopy(request), "sources": before,
              "remote_work": remote, "added_entries": entries, "catalog_digest": baseline.digest(catalog)}
    if follow_ups:
        result["follow_ups"] = follow_ups
    result["digest"] = baseline.digest(result)
    return result


def original_sources(base, task, prepared, operation):
    """恢复时只接受本操作登记的工作分支变化。"""
    current = sources(base, task["engineering_baseline"])
    for name in prepared.get("follow_ups", {}):
        step = operation["steps"].get("follow-up-branch:" + name)
        if step and current[name] == dict(prepared["sources"][name], branch=step["expected"]["branch"]):
            current[name] = prepared["sources"][name]
    return current


def prepare_follow_up_branch(base, name, original, target, operation):
    path = source.repository_path(base, name)
    branch, head = target["branch"], target["head"]
    ref = "refs/heads/" + branch
    step_name = "follow-up-branch:" + name
    exists = source.git(path, "rev-parse", "--verify", ref, check=False)
    if remote_head(path, "origin", branch):
        raise ValueError("补修远端分支已存在：" + name)
    if not exists.returncode and (step_name not in operation["steps"] or exists.stdout.strip() != head):
        raise ValueError("补修本地分支已存在或 Head 已变化：" + name)
    source.require_clean(path)
    operations.intent(base, operation, step_name, original, {"branch": branch, "head": head})
    if exists.returncode:
        source.git(path, "branch", branch, head)
    source.git(path, "checkout", branch)
    if (source.git(path, "branch", "--show-current").stdout.strip() != branch
            or source.git(path, "rev-parse", "HEAD").stdout.strip() != head):
        raise ValueError("补修工作分支回读不一致：" + name)
    operations.receipt(base, operation, step_name, {"branch": branch, "head": head})


def apply(base, issue, run_id, revision, operation_id, prepared, decision_ref):
    baseline.text(decision_ref, "decision_ref")
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        if not isinstance(prepared, dict) or prepared.get("digest") != baseline.digest({k: v for k, v in prepared.items() if k != "digest"}):
            raise ValueError("返工准备摘要无效")
        if prepared.get("run_id") != run_id or prepared.get("issue_key") != issue or prepared.get("revision") != revision:
            raise ValueError("返工准备不属于当前任务、run 或请求 revision")
        request = {"prepared": prepared, "decision_ref": decision_ref}
        if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(request, ensure_ascii=False)):
            raise ValueError("返工确认包含敏感内容")
        prior = operations.read(base)
        recovering = prior and prior["operation_id"] == operation_id
        if not recovering and prepare(base, issue, run_id, prepared["request"]) != prepared:
            raise ValueError("返工准备后事实已变化，请一次重新核对全部差异")
        operation = operations.begin(base, "replan", operation_id, revision, request, run_id)
        if operation["status"] == "done":
            return operation
        if operation.get("aborting"):
            raise ValueError("本操作已进入 abort，只能恢复 abort")
        if task.get("archive_ref") or task.get("outcome") != "in_progress":
            raise ValueError("不能返工已归档或结束的任务")
        step = operation["steps"].get("current")
        if step and raw(task) == step["expected"]:
            operations.receipt(base, operation, "current", {"revision": task["_revision"]})
            operations.finish(base, operation)
            return operation
        if baseline.digest(raw(task)) != prepared["task_digest"] or original_sources(base, task, prepared, operation) != prepared["sources"]:
            raise ValueError("返工恢复前原任务或源码已变化，保留现场")
        _, _, catalog = project(base)
        if baseline.digest(catalog) != prepared["catalog_digest"]:
            raise ValueError("项目仓库配置已变化")
        from workflow.authorization import revoke_authorization
        operations.intent(base, operation, "revoke", {}, {"reason": "task_replan"})
        revoke_authorization(base, issue, "task_replan")
        operations.receipt(base, operation, "revoke", {"revoked": True})
        additions = prepared["added_entries"]
        value = (baseline.append_repositories(task["engineering_baseline"], additions, decision_ref)
                 if additions else task["engineering_baseline"])
        if additions:
            source.prepare_repositories(base, catalog, sorted(additions), operation)
            for name, entry in additions.items():
                current_ref = source.git(source.repository_path(base, name), "rev-parse", "refs/remotes/origin/" + entry["ref_name"]).stdout.strip()
                if current_ref != entry["commit_sha"]:
                    raise ValueError("下载期间新增仓目标引用变化，请保留现场重新核对")
            # 仅检出新增仓，不触碰原仓 dirty 工作区。
            pending_checkout = {name: entry for name, entry in additions.items()
                                if "replan-branch:" + name not in operation["steps"]}
            if pending_checkout:
                subset = baseline.freeze({"id": "replan-additions", "revision": 1, "repositories": sorted(pending_checkout)}, catalog,
                    {name: dict(entry, verification="verified") for name, entry in pending_checkout.items()}, {"run_id": run_id})
                source.checkout_baseline(base, subset, operation)
        updated = raw(task)
        updated["engineering_baseline"] = value
        generated = task_store.generated_work_branch(base, task)
        for name, entry in additions.items():
            path = source.repository_path(base, name)
            branch_step = "replan-branch:" + name
            record = operation["steps"].get(branch_step)
            if record is None:
                if source.git(path, "show-ref", "--verify", "--quiet", "refs/heads/" + generated, check=False).returncode == 0:
                    raise ValueError("新增仓本地工作分支已存在，不能自动复用")
                if remote_head(path, "origin", generated):
                    raise ValueError("新增仓远端工作分支已存在")
                record = operations.intent(base, operation, branch_step, {}, {"head": entry["commit_sha"], "branch": generated})
            source.require_clean(path)
            found = source.git(path, "rev-parse", "--verify", "refs/heads/" + generated, check=False)
            if found.returncode:
                source.git(path, "branch", generated, entry["commit_sha"])
            elif found.stdout.strip() != entry["commit_sha"]:
                raise ValueError("恢复工作分支 Head 已变化")
            source.git(path, "checkout", generated)
            if source.git(path, "rev-parse", "HEAD").stdout.strip() != entry["commit_sha"]:
                raise ValueError("新增工作分支回读不一致")
            operations.receipt(base, operation, branch_step, record["expected"])
            row = prepared["request"]["additions"][name]
            updated["task_repositories"][name] = baseline.task_repository(value, name, generated, entry["ref_name"], row["scope"], row["verification"])
            updated.get("retained_repositories", {}).pop(name, None)
        for name, row in prepared["request"]["repositories"].items():
            updated["task_repositories"][name].update(approved_scope=row["scope"], verification_method=row["verification"])
        for name, target in prepared.get("follow_ups", {}).items():
            binding = updated["task_repositories"][name]
            if target["branch"]:
                prepare_follow_up_branch(base, name, prepared["sources"][name], target, operation)
                binding["work_branch"] = target["branch"]
            observation = binding.get("observation") or {}
            observation.update(results={"pull_request": None, "ci": None}, follow_up_after_pr=target["pr"])
            binding["observation"] = observation
        updated["facts"].update(prepared["request"]["facts"])
        updated.update(stage="design_review", pending=None)
        previous_impacts = task.get("replan", {}).get("item_revisions", {})
        impacts = dict(previous_impacts, **{key: prepared["digest"] for key in prepared["request"]["impact"]["items"]})
        updated["replan"] = {"operation_id": operation_id, "decision_ref": decision_ref, "digest": prepared["digest"],
                              "sources": prepared["sources"], "remote_work": {
                                  name: None if prepared.get("follow_ups", {}).get(name, {}).get("branch") else head
                                  for name, head in prepared["remote_work"].items()
                              }, "item_revisions": impacts}
        dependencies = dict(task.get("replan", {}).get("item_dependencies", {}))
        dependencies.update(prepared["request"]["impact"].get("cross_repository_checks", {}))
        if dependencies:
            updated["replan"]["item_dependencies"] = dependencies
        updated["history"].append({"event": "replan", "operation_id": operation_id, "decision_ref": decision_ref,
                                   "before_plan": copy.deepcopy(task["facts"]), "request": prepared["request"],
                                   "before_baseline": task["engineering_baseline"]["digest"], "after_baseline": value["digest"]})
        if original_sources(base, task, prepared, operation) != prepared["sources"]:
            raise ValueError("增仓期间原源码变化，保留原任务并等待核对")
        for name, binding in task["task_repositories"].items():
            if remote_head(source.repository_path(base, name), "origin", binding["work_branch"]) != prepared["remote_work"][name]:
                raise ValueError("返工期间原仓远端工作分支变化，保留现场")
        operations.intent(base, operation, "current", raw(task), updated)
        updated["_revision"] = task["_revision"]
        task_store.write_task(base, updated)
        readback = task_store.read_task(base, issue)
        if raw(readback) != operation["steps"]["current"]["expected"]:
            raise ValueError("返工 current 回读不一致")
        operations.receipt(base, operation, "current", {"revision": readback["_revision"]})
        operations.finish(base, operation)
        return operation


def abort(base, issue, run_id, operation_id, decision_ref):
    """只终止修订意图，保留源码，旧授权不复活；部分仓库归属留给清理预检。"""
    baseline.text(decision_ref, "decision_ref")
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        operation = operations.read(base)
        if not operation or operation["kind"] != "replan" or operation["operation_id"] != operation_id or operation["run_id"] != run_id:
            raise ValueError("只能放弃当前 run 的原返工操作")
        if operation["status"] == "done":
            if operation.get("outcome") == "aborted":
                if operation.get("abort_decision_ref") != decision_ref:
                    raise ValueError("同一 abort 不能更换确认来源")
                return operation
            raise ValueError("返工已完成；请重新 prepare，不能回滚已生效方案")
        if task.get("archive_ref") or task.get("outcome") != "in_progress":
            raise ValueError("不能放弃已结束任务的返工操作")
        if operation.get("aborting") and operation.get("abort_decision_ref") != decision_ref:
            raise ValueError("必须恢复原 abort 确认")
        step = operation["steps"].get("current")
        if step and raw(task) == step["expected"]:
            raise ValueError("新方案已写入，须恢复 apply 完成回执，不得倒退")
        from workflow.authorization import revoke_authorization
        revoke_authorization(base, issue, "task_replan")
        operation["aborting"] = True
        operation["abort_decision_ref"] = decision_ref
        operations.save(base, operation)
        preserved = copy.deepcopy(task.get("replan_preserved", {}))
        retained = copy.deepcopy(task.get("retained_repositories", {}))
        for name, entry in operation["request"]["prepared"]["added_entries"].items():
            path = source.repository_path(base, name)
            if path.exists():
                source.identity(path, entry["origin"])
                head = source.git(path, "rev-parse", "HEAD").stdout.strip()
                branch = source.git(path, "branch", "--show-current").stdout.strip()
                preserved[name] = {"fingerprint": quality.git_revision(path), "origin": entry["origin"]}
                retained[name] = {"head": head, "branch": branch}
        prepared = operation["request"]["prepared"]
        for name in prepared.get("follow_ups", {}):
            branch_step = operation["steps"].get("follow-up-branch:" + name)
            if branch_step is None:
                continue
            path = source.repository_path(base, name)
            row = source.inspect(base, task["engineering_baseline"])[name]
            before, after = branch_step["before"], branch_step["expected"]
            if row["branch"] == after["branch"]:
                if row["dirty"] or row["head"] != after["head"]:
                    raise ValueError("补修分支出现新成果，保留现场并核对：" + name)
                if source.git(path, "rev-parse", "refs/heads/" + before["branch"]).stdout.strip() != before["head"]:
                    raise ValueError("原工作分支已变化：" + name)
                source.git(path, "checkout", before["branch"])
            elif row["branch"] != before["branch"] or row["head"] != before["head"]:
                raise ValueError("补修分支现场已变化：" + name)
        event = {"event": "replan_aborted", "operation_id": operation_id, "decision_ref": decision_ref}
        if not any(h.get("event") == "replan_aborted" and h.get("operation_id") == operation_id for h in task["history"]):
            task.update(replan_preserved=preserved, retained_repositories=retained, stage="design_review", pending=None)
            task["history"].append(event)
            task.pop("repositories", None)
            task_store.write_task(base, task)
        else:
            if task.get("replan_preserved") != preserved or task.get("retained_repositories") != retained:
                raise ValueError("abort 写入后保留源码发生变化，不能冒充同一回执")
        for step in operation["steps"].values():
            if step["receipt"] is None:
                step["receipt"] = {"aborted": True, "disposition": "preserved_without_execution_confirmation"}
        operation["outcome"] = "aborted"
        operation["abort_decision_ref"] = decision_ref
        operations.finish(base, operation)
        return operation
