"""归档接续材料：确定性 Git 核验与 Jira 回读，不执行提交、推送或网络写入。"""
from __future__ import annotations

import json
from workflow import engineering_baseline as baseline, project_rules, station_source as source

FORMAT = 'agenticops-handoff-v1'


def prepare(base, task, supplied):
    """Agent 提供语义总结；程序补齐代码事实，输出可直接保存的接续正文。"""
    required = {'plan', 'progress', 'remaining', 'environment'}
    if not isinstance(supplied, dict) or not required <= set(supplied) or set(supplied) - required - {'evidence', 'discard_repositories', 'unresolved_operations'}:
        raise ValueError('接续材料需要 plan/progress/remaining/environment，可选 evidence/discard_repositories/unresolved_operations')
    if any(not isinstance(supplied[key], str) or not supplied[key].strip() for key in required):
        raise ValueError('接续方案、实际进度、剩余事项和环境要求必须明确；无缺项也应说明')
    evidence = supplied.get('evidence', [])
    if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
        raise ValueError('evidence 必须为已核实的持久证据引用列表；不能用本机路径冒充持久材料')
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
    if not isinstance(packet, dict) or packet.get('format') != FORMAT or not isinstance(packet.get('repositories'), dict):
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
