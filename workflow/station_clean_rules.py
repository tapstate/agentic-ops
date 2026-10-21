"""两份清单的声明校验、只读匹配和有限范围盘点。"""
from __future__ import annotations
import fnmatch
import json
import os
from pathlib import Path
import stat
from workflow import engineering_baseline as baseline, project_rules, station_layout

SCOPES = {'station-root', 'repository', 'runtime', 'configuration'}
ACTIONS = {'preserve', 'generated', 'configuration'}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('清单包含重复键：' + key)
        value[key] = item
    return value


def validate_layer(value):
    if not isinstance(value, dict) or (not {'version', 'rules'} <= set(value) or set(value) - {'version', 'rules', 'resources'}) or value['version'] != 3 or not isinstance(value['rules'], list):
        raise ValueError('清理清单必须为版本 3；旧工位由原版本退出')
    for row in value['rules']:
        if (not isinstance(row, dict) or not {'scope', 'pattern', 'type', 'action'} <= set(row)
                or set(row) - {'scope', 'pattern', 'type', 'action', 'repository'}
                or row['scope'] not in SCOPES or row['type'] not in ('file', 'directory')
                or row['action'] not in ACTIONS):
            raise ValueError('清理清单对象或动作无效')
        pattern = row['pattern']
        if (not isinstance(pattern, str) or not pattern or pattern.startswith('/')
                or any(p in ('', '.', '..', '.git') for p in pattern.split('/'))
                or any(c in pattern for c in ('\\', '\0', '[', ']', '!'))):
            raise ValueError('清理模式必须为受限相对路径')
        if '.agenticops' in pattern.split('/') and row['action'] != 'preserve':
            raise ValueError('受管状态不能由文件清单删除')
        if row['scope'] != 'repository' and ('/' in pattern or '**' in pattern):
            raise ValueError('只有仓库清单支持相对路径和 **')
        if 'repository' in row:
            if row['scope'] != 'repository':
                raise ValueError('repository 只适用于仓库对象')
            baseline.repository_id(row['repository'])

    if not isinstance(value.get('resources', []), list):
        raise ValueError('资源声明必须为数组')
    for resource in value.get('resources', []):
        if (not isinstance(resource, dict) or set(resource) != {'kind', 'path', 'project_prefix', 'identity_label'}
                or resource['kind'] != 'docker-project'
                or not isinstance(resource['path'], str) or not resource['path'].startswith('runtime/')
                or any(p in ('', '.', '..') for p in resource['path'].split('/'))
                or not all(isinstance(resource[k], str) and resource[k] and
                           all(c.isalnum() or c in '.-_' for c in resource[k]) for k in ('project_prefix', 'identity_label'))):
            raise ValueError('资源声明必须使用固定 Docker 项目类型和工位内路径')


def load(base):
    root, project = project_rules.station_context(base)
    init_path = Path(base) / '.agenticops/init.json'
    if init_path.is_symlink():
        raise ValueError('工位清单不能为链接')
    init = json.loads(init_path.read_text(), object_pairs_hook=_object)
    station = init.get('cleanup')
    if station is None:
        raise ValueError('工位缺少初始化清理清单，请用同版本 repair 刷新')
    path = project_rules.project_root(root, project) / 'station-clean.json'
    if path.is_symlink():
        raise ValueError('项目清单不能为链接')
    project_list = json.loads(path.read_text(), object_pairs_hook=_object) if path.exists() else {'version': 3, 'rules': []}
    layers = [station, project_list]
    for layer in layers:
        validate_layer(layer)
    return {'version': 3, 'layers': layers, 'digests': [baseline.digest(v) for v in layers],
            'structure': station_layout.load(root), 'owned': init.get('artifacts', [])}


def matches(pattern, relative):
    # **/name 同时覆盖根目录和深层目录，普通 * 不跨路径分隔符。
    parts, actual = pattern.split('/'), relative.split('/')
    def match(p, a):
        if not p:
            return not a
        if p[0] == '**':
            return match(p[1:], a) or bool(a) and match(p, a[1:])
        return bool(a) and fnmatch.fnmatchcase(a[0], p[0]) and match(p[1:], a[1:])
    return match(parts, actual)


