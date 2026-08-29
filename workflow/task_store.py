#!/usr/bin/env python3
"""项目工作空间中的多任务状态索引与任务级文件路径。

工作空间只绑定一个 Product Project；任务注册表统一管理任务身份与激活状态，任务
事实、授权、事件和 CI 记录按 Jira issue key 隔离保存。
"""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

import fcntl


REGISTRY_VERSION = 1
ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def workspace_path(base):
    return Path(base).resolve()


def state_path(base):
    return workspace_path(base) / ".agenticops"


def registry_path(base):
    return state_path(base) / "tasks" / "index.json"


def task_directory(base, issue_key):
    return state_path(base) / "tasks" / validate_issue_key(issue_key)


def task_path(base, issue_key):
    return task_directory(base, issue_key) / "state.json"


def authorization_path(base, issue_key):
    return task_directory(base, issue_key) / "authorization.json"


def events_path(base, issue_key):
    return task_directory(base, issue_key) / "events.jsonl"


def ci_path(base, issue_key, pr):
    return task_directory(base, issue_key) / ("ci-%s.json" % pr)


def validate_issue_key(issue_key):
    value = str(issue_key or "").strip().upper()
    if not ISSUE_KEY_PATTERN.fullmatch(value):
        raise ValueError("Jira issue key 格式无效：%s" % issue_key)
    return value


def workspace_project(base):
    path = state_path(base) / "workspace.json"
    if not path.is_file():
        raise ValueError("工作空间缺少 .agenticops/workspace.json，请先执行 agenticops init")
    try:
        binding = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工作空间绑定无法读取：%s" % error) from error
    project = binding.get("project")
    if not isinstance(project, str) or not project:
        raise ValueError("工作空间绑定缺少 project")
    return project


def empty_registry(project):
    return {"schema_version": REGISTRY_VERSION, "project": project, "tasks": {}}


def load_registry(base, create=False):
    migrate_legacy(base)
    path = registry_path(base)
    if not path.is_file():
        if create:
            return empty_registry(workspace_project(base))
        return None
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("任务注册表无法读取：%s" % error) from error
    if registry.get("schema_version") != REGISTRY_VERSION:
        raise ValueError("不支持的任务注册表版本：%s" % registry.get("schema_version"))
    if registry.get("project") != workspace_project(base):
        raise ValueError("任务注册表 project 与工作空间绑定不一致")
    if not isinstance(registry.get("tasks"), dict):
        raise ValueError("任务注册表 tasks 必须是对象")
    for issue, entry in registry["tasks"].items():
        if validate_issue_key(issue) != issue:
            raise ValueError("任务注册表 issue key 未规范化：%s" % issue)
        if not isinstance(entry, dict) or entry.get("status") not in (
            "active", "inactive", "completed"
        ):
            raise ValueError("任务注册表状态无效：%s" % issue)
    return registry


