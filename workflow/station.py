"""单任务工位四操作；所有副作用在同一工位锁和持久化操作下运行。"""
from __future__ import annotations

import importlib.util
import hashlib
import copy
import re
from datetime import datetime
import json
from pathlib import Path

from workflow import archive_store, engineering_baseline as baseline, project_rules, station_archive as archives
from workflow import station_operation as operations, station_source as source, task_store


def _resources():
    from workflow import station_resources
    return station_resources


def _project(base):
    root, project = project_rules.station_context(base)
    return project_rules.project_root(root, project)


def _plan_receipt(base, task, operation, phase, snapshot=None):
    archives.verify(base, task["archive_ref"], task)
    directory = archive_store.receipts(base, task["archive_ref"], create=True)
    plan = snapshot["plan"] if snapshot else operation["cleanup_plan"]
    generation = snapshot["plan_revision"] if snapshot else len(operation.get("plan_revisions", []))
    path = directory / (operation["operation_id"] + "-" + phase + "-" + str(generation) + "-" + plan["digest"] + ".json")
    value = {"operation_id": operation["operation_id"], "operation_kind": operation["kind"],
             "purpose": "archive_refresh" if operation["kind"] == "archive" else "cleanup",
             "run_id": task["run_id"], "phase": phase,
             "archive_digest": task["archive_ref"]["digest"], "cleanup_plan_digest": plan["digest"], "plan_revision": generation,
             "confirmation_digest": snapshot["confirmation_digest"] if snapshot else operation.get("confirmation_digest", operation["request"].get("confirmed_digest")),
             "inventory": [{"id": baseline.digest(entry), "action": entry.get("action", "external")} for entry in plan.get("entries", []) + plan.get("external", [])]}
    if phase == "resources-released":
        value["observed_result"] = operation["cleanup_manifest"]["result"]
    if path.is_symlink():
        raise ValueError("档案回执不能是符号链接")
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != value:
            raise ValueError("已有计划回执内容不一致")
    else:
        task_store._write_json_atomic(path, value)


def _flush_amendment_receipts(base, task, operation):
    if not task.get("archive_ref"):
        return
    for snapshot in operation.get("amendment_receipts", []):
        _plan_receipt(base, task, operation, "amendment", snapshot)


