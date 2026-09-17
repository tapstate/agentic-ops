#!/usr/bin/env python3
"""Product Root 共享仓库生命周期；显式准备/快进更新，只读状态，无任务语义。"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow import source_pool
from workflow.project_rules import canonical_repository_endpoint

MARKER = "agenticops.sharedRepository"
GIT_LOCAL_TIMEOUT = 45
GIT_NETWORK_TIMEOUT = 1800


def git(path, *args, check=True):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1",
               GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    command = ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.fsmonitor=false",
               "-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "submodule.recurse=false",
               "-c", "core.attributesFile=" + os.devnull, "-C", str(path), *args]
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        timeout = GIT_NETWORK_TIMEOUT if args and args[0] in ("clone", "fetch") else GIT_LOCAL_TIMEOUT
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ValueError("Git 操作超时；先 status 核验，不自动重试") from error
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise ValueError("Git 操作失败：%s（不保存远端原始日志）" % args[0])
    return result


def directory(root, relative):
    path = Path(root).resolve()
    for part in relative.split("/"):
        if part in ("", ".", ".."):
            raise ValueError("共享路径无效")
        path /= part
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError("共享路径必须为真实目录")
    return path


def specification(root, name):
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("共享仓库必须使用 owner/repo")
    value = json.loads((Path(root) / "bootstrap/shared-repositories.json").read_text())
    if not isinstance(value, dict) or set(value) != {"schema_version", "repositories"} or value["schema_version"] != 1 or not isinstance(value["repositories"], dict):
        raise ValueError("共享仓库登记版本不兼容")
    entry = value["repositories"].get(name)
    if not isinstance(entry, dict) or set(entry) != {"origin", "branch"}:
        raise ValueError("共享仓库未登记或配置无效")
    if not isinstance(entry["origin"], str) or not canonical_repository_endpoint(entry["origin"]):
        raise ValueError("共享仓库来源无效")
    branch = entry["branch"]
    if not isinstance(branch, str) or branch.startswith("-") or git(root, "check-ref-format", "--branch", branch, check=False).returncode:
        raise ValueError("共享仓库分支无效")
    return entry


@contextmanager
def locked(path, create=False):
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    descriptor = os.open(str(path) + ".lock", flags, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("共享仓库正在管理中，请稍后重试") from error
        yield
    finally:
        os.close(descriptor)


def inspect(path, name, entry):
    directory(path, ".git/objects/info")
    directory(path, ".git/objects/pack")
    if not (path / ".git/objects/info").is_dir():
        raise ValueError("缺少独立 Git 仓库")
    for relative in ("objects/info/alternates", "shallow", "index.lock", "MERGE_HEAD", "CHERRY_PICK_HEAD",
                     "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer"):
        item = path / ".git" / relative
        if item.exists() or item.is_symlink():
            raise ValueError("共享仓库有未完成 Git 操作或共享/不完整对象")
    for relative in ("config", "HEAD", "index", "commondir"):
        if (path / ".git" / relative).is_symlink():
            raise ValueError("Git 元数据不能是符号链接")
    if git(path, "config", "--local", "--get", MARKER, check=False).stdout.strip() != "1:" + name:
        raise ValueError("已有目录不属于本共享仓库管理器，拒绝采用")
    if git(path, "config", "--get-regexp", r"^(filter\.|include|core\.worktree|extensions\.)", check=False).returncode == 0:
        raise ValueError("共享仓库含外部过滤器、配置引用或不支持的扩展")
    if git(path, "rev-parse", "--is-bare-repository").stdout.strip() != "false":
        raise ValueError("共享副本不能是 bare 仓库")
    if Path(git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != path:
        raise ValueError("共享仓库根不一致")
    if Path(git(path, "rev-parse", "--absolute-git-dir").stdout.strip()).resolve() != path / ".git":
        raise ValueError("共享仓库 Git 目录不一致")
    common = Path(git(path, "rev-parse", "--git-common-dir").stdout.strip())
    if (common if common.is_absolute() else path / common).resolve() != path / ".git":
        raise ValueError("共享仓库不能依赖其它 Git 元数据")
    expected = canonical_repository_endpoint(entry["origin"])
    for args in (("config", "--get-all", "remote.origin.url"), ("remote", "get-url", "--all", "origin"),
                 ("remote", "get-url", "--push", "--all", "origin")):
        urls = git(path, *args).stdout.splitlines()
        if len(urls) != 1 or canonical_repository_endpoint(urls[0]) != expected:
            raise ValueError("共享仓库 origin 不符")
    if git(path, "branch", "--show-current").stdout.strip() != entry["branch"]:
        raise ValueError("共享仓库分支不符")
    if git(path, "status", "--porcelain", "--untracked-files=all").stdout:
        raise ValueError("共享仓库有本地修改，需研发处理")
    head = git(path, "rev-parse", "HEAD^{commit}").stdout.strip()
    target = git(path, "rev-parse", "refs/remotes/origin/" + entry["branch"] + "^{commit}").stdout.strip()
    if git(path, "merge-base", "--is-ancestor", head, target, check=False).returncode:
        raise ValueError("共享仓库超前或分叉，拒绝覆盖")
    return {"status": "ready", "repository": name, "path": str(path),
            "branch": entry["branch"], "relation": "equal" if head == target else "behind"}


def run(root, name, action, pool_root):
    if action not in ("ensure", "update", "status"):
        raise ValueError("不支持的共享仓库操作")
    root = Path(root).resolve()
    pool_root = Path(pool_root).expanduser().resolve()
    entry = specification(root, name)
    path = directory(pool_root, "shared-repositories/" + name)
    with locked(path, create=action == "ensure"):
        if action == "status" or (action == "ensure" and path.exists()):
            return inspect(path, name, entry)
        if action == "update":
            inspect(path, name, entry)
        with source_pool.refreshed_at_root(pool_root, name, entry["origin"], git) as pool:
            target = git(pool, "rev-parse", "refs/heads/" + entry["branch"] + "^{commit}").stdout.strip()
            if action == "ensure":
                with tempfile.TemporaryDirectory(prefix=".prepare-", dir=path.parent) as temporary:
                    staged = Path(temporary) / "repository"
                    git(path.parent, "clone", "--no-local", "--no-checkout", "--template=", "--", str(pool), str(staged))
                    git(staged, "remote", "set-url", "origin", entry["origin"])
                    git(staged, "config", MARKER, "1:" + name)
                    git(staged, "config", "gc.auto", "0")
                    git(staged, "config", "maintenance.auto", "false")
                    git(staged, "checkout", "-B", entry["branch"], target)
                    inspect(staged, name, entry)
                    staged.rename(path)
            else:
                # 从缓存只传输对象/目标引用；origin 仍保留真实源库。
                git(path, "fetch", "--no-tags", "--no-write-fetch-head", str(pool),
                    "refs/heads/" + entry["branch"] + ":refs/remotes/origin/" + entry["branch"])
                inspect(path, name, entry)
                git(path, "merge", "--ff-only", "--no-edit", target)
        return inspect(path, name, entry)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "update", "status"))
    parser.add_argument("--product-root", default=str(ROOT))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--source-pool", required=True)
    args = parser.parse_args()
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(args.product_root, args.repository, args.action, args.source_pool)
    except KeyboardInterrupt:
        print(json.dumps({"status": "unavailable", "reason": "操作已中断；先 status 核验，必要时由研发修复"}, ensure_ascii=False))
        return 130
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "unavailable", "reason": str(error)}, ensure_ascii=False))
        return 2
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
