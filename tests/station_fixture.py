"""测试专用单工位状态夹具；不提供产品迁移入口。"""
import json
import shutil
from pathlib import Path
from workflow import engineering_baseline, task_store


def product_cli(station, relative):
    """旧场景的最小规则夹具补齐同源 CLI，避免跨产品根混用代码与规则。"""
    source = Path(__file__).resolve().parents[1]
    try:
        binding = json.loads((Path(station) / '.agenticops/station.json').read_text())
        root = Path(binding['product_root']).resolve()
    except (OSError, ValueError, KeyError, TypeError):
        return source / relative  # 缺绑定的拒绝场景仍走实际 CLI。
    if root == source:
        return source / relative
    if not (root / 'workflow/task.py').is_file():
        def missing_only(src, dst):
            if not Path(dst).exists():
                shutil.copy2(src, dst)
            return dst
        for directory in ('bootstrap', 'workflow', 'gate', 'contracts', 'policies'):
            shutil.copytree(source / directory, root / directory, dirs_exist_ok=True,
                            copy_function=missing_only, ignore=shutil.ignore_patterns('__pycache__'))
        from bootstrap import product_state
        if not product_state.state_path(root).exists():
            product_state.save(root, {'schema_version': 2, 'mode': 'source',
                'repository': 'fixture', 'tracking_branch': 'develop', 'current_ref': 'a' * 40,
                'previous_ref': None, 'source_pool': str(root / 'pool')})
    return root / relative


def save_task(base, task, ownership=True):
    initialize_station(base)
    (task_store.state_path(base) / "evidence").mkdir(parents=True, exist_ok=True)
    repositories = task.get("repositories", [])
    if any((row.get("worktree") or {}).get("status") == "prepared" for row in repositories):
        task["source_prepared"] = True
    if repositories:
        catalog = {r["repository"]: {"origin": r.get("origin") or "https://" + r.get("authorized_endpoint", "github.com/" + r["repository"])} for r in repositories}
        profile = {"id": "test-fixture", "revision": 1,
                   "repositories": list(catalog), "optional_repositories": []}
        resolutions = {r["repository"]: {"verification": "verified", "ref_kind": "branch",
                        "ref_name": r.get("base_branch") or "develop", "commit_sha": r.get("base_sha") or "a" * 40,
                        "resolution_source": "fixture", "rule_version": "fixture-1"} for r in repositories}
        frozen = engineering_baseline.freeze(profile, catalog, resolutions, {"fixture": True})
        task["engineering_baseline"] = frozen
        bindings = {}
        for repository in repositories:
            name = repository["repository"]
            scope = repository.get("approved_scope") or "fixture scope"
            binding = engineering_baseline.task_repository(frozen, name,
                repository.get("work_branch") or "fix/fixture", repository.get("base_branch") or "develop",
                scope if isinstance(scope, list) else [scope], repository.get("verification_method") or "fixture verification")
            binding["observation"] = {key: value for key, value in repository.items()
                                      if key in ("worktree", "pull_request", "ci", "catalog_digest")}
            bindings[name] = binding
        task["task_repositories"] = bindings
    else:
        task.setdefault("engineering_baseline", {"status": "resolving"})
        task.setdefault("task_repositories", {})
    task.setdefault("outcome", "in_progress")
    task.setdefault("archive_ref", None)
    task.setdefault("terminal_proof", None)
    current = task_store.initialize_current(base)
    previous = current["current"]
    if previous and (previous["issue_key"], previous["run_id"]) != (task["issue_key"], task["run_id"]):
        current = task_store.compare_and_set(base, current["revision"], None)
    task["_revision"] = current["revision"]
    task_store.write_task(base, task)
    if ownership:
        coordination_proof(base, task["issue_key"])
    normalized = task_store.read_task(base, task["issue_key"])
    task["repositories"] = normalized["repositories"]


