#!/usr/bin/env python3
"""以工作空间目录 FD 为锚的生成产物访问。"""
from __future__ import annotations

import errno
import json
import os
import stat
import uuid
from pathlib import Path


def _relative_parts(relative):
    path = Path(relative)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ValueError("工作空间产物路径越界：%s" % relative)
    return tuple(path.parts)


def workspace_artifact_path(workspace, relative, allow_final_symlink=False):
    """返回仅用于展示的绝对路径，并拒绝当前已存在的 symlink 父目录。

    实际读写必须使用 :class:`WorkspaceDirectory`；这里保留给无副作用的路径展示和
    兼容调用，不能作为写入前的安全证明。
    """
    root = Path(workspace).resolve()
    parts = _relative_parts(relative)
    current = root
    for index, part in enumerate(parts):
        current = current / part
        final = index == len(parts) - 1
        if current.is_symlink() and not (final and allow_final_symlink):
            if final:
                raise ValueError("工作空间普通产物不能是符号链接：%s" % current)
            raise ValueError("工作空间产物父目录不能是符号链接：%s" % current)
        if not final and current.exists() and not current.is_dir():
            raise ValueError("工作空间产物父路径不是目录：%s" % current)
    return root.joinpath(*parts)


class WorkspaceDirectory:
    """在一次生命周期操作中持有 workspace 与父目录 FD。

    父目录通过 openat + O_NOFOLLOW 逐级打开。最终写、替换、接线和删除全部相对已
    打开的父 FD 执行，因此即使校验后路径被改成外部 symlink，也不会触达外部目录。
    每次副作用前还会复核缓存目录的 inode，检测到路径被替换时失败关闭。
    """

    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self._fds = {}
        self._observed = {}

    def __enter__(self):
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            root_fd = os.open(str(self.root), flags)
        except OSError as error:
            raise ValueError("工作空间目录无法安全打开：%s：%s" % (self.root, error)) from error
        self._fds[()] = root_fd
        return self

    def __exit__(self, _type, _value, _traceback):
        for fd in reversed(list(self._fds.values())):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()
        self._observed.clear()

    def path(self, relative):
        return self.root.joinpath(*_relative_parts(relative))

    @staticmethod
    def _directory_flags():
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return flags

    def _assert_cached_chain(self, parts):
        parent_fd = self._fds[()]
        prefix = ()
        for part in parts:
            prefix = prefix + (part,)
            child_fd = self._fds.get(prefix)
            if child_fd is None:
                raise ValueError("工作空间产物父目录未安全打开：%s" % self.path(Path(*prefix)))
            try:
                current = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
                opened = os.fstat(child_fd)
            except OSError as error:
                raise ValueError("工作空间产物父目录已被替换：%s" % self.path(Path(*prefix))) from error
            if (
                not stat.S_ISDIR(current.st_mode)
                or current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
            ):
                raise ValueError("工作空间产物父目录已被替换：%s" % self.path(Path(*prefix)))
            parent_fd = child_fd

    def _parent(self, relative, create=False):
        parts = _relative_parts(relative)
        parent_parts = parts[:-1]
        parent_fd = self._fds[()]
        prefix = ()
        for part in parent_parts:
            prefix = prefix + (part,)
            child_fd = self._fds.get(prefix)
            if child_fd is None:
                try:
                    child_fd = os.open(part, self._directory_flags(), dir_fd=parent_fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    try:
                        child_fd = os.open(part, self._directory_flags(), dir_fd=parent_fd)
                    except OSError as error:
                        raise ValueError(
                            "工作空间产物父目录无法安全创建：%s" % self.path(Path(*prefix))
                        ) from error
                except OSError as error:
                    if error.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise ValueError(
                            "工作空间产物父目录不能是符号链接或普通文件：%s"
                            % self.path(Path(*prefix))
                        ) from error
                    raise
                opened = os.fstat(child_fd)
                if not stat.S_ISDIR(opened.st_mode):
                    os.close(child_fd)
                    raise ValueError("工作空间产物父路径不是目录：%s" % self.path(Path(*prefix)))
                self._fds[prefix] = child_fd
            parent_fd = child_fd
        self._assert_cached_chain(parent_parts)
        return parent_fd, parts[-1]

    @staticmethod
    def _identity(info):
        if info is None:
            return None
        return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))

    def _raw_lstat(self, relative):
        try:
            parent_fd, leaf = self._parent(relative)
            return os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def lstat(self, relative):
        parts = _relative_parts(relative)
        info = self._raw_lstat(relative)
        self._observed.setdefault(parts, self._identity(info))
        return info

    def _assert_entry_unchanged(self, relative):
        parts = _relative_parts(relative)
        if parts not in self._observed:
            self.lstat(relative)
        current = self._identity(self._raw_lstat(relative))
        if current != self._observed[parts]:
            raise ValueError("工作空间产物在校验后已被替换：%s" % self.path(relative))

    def _refresh_entry(self, relative):
        self._observed[_relative_parts(relative)] = self._identity(self._raw_lstat(relative))

    def exists(self, relative):
        return self.lstat(relative) is not None

    def is_file(self, relative):
        info = self.lstat(relative)
        return info is not None and stat.S_ISREG(info.st_mode)

    def is_dir(self, relative):
        info = self.lstat(relative)
        return info is not None and stat.S_ISDIR(info.st_mode)

    def is_symlink(self, relative):
        info = self.lstat(relative)
        return info is not None and stat.S_ISLNK(info.st_mode)

    def readlink(self, relative):
        self.lstat(relative)
        self._assert_entry_unchanged(relative)
        parent_fd, leaf = self._parent(relative)
        target = os.readlink(leaf, dir_fd=parent_fd)
        self._assert_entry_unchanged(relative)
        return target

    def read_text(self, relative):
        self.lstat(relative)
        self._assert_entry_unchanged(relative)
        parent_fd, leaf = self._parent(relative)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(leaf, flags, dir_fd=parent_fd)
        except OSError as error:
            raise ValueError("工作空间普通产物无法安全读取：%s" % self.path(relative)) from error
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError("工作空间普通产物不是文件：%s" % self.path(relative))
            if self._identity(opened) != self._observed[_relative_parts(relative)]:
                raise ValueError("工作空间产物在校验后已被替换：%s" % self.path(relative))
            with os.fdopen(fd, "r", encoding="utf-8") as stream:
                fd = -1
                return stream.read()
        finally:
            if fd >= 0:
                os.close(fd)

    def read_json(self, relative, label):
        try:
            return json.loads(self.read_text(relative))
        except (ValueError, json.JSONDecodeError) as error:
            raise ValueError("%s无法读取：%s" % (label, error)) from error

    def write_text_atomic(self, relative, content, mode=0o600):
        self.lstat(relative)
        parent_fd, leaf = self._parent(relative, create=True)
        temporary = ".%s.%s.tmp" % (leaf, uuid.uuid4().hex)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temporary, flags, mode, dir_fd=parent_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                fd = -1
                stream.write(content)
            self._assert_cached_chain(_relative_parts(relative)[:-1])
            self._assert_entry_unchanged(relative)
            os.replace(temporary, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            self._refresh_entry(relative)
        except Exception:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise

    def write_json_atomic(self, relative, document):
        self.write_text_atomic(
            relative,
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )

    def unlink(self, relative, missing_ok=False):
        try:
            self.lstat(relative)
            parent_fd, leaf = self._parent(relative)
            self._assert_cached_chain(_relative_parts(relative)[:-1])
            self._assert_entry_unchanged(relative)
            os.unlink(leaf, dir_fd=parent_fd)
            self._refresh_entry(relative)
        except FileNotFoundError:
            if not missing_ok:
                raise

    def symlink(self, target, relative):
        self.lstat(relative)
        parent_fd, leaf = self._parent(relative, create=True)
        self._assert_cached_chain(_relative_parts(relative)[:-1])
        self._assert_entry_unchanged(relative)
        os.symlink(target, leaf, dir_fd=parent_fd)
        self._refresh_entry(relative)

    def chmod(self, relative, mode):
        self.lstat(relative)
        self._assert_entry_unchanged(relative)
        parent_fd, leaf = self._parent(relative)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(leaf, flags, dir_fd=parent_fd)
        try:
            if self._identity(os.fstat(fd)) != self._observed[_relative_parts(relative)]:
                raise ValueError("工作空间产物在校验后已被替换：%s" % self.path(relative))
            os.fchmod(fd, mode)
        finally:
            os.close(fd)

    def rmdir_cached(self, relative):
        parts = _relative_parts(relative)
        child_fd = self._fds.get(parts)
        if child_fd is None:
            return False
        parent_fd = self._fds[()] if len(parts) == 1 else self._fds.get(parts[:-1])
        if parent_fd is None:
            return False
        self._assert_cached_chain(parts[:-1])
        try:
            current = os.stat(parts[-1], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        opened = os.fstat(child_fd)
        if current.st_dev != opened.st_dev or current.st_ino != opened.st_ino:
            raise ValueError("工作空间产物父目录已被替换：%s" % self.path(relative))
        try:
            os.rmdir(parts[-1], dir_fd=parent_fd)
            return True
        except OSError:
            return False

    def _open_child_directory(self, parent_fd, name, display):
        try:
            child_fd = os.open(name, self._directory_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise ValueError("工作空间递归删除拒绝跟随目录链接：%s" % display) from error
        try:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(child_fd)
            if (
                not stat.S_ISDIR(current.st_mode)
                or current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
            ):
                raise ValueError("工作空间递归删除目录已被替换：%s" % display)
            return child_fd
        except Exception:
            os.close(child_fd)
            raise

    def _remove_tree_at(self, parent_fd, name, display):
        """相对 parent_fd 删除一个节点；符号链接只删除链接自身。"""
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            os.unlink(name, dir_fd=parent_fd)
            return

        directory_fd = self._open_child_directory(parent_fd, name, display)
        try:
            opened = os.fstat(directory_fd)
            for child in os.listdir(directory_fd):
                self._remove_tree_at(directory_fd, child, display / child)
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(current.st_mode)
                or current.st_dev != opened.st_dev
                or current.st_ino != opened.st_ino
            ):
                raise ValueError("工作空间递归删除目录已被替换：%s" % display)
        finally:
            os.close(directory_fd)
        os.rmdir(name, dir_fd=parent_fd)

    def remove_tree(self, relative, missing_ok=False):
        """完全基于已打开目录 FD 递归删除产物树，不重新解析绝对路径。"""
        try:
            self.lstat(relative)
            parent_fd, leaf = self._parent(relative)
            self._assert_cached_chain(_relative_parts(relative)[:-1])
            self._assert_entry_unchanged(relative)
            self._remove_tree_at(parent_fd, leaf, self.path(relative))
            self._refresh_entry(relative)
        except FileNotFoundError:
            if not missing_ok:
                raise
