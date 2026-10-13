"""当前 run 的退出输入；复用工位锁、evidence 和原清理修订。"""
import hashlib
import json
import os
import stat
from pathlib import Path

from workflow import engineering_baseline as baseline, station_records, task_store
from workflow import station_operation, station_archive, project_rules, station_context


def require_input_state(base, task, operation_id=None, revision=None, plan_digest=None, amendment=False, input_digest=None):
    from workflow import archive_store
    operation = station_operation.read(base)
    target = archive_store.run_directory(base, task['run_id'])
    if task.get('archive_ref') or target.exists() or target.is_symlink():
        raise ValueError('正式档案已发布或待核验，禁止补写会话正文')
    pending = operation and operation['status'] != 'done'
    if pending:
        if (operation['kind'] not in ('archive', 'clean', 'release') or operation['run_id'] != task['run_id']
                or operation['operation_id'] != operation_id or revision != task['_revision']
                or 'unbind' in operation['steps']):
            raise ValueError('退出输入必须绑定原 archive/clean/release operation 和当前 revision')
        if plan_digest != (operation.get('cleanup_plan') or {}).get('digest'):
            previous = operation.get('last_amendment') or {}
            if not (amendment and previous.get('expected_plan_digest') == plan_digest
                    and previous.get('expected_revision') == revision
                    and previous.get('request', {}).get('conversation_input_digest') == input_digest):
                raise ValueError('退出输入原计划绑定不匹配')
    elif operation_id is not None:
        raise ValueError('没有匹配的未完成退出操作')
    frozen = operation and (operation.get('archive_record') or operation.get('archive_evidence')
        or any(k.startswith('archive-publish:') for k in operation['steps']))
    if frozen and not amendment:
        raise ValueError('归档草稿已冻结，须沿 cleanup-amend 持锁修订')
    return operation if pending else None


def interaction(base, issue, run, name, operation_id=None, revision=None, plan_digest=None):
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run)
        require_input_state(base, task, operation_id, revision, plan_digest)
        return task_store.interaction_path(base, issue, run, name, create=True)


def read_regular(path, max_bytes=None):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('输入必须使用绝对路径')
    if path != path.resolve() or path.is_symlink():
        raise ValueError('输入路径不能包含链接或非规范路径')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('输入必须是普通文件')
        if max_bytes is not None and before.st_size > max_bytes:
            raise ValueError('退出输入超过对应内容类型的容量上限')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read()
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('输入读取期间发生变化')
        return raw
    finally:
        os.close(fd)


def prepare(base, task, manifest_path, *, amendment=False):
    station = Path(base).resolve()
    path = Path(manifest_path)
    if station == path.resolve() or station in path.resolve().parents:
        raise ValueError('退出输入清单必须位于工位外')
    raw = read_regular(path, station_records.MAX_TOTAL_BYTES)
    manifest = json.loads(raw, object_pairs_hook=station_context._unique_object)
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1
            or (manifest.get('issue_key'), manifest.get('run_id')) != (task['issue_key'], task['run_id'])
            or not isinstance(manifest.get('files'), list) or not manifest['files']):
        raise ValueError('退出输入清单版本、身份或文件列表无效')
    operation = require_input_state(base, task, manifest.get('operation_id'), manifest.get('revision'),
                                    manifest.get('plan_digest'), amendment, hashlib.sha256(raw).hexdigest())
    interaction_root = task_store.prepare_interaction_directory(base, task['issue_key'], task['run_id'], require=False)
    rules = project_rules.load_admission(station=base)
    replacements, originals, seen = {}, {}, set()
    directory = task_store.state_path(base) / 'evidence'
    if directory.is_symlink():
        raise ValueError('证据目录不能是链接')
    for item in manifest['files']:
        if not isinstance(item, dict) or item.get('kind') not in ('conversation', 'conversation-scope'):
            raise ValueError('退出输入只能录入会话或来源范围')
        source = Path(item.get('path', ''))
        if station == source.resolve() or station in source.resolve().parents:
            if source.parent != interaction_root:
                raise ValueError('工位内输入只能使用当前 run 的 interaction 普通文件')
        data = read_regular(source, station_records.MAX_FILE_BYTES if item['kind'] == 'conversation' else station_records.MAX_TOTAL_BYTES)
        if (type(item.get('size')) is not int or item['size'] != len(data)
                or item.get('sha256') != hashlib.sha256(data).hexdigest()):
            raise ValueError('退出输入文件大小或摘要不匹配')
        if item['kind'] == 'conversation' and len(data) > station_records.MAX_FILE_BYTES:
            raise ValueError('任务记录超过归档容量上限')
        value = json.loads(data, object_pairs_hook=station_context._unique_object)
        if not isinstance(value, dict) or value.get('kind') != item['kind']:
            raise ValueError('退出输入声明与正文类型不一致')
        value = station_archive.redact_evidence(rules, value)
        if item['kind'] == 'conversation-scope':
            station_records.validate_scope(value, task)
            name = 'conversation-scope.json'
        else:
            identity = [value.get('platform'), value.get('thread_id')]
            name = 'conversation-' + baseline.digest(identity)[:32] + '-%s.json' % value.get('part', 1)
        if name in seen:
            raise ValueError('退出输入来源或分块重复')
        seen.add(name)
        encoded = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        destination = directory / name
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise ValueError('退出输入目标不是普通文件')
        previous = destination.read_bytes() if destination.exists() else None
        if previous is not None and previous != encoded and item.get('replace_sha256') != hashlib.sha256(previous).hexdigest():
            raise ValueError('替换退出输入必须绑定原正文摘要')
        replacements[name] = encoded
        originals[name] = previous
    inputs = station_records.read_documents(base, task, replacements)
    coverage = station_records.conversations(base, task, inputs)
    return {'files': replacements, 'originals': originals, 'coverage': coverage,
            'manifest_digest': hashlib.sha256(raw).hexdigest(), 'operation': operation}


def write_prepared(base, prepared):
    directory = task_store.state_path(base) / 'evidence'
    for name, raw in prepared['files'].items():
        path = directory / name
        if path.is_symlink():
            raise ValueError('退出输入目标成为链接')
        current = path.read_bytes() if path.exists() else None
        if current == raw:
            continue
        if current != prepared['originals'][name]:
            raise ValueError('退出输入目标漂移，保留部分已写材料并用原清单恢复')
        task_store._write_json_atomic(path, json.loads(raw))



def record(base, issue, run, revision, manifest_path):
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, issue, run)
        if revision != task['_revision']:
            raise ValueError('退出输入工位 revision 已变化')
        prepared = prepare(base, task, manifest_path)
        write_prepared(base, prepared)
        coverage = station_records.conversations(base, task)
        from workflow import station_resources
        needs_amendment = bool(prepared['operation'] and (prepared['operation'].get('cleanup_plan') or {}).get('active_state', {}).get('files') != station_resources.active_files(base))
        return {'status': 'input_recorded', 'issue_key': issue, 'run_id': run, 'revision': revision,
                'manifest_digest': prepared['manifest_digest'], 'conversation': coverage,
                'requires_amendment': needs_amendment,
                'boundary': '仅录入退出材料；未确认新计划、未归档、未清理或释放'}
