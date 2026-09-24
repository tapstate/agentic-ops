"""版本 6 清理成果检查；原生工具和内置执行器共用，不信任命令退出码。"""
import os
import stat
import json
from pathlib import Path

from workflow import engineering_baseline as baseline, station_archive, station_artifacts as artifacts
from workflow import station_directories as directories, station_resources as resources
from workflow import station_source as source, station_operation as operations


def refs(repository):
    return dict(line.split(" ", 1) for line in source.git(
        repository, "for-each-ref", "--format=%(refname) %(objectname)").stdout.splitlines())


def source_directories(repository):
    """记录普通目录身份，发现特殊边界时不猜测或跟随；不读取 .git 内容。"""
    found = {}
    device = directories.identity(repository)["device"]
    def unreadable(error):
        raise error
    for root, names, files in os.walk(repository, followlinks=False, onerror=unreadable):
        root = Path(root)
        if root == repository:
            names[:] = [name for name in names if name != ".git"]
            files = [name for name in files if name != ".git"]
        for name in names + files:
            path = root / name
            info = path.lstat()
            if name == ".git" or info.st_dev != device or path.is_mount():
                raise ValueError("源码含嵌套 Git 或挂载对象，请明确处理")
            if stat.S_ISLNK(info.st_mode):
                continue  # Git/文件快照核验链接本身，os.walk 不跟随。
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError("源码含特殊对象，请明确处理：" + str(path))
            if stat.S_ISDIR(info.st_mode):
                found[path.relative_to(repository).as_posix()] = directories.identity(path)
    return found


def target_directories(repository, sha):
    paths = source.git(repository, "ls-tree", "-r", "--name-only", "-z", sha).stdout.split("\0")
    result = set()
    for filename in filter(None, paths):
        parts = filename.split("/")
        result.update("/".join(parts[:i]) for i in range(1, len(parts)))
    # 未初始化 gitlink 是允许保留的空目录。
    for row in filter(None, source.git(repository, "ls-tree", "-r", "-z", sha).stdout.split("\0")):
        metadata, filename = row.split("\t", 1)
        if metadata.startswith("160000 "):
            result.add(filename)
    return result


def guard(base, task, operation, final=False):
    plan = operation["cleanup_plan"]
    if (plan.get("schema_version") != 6 or operation["run_id"] != task["run_id"]
            or operation["kind"] not in ("clean", "release") or operation["status"] != "running"
            or baseline.digest({k: v for k, v in plan.items() if k != "digest"}) != plan["digest"]
            or operation.get("confirmation_digest", operation["request"].get("confirmed_digest")) != plan["digest"]):
        raise ValueError("清理结果核验必须绑定当前已确认操作")
    station_archive.verify(base, task.get("archive_ref"), task)
    artifacts.verify_coverage(base, task, plan)
    resources.verify_active(base, plan, operation)
    resources.verify_known_external(base, task)
    resources.verify_stopped(base, task, require_cleaned=final)
    resources.verify_station_inventory(base, plan["rules"], allow_pending=True)
    # 所有工位根必须先核验身份，不能在源码恢复后才发现 runtime 已被替换。
    for entry in plan["directories"]:
        if entry["kind"] == "source-generated":
            raise ValueError("版本 6 源码必须按文件快照处理，不能整目录回收")
        directories.validate(base, entry, missing=entry["disposition"] == "delete_root")
    if resources.task_fingerprint(task) != plan["active_state"]["task_digest"]:
        raise ValueError("任务范围或事实变化，需要补充确认")
    if operation["kind"] == "release":
        proof = task.get("terminal_proof") or {}
        if (task.get("outcome") != "completed" or operation["request"].get("candidate_digest") != proof.get("candidate_digest")
                or any(plan["source"][name].get("preserved_head") != proof.get("repositories", {}).get(name, {}).get("head")
                       for name in task.get("task_repositories", {}))):
            raise ValueError("释放保留成果与交付证明不一致")
    catalog = resources.project_rules.load_repository_catalog(station=base)["repositories"]
    if source.check_station_layout(base, catalog, plan["source"], task.get("replan_preserved")) != task.get("retained_repositories", {}):
        raise ValueError("未选择的持久仓库状态变化")
    for name, entry in plan["source"].items():
        repository = source.repository_path(base, name)
        source.identity(repository, entry["origin"])
        if entry.get("initial_checkout"):
            if {p.name for p in repository.iterdir()} != {".git"}:
                raise ValueError("未完成检出仓库出现未知内容")
            continue
        current = refs(repository)
        expected = dict(entry["refs"])
        preserved = entry["preserved_ref"]
        if (operation.get("steps", {}).get("neutral:" + name, {}).get("receipt") is not None
                and preserved not in current):
            raise ValueError("已核验的源码保留引用缺失：" + name)
        if preserved in current:
            expected[preserved] = entry["preserved_head"]
        branch_ref = "refs/heads/" + entry["checkout_branch"] if entry["checkout_branch"] else None
        if branch_ref in current and branch_ref not in expected:
            expected[branch_ref] = entry["neutral"]["sha"]
        from workflow import station_cleanup_stages
        expected = station_cleanup_stages.expected_refs(base, task, operation, name, expected, current)
        if current != expected:
            raise ValueError("保留 Git 引用变化：" + name)
        artifacts.verify_special_entries(repository, entry["neutral"]["sha"])
    return plan


