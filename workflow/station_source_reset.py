"""清理时归位独立源码；原操作私有区承载中断恢复，不以 Git 失败触发删除。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from workflow import engineering_baseline as baseline, project_rules, station_layout
from workflow import station_cleanup_tree as trees, station_directories as directories
from workflow import station_operation as operations, station_source as source, task_store
from workflow import source_pool
from workflow.git_environment import git_environment


def git_controls(path):
    """归位不得跟随外部 worktree，也不得接管仍持锁的 Git 操作。"""
    if source.git(path, 'config', '--get', 'core.worktree', check=False).stdout.strip():
        raise ValueError('仓库配置了外部 worktree，先核实，不能自动归位')
    if sum(row.startswith('worktree ') for row in source.git(path, 'worktree', 'list', '--porcelain').stdout.splitlines()) != 1:
        raise ValueError('仓库仍关联其它工作树，先核实并解除关联，不能自动归位')
    metadata = path / '.git'
    if any(item.is_symlink() for item in metadata.rglob('*')):
        raise ValueError('Git 元数据含链接，先核实独立存储，不能自动归位')
    if any(metadata.rglob('*.lock')):
        raise ValueError('Git 锁仍存在，保留现场，不能删除兜底')
    if any((metadata / name).exists() for name in ('rebase-apply', 'rebase-merge', 'sequencer')):
        raise ValueError('Git 变基或序列操作尚未结束，先核实原操作')


def object_type(path, sha):
    """batch 的 missing 是确定性对象缺失；访问/损坏/其他错误不当作缺失。"""
    try:
        result = subprocess.run(['git', '-C', str(path), 'cat-file', '--batch-check'],
                                input=sha + '\n', capture_output=True, text=True,
                                env=git_environment(read_only=True), timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('基线对象读取失败或超时，保留现场，不能删除兜底') from error
    if result.returncode or result.stderr.strip():
        raise ValueError('基线对象读取失败，先修复权限或核验 Git 对象，不能删除兜底')
    fields = result.stdout.strip().split()
    if fields == [sha, 'missing']:
        return 'missing'
    if len(fields) == 3 and fields[0] == sha and fields[1] == 'commit':
        return 'commit'
    raise ValueError('基线对象不是已核验 commit，保留现场')


def plan(base, task):
    root = Path(base).resolve() / station_layout.station_name(base, 'repositories')
    catalog = project_rules.load_repository_catalog(station=base)['repositories']
    frozen = (task or {}).get('engineering_baseline', {}).get('repositories', {})
    retained = (task or {}).get('retained_repositories', {})
    repos, extras = [], []
    if not root.exists():
        return {'repositories': repos, 'extras': [trees.entry(base, root.name, recreate=True)]}
    if root.is_symlink() or not root.is_dir():
        raise ValueError('source 根不是独立目录')
    # 只按项目目录识别受管仓库；外层普通材料仍纳入集中处置，不吞掉未知仓库。
    managed = {name: source.repository_path(base, name) for name in catalog}
    existing = {name: path for name, path in managed.items() if path.exists()}
    def visit(folder):
        for child in sorted(folder.iterdir()):
            if child in existing.values():
                continue
            if any(child in path.parents for path in existing.values()):
                visit(child)
            else:
                extras.append(trees.entry(base, child.relative_to(Path(base).resolve()).as_posix()))
    visit(root)
    for name, path in sorted(existing.items()):
        source.identity(path, catalog[name]['origin'])
        git_controls(path)
        entry = frozen.get(name)
        target = entry['commit_sha'] if entry else retained.get(name, {}).get('head')
        reason = '归位旧任务冻结基线' if entry else '归位接管前保留仓库的已记录 HEAD'
        if target is None:
            target = source.git(path, 'rev-parse', 'HEAD').stdout.strip()
            reason = '尚无冻结基线，清理工作区并保留当前已核验 HEAD，不冒充任务基线'
        if not baseline.SHA.fullmatch(target):
            raise ValueError('源码归位缺少完整提交：' + name)
        object_source = 'repository'
        if object_type(path, target) == 'missing':
            cache = source_pool.pool_path(base, name, catalog[name]['origin'])
            object_source = 'missing'
            if cache.exists():
                source_pool.identity(cache, catalog[name]['origin'], source.git)
                if object_type(cache, target) == 'commit':
                    object_source = 'cache'
            if object_source == 'missing':
                # 只取回旧基线的精确对象，不追赶远端分支，也不写工位源码。
                with tempfile.TemporaryDirectory(prefix='ao-source-recovery-') as temporary:
                    source.git(temporary, 'init', '--bare', '--quiet')
                    fetched = source.git(temporary, 'fetch', '--no-tags', '--depth=1',
                                         catalog[name]['origin'], target, check=False)
                    if fetched.returncode == 0 and object_type(temporary, target) == 'commit':
                        object_source = 'remote'
                    elif 'not our ref ' + target not in fetched.stderr:
                        raise ValueError('无法核验精确基线对象的远端恢复；网络、权限或未知错误不允许删除兜底：' + name)
        action = 'delete' if object_source == 'missing' else 'restore'
        if action == 'delete':
            reason = '仓库及本地源码池均缺少归位目标，远端明确拒绝该 SHA（not our ref）；成果处置后仅删除本仓库，下次接管重建'
        repos.append(dict(trees.entry(base, path.relative_to(Path(base).resolve()).as_posix()),
                          repository=name, origin=catalog[name]['origin'], target_sha=target,
                          action=action, object_source=object_source, reason=reason))
    return {'repositories': repos, 'extras': extras}


def stable_snapshot(path):
    """根目录 rename 不改变孩子身份；回执仍检测每个孩子的后续内容变化。"""
    if not path.exists() or path.is_symlink() or not path.is_dir():
        raise ValueError('源码归位恢复目录缺失或被替换')
    return {'identity': trees.identity(path.lstat()),
            'children': {child.name: trees.snapshot(child) for child in sorted(path.iterdir())}}


def restore(base, operation, item):
    path = trees.path_for(base, item['path'])
    if trees.identity(path.parent.lstat()) != item['parent']:
        raise ValueError('源码归位父目录已替换')
    key = 'restore:' + hashlib.sha256(item['path'].encode()).hexdigest()
    private = task_store.state_path(base) / 'cleanup'
    staging = private / operation['operation_id']
    working = staging / key.replace(':', '-')
    for folder in (private, staging):
        if folder.is_symlink() or folder.exists() and not folder.is_dir():
            raise ValueError('源码恢复区不是独立目录')
    step = operation['steps'].get(key)
    if step and step['receipt'] is not None:
        if working.exists() or stable_snapshot(path) != step['receipt']['snapshot']:
            raise ValueError('已归位源码出现新内容，保留并核实，不重复归位')
        return
    if not step:
        if trees.snapshot(path) != item['snapshot']:
            raise ValueError('源码归位范围已变化，重新核实保全及处置决定')
        git_controls(path)
        step = operations.intent(base, operation, key, {}, item)
    if working.exists():
        if working.is_symlink() or trees.identity(working.lstat()) != item['snapshot']['identity']:
            raise ValueError('私有源码恢复对象身份不符')
        if path.exists() or path.is_symlink():
            raise ValueError('原源码路径出现新内容，保留并核实')
    elif step.get('ready_snapshot'):
        if stable_snapshot(path) != step['ready_snapshot']:
            raise ValueError('源码放回后发生变化，保留并核实')
        operations.receipt(base, operation, key, {'snapshot': step['ready_snapshot']})
        return
    else:
        if trees.snapshot(path) != item['snapshot']:
            raise ValueError('待归位源码已变化，保留并核实')
        private.mkdir(mode=0o700, exist_ok=True)
        if any(child.name != operation['operation_id'] for child in private.iterdir()):
            raise ValueError('存在其它清理恢复现场')
        staging.mkdir(mode=0o700, exist_ok=True)
        os.rename(path, working)
        directories.sync(path.parent)
        directories.sync(staging)
    if not step.get('ready_snapshot'):
        source.identity(working, item['origin'])
        git_controls(working)
        if object_type(working, item['target_sha']) != 'commit':
            if item['object_source'] not in ('cache', 'remote'):
                raise ValueError('执行时基线对象已变化，不能转为删除兜底')
            location = item['origin']
            if item['object_source'] == 'cache':
                location = source_pool.pool_path(base, item['repository'], item['origin'])
                source_pool.identity(location, item['origin'], source.git)
                if object_type(location, item['target_sha']) != 'commit':
                    raise ValueError('已核验缓存对象变化，保留恢复区，不能转为删除兜底')
            source.git(working, 'fetch', '--no-tags', str(location), item['target_sha'])
            if object_type(working, item['target_sha']) != 'commit':
                raise ValueError('本地缓存对象取回未通过')
        def git(*args):
            return source.git(working, '-c', 'core.hooksPath=/dev/null', '-c', 'core.sparseCheckout=false', *args)
        # 私有区仅供本操作恢复，先脱离任务分支再 reset，不移动原任务分支的提交。
        git('update-ref', '--no-deref', 'HEAD', item['target_sha'])
        index = working / '.git/index'
        if index.is_symlink():
            raise ValueError('Git 索引为链接，保留现场')
        index.unlink(missing_ok=True)
        git('reset', '--hard', item['target_sha'])
        git('submodule', 'deinit', '--force', '--all')
        git('clean', '-ffdx')
        refs = git('for-each-ref', '--format=%(refname) %(objectname)').stdout.splitlines()
        for row in refs:
            ref, sha = row.split(' ', 1)
            if not ref.startswith('refs/remotes/'):
                git('update-ref', '-d', ref, sha)
        if (git('rev-parse', 'HEAD').stdout.strip() != item['target_sha']
                or source.git(working, 'symbolic-ref', '-q', 'HEAD', check=False).returncode == 0):
            raise ValueError('源码归位 HEAD 不一致')
        if source.git(working, '--no-optional-locks', 'status', '--porcelain', '--ignored', '--untracked-files=all').stdout:
            raise ValueError('源码归位后仍有工作区内容，保留私有恢复区')
        step['ready_snapshot'] = stable_snapshot(working)
        operations.save(base, operation)
    if stable_snapshot(working) != step['ready_snapshot']:
        raise ValueError('私有源码归位完成后内容变化')
    if path.exists() or path.is_symlink():
        raise ValueError('源码原路径出现新内容，不覆盖')
    os.rename(working, path)
    directories.sync(path.parent)
    directories.sync(staging)
    operations.receipt(base, operation, key, {'snapshot': stable_snapshot(path)})


def execute(base, operation, value):
    for item in value['repositories']:
        if item['action'] == 'restore':
            restore(base, operation, item)
        else:
            trees.remove(base, operation, {key: item[key] for key in ('path', 'parent', 'snapshot', 'recreate')})
    for item in value['extras']:
        trees.remove(base, operation, item)
