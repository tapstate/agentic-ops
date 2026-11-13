"""源码清理前的只读保全核验；不解释业务文件，不执行远端写入。"""
from __future__ import annotations

import os
import hashlib
import json
import stat
from pathlib import Path

from workflow import project_rules, repair_delivery, repository_delivery, station_clean_rules as rules
from workflow import station_layout, station_source as source, task_store


def backup_inventory(root, independent=False):
    """读取独立副本的实际字节；不跟随链接，不接受特殊文件或外部 Git 对象库。"""
    rows, identities = {}, set()
    for current, names, files in walk(root):
        folder = Path(current)
        for name in sorted(names + files):
            path = folder / name
            relative = path.relative_to(root).as_posix()
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode):
                rows[relative] = ['link', os.readlink(path)]
            elif stat.S_ISDIR(before.st_mode):
                if path.is_mount():
                    raise ValueError('源码副本含挂载，不能证明独立保全')
                rows[relative] = ['directory']
            elif stat.S_ISREG(before.st_mode):
                if independent and before.st_nlink != 1:
                    raise ValueError('源码副本含共享硬链接，不能证明独立保全')
                if relative.endswith('/objects/info/alternates') and path.read_bytes().strip():
                    raise ValueError('源码副本依赖外部 Git 对象库，先形成独立副本')
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                        raise ValueError('源码副本读取期间文件已变化')
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                rows[relative] = ['file', stat.S_IMODE(before.st_mode), before.st_size, digest.hexdigest()]
                identities.add((before.st_dev, before.st_ino))
            else:
                raise ValueError('源码副本含特殊文件，不能证明已保全：' + relative)
            after = path.lstat()
            if (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('源码副本读取期间文件已变化')
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    return digest, identities


def backup_path(base, value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path or not path.is_dir():
        raise ValueError('源码副本必须为无链接的绝对目录')
    station = Path(base).resolve()
    if path == station or station in path.parents or path in station.parents:
        raise ValueError('源码副本必须独立于工位及其清理范围')
    return path


def verified_backup(base, value):
    path = backup_path(base, value)
    root = Path(base).resolve() / station_layout.station_name(base, 'repositories')
    identity = path.stat()
    before, original_files = backup_inventory(root)
    copied, copied_files = backup_inventory(path, independent=True)
    after, _ = backup_inventory(root)
    if before != copied or before != after:
        raise ValueError('源码副本与当前源码不一致，先补齐保全再预检')
    if original_files & copied_files:
        raise ValueError('源码副本与工位共享硬链接，不能证明独立保全')
    return {'path': str(path), 'identity': [identity.st_dev, identity.st_ino], 'digest': copied}


def check_backup(base, record):
    if not record:
        return
    path = backup_path(base, record['path'])
    identity = path.stat()
    if ([identity.st_dev, identity.st_ino] != record['identity']
            or backup_inventory(path, independent=True)[0] != record['digest']):
        raise ValueError('已确认源码副本已变化，保留原清理操作并恢复副本后重试')


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


def assert_isolated(root):
    root = Path(root).resolve()
    found = repositories(root)
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
    return found


def generated(config, name, relative):
    path = Path(relative)
    actions = [rules.classify(config, relative, False, 'repository', name)['action']]
    actions.extend(rules.classify(config, parent.as_posix(), True, 'repository', name)['action']
                   for parent in path.parents if parent != Path('.'))
    return 'preserve' not in actions and 'generated' in actions


def source_preservation(base, config, retained_heads=None):
    root = Path(base).resolve() / station_layout.station_name(base, 'repositories')
    if root.is_symlink():
        raise ValueError('source 根为链接，先核实并修复工位路径，不递归删除目标')
    catalog = project_rules.load_repository_catalog(station=base)['repositories']
    found = assert_isolated(root)
    result = []
    task = task_store.read_task(base) or {}
    from workflow import station_operation
    operation = station_operation.read(base) or {}
    frozen = task.get('engineering_baseline', {}).get('repositories', {})
    retained_heads = retained_heads or {}
    for path in found:
        relative = path.relative_to(root).as_posix()
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
        delivered_ref, delivered_head = None, None
        binding = task.get('task_repositories', {}).get(relative)
        if trusted and binding and repair_delivery.enabled(task):
            work_ref = 'refs/heads/' + binding['work_branch']
            candidate = local.get(work_ref)
            delivery = repository_delivery.current(binding, candidate) if candidate else None
            if delivery and remote.get('refs/pull/%s/head' % delivery['pr']) == candidate:
                delivered_ref, delivered_head = work_ref, candidate
        # 已交付轮次的本地目标分支有时仍停在冻结起点，该对象由源码归位保留。
        frozen_target = ('refs/heads/' + entry['ref_name']
                         if delivered_ref and entry and entry['ref_kind'] == 'branch' else None)
        losses = [key for key, value in local.items() if remote.get(key) != value
                  and (key, value) != (delivered_ref, delivered_head)
                  and not (key in (managed, frozen_target) and value == entry['commit_sha']
                           and (value in remote.values() or retained_heads.get(relative) == value))]
        unchecked = [] if bare else [row[2:] for row in git(path, 'ls-files', '-v', '-z').split('\0')
                                     if row and (row[0].islower() or row[0] == 'S')]
        changed = [] if bare else sorted(set(filter(None, (git(path, 'diff', '--no-ext-diff', '--no-textconv', '--name-only', '-z') + git(path, 'diff', '--no-ext-diff', '--no-textconv', '--cached', '--name-only', '-z')).split('\0'))))
        clone = operation.get('steps', {}).get('clone:' + relative, {})
        if (operation.get('kind') == 'takeover' and operation.get('run_id') == task.get('run_id')
                and clone.get('before', {}).get('exists') is False and clone.get('receipt')
                and not (path / '.git/index').exists() and all(child.name == '.git' for child in path.iterdir())):
            changed = []  # 受管 no-checkout 中间态，没有用户删除源码。
        untracked = [] if bare else list(filter(None, git(path, 'ls-files', '--others', '--exclude-standard', '-z').split('\0')))
        ignored = [] if bare else list(filter(None, git(path, 'ls-files', '--others', '--ignored', '--exclude-standard', '-z').split('\0')))
        unique = [name for name in untracked + ignored if not generated(config, relative, name)]
        head = git(path, 'rev-parse', 'HEAD')
        if head not in remote.values() and head not in local.values() and head != retained_heads.get(relative):
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
            'boundary': '仅供源码保全核验，不授权归位或删除；清理操作另行核验范围与处置决定。远端只核对同名引用及完整 SHA，未核实项由 Agent 与研发处理；归位保留的目标 HEAD 不视为丢失，本地 reflog 和悬空对象不作为持久保全。',
            'requires_decision': bool(unknown or any(row['unpreserved_refs'] or row['changed_files'] or row['unique_files'] or row['unchecked_files'] or not row['remote_verified'] for row in result))}
