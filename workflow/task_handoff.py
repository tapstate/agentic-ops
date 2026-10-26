"""归档接续材料：确定性 Git 核验与 Jira 回读，不执行提交、推送或网络写入。"""
from __future__ import annotations

import json
from workflow import engineering_baseline as baseline, project_rules, station_source as source

FORMAT = 'agenticops-handoff-v2'


def prepare(base, task, supplied):
    """Agent 提供语义总结；程序补齐代码事实，输出可直接保存的接续正文。"""
    required = {'plan', 'progress', 'remaining', 'environment'}
    if not isinstance(supplied, dict) or not required <= set(supplied) or set(supplied) - required - {'evidence', 'discard_repositories', 'unresolved_operations'}:
        raise ValueError('接续材料需要 plan/progress/remaining/environment，可选 evidence/discard_repositories/unresolved_operations')
    if any(not isinstance(supplied[key], str) or not supplied[key].strip() for key in required):
        raise ValueError('接续方案、实际进度、剩余事项和环境要求必须明确；无缺项也应说明')
    evidence = verify_evidence(supplied.get('evidence', []))
    discard = supplied.get('discard_repositories', {})
    bindings = task.get('task_repositories', {})
    if not isinstance(discard, dict) or not set(discard) <= set(bindings) or any(not isinstance(ref, str) or not ref.strip() for ref in discard.values()):
        raise ValueError('放弃代码必须逐仓给出研发明确决定的来源')
    frozen = task.get('engineering_baseline', {})
    baseline.validate(frozen)
    repositories = {}
    for name, binding in sorted(bindings.items()):
        entry = frozen['repositories'][name]
        row = {'baseline_sha': entry['commit_sha'], 'target_branch': binding['target_branch']}
        if name in discard:
            repositories[name] = dict(row, disposition='discarded', decision_ref=discard[name],
                                      guidance='本次代码不作为可恢复成果，按已确认方案重新实施')
            continue
        path = source.repository_path(base, name)
        source.identity(path, entry['origin'])
        source.require_clean(path)
        branch = source.git(path, 'branch', '--show-current').stdout.strip()
        head = source.git(path, 'rev-parse', 'HEAD').stdout.strip()
        if branch != binding['work_branch']:
            raise ValueError('归档源码不在登记工作分支：' + name)
        observed = source.git(path, 'ls-remote', '--refs', 'origin', 'refs/heads/' + branch).stdout.splitlines()
        if observed != [head + '\trefs/heads/' + branch]:
            raise ValueError('需保留代码尚未在对应远端分支核实，请按授权推送并回读：' + name)
        if source.git(path, 'merge-base', '--is-ancestor', entry['commit_sha'], head, check=False).returncode:
            raise ValueError('历史基线不是归档代码祖先：' + name)
        repositories[name] = dict(row, disposition='retained', work_branch=branch, expected_head=head)
    from workflow import external_sync
    pending = external_sync.existing_warnings(base, task)
    unresolved = supplied.get('unresolved_operations', {})
    if not isinstance(unresolved, dict) or any(not isinstance(v, str) or not v.strip() for v in unresolved.values()):
        raise ValueError('unresolved_operations 须按待办 kind 记录原操作、对象及回读线索')
    missing = [row['kind'] for row in pending if row['status'] == 'unknown' and row['kind'] not in unresolved]
    if missing:
        raise ValueError('外部结果未知，先回读或补充可跨工位定位的原操作线索：' + '、'.join(missing))
    packet = {'format': FORMAT, 'issue_key': task['issue_key'], 'task_class': task['task_class'],
              **{key: supplied[key].strip() for key in sorted(required)}, 'evidence': evidence,
              'repositories': repositories, 'pending_external': pending, 'unresolved_operations': unresolved}
    rules = project_rules.load_admission(station=base)
    if project_rules.scan_sensitive(rules, json.dumps(packet, ensure_ascii=False)):
        raise ValueError('接续材料包含敏感内容或本机路径，请脱敏后重试；未输出原文')
    digest = baseline.digest(packet)
    body = ('AgenticOps 任务接续点 ' + digest + '\n\n'
            '以下记录是已核实成果的保存点，不代表任务完成或环境已恢复。'
            '已放弃代码需重新实施；外部结果未知须先回读。\n\n'
            '```json\n' + json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + '\n```')
    profile = project_rules.load_profile(station=base)
    site = profile.get('jira', {}).get('site')
    if not isinstance(site, str) or not site.startswith('https://'):
        raise ValueError('项目缺少可核验 Jira site')
    return {'digest': digest, 'packet': packet, 'body': body, 'site': site.rstrip('/')}


