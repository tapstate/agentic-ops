#!/usr/bin/env python3
"""单任务工位状态；只保留 current-task.json，不迁移旧状态。"""
from __future__ import annotations
import copy
import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
import fcntl

ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")
LEGACY_RUN_ID_PATTERN = re.compile(r"^run-[a-z0-9][a-z0-9-]*$")
CURRENT_RUN_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*-[0-9a-f]{8}$")
PREVIOUS_RUN_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*-[0-9a-f]{8}-[0-9a-f]{8}$")
RUN_ID_PATTERN = re.compile(r"^(?:run-[a-z0-9][a-z0-9-]*|[A-Z][A-Z0-9_]*-[1-9][0-9]*-[0-9a-f]{8}(?:-[0-9a-f]{8})?)$")
INTERACTION_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.(?:json|jsonl|log|md|txt))?$")
_held_locks = threading.local()

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

def station_path(base):
    return Path(base).resolve()

def state_path(base):
    return station_path(base) / ".agenticops"

def validate_issue_key(value):
    value = str(value or "").strip().upper()
    if not ISSUE_KEY_PATTERN.fullmatch(value):
        raise ValueError("Jira issue key 格式无效")
    return value


def timestamp_hex(seconds=None):
    """返回固定 8 位的 Unix 秒级 HEX；超出无符号 32 位范围时拒绝生成。"""
    if seconds is None:
        seconds = int(time.time())
    if type(seconds) is not int or not 0 <= seconds <= 0xFFFFFFFF:
        raise ValueError("秒级时间戳超出 8 位 HEX 可表示范围")
    return "%08x" % seconds


def new_run_id(issue_key, seconds=None):
    return "%s-%s" % (validate_issue_key(issue_key), timestamp_hex(seconds))


def validate_run_id_from_value(run_id):
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id 格式无效")
    return run_id


def validate_run_id(issue_key, run_id):
    issue = validate_issue_key(issue_key)
    validate_run_id_from_value(run_id)
    if (CURRENT_RUN_ID_PATTERN.fullmatch(run_id) or PREVIOUS_RUN_ID_PATTERN.fullmatch(run_id)) and not run_id.startswith(issue + "-"):
        raise ValueError("run_id 与 Jira issue key 不一致")
    return run_id


def validate_git_name(value):
    if not isinstance(value, str) or "/" in value:
        raise ValueError("git_name 必须是合法的单段 Git 分支前缀")
    from workflow import engineering_baseline
    try:
        engineering_baseline.ref_name(value)
    except ValueError as error:
        raise ValueError("git_name 必须是合法的单段 Git 分支前缀") from error
    return value


def station_git_name(base):
    path = state_path(base) / "station.json"
    if path.is_symlink():
        raise ValueError("工位配置不能是符号链接")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("工位配置无法读取") from error
    identity = document.get("branch_identity")
    if not isinstance(identity, dict):
        raise ValueError(
            "工位未配置 git_name；请执行 agenticops station identity --station <目录> "
            "。它会读取全局 Git user.name 并一次性保存，用于稳定生成任务工作分支，避免恢复时受机器配置变化影响"
        )
    if set(identity) != {"schema_version", "git_name", "source"} or identity.get("schema_version") != 1:
        raise ValueError("工位 git_name 配置结构无效")
    if identity.get("source") != "git_global_user_name":
        raise ValueError("工位 git_name 配置来源无效")
    return validate_git_name(identity.get("git_name"))


def generated_work_branch(base, task):
    return "%s/%s" % (station_git_name(base), validate_run_id(task["issue_key"], task["run_id"]))

def current_path(base):
    return state_path(base) / "current-task.json"

