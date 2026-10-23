"""按已确认目录快照清理；只在原操作私有区继续删除，不触碰后来出现的内容。"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from workflow import station_directories as directories, station_operation as operations, task_store


def identity(info):
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


def path_for(base, relative):
    parts = relative.split('/') if isinstance(relative, str) else []
    if not parts or any(part in ('', '.', '..') for part in parts) or parts[0] == '.agenticops':
        raise ValueError('清理路径越界或属于恢复状态')
    path = Path(base).resolve()
    for part in parts[:-1]:
        path = path / part
        if path.is_symlink() or not path.is_dir() or path.is_mount():
            raise ValueError('清理父路径不是独立目录')
    return path / parts[-1]


def check_mounts(root):
    if root.is_mount():
        raise ValueError('清理范围含挂载，先由 Agent 核实并解除挂载')
    # Linux 同设备 bind mount 不能仅靠 st_dev 或 ismount 识别。
    mounts = Path('/proc/self/mountinfo')
    if mounts.exists():
        for line in mounts.read_text().splitlines():
            target = line.split()[4]
            target = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), target)
            path = Path(target)
            if path == root or root in path.parents:
                raise ValueError('清理范围含挂载，先由 Agent 核实并解除挂载')


def snapshot(path):
    """摘要身份、大小和变更时间；不读取秘密正文，也不创建逐文件台账。"""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    check_mounts(path)
    digest = hashlib.sha256()
    device = path.lstat().st_dev

    def visit(parent, name, relative):
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if before.st_dev != device:
            raise ValueError('清理范围跨设备')
        values = [relative, *identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns]
        digest.update(json.dumps(values, ensure_ascii=True).encode())
        if stat.S_ISDIR(before.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if identity(os.fstat(child)) != identity(before):
                    raise ValueError('盘点期间目录已替换')
                for entry in sorted(os.listdir(child)):
                    visit(child, entry, relative + '/' + entry)
            finally:
                os.close(child)
        elif not (stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)):
            raise ValueError('清理范围含特殊对象，先核实处置')
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (identity(before), before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (identity(after), after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('盘点期间数据变化，协调停写后重新预检')

    parent = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        visit(parent, path.name, '.')
        return {'identity': identity(path.lstat()), 'digest': digest.hexdigest()}
    finally:
        os.close(parent)


def entry(base, relative, recreate=False):
    path = path_for(base, relative)
    return {'path': relative, 'snapshot': snapshot(path), 'parent': identity(path.parent.lstat()), 'recreate': recreate}


def remove(base, operation, item):
    """调用方持工位锁并先停止写入资源；源码保全与删除决定由清理主流程核验。"""
    if not re.fullmatch(r'op-[a-z0-9-]{8,80}', operation['operation_id']):
        raise ValueError('清理操作编号无效')
    target = path_for(base, item['path'])
    if identity(target.parent.lstat()) != item['parent']:
        raise ValueError('清理父目录已替换')
    key = 'tree:' + hashlib.sha256(item['path'].encode()).hexdigest()
    private = task_store.state_path(base) / 'cleanup'
    staging = private / operation['operation_id']
    trash = staging / key.replace(':', '-')
    for folder in (private, staging):
        if folder.is_symlink() or folder.exists() and not folder.is_dir():
            raise ValueError('清理恢复目录不是独立目录')
    step = operation['steps'].get(key)
    if step and step['receipt'] is not None:
        if snapshot(target) != step['receipt']['snapshot'] or trash.exists() or trash.is_symlink():
            raise ValueError('已清理区域出现新内容，保留并交接，不重复删除')
        return
    if not step:
        if snapshot(target) != item['snapshot']:
            raise ValueError('清理范围已变化，重新核实保全及删除决定')
        operations.intent(base, operation, key, {}, item)
    if trash.exists() or trash.is_symlink():
        if item['snapshot'] is None or identity(trash.lstat()) != item['snapshot']['identity']:
            raise ValueError('私有清理对象身份不符')
        if target.exists() or target.is_symlink():
            raise ValueError('原目录出现新内容，保留并交接')
    elif target.exists() or target.is_symlink():
        if snapshot(target) != item['snapshot']:
            # 删除和重建已完成，但写回执前中断；只采用空目录，不删除新对象。
            if item['recreate'] and not target.is_symlink() and target.is_dir() and not any(target.iterdir()):
                operations.receipt(base, operation, key, {'snapshot': snapshot(target)})
                return
            raise ValueError('待清理对象已变化，保留并交接')
        private.mkdir(mode=0o700, exist_ok=True)
        if any(child.name != operation['operation_id'] for child in private.iterdir()):
            raise ValueError('存在其它清理现场，先核实原操作')
        staging.mkdir(mode=0o700, exist_ok=True)
        os.rename(target, trash)
        directories.sync(target.parent)
        directories.sync(staging)
    if trash.exists() or trash.is_symlink():
        check_mounts(trash)
        if trash.is_dir() and not trash.is_symlink():
            descriptor = os.open(str(trash), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                opened = os.fstat(descriptor)
                if identity(opened) != item['snapshot']['identity']:
                    raise ValueError('清理对象打开时被替换')
                directories._walk(descriptor, opened.st_dev, allow_git=True)
                directories._walk(descriptor, opened.st_dev, delete=True, allow_git=True)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            trash.rmdir()
        else:
            trash.unlink()
        directories.sync(staging)
    if target.exists() or target.is_symlink():
        raise ValueError('删除期间原路径重新出现，保留并交接')
    if item['recreate']:
        target.mkdir()
    directories.sync(target.parent)
    operations.receipt(base, operation, key, {'snapshot': snapshot(target)})


def finish(base, operation):
    staging = task_store.state_path(base) / 'cleanup' / operation['operation_id']
    if staging.parent.is_symlink() or staging.is_symlink():
        raise ValueError('清理恢复目录被替换')
    if staging.exists():
        staging.rmdir()  # 只删除已空目录，未知内容保持待处理。
        directories.sync(staging.parent)
    private = staging.parent
    if private.exists():
        private.rmdir()
        directories.sync(private.parent)
