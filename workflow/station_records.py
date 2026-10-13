"""会话覆盖与任务记录的只读核验；不抓取平台会话。"""
import json
import hashlib
import re
from pathlib import Path

from workflow import engineering_baseline as baseline, task_store

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024

STATE_KEYS = {'schema_version', 'kind', 'issue_key', 'run_id', 'id', 'operation_id', 'stage', 'phase',
              'status', 'outcome', 'result', 'tests', 'failures', 'errors', 'skipped', 'head', 'sha',
              'sha256', 'digest', 'repository', 'repo', 'branch', 'target_branch', 'checkpoint',
              'checkpoint_id', 'source_ref', 'report_ref', 'readback_ref', 'observed_at', 'at',
              'decision', 'decision_ref', 'actor', 'reference', 'proof', 'scope', 'verification_method',
              'events', 'command', 'action', 'payload', 'context', 'revision', 'results', 'criterion',
              'method', 'raw_result', 'failure_kind', 'case_ref', 'case_version', 'target_revision',
              'uncovered', 'follow_up', 'accepted_gap'}


def state_projection(value, key=None):
    """未知证据只留来源指纹；结构化状态不携带日志、报告或源码正文。"""
    if isinstance(value, dict):
        return {name: state_projection(item, name) for name, item in value.items() if name in STATE_KEYS
                and (name not in {'payload', 'context', 'proof', 'result', 'results', 'events', 'command'}
                     or isinstance(item, (dict, list)))}
    if isinstance(value, list):
        return [state_projection(item, key) for item in value if isinstance(item, dict)
                or (key == 'scope' and isinstance(item, str) and '\n' not in item and len(item) <= 1024)]
    if key is None:
        return {}  # 不把未知顶层字符串或数组正文当状态。
    if key == 'raw_result' and value not in ('PASS', 'FAIL', 'UNKNOWN', 'NOT_RUN', 'SKIPPED'):
        return None
    return value


def evidence_projection(name, value, task):
    """现役同步信封只保存固定结果字段，不保存请求正文或任意动态载荷。"""
    if not (isinstance(value, dict) and value.get('schema_version', 1) == 1
            and value.get('run_id') == task['run_id'] and value.get('issue_key') == task['issue_key']):
        return {}
    result = state_projection(value)
    keys = {'trigger', 'operation_id', 'from_status', 'target_status', 'outcome', 'status', 'reason',
            'source_ref', 'readback_ref', 'readback_status', 'readback_value', 'at', 'completed_at',
            'field_id', 'field_name', 'logical_key', 'version', 'write_mode', 'payload_digest',
            'fact_digest', 'jira_field'}
    def row(record):
        return {key: item for key, item in record.items() if key in keys and isinstance(item, (str, int, bool))} if isinstance(record, dict) else {}
    if re.fullmatch(r'jira-status-[0-9a-f]{24}\.json', name) and isinstance(value.get('attempts'), dict):
        result['attempts'] = [{'trigger': trigger, **row(record)} for trigger, record in value['attempts'].items()]
    elif re.fullmatch(r'jira-watermark-[0-9a-f]{24}\.json', name):
        result['watermark'] = row(value.get('watermark'))
    elif re.fullmatch(r'jira-fields-[0-9a-f]{24}\.json', name) and isinstance(value.get('fields'), dict):
        result['fields'] = [{'fact_key': fact, **row(record)} for fact, record in value['fields'].items()]
    return result


def conversation_projection(value):
    keys = {'schema_version', 'kind', 'issue_key', 'run_id', 'platform', 'thread_id', 'source_ref',
            'cutoff', 'coverage', 'source_kind', 'first', 'last', 'missing', 'part', 'parts'}
    result = {key: item for key, item in value.items() if key in keys}
    result['messages'] = [{key: item for key, item in message.items() if key in {'role', 'text', 'id', 'order', 'kind'}}
                          for message in value['messages']]
    for source, target in zip(value['messages'], result['messages']):
        if source.get('attachments'):
            target['attachments'] = [{key: item for key, item in attachment.items()
                                      if key in {'source_ref', 'sha256', 'size', 'mime_type', 'title'}}
                                     for attachment in source['attachments']]
    return result