def read_current(base):
    path = current_path(base)
    if state_path(base).is_symlink():
        raise ValueError("工位状态目录不能是符号链接")
    if any((state_path(base) / name).exists() or (state_path(base) / name).is_symlink() for name in ("tasks", "worktrees")):
        raise ValueError("旧工位必须使用原版本受控解绑并重建")
    if path.is_symlink():
        raise ValueError("当前状态不能是符号链接")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("当前工位状态缺失或无法读取") from error
    if (not isinstance(document, dict)
            or set(document) != {"schema_version", "revision", "current"}
            or document["schema_version"] != 1
            or type(document["revision"]) is not int or document["revision"] < 0):
        raise ValueError("工位状态结构无效")
    current = document["current"]
    if current is not None and (not isinstance(current, dict)
            or validate_issue_key(current.get("issue_key")) != current.get("issue_key")):
        raise ValueError("当前任务身份无效")
    if current is not None:
        try:
            validate_run_id(current["issue_key"], current.get("run_id"))
        except ValueError as error:
            raise ValueError("当前任务身份无效") from error
    return document

def initialize_current(base):
    if current_path(base).exists():
        return read_current(base)
    if any((state_path(base) / name).exists() for name in ("tasks", "worktrees")):
        raise ValueError("不能在线迁移旧任务")
    value = {"schema_version": 1, "revision": 0, "current": None}
    _write_json_atomic(current_path(base), value)
    return value

def compare_and_set(base, expected_revision, current):
    before = read_current(base)
    if before["revision"] != expected_revision:
        raise ValueError("工位 revision 已变化：expected=%s actual=%s；请读取 task.py status，不使用质量日志 revision" % (expected_revision, before["revision"]))
    value = {"schema_version": 1, "revision": expected_revision + 1, "current": copy.deepcopy(current)}
    _write_json_atomic(current_path(base), value)
    return value

def read_task(base, issue_key=None):
    value = read_current(base)
    task = value["current"]
    if task is None:
        return None
    if issue_key is not None and validate_issue_key(issue_key) != task["issue_key"]:
        raise ValueError("请求任务不是当前任务")
    task = copy.deepcopy(task)
    task["_revision"] = value["revision"]
    # 现有质量工具消费的字段视图，不再持久化第二份仓库基线或 worktree 状态。
    entries = task.get("engineering_baseline", {}).get("repositories", {})
    repositories = []
    for name, binding in task.get("task_repositories", {}).items():
        if name not in entries:
            raise ValueError("任务仓库不属于冻结工程基线")
        entry = entries[name]
        observation = binding.get("observation") or {}
        results = observation.get("results", {})
        from workflow.project_rules import canonical_repository_endpoint
        repositories.append({
            "repository": name, "authorized_endpoint": canonical_repository_endpoint(entry["origin"]),
            "base_branch": binding["target_branch"], "base_sha": entry["commit_sha"],
            "work_branch": binding["work_branch"], "approved_scope": "\n".join(binding["approved_scope"]),
            "verification_method": binding["verification_method"],
            "catalog_digest": binding["baseline_entry_digest"],
            "worktree": ({"path": str(station_path(base) / entry["path"]), "status": "prepared"}
                         if task.get("source_prepared") else None),
            "pull_request": results.get("pull_request"), "ci": results.get("ci"),
        })
    task["repositories"] = repositories
    return task

def write_task(base, task):
    value = copy.deepcopy(task)
    expected = value.pop("_revision", None)
    repositories = value.pop("repositories", [])
    for row in repositories:
        binding = value.get("task_repositories", {}).get(row["repository"])
        if binding is None:
            raise ValueError("必须先登记 task_repositories，不能写入旧仓库清单")
        observation = binding.get("observation") or {}
        observation["results"] = {"pull_request": row.get("pull_request"), "ci": row.get("ci")}
        binding["observation"] = observation
    before = read_current(base)
    current = before["current"]
    if current is not None:
        if (current["issue_key"], current["run_id"]) != (value["issue_key"], value["run_id"]):
            raise ValueError("工位已占用，禁止覆盖当前任务")
        if expected is None:
            raise ValueError("写入缺少预期 revision")
    elif expected is None:
        expected = before["revision"]
    result = compare_and_set(base, expected, value)
    task["_revision"] = result["revision"]

def task_path(base, issue_key):
    validate_issue_key(issue_key)
    return current_path(base)

