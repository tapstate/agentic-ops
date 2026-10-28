"""按项目声明核对测试编写缺口与 PR 回读；不联网、不推断测试语义。"""
import re
from datetime import datetime
from urllib.parse import urlsplit

from workflow import verification


def validate_config(config):
    if isinstance(config, dict) and config.get('mode') == 'jira_manual':
        from workflow import it_manual
        it_manual.validate_config(config)
        return
    if (not isinstance(config, dict) or set(config) != {'issue_min_level', 'issue_when_unsupported'}
            or type(config['issue_min_level']) is not int or config['issue_min_level'] < 0
            or type(config['issue_when_unsupported']) is not bool):
        raise ValueError('测试编写缺口配置无效')


def levels(row):
    value = row.get('level', '')
    if value == 'N/A':
        return []
    if not isinstance(value, str) or not re.fullmatch(r'L(?:0|[1-9]\d*)', value):
        raise ValueError('每行测试级别须为单一 L数字或 N/A；多级按场景分别登记')
    return [int(value[1:])]


def issue_required(row, case, config):
    if config.get('mode') == 'jira_manual':
        return False
    return case.get('status') == 'missing' and (
        any(level >= config['issue_min_level'] for level in levels(row))
        or (config['issue_when_unsupported'] and case.get('framework_supported') is False))


def issue_url(value, repository, host='github.com'):
    if not isinstance(value, str) or not isinstance(repository, str):
        return False
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        return False
    url = urlsplit(value)
    return (url.scheme == 'https' and url.netloc.lower() == host.lower() and not url.query and not url.fragment
            and re.fullmatch('/' + re.escape(repository) + r'/issues/[1-9]\d*', url.path) is not None)


def plan_case_problems(row):
    try:
        rank = levels(row)
        cases = row.get('cases')
        if not isinstance(cases, list) or (bool(cases) != bool(rank)):
            raise ValueError('适用级别必须逐用例登记 cases；N/A 使用空列表')
        seen = set()
        for case in cases:
            if not isinstance(case, dict):
                raise ValueError('用例计划必须为对象')
            key = verification.nonempty(case.get('id'), '用例 id')
            if key in seen:
                raise ValueError('同一场景/级别的用例 id 重复')
            seen.add(key)
            if case.get('change') not in ('reuse', 'modify', 'new'):
                raise ValueError('逐用例 change 必须为 reuse/modify/new')
            verification.nonempty(case.get('reason'), '用例处理依据')
            if case['change'] in ('reuse', 'modify'):
                verification.nonempty(case.get('baseline_ref'), '原有用例及版本依据 baseline_ref')
        return []
    except (ValueError, TypeError, AttributeError) as error:
        return [str(error)]


def case_results(row, result):
    """计划与实绩按用例关联；分类不等于完成，完成不等于执行通过。"""
    planned = {c['id']: c for c in row['cases']}
    actual = result['case_coverage'].get('cases')
    if not isinstance(actual, list) or any(not isinstance(c, dict) for c in actual):
        raise ValueError('缺少逐用例完成和执行情况 cases')
    ids = [verification.nonempty(c.get('id'), '实绩用例 id') for c in actual]
    if len(ids) != len(set(ids)) or set(ids) != set(planned):
        raise ValueError('实绩用例遗漏、重复或不属于本场景/级别计划')
    missing = False
    for case in actual:
        if case.get('status') not in ('written', 'missing'):
            raise ValueError('用例 status 必须为 written/missing')
        verification.nonempty(case.get('report_ref'), '逐用例报告或未执行依据')
        if case.get('result') not in ('PASS', 'FAIL', 'UNKNOWN', 'NOT_RUN', 'SKIPPED'):
            raise ValueError('逐用例执行结果无效')
        if case['status'] == 'missing':
            missing = True
            if case['result'] != 'NOT_RUN':
                raise ValueError('尚未完成的用例只能标记 NOT_RUN')
        else:
            verification.nonempty(case.get('case_ref'), '实际用例位置与版本 case_ref')
            if planned[case['id']]['change'] != 'reuse':
                verification.nonempty(case.get('change_ref'), '新增/修改完成证据 change_ref')
        if result['result'] == 'PASS' and case['result'] != 'PASS':
            raise ValueError('级别 PASS 不能掩盖尚未完成或未通过的用例')
        if result['result'] == 'NOT_RUN' and case['result'] != 'NOT_RUN':
            raise ValueError('级别 NOT_RUN 与逐用例执行结果冲突')
    status = result['case_coverage']['status']
    if (status == 'missing') != missing:
        raise ValueError('编写覆盖结论与逐用例完成情况不一致')
    if result['result'] == 'PASS' and result.get('tests', 0) < len(actual):
        raise ValueError('级别报告计数少于已声明通过用例')


def level_decision(decision, row):
    verification.gap(decision)
    if decision['uncovered'] != row['scope'] or decision.get('level') != row['level']:
        raise ValueError('人工决定必须精确对应当前场景及级别')