def verify_readback(candidate, receipt):
    """回读来源由 Agent 原生工具提供；核对对象与正文，不认证调用者身份。"""
    from workflow.quality_write import body_matches
    required = {'site', 'issue_key', 'body', 'source_ref'}
    if not isinstance(receipt, dict) or not required <= set(receipt):
        raise ValueError('Jira 回读需要 site/issue_key/body/source_ref 及 comment_id')
    if any(not isinstance(receipt[key], str) or not receipt[key].strip() for key in required):
        raise ValueError('Jira 回读字段必须为非空文本')
    if (receipt['site'] != candidate['site'] or receipt['issue_key'] != candidate['packet']['issue_key']
            or not body_matches(candidate['body'], receipt['body'], receipt.get('body_representation', 'exact-text'))):
        raise ValueError('Jira 回读对象或正文不匹配；先核对原写入，不能重复发送')
    if receipt.get('status') == 'not_sent':
        if set(receipt) - required - {'status', 'decision_ref', 'body_representation'} or not isinstance(receipt.get('decision_ref'), str) or not receipt['decision_ref'].strip():
            raise ValueError('取消仅限明确尚未发送，需要 decision_ref；未知结果不能按未发送处理')
        return {key: receipt[key] for key in ('site', 'issue_key', 'source_ref', 'status', 'decision_ref')}
    if (set(receipt) - required - {'comment_id', 'body_representation'}
            or not isinstance(receipt.get('comment_id'), str) or not receipt['comment_id'].strip()):
        raise ValueError('Jira 回读需要唯一 comment_id')
    return {key: receipt[key] for key in ('site', 'issue_key', 'comment_id', 'source_ref')}


def continuation_input(packet):
    """只提取既有接管入口需要的代码引用，不导入阶段、授权或 PASS。"""
    if not isinstance(packet, dict) or packet.get('format') not in (FORMAT, 'agenticops-handoff-v1') or not isinstance(packet.get('repositories'), dict):
        raise ValueError('接续材料格式不符，先依据当前合同核实并补齐')
    from workflow import task_store
    task_store.validate_issue_key(packet.get('issue_key'))
    result = {}
    for name, row in packet['repositories'].items():
        if not isinstance(row, dict):
            raise ValueError('接续仓库材料必须为对象')
        baseline.repository_id(name)
        if row.get('disposition') == 'retained':
            for key in ('baseline_sha', 'expected_head'):
                if not isinstance(row.get(key), str) or not baseline.SHA.fullmatch(row[key]):
                    raise ValueError('接续提交必须为完整 SHA：' + name)
            baseline.ref_name(row.get('work_branch'))
            result[name] = {key: row[key] for key in ('work_branch', 'baseline_sha', 'expected_head')}
        elif row.get('disposition') != 'discarded':
            raise ValueError('接续源码处置不明确：' + name)
    return result


def verify_evidence(items, expected=None):
    """Agent 原生取回材料，所属功能核对字节；不自行联网或保存材料内容。"""
    import hashlib
    from pathlib import Path
    if not isinstance(items, list):
        raise ValueError('证据回读必须为列表')
    results = []
    for item in items:
        if (not isinstance(item, dict) or set(item) != {'reference', 'source_ref', 'local_file'}
                or any(not isinstance(v, str) or not v.strip() for v in item.values())):
            raise ValueError('必要证据须提供 reference、原生回读 source_ref 和已取回的 local_file，不能仅提供链接')
        path = Path(item['local_file'])
        if path.is_symlink() or not path.is_file():
            raise ValueError('必要证据回读必须为普通文件')
        before = path.stat()
        checksum = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                checksum.update(chunk)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('必要证据在核验过程中变化，重新取回')
        results.append({'reference': item['reference'], 'source_ref': item['source_ref'],
                        'sha256': checksum.hexdigest(), 'size_bytes': after.st_size})
    if len({row['reference'] for row in results}) != len(results):
        raise ValueError('必要证据引用重复')
    if expected is not None:
        if (not isinstance(expected, list) or any(not isinstance(row, dict) for row in expected)
                or len(expected) != len(results)):
            raise ValueError('旧证据缺少内容身份或回读不全，先从项目核实并经研发确认补齐接续材料')
        actual = {row['reference']: (row['sha256'], row['size_bytes']) for row in results}
        wanted = {row.get('reference'): (row.get('sha256'), row.get('size_bytes')) for row in expected}
        if actual != wanted or len(wanted) != len(expected):
            raise ValueError('必要材料与归档内容不一致，不能报告恢复完成')
    return results


