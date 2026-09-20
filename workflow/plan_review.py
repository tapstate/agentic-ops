"""结构化方案的有限检查：按项目声明聚合缺项、引用和待决问题。"""
from pathlib import Path, PurePosixPath
import subprocess
from workflow import quality_contract
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


def problems(plan, declaration, ctx, model):
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
    environment = plan.get('environment_readiness', {})
    if isinstance(environment, dict) and isinstance(environment.get('checks'), list):
        errors += ['环境缺项：' + str(row.get('name')) for row in environment['checks']
                   if isinstance(row, dict) and row.get('result') == 'missing']
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
        name, relative, revision = row.get('repository'), row.get('path'), row.get('source_revision')
        repo = repos.get(name, {}) if isinstance(name, str) else {}
        root = repo.get('source_path')
        if not root:
            errors.append('同类实现缺少可核验本地源码：' + str(name)); continue
        if not isinstance(relative, str) or PurePosixPath(relative).is_absolute() or any(p in ('..', '.git') for p in PurePosixPath(relative).parts):
            errors.append('同类实现路径无效：' + str(relative)); continue
        if not isinstance(revision, str) or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
            errors.append('同类实现必须绑定完整 Git SHA'); continue
        result = subprocess.run(['git', '--no-optional-locks', '-C', str(Path(root)), 'cat-file', '-t', revision + ':' + relative],
                                capture_output=True, timeout=30, env=git_environment(read_only=True))
        if result.returncode or result.stdout.strip() != b'blob':
            errors.append('同类实现路径/版本不能解析：' + str(name) + '/' + relative)
    return errors