def source_result(base, task, name, entry):
    repository = source.repository_path(base, name)
    if entry.get("initial_checkout"):
        return
    source.require_clean(repository)
    if source.git(repository, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout:
        raise ValueError("源码仍有 ignored 内容：" + name)
    branch = entry["checkout_branch"] or ""
    if (source.git(repository, "rev-parse", "HEAD").stdout.strip() != entry["neutral"]["sha"]
            or source.git(repository, "branch", "--show-current").stdout.strip() != branch
            or refs(repository).get(entry["preserved_ref"]) != entry["preserved_head"]):
        raise ValueError("源码基线或成果保留引用不符：" + name)
    extra = set(source_directories(repository)) - target_directories(repository, entry["neutral"]["sha"])
    if extra:
        raise ValueError("源码仍有基线外目录：" + name + "/" + sorted(extra)[0])


def verify(base, task, operation):
    """只读验收，无论是否执行过内置脚本；失败不写成功或解绑状态。"""
    plan = guard(base, task, operation, final=True)
    for name, entry in plan["source"].items():
        source_result(base, task, name, entry)
    from workflow import station_cleanup_stages
    station_cleanup_stages.verify_dispositions(base, task, operation)
    for entry in plan["directories"]:
        path = directories.validate(base, entry, missing=True)
        if path.exists() and (entry["disposition"] == "delete_root" or any(path.iterdir())):
            raise ValueError("清理仍有目录残留：" + entry["path"])
    external = [row for row in resources.inventory(base, task) if row.get("kind") == "external"
                and row.get("resource_type") not in ("git-branch", "pull-request")]
    clear = "clear-active:" + str(len(operation.get("plan_revisions", []))) + ":" + plan["digest"]
    if not external and clear in operation["steps"]:
        external = operation.get("cleanup_manifest", {}).get("result", {}).get("external", [])
    if resources.project_rules.scan_sensitive(resources.project_rules.load_admission(station=base), json.dumps(external, ensure_ascii=False)):
        raise ValueError("外部资源最终回读含敏感内容，请先脱敏")
    return {"plan_digest": plan["digest"], "verification": "observed_result", "external": external}


def _pending_added_file(repository, operation, name, prior, observed):
    """仅识别原精确清理意图中 rm --cached 后尚未 unlink 的新增文件。"""
    if (not prior or prior["action"] != "restore" or not prior["before_index"]
            or observed["action"] != "delete" or observed["before_index"]
            or observed["before"] is None
            or any(observed[key] != prior[key] for key in ("head", "before", "file_identity"))
            or any(observed[key] != artifacts.digest(b"") for key in ("index_patch", "worktree_patch"))):
        return False
    plan = operation["cleanup_plan"]
    key = "source-reset:%s:%s:%s" % (len(operation.get("plan_revisions", [])), plan["digest"], name)
    step = operation["steps"].get(key)
    entries = [entry for entry in plan["entries"] if entry["repository"] == name]
    expected = {"repository": name, "head": prior["head"], "entries_digest": baseline.digest(entries)}
    if (not step or step.get("receipt") is not None or step.get("superseded_by")
            or step.get("expected") != expected):
        return False
    # ls-tree 成功且无条目才证明原 HEAD 不含该路径；Git 错误仍向上传递。
    return not source.git(repository, "ls-tree", "-z", prior["head"], "--", prior["file"]).stdout


def _apply_repository(base, task, operation, name, entry):
    plan = operation["cleanup_plan"]
    if entry.get("initial_checkout"):
        return
    repository = source.repository_path(base, name)
    try:
        source_result(base, task, name, entry)
    except ValueError:
        pass
    else:
        return
    current_dirs = source_directories(repository)
    allowed = set(entry["directories"]) | target_directories(repository, entry["neutral"]["sha"])
    if set(current_dirs) - allowed:
        raise ValueError("清理确认后新增源码目录，请补充确认：" + name)
    for relative, identity in current_dirs.items():
        if relative in entry["directories"] and identity != entry["directories"][relative]:
            raise ValueError("清理确认后源码目录身份变化：" + name + "/" + relative)
    head = source.git(repository, "rev-parse", "HEAD").stdout.strip()
    if head not in (entry["head"], entry["neutral"]["sha"]):
        raise ValueError("清理期间源码 Head 变化：" + name)
    observed = artifacts.snapshot(base, name, {}, {}, entry["neutral"]["sha"], include_ignored=True)
    original = {row["path"]: row for row in plan["entries"] if row["repository"] == name}
    for row in observed["entries"]:
        prior = original.get(row["path"])
        unchanged = prior and all(row[key] == prior[key] for key in ("head", "before", "before_index", "index_patch", "worktree_patch", "file_identity"))
        if not unchanged and not _pending_added_file(repository, operation, name, prior, row):
            raise ValueError("清理确认后源码内容变化，请补充确认：" + row["path"])
    resources.clean(base, task, plan, plan["digest"], operation, source_only=True, only_repository=name)
    keep = target_directories(repository, entry["head"]) | target_directories(repository, entry["neutral"]["sha"])
    for relative in sorted(entry["directories"], key=lambda p: len(p.split("/")), reverse=True):
        path = repository / relative
        if relative not in keep and path.exists() and not path.is_symlink():
            if directories.identity(path) != entry["directories"][relative]:
                raise ValueError("待清空目录身份变化：" + str(path))
            path.rmdir()
    resources.neutral(base, task, operation, only_repository=name)
    source_result(base, task, name, entry)


def verify_sources(base, task, operation):
    errors = []
    for name, entry in operation["cleanup_plan"]["source"].items():
        try:
            source_result(base, task, name, entry)
        except (ValueError, OSError) as error:
            errors.append(name + ": " + str(error))
    if errors:
        raise ValueError("源码阶段验收未通过：\n" + "\n".join(errors))


def apply(base, task, operation):
    """同一操作内按阶段执行；阶段失败保留进度，验收通过才进入下一阶段。"""
    from workflow import station_cleanup_stages as stages
    operation["phase"] = "cleanup_source"
    operations.save(base, operation)
    plan = guard(base, task, operation)
    completed = "station-source-reset:" + str(len(operation.get("plan_revisions", []))) + ":" + plan["digest"]
    if operation.get("steps", {}).get(completed, {}).get("receipt") is not None:
        verify(base, task, operation)
        return
    def reset_sources():
        errors = []
        for name, entry in plan["source"].items():
            try:
                _apply_repository(base, task, operation, name, entry)
            except (ValueError, OSError) as error:
                errors.append((name, error))
        if len(errors) == 1:
            raise errors[0][1]
        if errors:
            error_type = OSError if all(isinstance(error, OSError) for _, error in errors) else ValueError
            raise error_type("源码复位未完成：\n" + "\n".join(name + ": " + str(error) for name, error in errors))
    stages.run(base, task, operation, "source", reset_sources,
               lambda: verify_sources(base, task, operation))
    stages.run(base, task, operation, "disposition",
               lambda: stages.dispose_local(base, task, operation),
               lambda: stages.verify_dispositions(base, task, operation))
    stages.run(base, task, operation, "resources",
               lambda: resources.clean(base, task, plan, plan["digest"], operation, directories_only=True),
               lambda: verify(base, task, operation))


def record(base, task, operation):
    observed = verify(base, task, operation)
    from workflow import archive_store, station_export, task_store
    for entry in operation["cleanup_plan"]["entries"]:
        if entry["preservation"]["action"] != "export":
            continue
        choice = entry["preservation"]
        target = station_export.receipt_path(base, task, entry["path"], choice["snapshot"], choice["path"])
        active = task_store.task_directory(base, task["issue_key"]) / target.name
        if active.exists():
            data = json.loads(active.read_text())  # verify 已核验该原回执及实际导出字节。
            archive_store.receipts(base, task["archive_ref"], create=True)
            if target.is_symlink() or (target.exists() and json.loads(target.read_text()) != data):
                raise ValueError("归档导出回执与原回执不一致")
            if not target.exists():
                task_store._write_json_atomic(target, data)
    # 执行中断留下的意图以实际结果闭合，不伪称原命令执行成功。
    for key, step in list(operation["steps"].items()):
        if step["receipt"] is None and not step.get("superseded_by") and key.startswith(("source-reset:", "neutral:", "station-source-reset:")):
            operations.receipt(base, operation, key, observed)
    name = "station-source-reset:" + str(len(operation.get("plan_revisions", []))) + ":" + observed["plan_digest"]
    operations.intent(base, operation, name, {}, {"plan_digest": observed["plan_digest"]})
    operations.receipt(base, operation, name, observed)
    return observed