def restore(base, packet, expected_digest, expected_run_id, expected_revision, source_ref, decision_ref, evidence):
    """将已核实成果接回当前任务；业务阶段、授权和质量结论由原入口检查。"""
    from workflow import task_store
    if baseline.digest(packet) != expected_digest:
        raise ValueError('接续正文摘要不匹配，先核对 Jira 最新有效决定')
    continuations = continuation_input(packet)
    for key in ('plan', 'progress', 'remaining', 'environment'):
        if not isinstance(packet.get(key), str) or not packet[key].strip():
            raise ValueError('恢复缺少方案、进度、剩余事项或环境说明，先从项目补齐')
    if any(not isinstance(value, str) or not value.strip() for value in (source_ref, decision_ref)):
        raise ValueError('恢复须记录 Jira 来源及已核实最新有效成果的决定')
    verified = verify_evidence(evidence, packet.get('evidence', []))
    rules = project_rules.load_admission(station=base)
    if project_rules.scan_sensitive(rules, json.dumps(packet, ensure_ascii=False)):
        raise ValueError('恢复材料含敏感内容，先脱敏，不保存原文')
    with task_store.task_state_lock(base):
        task = task_store.check_expected_run(base, packet['issue_key'], expected_run_id)
        if task['task_class'] != packet.get('task_class'):
            raise ValueError('恢复任务类型不一致')
        saved = task['facts'].get('handoff_resume')
        if saved and saved.get('digest') == expected_digest:
            return {'status': 'already_restored', 'run_id': task['run_id'], 'stage': task['stage']}
        if saved or task['_revision'] != expected_revision:
            raise ValueError('任务已有恢复决定或 revision 已变化，核实当前成果后再继续')
        for name, row in continuations.items():
            binding = task['task_repositories'].get(name)
            entry = task['engineering_baseline']['repositories'].get(name)
            if (not binding or not entry or binding['work_branch'] != row['work_branch']
                    or entry['commit_sha'] != row['baseline_sha']):
                raise ValueError('先通过正常仓库入口接回归档分支及历史基线：' + name)
            path = source.repository_path(base, name)
            source.identity(path, entry['origin'])
            source.require_clean(path)
            if (source.git(path, 'branch', '--show-current').stdout.strip() != row['work_branch']
                    or source.git(path, 'rev-parse', 'HEAD').stdout.strip() != row['expected_head']):
                raise ValueError('恢复代码与归档 Head 不一致，先核实后续成果：' + name)
        record = {key: packet[key] for key in ('plan', 'progress', 'remaining', 'environment')}
        record.update(digest=expected_digest, source_ref=source_ref, decision_ref=decision_ref,
                      evidence=verified, repositories=packet['repositories'],
                      pending_external=packet.get('pending_external', []),
                      unresolved_operations=packet.get('unresolved_operations', {}))
        if project_rules.scan_sensitive(rules, json.dumps(record, ensure_ascii=False)):
            raise ValueError('恢复来源或证据含敏感内容，先脱敏')
        task['facts']['handoff_resume'] = record
        task['history'].append({'event': 'handoff_restored', 'ts': task_store.now(), 'digest': expected_digest})
        task_store.write_task(base, task)
        return {'status': 'restored', 'run_id': task['run_id'], 'stage': task['stage'],
                'remaining': record['remaining'],
                'guidance': '已接回方案与成果来源；按当前项目合同复用有效决定和验证，只补缺项，不导入旧授权或 PASS'}
