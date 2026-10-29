"""同工位会话占用；不认证身份、不控制原生工具或 Agent 预算。"""
from __future__ import annotations
import hashlib
import json
import math
import os
import re
import time
from workflow import task_store

SESSION_ENV = 'AGENTICOPS_EXECUTION_ID'


def session_id():
    value = os.environ.get(SESSION_ENV, '')
    if not re.fullmatch(r'[a-f0-9]{32}', value):
        raise ValueError('受管写入需要本会话的 AGENTICOPS_EXECUTION_ID（32 位 UUID hex）；生成一次并在本会话后续命令复用，不借用其它会话标识')
    return value


def validate(control):
    if control is None:
        return
    if not isinstance(control, dict) or set(control) != {'session_id', 'executor', 'last_activity_epoch', 'handoff'}:
        raise ValueError('执行占用格式不符，先核实原会话与操作；不能手改占用绕过')
    if not isinstance(control['session_id'], str) or not re.fullmatch(r'[a-f0-9]{32}', control['session_id']):
        raise ValueError('执行会话标识无效')
    if not isinstance(control['executor'], str) or not control['executor'].strip():
        raise ValueError('执行者说明缺失')
    value = control['last_activity_epoch']
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('执行活动时间无效，先核实原会话')
    if control['handoff'] is not None and not isinstance(control['handoff'], dict):
        raise ValueError('执行交接来源无效')


def digest(control):
    return hashlib.sha256(json.dumps(control, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def status(base):
    current = task_store.read_current(base)
    control = current.get('execution_control')
    validate(control)
    if control is None:
        return {'status': 'unclaimed', 'control_digest': digest(None)}
    return {'status': 'occupied', **control, 'control_digest': digest(control),
            'boundary': '仅保护本地会话写入；远端归属由四项 Jira 标记回读核验，不推断在线或闲置'}


def _save(base, current, control):
    validate(control)
    current['execution_control'] = control
    task_store._write_json_atomic(task_store.current_path(base), current)


def enter(base, coordination=True):
    """调用者持有工位锁；首次有效会话自动占用，其它会话须显式交接。"""
    identity = session_id()
    current = task_store.read_current(base)
    control = current.get('execution_control')
    validate(control)
    if coordination and current['current'] is not None:
        from workflow import jira_ownership
        jira_ownership.require(base, current['current']['issue_key'])
    if control is None:
        control = {'session_id': identity, 'executor': os.environ.get('AGENTICOPS_EXECUTOR', 'Agent 会话'),
                   'last_activity_epoch': time.time(), 'handoff': None}
        _save(base, current, control)
    elif control['session_id'] != identity:
        raise ValueError('已有执行接管者：%s（%s）；只读查询 execution status，核实后沿 execution claim 交接，不能借用其会话标识' % (control['executor'], control['session_id']))
    return identity


def touch(base, identity):
    # purge 删除当前状态后不重建；正常任务解绑保留工位占用。
    if not task_store.current_path(base).exists():
        return
    current = task_store.read_current(base)
    control = current.get('execution_control')
    validate(control)
    if control is None or control['session_id'] != identity:
        raise ValueError('执行占用已变化，拒绝旧会话报活')
    control['last_activity_epoch'] = time.time()
    _save(base, current, control)


def release(base, identity):
    """调用者持工位锁，且已完成原清理操作；只释放本会话的空闲工位占用。"""
    current = task_store.read_current(base)
    control = current.get('execution_control')
    validate(control)
    if current['current'] is not None:
        raise ValueError('任务尚未解除，不能释放执行占用')
    if control is None:
        return
    if control['session_id'] != identity:
        raise ValueError('执行占用已经交接，不能释放其它会话')
    current.pop('execution_control', None)
    task_store._write_json_atomic(task_store.current_path(base), current)


def claim(base, executor, expected_control_digest=None, conflict_check_ref=None, decision_ref=None):
    identity = session_id()
    if not isinstance(executor, str) or not executor.strip():
        raise ValueError('接管需要执行者说明')
    with task_store.task_state_lock(base, execution=False):
        current = task_store.read_current(base)
        old = current.get('execution_control')
        validate(old)
        if old and old['session_id'] == identity:
            touch(base, identity)
            return status(base)
        if digest(old) != expected_control_digest:
            raise ValueError('原执行占用已变化，重新核实当前接管者，不能重放旧交接决定')
        handoff = None
        if old:
            if not isinstance(conflict_check_ref, str) or not conflict_check_ref.strip():
                raise ValueError('先核实原执行者已停写及在途操作，提供 conflict-check-ref；本地转移不授予 Jira 任务接管')
            handoff = {'previous_session_id': old['session_id'], 'conflict_check_ref': conflict_check_ref,
                       'decision_ref': decision_ref, 'previous_activity_epoch': old['last_activity_epoch']}
        _save(base, current, {'session_id': identity, 'executor': executor.strip(),
                             'last_activity_epoch': time.time(), 'handoff': handoff})
        return status(base)


def command(args):
    if args.execution_action == 'claim':
        claim(args.dir, args.executor, args.expected_control_digest, args.conflict_check_ref, args.decision_ref)
    if args.execution_action == 'heartbeat':
        with task_store.task_state_lock(args.dir):
            pass
    print(json.dumps(status(args.dir), ensure_ascii=False, indent=2))
    return 0
