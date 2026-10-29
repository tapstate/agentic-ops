"""旧协调记录的只读核验与受控退役；不再生成评论、报活或接替关系。"""
from __future__ import annotations
import copy
import json
import re
from workflow import execution, project_rules, task_store

MARKER = 'AgenticOps 任务接管协调 v1'

def text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_packet(packet):
    required = {'format', 'site', 'issue_id', 'issue_key', 'station_id', 'session_id',
                'executor', 'claim_id', 'update_id', 'active', 'supersedes', 'decision_ref', 'conflict_check_ref'}
    if not isinstance(packet, dict) or set(packet) != required or packet['format'] != MARKER:
        raise ValueError('接管协调正文格式不符，先由 Agent 核实修补')
    if any(not text(packet[key]) for key in required - {'active', 'supersedes', 'decision_ref', 'conflict_check_ref'}):
        raise ValueError('接管协调身份缺失')
    if not re.fullmatch(r'[a-f0-9]{32}', packet['session_id']) or not packet['issue_id'].isdigit():
        raise ValueError('接管会话或 Jira 任务 ID 无效')
    task_store.validate_issue_key(packet['issue_key'])
    if (type(packet['active']) is not bool or not isinstance(packet['supersedes'], list)
            or any(not isinstance(x, str) or not x.isdigit() for x in packet['supersedes'])
            or len(set(packet['supersedes'])) != len(packet['supersedes'])
            or any(not isinstance(packet[key], str) for key in ('decision_ref', 'conflict_check_ref'))):
        raise ValueError('接管协调状态或接替引用无效')


def validate(state):
    if state is None:
        return
    if not isinstance(state, dict) or set(state) - {'entries', 'proof', 'retired'} or not {'entries', 'proof'} <= set(state) or not isinstance(state['entries'], dict):
        raise ValueError('本地 Jira 协调回执损坏，保留现场并核实')
    if 'retired' in state and (not isinstance(state['retired'], dict) or not all(text(state['retired'].get(k)) for k in ('decision_ref', 'conflict_check_ref', 'source_ref'))):
        raise ValueError('旧协调退役核验缺失')
    for claim, entry in state['entries'].items():
        if not isinstance(entry, dict) or set(entry) != {'packet', 'comment_id', 'pending', 'actor', 'before'}:
            raise ValueError('接管协调意图损坏')
        validate_packet(entry['packet'])
        if entry['before'] is not None:
            validate_packet(entry['before'])
        if claim != entry['packet']['claim_id'] or not isinstance(entry['comment_id'], str) or type(entry['pending']) is not bool or not isinstance(entry['actor'], str):
            raise ValueError('接管协调意图身份不符')
    proof = state['proof']
    if proof is not None and (not isinstance(proof, dict) or set(proof) != {'site', 'issue_id', 'issue_key', 'session_id', 'station_id', 'comment_id', 'digest', 'valid'} or type(proof['valid']) is not bool):
        raise ValueError('接管协调检查回执损坏')


def state(base):
    return copy.deepcopy(task_store.read_current(base).get('jira_coordination', {'entries': {}, 'proof': None}))


def save(base, value):
    validate(value)
    current = task_store.read_current(base)
    current['jira_coordination'] = value
    task_store._write_json_atomic(task_store.current_path(base), current)


def require_retired(base):
    value = state(base)
    if not value.get('retired') and (value['entries'] or value['proof']):
        raise ValueError('旧协调记录尚未退役；先核实旧执行者停写和原写入结果，再 coordination retire，不改写远端历史')


def pending(base):
    value = state(base)
    return [] if value.get('retired') else [
        {'claim_id': key, 'issue_key': entry['packet']['issue_key'], 'comment_id': entry['comment_id'],
         'pending': entry['pending']} for key, entry in value['entries'].items() if entry['pending']]


def cleanup_notice(base):
    return {'records': pending(base), 'guidance': '旧协调写入结果尚未核实；保留本地原意图，原生回读后 coordination retire。不更新或清空远端归属。'}


def retire(base, supplied, decision_ref, conflict_check_ref):
    if not text(decision_ref) or not text(conflict_check_ref):
        raise ValueError('旧协调退役需要人工决定、旧执行者停写及在途操作核实')
    with task_store.task_state_lock(base, ownership=False):
        value = state(base)
        if value.get('retired'):
            return {'retired': True, 'repeated': True}
        if not isinstance(supplied, dict) or not text(supplied.get('source_ref')):
            raise ValueError('需要旧协调原意图的原生回读来源')
        observations = supplied.get('observations', [])
        for claim, entry in value['entries'].items():
            if not entry['pending']:
                continue
            matches = [row for row in observations if row.get('claim_id') == claim]
            if len(matches) != 1:
                raise ValueError('旧协调未知写入必须逐项核实，不能丢失恢复线索：' + claim)
            row = matches[0]
            packet = entry['packet']
            if (row.get('site') != packet['site'] or row.get('issue_id') != packet['issue_id']
                    or not text(row.get('source_ref'))):
                raise ValueError('旧协调回读身份不符')
            expected = MARKER + '\n\n```json\n' + json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + '\n```'
            if row.get('body') == expected and str(row.get('comment_id', '')).isdigit():
                if entry['comment_id'] and entry['comment_id'] != row['comment_id']:
                    raise ValueError('旧协调评论 ID 不符')
                entry['comment_id'] = row['comment_id']
            elif not (text(row.get('not_applied_ref')) and row.get('confirmed_not_applied') is True):
                raise ValueError('先核实原评论或明确原写入未生效，不因升级丢弃未知结果')
            entry['pending'] = False
        value['retired'] = {'decision_ref': decision_ref, 'conflict_check_ref': conflict_check_ref,
                            'source_ref': supplied['source_ref']}
        value['proof'] = None
        save(base, value)
        return {'retired': True, 'boundary': '保留远端历史；旧会话须已停写，四项标记仍需重新核验'}


def command(args):
    from pathlib import Path
    if args.coordination_action == 'status':
        value = state(args.dir)
        result = {'retired': bool(value.get('retired')), 'records': pending(args.dir)}
    else:
        supplied = json.loads(Path(args.input).read_text()) if args.input else None
        result = retire(args.dir, supplied, args.decision_ref, args.conflict_check_ref)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
