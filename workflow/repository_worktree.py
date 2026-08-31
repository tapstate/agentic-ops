#!/usr/bin/env python3
"""Source Pool 仓库校验与任务 linked worktree 生命周期。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import fcntl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bootstrap import repository_pool  # noqa: E402
from workflow import project_rules, task_store  # noqa: E402


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LEASE_SCHEMA_VERSION = 1


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _run(arguments, *, check=True):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=120)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ValueError("命令失败（%s）：%s" % (" ".join(arguments[:4]), detail))
    return result


def _read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("%s无法读取：%s" % (label, error)) from error


def _write_task(workspace, task):
    path = task_store.task_path(workspace, task["issue_key"])
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(str(temporary), str(path))


def _lease_path(product_root):
    return Path(product_root).resolve() / ".local" / "repository-worktrees.json"


def _load_leases(product_root):
    path = _lease_path(product_root)
    if not path.is_file():
        return []
    document = _read_json(path, "Source Pool worktree 租约")
    if document.get("schema_version") != LEASE_SCHEMA_VERSION or not isinstance(
        document.get("leases"), list
    ):
        raise ValueError("Source Pool worktree 租约结构无效：%s" % path)
    return [item for item in document["leases"] if isinstance(item, dict)]


def _write_leases(product_root, leases):
    path = _lease_path(product_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(
            {"schema_version": LEASE_SCHEMA_VERSION, "leases": leases},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(str(temporary), str(path))


@contextmanager
def _pool_lock(product_root):
    root = Path(product_root).resolve() / ".local"
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    with open(root / "repository-pool.lock", "a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _lease_identity(binding, task, item):
    return {
        "pool_root": str(Path(binding["repository_pool"]["root"]).resolve()),
        "repository": item["repository"],
        "workspace_id": binding["workspace_id"],
        "issue_key": task["issue_key"],
        "run_id": task["run_id"],
        "branch": item["work_branch"],
        "path": item["worktree"]["path"],
    }


def _same_lease(left, right):
    keys = ("pool_root", "repository", "workspace_id", "issue_key", "run_id", "branch", "path")
    return all(left.get(key) == right.get(key) for key in keys)


def workspace_binding(workspace):
    path = Path(workspace).resolve() / ".agenticops" / "workspace.json"
    binding = _read_json(path, "工作空间配置")
    if binding.get("schema_version") != 2:
        raise ValueError("工作空间尚未迁移 Source Pool 配置；请执行 agenticops repair")
    workspace_id = binding.get("workspace_id")
    pool = binding.get("repository_pool")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise ValueError("工作空间配置缺少 workspace_id")
    if not isinstance(pool, dict) or not isinstance(pool.get("root"), str):
        raise ValueError("工作空间未配置 Source Pool")
    product_root = Path(binding.get("product_root", "")).resolve()
    root = repository_pool.validate_root(product_root, pool["root"], create=False)
    if not root.is_dir():
        raise ValueError("Source Pool 不存在：%s" % root)
    return binding, product_root, root


def load_task(workspace, issue_key):
    issue = task_store.resolve_issue(workspace, issue_key)
    path = task_store.task_path(workspace, issue)
    if not path.is_file():
        raise ValueError("任务状态缺失：%s" % issue)
    task = _read_json(path, "任务状态")
    if not isinstance(task.get("run_id"), str):
        raise ValueError("任务缺少 run_id；请先执行 task.py reset 迁移本次执行")
    return task


def repository_path(pool_root, repository):
    if not REPOSITORY_PATTERN.fullmatch(repository or ""):
        raise ValueError("仓库必须使用 <owner>/<repo>：%s" % repository)
    owner, name = repository.split("/", 1)
    if owner in (".", "..") or name in (".", ".."):
        raise ValueError("仓库 owner/repo 不能使用相对路径段：%s" % repository)
    path = (pool_root / owner / name).resolve()
    try:
        path.relative_to(pool_root.resolve())
    except ValueError as error:
        raise ValueError("仓库路径越界：%s" % repository) from error
    return path


def task_worktree_path(workspace, task, repository):
    if not REPOSITORY_PATTERN.fullmatch(repository or ""):
        raise ValueError("仓库必须使用 <owner>/<repo>：%s" % repository)
    owner, name = repository.split("/", 1)
    root = Path(workspace).resolve() / ".agenticops" / "worktrees"
    path = (root / task["issue_key"] / task["run_id"] / owner / name).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError("任务 worktree 路径越界：%s" % path) from error
    return path


def task_execution_root(workspace, task):
    return (
        Path(workspace).resolve()
        / ".agenticops"
        / "worktrees"
        / task["issue_key"]
        / task["run_id"]
    ).resolve()


def _normalize_origin(value):
    text = (value or "").strip()
    if text.startswith("/"):
        return str(Path(text).resolve())
    if text.startswith("file://"):
        return "file://" + str(Path(text[7:]).resolve())
    if text.startswith("git@github.com:"):
        text = "github.com/" + text.split(":", 1)[1]
    elif text.startswith("ssh://git@github.com/"):
        text = "github.com/" + text.split("github.com/", 1)[1]
    elif text.startswith("https://github.com/"):
        text = "github.com/" + text.split("github.com/", 1)[1]
    return text[:-4] if text.endswith(".git") else text


def _catalog(workspace):
    return project_rules.load_repository_catalog(workspace=workspace)


def _catalog_digest(workspace):
    path = project_rules.repository_catalog_path(workspace=workspace)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ensure_main(workspace, product_root, pool_root, repository, entry):
    main = repository_path(pool_root, repository)
    if not main.exists():
        mode = repository_pool.load(product_root)["provisioning"]
        if mode != "auto-clone":
            raise ValueError(
                "Source Pool 未接入仓库 %s：期望 %s；请按 owner/repo 布局下载，"
                "或将 provisioning 配置为 auto-clone" % (repository, main)
            )
        main.parent.mkdir(parents=True, exist_ok=True)
        _run(
            [
                "git", "clone", "--branch", entry["baseline_branch"],
                "--single-branch", entry["origin"], str(main),
            ]
        )
    if not main.is_dir():
        raise ValueError("仓库 main 不是目录：%s" % main)
    top = _run(["git", "-C", str(main), "rev-parse", "--show-toplevel"]).stdout.strip()
    if Path(top).resolve() != main.resolve():
        raise ValueError("仓库 main 不是 Git 主工作树根目录：%s" % main)
    actual_origin = _run(
        ["git", "-C", str(main), "config", "--get", "remote.origin.url"]
    ).stdout.strip()
    if _normalize_origin(actual_origin) != _normalize_origin(entry.get("origin")):
        raise ValueError(
            "仓库 origin 不匹配：%s（实际 %s，期望 %s）"
            % (repository, actual_origin, entry.get("origin"))
        )
    dirty = _run(["git", "-C", str(main), "status", "--porcelain"]).stdout.strip()
    if dirty:
        raise ValueError("Source Pool 主工作树存在未提交变更，拒绝同步：%s" % main)
    return main


def _prepare_repository(workspace, task, item, *, reuse_existing_branch=False):
    binding, product_root, pool_root = workspace_binding(workspace)
    catalog = _catalog(workspace)
    entry = catalog.get("repositories", {}).get(item["repository"])
    if entry is None:
        raise ValueError("项目仓库目录未登记：%s" % item["repository"])
    if item.get("base_branch") != entry.get("baseline_branch"):
        raise ValueError(
            "任务基线分支与最新项目仓库目录不一致：%s（任务 %s，目录 %s）"
            % (item["repository"], item.get("base_branch"), entry.get("baseline_branch"))
        )
    main = ensure_main(
        workspace, product_root, pool_root, item["repository"], entry
    )
    current_branch = _run(
        ["git", "-C", str(main), "branch", "--show-current"]
    ).stdout.strip()
    if current_branch != item["base_branch"]:
        raise ValueError(
            "Source Pool 主工作树分支不是任务基线：%s（当前 %s，期望 %s）"
            % (main, current_branch or "detached", item["base_branch"])
        )
    _run(["git", "-C", str(main), "fetch", "--prune", "origin", item["base_branch"]])
    base_ref = "refs/remotes/origin/%s" % item["base_branch"]
    _run(["git", "-C", str(main), "merge", "--ff-only", base_ref])
    base_sha = _run(["git", "-C", str(main), "rev-parse", "--verify", base_ref]).stdout.strip()
    path = task_worktree_path(workspace, task, item["repository"])
    existing = item.get("worktree")
    if existing and existing.get("status") == "prepared":
        if Path(existing.get("path", "")).resolve() != path or not path.is_dir():
            raise ValueError("任务 worktree 记录与磁盘状态不一致：%s" % item["repository"])
        branch = _run(
            ["git", "-C", str(path), "branch", "--show-current"]
        ).stdout.strip()
        if branch != item["work_branch"]:
            raise ValueError("任务 worktree 分支漂移：%s" % path)
        item["base_sha"] = base_sha
        item["catalog_digest"] = _catalog_digest(workspace)
        return path, False
    if path.exists():
        raise ValueError("任务 worktree 目标已存在但未被任务状态持有：%s" % path)
    _run(["git", "check-ref-format", "--branch", item["work_branch"]])
    branch_exists = _run(
        ["git", "-C", str(main), "show-ref", "--verify", "--quiet", "refs/heads/%s" % item["work_branch"]],
        check=False,
    ).returncode == 0
    if branch_exists and not reuse_existing_branch:
        raise ValueError(
            "本地任务分支已存在：%s；请换用新分支，或显式传入 --reuse-existing-branch"
            % item["work_branch"]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if branch_exists:
        _run(["git", "-C", str(main), "worktree", "add", str(path), item["work_branch"]])
    else:
        _run(
            ["git", "-C", str(main), "worktree", "add", "-b", item["work_branch"], str(path), base_sha]
        )
    item["base_sha"] = base_sha
    item["catalog_digest"] = _catalog_digest(workspace)
    item["worktree"] = {
        "path": str(path),
        "status": "prepared",
        "branch_reused": branch_exists,
        "prepared_at": now(),
    }
    return path, True


def prepare_task(workspace, issue_key, *, reuse_existing_branch=False):
    workspace = Path(workspace).resolve()
    task = load_task(workspace, issue_key)
    if not task.get("repositories"):
        raise ValueError("任务没有登记源码仓库；请先执行 task.py repository add")
    binding, product_root, pool_root = workspace_binding(workspace)
    created = []
    with _pool_lock(product_root):
        leases = [
            item for item in _load_leases(product_root)
            if Path(item.get("path", "")).exists()
        ]
        for item in task["repositories"]:
            conflicts = [
                lease for lease in leases
                if lease.get("pool_root") == str(pool_root)
                and lease.get("repository") == item["repository"]
                and lease.get("branch") == item["work_branch"]
                and not (
                    lease.get("workspace_id") == binding["workspace_id"]
                    and lease.get("issue_key") == task["issue_key"]
                    and lease.get("run_id") == task["run_id"]
                )
            ]
            if conflicts:
                raise ValueError(
                    "Source Pool worktree 租约冲突：%s:%s 已由 %s/%s 持有"
                    % (
                        item["repository"],
                        item["work_branch"],
                        conflicts[0].get("workspace_id"),
                        conflicts[0].get("issue_key"),
                    )
                )
        try:
            for item in task["repositories"]:
                path, was_created = _prepare_repository(
                    workspace, task, item, reuse_existing_branch=reuse_existing_branch
                )
                if was_created:
                    created.append((item, path))
            identities = [_lease_identity(binding, task, item) for item in task["repositories"]]
            leases = [
                lease for lease in leases
                if not any(_same_lease(lease, identity) for identity in identities)
            ]
            _write_leases(product_root, [*leases, *identities])
            task["history"].append({"ts": now(), "event": "worktrees_prepare", "run_id": task["run_id"]})
            _write_task(workspace, task)
        except Exception:
            for item, path in reversed(created):
                main = repository_path(pool_root, item["repository"])
                _run(["git", "-C", str(main), "worktree", "remove", str(path)], check=False)
                _prune_empty_worktree_parents(workspace, path)
            raise
    return [Path(item["worktree"]["path"]) for item in task["repositories"]]


def preflight_cleanup(workspace, task):
    checks = []
    for item in task.get("repositories", []):
        worktree = item.get("worktree")
        if not worktree or worktree.get("status") != "prepared":
            continue
        path = Path(worktree["path"]).resolve()
        if path.exists():
            dirty = _run(["git", "-C", str(path), "status", "--porcelain"]).stdout.strip()
            if dirty:
                raise ValueError("任务 worktree 存在未提交变更，拒绝清理：%s" % path)
        checks.append((item, path))
    return checks


def _prune_empty_worktree_parents(workspace, path):
    stop = Path(workspace).resolve() / ".agenticops" / "worktrees"
    current = Path(path).resolve().parent
    while current != stop and stop in current.parents:
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent
    try:
        stop.rmdir()
    except OSError:
        pass


def cleanup_task(workspace, issue_key, *, delete_branches=False):
    workspace = Path(workspace).resolve()
    task = load_task(workspace, issue_key)
    binding, product_root, pool_root = workspace_binding(workspace)
    with _pool_lock(product_root):
        checks = preflight_cleanup(workspace, task)
        identities = []
        for item, path in checks:
            identities.append(_lease_identity(binding, task, item))
            main = repository_path(pool_root, item["repository"])
            if path.exists():
                _run(["git", "-C", str(main), "worktree", "remove", str(path)])
            _prune_empty_worktree_parents(workspace, path)
            _run(["git", "-C", str(main), "worktree", "prune"])
            cleanup = "retained"
            if delete_branches:
                result = _run(
                    ["git", "-C", str(main), "branch", "-d", item["work_branch"]], check=False
                )
                cleanup = "deleted" if result.returncode == 0 else "retained-not-merged"
            item["worktree"] = {
                "path": str(path),
                "status": "removed",
                "removed_at": now(),
                "branch_cleanup": cleanup,
            }
        leases = [
            lease for lease in _load_leases(product_root)
            if not any(_same_lease(lease, identity) for identity in identities)
            and Path(lease.get("path", "")).exists()
        ]
        _write_leases(product_root, leases)
        task["history"].append({"ts": now(), "event": "worktrees_cleanup", "run_id": task["run_id"]})
        _write_task(workspace, task)
    return checks


def task_roots(workspace, issue_key):
    workspace = Path(workspace).resolve()
    task = load_task(workspace, issue_key)
    binding, product_root, pool_root = workspace_binding(workspace)
    leases = _load_leases(product_root)
    roots = []
    for item in task.get("repositories", []):
        worktree = item.get("worktree")
        if not worktree or worktree.get("status") != "prepared":
            raise ValueError("任务仓库尚未准备 worktree：%s" % item["repository"])
        if item.get("catalog_digest") != _catalog_digest(workspace):
            raise ValueError(
                "项目仓库目录已变化，任务 worktree 绑定失效：%s；请清理后重新准备"
                % item["repository"]
            )
        identity = _lease_identity(binding, task, item)
        if not any(_same_lease(lease, identity) for lease in leases):
            raise ValueError("任务 worktree 缺少有效租约：%s" % item["repository"])
        expected = task_worktree_path(workspace, task, item["repository"])
        path = Path(worktree["path"]).resolve()
        if path != expected:
            raise ValueError("任务 worktree 路径越界：%s" % path)
        if not path.is_dir():
            raise ValueError("任务 worktree 不存在：%s" % path)
        branch = _run(["git", "-C", str(path), "branch", "--show-current"]).stdout.strip()
        if branch != item["work_branch"]:
            raise ValueError("任务 worktree 分支漂移：%s" % path)
        roots.append(path)
    if not roots:
        raise ValueError("任务没有可执行的 worktree")
    return roots


def execution_root(workspace, issue_key):
    workspace = Path(workspace).resolve()
    task = load_task(workspace, issue_key)
    task_roots(workspace, issue_key)
    root = task_execution_root(workspace, task)
    if not root.is_dir():
        raise ValueError("任务执行目录不存在：%s" % root)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--issue-key", required=True)
    prepare.add_argument("--reuse-existing-branch", action="store_true")
    prepare.add_argument("--dir", default=".")
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--issue-key", required=True)
    cleanup.add_argument("--delete-branches", action="store_true")
    cleanup.add_argument("--dir", default=".")
    roots = commands.add_parser("roots")
    roots.add_argument("--issue-key", required=True)
    roots.add_argument("--dir", default=".")
    execution = commands.add_parser("execution-root")
    execution.add_argument("--issue-key", required=True)
    execution.add_argument("--dir", default=".")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            paths = prepare_task(args.dir, args.issue_key, reuse_existing_branch=args.reuse_existing_branch)
            for path in paths:
                print(path)
        elif args.command == "cleanup":
            checks = cleanup_task(args.dir, args.issue_key, delete_branches=args.delete_branches)
            print("已清理 %s 个任务 worktree。" % len(checks))
        elif args.command == "roots":
            for path in task_roots(args.dir, args.issue_key):
                print(path)
        else:
            print(execution_root(args.dir, args.issue_key))
        return 0
    except (ValueError, subprocess.TimeoutExpired) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
