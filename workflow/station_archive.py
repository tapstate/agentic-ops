"""尽力记录当前任务结果；无材料准入、源码复制或生命周期副作用。"""
from __future__ import annotations
import json
from pathlib import Path
from workflow import archive_store, engineering_baseline as baseline, project_rules, station_context, task_store


def redact_evidence(rules, value, key=''):
    if any(word in key.lower() for word in ('password', 'secret', 'token', 'credential', 'private_key')):
        return '[redacted]'
    if isinstance(value, dict):
        return {name: redact_evidence(rules, item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [redact_evidence(rules, item) for item in value]
    if isinstance(value, str) and project_rules.scan_sensitive(rules, value):
        return '[redacted]'
    return value


def prepare(base, task, request, operation=None):
    """冻结已有材料；重试不得用清理后的现场替换失败快照。"""
    rules = project_rules.load_admission(station=base)
    record = {'schema_version': 2, 'run_id': task['run_id'], 'issue_key': task['issue_key'],
              'station_id': station_context.read_binding(base)['station_id']}
    snapshot = {'operation_id': (operation or {}).get('operation_id'),
                'task': {key: value for key, value in task.items() if key not in ('_revision', 'repositories')},
                'summary': request.get('summary', ''), 'reason': request.get('reason', ''),
                'cleanup_result': (operation or {}).get('cleanup_result'),
                'coverage': 'available_state', 'gaps': ['仅记录已有状态和引用；未收集平台会话或源码正文']}
    # 可直接取得的小型状态材料尽力记录；单项缺失/损坏不使其它记录丢失。
    evidence = task_store.state_path(base) / 'evidence'
    snapshot['evidence'] = {}
    total = 0
    if evidence.is_dir() and not evidence.is_symlink():
        for item in sorted(evidence.iterdir()):
            if item.is_symlink() or not item.is_file() or item.suffix != '.json':
                snapshot['gaps'].append('未采集：' + item.name)
                continue
            try:
                size = item.stat().st_size
                if size > 1024 * 1024 or total + size > 8 * 1024 * 1024:
                    snapshot['gaps'].append('超出状态采集容量：' + item.name)
                    continue
                snapshot['evidence'][item.name] = json.loads(item.read_text())
                total += size
            except (ValueError, OSError) as error:
                snapshot['gaps'].append(item.name + '：' + type(error).__name__)
    snapshot = redact_evidence(rules, snapshot)
    # 不主动读取报告、配置或源码；超限作为归档警告，不强制采集或截断。
    if len(json.dumps(snapshot, ensure_ascii=False).encode()) > 16 * 1024 * 1024:
        raise ValueError('已有状态超出归档上限')
    return {'record': record, 'snapshot': snapshot}


def observe(base, payload):
    """只回读确切快照；缺失可确认，读取失败或身份冲突不能视为缺失。"""
    record, snapshot = payload['record'], payload['snapshot']
    target = archive_store.run_directory(base, record['run_id'])
    if target.is_symlink():
        raise ValueError('档案目录不能为链接')
    record_path = target / 'record.json'
    path = target / ('result-' + baseline.digest(snapshot) + '.json')
    for item, expected in ((record_path, record), (path, snapshot)):
        if item.is_symlink():
            raise ValueError('档案文件不能为链接')
        try:
            value = json.loads(item.read_text())
        except FileNotFoundError:
            continue
        if value != expected:
            raise ValueError('已有档案身份或快照冲突，保留原档案')
    return record_path.exists() and path.exists()


def publish(base, task, request, inventory=None, operation=None, check_stable=None):
    """发布冻结快照；失败由原操作交接用户决策。"""
    payload = (operation or {}).get('archive_payload') or prepare(base, task, request, operation)
    record, snapshot = payload['record'], payload['snapshot']
    if record['run_id'] != task['run_id'] or record['issue_key'] != task['issue_key']:
        raise ValueError('冻结归档 run 不匹配')
    target = archive_store.run_directory(base, task['run_id'], create_root=True)
    observe(base, payload)
    target.mkdir(mode=0o700, exist_ok=True)
    record_path = target / 'record.json'
    if not record_path.exists():
        task_store._write_json_atomic(record_path, record)
    path = target / ('result-' + baseline.digest(snapshot) + '.json')
    if path.is_symlink():
        raise ValueError('档案快照不能为链接')
    if path.exists():
        if json.loads(path.read_text()) != snapshot:
            raise ValueError('档案快照冲突')
    else:
        task_store._write_json_atomic(path, snapshot)
    return archive_store.reference(task['run_id'], baseline.digest(record))


def verify(base, reference, task):
    target = archive_store.from_reference(base, reference)
    path = target / 'record.json'
    if target.is_symlink() or path.is_symlink():
        raise ValueError('档案不能为链接')
    record = json.loads(path.read_text())
    if (baseline.digest(record) != reference['digest'] or record['run_id'] != task['run_id']
            or record['issue_key'] != task['issue_key']):
        raise ValueError('档案身份不符')
    return record


def receipt(base, task, operation, result, name='result'):
    return publish(base, task, {'summary': name}, operation=dict(operation, cleanup_result=result))
