"""结构化方案的有限检查：按项目声明聚合缺项、引用和待决问题。"""
from pathlib import PurePosixPath
import subprocess
from workflow import quality_contract
from workflow.engineering_baseline import SHA
from workflow.git_environment import git_environment


def shape(value, schema, path):
    """复用契约叶节点校验；逐字段收集，不在首个缺项处停止。"""
    errors = []
    leaf = {k: v for k, v in schema.items() if k not in ('properties', 'items', 'required', 'additionalProperties')}
    try:
        quality_contract.validate(value, leaf, path=path)
    except ValueError as exc:
        return [str(exc)]
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                errors.append(path + '.' + key + ' 缺失')
        if schema.get('additionalProperties') is False:
            errors += [path + ' 含未声明字段 ' + key for key in value if key not in schema.get('properties', {})]
        for key, child in schema.get('properties', {}).items():
            if key in value:
                errors += shape(value[key], child, path + '.' + key)
    if isinstance(value, list) and 'items' in schema:
        for i, child in enumerate(value):
            errors += shape(child, schema['items'], '%s[%s]' % (path, i))
    return errors


def get(value, path):
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def environment_problems(plan, declaration, ctx, model, rules):
    """按事件保存的项目版本解释环境依赖；实际执行仍由检查项验收。"""
    version = declaration.get('environment_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('环境依赖契约版本无效')
    environment = plan.get('environment_readiness', {})
    rows = environment.get('checks', []) if isinstance(environment, dict) else []
    errors = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get('name'))
        if version == 1:
            if row.get('result') == 'missing':
                errors.append('环境缺项：' + name)
            continue
        dependency = row.get('required_for', 'implementation')
        if dependency not in ('implementation', 'verification'):
            errors.append('环境依赖阶段无效：' + name)
            continue
        if dependency == 'implementation':
            if 'item_ids' in row:
                errors.append('实现环境不能声明延期检查项：' + name)
            if row.get('result') == 'missing':
                errors.append('环境缺项：' + name)
            continue
        ids = row.get('item_ids')
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i.strip() for i in ids)
                or len(ids) != len(set(ids))):
            errors.append('后续验证环境必须关联不重复的检查项：' + name)
            continue
        if not rules:
            errors.append('后续验证环境缺少检查点规则：' + name)
            continue
        from workflow import quality
        points = [point['id'] for point in rules['checkpoints']]
        selection = points.index(rules['selection_checkpoint'])
        for item_id in ids:
            item = model.get('items', {}).get(item_id)
            if not item:
                errors.append('环境引用的检查项不存在：' + item_id)
                continue
            item_plan = item['plan']
            checkpoint = item_plan.get('checkpoint')
            if (item_plan.get('timing') != 'after_fix' or checkpoint not in points
                    or points.index(checkpoint) <= selection):
                errors.append('环境只能延期到方案确认后的验收项：' + item_id)
            elif not quality.item_view(item, rules, ctx)['selected']:
                errors.append('环境引用的验收项尚未选择：' + item_id)
    return errors


