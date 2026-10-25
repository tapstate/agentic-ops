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
from workflow import engineering_baseline as baseline, station_archive as archives, station_clean_rules as rules
from workflow import station_context, station_operation as operations
from workflow import station_resources as resources, task_store


def check(base, task=None, request=None):
    from workflow import station_cleanup
    plan = station_cleanup.plan(base, (request or {}).get('scope', 'task'))
    return {'status': 'preview', 'cleanup_plan': plan}


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


def _handoff_archive(base, task, operation, candidate=None, jira_readback=None):
    from workflow import task_handoff
    if operation.get('archive_payload', {}).get('snapshot', {}).get('handoff') is None:
        if operation.get('handoff_readback'):
            raise ValueError('已有 Jira 回读但接续正文缺失，先核对原操作，不能重新生成')
        if candidate is None:
            candidate = task_handoff.prepare(base, task, operation['request']['handoff'])
        payload = archives.prepare(base, task, operation['request'], operation, handoff=candidate)
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
    except (OSError, ValueError) as error:
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
    if kind == 'clean':
        if cleanup_mode != 'execute' or archive_decision or jira_readback:
            raise ValueError('清理沿原操作执行和核验，归档应独立处理')
        from workflow import station_cleanup
        return station_cleanup.execute(base, issue, run_id, revision, operation_id, request, allow_product_lifecycle)
    if kind != 'archive':
        raise ValueError('生命周期清理已收敛为 clean 的 task/station 两个范围；归档独立执行')
    if 'handoff' not in request:
        raise ValueError('归档需要 Jira 持久接续材料 handoff')
    if archive_decision:
        raise ValueError('持久归档使用 Jira 回读；明确未发送才可沿 not_sent 取消')
    from workflow import task_handoff
    with task_store.task_state_lock(base, allow_product_lifecycle=allow_product_lifecycle):
        old = operations.read(base)
        same = old and old['operation_id'] == operation_id
        candidate = None
        if same:
            if (old['kind'] != kind or old['request_digest'] != baseline.digest(request)
                    or old['expected_revision'] != revision or old['run_id'] != run_id):
                raise ValueError('恢复必须复用原操作和请求')
            operation = old
            task = task_store.read_task(base) or operation.get('exit_task')
            if operation['status'] == 'done':
                if jira_readback is not None:
                    receipt = task_handoff.verify_readback(operation['archive_payload']['snapshot']['handoff'], jira_readback)
                    if receipt != operation.get('handoff_readback'):
                        raise ValueError('已完成归档不能替换原 Jira 回读')
                return operation
        else:
            task = task_store.check_expected_run(base, issue, run_id)
            candidate = task_handoff.prepare(base, task, request['handoff'])
            operation = operations.begin(base, kind, operation_id, revision, request, run_id)
            operation['exit_task'] = task
            operations.save(base, operation)
        return _handoff_archive(base, task, operation, candidate, jira_readback)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', default='.')
    parser.add_argument('--action', choices=('clean', 'archive'), default='clean')
    parser.add_argument('--scope', choices=('task', 'station'), default='task')
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--operation-id')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--cancel', action='store_true', help='仅取消尚未开始删除且无未知资源动作的原清理')
    parser.add_argument('--expected-plan-digest')
    parser.add_argument('--decision-ref')
    parser.add_argument('--materials', choices=('preserved', 'discard'))
    parser.add_argument('--handoff-input')
    parser.add_argument('--jira-readback')
    parser.add_argument('--details', action='store_true')
    parser.add_argument('--output', help='完整结果输出到工位外的新文件')
    args = parser.parse_args(argv)
    from workflow.station_clean_view import emit
    from workflow import station_cleanup, project_rules
    from bootstrap.station_compatibility import require_station_can_adopt
    require_station_can_adopt(project_rules.product_root_from_station(args.dir), args.dir)
    if args.cancel:
        if not args.operation_id or args.execute or args.resume or args.check:
            raise ValueError('取消须单独指定 --operation-id 和 --decision-ref')
        emit(station_cleanup.cancel(args.dir, args.operation_id, args.decision_ref), args.details, args.output, args.dir)
        return 0
    task = task_store.read_task(args.dir)
    operation = operations.read(args.dir)
    if not args.execute and not args.resume or args.check:
        with task_store.task_state_lock(args.dir, execution=False):
            if args.action == 'archive':
                from workflow import task_handoff
                if task is None or not args.handoff_input:
                    raise ValueError('归档预检需要当前任务和 --handoff-input')
                preview = {'status': 'preview', 'handoff': task_handoff.prepare(args.dir, task, json.loads(Path(args.handoff_input).read_text()))}
            else:
                preview = {'status': 'preview', 'cleanup_plan': station_cleanup.plan(args.dir, args.scope)}
        emit(preview, args.details, args.output, args.dir)
        return 0
    if args.resume:
        if not operation or args.operation_id and args.operation_id != operation['operation_id']:
            raise ValueError('没有匹配的原操作')
        kind, request, op, revision, run_id = (operation[key] for key in ('kind', 'request', 'operation_id', 'expected_revision', 'run_id'))
        issue = (task or operation.get('exit_task') or {}).get('issue_key')
    else:
        kind = args.action
        request = {'scope': args.scope, 'expected_plan_digest': args.expected_plan_digest,
                   'decision_ref': args.decision_ref, 'materials': args.materials}
        if kind == 'archive':
            if not task or not args.handoff_input:
                raise ValueError('归档需要当前任务和 --handoff-input')
            request = {'handoff': json.loads(Path(args.handoff_input).read_text())}
        op = args.operation_id or 'op-' + uuid.uuid4().hex
        revision = task_store.read_current(args.dir)['revision']
        run_id = task['run_id'] if task else operation['run_id'] if operation and operation['status'] != 'done' else 'run-' + station_context.read_binding(args.dir)['station_id']
        issue = task['issue_key'] if task else None
    result = execute(args.dir, kind, issue, run_id, revision, op, request,
                     jira_readback=json.loads(Path(args.jira_readback).read_text()) if args.jira_readback else None)
    emit({'operation_id': result['operation_id'], 'status': result['status'],
          'cleanup': result.get('cleanup_result'), 'archive': result.get('archive_result')}, args.details, args.output, args.dir)
    return 0 if result['status'] == 'done' else 2
