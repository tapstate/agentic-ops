"""两个生命周期清理操作；复用资源收尾，不解释业务数据，不强制归档。"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

from workflow import engineering_baseline as baseline, station_clean_rules as rules
from workflow import station_cleanup_tree as trees, station_context, station_layout
from workflow import station_operation as operations, station_source_preservation as preservation, task_store


def initial_paths(base, config):
    """只保留当前初始化清单接线；修改过的接线由现有 repair 能力处理。"""
    protected = {'.agenticops'}
    for item in config['owned']:
        relative = item['path']
        path = trees.path_for(base, relative)
        if item.get('kind', 'file') == 'symlink':
            import os
            valid = path.is_symlink() and os.readlink(path) == item['target']
        else:
            valid = path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == item['sha256']
        if not valid:
            raise ValueError('初始接线已修改或缺失，先用本版 repair 核实恢复：' + relative)
        protected.add(relative)
    return protected


def plan(base, scope='task'):
    if scope not in ('task', 'station'):
        raise ValueError('清理范围必须为 task 或 station')
    base = Path(base).resolve()
    config = rules.load(base)
    current = task_store.read_current(base)
    task = current['current']
    source = station_layout.station_name(base, 'repositories')
    runtime = station_layout.station_name(base, 'task-runtime')
    targets = [trees.entry(base, name, recreate=True) for name in (source, runtime)]
    if scope == 'station':
        protected = initial_paths(base, config)
        def visit(folder):
            for path in sorted(folder.iterdir()):
                name = path.relative_to(base).as_posix()
                if name in protected or name in (source, runtime):
                    continue
                if any(item.startswith(name + '/') for item in protected):
                    if path.is_symlink() or not path.is_dir():
                        raise ValueError('初始接线父目录异常：' + name)
                    visit(path)
                else:
                    recreate = name == station_layout.station_name(base, 'configuration')
                    targets.append(trees.entry(base, name, recreate=recreate))
        visit(base)
    other_repositories = []
    for item in targets[1:]:
        path = trees.path_for(base, item['path'])
        if path.is_dir() and not path.is_symlink():
            other_repositories.extend(repo.relative_to(base).as_posix() for repo in preservation.assert_isolated(path))
    source_report = preservation.source_preservation(base, config)
    if other_repositories:
        source_report['other_repositories'] = other_repositories
        source_report['requires_decision'] = True
    value = {'schema_version': 8, 'scope': scope, 'revision': current['revision'],
             'run_id': task['run_id'] if task else None, 'config': config, 'targets': targets,
             'source': source_report}
    value['digest'] = baseline.digest(value)
    return value


def validate(plan):
    from workflow import quality_contract
    quality_contract.validate(plan, 'station-reset.schema.json')
    if (not isinstance(plan, dict) or plan.get('schema_version') != 8
            or plan.get('scope') not in ('task', 'station') or not isinstance(plan.get('targets'), list)
            or baseline.digest({key: value for key, value in plan.items() if key != 'digest'}) != plan.get('digest')):
        raise ValueError('生命周期清理范围格式或摘要无效')
    paths = [item['path'] for item in plan['targets']]
    if len(paths) != len(set(paths)) or any(len(item['parent']) != 3 or item['snapshot'] and len(item['snapshot']['identity']) != 3 for item in plan['targets']):
        raise ValueError('清理路径重复或目录身份无效')


def clear_state(base, operation):
    from workflow import station_clean, station_directories
    station_clean._clear_active(base, operation)
    root = task_store.state_path(base)
    for name in ('events.jsonl', 'git-ref-cache-v2.json', 'git-ref-cache-v2.json.lock'):
        path = root / name
        if path.is_symlink() or path.exists() and not path.is_file():
            raise ValueError('任务运行数据类型异常，先核实：' + name)
        if path.exists():
            path.unlink()
    keep = {'station.json', 'init.json', 'current-task.json', 'operation.json', 'operation-data'}
    unexpected = [path.name for path in root.iterdir() if path.name not in keep]
    if unexpected:
        raise ValueError('受管状态存在未知残留，先由 Agent 核实处置：' + ', '.join(sorted(unexpected)))
    station_directories.sync(root)


def finish(base, operation, identity):
    """最后回收任务材料，只留下本次操作的最小重试回执。"""
    from workflow import execution
    if task_store.read_current(base)['current'] is not None:
        raise ValueError('任务尚未解除，不能结束清理')
    for key in operations.PAYLOAD_FIELDS:
        operation.pop(key, None)
    operation['phase'] = 'finalizing'
    operation['status'] = 'running'
    operation['cleanup_result'].update(released=True, complete=False, residuals=[])
    operations.save(base, operation)
    residuals = operations.collect_payloads(base) or []
    if residuals:
        operation['cleanup_result']['residuals'] = residuals
        operations.save(base, operation)
        return operation
    operation['cleanup_result']['complete'] = True
    operations.finish(base, operation)
    if not operation['cleanup_result']['complete']:
        operation.update(status='running', phase='finalizing')
        operations.save(base, operation)
        return operation
    execution.release(base, identity)
    return operation


def execute(base, issue, run_id, revision, operation_id, request, allow_product_lifecycle=False):
    """新清理唯一执行链；调用入口负责确认精确范围和本地唯一材料处置。"""
    from workflow import execution, station_clean, station_resources
    with task_store.task_state_lock(base, execution=False, allow_product_lifecycle=allow_product_lifecycle):
        old = operations.read(base)
        current = task_store.read_current(base)
        same = old and old['operation_id'] == operation_id
        if same:
            if (old['kind'] != 'clean' or old['request_digest'] != baseline.digest(request)
                    or old['expected_revision'] != revision or old['run_id'] != run_id):
                raise ValueError('恢复必须复用原清理操作和请求')
            if old['status'] == 'done':
                # 已完成的原操作只回读，不重新取得占用或清除后来接管者。
                if current['current'] is None and current.get('execution_control', {}).get('session_id') == old.get('cleanup_executor'):
                    execution.release(base, old['cleanup_executor'])
                return old
        else:
            if current['revision'] != revision:
                raise ValueError('工位 revision 已变化')
            task = current['current']
            if task and (task['issue_key'] != issue or task['run_id'] != run_id):
                raise ValueError('当前任务已变化')
        # 使用已有工位锁串行检查所有权；不通过嵌套写锁升级只读锁。
        identity = execution.enter(base)
        if same:
            operation = old
            if operation['phase'] == 'finalizing':
                operation['cleanup_executor'] = identity
                return finish(base, operation, identity)
            task = operation.get('exit_task')
            cleanup_plan = operation['cleanup_plan']
        else:
            cleanup_plan = plan(base, request.get('scope', 'task'))
            if cleanup_plan['digest'] != request.get('expected_plan_digest'):
                raise ValueError('预检范围已变化，重新展示并确认清理范围')
            if not isinstance(request.get('decision_ref'), str) or not request['decision_ref'].strip():
                raise ValueError('清理需要本次范围、停写和材料处置的用户决定来源')
            if request.get('materials') not in ('preserved', 'discard'):
                raise ValueError('先确认必要材料已可靠保全，或明确接受丢失')
            if cleanup_plan['source']['requires_decision'] and request['materials'] != 'discard':
                raise ValueError('源码尚有未保全或未核实内容；先保全后重检，或明确放弃')
            if old and old['status'] != 'done':
                operation = operations.handoff_clean(base, operation_id, revision, request, run_id, cleanup_plan)
            else:
                operation = operations.begin(base, 'clean', operation_id, revision, request, run_id)
            operation['cleanup_plan'] = cleanup_plan
            operation['exit_task'] = task
            operations.save(base, operation)
        operation['cleanup_executor'] = identity
        validate(cleanup_plan)
        result = {'released': False, 'complete': False, 'residuals': [], 'resources': []}
        operation['cleanup_result'] = result
        try:
            if rules.load(base)['digests'] != cleanup_plan['config']['digests']:
                raise ValueError('资源声明变化，先核实原操作归属，不能沿新规则扩大清理')
            result['resources'] = station_clean.stop_resources(base, task) if task and current['current'] else []
            result['resources'].extend(station_resources.cleanup_declared(base, cleanup_plan['config'], operation))
            if result['resources']:
                operation['phase'] = 'awaiting_resources'
                operations.save(base, operation)
                return operation
            for index, item in enumerate(cleanup_plan['targets']):
                key = 'tree:' + hashlib.sha256(item['path'].encode()).hexdigest()
                recorded = operation['steps'].get(key)
                if recorded:
                    effective = recorded['expected']
                elif index == 0:
                    effective = item  # 源码成果必须匹配用户已核实的现场。
                else:
                    path = trees.path_for(base, item['path'])
                    if path.is_dir() and not path.is_symlink():
                        preservation.assert_isolated(path)
                    effective = trees.entry(base, item['path'], item['recreate'])
                    before, after = item['snapshot'], effective['snapshot']
                    if after and (not before or before['identity'] != after['identity']):
                        raise ValueError('停写期间清理根被替换，保留并核实：' + item['path'])
                    # 资源停止可刷新日志/数据库字节；整区处置覆盖停写前的最终内容。
                trees.remove(base, operation, effective)
            trees.finish(base, operation)
            # 所有恢复材料在资源及用户目录核验之后处理；操作日志保留到最终回读。
            clear_state(base, operation)
            operations.intent(base, operation, 'unbind', {'run_id': run_id}, {'current': None})
            observed = task_store.read_current(base)
            if observed['current'] is not None:
                if observed['current']['run_id'] != run_id:
                    raise ValueError('工位已经有其它任务，拒绝解绑')
                task_store.compare_and_set(base, observed['revision'], None)
            operations.receipt(base, operation, 'unbind', {'current': None})
            return finish(base, operation, identity)
        except (ValueError, OSError) as error:
            result['residuals'].append({'reason': str(error)})
            result['complete'] = False
            operation['status'] = 'running'
            if operation['phase'] != 'finalizing':
                operation['phase'] = 'awaiting_cleanup'
            operations.save(base, operation)
        return operation


def cancel(base, operation_id, decision_ref):
    """源码尚未开始删除时允许取消原清理，修正材料后重新预检；不恢复已停止资源。"""
    if not isinstance(decision_ref, str) or not decision_ref.strip():
        raise ValueError('取消清理需要用户决定来源')
    with task_store.task_state_lock(base):
        operation = operations.read(base)
        if not operation or operation['operation_id'] != operation_id or operation['kind'] != 'clean':
            raise ValueError('没有匹配的清理操作')
        if operation['status'] == 'done':
            if operation.get('cancelled'):
                return operation
            raise ValueError('已完成清理不能取消')
        if any(name.startswith('tree:') or name == 'unbind' for name in operation['steps']):
            raise ValueError('数据删除已经开始，只能沿原操作核实并继续')
        if any(step['receipt'] is None for step in operation['steps'].values()):
            raise ValueError('资源动作尚未回读，先处理原操作，不能取消未知结果')
        operation['cancelled'] = {'decision_ref': decision_ref, 'boundary': '保留任务，已停止的资源不会自动启动'}
        operation['cleanup_result'] = {'released': False, 'complete': False, 'residuals': [], 'resources': []}
        operations.finish(base, operation)
        return operation