def classify(config, name, directory=False, scope='station-root', repository=None):
    rows = [r for layer in config['layers'] for r in layer['rules']
            if r['scope'] == scope and r['type'] == ('directory' if directory else 'file')
            and ('repository' not in r or r['repository'] == repository) and matches(r['pattern'], name)]
    actions = {r['action'] for r in rows}
    if 'preserve' in actions:
        return {'action': 'preserve'}
    return {'action': next(iter(actions)) if len(actions) == 1 else 'unknown'}


def identity(path):
    info = path.lstat()
    return {'device': info.st_dev, 'inode': info.st_ino, 'mode': stat.S_IFMT(info.st_mode)}


def inspect(base, owned=(), registered=(), partial=False, config=None):
    """接管只验证核心边界；未知用户文件不成为全局门禁。"""
    config = config or load(base)
    errors, objects = [], {}
    for row in config['structure']['roots']:
        path = Path(base) / row['path']
        if row.get('role') == 'retained-material' and not path.exists() and not path.is_symlink():
            continue
        if path.is_symlink() or not path.is_dir():
            errors.append('工位核心目录缺失或不安全：' + row['path'])
    for path in Path(base).iterdir():
        objects[path.name] = classify(config, path.name, path.is_dir())
    if errors and not partial:
        raise ValueError('；'.join(errors))
    return {**config, 'objects': objects, 'errors': errors}


def validate_snapshot(plan):
    if plan.get('schema_version') != 7:
        raise ValueError('旧清理计划必须由原版本退出')
    from workflow import quality_contract
    quality_contract.validate(plan, 'station-reset.schema.json')
    if baseline.digest({k: v for k, v in plan.items() if k != 'digest'}) != plan.get('digest'):
        raise ValueError('清理范围摘要不一致')


