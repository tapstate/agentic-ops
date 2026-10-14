"""清单对象处理与实际成果核验；不要求任务档案，不复位源码。"""
from __future__ import annotations
import os
from pathlib import Path
import stat
from workflow import station_clean_rules as rules, station_directories as directories, station_source as source


def path_for(base, entry, missing=False):
    parts = entry['path'].split('/')
    if any(part in ('', '.', '..', '.git', '.agenticops') for part in parts) or entry['path'].startswith('/'):
        raise ValueError('清理路径越界')
    path = Path(base).resolve()
    for part in parts:
        path = path / part
        if path.is_symlink() or path.is_mount():
            raise ValueError('清理路径含链接或挂载')
        if path != Path(base).resolve().joinpath(*parts) and not path.is_dir():
            raise ValueError('清理父路径不是目录')
    if rules.identity(path.parent) != entry['parent']:
        raise ValueError('对象父目录身份已变化')
    if not path.exists() and missing:
        return path
    if rules.identity(path) != entry['identity']:
        raise ValueError('对象身份已变化')
    return path


def protected(base, entry):
    if entry.get('repository'):
        repo = source.repository_path(base, entry['repository'])
        source.identity(repo, entry['origin'])
        candidate = repo / entry['relative']
        for parent in (candidate, *candidate.parents):
            if parent == repo:
                break
            marker = parent / '.git'
            if marker.exists() or marker.is_symlink():
                raise ValueError('对象位于嵌套 Git 仓库，保留')
        names = source.git(repo, 'ls-files', '-z').stdout.split('\0')
        names += source.git(repo, 'ls-tree', '-r', '--name-only', '-z', 'HEAD').stdout.split('\0')
        relative = entry['relative']
        if any(n == relative or n.startswith(relative + '/') for n in names if n):
            raise ValueError('对象含 Git 跟踪内容，保留')


def remove(base, entry):
    path = path_for(base, entry, missing=True)
    if not path.exists():
        return {'status': 'absent'}
    protected(base, entry)
    parent = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = os.fstat(parent)
        if (current.st_dev, current.st_ino) != (entry['parent']['device'], entry['parent']['inode']):
            raise ValueError('父目录在打开时变化')
        if entry['type'] == 'directory':
            fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                current = os.fstat(fd)
                if (current.st_dev, current.st_ino) != (entry['identity']['device'], entry['identity']['inode']):
                    raise ValueError('目录在打开时变化')
                directories._walk(fd, current.st_dev)
                protected(base, entry)
                directories._walk(fd, current.st_dev, delete=True)
                os.fsync(fd)
            finally:
                os.close(fd)
            path_for(base, entry)
            os.rmdir(path.name, dir_fd=parent)
        else:
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (entry['identity']['device'], entry['identity']['inode']) or not stat.S_ISREG(current.st_mode):
                raise ValueError('文件在删除前变化')
            os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    return {'status': 'removed'}


def verify_object(base, entry):
    path = path_for(base, entry, missing=True)
    if path.exists():
        raise ValueError('对象仍存在或清理后重新产生，保留并交接')
    return {'status': 'absent'}


def source_directories(repository):
    """只读来源检查供源码保全使用，不赋予目录删除权。"""
    result = {}
    for root, names, _ in os.walk(repository, followlinks=False):
        names[:] = [name for name in names if name != '.git' and not (Path(root) / name).is_symlink()]
        for name in names:
            path = Path(root) / name
            if path.is_mount():
                raise ValueError('源码包含挂载目录')
            result[path.relative_to(repository).as_posix()] = directories.identity(path)
    return result