def read_documents(base, task, overrides=None):
    """只识别固定会话/控制文件；未知证据只读取指纹，不解释其正文。"""
    from workflow import file_digest, station_context
    directory = task_store.state_path(base) / 'evidence'
    if directory.is_symlink():
        raise ValueError('证据目录不能是链接')
    result = {}
    for path in sorted(directory.glob('*.json')):
        if path.is_symlink() or not path.is_file():
            raise ValueError('证据不是普通文件')
        conversation = path.name.startswith('conversation')
        control = path.name == 'resources.json' or bool(re.fullmatch(r'(?:quality|jira-status|jira-watermark|jira-fields)-[0-9a-f]{24}\.json', path.name))
        size = path.stat().st_size
        if conversation and not path.name.startswith('conversation-scope') and size > MAX_FILE_BYTES:
            raise ValueError('任务记录超过归档容量上限；请分块或明确覆盖缺口，不静默截断')
        value = None
        if conversation or control:
            value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=station_context._unique_object)
        result[path.name] = {'value': value, 'bytes': size, 'sha256': file_digest.sha256_file(path)}
    for name, raw in (overrides or {}).items():
        result[name] = {'value': json.loads(raw, object_pairs_hook=station_context._unique_object),
                        'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    return result


def validate_scope(value, task):
    if (not isinstance(value, dict) or (type(value.get('schema_version')) is not int or value['schema_version'] != 1) or value.get('kind') != 'conversation-scope'
            or (value.get('issue_key'), value.get('run_id')) != (task['issue_key'], task['run_id'])
            or not isinstance(value.get('sources'), list)):
        raise ValueError('会话来源范围版本、身份或结构无效')
    seen = set()
    for row in value['sources']:
        if not isinstance(row, dict):
            raise ValueError('会话来源不是对象')
        for key in ('platform', 'thread_id', 'source_ref', 'cutoff', 'boundary_ref'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError('会话来源缺少可回查边界：' + key)
        identity = (row['platform'], row['thread_id'])
        if identity in seen:
            raise ValueError('会话来源身份重复')
        seen.add(identity)
        if row.get('source_kind') not in ('native_messages', 'user_export', 'summaries', 'unavailable'):
            raise ValueError('会话来源类型无效')
        first, last = row.get('first'), row.get('last')
        known = type(first) is int and type(last) is int and 0 <= first <= last
        if not known and not (first is None and last is None and isinstance(row.get('missing'), str) and row['missing'].strip()):
            raise ValueError('会话来源须给出连续首末边界或明确未知原因')
        if row['source_kind'] in ('summaries', 'unavailable') and not row.get('missing'):
            raise ValueError('摘要或不可用来源须说明缺口')
    if not value['sources'] and not value.get('missing'):
        raise ValueError('空来源范围须说明缺口')
    return value


def conversations(base, task, documents=None):
    records, scope, total = [], None, 0
    documents = read_documents(base, task) if documents is None else documents
    for name, row in sorted(documents.items()):
        value, size = row['value'], row['bytes']
        if name.startswith('conversation-scope'):
            if scope is not None:
                raise ValueError('会话来源范围只能有一份')
            scope = validate_scope(value, task)
            continue
        if not name.startswith('conversation'):
            continue
        if not isinstance(value, dict) or value.get('kind') != 'conversation':
            raise ValueError('会话输入类型无效')
        if size > MAX_FILE_BYTES:
            raise ValueError('任务记录超过归档容量上限；请分块或明确覆盖缺口，不静默截断')
        if ((type(value.get('schema_version')) is not int or value['schema_version'] != 1) or value.get('issue_key') != task['issue_key']
                or value.get('run_id') != task['run_id'] or value.get('coverage') not in ('complete', 'partial', 'unavailable')):
            raise ValueError('会话记录版本、身份或覆盖标识无效')
        for key in ('platform', 'thread_id', 'source_ref', 'cutoff'):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError('会话记录缺少来源或截止点：' + key)
        messages = value.get('messages')
        if not isinstance(messages, list):
            raise ValueError('会话消息必须是数组')
        orders = []
        ids = []
        for message in messages:
            if (not isinstance(message, dict) or message.get('role') not in ('user', 'assistant', 'tool')
                    or not isinstance(message.get('text'), str) or type(message.get('order')) is not int
                    or message['order'] < 0):
                raise ValueError('会话消息缺少可见原文或稳定导出顺序')
            attachments = message.get('attachments', [])
            if (not isinstance(attachments, list) or any(not isinstance(a, dict)
                    or not isinstance(a.get('source_ref'), str) or not a['source_ref'].strip() for a in attachments)):
                raise ValueError('会话附件只能提供可核验来源引用')
            orders.append(message['order'])
            if message.get('id'):
                ids.append(message['id'])
        if orders != sorted(set(orders)) or len(set(ids)) != len(ids):
            raise ValueError('会话消息重复或顺序无效')
        if value['coverage'] == 'complete':
            if (value.get('source_kind') not in ('native_messages', 'user_export') or not orders
                    or value.get('first') != orders[0] or value.get('last') != orders[-1]
                    or any(right != left + 1 for left, right in zip(orders, orders[1:]))
                    or any(m.get('kind', 'message') != 'message' for m in messages)):
                raise ValueError('完整会话必须提供原文来源和连续首末边界；摘要不能标为 complete')
        elif not isinstance(value.get('missing'), str) or not value['missing'].strip():
            raise ValueError('部分或不可用会话必须说明缺口')
        part, parts = value.get('part', 1), value.get('parts', 1)
        if type(part) is not int or type(parts) is not int or not 1 <= part <= parts:
            raise ValueError('会话分块编号无效')
        total += len(json.dumps(conversation_projection(value), ensure_ascii=False).encode('utf-8'))
        if total > MAX_TOTAL_BYTES:
            raise ValueError('任务记录超过归档总量上限')
        records.append({'file': name, 'value': value, 'part': part, 'parts': parts, 'bytes': size})
    groups = {}
    for row in records:
        v = row['value']
        groups.setdefault((v['platform'], v['thread_id']), []).append(row)
    complete = bool(records)
    for rows in groups.values():
        rows.sort(key=lambda r: r['part'])
        if len({r['value']['cutoff'] for r in rows}) != 1:
            raise ValueError('同一会话的分块截止点不一致')
        expected = rows[0]['parts']
        numbers = [r['part'] for r in rows]
        if (any(r['parts'] != expected for r in rows) or len(set(numbers)) != len(numbers)
                or (all(r['value']['coverage'] == 'complete' for r in rows) and numbers != list(range(1, expected + 1)))):
            raise ValueError('会话分块缺失或重复，请如实记录 partial 覆盖')
        for left, right in zip(rows, rows[1:]):
            if left['value']['coverage'] == right['value']['coverage'] == 'complete' and left['value']['last'] + 1 != right['value']['first']:
                raise ValueError('完整会话分块边界不连续')
        all_orders = [m['order'] for row in rows for m in row['value']['messages']]
        if len(set(all_orders)) != len(all_orders):
            raise ValueError('同一来源会话的分块消息重复')
        all_ids = [m['id'] for row in rows for m in row['value']['messages'] if m.get('id')]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError('同一来源会话的消息 ID 重复')
        complete = complete and all(r['value']['coverage'] == 'complete' for r in rows)
    missing = []
    declared = scope['sources'] if scope else []
    if not declared:
        missing.append((scope or {}).get('missing', '未声明可核验会话来源范围'))
        complete = False
    declared_ids = {(r['platform'], r['thread_id']) for r in declared}
    if set(groups) - declared_ids:
        complete = False
        missing.append('有会话来源未列入声明范围')
    for source in declared:
        rows = groups.get((source['platform'], source['thread_id']), [])
        covered = bool(rows) and all(r['value']['coverage'] == 'complete' for r in rows)
        covered = covered and source['source_kind'] in ('native_messages', 'user_export')
        covered = covered and all(r['value']['cutoff'] == source['cutoff'] and r['value']['source_kind'] == source['source_kind'] for r in rows)
        covered = covered and rows[0]['value'].get('first') == source['first'] and rows[-1]['value'].get('last') == source['last']
        if not covered:
            complete = False
            missing.append(source['platform'] + ':' + source['thread_id'] + '：' + source.get('missing', '来源缺失或边界未完整覆盖'))
    snapshot = {'declared_scope': scope, 'missing': missing, 'coverage': 'complete' if complete else 'partial' if records else 'unavailable',
                'sources': [{k: row['value'].get(k) for k in ('platform', 'thread_id', 'source_ref', 'cutoff', 'coverage', 'first', 'last', 'missing')}
                            for row in records], 'content_digest': baseline.digest([r['value'] for r in records]),
                'evidence_bytes': sum(row['bytes'] for row in records)}
    snapshot['digest'] = baseline.digest(snapshot)
    return snapshot


def verify_decision(base, task, request, documents=None):
    snapshot = conversations(base, task, documents)
    required = request.get('require_complete_conversation', True)
    if type(required) is not bool:
        raise ValueError('require_complete_conversation 必须是布尔值')
    if snapshot['coverage'] != 'complete':
        if request.get('accept_conversation_digest') != snapshot['digest'] or not request.get('decision_ref'):
            raise ValueError('完整会话不可取得：请提供导出或明确接受当前覆盖摘要')
    return snapshot


def effective_request(operation, amendment=None):
    """原请求身份不变，仅消费当前修订明确接受的会话覆盖决定。"""
    result = dict(operation['request'])
    latest = operation.get('last_amendment')
    updates = []
    if latest:
        revisions = operation.get('plan_revisions', [])
        if not revisions or revisions[-1].get('amendment_digest') != latest.get('digest'):
            raise ValueError('会话决定不属于当前清理修订')
        updates.append(latest.get('conversation_decision', latest['request']))
    if amendment:
        updates.append(amendment)
    for update in updates:
        if update.get('require_complete_conversation'):
            result['require_complete_conversation'] = True
        if 'accept_conversation_digest' in update:
            result['accept_conversation_digest'] = update['accept_conversation_digest']
            result['decision_ref'] = update.get('decision_ref')
    return result


def artifact_manifest(base, task, plan):
    from workflow import project_rules, station_archive
    value = {'schema_version': 1, 'run_id': task['run_id'], 'repositories': {name: {'head': value.get('head'), 'entries': [], 'untracked': {}} for name, value in plan['source'].items()},
                       'dispositions': [{'path': e['path'], 'preservation': e['preservation'],
                                         'snapshot': {k: e[k] for k in ('head', 'before', 'before_index', 'index_patch', 'worktree_patch')}}
                                        for e in plan['entries']]}
    return json.dumps(station_archive.redact_evidence(project_rules.load_admission(station=base), value),
                      sort_keys=True, ensure_ascii=False).encode()


def verify_source_choices(plan):
    unresolved = [e['path'] for e in plan['entries'] if e['preservation']['action'] == 'archive']
    if unresolved:
        raise ValueError('任务档案不保存源码正文；请先精确 export 或确认 discard：' + ', '.join(unresolved[:10]))
