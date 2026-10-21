"""工位持久环境输入：受管路径、旧材料备份与可恢复目录发布。"""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import stat
import uuid

NAME = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')


def path(root, relative):
    root = Path(root).absolute()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise ValueError('配置引用必须为受管相对路径')
    result = root / relative
    if result.resolve() != result or root.resolve() != root:
        raise ValueError('配置路径不得经过符号链接')
    return result


def write(pathname, value):
    pathname = Path(pathname)
    if pathname.is_symlink():
        raise ValueError('配置文件不得为符号链接')
    pathname.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = pathname.with_name('.' + pathname.name + '-' + uuid.uuid4().hex)
    try:
        with tmp.open('x', encoding='utf-8') as out:
            os.chmod(tmp, 0o600)
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, pathname)
    finally:
        tmp.unlink(missing_ok=True)


def read(file):
    return json.loads(Path(file).read_text(encoding='utf-8'))


def inventory(root):
    """核验普通文件树；不跟随链接、挂载或嵌套仓库，不输出文件内容。"""
    root = Path(root)
    result = {}
    items = [root] + (sorted(root.rglob('*')) if root.is_dir() and not root.is_symlink() else [])
    for item in items:
        info = item.lstat()
        if item.is_symlink() or item.is_mount() or item.name == '.git' or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError('配置包含链接、挂载、仓库或特殊对象；保留原件')
        key = item.relative_to(root).as_posix()
        result[key] = hashlib.sha256(item.read_bytes()).hexdigest() if item.is_file() else None
    return result


def private_tree(root):
    for item in [root] + list(root.rglob('*')):
        os.chmod(item, 0o700 if item.is_dir() else 0o600)


class Store:
    def __init__(self, station):
        self.root = path(Path(station), 'config')
        self.environments = path(self.root, 'environments')

    def environment(self, name):
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError('环境名称必须为小写字母、数字和连字符')
        return path(self.environments, name)

    def reference(self, value, name=None, draft=False, secret=False):
        if not isinstance(value, str):
            raise ValueError('配置引用必须为字符串')
        target = path(self.root, value)
        parts = Path(value).parts
        if parts[0] == 'shared':
            tail = parts[1:]
        elif len(parts) > 2 and parts[0] == 'environments' and NAME.fullmatch(parts[1]) and (name is None or parts[1] == name):
            tail = parts[2:]
            if tail[0] == 'drafts':
                if not draft:
                    raise ValueError('正式配置不得依赖草稿')
                tail = tail[1:]
        else:
            raise ValueError('引用不属于本环境或 shared；禁止引用备份和其它环境')
        if not tail or tail[0] not in (('secrets',) if secret else ('components', 'testing', 'secrets')) or len(tail) < 2:
            raise ValueError('引用的配置类别不受支持')
        return target

    def names(self):
        if not self.environments.exists():
            return []
        return sorted(p.name for p in self.environments.iterdir() if NAME.fullmatch(p.name) and path(self.environments, p.name).is_dir() and (p / 'config.json').is_file())

    def recover(self, name):
        target = self.environment(name)
        old = path(self.environments, '.' + name + '-previous')
        ready = path(self.environments, '.' + name + '-ready')
        if old.exists():
            if not target.exists():
                os.replace(old, target)  # 中断发布回滚至原完整组合。
            else:
                inventory(old)
                shutil.rmtree(old)
        if ready.exists():
            inventory(ready)
            shutil.rmtree(ready)

    def publish(self, name, populate):
        """调用方持工位锁；复制、验证候选后交换目录，中断可恢复。"""
        self.recover(name)
        target = self.environment(name)
        ready = path(self.environments, '.' + name + '-ready')
        old = path(self.environments, '.' + name + '-previous')
        self.environments.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            if target.exists():
                inventory(target)
                shutil.copytree(target, ready)
            else:
                ready.mkdir(mode=0o700)
            populate(ready)
            inventory(ready)
            private_tree(ready)
            if target.exists():
                os.replace(target, old)
            os.replace(ready, target)
        except BaseException:
            if old.exists() and not target.exists():
                os.replace(old, target)
            if ready.exists():
                shutil.rmtree(ready)
            raise
        if old.exists():
            shutil.rmtree(old)

    def backup_legacy(self):
        """完整副本核验后隔离原件；重入复用未完成批次，不转换。"""
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        pending = path(self.root, '.backup-pending.json')
        if pending.exists():
            record = read(pending)
            destination = path(self.root, 'backups/' + record['id'])
        else:
            entries = [p for p in sorted(self.root.iterdir()) if p.name not in ('environments', 'shared', 'backups', '.lock', '.backup-pending.json')]
            if not entries:
                return None
            if any(p.name == '.complete.json' for p in entries):
                raise ValueError('旧配置含备份保留名称，保留原件并核验归属')
            record = {'id': uuid.uuid4().hex, 'entries': {p.name: inventory(p) for p in entries}}
            destination = path(self.root, 'backups/' + record['id'])
            destination.mkdir(parents=True, mode=0o700)
            for entry in entries:
                dest = destination / entry.name
                if entry.is_dir():
                    shutil.copytree(entry, dest)
                else:
                    shutil.copyfile(entry, dest)
                if inventory(dest) != record['entries'][entry.name]:
                    raise ValueError('配置备份核验失败，保留原件')
            private_tree(destination)
            write(pending, record)
        for name, expected in record['entries'].items():
            source = path(self.root, name)
            dest = path(destination, name)
            if inventory(dest) != expected:
                raise ValueError('备份已变化，保留原件并停止隔离')
            if source.exists():
                observed = inventory(source)
                if any(key not in expected or expected[key] != val for key, val in observed.items()):
                    raise ValueError('原配置已变化，保留双方材料')
                # 每个文件删除前再核验，失败保留剩余材料和原批次。
                for rel, checksum in sorted(expected.items(), key=lambda row: len(Path(row[0]).parts), reverse=True):
                    item = source if rel == '.' else path(source, rel)
                    if not item.exists():
                        continue
                    if checksum is not None:
                        if hashlib.sha256(item.read_bytes()).hexdigest() != checksum:
                            raise ValueError('原配置已变化，停止隔离')
                        item.unlink()
                    else:
                        item.rmdir()
        write(destination / ".complete.json", {"schema_version": 1})
        pending.unlink()
        return str(destination)


def references(station, relative):
    """从普通配置文本核验共享引用；无法读取即保留，不扫描秘密内容。"""
    store = Store(station)
    hits = []
    for root in (store.environments, path(store.root, 'shared')):
        if not root.exists():
            continue
        inventory(root)
        for file in root.rglob('*'):
            if not file.is_file() or 'secrets' in file.relative_to(root).parts:
                continue
            text = file.read_text(encoding='utf-8')
            if relative in text:
                hits.append(file.relative_to(store.root).as_posix())
    return hits


def check_clear(station, selected, resource_paths=()):
    store = Store(station)
    for value in selected:
        if not value.startswith('config/'):
            continue
        relative = value[len('config/'):]
        target = path(store.root, relative)
        if relative in ('environments', 'shared', 'backups'):
            raise ValueError('请明确选择环境、共享对象或备份批次，不整体删除配置分类')
        if relative.startswith('shared/'):
            if references(station, relative):
                raise ValueError('共享配置仍被正式配置或草稿引用，保留')
        if relative.startswith('environments/'):
            for resource in resource_paths:
                active = path(Path(station), resource) / 'active.json'
                if active.exists() and read(active).get('environment') == Path(relative).parts[1]:
                    raise ValueError('配置属于活动环境，先处理运行现场')
        if target.exists():
            inventory(target)