def _write_json_atomic(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


@contextmanager
def registry_lock(base):
    root = state_path(base)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "tasks.lock", "a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def save_registry(base, registry):
    _write_json_atomic(registry_path(base), registry)


def register(base, issue_key, status="active"):
    issue = validate_issue_key(issue_key)
    with registry_lock(base):
        registry = load_registry(base, create=True)
        entry = registry["tasks"].get(issue)
        timestamp = now()
        if entry is None:
            registry["tasks"][issue] = {
                "status": status,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        else:
            entry["status"] = status
            entry["updated_at"] = timestamp
        save_registry(base, registry)
    return registry["tasks"][issue]


def set_status(base, issue_key, status):
    if status not in ("active", "inactive", "completed"):
        raise ValueError("未知任务注册状态：%s" % status)
    issue = validate_issue_key(issue_key)
    with registry_lock(base):
        registry = load_registry(base, create=False)
        if registry is None or issue not in registry["tasks"]:
            raise ValueError("任务未注册：%s" % issue)
        registry["tasks"][issue]["status"] = status
        registry["tasks"][issue]["updated_at"] = now()
        save_registry(base, registry)


def registered_issues(base, statuses=None):
    registry = load_registry(base, create=False)
    if registry is None:
        return []
    accepted = set(statuses or ())
    return sorted(
        issue
        for issue, entry in registry["tasks"].items()
        if not accepted or entry.get("status") in accepted
    )


def resolve_issue(base, issue_key=None):
    if issue_key:
        issue = validate_issue_key(issue_key)
        if issue not in registered_issues(base):
            raise ValueError("任务未注册：%s" % issue)
        return issue
    active = registered_issues(base, statuses=("active",))
    if len(active) == 1:
        return active[0]
    if not active:
        raise ValueError("工作空间没有激活任务，请先 init 或 activate")
    raise ValueError(
        "工作空间有多个激活任务，必须显式提供 --issue-key：%s" % ", ".join(active)
    )


def resolve_active_issue(base, issue_key=None):
    issue = resolve_issue(base, issue_key)
    if issue not in registered_issues(base, statuses=("active",)):
        raise ValueError("任务不是 active，不能执行状态变更：%s" % issue)
    return issue


def _remove_empty_tree(path):
    for directory in sorted(
        (item for item in path.rglob("*") if item.is_dir()), reverse=True
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
    try:
        path.rmdir()
    except OSError:
        pass


def migrate_legacy(base):
    """把开发期 `.gate` 状态一次性迁入 `.agenticops/tasks`。"""
    legacy_root = workspace_path(base) / ".gate"
    if not legacy_root.is_dir():
        return None
    new_registry = registry_path(base)
    legacy_registry = legacy_root / "tasks.json"
    if legacy_registry.is_file():
        if new_registry.exists():
            raise ValueError("新旧任务注册表同时存在，拒绝自动合并")
        try:
            registry = json.loads(legacy_registry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("旧任务注册表无法迁移：%s" % error) from error
        tasks = registry.get("tasks")
        if not isinstance(tasks, dict):
            raise ValueError("旧任务注册表 tasks 必须是对象")
        migrations = []
        for issue in sorted(tasks):
            validate_issue_key(issue)
            source = legacy_root / "tasks" / issue
            destination = task_directory(base, issue)
            if not source.is_dir() or not (source / "task.json").is_file():
                raise ValueError("旧任务目录或 task.json 缺失：%s" % source)
            if destination.exists():
                raise ValueError("任务迁移目标已存在，拒绝覆盖：%s" % destination)
            migrations.append((source, destination))
        for source, destination in migrations:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(source), str(destination))
            old_state = destination / "task.json"
            os.replace(str(old_state), str(destination / "state.json"))
        new_registry.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(legacy_registry), str(new_registry))
        _remove_empty_tree(legacy_root)
        return "multiple"

    legacy_task = legacy_root / "task.json"
    if not legacy_task.is_file():
        _remove_empty_tree(legacy_root)
        return None
    try:
        task = json.loads(legacy_task.read_text(encoding="utf-8"))
        issue = validate_issue_key(task.get("issue_key"))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("旧版任务状态无法迁移：%s" % error) from error
    destination = task_directory(base, issue)
    if registry_path(base).exists():
        raise ValueError("新旧任务注册表同时存在，拒绝自动合并")
    if destination.exists():
        raise ValueError("任务迁移目标已存在，拒绝覆盖：%s" % destination)
    destination.mkdir(parents=True, exist_ok=True)
    legacy_files = [
        legacy_task,
        legacy_root / "authorization.json",
        legacy_root / "events.jsonl",
    ]
    legacy_files.extend(sorted(legacy_root.glob("ci-*.json")))
    pairs = []
    for source in legacy_files:
        if not source.exists():
            continue
        target_name = "state.json" if source.name == "task.json" else source.name
        target = destination / target_name
        if target.exists():
            raise ValueError("旧版状态迁移目标已存在，拒绝覆盖：%s" % target)
        pairs.append((source, target))
    for source, target in pairs:
        os.replace(str(source), str(target))
    registry = empty_registry(workspace_project(base))
    timestamp = now()
    registry["tasks"][issue] = {
        "status": "active",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    save_registry(base, registry)
    _remove_empty_tree(legacy_root)
    return issue
