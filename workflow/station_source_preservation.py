"""源码清理前的只读保全核验；不解释业务文件，不执行远端写入。"""
from __future__ import annotations

import os
from pathlib import Path

from workflow import project_rules, station_clean_rules as rules
from workflow import station_layout, station_source as source, task_store


def git(path, *args):
    try:
        return source.git(path, '--no-optional-locks', '-c', 'core.fsmonitor=false', *args).stdout.rstrip('\n')
    except (ValueError, OSError):
        raise ValueError('无法核验仓库 %s 的 %s；保留现场，由 Agent 核实 Git 输入后重试' % (path.name, args[0])) from None


def walk(root):
    def failed(error):
        raise ValueError('源码目录不能完整读取，保留现场并修复访问后重试') from None
    return os.walk(root, followlinks=False, onerror=failed)


def repositories(root):
    """识别嵌套仓库但不进入 Git 元数据；链接只作为待清理链接，不跟随。"""
    found = []
    if not root.exists():
        return found
    for current, names, files in walk(root):
        path = Path(current)
        if path.is_mount():
            raise ValueError('源码包含挂载，先由 Agent 核实')
        marker = path / '.git'
        if marker.is_symlink():
            raise ValueError('Git 元数据为链接，先核实独立仓库与外部工作树')
        bare = {'HEAD', 'config'} <= set(files) and {'objects', 'refs'} <= set(names)
        if marker.exists() or bare:
            found.append(path)
        if bare:
            names[:] = []
            continue
        names[:] = [name for name in names if name != '.git' and not (path / name).is_symlink()]
    return found


def generated(config, name, relative):
    path = Path(relative)
    actions = [rules.classify(config, relative, False, 'repository', name)['action']]
    actions.extend(rules.classify(config, parent.as_posix(), True, 'repository', name)['action']
                   for parent in path.parents if parent != Path('.'))
    return 'preserve' not in actions and 'generated' in actions


def source_preservation(base, config):
    root = Path(base).resolve() / station_layout.station_name(base, 'repositories')
    if root.is_symlink():
        raise ValueError('source 根为链接，先核实并修复工位路径，不递归删除目标')
    catalog = project_rules.load_repository_catalog(station=base)['repositories']
    found = repositories(root)
    result = []
    task = task_store.read_task(base) or {}
    frozen = task.get('engineering_baseline', {}).get('repositories', {})
    for path in found:
        relative = path.relative_to(root).as_posix()
        common = Path(git(path, 'rev-parse', '--git-common-dir'))
        common = (path / common).resolve() if not common.is_absolute() else common.resolve()
        if common != root and root not in common.parents:
            raise ValueError('源码关联外部 Git 元数据，先处理关联工作树：' + relative)
        for row in git(path, 'worktree', 'list', '--porcelain').splitlines():
            if row.startswith('worktree '):
                worktree = Path(row[len('worktree '):]).resolve()
                if worktree != root and root not in worktree.parents:
                    raise ValueError('源码 Git 仍被外部工作树使用，先由研发处理：' + relative)
        bare = git(path, 'rev-parse', '--is-bare-repository') == 'true'
        refs = dict(row.split('\t', 1) for row in git(path, 'for-each-ref', '--format=%(refname)\t%(objectname)').splitlines() if row)
        local = {key: value for key, value in refs.items() if not key.startswith('refs/remotes/')}
        remote = {}
        trusted = relative in catalog
        if trusted:
            try:
                source.identity(path, catalog[relative]['origin'])
            except ValueError:
                trusted = False
        remote_error = None
        if trusted:
            # 单次真实远端回读，不能用本地 tracking refs 冒充已经推送。
            try:
                remote = {row.split('\t')[1]: row.split('\t')[0]
                          for row in git(path, 'ls-remote', '--refs', 'origin').splitlines() if row}
            except ValueError:
                trusted = False
                remote_error = '远端回读失败；不能证明已经推送，先核实保全或由研发明确放弃'
        entry = frozen.get(relative)
        managed = 'refs/heads/' + source.baseline_branch(entry) if entry and source.baseline_branch(entry) else None
        losses = [key for key, value in local.items() if remote.get(key) != value
                  and not (key == managed and value == entry['commit_sha'] and value in remote.values())]
        unchecked = [] if bare else [row[2:] for row in git(path, 'ls-files', '-v', '-z').split('\0')
                                     if row and (row[0].islower() or row[0] == 'S')]
        changed = [] if bare else sorted(set(filter(None, (git(path, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z') + git(path, 'diff', '--no-ext-diff', '--no-textconv', '--cached', '--name-only', '-z')).split('\0'))))
        untracked = [] if bare else list(filter(None, git(path, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
        ignored = [] if bare else list(filter(None, git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').split('\0')))
        unique = [name for name in untracked + ignored if not generated(config, relative, name)]
        head = git(path, 'rev-parse', 'HEAD')
        if head not in remote.values() and head not in local.values():
            losses.append('detached HEAD')
        result.append({'path': relative, 'head': head, 'remote_verified': trusted, 'remote_error': remote_error,
                       'unpreserved_refs': losses, 'changed_files': changed, 'unique_files': unique, 'unchecked_files': unchecked,
                       'generated_file_count': len(untracked) + len(ignored) - len(unique)})
    unknown = []
    if root.exists():
        for current, names, files in walk(root):
            path = Path(current)
            if any(path == repo or repo in path.parents for repo in found):
                names[:] = []
                continue
            for name in files + [name for name in names if (path / name).is_symlink()]:
                unknown.append((path / name).relative_to(root).as_posix())
    return {'repositories': result, 'unknown_files': sorted(unknown),
            'boundary': '仅供源码保全核验，不授权删除；现役清理仍保护源码。远端只核对同名引用及完整 SHA，未核实项由 Agent 与研发处理；本地 reflog 和悬空对象随未来源码删除丢弃，不作为持久成果。',
            'requires_decision': bool(unknown or any(row['unpreserved_refs'] or row['changed_files'] or row['unique_files'] or row['unchecked_files'] or not row['remote_verified'] for row in result))}