def plan(base, task=None, options=None):
    from workflow import station_source as source, station_resources as resources
    base = Path(base).resolve()
    config = load(base)
    options = options or {}
    selected = options.get('clear_config', [])
    if not isinstance(selected, list) or any(not isinstance(x, str) for x in selected):
        raise ValueError('clear_config 必须为配置相对路径数组')
    if any(p.startswith('/') or any(part in ('', '.', '..', '.git', '.agenticops') for part in p.split('/')) for p in selected):
        raise ValueError('配置选择必须为工位内受限相对路径')
    from workflow.environment_config import check_clear
    check_clear(base, selected, [item['path'] for layer in config['layers'] for item in layer.get('resources', [])])
    candidates, residuals, retained = [], [], []
    catalog = project_rules.load_repository_catalog(station=base)['repositories']
    owned = {r['path'] for r in config['owned']}
    roots = {r['role']: r['path'] for r in config['structure']['roots']}
    from workflow import station_operation
    previous = station_operation.read(base) or {}
    protected_paths = set(previous.get('protected_paths', []))
    if task:
        try:
            protected_paths.update(row['path'] for row in resources.inventory(base, task)
                                   if row.get('kind') == 'source-disposition' and row.get('preservation', {}).get('action') in ('archive', 'export'))
        except (ValueError, OSError):
            # 保全登记不可信时，不删除仓库生成目录；其它独立对象照常处理。
            protected_paths.add(roots['repositories'])
            residuals.append({'path': roots['repositories'], 'reason': '保全登记不可读，保留源码区'})
    def add(path, scope, relative, repository=None, tracked=()):
        local = path.relative_to(base).as_posix()
        try:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or path.is_mount():
                residuals.append({'path': local, 'reason': '链接、挂载或特殊对象保留'})
                return False
            if stat.S_ISDIR(info.st_mode) and ((path / '.git').exists() or (path / '.git').is_symlink()):
                residuals.append({'path': local, 'reason': '嵌套 Git 仓库保留，不使用外层仓库清单处理'})
                return True
            decision = classify(config, relative, stat.S_ISDIR(info.st_mode), scope, repository)['action']
            tracked_here = any(t == relative or t.startswith(relative + '/') for t in tracked)
            if tracked_here and decision in ('generated', 'configuration'):
                retained.append(local)
                return True
            if tracked_here:
                if stat.S_ISREG(info.st_mode):
                    retained.append(local)
                    return True
                return False
            if (local in owned or any(p.startswith(local + '/') for p in owned)
                    or any(local == p or local.startswith(p + '/') or p.startswith(local + '/') for p in protected_paths)):
                decision = 'preserve'
            if decision == 'configuration' and local not in selected:
                decision = 'preserve'
            if decision in ('generated', 'configuration'):
                candidates.append({'path': local, 'identity': identity(path), 'parent': identity(path.parent),
                                   'repository': repository, 'origin': catalog[repository]['origin'] if repository else None, 'relative': relative, 'type': 'directory' if stat.S_ISDIR(info.st_mode) else 'file'})
                return True
            if decision == 'preserve':
                retained.append(local)
                return True
            residuals.append({'path': local, 'reason': '清单未声明，保留'})
            return False
        except OSError as error:
            residuals.append({'path': local, 'reason': type(error).__name__})
            return False
    for path in sorted(base.iterdir()):
        if path.name in roots.values() or path.name in owned:
            continue
        add(path, 'station-root', path.name)
    for role, scope in (('task-runtime', 'runtime'), ('configuration', 'configuration')):
        parent = base / roots[role]
        if parent.is_symlink() or not parent.is_dir():
            residuals.append({'path': roots[role], 'reason': '目录缺失或不安全'})
            continue
        for path in sorted(parent.iterdir()):
            add(path, scope, path.name)
    catalog = project_rules.load_repository_catalog(station=base)['repositories']
    source_root = base / roots['repositories']
    if source_root.is_dir() and not source_root.is_symlink():
        def unknown_sources(parent, prefix=''):
            for child in sorted(parent.iterdir()):
                name = prefix + child.name
                if name in catalog:
                    continue
                if any(repo.startswith(name + '/') for repo in catalog) and child.is_dir() and not child.is_symlink() and not child.is_mount():
                    unknown_sources(child, name + '/')
                else:
                    residuals.append({'path': child.relative_to(base).as_posix(), 'reason': '未声明源码对象，保留'})
        unknown_sources(source_root)
    # 只进入项目声明的独立仓库，不跟随用户新增目录或链接。
    for name, definition in catalog.items():
        path = source_root / name
        try:
            path = source.repository_path(base, name)
            if not path.exists():
                continue
            source.identity(path, definition['origin'])
            tracked = set(filter(None, source.git(path, 'ls-files', '-z').stdout.split('\0')))
            tracked.update(filter(None, source.git(path, 'ls-tree', '-r', '--name-only', '-z', 'HEAD').stdout.split('\0')))
            def walk(parent):
                for child in sorted(parent.iterdir()):
                    if child.name == '.git':
                        continue
                    rel = child.relative_to(path).as_posix()
                    handled = add(child, 'repository', rel, name, tracked)
                    if not handled and child.is_dir() and not child.is_symlink() and not child.is_mount():
                        walk(child)
            walk(path)
        except (ValueError, OSError) as error:
            residuals.append({'path': str(path.relative_to(base)) if 'path' in locals() else name, 'reason': str(error)})
    for requested in selected:
        if requested.startswith('config/') and (base / requested).exists() and not any(c['path'] == requested for c in candidates):
            add(base / requested, 'configuration', Path(requested).name)
        if (base / requested).exists() and not any(c['path'] == requested for c in candidates):
            residuals.append({'path': requested, 'reason': '未匹配可清理配置，保留'})
    value = {'schema_version': 7, 'run_id': task['run_id'] if task else None,
             'config': config, 'protected_paths': sorted(protected_paths), 'objects': candidates, 'retained': sorted(set(retained)), 'residuals': residuals}
    value['digest'] = baseline.digest(value)
    return value


def file_action(config, path, scope):
    if path.is_symlink() or not path.is_file():
        return 'unknown'
    return classify(config, path.name, False, scope)['action']
