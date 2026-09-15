"""工位固定独立仓库的准备与只读核验，不使用 linked worktree 或共享对象。"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from workflow import engineering_baseline as baseline, project_rules, station_operation as operations


def git(path, *arguments, check=True):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    result = subprocess.run(["git", "-C", str(path), *arguments], env=environment,
                            text=True, capture_output=True, timeout=120)
    if check and result.returncode:
        raise ValueError("Git 操作失败（%s）：%s" % (arguments[0], result.stderr.strip()))
    return result


def repository_path(workspace, name):
    baseline.repository_id(name)
    root = Path(workspace).resolve()
    path = root / "source" / name
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ValueError("源码路径不是独立真实目录：%s" % current)
    return path


def identity(path, origin):
    metadata = path / ".git"
    if metadata.is_symlink() or not metadata.is_dir():
        raise ValueError("工位源码必须是独立仓库，不能使用 linked worktree")
    if Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != path:
        raise ValueError("源码目录不是仓库根目录")
    common = Path(git(path, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = path / common
    if common.resolve() != metadata:
        raise ValueError("工位仓库不能共享 Git 元数据")
    for part in (metadata / "objects", metadata / "objects/info"):
        if part.is_symlink() or not part.is_dir():
            raise ValueError("工位对象目录必须独立")
    alternate = metadata / "objects/info/alternates"
    if alternate.exists() or alternate.is_symlink():
        raise ValueError("工位仓库不能依赖共享 alternates")
    expected = project_rules.canonical_repository_endpoint(origin)
    for arguments in (("config", "--get-all", "remote.origin.url"),
                      ("remote", "get-url", "--all", "origin"),
                      ("remote", "get-url", "--push", "--all", "origin")):
        urls = git(path, *arguments).stdout.splitlines()
        if len(urls) != 1 or not expected or expected != project_rules.canonical_repository_endpoint(urls[0]):
            raise ValueError("工位仓库 origin 的配置、下载和推送地址必须唯一且与项目目录一致")


def require_clean(path):
    if git(path, "status", "--porcelain", "--untracked-files=all").stdout:
        raise ValueError("源码仓库存在未提交修改，拒绝覆盖：%s" % path)


def prepare_repositories(workspace, catalog, selected, operation):
    """先登记 clone/fetch 意图；失败保留现场，只按同一操作恢复。"""
    observations = {}
    for name in selected:
        path = repository_path(workspace, name)
        origin = catalog[name]["origin"]
        step = "clone:" + name
        recorded = operation["steps"].get(step)
        if not path.exists():
            if recorded and recorded["receipt"] is not None:
                raise ValueError("已准备仓库被移除，拒绝重建冒充恢复")
            operations.intent(workspace, operation, step, {"exists": False}, {"origin": origin})
            path.parent.mkdir(parents=True, exist_ok=True)
            git(path.parent, "clone", "--no-local", "--no-checkout", "--", origin, str(path))
        elif recorded is None:
            identity(path, origin)
            require_clean(path)
            operations.intent(workspace, operation, step, {"exists": True}, {"origin": origin})
        identity(path, origin)
        operations.receipt(workspace, operation, step, {"path": str(path), "origin": origin})
        # --no-checkout 新克隆尚无 index；已有仓库必须在操作开始前洁净。
        fetch_step = "fetch:" + name
        fetch = operations.intent(workspace, operation, fetch_step, {}, {"origin": origin})
        if fetch["receipt"] is None:
            git(path, "fetch", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*", "refs/tags/*:refs/tags/*")
        refs = {}
        for line in git(path, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin/").stdout.splitlines():
            ref, sha = line.split(" ", 1)
            if ref != "refs/remotes/origin/HEAD":
                refs[ref.removeprefix("refs/remotes/origin/")] = sha
        if fetch["receipt"] is not None and fetch["receipt"] != {"refs": refs}:
            raise ValueError("已核验的远端引用发生漂移")
        operations.receipt(workspace, operation, fetch_step, {"refs": refs})
        observations[name] = {"selection": "required", "local": {"status": "available"},
                              "refs": {"verification": "verified"}, "_path": path, "_refs": refs}
    return observations


def checkout_baseline(workspace, value, operation):
    baseline.validate(value)
    for name, entry in value["repositories"].items():
        path = repository_path(workspace, name)
        identity(path, entry["origin"])
        step = "checkout:" + name
        expected = {"sha": entry["commit_sha"], "detached": True}
        recorded = operation["steps"].get(step)
        if recorded and recorded["receipt"] is not None:
            if (git(path, "rev-parse", "HEAD").stdout.strip() != entry["commit_sha"]
                    or git(path, "branch", "--show-current").stdout.strip()):
                raise ValueError("已完成的 checkout 发生漂移")
            require_clean(path)
            continue
        operations.intent(workspace, operation, step, {}, expected)
        clone = operation["steps"]["clone:" + name]
        if clone["before"]["exists"]:
            require_clean(path)
        git(path, "checkout", "--detach", entry["commit_sha"])
        require_clean(path)
        if git(path, "rev-parse", "HEAD").stdout.strip() != entry["commit_sha"]:
            raise ValueError("checkout 回读不一致")
        operations.receipt(workspace, operation, step, expected)


def inspect(workspace, value):
    """核对固定基线对象和实时源码状态，不要求远端分支仍指向历史基线。"""
    baseline.validate(value)
    result = {}
    for name, entry in value["repositories"].items():
        path = repository_path(workspace, name)
        identity(path, entry["origin"])
        if git(path, "cat-file", "-t", entry["commit_sha"]).stdout.strip() != "commit":
            raise ValueError("冻结基线对象缺失")
        result[name] = {"head": git(path, "rev-parse", "HEAD").stdout.strip(),
                        "branch": git(path, "branch", "--show-current").stdout.strip(),
                        "dirty": bool(git(path, "status", "--porcelain", "--untracked-files=all").stdout)}
    return result