def task_directory(base, issue_key):
    resolve_issue(base, issue_key)
    return state_path(base) / "evidence"

def authorization_path(base, issue_key):
    resolve_issue(base, issue_key)
    return state_path(base) / "authorization.json"

def events_path(base, issue_key):
    return task_directory(base, issue_key) / "events.jsonl"

def ci_path(base, issue_key, pr):
    return task_directory(base, issue_key) / ("ci-%s.json" % pr)

def interaction_directory(base, issue_key, run_id):
    check_expected_run(base, issue_key, run_id)
    return task_directory(base, issue_key) / "interactions"

def prepare_interaction_directory(base, issue_key, run_id, create=False, require=False):
    directory = interaction_directory(base, issue_key, run_id)
    for path in (directory.parent, directory):
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise ValueError("交互目录必须为真实目录")
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
    if require and not directory.is_dir():
        raise ValueError("交互目录缺失")
    return directory

def interaction_path(base, issue_key, run_id, name, create=False):
    if not isinstance(name, str) or not INTERACTION_NAME_PATTERN.fullmatch(name):
        raise ValueError("交互文件名无效")
    path = prepare_interaction_directory(base, issue_key, run_id, create=create, require=create) / name
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError("交互文件不是普通文件")
    return path

def station_project(base):
    from workflow import project_rules
    return project_rules.project_from_station(base)

def _write_json_atomic(path, document):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("不能覆盖符号链接状态")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        parent = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)

def _active_product_lifecycle(product_root):
    lock = Path(product_root).resolve() / ".local" / "lifecycle.lock"
    if not lock.is_dir():
        return None
    try:
        owner_text = (lock / "owner").read_text(encoding="utf-8").strip()
        owner = int(owner_text)
    except (OSError, ValueError):
        return "unknown"
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    try:
        operation = (lock / "operation").read_text(encoding="utf-8").strip()
    except OSError:
        operation = "unknown"
    return operation or "unknown"


