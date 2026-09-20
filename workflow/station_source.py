"""工位固定独立仓库的准备与只读核验，不使用 linked worktree 或共享对象。"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tempfile

from workflow import engineering_baseline as baseline, git_refs, project_rules, station_operation as operations, source_pool

GIT_LOCAL_TIMEOUT = 120
GIT_NETWORK_TIMEOUT = 1800
GIT_PROGRESS_INTERVAL = 10


def git(path, *arguments, check=True):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    if arguments[0] == "status":
        environment["GIT_OPTIONAL_LOCKS"] = "0"
    command = ["git", "-C", str(path), *arguments]
    network = arguments[0] in ("clone", "fetch")
    timeout = GIT_NETWORK_TIMEOUT if network else GIT_LOCAL_TIMEOUT
    started = time.monotonic()
    process = subprocess.Popen(command, env=environment, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            try:
                stdout, stderr = process.communicate(timeout=max(0, min(GIT_PROGRESS_INTERVAL, remaining)))
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - started >= timeout:
                    raise ValueError("Git 操作超时（%s，%s 秒）；保留现场并恢复原操作" % (arguments[0], timeout))
                if network:
                    print("[station-source] %s 仍在执行（%s 秒）" %
                          (arguments[0], int(time.monotonic() - started)), file=sys.stderr, flush=True)
    except BaseException:
        # Git 的 SSH/index-pack 子进程也可能持有输出管道或继续写仓库。
        # 仅终止本次创建的进程组，回收后才允许调用方恢复同一操作。
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check and result.returncode:
        raise ValueError("Git 操作失败（%s）：%s" % (arguments[0], result.stderr.strip()))
    return result


def repository_path(station, name):
    baseline.repository_id(name)
    root = Path(station).resolve()
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


def baseline_branch(entry):
    """真实分支基线以稳定、只由 AgenticOps 管理的本地分支呈现。"""
    if entry.get("ref_kind") != "branch":
        return None
    reference = baseline.ref_name(entry["ref_name"])
    return baseline.ref_name("agenticops/baseline/%s-%s" % (reference, entry["commit_sha"][:12]))


def check_station_layout(station, catalog, selected, preserved=None):
    """仅检查工位源码边界；未知目录不被当作可回收产物。"""
    root = Path(station).resolve() / "source"
    if root.is_symlink():
        raise ValueError("source 不能是符号链接")
    retained = {}
    if not root.exists():
        return retained
    owners = {name.split("/")[0] for name in catalog}
    for owner in root.iterdir():
        if owner.is_symlink() or not owner.is_dir() or owner.name not in owners:
            raise ValueError("source 含未知顶层对象：" + owner.name)
        for repository in owner.iterdir():
            name = owner.name + "/" + repository.name
            if name not in catalog:
                raise ValueError("source 含未知仓库：" + name)
            identity(repository, catalog[name]["origin"])
            if name not in selected:
                recovery = (preserved or {}).get(name)
                if recovery:
                    from workflow.quality import git_revision
                    if recovery.get("origin") != catalog[name]["origin"] or git_revision(repository) != recovery.get("fingerprint"):
                        raise ValueError("保留的返工仓库已变化：" + name)
                else:
                    require_clean(repository)
                if git(repository, "ls-files", "--others", "--ignored", "--exclude-standard").stdout:
                    raise ValueError("未选择的持久仓库含未知生成物：" + name)
                retained[name] = {"head": git(repository, "rev-parse", "HEAD").stdout.strip(),
                                  "branch": git(repository, "branch", "--show-current").stdout.strip()}
    return retained


def prepare_repositories(station, catalog, selected, operation):
    """先登记 clone/fetch 意图；失败保留现场，只按同一操作恢复。"""
    observations = {}
    for name in selected:
        path = repository_path(station, name)
        origin = catalog[name]["origin"]
        step = "clone:" + name
        recorded = operation["steps"].get(step)
        fetch_step = "fetch:" + name
        completed_fetch = operation["steps"].get(fetch_step)
        if path.exists() and recorded is None:
            identity(path, origin)
            require_clean(path)
        if not path.exists() and recorded and recorded["receipt"] is not None:
            raise ValueError("已准备仓库被移除，拒绝重建冒充恢复")
        if path.exists():
            identity(path, origin)
        if not completed_fetch or completed_fetch["receipt"] is None:
            operations.intent(station, operation, step,
                              recorded["before"] if recorded else {"exists": path.exists()}, {"origin": origin})
            with source_pool.refreshed(station, name, origin, git) as cache:
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=path.parent) as temporary:
                        staged = Path(temporary) / "repository"
                        git(path.parent, "clone", "--no-local", "--no-checkout", "--", str(cache), str(staged))
                        git(staged, "remote", "set-url", "origin", origin)
                        identity(staged, origin)
                        staged.rename(path)
                operations.receipt(station, operation, step, {"path": str(path), "origin": origin})
                operations.intent(station, operation, fetch_step, {}, {"origin": origin})
                git(path, "fetch", "--prune", str(cache),
                    "+refs/heads/*:refs/remotes/origin/*", "refs/tags/*:refs/tags/*")
        identity(path, origin)
        fetch = operation["steps"][fetch_step]
        refs = {}
        for line in git(path, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin/").stdout.splitlines():
            ref, sha = line.split(" ", 1)
            if ref != "refs/remotes/origin/HEAD":
                refs[ref.removeprefix("refs/remotes/origin/")] = sha
        if fetch["receipt"] is not None and fetch["receipt"] != {"refs": refs}:
            raise ValueError("已核验的远端引用发生漂移")
        operations.receipt(station, operation, fetch_step, {"refs": refs})
        observations[name] = {"selection": "required", "local": {"status": "available"},
                              "refs": {"verification": "verified"}, "_path": path, "_refs": refs}
    return observations


def checkout_baseline(station, value, operation):
    baseline.validate(value)
    for name, entry in value["repositories"].items():
        path = repository_path(station, name)
        identity(path, entry["origin"])
        step = "checkout:" + name
        branch = baseline_branch(entry)
        expected = {"sha": entry["commit_sha"], "ref_kind": entry["ref_kind"],
                    "ref_name": entry["ref_name"], "checkout_mode": "managed_branch" if branch else "detached",
                    "checkout_branch": branch}
        recorded = operation["steps"].get(step)
        if recorded and recorded["receipt"] is not None:
            if (git(path, "rev-parse", "HEAD").stdout.strip() != entry["commit_sha"]
                    or git(path, "branch", "--show-current").stdout.strip() != (branch or "")):
                raise ValueError("已完成的 checkout 发生漂移")
            require_clean(path)
            continue
        operations.intent(station, operation, step, {}, expected)
        clone = operation["steps"]["clone:" + name]
        if clone["before"]["exists"]:
            require_clean(path)
        if branch:
            existing = git(path, "rev-parse", "--verify", "refs/heads/" + branch, check=False)
            if existing.returncode == 0 and existing.stdout.strip() != entry["commit_sha"]:
                raise ValueError("受管冻结基线分支已指向不同提交")
            if existing.returncode:
                git(path, "branch", branch, entry["commit_sha"])
            git(path, "checkout", branch)
        else:
            git(path, "checkout", "--detach", entry["commit_sha"])
        require_clean(path)
        if git(path, "rev-parse", "HEAD").stdout.strip() != entry["commit_sha"]:
            raise ValueError("checkout 回读不一致")
        if git(path, "branch", "--show-current").stdout.strip() != (branch or ""):
            raise ValueError("checkout 分支回读不一致")
        operations.receipt(station, operation, step, expected)


def inspect(station, value):
    """核对固定基线对象和实时源码状态，不要求远端分支仍指向历史基线。"""
    baseline.validate(value)
    result = {}
    for name, entry in value["repositories"].items():
        path = repository_path(station, name)
        identity(path, entry["origin"])
        if git(path, "cat-file", "-t", entry["commit_sha"]).stdout.strip() != "commit":
            raise ValueError("冻结基线对象缺失")
        result[name] = {"head": git(path, "rev-parse", "HEAD").stdout.strip(),
                        "branch": git(path, "branch", "--show-current").stdout.strip(),
                        "dirty": bool(git(path, "status", "--porcelain", "--untracked-files=all").stdout)}
    return result


def require_ready_source(station, task, name):
    path = repository_path(station, name)
    previous = task.get("replan", {}).get("sources", {}).get(name)
    if previous and task.get("stage") == "design_review":
        from workflow.quality import git_revision
        if git_revision(path) != previous["fingerprint"]:
            raise ValueError("返工源码指纹变化，请重新准备方案：" + name)
    else:
        require_clean(path)


def readiness_snapshot(station, task):
    """只读核对 B/W/T；远端查询结果必须与已下载对象一致。"""
    value = task["engineering_baseline"]
    observed = inspect(station, value)
    if not task.get("source_prepared") or not task.get("task_repositories"):
        raise ValueError("完整工程及任务分支尚未准备")
    from workflow import station_resources
    from workflow import station_directories
    managed = station_directories.load(station, task)
    result = {}
    for name, state in observed.items():
        path = repository_path(station, name)
        require_ready_source(station, task, name)
        ignored = git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout.split("\0")
        if any(filename and not station_directories.covered("source/" + name + "/" + filename, managed) for filename in ignored):
            raise ValueError("源码含未登记 ignored 产物：" + name)
        entry = value["repositories"][name]
        binding = task["task_repositories"].get(name)
        if not binding:
            if state["head"] != entry["commit_sha"] or state["branch"] != (baseline_branch(entry) or ""):
                raise ValueError("配套仓偏离冻结基线：" + name)
            continue
        if state["branch"] != binding["work_branch"]:
            raise ValueError("工作分支与登记不一致：" + name)
        base = entry["commit_sha"]
        if git(path, "merge-base", "--is-ancestor", base, state["head"], check=False).returncode:
            raise ValueError("工作分支不从冻结基线派生：" + name)
        target_branch = baseline.ref_name(binding["target_branch"])
        work_branch = baseline.ref_name(binding["work_branch"])
        output = git(path, "ls-remote", "--refs", "origin",
                     "refs/heads/" + target_branch, "refs/heads/" + work_branch).stdout
        remote_heads = git_refs.parse_head_response(output, (target_branch, work_branch))
        target = remote_heads.get(target_branch)
        if target is None:
            raise ValueError("远端目标分支缺失或不明确：" + name)
        if git(path, "rev-parse", "--verify", "refs/remotes/origin/" + binding["target_branch"]).stdout.strip() != target:
            raise ValueError("远端引用变化，请重新执行 source-readiness：" + name)
        if target != base and git(path, "merge-base", "--is-ancestor", base, target, check=False).returncode:
            raise ValueError("目标分支与冻结基线分叉或回退：" + name)
        remote_head = remote_heads.get(work_branch)
        continuation = value.get("resolution_input", {}).get("continuations", {}).get(name)
        expected_remote = continuation["expected_head"] if continuation else None
        if task.get("replan") and task.get("stage") == "design_review":
            expected_remote = task["replan"].get("remote_work", {}).get(name)
        replan = task.get("replan") and task.get("stage") == "design_review"
        if (replan or remote_head is not None) and remote_head != expected_remote:
            raise ValueError("远端工作分支与接管合同不一致：" + name)
        result[name] = dict(state, target_branch=binding["target_branch"], target_sha=target,
                            remote_work_sha=remote_head, baseline_sha=base, relation="equal" if target == base else "advanced")
    snapshot = {"run_id": task["run_id"], "baseline_digest": value["digest"],
                "bindings": {name: {key: binding[key] for key in ("work_branch", "target_branch", "approved_scope", "verification_method")}
                             for name, binding in task["task_repositories"].items()}, "repositories": result}
    snapshot["digest"] = baseline.digest(snapshot)
    return snapshot


def prepare_readiness(station, task):
    """持锁调用；先使旧证据失效，逐仓记录 fetch 意图，再发布可核对结果。"""
    from workflow import task_store
    path = task_store.task_directory(station, task["issue_key"]) / "source-readiness.json"
    record = {"run_id": task["run_id"], "status": "refreshing", "fetches": {}}
    task_store._write_json_atomic(path, record)
    for name, binding in task["task_repositories"].items():
        repository = repository_path(station, name)
        identity(repository, task["engineering_baseline"]["repositories"][name]["origin"])
        require_ready_source(station, task, name)
        branch = baseline.ref_name(binding["target_branch"])
        record["fetches"][name] = {"target_branch": branch, "status": "intent"}
        task_store._write_json_atomic(path, record)
        git(repository, "fetch", "--no-tags", "origin", "+refs/heads/" + branch + ":refs/remotes/origin/" + branch)
        record["fetches"][name]["status"] = "done"
        task_store._write_json_atomic(path, record)
    snapshot = readiness_snapshot(station, task)
    record.update(status="observed", snapshot=snapshot)
    task_store._write_json_atomic(path, record)
    return record


def require_readiness(station, task):
    from workflow import task_store
    import json
    if task.get("facts", {}).get("station_contract") not in (2, 3):
        return None  # 已有 run 沿原合同恢复，新接管采用新检查。
    path = task_store.task_directory(station, task["issue_key"]) / "source-readiness.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("编码前须执行 source-readiness")
    record = json.loads(path.read_text())
    snapshot = readiness_snapshot(station, task)
    if record.get("status") != "observed" or record.get("snapshot") != snapshot:
        raise ValueError("仓库就绪证据失效，请重新执行 source-readiness")
    if any(item["relation"] == "advanced" for item in snapshot["repositories"].values()):
        if record.get("accepted_digest") != snapshot["digest"] or not record.get("decision_ref"):
            raise ValueError("目标分支已推进；请确认沿冻结基线开发并在 PR 前同步，或归档清理后重新接管")
    return snapshot["digest"]
