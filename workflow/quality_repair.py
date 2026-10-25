"""质量日志外层格式修补；只采用原样事件，不转换事件语义或授予批准。"""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from workflow import quality, quality_contract, station_context, task_store


def _read(path):
    if path.parent.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError('质量材料必须是实际普通文件，保留现场并核实路径')
    return path.read_bytes()


def _object(raw):
    value = json.loads(raw, object_pairs_hook=station_context._unique_object)
    if not isinstance(value, dict):
        raise ValueError('质量材料顶层须为对象；无法可靠定位事件时不采用')
    return value


def _candidate(original, candidate, task, event_field):
    if event_field in ('schema_version', 'issue_key', 'run_id', 'revision'):
        raise ValueError('事件字段不能与身份或版本字段重叠')
    if set(original) - {'schema_version', 'issue_key', 'run_id', 'revision', event_field}:
        raise ValueError('存在无法解释的其它字段；本入口只修补质量日志外层格式')
    for key in ('issue_key', 'run_id'):
        if original.get(key) != task[key] or candidate.get(key) != task[key]:
            raise ValueError('原质量材料或候选的任务/run 身份不符，不能由 AI 补造身份')
    events = original.get(event_field)
    if not isinstance(events, list) or quality.digest(events) != quality.digest(candidate.get('events')):
        raise ValueError('必须原样保留全部事件及顺序；不得增加、删除或改写历史事件')
    if 'revision' in original and (type(original['revision']) is not int or original['revision'] != len(events)):
        raise ValueError('原材料 revision 与事件数冲突，先核实缺失事件，不用格式修补掩盖')
    quality_contract.validate(candidate, 'quality-state.schema.json')
    if candidate['revision'] != len(events):
        raise ValueError('候选 revision 必须等于保全的事件数')
    for event in events:
        ctx = event['context']
        if any(ctx.get(key) != task[key] for key in ('issue_key', 'run_id')):
            raise ValueError('历史事件包含其它任务/run，不能采用')
    quality.replay(candidate)


def repair(base, issue, run_id=None, expected_digest=None, candidate_path=None, event_field='events'):
    with task_store.task_state_lock(base, execution=candidate_path is not None,
                                    refresh_activity=candidate_path is not None,
                                    allow_incompatible_station=candidate_path is None):
        task = task_store.read_task(base, issue)
        if task is None:
            raise ValueError('没有当前任务')
        path = quality.state_path(base, task)
        raw = _read(path)
        source_digest = hashlib.sha256(raw).hexdigest()
        if candidate_path is None:
            try:
                quality.load(base, task)
                problem = None
            except (ValueError, KeyError, TypeError) as error:
                problem = str(error)
            return {'status': 'repair_required' if problem else 'compatible', 'source': str(path),
                    'source_digest': source_digest, 'issue_key': task['issue_key'], 'run_id': task['run_id'],
                    'problem': problem, 'contract': 'contracts/quality-state.schema.json',
                    'next': 'AI 在工位状态目录外准备当前合同 JSON；保留原 issue_key/run_id 和全部事件。用 repair --input 候选 --event-field 原事件字段 --expected-run-id 本run --expected-source-digest 本摘要复查采用；身份/事件不明则停止并反馈。',
                    'boundary': '仅质量日志外层格式；不转换历史事件、不恢复授权、不放宽 epoch'}
        if run_id != task['run_id'] or not isinstance(expected_digest, str) or len(expected_digest) != 64:
            raise ValueError('修补需绑定当前 run 和诊断得到的原文件摘要')
        if any(c not in '0123456789abcdef' for c in expected_digest):
            raise ValueError('原文件摘要格式无效')
        task_store.require_development(base, task)
        candidate = station_context.read_object(candidate_path, '质量修补候选')
        backup = path.with_name(path.name + '.before-repair-' + expected_digest + '.json')
        if source_digest != expected_digest:
            # 原子采用后的重试只回读；不能重新导入旧材料。
            if _object(raw) == candidate and backup.is_file():
                original_raw = _read(backup)
                if hashlib.sha256(original_raw).hexdigest() == expected_digest:
                    _candidate(_object(original_raw), candidate, task, event_field)
                    return {'status': 'already_applied', 'run_id': run_id, 'backup': str(backup),
                            'next': '执行 quality.py status，按当前规则核验后继续'}
            raise ValueError('原材料已变化；重新诊断，不覆盖并发更新')
        _candidate(_object(raw), candidate, task, event_field)
        if _object(raw) == candidate:
            return {'status': 'compatible', 'run_id': run_id, 'next': '按正常质量入口继续'}
        if backup.exists():
            if _read(backup) != raw:
                raise ValueError('原件保全记录不一致，停止采用')
        else:
            fd, temporary = tempfile.mkstemp(prefix='.quality-original-', dir=str(path.parent))
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, backup)  # 原件原子落位且不覆盖；中断不留下半份保全记录。
            finally:
                os.unlink(temporary)
        if _read(path) != raw:
            raise ValueError('采用前原材料变化；原件保留，重新诊断')
        task_store._write_json_atomic(path, candidate)
        return {'status': 'repaired', 'run_id': run_id, 'revision': candidate['revision'],
                'backup': str(backup), 'next': '执行 quality.py status，按当前规则核验后继续；格式修补不等于 PASS'}