def case_problems(row, result, config):
    label = '%s / %s / %s：' % (row['repository'], row['scope'], row['level'])
    try:
        rank = levels(row)
        if result.get('level') != row['level']:
            raise ValueError('结果级别与计划不一致')
        case = result.get('case_coverage')
        if not isinstance(case, dict) or case.get('status') not in ('covered', 'missing', 'not_applicable'):
            raise ValueError('缺少测试编写情况 case_coverage')
        verification.nonempty(case.get('reason'), '编写情况及覆盖依据')
        if type(case.get('framework_supported')) is not bool:
            raise ValueError('必须明确测试框架是否支持')
        current = config.get('mode') == 'jira_manual'
        if current:
            from workflow import it_manual
            if case['framework_supported'] != row.get('framework_supported'):
                raise ValueError('框架支持结论与方案不一致，先更新方案')
            if it_manual.required(row, config):
                if case['status'] != 'missing' or result['result'] != 'NOT_RUN':
                    raise ValueError('手动替代不改写自动化覆盖：必须保留 missing/NOT_RUN')
                case_results(row, result)
                if any(c['status'] != 'missing' for c in case['cases']):
                    raise ValueError('框架不支持的自动化用例必须保留 missing，手动证据复用验收项')
                return []
            if rank and result['result'] == 'FAIL' and result.get('failure_kind') != 'environment':
                raise ValueError('用例断言失败或归因未知须先处理，不能作为环境或 CI 未完成例外')
        if case['status'] == 'covered':
            if not rank:
                raise ValueError('已有覆盖必须有适用级别')
        elif case['status'] == 'not_applicable':
            if rank or result['result'] == 'PASS':
                raise ValueError('不适用必须使用 N/A，不能冒充测试 PASS')
        else:
            if not rank:
                raise ValueError('应写未写不能声明级别不适用')
            if current:
                raise ValueError('框架支持但应写未写，必须补齐自动化用例')
            # 编写决定与执行决定分别保留；即使其它用例 PASS 也不能漏掉编写缺口。
            level_decision(case.get('decision'), row)
            if issue_required(row, case, config):
                follow = case.get('issue') or {}
                if not issue_url(follow.get('url'), follow.get('repository')):
                    raise ValueError('缺少对应 GitHub 仓库的补测 Issue')
        case_results(row, result)
        if 'decision' in result:
            level_decision(result['decision'], row)
        return []
    except (ValueError, TypeError, AttributeError, KeyError) as error:
        return [label + str(error)]


def text(value):
    # 只生成可见纯文本单元格，保留换行含义而不注入 Markdown/HTML。
    value = str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    for char in ('\\', '`', '*', '_', '[', ']', '|'):
        value = value.replace(char, '\\' + char)
    return ' / '.join(value.splitlines())


def case_details(row, coverage):
    actual = {c.get('id'): c for c in coverage.get('cases', []) if isinstance(c, dict)}
    parts = []
    for planned in row.get('cases', []):
        case = actual.get(planned['id'], {})
        parts.append('；'.join(str(value) for value in (
            planned['id'], {'reuse': '复用', 'modify': '修改', 'new': '新增'}.get(planned['change']),
            planned['reason'], planned.get('baseline_ref', ''),
            {'written': '已完成', 'missing': '尚未完成'}.get(case.get('status'), '待核验'),
            case.get('case_ref', ''), case.get('change_ref', ''),
            case.get('result', 'UNKNOWN'), case.get('report_ref', '待核验')) if value))
    return ' / '.join(parts)


