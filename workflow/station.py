"""单任务工位四操作；所有副作用在同一工位锁和持久化操作下运行。"""
from __future__ import annotations

import importlib.util
import hashlib
import copy
import re
from datetime import datetime
import json
from pathlib import Path

from workflow import station_context, station_layout
from workflow import archive_store, engineering_baseline as baseline, project_rules, station_archive as archives
from workflow import station_operation as operations, station_source as source, task_store


def _resources():
    from workflow import station_resources
    return station_resources


def _project(base):
    root, project = project_rules.station_context(base)
    return project_rules.project_root(root, project)


def takeover(base, request, operation_id, expected_revision):
    with task_store.task_state_lock(base, ownership=False):
        issue = task_store.validate_issue_key(request["issue_key"])
        project_profile = project_rules.load_profile(station=base)
        project_rules.validate_project_issue(project_profile, issue)
        previous = operations.read(base)
        routing = None
        # 已开始的旧操作按原请求恢复；不向旧请求补字段或重算摘要。
        if project_profile.get("jira", {}).get("routing") and not (
                previous and previous["operation_id"] == operation_id and "jira_snapshot" not in request):
            from workflow import issue_routing
            routing = issue_routing.resolve(project_profile, issue, request.get("jira_snapshot"))
            if project_rules.scan_sensitive(project_rules.load_admission(station=base),
                                            json.dumps(request["jira_snapshot"], ensure_ascii=False)):
                raise ValueError("类型快照包含敏感内容；仅提供必要身份字段和脱敏来源")
            if request["task_class"] != routing["task_class"]:
                raise ValueError("真实 Jira 类型要求 %s，拒绝指定其它流程" % routing["task_class"])
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
            _resources().check_retained_resources(base, previous)
            _resources().verify_station_inventory(base)
            for name in selected:
                path = source.repository_path(base, name)
                if path.exists():
                    source.identity(path, catalog[name]["origin"])
                    source.require_clean(path)
            if any((task_store.state_path(base) / name).exists() for name in ("authorization.json", "evidence")):
                raise ValueError("空闲工位仍有活动授权或证据，拒绝接管")
        if not previous or previous["operation_id"] != operation_id:
            runtime = Path(base) / station_layout.station_name(base, "task-runtime")
            if runtime.is_symlink() or not runtime.is_dir() or any(runtime.iterdir()):
                raise ValueError("接管前 runtime 必须为空；保留残留，尚未创建操作")
        from workflow import station_takeover, jira_ownership
        def prepare_request():
            jira_ownership.preflight(base, issue, request.get('jira_snapshot', {}), request['task_class'])
            value = station_takeover.preflight(base, request)
            return value
        operation = operations.begin(base, "takeover", operation_id, expected_revision, request,
                                     before_create=prepare_request)
        if operation["status"] == "done":
            return operation
        task = task_store.read_task(base)
        from workflow import station_directories
        if task is None:
            task = {"issue_key": issue, "run_id": operation["run_id"], "task_class": request["task_class"],
                    "stage": "waiting_takeover", "outcome": "in_progress", "facts": {"station_contract": 3}, "history": [],
                    "pending": None, "engineering_baseline": {"status": "resolving"},
                    "task_repositories": {}, "terminal_proof": None, "archive_ref": None}
            if routing is not None:
                task["facts"]["jira_routing"] = routing
            runtime = station_directories.path_at(base, station_layout.station_name(base, "task-runtime"))
            if any(runtime.iterdir()):
                raise ValueError("接管前 runtime 必须为空")
            task["retained_repositories"] = source.check_station_layout(base, catalog, selected)
            task["initial_runtime"] = {"path": station_layout.station_name(base, "task-runtime"), "run_id": task["run_id"],
                "station_id": station_context.read_binding(base)["station_id"],
                "kind": "runtime-exclusive", "producer": "workflow", "recipe": {"id": "workflow-runtime", "revision": 1},
                "parent": station_directories.identity(runtime.parent), "identity": station_directories.identity(runtime),
                "disposition": "clear_children_keep_root"}
            task_store.write_task(base, task)
        if task["run_id"] != operation["run_id"]:
            raise ValueError("接管恢复 run 不一致")
        if not operation.get('takeover_baseline'):
            # 旧接管采用新顺序，先核验原请求；不重建 run 或冒充原历史已有预检。
            operation['takeover_baseline'] = station_takeover.preflight(base, request)
            operations.save(base, operation)
        if not station_takeover.publication_ready(base, task, operation):
            return operation
        operation['phase'] = 'preparing_source'
        operations.save(base, operation)
        if 'shared-references' not in operation['steps']:
            operations.intent(base, operation, 'shared-references', {}, {})
        if operation['steps']['shared-references']['receipt'] is None:
            source.refresh_shared_references(base)
            operations.receipt(base, operation, 'shared-references', {'attempted': True})
        if "retained_repositories" not in task:
            task["retained_repositories"] = source.check_station_layout(base, catalog, selected)
            task_store.write_task(base, task)
        from workflow import station_directories
        station_directories.create(base, task, station_layout.station_name(base, "task-runtime"), "workflow", adopt=True)
        if task["engineering_baseline"]["status"] != "frozen":
            observations = source.prepare_repositories(base, catalog, selected, operation)
            specification = importlib.util.spec_from_file_location("station_project_resolver", project / "scripts/engineering_baseline.py")
            module = importlib.util.module_from_spec(specification)
            specification.loader.exec_module(module)
            config = json.loads((project / "version-branch-alignments.json").read_text())
            value = copy.deepcopy(operation.get("takeover_baseline"))
            if value is None:  # 合法旧操作继续复用原请求与原准备回执。
                value = module.resolve(request["version"], config, catalog, profile, observations,
                                       optional=request.get("optional_repositories", []),
                                       explicit_branches=request.get("explicit_branches", {}))
            else:
                baseline.validate(value)
                if value["profile"] != {"id": profile["id"], "revision": profile["revision"], "digest": baseline.digest(profile)}:
                    raise ValueError("预检后工程 Profile 已变化，保留原操作")
                if any(entry["rule_version"] != baseline.digest(config) for entry in value["repositories"].values()):
                    raise ValueError("预检后项目分支规则已变化，保留原操作")
                for name, entry in value["repositories"].items():
                    if entry["origin"] != catalog[name]["origin"]:
                        raise ValueError("预检后仓库来源已变化，保留原操作：" + name)
                    path = source.repository_path(base, name)
                    sha = entry["commit_sha"]
                    step = "baseline-object:" + name + ":" + sha
                    if source.git(path, "cat-file", "-t", sha, check=False).stdout.strip() != "commit":
                        operations.intent(base, operation, step, {}, {"sha": sha})
                        source.git(path, "fetch", "--no-tags", "origin", sha)
                        if source.git(path, "cat-file", "-t", sha).stdout.strip() != "commit":
                            raise ValueError("预检对象无法取回，保留原操作：" + name)
                    if step in operation["steps"]:
                        operations.receipt(base, operation, step, {"sha": sha})
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
                    raise ValueError("续办工作分支与预期 Head 不一致；先回读远端与最新 Jira 决定，核实新增成果后整理当前 continuation-input。确需历史点时，经授权将原 SHA 保全到独立远端分支再接续，不能回退或覆盖原分支")
                if source.git(path, "merge-base", "--is-ancestor", continuation["baseline_sha"], continuation["expected_head"], check=False).returncode:
                    raise ValueError("续办历史基线不是候选祖先")
                resolutions[name].update(ref_kind="commit", ref_name=continuation["baseline_sha"],
                                         commit_sha=continuation["baseline_sha"], resolution_source="explicit_continuation")
            if continuations:
                inputs = dict(value["resolution_input"], continuations=continuations)
                value = baseline.freeze(profile, catalog, resolutions, inputs, request.get("optional_repositories", []))
            if operation.get("takeover_baseline"):
                # 远端分支可能已推进，预检固定的 SHA 仍是本次工程对象，不改写本地远端 refs。
                for name, entry in value["repositories"].items():
                    baseline.verify_local_repository(base, name, entry["origin"], "commit",
                                                     entry["commit_sha"], entry["commit_sha"])
            else:
                baseline.verify_local_baseline(base, value)
            task["engineering_baseline"] = value
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
                                               rules["pr_ready"].get("required_verification", []), rules))
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


def execute(base, kind, issue, run_id, revision, operation_id, request, cleanup_mode="execute", jira_readback=None):
    from workflow import station_clean
    return station_clean.execute(base, kind, issue, run_id, revision, operation_id, request, cleanup_mode, jira_readback=jira_readback)
