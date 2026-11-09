#!/usr/bin/env python3
"""工位工作段计时和 Jira 工时意图；不联网，不推断 Agent 在线或人工工时。"""
from __future__ import annotations

if __name__ == '__main__' and not globals().get('_AO_PRODUCT_ENTRY'):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / 'bootstrap/product_access.py'
    _ao_namespace = {'__file__': str(_ao_loader), '_AO_LOADER_SOURCE': _ao_loader.read_bytes()}
    exec(compile(_ao_namespace['_AO_LOADER_SOURCE'], str(_ao_loader), 'exec'), _ao_namespace)
    raise SystemExit(_ao_namespace['python_entry'](__file__))

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import execution, project_rules, station_context, sync_recovery, task_store

CATEGORIES = {'takeover': '接管', 'plan': '方案', 'environment': '环境',
              'development': '开发', 'test': '测试', 'pr': 'PR'}
PROPERTY = 'agenticops.worklog'
MCP_PROPERTY = 'atlassian-mcp.agenticops.worklog'


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('工时时间必须带时区')
    return result


def state_path(base, issue):
    task_store.validate_issue_key(issue)
    return task_store.state_path(base) / 'evidence' / ('jira-worklog-' + issue + '.json')


def load(base, issue):
    path = state_path(base, issue)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('工时材料不能是符号链接')
    binding = station_context.read_binding(base)
    if not path.exists():
        return {'schema_version': 1, 'issue_key': issue, 'station_id': binding['station_id'],
                'run_id': None, 'active': None, 'segments': {}}
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or value.get('schema_version') != 1 or value.get('issue_key') != issue
            or value.get('station_id') != binding['station_id'] or not isinstance(value.get('segments'), dict)
            or not {'run_id', 'active'} <= set(value)
            or value.get('active') is not None and value['active'] not in value['segments']):
        raise ValueError('工时材料身份或结构不符，保留原件核实')
    for key, row in value['segments'].items():
        if (not isinstance(row, dict) or row.get('id') != key or row.get('category') not in CATEGORIES
                or row.get('status') not in ('active', 'pending', 'deferred', 'intent', 'verified', 'unmeasured', 'empty')
                or not isinstance(row.get('summary'), str) or not row['summary'].strip()
                or row.get('status') == 'active' and value['active'] != key):
            raise ValueError('工时工作段结构不符')
        instant(row['started'])
        if 'spans' in row:
            if not isinstance(row['spans'], list):
                raise ValueError('工时执行片段必须为列表')
            previous_end = instant(row['started'])
            for span in row['spans']:
                if (not isinstance(span, dict) or type(span.get('seconds')) is not int or span['seconds'] < 0
                        or instant(span['started']) < previous_end
                        or int((instant(span['ended']) - instant(span['started'])).total_seconds()) != span['seconds']):
                    raise ValueError('工时执行片段重叠或秒数不符')
                previous_end = instant(span['ended'])
        if 'seconds' in row and (type(row['seconds']) is not int or row['seconds'] < 0):
            raise ValueError('工时秒数无效')
        if row['status'] in ('pending', 'deferred', 'intent', 'verified'):
            measured = sum(span['seconds'] for span in row['spans']) if 'spans' in row else int(
                (instant(row['ended']) - instant(row['started'])).total_seconds())
            if type(row.get('seconds')) is not int or row['seconds'] <= 0 or measured != row['seconds']:
                raise ValueError('工时起止时间与结算秒数不一致')
        if 'request' in row and row.get('request_digest') != digest(row['request']):
            raise ValueError('工时发送载荷摘要不符')
        if row.get('transport') == 'atlassian_mcp':
            arguments = row['request']['arguments']
            wanted = row['expected_worklog']
            minute = re.fullmatch(r'([1-9][0-9]*)m', arguments.get('timeSpent', ''))
            if (not minute or int(minute[1]) * 60 != wanted['timeSpentSeconds']
                    or row['logged_seconds'] != wanted['timeSpentSeconds']
                    or row['unlogged_seconds'] != row['seconds'] - row['logged_seconds']
                    or not 0 <= row['unlogged_seconds'] < 60
                    or arguments.get('issueIdOrKey') != issue
                    or instant(arguments['started']) != instant(wanted['started'])
                    or arguments.get('comment') != wanted['comment']['content'][0]['content'][0]['text']
                    or wanted['properties'] != [{'key': MCP_PROPERTY, 'value': {'id': row['property_id']}}]):
                raise ValueError('MCP 工时载荷与冻结回读要求不一致')
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _require_pre_takeover(state):
    if state['run_id'] is not None:
        raise ValueError('工时不是本任务合法接管前记录，保留原件核实')
    for row in state['segments'].values():
        # prepare 在没有 run 时拒绝发送；未知意图不能伪装成接管前记录。
        if (row['category'] != 'takeover' or row['status'] not in
                ('active', 'pending', 'deferred', 'unmeasured', 'empty')
                or any(key in row for key in ('request', 'worklog_id', 'property_id'))
                or not isinstance(row.get('session_id'), str) or not row['session_id']):
            raise ValueError('工时不是本任务合法接管前记录，保留原件核实')
        if row['status'] == 'active':
            if not isinstance(row.get('active_started'), str):
                raise ValueError('工时活动段缺少合法开始时间')
            instant(row['active_started'])
        elif state['active'] == row['id']:
            raise ValueError('工时活动段与状态不一致')