def _clear_active(base, operation):
    root = task_store.state_path(base)
    def inventory():
        values = {}
        for name in ("authorization.json", "evidence"):
            target = root / name
            paths = [target] + list(target.rglob("*")) if target.is_dir() else [target]
            for path in paths:
                if path.is_symlink():
                    raise ValueError("活动材料中含符号链接")
                if path.is_file():
                    values[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
                elif path.exists() and not path.is_dir():
                    raise ValueError("活动材料含非普通对象")
        return values
    actual = inventory()
    plan = operation["cleanup_plan"]
    _resources().verify_active(base, plan, operation)
    step_name = "clear-active:" + str(len(operation.get("plan_revisions", []))) + ":" + operation["cleanup_plan"]["digest"]
    step = operations.intent(base, operation, step_name, {}, {"files": actual}) if step_name not in operation["steps"] else operation["steps"][step_name]
    expected = step["expected"]["files"]
    if any(name not in expected or expected[name] != digest for name, digest in actual.items()):
        raise ValueError("归档后活动材料新增或变化，拒绝删除")
    for name in expected:
        path = root / name
        if path.exists():
            path.unlink()
    evidence = root / "evidence"
    if evidence.exists():
        for directory in sorted((p for p in evidence.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            directory.rmdir()
        evidence.rmdir()
    operations.receipt(base, operation, step_name, {"cleared": True})


def takeover(base, request, operation_id, expected_revision):
    with task_store.task_state_lock(base):
        issue = task_store.validate_issue_key(request["issue_key"])
        project_rules.validate_project_issue(project_rules.load_profile(station=base), issue)
        project_rules.class_spec(project_rules.load_admission(station=base), request["task_class"])
        project = _project(base)
        profiles = json.loads((project / "engineering-profiles.json").read_text())
        profile = profiles["profiles"][request.get("profile", "full-application")]
        catalog = project_rules.load_repository_catalog(station=base)["repositories"]
        selected = baseline.selected_repositories(profile, catalog, request.get("optional_repositories", []))
        previous = operations.read(base)
        if not previous or previous["operation_id"] != operation_id:
            if task_store.read_current(base)["current"] is not None:
                raise ValueError("工位仍有当前任务，不能接管新任务")
            _resources().verify_station_inventory(base)
            for name in selected:
                path = source.repository_path(base, name)
                if path.exists():
                    source.identity(path, catalog[name]["origin"])
                    source.require_clean(path)
            if any((task_store.state_path(base) / name).exists() for name in ("authorization.json", "evidence")):
                raise ValueError("空闲工位仍有活动授权或证据，拒绝接管")
        operation = operations.begin(base, "takeover", operation_id, expected_revision, request)
        if operation["status"] == "done":
            return operation
        task = task_store.read_task(base)
        from workflow import station_directories
        if task is None:
            task = {"issue_key": issue, "run_id": operation["run_id"], "task_class": request["task_class"],
                    "stage": "waiting_takeover", "outcome": "in_progress", "facts": {"station_contract": 3}, "history": [],
                    "pending": None, "engineering_baseline": {"status": "resolving"},
                    "task_repositories": {}, "terminal_proof": None, "archive_ref": None}
            runtime = station_directories.path_at(base, "runtime")
            if any(runtime.iterdir()):
                raise ValueError("接管前 runtime 必须为空")
            task["retained_repositories"] = source.check_station_layout(base, catalog, selected)
            task["initial_runtime"] = {"path": "runtime", "run_id": task["run_id"],
                "station_id": json.loads((task_store.state_path(base) / "station.json").read_text())["station_id"],
                "kind": "runtime-exclusive", "producer": "workflow", "recipe": {"id": "workflow-runtime", "revision": 1},
                "parent": station_directories.identity(runtime.parent), "identity": station_directories.identity(runtime),
                "disposition": "clear_children_keep_root"}
            task_store.write_task(base, task)
        if task["run_id"] != operation["run_id"]:
            raise ValueError("接管恢复 run 不一致")
        if "retained_repositories" not in task:
            task["retained_repositories"] = source.check_station_layout(base, catalog, selected)
            task_store.write_task(base, task)
        from workflow import station_directories
        station_directories.create(base, task, "runtime", "workflow", adopt=True)
        if task["engineering_baseline"]["status"] != "frozen":
            observations = source.prepare_repositories(base, catalog, selected, operation)
            specification = importlib.util.spec_from_file_location("station_project_resolver", project / "scripts/engineering_baseline.py")
            module = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(module)
            config = json.loads((project / "version-branch-alignments.json").read_text())
            value = module.resolve(request["version"], config, catalog, profile, observations,
                                   optional=request.get("optional_repositories", []),
                                   explicit_branches=request.get("explicit_branches", {}))
            continuations = request.get("continuations", {})
            if not isinstance(continuations, dict) or not set(continuations) <= set(selected):
                raise ValueError("续办仓库必须属于完整工程")
            resolutions = {name: dict(entry, verification="verified") for name, entry in value["repositories"].items()}
            for name, continuation in continuations.items():
                if not isinstance(continuation, dict) or set(continuation) != {"work_branch", "baseline_sha", "expected_head"}:
                    raise ValueError("续办必须明确工作分支、历史基线和预期 Head")
                branch = baseline.ref_name(continuation["work_branch"])
                path = source.repository_path(base, name)
                for field in ("baseline_sha", "expected_head"):
                    sha = continuation[field]
                    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(sha)) or source.git(path, "cat-file", "-t", sha).stdout.strip() != "commit":
                        raise ValueError("续办提交必须为已存在的完整 commit SHA")
                found = []
                for ref in ("refs/heads/" + branch, "refs/remotes/origin/" + branch):
                    observed = source.git(path, "rev-parse", "--verify", ref, check=False)
                    if observed.returncode == 0:
                        found.append(observed.stdout.strip())
                if not found or any(head != continuation["expected_head"] for head in found):
                    raise ValueError("续办工作分支与预期 Head 不一致")
                if source.git(path, "merge-base", "--is-ancestor", continuation["baseline_sha"], continuation["expected_head"], check=False).returncode:
                    raise ValueError("续办历史基线不是候选祖先")
                resolutions[name].update(ref_kind="commit", ref_name=continuation["baseline_sha"],
                                         commit_sha=continuation["baseline_sha"], resolution_source="explicit_continuation")
            if continuations:
                inputs = dict(value["resolution_input"], continuations=continuations)
                value = baseline.freeze(profile, catalog, resolutions, inputs, request.get("optional_repositories", []))
            baseline.verify_local_baseline(base, value)
            task["engineering_baseline"] = value
        if "reset_baseline" not in task:
            reset_baseline = {}
            for name in selected:
                reference = catalog[name].get("dev_branch")
                if not reference:
                    raise ValueError("项目仓库未声明开发引用")
                baseline.ref_name(reference)
                sha = source.git(source.repository_path(base, name), "rev-parse", "--verify", "refs/remotes/origin/" + reference + "^{commit}").stdout.strip()
                reset_baseline[name] = {"ref": reference, "sha": sha, "source": "project.dev_branch/origin", "observed_at": task_store.now(), "recipe_revision": profile["revision"]}
            task["reset_baseline"] = reset_baseline
            task_store.write_task(base, task)
        source.checkout_baseline(base, task["engineering_baseline"], operation)
        task["source_prepared"] = True
        task_store.write_task(base, task)
        operations.finish(base, operation)
        return operation


def scope_change(base, issue, run_id, revision, operation_id, name, work_branch, target_branch, scope, verification, expected_head=None):
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        previous = operations.read(base)
        if not previous or previous["operation_id"] != operation_id:
            task_store.require_development(base, task)
            if name in task.get("task_repositories", {}):
                raise ValueError("仓库已经登记；不能覆盖本次修改与交付绑定")
        value = task["engineering_baseline"]
        continuation = value["resolution_input"].get("continuations", {}).get(name)
        if expected_head is None:
            generated = task_store.generated_work_branch(base, task)
            if work_branch is not None and work_branch != generated:
                raise ValueError("新工作分支必须使用工位 git_name 与当前 run 生成")
            work_branch = generated
        elif work_branch is None:
            raise ValueError("续办必须明确既有工作分支")
        item = baseline.task_repository(value, name, work_branch, target_branch, scope, verification)
        path = source.repository_path(base, name)
        source.identity(path, value["repositories"][name]["origin"])
        source.require_clean(path)
        if expected_head is not None and (not continuation or continuation["expected_head"] != expected_head or continuation["work_branch"] != work_branch):
            raise ValueError("续办 Head 必须已在接管时绑定历史基线")
        if not previous or previous["operation_id"] != operation_id:
            for ref in ("refs/heads/" + work_branch, "refs/remotes/origin/" + work_branch):
                result = source.git(path, "rev-parse", "--verify", ref, check=False)
                if result.returncode == 0 and (expected_head is None or result.stdout.strip() != expected_head):
                    raise ValueError("同名分支已存在；需在接管时明确历史基线和预期 Head")
        request = {"repository": name, "binding": item, "expected_head": expected_head}
        operation = operations.begin(base, "scope_change", operation_id, revision, request, run_id)
        if operation["status"] == "done":
            return operation
        path = source.repository_path(base, name)
        source.identity(path, value["repositories"][name]["origin"])
        source.require_clean(path)
        sha = value["repositories"][name]["commit_sha"]
        continuation = value["resolution_input"].get("continuations", {}).get(name)
        if expected_head is not None:
            if not continuation or continuation["expected_head"] != expected_head or continuation["work_branch"] != work_branch:
                raise ValueError("续办 Head 必须已在接管时绑定历史基线")
            sha = expected_head
        from workflow.authorization import revoke_authorization
        revoke_authorization(base, issue, "scope_changed")
        step = operations.intent(base, operation, "branch", {}, {"branch": work_branch, "sha": sha})
        if expected_head is None and not step["before"].get("authorized_creation"):
            if source.git(path, "show-ref", "--verify", "--quiet", "refs/remotes/origin/" + work_branch, check=False).returncode == 0:
                raise ValueError("远端同名分支已存在，接管时必须明确历史基线及预期 Head")
        exists = source.git(path, "show-ref", "--verify", "--quiet", "refs/heads/" + work_branch, check=False).returncode == 0
        if exists:
            if not step["before"].get("authorized_creation") and expected_head is None:
                raise ValueError("同名分支已存在，不能自动复用")
            if source.git(path, "rev-parse", "refs/heads/" + work_branch).stdout.strip() != sha:
                raise ValueError("恢复分支 Head 与意图不一致")
        else:
            if source.git(path, "rev-parse", "HEAD").stdout.strip() != value["repositories"][name]["commit_sha"]:
                raise ValueError("新增修改仓库必须位于冻结基线")
            step["before"]["authorized_creation"] = True
            operations.save(base, operation)
            source.git(path, "branch", work_branch, sha)
        source.git(path, "checkout", work_branch)
        source.require_clean(path)
        if (source.git(path, "rev-parse", "HEAD").stdout.strip() != sha
                or source.git(path, "branch", "--show-current").stdout.strip() != work_branch):
            raise ValueError("工作分支 checkout 回读不一致")
        operations.receipt(base, operation, "branch", {"branch": work_branch, "sha": sha})
        task.setdefault("task_repositories", {})[name] = item
        task_store.write_task(base, task)
        operations.finish(base, operation)
        return operation


def amend_scope(base, issue, run_id, revision, operation_id, name, binding_digest,
                expected_head, scope, verification, decision_ref):
    """仅设计前修订已有绑定；不操作 Git，不重建绑定，不迁移交付证据。"""
    request = {"mode": "amend", "repository": name, "binding_digest": binding_digest,
               "expected_head": expected_head, "scope": scope,
               "verification": verification, "decision_ref": decision_ref}
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        previous = operations.read(base)
        recovering = previous and previous["operation_id"] == operation_id
        operation = None
        if recovering:
            operation = operations.begin(base, "scope_change", operation_id, revision, request, run_id)
            if operation["status"] == "done":
                return operation
        else:
            task_store.require_development(base, task)
        if (task.get("archive_ref") or task.get("outcome") != "in_progress"
                or task.get("stage") not in ("task_intake", "design_review")):
            raise ValueError("范围修订仅支持未归档的 task_intake/design_review")
        if not isinstance(decision_ref, str) or not decision_ref.strip():
            raise ValueError("范围修订必须有确认来源")
        if not isinstance(expected_head, str) or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", expected_head):
            raise ValueError("范围修订必须绑定完整 Head")
        current = task.get("task_repositories", {}).get(name)
        if current is None:
            raise ValueError("仓库尚未登记")
        step = operation["steps"].get("binding") if operation else None
        before = step["before"] if step else copy.deepcopy(current)
        if baseline.digest(before) != binding_digest:
            raise ValueError("仓库绑定摘要已变化")
        if (before.get("deliveries") or before.get("disposition") != "pending"
                or any((before.get("observation") or {}).get("results", {}).values())
                or any(k != "results" and v for k, v in (before.get("observation") or {}).items())):
            raise ValueError("已有交付或 PR/CI 观察，不能修订范围")
        # 仅用于输入验证；不得使用新建绑定替换已有观察和历史。
        baseline.task_repository(task["engineering_baseline"], name, before["work_branch"],
                                 before["target_branch"], scope, verification)
        after = copy.deepcopy(before)
        after.update(approved_scope=list(scope), verification_method=verification)
        if after == before:
            raise ValueError("范围和验证方式未变化")
        history = {"event": "repository_scope_amended", "operation_id": operation_id,
                   "repository": name, "before_digest": binding_digest,
                   "after_digest": baseline.digest(after), "decision_ref": decision_ref}
        applied = current == after and history in task.get("history", [])
        if (current != before and not applied) or task["_revision"] != revision + int(applied):
            raise ValueError("范围修订现场或 revision 已变化")
        if not applied:
            path = source.repository_path(base, name)
            source.identity(path, task["engineering_baseline"]["repositories"][name]["origin"])
            source.require_clean(path)
            if (source.git(path, "rev-parse", "HEAD").stdout.strip() != expected_head
                    or source.git(path, "branch", "--show-current").stdout.strip() != before["work_branch"]):
                raise ValueError("范围修订的工作分支或 Head 已变化")
        if operation is None:
            operation = operations.begin(base, "scope_change", operation_id, revision, request, run_id)
        operations.intent(base, operation, "binding", before, after)
        from workflow.authorization import revoke_authorization
        revoke_authorization(base, issue, "repository_scope_amended")
        if not applied:
            task["task_repositories"][name] = after
            task.setdefault("history", []).append(history)
            # 衍生 repositories 视图不能反写 observation，完整保留原绑定其余字段。
            task.pop("repositories", None)
            task_store.write_task(base, task)
        readback = task_store.read_task(base, issue)
        if readback["task_repositories"][name] != after or history not in readback["history"]:
            raise ValueError("范围修订回读不一致")
        operations.receipt(base, operation, "binding", {"digest": baseline.digest(after),
                                                       "revision": readback["_revision"]})
        operations.finish(base, operation)
        return operation


def evaluate_completion(base, task):
    """只读完成预检；与实际推进/释放共用，不写任务或回执。"""
    problems, deliveries, dispositions = [], [], {}
    try:
        observed = source.inspect(base, task["engineering_baseline"])
    except (ValueError, OSError) as error:
        observed = {}
        problems.append("完成前源码核验失败：%s" % error)
    for name, state in observed.items():
        if state["dirty"]:
            problems.append("完成前源码必须洁净：" + name)
        entry = task["engineering_baseline"]["repositories"][name]
        binding = task["task_repositories"].get(name)
        if state["head"] == entry["commit_sha"]:
            dispositions[name] = "no_change"
            continue
        if binding is None:
            problems.append("配套仓有未登记提交：" + name)
            continue
        if state["branch"] != binding["work_branch"]:
            problems.append("最终候选不在登记工作分支：" + name)
        valid = [item for item in binding.get("deliveries", []) if not item.get("superseded_by")]
        if len(valid) != 1:
            problems.append("awaiting_merge：变更仓必须有唯一有效合并 PR：" + name)
            continue
        delivery = valid[0]
        if (delivery.get("repository") != name or delivery.get("candidate_head") != state["head"]
                or delivery.get("pr_head") != state["head"] or delivery.get("target_branch") != binding["target_branch"]
                or not all(delivery.get(key) for key in ("pr", "merged_at", "merge_commit", "readback_ref"))):
            problems.append("awaiting_merge：PR 合并事实与最终候选不一致：" + name)
            continue
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", str(delivery["merge_commit"])):
            problems.append("合并提交必须是完整 SHA：" + name)
            continue
        try:
            merged = datetime.fromisoformat(delivery["merged_at"].replace("Z", "+00:00"))
            if merged.tzinfo is None:
                raise ValueError("合并时间需要时区")
        except (ValueError, TypeError, AttributeError):
            problems.append("合并时间不是可核验时间戳：" + name)
            continue
        deliveries.append(delivery)
        dispositions[name] = "merged"
    from workflow import project_rules, task_checks
    problems.extend(task_checks.check_advance(task, "completed", base, project_rules.load_admission(station=base)))
    from workflow import quality, pr_ready, verification
    rules = quality.config(base, task)
    if quality.enabled(task, rules) and isinstance(rules.get("pr_ready"), dict):
        problems.extend(pr_ready.quality_problems(base, task, rules))
        changed = {name for name, state in observed.items()
                   if state["head"] != task["engineering_baseline"]["repositories"][name]["commit_sha"]}
        candidate = dict(task, repositories=[item for item in task.get("repositories", []) if item["repository"] in changed])
        problems.extend(pr_ready.ci_problems(base, candidate))
        problems.extend(verification.problems(quality.replay(quality.load(base, task)), quality.context(base, task),
                                               rules["pr_ready"].get("required_verification", [])))
    if task["stage"] not in ("ci_validation", "completed"):
        problems.append("任务尚未到完成验收阶段")
    proof = {"run_id": task["run_id"], "repositories": observed, "deliveries": copy.deepcopy(deliveries),
             "dispositions": dispositions, "candidate_digest": baseline.digest(observed)}
    return {"ready": not problems, "problems": problems, "proof": proof if not problems else None}


def completion_proof(base, task):
    result = evaluate_completion(base, task)
    if not result["ready"]:
        raise ValueError("；".join(result["problems"]))
    return result["proof"]


def apply_completion(task, proof):
    """调用方必须持工位锁；仅在最终写入时更新完成处置。"""
    for name, disposition in proof["dispositions"].items():
        if name in task.get("task_repositories", {}):
            task["task_repositories"][name]["disposition"] = disposition
    task.update(outcome="completed", stage="completed", terminal_proof=proof)


def amend_cleanup(base, issue, run_id, revision, operation_id, expected_plan_digest, request):
    """同一操作精确重新确认；archive 只刷新未发布草稿，绝不授权删除。"""
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run_id)
        operation = operations.read(base)
        if (not operation or operation["operation_id"] != operation_id or operation["run_id"] != run_id
                or operation["kind"] not in ("archive", "clean", "release") or operation["status"] == "done"):
            raise ValueError("只能补充当前未完成的 archive/clean/release 操作")
        if "unbind" in operation["steps"]:
            raise ValueError("已开始解绑，不能再扩展清理范围")
        amendment = {"expected_plan_digest": expected_plan_digest, "request": request,
                     "expected_revision": revision}
        amendment_digest = baseline.digest(amendment)
        if operation.get("last_amendment", {}).get("digest") == amendment_digest:
            _flush_amendment_receipts(base, task, operation)
            return operation
        if task["_revision"] != revision:
            raise ValueError("清理补充确认的工位 revision 已变化")
        archives.recover_published(base, task, operation)
        if operation["kind"] == "archive" and task.get("archive_ref"):
            raise ValueError("正式档案已发布，不能刷新；请恢复原 archive 操作完成绑定")
        if operation["kind"] == "release":
            proof = task.get("terminal_proof") or {}
            observed = source.inspect(base, task["engineering_baseline"])
            if (task.get("outcome") != "completed" or any(not _resources().delivery_head_matches(base, name, state, proof.get("repositories", {}).get(name, {}).get("head"), operation)
                    for name, state in observed.items())):
                raise ValueError("释放后新增提交尚未核验，不能用补充文件清理绕过交付")
        current = operation.get("cleanup_plan")
        if not current:
            raise ValueError("当前操作尚无已确认清理计划")
        if current["digest"] != expected_plan_digest:
            raise ValueError("原清理计划摘要已变化，拒绝旧确认")
        history = operation.setdefault("plan_revisions", [])
        generation = len(history)
        if type(request.get("expected_plan_revision")) is not int or request["expected_plan_revision"] != generation:
            raise ValueError("原清理计划修订编号已变化或缺失，拒绝旧确认")
        plan = _resources().plan(base, task, version=current["schema_version"])
        confirmed = request.get("confirmed_digest")
        if not confirmed or confirmed != plan["digest"]:
            raise ValueError("必须明确确认当前补充清理计划摘要")
        discarded = [entry for entry in plan["entries"] if entry["preservation"]["action"] == "discard"]
        if discarded and request.get("discard_digest") != baseline.digest(discarded):
            raise ValueError("丢弃源码成果需要额外确认 discard_digest")
        history.append({"revision": generation, "plan": current,
                        "confirmation_digest": operation.get("confirmation_digest", operation["request"].get("confirmed_digest")),
                        "purpose": "archive_refresh" if operation["kind"] == "archive" else "cleanup",
                        "superseded_by": confirmed, "superseded_revision": generation + 1, "amendment_digest": amendment_digest})
        if not task.get("archive_ref") and operation.get("archive_record"):
            operation.setdefault("archive_drafts", []).append({"plan_revision": generation,
                "record": operation.pop("archive_record"), "evidence": operation.pop("archive_evidence", None), "artifacts": operation.pop("archive_artifacts", None), "logs": operation.pop("archive_logs", None),
                "publication_intent": copy.deepcopy(operation["steps"].get("archive-publish:" + str(generation)))})
        for name, step in operation["steps"].items():
            if name.startswith(("resource:", "source-reset:", "station-source-reset:", "external:", "clear-active:", "archive-publish:")) and step["receipt"] is None and not step.get("superseded_by"):
                step["superseded_by"] = confirmed
                step["superseded_plan_digest"] = expected_plan_digest
                step["superseded_plan_revision"] = generation
                step["superseded_revision"] = generation + 1
        operation["cleanup_plan"] = plan
        operation["confirmation_digest"] = confirmed
        if operation["kind"] in ("clean", "release"):
            operation["phase"] = "intent"
        if task.get("archive_ref"):
            operation["cleanup_manifest"] = {"cleanup_plan_digest": confirmed, "confirmation_digest": confirmed,
                                             "archive_digest": task["archive_ref"]["digest"]}
        else:
            operation.pop("cleanup_manifest", None)
        operation.setdefault("amendment_receipts", []).append({"plan_revision": generation + 1,
            "plan": plan, "confirmation_digest": confirmed, "amendment_digest": amendment_digest})
        operation["last_amendment"] = dict(amendment, digest=amendment_digest)
        # 保留原始请求身份与已完成中立状态，不能让 release 重新判定或改写结果。
        operations.save(base, operation)
        _flush_amendment_receipts(base, task, operation)
        return operation


def _verify_cleanup_decision(base, task, request):
    plan = _resources().plan(base, task, version=request.get("cleanup_version", 6))
    if request.get("confirmed_digest") != plan["digest"] or not request.get("decision_ref"):
        raise ValueError("请展示重置范围并确认 confirmed_digest 与真实 decision_ref")
    discard = [e for e in plan["entries"] if e["preservation"]["action"] == "discard"]
    if discard and request.get("discard_digest") != baseline.digest(discard):
        raise ValueError("丢弃源码成果需要额外确认 discard_digest")


def execute(base, kind, issue, run_id, revision, operation_id, request, cleanup_mode="execute"):
    if kind not in ("archive", "release", "clean"):
        raise ValueError("未知生命周期操作")
    if cleanup_mode not in ("execute", "prepare", "verify"):
        raise ValueError("未知清理执行模式")
    _resources().require_cleanup_version(request.get("cleanup_version", 6))
    if cleanup_mode != "execute" and kind == "archive":
        raise ValueError("Agent 接力只用于版本 6 清理操作")
    with task_store.task_state_lock(base):
        previous = operations.read(base)
        if previous and previous["operation_id"] == operation_id and "unbind" in previous["steps"] and task_store.read_current(base)["current"] is None:
            if (previous["request_digest"] != baseline.digest(request) or previous["run_id"] != run_id
                    or previous["kind"] != kind or previous["expected_revision"] != revision):
                raise ValueError("解绑恢复请求不一致")
            if task_store.read_current(base)["current"] is not None:
                raise ValueError("解绑恢复发现其它任务")
            operations.receipt(base, previous, "unbind", {"current": None})
            final_task = previous["final_task"]
            archives.receipt(base, final_task, previous, {"outcome": final_task["outcome"], "current": None}, "done")
            operations.finish(base, previous)
            return previous
        task = task_store.check_expected_run(base, issue, run_id)
        if kind == "release" and task.get("archive_ref"):
            record = archives.verify(base, task["archive_ref"], task)
            if record["task_result"] != "completed" or task.get("outcome") != "completed":
                raise ValueError("未完成档案只能 clean；不能后置完成再复用 incomplete 档案释放")
        fresh = not previous or previous["operation_id"] != operation_id
        if fresh:
            if kind == "clean" and request.get("abandon_changes") is not True:
                raise ValueError("未完成任务需要明确放弃变更，未写入状态")
            if kind in ("archive", "clean", "release"):
                _verify_cleanup_decision(base, task, request)
            if not task.get("archive_ref"):
                baseline.text(request.get("summary"), "归档总结")
                baseline.text(request.get("reason"), "归档原因")
                if project_rules.scan_sensitive(project_rules.load_admission(station=base), request["summary"] + "\n" + request["reason"]):
                    raise ValueError("归档输入包含敏感内容")
            _resources().verify_stopped(base, task)
            _resources().verify_known_external(base, task)
            if kind != "archive":
                candidate_plan = _resources().plan(base, task, version=request.get("cleanup_version", 6))
                if request.get("confirmed_digest") != candidate_plan["digest"]:
                    raise ValueError("必须确认当前精确 cleanup plan digest")
            if kind == "release":
                proof = task.get("terminal_proof") if task.get("outcome") == "completed" else completion_proof(base, task)
                if not proof or request.get("candidate_digest") != proof["candidate_digest"]:
                    raise ValueError("释放确认必须绑定最终候选摘要")
                if baseline.digest(source.inspect(base, task["engineering_baseline"])) != proof["candidate_digest"]:
                    raise ValueError("完成后源码已变化，拒绝归档释放")
            if kind == "clean" and task.get("outcome") == "completed":
                raise ValueError("已完成任务应释放")
        if previous and previous["kind"] == "takeover" and previous["status"] != "done" and kind == "clean":
            handoff = previous.get("handoff")
            plan = handoff["cleanup_plan"] if handoff else _resources().plan(base, task, version=request.get("cleanup_version", 6))
            operation = operations.handoff_clean(base, operation_id, revision, request, run_id, plan)
        else:
            operation = operations.begin(base, kind, operation_id, revision, request, run_id)
        if operation["status"] == "done":
            return operation
        resources = _resources()
        # 恢复也核查，不因 fresh=False 跳过未知外部写入；与回执保存共用工位锁。
        resources.verify_known_external(base, task)
        resources.verify_stopped(base, task)
        if kind == "release" and task.get("outcome") != "completed":
            proof = completion_proof(base, task)
            if request.get("candidate_digest") != proof["candidate_digest"]:
                raise ValueError("释放确认必须绑定最终候选摘要")
            apply_completion(task, proof)
            task_store.write_task(base, task)
        if kind == "clean" and task.get("outcome") == "completed":
            raise ValueError("已完成任务应释放，不允许改写为未完成")
        plan = operation.get("cleanup_plan")
        if plan is None:
            plan = resources.plan(base, task, version=request.get("cleanup_version", 6))
            if kind != "archive" and request.get("confirmed_digest") != plan["digest"]:
                raise ValueError("清理或释放必须明确确认当前精确 cleanup plan digest")
            operation["cleanup_plan"] = plan
            operations.save(base, operation)
        resources.require_cleanup_version(plan.get("schema_version"))
        resources.verify_station_inventory(base, plan["rules"], allow_pending=True)
        reference = archives.publish(base, task, request, plan, operation, lambda: resources.plan(base, task, version=plan["schema_version"]))
        _flush_amendment_receipts(base, task, operation)
        if kind == "archive":
            operations.finish(base, operation)
            return operation
        if kind == "clean":
            task["outcome"] = "interrupted"
            task_store.write_task(base, task)
        confirmed = operation.get("confirmation_digest", request["confirmed_digest"])
        previous_result = operation.get("cleanup_manifest", {}).get("result")
        operation["cleanup_manifest"] = {"cleanup_plan_digest": plan["digest"],
                                         "confirmation_digest": confirmed, "archive_digest": reference["digest"]}
        if previous_result and previous_result.get("plan_digest") == plan["digest"]:
            operation["cleanup_manifest"]["result"] = previous_result
        operations.save(base, operation)
        from workflow import station_reset_result
        if cleanup_mode == "prepare":
            operation["phase"] = "awaiting_cleanup_result"
            operations.save(base, operation)
            return operation
        if cleanup_mode == "execute":
            station_reset_result.apply(base, task, operation)
        operation["cleanup_manifest"]["result"] = station_reset_result.record(base, task, operation)
        operation["phase"] = "neutral"
        operations.save(base, operation)
        # 已按实际清理成果核验；活动授权随归档保全后的 clear-active 意图移除。
        if operation.get("plan_revisions"):
            _plan_receipt(base, task, operation, "resources-released")
        else:
            archives.receipt(base, task, operation, {"outcome": task["outcome"], "cleanup_manifest": operation["cleanup_manifest"]})
        resources.verify_station_inventory(base, plan.get("rules"))
        _clear_active(base, operation)
        operation["final_task"] = {key: task[key] for key in ("issue_key", "run_id", "outcome", "archive_ref")}
        operations.save(base, operation)
        operations.intent(base, operation, "unbind", {"run_id": run_id}, {"current": None})
        task_store.compare_and_set(base, task["_revision"], None)
        operations.receipt(base, operation, "unbind", {"current": None})
        operation["phase"] = "unbound"
        operations.save(base, operation)
        archives.receipt(base, task, operation, {"outcome": task["outcome"], "current": None}, "done")
        operations.finish(base, operation)
        return operation