def summary(plan, material, version, model=None, policy=None):
    rows = {(r['scope'], r.get('level')): r for r in material.get('results', [])}
    lines = ['### 测试覆盖与编写情况', '', 'AgenticOps 版本 ' + text(version), '',
             '验证代码：' + text(material['head_revision']), '',
             '| 场景 / 级别 | 用例编写与依据 | 执行结果与证据 | 缺口处置与跟进 |',
             '| --- | --- | --- | --- |']
    for row in plan:
        result = rows.get((row['scope'], row['level']), {})
        case = result.get('case_coverage') or {}
        decision = case.get('decision') or {}
        execution = result.get('decision') or {}
        follow = case.get('issue') or {}
        status = {'covered': '已覆盖', 'missing': '应写未写', 'not_applicable': '不适用'}.get(case.get('status'), '待核验')
        cells = [row['scope'] + ' / ' + row['level'],
                 '%s；%s；%s；%s；级别依据：%s；%s' % (status, row['case_strategy'], case.get('reason', ''),
                     case_details(row, case), row['knowledge_ref'], row['rationale']),
                 '%s；%s' % (result.get('result', 'UNKNOWN'), result.get('report_ref', '待核验')),
                 '；'.join('%s：%s' % (label, value) for label, value in (
                     ('未编写原因', decision.get('reason')), ('编写处置', decision.get('follow_up')),
                     ('研发确认', (decision.get('proof') or {}).get('reference')),
                     ('执行缺口原因', execution.get('reason')), ('执行处置', execution.get('follow_up')),
                     ('执行确认', (execution.get('proof') or {}).get('reference'))) if value) or '无待处理缺口']
        cells = [text(cell) for cell in cells]
        if issue_url(follow.get('url'), follow.get('repository')):
            if not policy:
                cells[-1] += '；[补测 Issue](' + follow['url'] + ')'
        if policy:
            from workflow import it_manual
            cells[0] += '；框架' + ('支持' if row.get('framework_supported') else '不支持')
            cells[0] += '；依据：' + text(row.get('framework_ref', '待核验'))
            if it_manual.required(row, policy):
                cells[-1] = it_manual.summary(row, model or {}, policy)
            elif row.get('level') != 'N/A' and result.get('result') != 'PASS':
                cells[-1] += '；测试闭环未完成（' + ('研发已明确处置' if result.get('decision') else '待研发处置') + '）'
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def visible(body):
    if not isinstance(body, str):
        return ''
    # 约定总结作为普通 Markdown 展示，不接受藏在注释或代码块中的副本。
    body = re.sub(r'<!--[\s\S]*?(?:-->|$)', '', body)
    body = re.sub(r'(?m)^\s*(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^\s*\1\s*$|\Z)', '', body)
    return body.replace('\r\n', '\n')


def has_link(body, url):
    return re.search(r'(?<![\w/])' + re.escape(url) + r'(?![\w/#?=&%-])', visible(body)) is not None


def observation(row):
    try:
        verification.nonempty(row.get('source_ref'), '回读来源')
        timestamp = datetime.fromisoformat(row.get('observed_at', '').replace('Z', '+00:00'))
        return timestamp.tzinfo is not None
    except (ValueError, TypeError, AttributeError):
        return False


def publication_problems(task, packet, model, ctx, rules, version):
    contract = rules.get('verification_plan') or {}
    config = contract.get('case_coverage')
    if not config:
        return [], {}
    errors, summaries = [], {}
    from workflow import it_manual
    policy = it_manual.config(rules)
    if policy:
        errors.extend(it_manual.closure_problems(model, ctx, rules))
    plan_errors = verification.plan_problems(rules, ctx)
    if plan_errors:
        return plan_errors, summaries
    observed = {r['repository']: r for r in packet.get('pull_requests', [])}
    issues = [] if policy else packet.get('issues', [])
    if not isinstance(issues, list) or any(not isinstance(i, dict) for i in issues):
        return ['Issue 回读必须为对象列表'], summaries
    by_url = {i.get('url'): i for i in issues if isinstance(i.get('url'), str)}
    if len(by_url) != len(issues):
        errors.append('Issue 回读缺少 URL 或重复；同一 Issue 只维护一个快照')
    for repo in task.get('repositories', []):
        name = repo['repository']
        material = model.get('verification', {}).get(name, {}).get('ci', {}).get('data')
        if not material:
            errors.append(name + ' 缺少当前 CI 测试总结材料')
            continue
        plan = [r for r in verification.plan_rows(rules, ctx) if r['repository'] == name]
        coverage_errors = verification.coverage_problems(material, rules, ctx)
        if coverage_errors:
            errors.extend(coverage_errors)
            continue
        expected = summary(plan, material, version, model, policy)
        summaries[name] = expected
        pr = observed.get(name, {})
        endpoint = repo.get('authorized_endpoint') or 'github.com/' + name
        url = 'https://' + endpoint.split('/', 1)[0] + '/' + name + '/pull/' + str(repo.get('pull_request'))
        if (pr.get('url') != url or str(pr.get('number')) != str(repo.get('pull_request'))
                or pr.get('headRefOid') != ctx['repositories'][name]['live_revision']
                or pr.get('state') != 'OPEN' or not observation(pr)):
            errors.append(name + ' 缺少当前 PR 身份、Head、OPEN 及来源时间回读')
        if expected not in visible(pr.get('body')):
            errors.append(name + ' PR 正文缺少当前测试总结或实际 AgenticOps 版本标识')
        for row in plan:
            result = next((r for r in material['results'] if r['scope'] == row['scope'] and r.get('level') == row['level']), {})
            case = result.get('case_coverage') or {}
            if not issue_required(row, case, config):
                continue
            follow = case.get('issue') or {}
            issue = by_url.get(follow.get('url'), {})
            if (not issue_url(follow.get('url'), follow.get('repository'))
                    or issue.get('repository') != follow.get('repository') or not observation(issue)
                    or not has_link(pr.get('body'), follow.get('url', ''))
                    or not has_link(issue.get('body'), url)):
                errors.append(name + ' / ' + row['scope'] + ' 补测 Issue 与 PR 尚未双向回读核验')
    return errors, summaries