def check_takeover_evidence(base, issue):
    """空闲工位仅接纳本任务合法接管前工时；调用者持工位锁，不修改材料。"""
    path = state_path(base, issue)
    directory = path.parent
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError('接管前证据目录不可信，保留原件核实')
    if not directory.exists():
        return
    for entry in directory.iterdir():
        if entry != path or entry.is_symlink() or not entry.is_file():
            raise ValueError('空闲工位仍有未知或其它任务证据，拒绝接管')
    if path.exists():
        state = load(base, issue)
        _require_pre_takeover(state)


def attach_takeover(base, task):
    """原接管建立 current 后幂等绑定；保留已暂停段、计时和冻结发送回执。"""
    path = state_path(base, task['issue_key'])
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('工时材料不能是符号链接')
    if not path.exists():
        return
    state = load(base, task['issue_key'])
    if state['run_id'] == task['run_id']:
        return
    _require_pre_takeover(state)
    state['run_id'] = task['run_id']
    save(base, state)


def save(base, state):
    path = state_path(base, state['issue_key'])
    rules = project_rules.load_admission(station=base)
    if project_rules.scan_sensitive(rules, json.dumps(state, ensure_ascii=False)):
        raise ValueError('工时说明或回执含敏感内容；未保存')
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError('工时材料不能是符号链接')
    path.parent.mkdir(parents=True, exist_ok=True)
    task_store._write_json_atomic(path, state)


def context(base, issue, expected_run, state, *, receipt=False):
    task = task_store.read_task(base)
    if task is None:
        if expected_run or state['run_id']:
            raise ValueError('原任务已释放，不能继续写入旧工时；从持久接续材料核实')
        return None
    if task['issue_key'] != issue or task['run_id'] != expected_run:
        raise ValueError('任务或 expected-run-id 已变化，先核对当前 run')
    if state['run_id'] not in (None, task['run_id']):
        raise ValueError('工时属于旧 run，不能借给新任务')
    if receipt:
        sync_recovery.require_receipt(base, task)
    else:
        if task.get('archive_ref') or task.get('outcome') in ('completed', 'interrupted'):
            raise ValueError('任务已归档或终止，不能新增计时或发送')
        from workflow import station_operation
        operation = station_operation.read(base) or {}
        if operation.get('status') not in (None, 'done') and operation.get('kind') != 'takeover':
            raise ValueError('工位正在退出或变更范围，先结算计时再进入该操作')
    state['run_id'] = task['run_id']
    return task