def initialize_station(base):
    state = task_store.state_path(base)
    binding_path = state / "station.json"
    binding = json.loads(binding_path.read_text())
    binding["schema_version"] = 4
    binding.setdefault("station_id", "a" * 32)
    binding["agents"] = binding.get("agents") or ["codex"]
    binding["source_pool"] = binding.get("source_pool") or str(Path(base) / "pool")
    binding.pop("repository_pool", None)
    task_store._write_json_atomic(binding_path, binding)
    manifest = Path(binding["product_root"]) / "contracts/station-state-compatibility.json"
    if not manifest.exists():
        original = Path(__file__).resolve().parents[1] / "contracts/station-state-compatibility.json"
        task_store._write_json_atomic(manifest, json.loads(original.read_text()))
    epoch = json.loads(manifest.read_text())["station_state_epoch"]
    task_store._write_json_atomic(state / "init.json", {"station_state_epoch": epoch,
        "cleanup": json.loads((Path(__file__).resolve().parents[1] / "policies/station-clean.json").read_text())})


def task_state_snapshot(station):
    """比较任务信封；活动时间另由执行接管测试核验。"""
    value = task_store.read_current(station)
    value.pop("execution_control", None)
    return value


def ownership_snapshot(base, issue='TAP-123', values=None):
    from workflow import project_rules
    profile = project_rules.load_profile(station=base)
    rules = profile['jira']['takeover_watermark']
    fields = {key: None for key in [rules['field_id'], *rules.get('marker_fields', {}).values()]}
    fields.update(values or {})
    fields.update(issuetype={'id': '10011'}, assignee={'accountId': 'fixture-user'}, status={'name': 'Analyzed'})
    return {'site': profile['jira']['site'].rstrip('/'), 'source_ref': 'fixture:jira/issue',
            'issue': {'id': '123', 'key': issue, 'fields': fields},
            'current_user': {'accountId': 'fixture-user', 'displayName': 'Fixture', 'active': True, 'source_ref': 'fixture:jira/me'}}


def coordination_proof(base, issue_key, identity=None):
    """其它功能的已核验归属夹具；四项标记专项通过真实 prepare/complete 验证。"""
    from workflow import jira_watermark as marker, project_rules, execution, station_context
    current = task_store.read_current(base)
    current.pop('jira_coordination', None)
    task_store._write_json_atomic(task_store.current_path(base), current)
    task = task_store.read_task(base, issue_key)
    if task is None:
        return
    if marker.state_path(base, task).exists() and identity is None:
        return
    root, project = project_rules.station_context(base)
    directory = project_rules.project_root(root, project)
    if not (directory / 'profile.json').exists():
        # 与 Jira 无关的最小模块夹具也提供明确的项目归属合同，不修改产品的缺配置拒绝逻辑。
        template = json.loads((Path(__file__).resolve().parents[1] / 'projects/tapdata/profile.json').read_text())
        directory.mkdir(parents=True, exist_ok=True)
        task_store._write_json_atomic(directory / 'profile.json', {
            'jira': {'site': 'https://fixture.atlassian.net', 'takeover_watermark': template['jira']['takeover_watermark']},
            'repositories': {'catalog': 'repositories.json'}})
        if not (directory / 'repositories.json').exists():
            task_store._write_json_atomic(directory / 'repositories.json', {'schema_version': 1, 'repositories': {}})
    profile = project_rules.load_profile(station=base)
    rules = profile.get('jira', {}).get('takeover_watermark', {})
    field = rules.get('field_id', 'customfield_10421')
    mapping = rules.get('marker_fields', {'begin_at': 'customfield_10366', 'station': 'customfield_10462', 'run_id': 'customfield_10365'})
    expected = {field: 'fixture-version', mapping['begin_at']: '2026-10-29T08:00:00+0000',
                mapping['station']: station_context.read_binding(base)['station_id'], mapping['run_id']: task['run_id']}
    record = {'at': '2026-10-29T08:00:00+0000', 'source_ref': 'fixture:jira', 'issue_key': issue_key,
        'field_id': field, 'field_name': 'AgenticOps Version', 'logical_key': 'agenticops_version',
        'issue_type_id': '10011', 'version': 'fixture-version', 'write_mode': 'overwrite',
        'payload_digest': marker.marker_digest(expected), 'outcome': 'verified', 'reason': 'fixture',
        'marker_version': 2, 'marker_fields': mapping, 'expected_fields': expected,
        'actor': {'accountId': 'fixture-user', 'displayName': 'Fixture', 'source_ref': 'fixture:jira/me'},
        'completed_at': '2026-10-29T08:00:00+0000', 'readback_ref': 'fixture:jira', 'readback_fields': expected,
        'readback_value': 'fixture-version', 'verified_ref': 'fixture:jira',
        'ownership': {'site': profile.get('jira', {}).get('site', 'https://fixture.atlassian.net').rstrip('/'),
                      'issue_id': '123', 'session_id': identity or execution.session_id()}}
    task_store._write_json_atomic(marker.state_path(base, task), {'schema_version': 1, 'issue_key': issue_key,
        'run_id': task['run_id'], 'watermark': record})


