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
    from workflow import station_cleanup
    station_cleanup.validate(plan)


def file_action(config, path, scope):
    if path.is_symlink() or not path.is_file():
        return 'unknown'
    return classify(config, path.name, False, scope)['action']
