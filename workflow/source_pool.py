"""产品根下可丢弃的下载缓存；锁内刷新并向独立工位传输对象。"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile

from workflow import project_rules


def pool_path(workspace, name, origin):
    binding = json.loads((Path(workspace) / ".agenticops/workspace.json").read_text())
    root = Path(binding["product_root"]).resolve()
    endpoint = project_rules.canonical_repository_endpoint(origin)
    if not endpoint:
        raise ValueError("源码池 origin 无效")
    key = hashlib.sha256(endpoint.encode()).hexdigest()
    path = root / ".local/source-pool" / key / name
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError("源码池路径必须是真实目录：%s" % current)
    return path


def identity(path, origin, git):
    if git(path, "rev-parse", "--is-bare-repository").stdout.strip() != "true":
        raise ValueError("源码池必须是独立 bare 仓库")
    if Path(git(path, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve() != path:
        raise ValueError("源码池 Git 元数据路径不符")
    for relative in ("objects", "objects/info"):
        item = path / relative
        if item.is_symlink() or not item.is_dir():
            raise ValueError("源码池对象目录必须独立")
    if (path / "objects/info/alternates").exists() or (path / "objects/info/alternates").is_symlink():
        raise ValueError("源码池不能依赖 alternates")
    expected = project_rules.canonical_repository_endpoint(origin)
    for args in (("config", "--get-all", "remote.origin.url"),
                 ("remote", "get-url", "--all", "origin")):
        urls = git(path, *args).stdout.splitlines()
        if len(urls) != 1 or project_rules.canonical_repository_endpoint(urls[0]) != expected:
            raise ValueError("源码池 origin 与项目目录不符")


@contextmanager
def refreshed(workspace, name, origin, git):
    path = pool_path(workspace, name, origin)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if not path.exists():
            # 只清理本次创建的临时目录；中断不会发布半成品缓存。
            with tempfile.TemporaryDirectory(prefix=".download-", dir=path.parent) as temporary:
                staged = Path(temporary) / "repository"
                git(path.parent, "clone", "--bare", "--no-local", "--", origin, str(staged))
                identity(staged, origin, git)
                staged.rename(path)
        identity(path, origin, git)
        git(path, "fetch", "--prune", "origin", "+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
        # 消费期间继续持锁，避免另一个工位刷新或回收正在传输的对象。
        yield path
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
