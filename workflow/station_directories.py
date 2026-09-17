"""任务独占目录的创建与回收；不按生成文件数量写操作日志。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from workflow import engineering_baseline as baseline, project_rules, task_store as store


def identity(path):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or path.is_mount():
        raise ValueError("受管目录不是普通独立目录：%s" % path)
    return {"device": info.st_dev, "inode": info.st_ino}


def path_at(base, relative):
    parts = relative.split("/") if isinstance(relative, str) else []
    extra = len(parts) == 1 and parts[0] not in ("config", "archive", ".agenticops", ".agents", ".claude", ".git")
    if not parts or (parts[0] not in ("runtime", "source") and not extra) or any(p in ("", ".", "..", ".git") for p in parts):
        raise ValueError("受管目录路径越界")
    path = Path(base).resolve()
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("受管目录或祖先不能为符号链接")
        if path.exists():
            identity(path)
    return path


def registry_path(base, task):
    return store.task_directory(base, task["issue_key"]) / "managed-directories.json"


def load(base, task):
    path = registry_path(base, task)
    if path.is_symlink():
        raise ValueError("目录登记不能是符号链接")
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if value.get("run_id") != task["run_id"]:
        raise ValueError("目录登记 run 不一致")
    from workflow import quality_contract
    station_id = json.loads((store.state_path(base) / "workspace.json").read_text())["workspace_id"]
    for name, entry in value["roots"].items():
        quality_contract.validate(entry, "station-directory.schema.json")
        if entry["path"] != name or entry["run_id"] != task["run_id"] or entry["station_id"] != station_id:
            raise ValueError("目录归属身份不一致")
        if ((entry["kind"] == "source-generated" and not name.startswith("source/"))
                or (entry["kind"] == "workspace-generated" and ("/" in name or name in ("source", "runtime", "config", "archive", ".agenticops")))):
            raise ValueError("目录归属类型与路径不一致")
        runtime = name == "runtime"
        if (entry["disposition"] == "clear_children_keep_root") != runtime or (entry["kind"] == "runtime-exclusive") != runtime:
            raise ValueError("目录类型与回收动作不一致")
    return value["roots"]


def save(base, task, roots):
    store._write_json_atomic(registry_path(base, task), {"schema_version": 1, "run_id": task["run_id"], "roots": roots})


def recipe(base, task):
    root = project_rules.product_root_from_workspace(base)
    profile = task.get("engineering_baseline", {}).get("profile")
    if not profile:
        raise ValueError("生成目录需要已冻结的项目配方")
    value = json.loads((root / "projects" / store.workspace_project(base) / "engineering-profiles.json").read_text())["profiles"][profile["id"]]
    if value["revision"] != profile["revision"]:
        raise ValueError("生成目录配方版本已变化")
    return value


def create(base, task, relative, producer, adopt=False):
    """调用者持工位锁；先持久意图，创建/空目录采用后登记身份，再启动生产者。"""
    path = path_at(base, relative)
    runtime = relative == "runtime"
    workspace_generated = not runtime and "/" not in relative
    if workspace_generated:
        from workflow import workspace_clean_rules
        decision = workspace_clean_rules.classify(workspace_clean_rules.load(base), relative, True)
        if decision["action"] != "remove":
            raise ValueError("工位附属目录必须由清理黑名单声明 remove")
        init = json.loads((store.state_path(base) / "init.json").read_text())
        if any(e["path"] == relative or e["path"].startswith(relative + "/") for e in init.get("artifacts", [])):
            raise ValueError("不能登记初始化接线为清理目录")
        rule = {"id": "workspace-clean", "revision": 1}
    elif runtime:
        rule = {"id": "workflow-runtime", "revision": 1}
    else:
        from workflow import station_source as source
        repositories = task.get("engineering_baseline", {}).get("repositories", {})
        names = [name for name in repositories if relative.startswith("source/" + name + "/")]
        if len(names) != 1:
            raise ValueError("生成目录不属于当前工程")
        name = names[0]
        rule = recipe(base, task)
        local = relative[len("source/" + name + "/"):]
        if not any(Path(local).match(pattern) for pattern in rule.get("generated_directories", [])):
            raise ValueError("生成目录未由 Project 配方声明")
        repository = source.repository_path(base, name)
        if source.git(repository, "ls-files", "-z", "--", local).stdout:
            raise ValueError("生成目录包含已跟踪源码")
    if not producer:
        raise ValueError("目录必须有生产来源")
    parent = identity(path.parent)
    roots = load(base, task)
    existing = roots.get(relative)
    if existing and existing.get("identity"):
        if existing["producer"] != producer or existing["recipe"] != {"id": rule["id"], "revision": rule["revision"]}:
            raise ValueError("目录创建恢复生产来源或配方不一致")
        if identity(path) != existing["identity"] or parent != existing["parent"]:
            raise ValueError("受管目录身份已变化")
        return existing
    if path.exists() and (any(path.iterdir()) or not (runtime or adopt)):
        raise ValueError("已有目录只能显式采用已核验的空目录")
    for other in roots:
        if other != relative and (other.startswith(relative + "/") or relative.startswith(other + "/")):
            raise ValueError("受管目录不能重叠")
    entry = {"path": relative, "run_id": task["run_id"], "station_id": json.loads((store.state_path(base) / "workspace.json").read_text())["workspace_id"],
        "kind": "runtime-exclusive" if runtime else ("workspace-generated" if workspace_generated else "source-generated"), "producer": producer,
             "recipe": {"id": rule["id"], "revision": rule["revision"]}, "parent": parent,
             "disposition": "clear_children_keep_root" if runtime else "delete_root", "identity": None}
    if existing and any(existing[k] != entry[k] for k in entry if k != "identity"):
        raise ValueError("目录创建恢复意图不一致")
    roots[relative] = entry
    save(base, task, roots)
    if not path.exists():
        path.mkdir(mode=0o700)
        sync(path.parent)
    entry["identity"] = identity(path)
    save(base, task, roots)
    return entry


def runtime_child(base, task, name):
    """返回当前 run 的 runtime 子路径，不把子目录登记为第二个受管根。"""
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError("runtime 子目录名称无效")
    roots = load(base, task)
    entry = roots.get("runtime")
    if entry is None:
        raise ValueError("当前 run 缺少受管 runtime 根目录")
    root = validate(base, entry)
    path = root / name
    if path.is_symlink():
        raise ValueError("runtime 子目录不能是符号链接")
    if path.exists() and not path.is_dir():
        raise ValueError("runtime 子路径必须是目录")
    if path.exists() and path.is_mount():
        raise ValueError("runtime 子目录不能是挂载点")
    return path


def sync(path):
    descriptor = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def covered(relative, roots):
    return any(relative == root or relative.startswith(root + "/") for root in roots)


def _walk(fd, device, delete=False):
    """所有递归操作基于目录 FD，不跟随子链接，不跨设备，不删除特殊对象。"""
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if name == ".git" or info.st_dev != device:
            raise ValueError("受管目录含嵌套 Git 或挂载对象")
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError("生成目录在遍历时被替换")
                _walk(child, device, delete)
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ValueError("生成目录在删除时被替换")
                if delete:
                    os.fsync(child)
            finally:
                os.close(child)
            if delete:
                os.rmdir(name, dir_fd=fd)
        elif stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            if delete:
                os.unlink(name, dir_fd=fd)
        else:
            raise ValueError("受管目录含特殊文件，停止重置")


def validate(base, entry, missing=False):
    path = path_at(base, entry["path"])
    if identity(path.parent) != entry["parent"]:
        raise ValueError("受管目录父身份变化")
    if not path.exists() and missing and entry["disposition"] == "delete_root":
        return path
    if not entry.get("identity") or identity(path) != entry["identity"]:
        raise ValueError("受管目录身份变化或创建未完成")
    return path


def cleanup_paths(base, task, entry, operation):
    directory = Path(base).resolve() / task["archive_ref"]["path"] / "receipts"
    if directory.is_symlink():
        raise ValueError("回执目录不能是链接")
    stem = operation["operation_id"] + "-root-v%s-" % len(operation.get("plan_revisions", [])) + baseline.digest(entry)
    return tuple(directory / (stem + suffix) for suffix in ("-intent.json", "-done.json"))


def precheck(base, task, entry, operation):
    """所有目录在源码副作用之前回读身份、缺失事实和已有回执。"""
    intent_path, receipt_path = cleanup_paths(base, task, entry, operation)
    for p in (intent_path, receipt_path):
        if p.is_symlink():
            raise ValueError("目录回执不能是链接")
    intended = intent_path.exists()
    path = validate(base, entry, missing=True)
    if not intended and not path.exists() and not entry.get("observed_missing_before_intent"):
        raise ValueError("目录在确认后、清理意图前缺失，需要重新确认观测结果")
    # 预检已明确绑定提前缺失的事实；回执不声称本操作删除了该目录。
    expected = {"run_id": task["run_id"], "operation_id": operation["operation_id"], "root": entry}
    if intended:
        if json.loads(intent_path.read_text()) != expected:
            raise ValueError("目录清理意图不匹配")
    if receipt_path.exists():
        if not intended or json.loads(receipt_path.read_text()) != expected or (path.exists() and (entry["disposition"] == "delete_root" or any(path.iterdir()))):
            raise ValueError("回收后目录再次产生内容，拒绝重删")
    return path, expected


def reset(base, task, entry, operation):
    """正式档案内按目录保存回执；文件数量不会增加 operation 的大小或写入次数。"""
    path, expected = precheck(base, task, entry, operation)
    intent_path, receipt_path = cleanup_paths(base, task, entry, operation)
    intent_path.parent.mkdir(exist_ok=True)
    if not intent_path.exists():
        store._write_json_atomic(intent_path, expected)
    if receipt_path.exists():
        return
    if path.exists():
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            opened = os.fstat(fd)
            if {"device": opened.st_dev, "inode": opened.st_ino} != entry["identity"]:
                raise ValueError("目录在打开时被替换")
            _walk(fd, opened.st_dev)  # 在任何删除之前检查不支持的节点。
            _walk(fd, opened.st_dev, delete=True)
            os.fsync(fd)
        finally:
            os.close(fd)
        validate(base, entry)
        if any(path.iterdir()):
            raise ValueError("清理后仍有写入者")
        if entry["disposition"] == "delete_root":
            path.rmdir()
            sync(path.parent)
    store._write_json_atomic(receipt_path, expected)
