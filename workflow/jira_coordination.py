"""Jira 任务接管协调：消费原生回读，不联网、不提供全局排他。"""
from __future__ import annotations

import copy
import json
import re
import time
from datetime import datetime

from workflow import engineering_baseline, execution, project_rules, station_context, task_store

MARKER = 'AgenticOps 任务接管协调 v1'
IDLE_SECONDS = 8 * 3600


def timestamp(value):
    try:
        normalized = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', value.replace('Z', '+00:00'))
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            raise ValueError()
        result = parsed.timestamp()
        if result < 0 or result > time.time() + 300:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError):
        raise ValueError('Jira 服务端活动时间无效或在未来，先核实回读和本机时钟')


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
    if not isinstance(state, dict) or set(state) != {'entries', 'proof'} or not isinstance(state['entries'], dict):
        raise ValueError('本地 Jira 协调回执损坏，保留现场并核实')
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


def snapshot(base, supplied):
    """pages 使用原生 list comments 的完整分页；不把 filtered/截断结果当全量。"""
    site = project_rules.load_profile(station=base)['jira']['site'].rstrip('/')
    if (not isinstance(supplied, dict) or supplied.get('site') != site
            or not isinstance(supplied.get('issue_id'), str) or not supplied['issue_id'].isdigit()
            or not text(supplied.get('source_ref')) or not isinstance(supplied.get('pages'), list) or not supplied['pages']):
        raise ValueError('需要 Jira site/issue_id/issue_key/source_ref 和完整 pages 回读')
    issue = task_store.validate_issue_key(supplied.get('issue_key'))
    project_rules.validate_project_issue(project_rules.load_profile(station=base), issue)
    offset, total, seen, rows = 0, None, set(), []
    for index, page in enumerate(supplied['pages']):
        if (not isinstance(page, dict) or type(page.get('total')) is not int or page['total'] < 0
                or page.get('startAt') != offset or not isinstance(page.get('comments'), list)
                or type(page.get('isLast')) is not bool):
            raise ValueError('Jira 评论分页不完整或结构不符')
        if total is None:
            total = page['total']
        if total != page['total']:
            raise ValueError('分页期间评论总数变化，重新读取')
        for comment in page['comments']:
            cid = comment.get('id') if isinstance(comment, dict) else None
            if not isinstance(cid, str) or not cid.isdigit() or cid in seen or not isinstance(comment.get('body'), str):
                raise ValueError('评论身份重复或正文缺失，不能推定空闲')
            seen.add(cid)
            body = comment['body']
            if not body.startswith(MARKER):
                continue
            match = re.fullmatch(re.escape(MARKER) + r'\n\n```json\n(.*)\n```', body, re.S)
            if not match:
                raise ValueError('发现无法解释的协调评论，先核实正文格式')
            try:
                packet = json.loads(match.group(1))
            except ValueError:
                raise ValueError('协调评论 JSON 无效')
            validate_packet(packet)
            if (packet['site'], packet['issue_id']) != (site, supplied['issue_id']):
                raise ValueError('协调评论与 Jira 任务身份不一致')
            rows.append({'id': cid, 'packet': packet, 'updated': timestamp(comment.get('updated')), 'body': body})
        offset += len(page['comments'])
        if page['isLast'] != (index == len(supplied['pages']) - 1) or page['isLast'] and offset != total:
            raise ValueError('Jira 评论分页尚未完整回读')
    if offset != total:
        raise ValueError('Jira 评论分页数量不一致')
    ids = {row['id'] for row in rows}
    superseded = set()
    claims = set()
    for row in rows:
        packet = row['packet']
        if packet['claim_id'] in claims or row['id'] in packet['supersedes'] or not set(packet['supersedes']) <= ids:
            raise ValueError('协调接替引用缺失、自引用或接管编号重复')
        claims.add(packet['claim_id'])
        superseded.update(packet['supersedes'])
    links = {row['id']: row['packet']['supersedes'] for row in rows}
    remaining = set(links)
    while remaining:
        ready = {cid for cid in remaining if not set(links[cid]) & remaining}
        if not ready:
            raise ValueError('协调接替关系存在循环，先核实记录')
        remaining -= ready
    heads = [r for r in rows if r['packet']['active'] and r['id'] not in superseded]
    digest = engineering_baseline.digest({'site': site, 'issue_id': supplied['issue_id'], 'rows': sorted(rows, key=lambda r:r['id'])})
    return {'site': site, 'issue_id': supplied['issue_id'], 'issue_key': issue, 'rows': rows, 'heads': heads, 'digest': digest}


