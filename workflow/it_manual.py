"""IT 手动替代复用质量 item/execute/decide 与 local 材料，不访问 Jira。"""
import re
from urllib.parse import urlsplit

from workflow import verification


def config(rules):
    value = ((rules or {}).get('verification_plan') or {}).get('case_coverage') or {}
    return value if value.get('mode') == 'jira_manual' else None


def validate_config(value):
    fields = {'mode', 'manual_levels', 'assignee_account_id', 'jira_base_url', 'acceptance_checkpoint'}
    if set(value) != fields or not isinstance(value['manual_levels'], list):
        raise ValueError('IT 手动替代项目配置无效')
    for level in value['manual_levels']:
        if not isinstance(level, str) or not re.fullmatch(r'L[1-9]\d*', level):
            raise ValueError('手动替代级别无效')
    for key in fields - {'manual_levels'}:
        verification.nonempty(value[key], key)
    url = urlsplit(value['jira_base_url'])
    if url.scheme != 'https' or not url.netloc or url.path or url.query or url.fragment or url.username:
        raise ValueError('Jira 基址必须是 HTTPS 站点')


def required(row, policy):
    return row.get('level') != 'N/A' and (row.get('level') in policy['manual_levels']
                                       or row.get('framework_supported') is False)


def plan_problems(row, policy):
    errors = []
    if type(row.get('framework_supported')) is not bool or not str(row.get('framework_ref') or '').strip():
        errors.append('缺少 Wiki、方案与源码核验后的框架支持结论及依据')
    if row.get('level') in policy['manual_levels'] and row.get('framework_supported') is not False:
        errors.append('项目当前规定该级别框架不支持，须走手动验证')
    if required(row, policy):
        ids = [c.get('manual_item') for c in row.get('cases', [])]
        if not ids or any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
            errors.append('每个手动用例必须关联唯一的现有质量检查项 manual_item')
    return errors


def jira_observation(value, policy):
    from workflow.test_coverage import observation
    if not isinstance(value, dict):
        return False
    key = value.get('key')
    return (isinstance(key, str) and re.fullmatch(r'[A-Z][A-Z0-9_]*-[1-9]\d*', key)
            and value.get('url') == policy['jira_base_url'] + '/browse/' + key and observation(value))


def followup(model, row):
    entry = model.get('verification', {}).get(row['repository'], {}).get('local', {})
    values = entry.get('data', {}).get('it_followups', [])
    if not isinstance(values, list):
        return None
    found = [v for v in values if isinstance(v, dict) and v.get('scope') == row['scope']
             and v.get('level') == row['level']]
    return found[0] if len(found) == 1 else None


def linked(value, field, key):
    values = value.get(field)
    return isinstance(values, list) and isinstance(key, str) and key in values


