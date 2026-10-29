"""复用四项接管标记核验归属；不联网、不提供租约或全局互斥。"""
from workflow import execution, project_rules, station_context, task_store


def inspect(base, issue, snapshot, task=None, *, readback=False):
    from workflow import jira_watermark as markers
    if not readback:
        from workflow import jira_coordination
        jira_coordination.require_retired(base)
    profile = project_rules.load_profile(station=base)
    rules = profile.get('jira', {}).get('takeover_watermark', {})
    if rules.get('schema_version') != 2:
        raise ValueError('Project 尚未配置四项接管标记，不能核验归属')
    site = profile['jira']['site'].rstrip('/')
    identity = snapshot.get('issue', {})
    if (snapshot.get('site') != site or not str(identity.get('id', '')).isdigit()
            or not isinstance(snapshot.get('source_ref'), str) or not snapshot['source_ref'].strip()):
        raise ValueError('归属回读需要同一 Jira site、issue.id 和 source_ref')
    bound = (task or {}).get('facts', {}).get('jira_routing', {}).get('issue_id')
    if bound and str(identity['id']) != bound:
        raise ValueError('Jira 归属回读任务 ID 与当前 run 的准入身份不符')
    fields, _ = markers.issue_from(snapshot, issue)
    values = markers.marker_values(fields, rules, snapshot)
    actor = snapshot.get('current_user', {})
    if (actor.get('active') is not True or not all(isinstance(actor.get(k), str) and actor[k].strip()
            for k in ('accountId', 'displayName', 'source_ref'))):
        raise ValueError('需要同一 Jira 连接的当前登录用户回读')
    mapping = rules['marker_fields']
    populated = sum(value is not None for value in values.values())
    if populated not in (0, 4) and not readback:
        raise ValueError('四项接管标记残缺，先核实并修复，不能推定空闲')
    own = bool(task and values[mapping['station']] == station_context.read_binding(base)['station_id']
               and values[mapping['run_id']] == task['run_id'])
    if populated and not own and not readback:
        handoff = snapshot.get('previous_takeover', {})
        if (handoff.get('observed_fields') != values or not all(
                isinstance(handoff.get(k), str) and handoff[k].strip()
                for k in ('evidence_ref', 'decision_ref', 'conflict_check_ref'))):
            raise ValueError('已有其它接管归属；须核实旧执行者停写、在途操作及人工交接决定')
    return {'site': site, 'issue_id': str(identity['id']), 'session_id': execution.session_id()}


def require(base, issue, *, archival=False):
    from workflow import jira_watermark as markers, jira_coordination
    jira_coordination.require_retired(base)
    task = task_store.read_task(base, issue)
    if not task or task.get('archive_ref') or (not archival and task.get('outcome') in ('completed', 'interrupted')):
        raise ValueError('当前 run 已结束、终止或归档，或任务不存在；历史归属不能恢复执行')
    record = markers.status(base, task)
    identity = record.get('ownership', {})
    site = project_rules.load_profile(station=base).get('jira', {}).get('site', '').rstrip('/')
    if (not markers.summary_ready(base, task) or identity.get('site') != site
            or not str(identity.get('issue_id', '')).isdigit()
            or identity.get('session_id') != execution.session_id()):
        raise ValueError('四项接管标记归属尚未核验；先原生读取并 prepare/complete，暂停依赖归属的写入')


def preflight(base, issue, snapshot, task_class=None):
    from workflow import jira_coordination
    jira_coordination.require_retired(base)
    result = inspect(base, issue, snapshot)
    profile = project_rules.load_profile(station=base)
    fields = snapshot['issue']['fields']
    policy = profile['jira'].get('takeover', {})
    allowed = policy.get('allowed_statuses', [])
    kind = str(fields.get('issuetype', {}).get('id', ''))
    workflow = next((row for row in profile.get('workflows_by_issue_type', [])
                     if str(row.get('issue_type', {}).get('id')) == kind), None)
    if workflow is not None:
        allowed = [row['name'] for row in workflow.get('statuses', [])
                   if row.get('stage') in ('waiting_takeover', 'implementation')]
    if (fields.get('status', {}).get('name') not in allowed
            or fields.get('assignee', {}).get('accountId') != snapshot['current_user']['accountId']):
        raise ValueError('Jira 状态或经办人不满足项目接管准入')
    if task_class is not None:
        from workflow import quality
        rules = quality.config(base, {'task_class': task_class})
        if not rules or rules.get('publication_mode') != 'checkpoint':
            raise ValueError('Project 尚未配置接管评论发布能力，先补齐项目配置；未创建接管操作')
    return result
