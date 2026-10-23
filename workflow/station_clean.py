"""统一清理脚本：正常路径自动执行，残留和异常交给 Agent 接力。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid
from workflow import archive_store, engineering_baseline as baseline, station_archive as archives, station_clean_rules as rules
from workflow import station_context, station_operation as operations, station_reset_result as results
from workflow import station_resources as resources, task_store


def check(base, task=None, request=None, operation=None, replacements=None):
    plan = rules.plan(base, task, request)
    return {'status': 'preview', 'cleanup_plan': plan, 'residuals': plan['residuals'], 'retained': plan['retained']}


def stop_resources(base, task, observe_only=False):
    pending = []
    for item in resources.inventory(base, task):
        if item.get('resource_type') in ('git-branch', 'pull-request'):
            continue
        try:
            if item.get('kind') == 'process':
                pid = item.get('pid')
                if type(pid) is not int or pid <= 1 or pid == os.getpid() or not item.get('started_at'):
                    raise ValueError('进程身份无法确认')
                def alive():
                    p = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True, timeout=5)
                    if p.returncode not in (0, 1):
                        raise ValueError('进程状态无法读取')
                    return p.stdout.strip() == item['started_at']
                if alive():
                    if observe_only:
                        raise ValueError('登记进程仍在运行')
                    cwd = Path(item.get('cwd', '')).resolve()
                    if Path(base).resolve() not in cwd.parents:
                        raise ValueError('进程工作目录不属于本工位')
                    observed = subprocess.run(['ps', '-p', str(pid), '-o', 'comm='], capture_output=True, text=True, timeout=5)
                    if observed.returncode or os.path.realpath(observed.stdout.strip()) != os.path.realpath(item.get('executable', '')):
                        raise ValueError('进程程序身份已变化')
                    if not alive():
                        continue
                    os.kill(pid, signal.SIGTERM)
                    until = time.monotonic() + 3
                    while alive() and time.monotonic() < until:
                        time.sleep(.05)
                    if alive():
                        raise ValueError('进程未停止，未强杀')
            elif item.get('kind') == 'external' and (item.get('status') not in ('cleaned', 'retained') or not item.get('readback_ref') or item.get('status') == 'retained' and not item.get('isolation_proof')):
                raise ValueError('外部资源需原生工具按原操作回读并收尾')
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            pending.append({'resource': item, 'reason': str(error)})
    return pending


def _depends(base, entry, pending):
    resource = pending.get('resource', {})
    scope = resource.get('path')
    if scope:
        return entry['path'] == scope or entry['path'].startswith(scope + '/') or scope.startswith(entry['path'] + '/')
    cwd = resource.get('cwd')
    if cwd:
        root = Path(base).resolve()
        candidate = root / entry['path']
        working = Path(cwd).resolve()
        return working == candidate or working in candidate.parents or candidate in working.parents
    # 无可核验范围时保留运行区，仍可处理独立配置；未知进程保守保留源码输出。
    return entry['path'].startswith('runtime/') or bool(entry.get('repository'))


def _archive(base, task, operation, checkpoint, decision=None):
    if operation.get('archive_checkpoint') != checkpoint:
        if decision:
            raise ValueError('归档决定不能用于另一个快照')
        operation['archive_payload'] = archives.prepare(base, task, operation['request'], operation)
        operation['archive_checkpoint'] = checkpoint
        operation.pop('archive_result', None)
        operations.save(base, operation)
    digest = baseline.digest(operation['archive_payload'])
    if decision:
        if (not isinstance(decision, dict) or set(decision) != {'action', 'digest'}
                or decision['action'] not in ('retry', 'discard') or decision['digest'] != digest
                or operation.get('archive_result', {}).get('status') != 'awaiting_decision'):
            raise ValueError('归档决定必须匹配当前待处理快照')
        # 用户选择不能把已写成功或读取不明的结果当作丢弃。
        saved = archives.observe(base, operation['archive_payload'])
        decisions = operation.setdefault('archive_decisions', [])
        if decisions and decisions[-1].get('state') == 'authorized' and any(decisions[-1].get(key) != decision[key] for key in ('action', 'digest')):
            raise ValueError('已有未完成归档决定，先恢复该决定')
        if not decisions or decisions[-1].get('state') != 'authorized':
            decisions.append(dict(decision, checkpoint=checkpoint, state='authorized'))
        operations.save(base, operation)
        if saved:
            return {'status': 'saved', 'reference': archive_store.reference(task['run_id'], baseline.digest(operation['archive_payload']['record']))}
        if decision['action'] == 'discard':
            return {'status': 'discarded', 'digest': digest, 'checkpoint': checkpoint,
                    'authorization': 'explicit_user_decision'}
    try:
        reference = archives.publish(base, task, operation['request'], operation=operation)
        operation['archive_ref'] = reference
        return {'status': 'saved', 'reference': reference}
    except OSError as error:
        return {'status': 'awaiting_decision', 'digest': digest, 'checkpoint': checkpoint,
                'reason': type(error).__name__ + ': ' + str(error),
                'path': str(archive_store.run_directory(base, task['run_id'])),
                'guidance': '先回读写入结果；由用户授权修复后重试或明确放弃此快照，禁止自动丢弃'}


def _archive_checkpoint(base, task, operation, checkpoint, decision=None):
    result = operation.get('archive_result', {})
    previous_decision = (operation.get('archive_decisions') or [{}])[-1]
    if not decision and previous_decision.get('state') == 'authorized' and previous_decision.get('checkpoint') == checkpoint:
        decision = {key: previous_decision[key] for key in ('action', 'digest')}
    if operation.get('archive_checkpoint') == checkpoint and result.get('status') == 'awaiting_decision' and not decision:
        return False
    if operation.get('archive_checkpoint') == checkpoint and result.get('status') in ('saved', 'discarded'):
        if decision:
            raise ValueError('此归档快照已处理，不能重复授权')
        return True
    operation['archive_result'] = _archive(base, task, operation, checkpoint, decision)
    if decision:
        operation['archive_decisions'][-1]['state'] = 'handled'
    if operation['archive_result']['status'] == 'awaiting_decision':
        operation['phase'] = 'awaiting_archive_decision'
        operations.save(base, operation)
        return False
    operations.save(base, operation)
    return True


def _handoff_archive(base, task, operation, candidate=None, jira_readback=None):
    from workflow import task_handoff
    if 'archive_payload' not in operation:
        payload = archives.prepare(base, task, operation['request'], operation)
        payload['snapshot'] = {'handoff': candidate}
        operation['archive_payload'] = payload
        operation['phase'] = 'awaiting_jira'
        operations.save(base, operation)
    candidate = operation['archive_payload']['snapshot']['handoff']
    if jira_readback is not None:
        receipt = task_handoff.verify_readback(candidate, jira_readback)
        old = operation.get('handoff_readback')
        if old and old != receipt:
            raise ValueError('已有不同 Jira 回读，先核对原操作，不能替换')
        operation['handoff_readback'] = receipt
        operations.save(base, operation)
    if not operation.get('handoff_readback'):
        operation['archive_result'] = {
            'status': 'awaiting_jira', 'content_digest': candidate['digest'],
            'site': candidate['site'], 'issue_key': task['issue_key'], 'body': candidate['body'],
            'guidance': '先查找并回读相同接续点；未写入时按原授权用原生 Jira 工具保存。结果未知先回读，再沿原 operation 提交 --jira-readback。'}
        operations.save(base, operation)
        return operation
    receipt = operation['handoff_readback']
    if receipt.get('status') == 'not_sent':
        operation['archive_result'] = {'status': 'discarded', 'reason': '明确未发送，取消本次准备；旧接续点保持有效',
                                       'content_digest': candidate['digest'], 'decision_ref': receipt['decision_ref']}
        operations.finish(base, operation)
        return operation
    operation['archive_payload']['snapshot']['jira'] = {
        key: receipt[key] for key in ('site', 'issue_key', 'comment_id')}
    result = {'status': 'saved', 'storage': 'jira', 'content_digest': candidate['digest'],
              'jira': receipt, 'snapshot_digest': baseline.digest(operation['archive_payload']['snapshot']),
              'guidance': '仅对应已回读正文中的成果；后续新增修改仍须保存或明确放弃。'}
    try:
        result['local_reference'] = archives.publish(base, task, operation['request'], operation=operation)
    except OSError as error:
        result['local_warning'] = type(error).__name__ + '：本地副本未保存，Jira 接续点已核实；新工位不依赖本地档案'
    operation['archive_result'] = result
    operations.finish(base, operation)
    return operation


def _clear_active(base, operation):
    """受管活动状态移除不依赖档案；错误保持可恢复，不伪造解绑。"""
    from workflow import station_directories as directories
    root = task_store.state_path(base)
    for name in ('authorization.json', 'evidence'):
        if name == 'evidence' and any(not row.get('resource') for row in operation.get('cleanup_result', {}).get('resources', [])):
            continue  # 无法解释的资源登记原件留给后续接手，不随解绑丢失。
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink():
            raise ValueError('活动状态为链接，拒绝清除：' + name)
        if path.is_dir():
            fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                directories._walk(fd, os.fstat(fd).st_dev)
                directories._walk(fd, os.fstat(fd).st_dev, delete=True)
                os.fsync(fd)
            finally:
                os.close(fd)
            path.rmdir()
        elif path.is_file():
            path.unlink()
        else:
            raise ValueError('活动状态包含特殊对象')
    directories.sync(root)


def execute(base, kind, issue, run_id, revision, operation_id, request, cleanup_mode='execute', allow_product_lifecycle=False, archive_decision=None, jira_readback=None):
    if kind not in ('clean', 'release', 'archive', 'work-clean') or cleanup_mode not in ('execute', 'prepare', 'verify'):
        raise ValueError('清理操作或模式无效')
    if request.get('cleanup_version', 7) != 7:
        raise ValueError('旧清理请求必须由原版本退出')
    with task_store.task_state_lock(base, allow_product_lifecycle=allow_product_lifecycle):
        old = operations.read(base)
        current = task_store.read_current(base)
        task = task_store.read_task(base)
        same = old and old['operation_id'] == operation_id
        candidate = None
        if archive_decision and 'handoff' in request:
            raise ValueError('持久归档请提交 Jira 回读；明确未发送时使用 not_sent 取消，不能使用本地归档决定')
        if jira_readback is not None and (kind != 'archive' or 'handoff' not in request):
            raise ValueError('Jira 接续回读只适用于持久归档')
        if kind == 'archive' and 'handoff' in request and not same:
            from workflow import task_handoff
            task = task_store.check_expected_run(base, issue, run_id)
            candidate = task_handoff.prepare(base, task, request['handoff'])
        if archive_decision and (not same or kind not in ('archive', 'release')):
            raise ValueError('归档决定只能恢复原归档或 release 操作')
        if same:
            if (old['kind'] != kind or old['request_digest'] != baseline.digest(request)
                    or old['expected_revision'] != revision or old['run_id'] != run_id):
                raise ValueError('恢复必须复用原操作和请求')
            operation = old
            task = task or operation.get('exit_task')
            if operation['status'] == 'done' and cleanup_mode != 'verify':
                if jira_readback is not None:
                    from workflow import task_handoff
                    receipt = task_handoff.verify_readback(operation['archive_payload']['snapshot']['handoff'], jira_readback)
                    if receipt != operation.get('handoff_readback'):
                        raise ValueError('已完成归档不能替换原 Jira 回读')
                return operation
        else:
            if kind != 'work-clean':
                task = task_store.check_expected_run(base, issue, run_id)
            if task and task['run_id'] != run_id:
                raise ValueError('当前任务已变化')
            if current['revision'] != revision:
                raise ValueError('当前 revision 已变化')
            handoff = (old or {}).get('handoff', {})
            plan = handoff.get('cleanup_plan') if handoff.get('operation_id') == operation_id else rules.plan(base, task, request) if kind != 'archive' else None
            if old and old['kind'] in ('takeover', 'scope_change', 'replan', 'archive') and old['status'] != 'done' and task and kind in ('clean', 'release'):
                operation = operations.handoff_clean(base, operation_id, revision, request, run_id, plan, kind)
                operation['kind'] = kind
                operations.save(base, operation)
            else:
                operation = operations.begin(base, kind, operation_id, revision, request, run_id)
            if plan:
                operation['cleanup_plan'] = plan
                operation['protected_paths'] = plan['protected_paths']
            operation['exit_task'] = task
            operations.save(base, operation)
        if kind == 'archive':
            if 'handoff' in request:
                return _handoff_archive(base, task, operation, candidate, jira_readback)
            if not _archive_checkpoint(base, task, operation, 'archive', archive_decision):
                return operation
            operations.finish(base, operation)
            return operation
        if 'cleanup_plan' not in operation:
            operation['cleanup_plan'] = rules.plan(base, task, request)
            operation['exit_task'] = task
            operation['protected_paths'] = operation['cleanup_plan']['protected_paths']
            operations.save(base, operation)
        plan = operation['cleanup_plan']
        rules.validate_snapshot(plan)
        if kind == 'release':
            if operation.get('archive_checkpoint') == 'after-cleanup':
                if not _archive_checkpoint(base, task, operation, 'after-cleanup', archive_decision):
                    return operation
                operations.finish(base, operation)
                return operation
            if not _archive_checkpoint(base, task, operation, 'before-cleanup', archive_decision):
                return operation
        if cleanup_mode == 'prepare':
            operation['phase'] = 'awaiting_cleanup_result'
            operations.save(base, operation)
            return operation
        config_changed = rules.load(base)['digests'] != plan['config']['digests']
        preserved_now = set(plan.get('protected_paths', []))
        if current['current'] is not None or kind == 'work-clean':
            try:
                pending = stop_resources(base, task, observe_only=cleanup_mode == 'verify' or config_changed) if task else []
                if task:
                    preserved_now.update(row['path'] for row in resources.inventory(base, task) if row.get('kind') == 'source-disposition' and row.get('preservation', {}).get('action') in ('archive', 'export'))
            except (ValueError, OSError) as error:
                pending = [{'resource': {}, 'reason': '资源登记无法读取：' + str(error)}]
            pending.extend(resources.cleanup_declared(base, plan['config'], operation, observe_only=cleanup_mode == 'verify' or config_changed))
        else:
            pending = resources.observe_retained_resources(base, operation.get('cleanup_result', {}).get('resources', []))
        leftovers = [row for row in plan['residuals'] if (Path(base) / row['path']).exists() or (Path(base) / row['path']).is_symlink()]
        completed = []
        for entry in plan['objects']:
            key = 'object:' + baseline.digest(entry)
            step = operation['steps'].get(key)
            try:
                if step and step['receipt'] is not None:
                    results.verify_object(base, entry)
                elif config_changed:
                    raise ValueError('清单已变化，原操作不按旧规则删除')
                elif any(entry['path'] == p or entry['path'].startswith(p + '/') or p.startswith(entry['path'] + '/') for p in preserved_now):
                    raise ValueError('对象已明确保全，保留')
                elif any(_depends(base, entry, row) for row in pending):
                    raise ValueError('相关写入资源未确认停止，保留对象')
                else:
                    operations.intent(base, operation, key, entry, {'path': entry['path'], 'absent': True})
                    result = results.verify_object(base, entry) if cleanup_mode == 'verify' else results.remove(base, entry)
                    operations.receipt(base, operation, key, result)
                completed.append(entry['path'])
            except (ValueError, OSError) as error:
                leftovers.append({'path': entry['path'], 'reason': str(error)})
                # 失败对象仍留在结果中，不能冒充 removed；结束本次尝试不阻塞其他对象。
                if key in operation['steps'] and operation['steps'][key]['receipt'] is None:
                    operations.receipt(base, operation, key, {'status': 'retained', 'reason': str(error)})
        # 最终只读盘点，晚产生内容交给用户，不沿新清单继续删除。
        try:
            observed = rules.plan(base, task, request)
            known = {row.get('path') for row in leftovers}
            for entry in observed['objects']:
                if entry['path'] not in known:
                    leftovers.append({'path': entry['path'], 'reason': '最终复查仍存在，不重复删除'})
            for entry in observed['residuals']:
                if entry['path'] not in known:
                    leftovers.append(entry)
        except (ValueError, OSError) as error:
            leftovers.append({'path': '.', 'reason': '复查未完成：' + str(error)})
        operation['protected_paths'] = sorted(preserved_now)
        operation['cleanup_result'] = {'released': False, 'complete': not leftovers and not pending,
                                       'completed': completed, 'retained': plan['retained'],
                                       'residuals': leftovers, 'resources': pending}
        operations.save(base, operation)
        if kind != 'work-clean':
            operations.intent(base, operation, 'unbind', {'run_id': run_id}, {'current': None})
            if current['current'] is not None:
                if current['current']['run_id'] != run_id:
                    raise ValueError('工位已被其它任务占用')
                _clear_active(base, operation)
                task_store.compare_and_set(base, current['revision'], None)
            if task_store.read_current(base)['current'] is not None:
                raise ValueError('任务解绑未完成')
            operations.receipt(base, operation, 'unbind', {'current': None})
            operation['cleanup_result']['released'] = True
        if kind == 'release':
            if not _archive_checkpoint(base, task, operation, 'after-cleanup'):
                return operation
        operations.finish(base, operation)
        return operation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', default='.')
    parser.add_argument('--action', choices=('work-clean', 'clean', 'release', 'archive'), default='release')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--clear-config', action='append', default=[])
    parser.add_argument('--operation-id')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--handoff-input', help='Agent 整理的持久接续材料 JSON')
    parser.add_argument('--jira-readback', help='原生 Jira 回读 JSON，仅核验不发送')
    parser.add_argument('--archive-decision', choices=('retry', 'discard'))
    parser.add_argument('--expected-archive-digest')
    parser.add_argument('--verify-result', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--details', action='store_true')
    parser.add_argument('--output', help='完整结果输出到工位外的新文件')
    args = parser.parse_args(argv)
    if bool(args.archive_decision) != bool(args.expected_archive_digest) or args.archive_decision and not args.resume:
        raise ValueError('归档决定必须带 --resume 和 --expected-archive-digest')
    from workflow.station_clean_view import emit
    from bootstrap.station_compatibility import require_station_can_adopt
    from workflow import project_rules
    require_station_can_adopt(project_rules.product_root_from_station(args.dir), args.dir)
    task = task_store.read_task(args.dir)
    operation = operations.read(args.dir)
    if not args.execute and not args.resume or args.check:
        if args.action == 'archive':
            from workflow import task_handoff
            if task is None or not args.handoff_input:
                raise ValueError('归档预检需要当前任务和 --handoff-input')
            with task_store.task_state_lock(args.dir):
                task = task_store.read_task(args.dir)
                preview = task_handoff.prepare(args.dir, task, json.loads(Path(args.handoff_input).read_text()))
            emit({'status': 'preview', 'handoff': preview, 'boundary': 'Git 远端只读核验；尚未写入 Jira'}, args.details, args.output, args.dir)
            return 0
        emit(check(args.dir, task, {'clear_config': args.clear_config}), args.details, args.output, args.dir)
        return 0
    if args.resume:
        if not operation or args.operation_id and args.operation_id != operation['operation_id']:
            raise ValueError('没有匹配的原操作')
        kind, request, op, revision, run_id = (operation[k] for k in ('kind', 'request', 'operation_id', 'expected_revision', 'run_id'))
        issue = (task or operation.get('exit_task') or {}).get('issue_key')
    else:
        kind, request = args.action, {'clear_config': args.clear_config}
        if kind == 'archive':
            if not args.handoff_input:
                raise ValueError('任务归档需 --handoff-input；计划/进度由 Agent 整理，代码由程序核验')
            request['handoff'] = json.loads(Path(args.handoff_input).read_text())
        if not task and kind != 'work-clean':
            print(json.dumps({'status': 'idle', 'released': True}, ensure_ascii=False)); return 0
        op = args.operation_id or 'op-' + uuid.uuid4().hex
        revision = task_store.read_current(args.dir)['revision']
        run_id = task['run_id'] if task else 'run-' + station_context.read_binding(args.dir)['station_id']
        issue = task['issue_key'] if task else None
    result = execute(args.dir, kind, issue, run_id, revision, op, request,
                     'verify' if args.verify_result else 'prepare' if args.prepare_only else 'execute',
                     archive_decision={'action': args.archive_decision, 'digest': args.expected_archive_digest} if args.archive_decision else None,
                     jira_readback=json.loads(Path(args.jira_readback).read_text()) if args.jira_readback else None)
    emit({'operation_id': result['operation_id'], 'status': result['status'],
          'cleanup': result.get('cleanup_result'), 'archive': result.get('archive_result')}, args.details, args.output, args.dir)
    return 0
