"""空闲开发环境源码准备；复用工程解析与下载池，不建立任务或持久操作。"""
import contextlib
import json
from pathlib import Path
import tempfile

from workflow import engineering_baseline, project_rules, source_pool, station_operation, station_layout, task_store
from workflow import station_source as source
from projects.tapdata.scripts.engineering_baseline import resolve


class PreparationError(ValueError):
    """仅包含固定业务描述；底层路径、origin 和 stderr 不进入 CLI。"""


def checked(message, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except PreparationError:
        raise
    except (ValueError, OSError):
        raise PreparationError(message) from None


def git(path, *args, **kwargs):
    return checked("源码 Git 操作失败；核验网络、权限或本地 Git 状态后重试，现场保留",
                   source.git, path, *args, **kwargs)


def identity(path, origin):
    checked("源码仓库身份或 origin 不符；核验独立仓库及可信来源，现场保留",
            source.identity, path, origin)


def clean_branch(path):
    if git(path, "status", "--porcelain", "--untracked-files=all").stdout:
        raise PreparationError("源码存在未提交修改；先保全并处理本地修改，再重试")
    if git(path, "ls-files", "--others", "--ignored", "--exclude-standard").stdout:
        raise PreparationError("源码存在 ignored 产物，先按工位清理合同处理")
    branch = git(path, "symbolic-ref", "-q", "--short", "HEAD", check=False)
    if branch.returncode:
        raise PreparationError("源码处于 detached HEAD，请先核验并归位")
    return branch.stdout.strip(), git(path, "rev-parse", "HEAD").stdout.strip()


def prepare(station):
    """每个仓库原子发布；失败前已完成仓库可按真实 Git 事实继续。"""
    with task_store.task_state_lock(station):
        if task_store.read_current(station)["current"] is not None:
            raise PreparationError("prepare-source 仅允许无当前任务的空闲工位")
        operation = station_operation.read(station)
        if operation and operation["status"] != "done":
            raise PreparationError("工位存在未完成操作，请先恢复原操作")
        root, project = project_rules.station_context(station)
        if project != "tapdata":
            raise PreparationError("需要 TapData 工位")
        project_root = project_rules.project_root(root, project)
        catalog = project_rules.load_repository_catalog(station=station)["repositories"]
        profile = json.loads((project_root / "engineering-profiles.json").read_text())["profiles"]["full-application"]
        config = json.loads((project_root / "version-branch-alignments.json").read_text())
        selected = engineering_baseline.selected_repositories(profile, catalog, ())
        checked("源码布局或仓库身份异常；核验未知目录、符号链接及 origin，保留现场后恢复",
                source.check_station_layout, station, catalog, selected)
        before, observations, paths = {}, {}, {}
        # 先核验所有已有仓库，避免发现脏仓前就改其它仓的 checkout。
        for name in selected:
            path = source.repository_path(station, name)
            if path.exists():
                identity(path, catalog[name]["origin"])
                before[name] = clean_branch(path)
        with contextlib.ExitStack() as temporary:
            for name in selected:
                destination = source.repository_path(station, name)
                origin = catalog[name]["origin"]
                with source_pool.refreshed(station, name, origin, git) as cache:
                    path = destination
                    if name not in before:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        runtime = Path(station).resolve() / station_layout.station_name(station, "task-runtime")
                        if runtime.is_symlink() or not runtime.is_dir():
                            raise PreparationError("运行区不是独立目录；核验工位路径后重试")
                        # 中断残留不占据源码目录；嵌套 Git 继续遵循现役保全合同。
                        directory = temporary.enter_context(tempfile.TemporaryDirectory(
                            prefix="tapdata-source-preparation-", dir=runtime))
                        path = Path(directory) / "repository"
                        git(destination.parent, "clone", "--no-local", "--no-checkout", "--", str(cache), str(path))
                        git(path, "remote", "set-url", "origin", origin)
                    identity(path, origin)
                    git(path, "fetch", "--prune", str(cache),
                               "+refs/heads/*:refs/remotes/origin/*", "refs/tags/*:refs/tags/*")
                refs = {}
                for line in git(path, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin/").stdout.splitlines():
                    ref, sha = line.split(" ", 1)
                    if ref != "refs/remotes/origin/HEAD":
                        refs[ref.removeprefix("refs/remotes/origin/")] = sha
                paths[name] = path
                observations[name] = {"selection": "required", "local": {"status": "available"},
                                      "refs": {"verification": "verified"}, "_path": path, "_refs": refs}
            value = checked("develop 工程分支解析失败；核验项目映射与远端分支后重试",
                            resolve, "develop", config, catalog, profile, observations)
            for name, entry in value["repositories"].items():
                if name in before:
                    path = paths[name]
                    if clean_branch(path) != before[name] or before[name][0] != entry["ref_name"]:
                        raise PreparationError("源码分支与解析开发线不同或现场变化，保留：" + name)
                    if git(path, "merge-base", "--is-ancestor", before[name][1], entry["commit_sha"], check=False).returncode:
                        raise PreparationError("源码含本地独有提交或分叉，保留：" + name)
            results = {}
            for name, entry in value["repositories"].items():
                path = paths[name]
                identity(path, entry["origin"])
                if name in before:
                    if clean_branch(path) != before[name]:
                        raise PreparationError("准备期间源码变化，保留：" + name)
                    git(path, "merge", "--ff-only", entry["commit_sha"])
                else:
                    git(path, "checkout", "-B", entry["ref_name"], entry["commit_sha"])
                    identity(path, entry["origin"])
                if clean_branch(path) != (entry["ref_name"], entry["commit_sha"]):
                    raise PreparationError("源码准备回读不一致：" + name)
                if name not in before:
                    destination = source.repository_path(station, name)
                    if destination.exists():
                        raise PreparationError("发布前源码目标已出现，保留：" + name)
                    path.rename(destination)
                results[name] = {"branch": entry["ref_name"], "head": entry["commit_sha"],
                                 "status": "prepared" if name not in before else "updated"}
        result = {"status": "prepared", "version": "develop", "repositories": results}
        runtime = Path(station).resolve() / station_layout.station_name(station, "task-runtime")
        residuals = sorted(path.relative_to(Path(station).resolve()).as_posix()
                           for path in runtime.glob("tapdata-source-preparation-*"))
        if residuals:
            result["preparation_residuals"] = residuals
            result["recovery_hint"] = "源码已准备；接管前保全运行区临时仓库，并按任务清理的已确认范围处理残留"
        return result