def closure_problems(model, ctx, rules, *, planning=False):
    """Q2 只检查意图；Q4 以后核对当前 Manual 执行及 Jira 关联，集中返回缺项。"""
    from workflow import quality
    policy = config(rules)
    if not policy:
        return []
    errors = verification.plan_problems(rules, ctx)
    if errors:
        return errors
    for row in verification.plan_rows(rules, ctx):
        if not required(row, policy):
            continue
        label = '%s / %s / %s：' % (row['repository'], row['scope'], row['level'])
        test_keys = set()
        plans = []
        for case in row['cases']:
            item = model.get('items', {}).get(case['manual_item'])
            if not item:
                errors.append(label + case['id'] + ' 缺少手动质量检查项')
                continue
            plan = item['plan']
            if (plan.get('repository') != row['repository'] or plan.get('method') != 'manual'
                    or plan.get('timing') != 'after_fix' or plan.get('expected_result') != 'PASS'
                    or plan.get('checkpoint') != policy['acceptance_checkpoint']):
                errors.append(label + case['id'] + ' 必须关联本仓 Q4 修复后 Manual/PASS 检查项')
            if planning:
                continue
            plans.append(plan)
            test_keys.add(plan.get('case_ref'))
            view = quality.item_view(item, rules, ctx)
            if not quality.item_accepted(view, outcomes=('accept',)):
                errors.append(label + case['id'] + ' 手动验证尚无当前有效通过确认')
            decision = (view.get('decision') or {}).get('decision', {})
            evidence = next((e for e in view['executions'] if e['id'] == decision.get('evidence_id')), {})
            keys = ('case_ref', 'case_version', 'method', 'repository', 'target_revision')
            if (not quality.exact_commit(plan.get('target_revision', ''))
                    or plan.get('target_revision') != ctx['repositories'][row['repository']]['live_revision']
                    or evidence.get('raw_result') != 'PASS'
                    or any(evidence.get(k) != plan.get(k) for k in keys)):
                errors.append(label + case['id'] + ' 缺少当前代码及用例版本的手动 PASS 证据')
        if planning:
            continue
        entry = model.get('verification', {}).get(row['repository'], {}).get('local')
        if not entry:
            errors.append(label + '缺少本地验证材料及 Jira 回读')
            continue
        errors.extend(verification.material_problems(entry, row['repository'], 'local', ctx, rules,
                                                     check_artifacts=False))
        value = followup(model, row)
        if not value:
            errors.append(label + '缺少唯一的 it_followups 场景/级别回读')
            continue
        task = value.get('task')
        tests = value.get('tests')
        if not isinstance(tests, list) or any(not isinstance(t, dict) for t in tests):
            errors.append(label + '缺少手动测试用例回读列表')
            tests = []
        by_key = {t.get('key'): t for t in tests if isinstance(t.get('key'), str)}
        if len(by_key) != len(tests) or set(by_key) != test_keys:
            errors.append(label + '手动用例回读遗漏、重复或与质量检查项不一致')
        for plan in plans:
            test = by_key.get(plan.get('case_ref'), {})
            if (not jira_observation(test, policy) or test.get('test_type') != 'Manual'
                    or test.get('case_version') != plan.get('case_version')
                    or not linked(test, 'business_issues', ctx.get('issue_key'))):
                errors.append(label + str(plan.get('case_ref')) + ' 缺少有效 Manual 用例版本及原任务关联回读')
            definition = test.get('definition_refs')
            for field in ('goal', 'preconditions', 'steps', 'input_data', 'expected_result', 'cleanup'):
                if (not isinstance(definition, dict) or not isinstance(definition.get(field), str)
                        or not definition[field].strip()):
                    errors.append(label + str(plan.get('case_ref')) + ' 用例定义缺少内容核验来源：' + field)
        if not jira_observation(task, policy):
            errors.append(label + '缺少兼容 Jira 任务身份、来源及时间回读')
            task = task if isinstance(task, dict) else {}
        if task.get('assignee_account_id') != policy['assignee_account_id']:
            errors.append(label + '兼容任务未分派给项目指定负责人 Leon')
        for field in ('reason', 'dedup_ref'):
            if not isinstance(task.get(field), str) or not task[field].strip():
                errors.append(label + '兼容任务缺少 ' + field)
        linked_keys = task.get('manual_tests')
        if not isinstance(linked_keys, list) or any(k not in linked_keys for k in test_keys):
            errors.append(label + '兼容任务未关联全部手动测试用例')
        if not linked(task, 'business_issues', ctx.get('issue_key')):
            errors.append(label + '兼容任务未关联原业务任务')
    return list(dict.fromkeys(errors))


def summary(row, model, policy):
    """只展示真实材料，不从建卡推导通过。"""
    from workflow.test_coverage import text
    value = followup(model, row) or {}
    task = value.get('task') or {}
    parts = ['自动化待补；框架依据：' + text(row.get('framework_ref', '待核验'))]
    for case in row.get('cases', []):
        item = model.get('items', {}).get(case.get('manual_item'), {})
        plan = item.get('plan', {})
        decision = (item.get('decision') or {}).get('decision', {})
        evidence = next((e for e in item.get('executions', []) if e['id'] == decision.get('evidence_id')), {})
        parts.append(text('%s；手动用例 %s；版本 %s；结果 %s；证据 %s' % (
            case['id'], plan.get('case_ref', '待关联'), plan.get('case_version', '待核验'),
            evidence.get('raw_result', 'NOT_RUN'), evidence.get('source_ref', '待核验'))))
        tests = value.get('tests')
        for test in tests if isinstance(tests, list) else []:
            if isinstance(test, dict) and test.get('key') == plan.get('case_ref') and jira_observation(test, policy):
                parts.append('[手动用例 ' + text(test['key']) + '](' + test['url'] + ')')
    if jira_observation(task, policy):
        parts.append('[Jira 兼容任务 ' + text(task['key']) + '](' + task['url'] + ')')
    else:
        parts.append('Jira 兼容任务待核验')
    return '；'.join(parts)