def own(base, packet):
    return (packet['station_id'] == station_context.read_binding(base)['station_id']
            and packet['session_id'] == execution.session_id())


def observe(base, view, value):
    head = view['heads'][0] if len(view['heads']) == 1 else None
    value['proof'] = {key: view[key] for key in ('site', 'issue_id', 'issue_key', 'digest')}
    value['proof'].update(session_id=execution.session_id(), station_id=station_context.read_binding(base)['station_id'],
                          comment_id=head['id'] if head else '', valid=bool(head and own(base, head['packet'])))
    # 未核实写入仍不能因为观察到旧的有效记录而继续。
    if any(e['pending'] and own(base, e['packet']) for e in value['entries'].values()):
        value['proof']['valid'] = False
    save(base, value)


def require(base, issue):
    value = state(base)
    proof = value['proof']
    if (not proof or not proof['valid'] or proof['issue_key'] != issue
            or proof['session_id'] != execution.session_id()
            or proof['station_id'] != station_context.read_binding(base)['station_id']):
        raise ValueError('任务级接管尚未核实：先用 coordination check/prepare/complete 回读 Jira，冲突写入暂停')


def body(packet):
    return MARKER + '\n\n```json\n' + json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + '\n```'


def pending(base):
    return [{'claim_id': key, 'issue_key': e['packet']['issue_key'], 'comment_id': e['comment_id'],
             'pending': e['pending'], 'active': e['packet']['active']} for key,e in state(base)['entries'].items() if e['pending'] or e['packet']['active']]


