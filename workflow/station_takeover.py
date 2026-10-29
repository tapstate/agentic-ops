"""接管预检和未冻结请求更正；不改变通用操作的重试语义。"""
from __future__ import annotations

import copy
import importlib.util
import json

from workflow import engineering_baseline as baseline, project_rules, station_operation as operations
from workflow import station_source as source, task_store


def preparing(base, task):
    operation = operations.read(base) or {}
    return (operation.get('kind') == 'takeover' and operation.get('status') != 'done'
            and operation.get('run_id') == task['run_id'] and bool(operation.get('takeover_baseline'))
            and not operation.get('handoff') and task.get('outcome') == 'in_progress'
            and not task.get('archive_ref') and task.get('stage') == 'waiting_takeover')


def require_external(base, task):
    if not preparing(base, task):
        task_store.require_development(base, task)


def publication_ready(base, task, operation):
    from workflow import jira_ownership, external_sync, quality, jira_status
    try:
        jira_ownership.require(base, task['issue_key'])
    except ValueError:
        operation['phase'] = 'awaiting_markers'
        operations.save(base, operation)
        return False
    publications = quality.replay(quality.load(base, task))['publications']
    key = external_sync.takeover_publication_id(base, task, publications)
    notice = publications.get(key)
    if not notice or notice['status'] not in ('created', 'unknown', 'verified', 'deferred'):
        operation['phase'] = 'awaiting_notice'
        operations.save(base, operation)
        return False
    # 通知结果未知仍保留待办；状态必须紧接着处理，不能先进入源码准备。
    rules = jira_status.config(base, task)
    if ('takeover' in rules.get('attempts', {}) and jira_status.load_state(base, task)['attempts'].get(
            'takeover', {}).get('outcome') in (None, 'ready')):
        operation['phase'] = 'awaiting_jira_status'
        operations.save(base, operation)
        return False
    return True


def inputs(base, request):
    root, project = project_rules.station_context(base)
    directory = project_rules.project_root(root, project)
    profiles = json.loads((directory / "engineering-profiles.json").read_text())
    profile = profiles["profiles"][request.get("profile", "full-application")]
    catalog = project_rules.load_repository_catalog(station=base)["repositories"]
    config = json.loads((directory / "version-branch-alignments.json").read_text())
    specification = importlib.util.spec_from_file_location("station_project_resolver", directory / "scripts/engineering_baseline.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module, config, catalog, profile


def preflight(base, request):
    module, config, catalog, profile = inputs(base, request)
    value = module.preflight(base, request["version"], config, catalog, profile,
                             optional=request.get("optional_repositories", []),
                             explicit_branches=request.get("explicit_branches", {}))
    return value


def correct(base, issue, run_id, operation_id, revision, request_digest, version, overrides, decision_ref):
    """同一锁内核验并原子保存请求与预检基线；重复调用只回读既有更正。"""
    baseline.text(decision_ref, "decision_ref")
    with task_store.task_state_lock(base, ownership=False):
        task = task_store.check_expected_run(base, issue, run_id)
        operation = operations.read(base)
        if (not operation or operation["kind"] != "takeover" or operation["operation_id"] != operation_id
                or operation["run_id"] != run_id or operation.get("handoff")
                or operation["request"].get("issue_key") != task["issue_key"]):
            raise ValueError("必须更正当前未交接的原 takeover 操作")
        request = copy.deepcopy(operation["request"])
        request.update(version=version, explicit_branches=overrides)
        after_digest = baseline.digest(request)
        revisions = operation.get("takeover_revisions", [])
        if revisions and revisions[-1] == {"before_request": revisions[-1]["before_request"],
                "before_digest": request_digest, "after_digest": after_digest,
                "expected_revision": revision, "decision_ref": decision_ref}:
            return operation
        if operation["request_digest"] != request_digest or task["_revision"] != revision:
            raise ValueError("接管请求或工位 revision 已变化，请重新回读")
        if (operation["status"] == "done" or task["engineering_baseline"]["status"] != "resolving"
                or task.get("stage") != "waiting_takeover" or task.get("outcome") != "in_progress"
                or task.get("task_repositories") or task.get("source_prepared") or task.get("archive_ref")
                or task.get("terminal_proof")
                or (task_store.state_path(base) / "authorization.json").exists()):
            raise ValueError("接管已有冻结基线、开发绑定或授权，不能更正；保留成果并核实后续路径")
        if after_digest == request_digest:
            raise ValueError("接管参数未变化，请沿原请求恢复")
        from workflow import jira_coordination
        jira_coordination.require_retired(base)
        _, _, catalog, profile = inputs(base, request)
        selected = baseline.selected_repositories(profile, catalog, request.get("optional_repositories", []))
        pinned = operation.get("takeover_baseline")
        if pinned and (set(pinned["repositories"]) != set(selected)
                       or pinned["profile"]["digest"] != baseline.digest(profile)):
            raise ValueError("原接管工程 Profile 已变化，不能通过版本更正改变仓库集合")
        source.verify_takeover_preparation(base, catalog, selected, operation)
        value = preflight(base, request)
        # 远端读取期间原生工具仍可能修改源码；写入前再次核验现场。
        source.verify_takeover_preparation(base, catalog, selected, operation)
        updated = copy.deepcopy(operation)
        updated.setdefault("takeover_revisions", []).append({
            "before_request": operation["request"], "before_digest": request_digest,
            "after_digest": after_digest, "expected_revision": revision, "decision_ref": decision_ref})
        updated.update(request=request, request_digest=after_digest, takeover_baseline=value)
        operations.save(base, updated)
        return operations.read(base)