def start(state, segment_id, category, summary, identity):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{7,79}', segment_id or ''):
        raise ValueError('需要稳定 segment-id（8—80 位小写 ASCII 标识），重试复用')
    if category not in CATEGORIES or not isinstance(summary, str) or not summary.strip() or len(summary) > 160 or '\n' in summary:
        raise ValueError('需要有效分类及简洁单行工作说明（最多 160 字）')
    old = state['segments'].get(segment_id)
    if old:
        if (old['category'], old['summary']) != (category, summary.strip()):
            raise ValueError('同一 segment-id 的分类或说明已变化')
        return
    if state['active']:
        raise ValueError('已有计时工作段；先 pause，异常恢复用 recover，不重叠计时')
    started = now()
    state['segments'][segment_id] = {'id': segment_id, 'category': category, 'summary': summary.strip(),
                                    'started': started, 'active_started': started, 'spans': [],
                                    'session_id': identity, 'status': 'active'}
    state['active'] = segment_id


def pause(state, segment_id, identity, *, recover=False):
    row = state['segments'][segment_id]
    if row['status'] != 'active':
        return
    if state['active'] != segment_id:
        raise ValueError('当前计时工作段已变化')
    if not recover and row['session_id'] != identity:
        raise ValueError('原会话工作段未结束；recover 保留未计量尾段，不补算离线时间')
    end = now()
    began = row.get('active_started', row['started'])
    seconds = int((instant(end) - instant(began)).total_seconds())
    if seconds < 0 and not recover:
        raise ValueError('时钟倒退，不能结算；核实后 recover 保留未计量尾段')
    row['ended'] = end
    if recover:
        row['status'] = 'pending' if row.get('seconds', 0) else 'unmeasured'
        row['reason'] = '会话异常或恢复，未结尾段不自动估算'
        row['unmeasured_tail'] = {'started': began, 'ended': end}
    else:
        row.setdefault('spans', []).append({'started': began, 'ended': end, 'seconds': seconds})
        row['seconds'] = sum(span['seconds'] for span in row['spans'])
        row['status'] = 'pending' if row['seconds'] else 'empty'
    row.pop('active_started', None)
    state['active'] = None


def resume(state, segment_id, identity):
    row = state['segments'][segment_id]
    if state['active'] == segment_id and row['session_id'] == identity:
        return
    if state['active'] or row['status'] not in ('pending', 'deferred', 'empty') or 'request' in row:
        raise ValueError('只能继续未发送的暂停工作段；已发送或未知工作段另开新段')
    if 'spans' not in row:
        row['spans'] = [{'started': row['started'], 'ended': row['ended'], 'seconds': row['seconds']}]
    started = now()
    if row['spans'] and instant(started) < instant(row['spans'][-1]['ended']):
        raise ValueError('时钟倒退，不能继续原工作段')
    row.update(status='active', active_started=started, session_id=identity)
    state['active'] = segment_id