def transferred_ownership_fixture(base):
    """模拟已观察到其它工位接管；保留原意图和首次核验历史。"""
    from workflow import jira_watermark as marker
    task = task_store.read_task(base)
    state = marker.load_state(base, task)
    record = state['watermark']
    actual = dict(record['expected_fields'])
    actual[record['marker_fields']['station']] = 'b' * 32
    actual[record['marker_fields']['run_id']] = 'run-fedcba987654'
    record.update(outcome='unknown', reason='markers_unknown', readback_fields=actual,
                  readback_ref='fixture:jira/transferred', readback_value=record['version'])
    record.pop('native_request', None)
    marker.validate_record(record)
    task_store._write_json_atomic(marker.state_path(base, task), state)


def prepared_ownership_fixture(base, task, operation):
    """源码生命周期专项不测远端通知；仅提供明确的归属夹具，专项另覆盖真实屏障。"""
    coordination_proof(base, task['issue_key'])
    return True


def complete_takeover_registration(base, snapshot):
    """隔离集成测试的原生 Jira 响应夹具；通过真实标记、发布和状态入口登记。"""
    import copy
    from datetime import datetime, timezone
    from workflow import jira_watermark, external_sync, quality, jira_status
    task = task_store.read_task(base)
    observed = copy.deepcopy(snapshot)
    marker = jira_watermark.prepare(base, task['issue_key'], observed)
    observed['issue']['fields'].update(marker['expected_fields'])
    jira_watermark.complete(base, task['issue_key'], 'unknown', observed)
    task = task_store.read_task(base)
    row = next(r for r in external_sync.actions(base, task)['sync_actions'] if r['kind'] == 'takeover_comment')
    def apply(action, payload):
        state = quality.load(base, task)
        return quality.apply(base, task['issue_key'], task['run_id'], state['revision'], {'action': action, 'payload': payload})
    view = apply('draft', {'id': row['id'], 'body': row['body']})
    digest = view['publications'][row['id']]['digest']
    apply('confirm', {'id': row['id'], 'digest': digest, 'proof': {
        'actor': 'Fixture', 'source': 'user_message', 'reference': 'fixture:takeover',
        'at': datetime.now(timezone.utc).isoformat()}})
    view = apply('prepare_write', {'id': row['id'], 'digest': digest, 'takeover_snapshot': observed})
    record = view['publications'][row['id']]
    apply('readback', {'id': row['id'], 'operation_id': record['operation_id'], 'site': record['site'],
        'issue_key': task['issue_key'], 'comment_id': '100', 'body': row['body'], 'source_ref': 'fixture:jira/100',
        'author_account_id': observed['current_user']['accountId']})
    if 'takeover' in jira_status.config(base, task).get('attempts', {}):
        observed['issue']['fields']['status'] = {'id': '3', 'name': 'In Progress'}
        jira_status.prepare(base, task['issue_key'], 'takeover', observed)
