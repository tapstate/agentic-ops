"""只在空闲工位刷新独立源码；不改变任务基线，不覆盖本地成果。"""
import argparse
import json

from bootstrap.station_compatibility import require_station_can_adopt
from workflow import project_rules, station_operation, station_source as source, task_store
from workflow import engineering_baseline


def update_repository(path, entry):
    source.identity(path, entry["origin"])
    source.require_clean(path)
    if source.git(path, "ls-files", "--others", "--ignored", "--exclude-standard").stdout:
        raise ValueError("源码存在 ignored 产物，先处理后刷新")
    branch = engineering_baseline.ref_name(entry["dev_branch"])
    ref = "refs/heads/" + branch
    current = source.git(path, "symbolic-ref", "-q", "HEAD", check=False)
    if current.returncode == 0 and current.stdout.strip() != ref:
        raise ValueError("源码未处于项目开发分支，请先完成工位清理")
    if current.returncode != 0:
        raise ValueError("源码处于 detached HEAD，请先回到项目开发分支")
    before = source.git(path, "rev-parse", "HEAD").stdout.strip()
    source.git(path, "fetch", "origin", "+refs/heads/" + branch + ":refs/remotes/origin/" + branch)
    target = source.git(path, "rev-parse", "refs/remotes/origin/" + branch).stdout.strip()
    if source.git(path, "merge-base", "--is-ancestor", before, target, check=False).returncode:
        raise ValueError("开发分支含本地独有提交或分叉，拒绝覆盖")
    source.require_clean(path)
    if (source.git(path, "rev-parse", "HEAD").stdout.strip() != before
            or source.git(path, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip() != ref
            or source.git(path, "ls-files", "--others", "--ignored", "--exclude-standard").stdout):
        raise ValueError("刷新期间源码发生变化")
    # --ff-only 仅允许快进，不产生合并提交或隐式 rebase/reset/stash。
    source.git(path, "merge", "--ff-only", target)
    source.require_clean(path)
    if (source.git(path, "symbolic-ref", "HEAD").stdout.strip() != ref
            or source.git(path, "rev-parse", "HEAD").stdout.strip() != target):
        raise ValueError("开发分支刷新回读不一致")
    return {"branch": branch, "before": before, "head": target,
            "status": "unchanged" if before == target else "updated"}


def update(base, repository=None):
    root = project_rules.product_root_from_station(base)
    require_station_can_adopt(root, base)
    with task_store.task_state_lock(base):
        if task_store.read_current(base)["current"] is not None:
            raise ValueError("source-update 仅允许未接管任务的空闲工位")
        operation = station_operation.read(base)
        if operation and operation["status"] != "done":
            raise ValueError("工位存在未完成操作，请先恢复原操作")
        catalog = project_rules.load_repository_catalog(station=base)["repositories"]
        if repository is not None and repository not in catalog:
            raise ValueError("仓库未在项目 Profile 登记")
        names = [repository] if repository else [name for name in catalog if source.repository_path(base, name).exists()]
        if not names:
            raise ValueError("工位尚未准备源码仓库")
        results = {}
        for name in names:
            try:
                results[name] = update_repository(source.repository_path(base, name), catalog[name])
            except (ValueError, OSError) as error:
                results[name] = {"status": "failed", "reason": str(error)}
        return {"status": "failed" if any(x["status"] == "failed" for x in results.values()) else "done",
                "repositories": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", default=".")
    parser.add_argument("--repo")
    args = parser.parse_args(argv)
    result = update(args.station, args.repo)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "done" else 2