def native_identity(base, issue, snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError('原生 Jira 回读必须为对象')
    site = project_rules.load_profile(station=base)['jira']['site'].rstrip('/')
    target = snapshot.get('issue', {})
    actor = snapshot.get('current_user', {})
    if (not isinstance(target, dict) or not isinstance(actor, dict)
            or snapshot.get('site') != site or target.get('key') != issue
            or not str(target.get('id', '')).isdigit() or not snapshot.get('source_ref')
            or actor.get('active') is not True or not actor.get('accountId') or not actor.get('source_ref')):
        raise ValueError('需要同一连接的 Jira site、任务 ID/key、当前用户和回读来源')
    return site, str(target['id']), actor['accountId']


def prepare(base, state, segment_id, snapshot, task):
    row = state['segments'][segment_id]
    if row['status'] in ('intent', 'verified'):
        return None  # 已保存发送意图只能先回读；绝不再次输出可发送载荷。
    if row['status'] not in ('pending', 'deferred'):
        raise ValueError('工作段尚未结算或没有可登记工时')
    if task is None:
        raise ValueError('尚未建立有效 run；保留接管工作段，接管后再同步')
    from workflow import jira_ownership, jira_watermark
    jira_ownership.require(base, state['issue_key'])
    site, issue_id, actor = native_identity(base, state['issue_key'], snapshot)
    ownership = jira_ownership.inspect(base, state['issue_key'], snapshot, task)
    marker = jira_watermark.status(base, task)
    fields, _ = jira_watermark.issue_from(snapshot, state['issue_key'])
    rules = jira_watermark.config(base)
    values = jira_watermark.marker_values(fields, rules, snapshot)
    if (ownership['issue_id'] != issue_id
            or values[rules['marker_fields']['run_id']] != task['run_id']
            or values[rules['marker_fields']['station']] != state['station_id']
            or actor != marker.get('actor', {}).get('accountId')):
        raise ValueError('Jira 接管归属或登录用户已变化，暂停工时写入')
    capabilities = snapshot.get('capabilities', {})
    mcp = capabilities.get('worklog_create') is True and capabilities.get('worklog_property_edit') is True
    if 'request' in row and (row.get('transport') == 'atlassian_mcp') != mcp:
        raise ValueError('原工时意图的发送模式不能改变')
    if not mcp and (capabilities.get('worklog_properties') is not True or capabilities.get('adjust_estimate_leave') is not True):
        raise ValueError('原生能力需支持 MCP 创建及属性补写，或 REST 原子属性与预估参数；否则 defer 保留待登记')
    identifier = digest([site, issue_id, state['station_id'], state['run_id'], segment_id])
    comment = CATEGORIES[row['category']] + '｜' + row['summary']
    body = {'started': row['started'], 'timeSpentSeconds': row['seconds'],
            'comment': {'type': 'doc', 'version': 1, 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': comment}]}]},
            'properties': [{'key': PROPERTY, 'value': {'id': identifier}}]}
    request = {'method': 'POST', 'site': site, 'path': '/rest/api/3/issue/' + issue_id + '/worklog',
               'query': {'adjustEstimate': 'leave'}, 'body': body}
    if mcp:
        if snapshot.get('worklogs_complete') is not True or not isinstance(snapshot.get('worklogs'), list):
            raise ValueError('MCP 创建前须完整回读 Worklog 列表，保留既有 ID 防重')
        before = [str(w['id']) for w in snapshot['worklogs']]
        if any(not value.isdigit() for value in before) or len(set(before)) != len(before):
            raise ValueError('创建前工时列表 ID 缺失或重复')
        seconds = row['seconds'] // 60 * 60
        if not seconds:
            row.update(status='deferred', reason='MCP 按整分钟登记；不足一分钟保留原段，继续同一工作时 resume 累计')
            return None
        body['timeSpentSeconds'] = seconds
        body['properties'][0]['key'] = MCP_PROPERTY
        if any(p.get('key') == MCP_PROPERTY and isinstance(p.get('value'), dict) and p['value'].get('id') == identifier
               for worklog in snapshot['worklogs'] for p in worklog.get('properties', [])):
            raise ValueError('已有本段防重属性，先核实原记录，不重新创建')
        request = {'site': site, 'tool': 'addOrEditJiraIssueWorklog', 'arguments': {
            'issueIdOrKey': state['issue_key'], 'timeSpent': str(seconds // 60) + 'm',
            'started': instant(row['started']).strftime('%Y-%m-%dT%H:%M:%S.000%z'), 'comment': comment}}
        row.update(transport='atlassian_mcp', expected_worklog=body, before_worklog_ids=before,
                   logged_seconds=seconds, unlogged_seconds=row['seconds'] - seconds)
    row.update(status='intent', request=request, request_digest=digest(request), actor_account_id=actor,
               issue_id=issue_id, property_id=identifier, prepared_ref=snapshot['source_ref'])
    return request


def expected(row):
    return row.get('expected_worklog', row['request'].get('body'))


def body_matches(worklog, wanted, row):
    from workflow.quality_write import body_matches as text_matches
    comment = worklog.get('comment')
    comment_ok = comment == wanted['comment']
    if row.get('transport') == 'atlassian_mcp' and isinstance(comment, str):
        text = wanted['comment']['content'][0]['content'][0]['text']
        comment_ok = text_matches(text, comment, worklog.get('body_representation', 'exact-text'))
    return (str(worklog.get('issueId')) == row['issue_id']
            and worklog.get('author', {}).get('accountId') == row['actor_account_id']
            and type(worklog.get('timeSpentSeconds')) is int
            and worklog['timeSpentSeconds'] == wanted['timeSpentSeconds']
            and instant(worklog.get('started', '')) == instant(wanted['started']) and comment_ok)


def bind(base, state, segment_id, snapshot):
    """两步 MCP 的创建回读：只绑定唯一新工时，再准备属性写入；不重新创建。"""
    row = state['segments'][segment_id]
    if row['status'] != 'intent' or row.get('transport') != 'atlassian_mcp':
        raise ValueError('没有原 MCP 工时创建意图')
    site, issue_id, actor = native_identity(base, state['issue_key'], snapshot)
    if (site != row['request']['site'] or issue_id != row['issue_id'] or actor != row['actor_account_id']
            or snapshot.get('worklogs_complete') is not True or not isinstance(snapshot.get('worklogs'), list)):
        raise ValueError('需要原目标、登录用户和完整工时列表回读')
    wanted = expected(row)
    matches = [w for w in snapshot['worklogs'] if str(w.get('id', '')) not in row['before_worklog_ids']
               and body_matches(w, wanted, row)]
    receipt_id = row.get('worklog_id') or snapshot.get('created_worklog_id')
    if receipt_id:
        matches = [w for w in matches if str(w.get('id')) == str(receipt_id)]
    if len(matches) != 1 or not str(matches[0].get('id', '')).isdigit():
        row['reason'] = '无法唯一定位原创建结果；只回读，不再创建或写属性'
        return None
    worklog = matches[0]
    property_value = next((p.get('value') for p in worklog.get('properties', []) if p.get('key') == MCP_PROPERTY), None)
    if property_value is not None and property_value != {'id': row['property_id']}:
        raise ValueError('工时已有其它防重归属，不覆盖其属性')
    row.update(worklog_id=str(worklog['id']), readback_ref=snapshot['source_ref'])
    if property_value == {'id': row['property_id']}:
        complete(base, state, segment_id, snapshot)
        return None
    return {'site': site, 'tool': 'editJiraEntityProperty', 'arguments': {
        'entityType': 'issueWorklog', 'issueIdOrKey': state['issue_key'], 'worklogId': row['worklog_id'],
        'propertyKey': MCP_PROPERTY, 'value': {'id': row['property_id']}}}


def complete(base, state, segment_id, snapshot):
    row = state['segments'][segment_id]
    if row['status'] not in ('intent', 'verified'):
        raise ValueError('没有原工时发送意图；不能用回读新建工时')
    site, issue_id, actor = native_identity(base, state['issue_key'], snapshot)
    if (site != row['request']['site'] or issue_id != row['issue_id'] or actor != row['actor_account_id']):
        raise ValueError('工时回读站点、任务或登录用户与原意图不一致')
    if snapshot.get('worklogs_complete') is not True or not isinstance(snapshot.get('worklogs'), list):
        raise ValueError('工时须读完分页并展开 properties，不能把部分结果当作不存在')
    wanted = expected(row)
    property_key = wanted['properties'][0]['key']
    matches = [w for w in snapshot['worklogs'] if any(
        p.get('key') == property_key and isinstance(p.get('value'), dict) and p['value'].get('id') == row['property_id'] for p in w.get('properties', []))]
    if len(matches) != 1:
        row['status'] = 'intent'
        row['reason'] = '未唯一定位原工时；保持未知，只回读，不盲目重发'
        row['readback_ref'] = snapshot['source_ref']
        return
    worklog = matches[0]
    if not str(worklog.get('id', '')).isdigit() or not body_matches(worklog, wanted, row):
        raise ValueError('原工时作者、目标、开始时间、耗时或正文不一致；不覆盖或重发')
    if row.get('worklog_id') and row['worklog_id'] != str(worklog['id']):
        raise ValueError('原工时 ID 已变化；保留原回执核实')
    row.update(status='verified', worklog_id=str(worklog['id']), readback_ref=snapshot['source_ref'])
    row.pop('reason', None)


def pending(base, issue):
    """可供既有同步警告、交接及清理保全使用的最小可恢复材料。"""
    state = load(base, issue)
    return [{'segment_id': row['id'], 'issue_key': issue, 'run_id': state['run_id'],
             'station_id': state['station_id'], 'category': CATEGORIES[row['category']], 'summary': row['summary'],
             'started': row['started'], 'status': row['status'],
             **{k: copy.deepcopy(row[k]) for k in ('ended', 'seconds', 'reason', 'request', 'request_digest',
                'property_id', 'actor_account_id', 'issue_id', 'prepared_ref', 'readback_ref', 'worklog_id',
                'transport', 'expected_worklog', 'before_worklog_ids', 'logged_seconds', 'unlogged_seconds',
                'spans', 'unmeasured_tail') if k in row}}
            for row in state['segments'].values() if row['status'] not in ('verified', 'empty') or row.get('unlogged_seconds')]


def all_pending(base):
    """保全失败接管留下的其它任务工作段；不把它们绑定到当前 run。"""
    directory = task_store.state_path(base) / 'evidence'
    if directory.is_symlink():
        raise ValueError('工时材料目录不能为链接')
    return [row for path in sorted(directory.glob('jira-worklog-*.json'))
            for row in pending(base, path.stem.removeprefix('jira-worklog-'))]


def boundary(base, task):
    """已有阶段推进的结算点；不创建计时、不根据阶段补算历史。调用者持工位锁。"""
    if not state_path(base, task['issue_key']).exists():
        return
    state = load(base, task['issue_key'])
    if state['run_id'] not in (None, task['run_id']):
        raise ValueError('原工时属于其它 run，保留并核实')
    if state['active']:
        row = state['segments'][state['active']]
        pause(state, row['id'], execution.session_id(), recover=row['session_id'] != execution.session_id())
        state['run_id'] = task['run_id']
        save(base, state)


def settle_boundary(base, task):
    """工时故障不代替业务检查点判定；原材料保持待核验。"""
    try:
        boundary(base, task)
    except (ValueError, KeyError, TypeError, OSError):
        return '工时结算未核验；保留原工作段，查询 jira_worklog status 后处理，不影响独立业务验收'
    return None


def retry(base, state, segment_id, snapshot):
    """只接受原调用的明确拒绝回执；没有查到不等于没有写入。"""
    row = state['segments'][segment_id]
    if row['status'] != 'intent':
        raise ValueError('只有原未知发送意图可核实拒绝结果')
    site, issue_id, actor = native_identity(base, state['issue_key'], snapshot)
    if (site != row['request']['site'] or issue_id != row['issue_id'] or actor != row['actor_account_id']
            or snapshot.get('rejected_request_digest') != row['request_digest']
            or snapshot.get('http_status') not in (400, 401, 403, 404, 413, 422)
            or not snapshot.get('rejection_ref') or snapshot.get('worklogs_complete') is not True
            or not isinstance(snapshot.get('worklogs'), list)):
        raise ValueError('需原调用明确拒绝的状态、请求摘要、来源和完整工时回读；未知结果不能重发')
    if row.get('transport') == 'atlassian_mcp' and (row.get('worklog_id') or any(
            str(w.get('id')) not in row['before_worklog_ids'] and body_matches(w, expected(row), row)
            for w in snapshot['worklogs'])):
        raise ValueError('已发现原 MCP 工时；先 bind 回读，不得重新创建')
    if any(p.get('key') == expected(row)['properties'][0]['key'] and isinstance(p.get('value'), dict) and p['value'].get('id') == row['property_id']
           for w in snapshot['worklogs'] for p in w.get('properties', [])):
        raise ValueError('已发现原工时，改用 complete 回读，不得重发')
    row.update(status='deferred', reason='原调用明确拒绝，已核实未登记', rejection_ref=snapshot['rejection_ref'])


def command(args):
    mutating = args.action != 'status'
    with task_store.task_state_lock(args.dir, execution=mutating, ownership=False, refresh_activity=False):
        state = load(args.dir, args.issue_key)
        request = None
        if mutating:
            task = context(args.dir, args.issue_key, args.expected_run_id, state, receipt=args.action in ('complete', 'bind'))
            if task is None and args.action == 'start' and args.category != 'takeover':
                raise ValueError('未接管时只能记录接管工作段')
            if args.action in ('start', 'resume'):
                for path in state_path(args.dir, args.issue_key).parent.glob('jira-worklog-*.json'):
                    if path != state_path(args.dir, args.issue_key):
                        other = load(args.dir, path.stem.removeprefix('jira-worklog-'))
                        if other['active']:
                            raise ValueError('其它任务还有计时工作段；先暂停或恢复原段，不重叠计时')
            if args.action == 'start':
                start(state, args.segment_id, args.category, args.summary, execution.session_id())
            else:
                if args.segment_id not in state['segments']:
                    raise ValueError('未找到原 segment-id')
                if args.action in ('pause', 'recover'):
                    pause(state, args.segment_id, execution.session_id(), recover=args.action == 'recover')
                elif args.action == 'resume':
                    resume(state, args.segment_id, execution.session_id())
                elif args.action == 'defer':
                    row = state['segments'][args.segment_id]
                    if row['status'] not in ('pending', 'deferred') or not args.reason:
                        raise ValueError('defer 仅适用于已知未发送的已结算工时，需要原因')
                    row.update(status='deferred', reason=args.reason)
                else:
                    if not args.snapshot:
                        raise ValueError('需要原生 Jira 回读 snapshot')
                    snapshot = json.loads(Path(args.snapshot).read_text())
                    if args.action == 'prepare':
                        request = prepare(args.dir, state, args.segment_id, snapshot, task)
                    elif args.action == 'complete':
                        complete(args.dir, state, args.segment_id, snapshot)
                    elif args.action == 'retry':
                        retry(args.dir, state, args.segment_id, snapshot)
                    elif args.action == 'bind':
                        request = bind(args.dir, state, args.segment_id, snapshot)
            save(args.dir, state)  # 意图先原子落盘，成功后才输出可发送载荷。
        result = {'issue_key': args.issue_key, 'run_id': state['run_id'], 'active': state['active'],
                  'segments': [{k: v for k, v in row.items() if k not in ('request', 'session_id', 'request_digest', 'property_id', 'actor_account_id', 'issue_id')}
                               for row in state['segments'].values()],
                  'boundary': '工作段耗时，不是人工投入；等待需显式 pause，异常恢复用 recover，不推断在线'}
        result['readbacks'] = [{'segment_id': row['id'], 'site': row['request']['site'],
                                'path': '/rest/api/3/issue/' + row['issue_id'] + '/worklog',
                                'query': {'expand': 'properties'}, 'property_id': row['property_id'],
                                'request_digest': row['request_digest']}
                               for row in state['segments'].values() if row['status'] == 'intent']
        if request:
            result['native_request'] = request
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'pause', 'resume', 'recover', 'status', 'prepare', 'bind', 'complete', 'defer', 'retry'))
    parser.add_argument('--dir', default='.')
    parser.add_argument('--issue-key', required=True)
    parser.add_argument('--expected-run-id')
    parser.add_argument('--segment-id')
    parser.add_argument('--category', choices=CATEGORIES)
    parser.add_argument('--summary')
    parser.add_argument('--snapshot')
    parser.add_argument('--reason')
    try:
        return command(parser.parse_args())
    except (ValueError, KeyError, TypeError, OSError) as error:
        print('[AgenticOps][工时][失败] ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