def _require_station_epoch_supported(base, product_root):
    """只允许当前产品 epoch 的已初始化工位写入。"""
    manifest_path = (
        Path(product_root).resolve()
        / "contracts"
        / "station-state-compatibility.json"
    )
    init_path = state_path(base) / "init.json"
    if not manifest_path.is_file() or not init_path.is_file():
        raise ValueError("工位兼容性清单或初始化标记缺失，请受控解绑并重建")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        init = json.loads(init_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工位状态代际无法核验：%s" % error) from error
    if (
        not isinstance(manifest, dict)
        or not isinstance(init, dict)
        or set(manifest) != {"station_state_epoch"}
    ):
        raise ValueError("工位状态兼容性清单或代际标记无效")
    product_epoch = manifest.get("station_state_epoch")
    epoch = init.get("station_state_epoch")
    if (
        type(product_epoch) is not int
        or product_epoch < 1
        or type(epoch) is not int
        or epoch < 1
    ):
        raise ValueError("工位状态兼容性清单或代际标记无效")
    if epoch != product_epoch:
        raise ValueError(
            "工位状态代际 %s 与当前产品不兼容；请使用可处理该状态的原版本，"
            "保存材料后将这个旧工位受控解绑并重建；repair 不执行跨代际采用" % epoch
        )


@contextmanager
def task_state_lock(
    base,
    allow_product_lifecycle=False,
    allow_incompatible_station=False,
):
    """持有工位状态目录锁，并在获得锁后重新核验工位绑定。

    任务事实必须只写入项目工位。使用 ``.agenticops`` 目录自身作为锁对象，
    可使普通任务写入不依赖 Product Root 的 ``.local``，同时让 purge 在删除状态
    目录前与所有任务写入互斥。
    """
    base = station_path(base)
    held = getattr(_held_locks, "stations", None)
    if held is None:
        held = _held_locks.stations = set()
    # 仅同一进程、同一线程的嵌套 Workflow 调用复用锁；线程/进程间仍互斥。
    lock_key = (os.getpid(), str(base))
    if lock_key in held:
        yield
        return
    state_root = state_path(base)
    binding_path = state_root / "station.json"
    if state_root.is_symlink() or binding_path.is_symlink():
        raise ValueError("工位状态目录与绑定不能是符号链接")
    try:
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工位绑定无法读取：%s" % error) from error
    product_root = binding.get("product_root")
    if not isinstance(product_root, str) or not product_root:
        raise ValueError("工位绑定缺少 product_root")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(state_root), flags)
    except OSError as error:
        raise ValueError("工位状态目录无法加锁：%s" % error) from error
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            opened = os.fstat(descriptor)
            try:
                current_directory = os.stat(state_root)
            except OSError as error:
                raise ValueError("获得任务状态锁后工位状态目录无法读取：%s" % error) from error
            if (opened.st_dev, opened.st_ino) != (current_directory.st_dev, current_directory.st_ino):
                raise ValueError("获得任务状态锁后工位状态目录已替换，拒绝继续")
            try:
                current = json.loads(binding_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError("获得任务状态锁后工位绑定无法读取：%s" % error) from error
            current_root = current.get("product_root")
            if (current != binding or state_root.is_symlink() or binding_path.is_symlink()
                    or not isinstance(current_root, str) or Path(current_root).resolve() != Path(product_root).resolve()):
                raise ValueError("获得任务状态锁后工位绑定已变化，拒绝继续")
            lifecycle = _active_product_lifecycle(current_root)
            if lifecycle and not allow_product_lifecycle:
                raise ValueError(
                    "Product Root 正在执行生命周期操作，任务状态暂不可变更：%s"
                    % lifecycle
                )
            if not allow_incompatible_station:
                _require_station_epoch_supported(base, current_root)
            held.add(lock_key)
            try:
                yield
            finally:
                held.remove(lock_key)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def task_run_lock(base, issue_key):
    validate_issue_key(issue_key)
    with task_state_lock(base):
        yield



def check_expected_run(base, issue_key, expected_run_id):
    if not expected_run_id:
        raise ValueError("状态变更需要 --expected-run-id")
    task = read_task(base, issue_key)
    if task is None or task["run_id"] != expected_run_id:
        raise ValueError("任务 run 已变化，拒绝旧请求")
    return task

def require_development(base, task):
    if task.get("archive_ref") or task.get("outcome") in ("completed", "interrupted"):
        raise ValueError("任务已归档或终止，只允许释放或清理")
    path = state_path(base) / "operation.json"
    if path.exists() and json.loads(path.read_text()).get("status") != "done":
        raise ValueError("工位有未完成操作，请先恢复")

def task_mutation(function):
    @wraps(function)
    def locked(args):
        with task_state_lock(args.dir):
            issue = resolve_issue(args.dir, args.issue_key)
            task = check_expected_run(args.dir, issue, getattr(args, "expected_run_id", None))
            require_development(args.dir, task)
            args.issue_key = issue
            return function(args)
    return locked

def registered_issues(base, statuses=None):
    task = read_task(base)
    if task is None:
        return []
    status = "completed" if task.get("outcome") == "completed" else "active"
    return [task["issue_key"]] if not statuses or status in statuses else []

def resolve_issue(base, issue_key=None):
    task = read_task(base, issue_key)
    if task is None:
        raise ValueError("工位没有当前任务，请先接管")
    return task["issue_key"]

def resolve_active_issue(base, issue_key=None):
    task = read_task(base, issue_key)
    if task is None:
        raise ValueError("工位没有当前任务")
    require_development(base, task)
    return task["issue_key"]

def task_status(base, issue_key):
    task = read_task(base, issue_key)
    if task is None:
        raise ValueError("工位没有当前任务")
    return "completed" if task.get("outcome") == "completed" else "active"

def set_status(base, issue_key, status):
    if status != "completed":
        raise ValueError("不再支持激活/停用多任务；请归档后释放或清理")
    task = read_task(base, issue_key)
    task["outcome"] = "completed"
    write_task(base, task)