def problems(plan, declaration, ctx, model, rules=None):
    errors = []
    for key, schema in declaration['sections'].items():
        if key not in plan:
            errors.append('方案 ' + key + ' 缺失')
        else:
            errors += shape(plan[key], schema, '方案.' + key)
    for key in declaration['nonempty']:
        if not get(plan, key):
            errors.append('方案 ' + key + ' 不能为空')
    # 结构有误也继续检查其它可读区域，便于同一 Q2 决策包列全。
    coverage = declaration['coverage']
    criteria = ctx['facts'].get(coverage['fact'])
    criteria = [criteria] if isinstance(criteria, str) else criteria
    rows = plan.get(coverage['section'], [])
    rows = rows if isinstance(rows, list) else []
    valid = [r for r in rows if isinstance(r, dict)]
    if not isinstance(criteria, list) or not criteria or any(not isinstance(c, str) or not c.strip() for c in criteria):
        errors.append('验收事实必须为明确文本或非空文本列表')
    else:
        mapped = [r.get('criterion') for r in valid]
        for criterion in criteria:
            if criterion not in mapped:
                errors.append('AC 未映射：' + criterion)
        for criterion in mapped:
            if criterion not in criteria:
                errors.append('AC 映射不属于已确认验收事实：' + str(criterion))
    for row in valid:
        ids = row.get('case_ids', [])
        if not isinstance(ids, list) or not ids:
            errors.append('AC 映射缺少用例引用')
            continue
        for item_id in ids:
            item = model.get('items', {}).get(str(item_id))
            if not item or item['plan']['timing'] != 'after_fix':
                errors.append('AC 引用的验收项不存在：' + str(item_id))
            elif row.get('repository') != item['plan']['repository']:
                errors.append('AC 模块与验收项仓库不一致：' + str(item_id))
    repos = ctx['repositories']
    errors += environment_problems(plan, declaration, ctx, model, rules)
    scope = plan.get('scope_rationale', {})
    changes = scope.get('changes', []) if isinstance(scope, dict) else []
    changes = changes if isinstance(changes, list) else []
    for row in valid:
        if not any(isinstance(c, dict) and c.get('repository') == row.get('repository') and c.get('module') == row.get('module') for c in changes):
            errors.append('AC 实现模块未解释修改必要性：' + str(row.get('module')))

    for section in declaration['repository_sections']:
        values = get(plan, section)
        if not isinstance(values, list):
            continue
        for row in values:
            if isinstance(row, dict) and (not isinstance(row.get('repository'), str) or row.get('repository') not in repos):
                errors.append(section + ' 引用未登记仓库：' + str(row.get('repository')))
    dependencies = plan.get(declaration['dependencies'], [])
    if isinstance(dependencies, list):
        declared = {r.get('repository') for r in dependencies if isinstance(r, dict) and isinstance(r.get('repository'), str)}
        errors += ['交付依赖未盘点仓库：' + name for name in repos if name not in declared]
        for row in dependencies:
            if isinstance(row, dict) and isinstance(row.get('depends_on'), list):
                errors += ['交付依赖引用未登记仓库：' + str(name) for name in row['depends_on'] if not isinstance(name, str) or name not in repos]
            if isinstance(row, dict) and row.get('state') == 'unresolved':
                errors.append('交付依赖待研发决定：' + str(row.get('repository')))
    for section in declaration['decision_sections']:
        value = plan.get(section, {})
        if isinstance(value, dict):
            for row in value.get('blocking_inputs', []) if isinstance(value.get('blocking_inputs'), list) else []:
                errors.append(section + ' 待研发决定：' + str(row))
    references = plan.get(declaration['references'], [])
    for row in references if isinstance(references, list) else []:
        if isinstance(row, dict) and row.get('status') == 'not_found' and not row.get('search_scope'):
            errors.append('未发现同类实现时必须提供检索范围')
        if not isinstance(row, dict) or row.get('status') != 'found':
            continue
        relative, revision = row.get('path'), row.get('source_revision')
        if not isinstance(relative, str) or PurePosixPath(relative).is_absolute() or any(p in ('..', '.git') for p in PurePosixPath(relative).parts):
            errors.append('同类实现路径无效：' + str(relative)); continue
        if not isinstance(revision, str) or not SHA.fullmatch(revision):
            errors.append('同类实现必须绑定完整 Git SHA'); continue
    return errors


def source_issues(base, task, rules):
    """仅实时调用：定位来自工位，不来自质量事件；结果不参与持久化或摘要。"""
    from workflow import station_source
    contract = rules.get('plan_contract') or {}
    declaration = contract.get('review') or {}
    plan = task.get('facts', {}).get(contract.get('fact_key'), {})
    rows = plan.get(declaration.get('references'), []) if isinstance(plan, dict) else []
    registered = {r['repository']: r for r in task.get('repositories', [])}
    frozen = task.get('engineering_baseline', {}).get('repositories', {})
    issues = []

    def add(category, index, message, recovery):
        # 不回显未经验证的仓库名、路径或 Git stderr，避免诊断自身泄漏材料。
        issues.append({'category': category, 'reference_index': index, 'message': message,
                       'owner': '研发与 Agent' if category == 'evidence_gap' else '维护侧与研发',
                       'recovery': recovery, 'checkpoint': rules['selection_checkpoint']})

    for index, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict) or row.get('status') != 'found':
            continue
        name, relative, revision = row.get('repository'), row.get('path'), row.get('source_revision')
        if (not isinstance(name, str) or name not in registered or name not in frozen or
                (registered[name].get('worktree') or {}).get('status') != 'prepared'):
            add('evidence_gap', index, '同类实现引用仓库未登记或源码未准备', '核对当前任务仓库并完成源码准备')
            continue
        if (not isinstance(relative, str) or not relative or PurePosixPath(relative).is_absolute() or
                any(p in ('..', '.git') for p in PurePosixPath(relative).parts) or
                not isinstance(revision, str) or not SHA.fullmatch(revision)):
            add('evidence_gap', index, '同类实现必须提供仓库内路径与完整 Git SHA', '修正该项源码引用并重新核验')
            continue
        try:
            root = station_source.repository_path(base, name)
            station_source.identity(root, frozen[name]['origin'])
            result = subprocess.run(['git', '--no-optional-locks', '-C', str(root), 'cat-file', '-t', revision + ':' + relative],
                                    capture_output=True, timeout=30, env=git_environment(read_only=True))
        except (OSError, subprocess.TimeoutExpired):
            add('tool_failure', index, '同类实现源码核验工具不可用或超时', '恢复本地 Git/源码访问后重试；无依赖工作可继续')
            continue
        except (ValueError, KeyError) as error:
            if isinstance(error, ValueError) and str(error).startswith('Git 操作'):
                add('tool_failure', index, '同类实现源码核验工具失败或超时', '恢复本地 Git/源码访问后重试；无依赖工作可继续')
            else:
                add('evidence_gap', index, '同类实现源码目录或仓库身份不符合工位绑定', '核对工位源码和冻结仓库身份；不得改写质量日志')
            continue
        if result.returncode or result.stdout.strip() != b'blob':
            add('evidence_gap', index, '同类实现路径/版本不能解析为源码文件', '核对文件和完整 SHA，补齐可复核源码引用')
    return issues