def run(base, action, supplied=None, operation_id=None, mode='claim', executor=None,
        expected_digest=None, conflict_check_ref=None, decision_ref=None, claim_id=None):
    with task_store.task_state_lock(base, execution=False):
        value = state(base)
        if action == 'status':
            return {'proof': value['proof'], 'records': pending(base)}
        try:
            view = snapshot(base, supplied)
        except (ValueError, KeyError, TypeError):
            value['proof'] = None
            save(base, value)
            raise
        observe(base, view, value)
        heads = view['heads']
        if action == 'check':
            return {'verified': value['proof']['valid'], 'digest': view['digest'],
                    'holders': [{'comment_id': r['id'], 'executor': r['packet']['executor'], 'station_id': r['packet']['station_id'],
                                 'session_id': r['packet']['session_id'], 'last_reported_epoch': r['updated'],
                                 'idle': time.time() - r['updated'] >= IDLE_SECONDS} for r in heads],
                    'boundary': '仅报告活动，不保证原生进程停止或全局互斥'}
        if action == 'complete':
            entry = value['entries'].get(claim_id)
            if not entry or entry['actor'] != execution.session_id():
                raise ValueError('只能回读本会话原协调意图')
            matches = [r for r in view['rows'] if r['body'] == body(entry['packet']) and (not entry['comment_id'] or r['id'] == entry['comment_id'])]
            if len(matches) != 1:
                raise ValueError('原写入未唯一匹配，先核实 Jira，不能盲目重发')
            entry.update(comment_id=matches[0]['id'], pending=False, before=None)
            observe(base, view, value)
            return {'saved': True, 'comment_id': matches[0]['id'], 'verified': value['proof']['valid'], 'records': pending(base)}
        if action == 'cancel':
            entry = value['entries'].get(claim_id)
            if not entry or not entry['pending'] or entry['actor'] != execution.session_id() or not text(decision_ref):
                raise ValueError('取消需要本会话待发送意图及明确尚未发送的决定；未知写入先回读')
            if any(r['body'] == body(entry['packet']) for r in view['rows']):
                raise ValueError('原写入已存在，应 complete 回读而非取消')
            if entry['before'] is not None:
                entry.update(packet=entry['before'], pending=False, before=None)
            else:
                del value['entries'][claim_id]
            observe(base, view, value)
            return {'cancelled': True, 'records': pending(base)}
        if action != 'prepare'  or mode not in ('claim', 'report', 'end') or not text(operation_id):
            raise ValueError('协调操作无效，需要稳定 operation-id')
        original = next((e for e in value['entries'].values() if e['packet']['update_id'] == operation_id), None)
        if original:
            if original['actor'] != execution.session_id() or original['packet']['issue_id'] != view['issue_id'] or (mode == 'end') == original['packet']['active']:
                raise ValueError('协调操作编号已用于其它身份')
            return {'claim_id': original['packet']['claim_id'], 'comment_id': original['comment_id'], 'body': body(original['packet']),
                    'pending': original['pending'], 'guidance': '回读原写入；仅已证明未发送时发送原正文'}
        if any(e['pending'] and e['packet']['issue_id'] == view['issue_id'] for e in value['entries'].values()):
            raise ValueError('存在结果未确认的协调写入，先 complete 回读原意图')
        if expected_digest != view['digest']:
            raise ValueError('协调观察已变化，重新核对接管者及决定')
        if mode == 'claim':
            if len(heads) == 1 and own(base, heads[0]['packet']):
                raise ValueError('当前会话已经接管，使用 check 或 report 继续')
            if heads and not text(conflict_check_ref):
                raise ValueError('先核实原执行者停写及在途操作，提供 conflict-check-ref')
            if (len(heads) > 1 or any(time.time() - r['updated'] < IDLE_SECONDS for r in heads)) and not text(decision_ref):
                raise ValueError('8 小时内活动或竞争记录需要用户明确交接决定 decision-ref')
            if not text(executor):
                raise ValueError('接管者说明缺失')
            packet = {'format': MARKER, **{k:view[k] for k in ('site','issue_id','issue_key')},
                      'station_id': station_context.read_binding(base)['station_id'], 'session_id': execution.session_id(),
                      'executor': executor, 'claim_id': operation_id, 'update_id': operation_id, 'active': True,
                      'supersedes': [r['id'] for r in heads], 'conflict_check_ref': conflict_check_ref or '', 'decision_ref': decision_ref or ''}
            cid = ''
        else:
            entry = value['entries'].get(claim_id)
            if not entry or entry['packet']['station_id'] != station_context.read_binding(base)['station_id'] or entry['packet']['issue_id'] != view['issue_id']:
                raise ValueError('只能更新本工位本任务的协调记录')
            if not own(base, entry['packet']) and (mode != 'end' or not text(decision_ref) or not text(conflict_check_ref)):
                raise ValueError('结束本工位旧会话记录需要人工决定和停写核实；不能替旧会话报活')
            row = next((r for r in view['rows'] if r['id'] == entry['comment_id']), None)
            if not row or row['body'] != body(entry['packet']):
                raise ValueError('原协调正文已变化或不可见，先核实，不能覆盖')
            if mode == 'report' and (len(heads) != 1 or row not in heads):
                raise ValueError('已被接替或存在冲突，旧记录不能报活夺回接管')
            packet = dict(entry['packet'], update_id=operation_id, active=mode != 'end')
            cid = entry['comment_id']
        if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(packet,ensure_ascii=False)):
            raise ValueError('协调正文含敏感内容，先脱敏')
        prior = value['entries'].get(packet['claim_id'])
        value['entries'] = {k:e for k,e in value['entries'].items() if e['pending'] or e['packet']['active']}
        value['entries'][packet['claim_id']] = {'packet': packet, 'comment_id': cid, 'pending': True, 'actor': execution.session_id(), 'before': prior['packet'] if prior else None}
        value['proof'] = None
        save(base, value)
        return {'claim_id': packet['claim_id'], 'comment_id': cid, 'body': body(packet), 'pending': True,
                'guidance': '使用原生 Jira 工具写入并完整回读，随后 complete；结果未知禁止重发'}


def cleanup_notice(base):
    value = state(base)
    value['proof'] = None
    save(base, value)
    return {'records': pending(base), 'guidance': '本地清理完成不等于 Jira 已结束；回读后逐条 prepare --mode end / complete，仅处理本工位记录'}


def command(args):
    from pathlib import Path
    try:
        supplied = json.loads(Path(args.input).read_text()) if args.input else None
    except (OSError, ValueError):
        with task_store.task_state_lock(args.dir, execution=False):
            value = state(args.dir)
            value['proof'] = None
            save(args.dir, value)
        raise ValueError('无法读取 Jira 协调材料，保留原意图并暂停冲突写入')
    result = run(args.dir, args.coordination_action, supplied, args.operation_id, args.mode, args.executor,
                 args.expected_digest, args.conflict_check_ref, args.decision_ref, args.claim_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if args.coordination_action != 'check' or result['verified'] else 2
