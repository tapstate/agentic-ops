"""按 run 登记的资源与精确清理清单；不扫描或停止未知进程。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from workflow import engineering_baseline as baseline, station_operation as operations, project_rules
from workflow import station_source as source, task_store as store
from workflow import station_directories as directories, station_artifacts as artifacts


def resource_path(base, relative, leaf_link=False):
    if not isinstance(relative, str):
        raise ValueError("资源路径必须是相对路径")
    parts = relative.split("/")
    if any(part in ("", ".", "..", ".git") for part in parts) or parts[0] not in ("runtime", "source"):
        raise ValueError("资源只能位于 runtime 或 source，不能包括 Git 元数据")
    root = Path(base).resolve()
    path = root
    for index, part in enumerate(parts):
        path = path / part
        if path.is_symlink() and not (leaf_link and index == len(parts) - 1):
            raise ValueError("资源路径不能包含符号链接：%s" % relative)
    return path


def fingerprint(path):
    if path.is_symlink():
        data = os.fsencode(os.readlink(path))
        return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                "mode": stat.S_IMODE(path.lstat().st_mode), "link": True}
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("清理只接受普通文件：%s" % path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    metadata = path.stat()
    return {"sha256": digest.hexdigest(), "size": metadata.st_size, "mode": stat.S_IMODE(metadata.st_mode)}


def inventory(base, task):
    path = store.task_directory(base, task["issue_key"]) / "resources.json"
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("资源登记不能是符号链接")
    document = json.loads(path.read_text())
    if document.get("run_id") != task["run_id"] or not isinstance(document.get("entries"), list):
        raise ValueError("资源登记不属于当前 run")
    return document["entries"]


def verify_stopped(base, task, require_cleaned=False):
    for item in inventory(base, task):
        if item.get("kind") == "process":
            pid = item.get("pid")
            if type(pid) is not int or pid <= 1 or not item.get("started_at"):
                raise ValueError("进程身份登记不完整")
            result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode not in (0, 1):
                raise ValueError("无法核验登记进程是否停止")
            if result.stdout.strip() == item["started_at"]:
                raise ValueError("登记写入进程仍存活，请先按身份核验后停止：%s" % pid)
        elif item.get("kind") == "external":
            if item.get("status") in ("intent", "unknown"):
                raise ValueError("外部写入结果未知，先回读原操作")
            if item.get("resource_type") in ("git-branch", "pull-request"):
                continue
            if item.get("action") == "retain" and not item.get("isolation_proof"):
                raise ValueError("运行资源保留必须有无污染隔离证据")
            terminal = "retained" if item.get("action") == "retain" else "cleaned"
            accepted = (terminal,) if require_cleaned else ("quiesced", terminal)
            if item.get("status") not in accepted or not item.get("readback_ref"):
                raise ValueError("外部资源尚未回读%s，请人工处理：%s" % (
                    "清理完成" if require_cleaned else "停止写入", item.get("id")))


def active_files(base):
    """活动材料的授权指纹；资源回读单独校验，不让回读改变已确认身份。"""
    root = store.state_path(base)
    result = {}
    for name in ("authorization.json", "evidence"):
        target = root / name
        paths = [target] + sorted(target.rglob("*")) if target.is_dir() else [target]
        for path in paths:
            if path.is_symlink() or (path.exists() and not path.is_file() and not path.is_dir()):
                raise ValueError("活动材料含符号链接或特殊对象")
            if path.is_file():
                data = path.read_bytes()
                if path == root / "evidence/resources.json":
                    document = json.loads(data)
                    document.setdefault("schema_version", 1)
                    for entry in document.get("entries", []):
                        if entry.get("kind") == "external":
                            entry.pop("status", None)
                            entry.pop("readback_ref", None)
                    data = json.dumps(document, sort_keys=True, ensure_ascii=False).encode("utf-8")
                result[path.relative_to(root).as_posix()] = hashlib.sha256(data).hexdigest()
    return result


def task_fingerprint(task):
    """排除本次退出自身产生的 outcome/archive/revision，绑定开发事实与范围。"""
    return baseline.digest({"issue_key": task["issue_key"], "run_id": task["run_id"],
        "facts": task.get("facts"), "pending": task.get("pending"), "history": task.get("history"),
        "engineering_baseline": task.get("engineering_baseline"),
        "reset_baseline": task.get("reset_baseline"),
        "bindings": {name: {key: binding.get(key) for key in ("work_branch", "target_branch", "approved_scope", "verification_method", "baseline_entry_digest")}
                     for name, binding in task.get("task_repositories", {}).items()}})


def verify_active(base, cleanup_plan, operation):
    expected = cleanup_plan["active_state"]["files"]
    actual = active_files(base)
    name = "clear-active:" + str(len(operation.get("plan_revisions", []))) + ":" + cleanup_plan["digest"]
    step = operation.get("steps", {}).get(name)
    if step is None:
        valid = actual == expected
    elif step.get("receipt") is not None:
        valid = not actual
    else:
        valid = all(name in expected and expected[name] == sha for name, sha in actual.items())
    if not valid:
        raise ValueError("活动材料变化，需要重新确认清理计划")


def verify_station_inventory(base, rules=None, allow_pending=False):
    """保留工位根 IDE 配置；其它未知对象阻止复用，不猜测其所有权。"""
    root = Path(base).resolve()
    state = store.state_path(base)
    init = json.loads((state / "init.json").read_text())
    owned = {entry["path"]: entry for entry in init.get("artifacts", [])}
    from workflow import station_clean_rules
    task = store.read_task(base)
    registered = directories.load(base, task) if task else {}
    observed = station_clean_rules.inspect(base, owned, registered)
    if rules is not None and observed["digests"] != rules["digests"]:
        raise ValueError("清理配置已变化，需要重新确认")
    def inspect(directory, prefix=""):
        for path in directory.iterdir():
            relative = prefix + path.name
            if not prefix and allow_pending and relative in registered and observed["objects"].get(relative, {}).get("action") == "remove":
                continue
            if not prefix and relative in observed["objects"] and observed["objects"][relative]["action"] == "preserve":
                continue
            if not prefix and path.name in ("config", "source", "runtime", "archive", ".agenticops"):
                if path.is_symlink() or not path.is_dir():
                    raise ValueError("工位持久目录异常：" + relative)
                continue
            if relative in owned:
                record = owned[relative]
                if record.get("kind", "file") == "symlink":
                    valid = path.is_symlink() and os.readlink(path) == record.get("target")
                else:
                    valid = path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == record.get("sha256")
                if not valid:
                    raise ValueError("初始化接线已变化：" + relative)
                continue
            if any(item.startswith(relative + "/") for item in owned) and path.is_dir() and not path.is_symlink():
                inspect(path, relative + "/")
            else:
                raise ValueError("工位存在未知材料，保留并停止解绑：" + relative)
    inspect(root)
    allowed = {"station.json", "init.json", "current-task.json", "operation.json", "events.jsonl",
               "git-ref-cache-v2.json", "git-ref-cache-v2.json.lock", "authorization.json", "evidence", "operation-data"}
    for path in state.iterdir():
        if path.name not in allowed or path.is_symlink():
            raise ValueError("工位状态包含未知对象：" + path.name)
        if path.name not in ("evidence", "operation-data") and not path.is_file():
            raise ValueError("工位状态对象类型异常：" + path.name)
    blobs = state / "operation-data"
    if blobs.exists():
        if not blobs.is_dir():
            raise ValueError("操作材料目录类型异常")
        for blob in blobs.iterdir():
            if blob.is_symlink() or not blob.is_file() or blob.suffix != ".json" or baseline.digest(json.loads(blob.read_text())) != blob.stem:
                raise ValueError("操作材料包含未知或损坏对象")
    operations.read(base)


def preflight(base, task):
    """只读预检允许写入者仍运行；错误作为阻塞展示，不推定现场可清理。"""
    operation = operations.read(base)
    value = {"issue_key": task["issue_key"], "run_id": task["run_id"],
             "revision": task.get("_revision"), "stage": task.get("stage"),
             "outcome": task.get("outcome"), "pending": task.get("pending"),
             "archive_ref": task.get("archive_ref"),
             "operation": {k: operation[k] for k in ("operation_id", "kind", "status", "phase")} if operation else None,
             "decision_required": not bool(task.get("archive_ref")), "blockers": [],
             "task_repositories": task.get("task_repositories", {})}
    try:
        value["resources"] = inventory(base, task)
        value["active_files"] = active_files(base)
        value["cleanup_plan"] = plan(base, task)
        verify_stopped(base, task)
        verify_known_external(base, task)
        discarded = [e for e in value["cleanup_plan"]["entries"] if e["preservation"]["action"] == "discard"]
        if discarded:
            value["discard_digest"] = baseline.digest(discarded)
    except (ValueError, OSError) as error:
        value["blockers"].append(str(error))
    value["digest"] = baseline.digest(value)
    return value


def require_cleanup_version(version):
    if type(version) is not int or version != 6:
        raise ValueError("仅支持清理计划版本 6；旧清理合同必须由原版本退出，不在线迁移")


def plan(base, task, version=None, decisions_override=None):
    require_cleanup_version(6 if version is None else version)
    # 操作读取器统一拒绝旧计划（含 handoff 和计划修订），不在线转换。
    operation = operations.read(base) or {}
    roots = {name: entry for name, entry in directories.load(base, task).items()
             if entry["kind"] != "source-generated"}
    if "runtime" not in roots:
        original = operation.get("previous_operation", operation)
        if (original.get("kind") != "takeover" or original.get("run_id") != task["run_id"]
                or original.get("steps") or not task.get("initial_runtime")):
            raise ValueError("runtime 缺少当前 run 的目录归属")
        roots["runtime"] = task["initial_runtime"]
        path = directories.validate(base, roots["runtime"])
        if any(path.iterdir()):
            raise ValueError("尚未启动生产者的 runtime 出现未知材料")
    for entry in roots.values():
        # 已确认回收的工位根可以缺失，身份与原确认由成果核验器检查。
        directories.validate(base, entry, missing=True)
    engineering = task.get("engineering_baseline", {})
    repositories = engineering.get("repositories", {}) if task.get("source_prepared") else _partial_repositories(base)
    catalog = project_rules.load_repository_catalog(station=base)["repositories"]
    if source.check_station_layout(base, catalog, repositories, task.get("replan_preserved")) != task.get("retained_repositories", {}):
        raise ValueError("未选择的持久仓库状态变化")
    entries, states = [], {}
    decisions = {item["path"]: item["preservation"] for item in inventory(base, task) if item.get("kind") == "source-disposition"}
    if decisions_override:
        decisions.update(decisions_override)
    for name, row in repositories.items():
        repository = source.repository_path(base, name)
        source.identity(repository, row["origin"])
        if row.get("initial_checkout"):
            states[name] = {"origin": row["origin"], "initial_checkout": True}
            continue
        target = task.get("reset_baseline", {}).get(name)
        if not target or source.git(repository, "cat-file", "-t", target["sha"]).stdout.strip() != "commit":
            raise ValueError("缺少已核验开发基线，拒绝猜测源码归位")
        state = artifacts.snapshot(base, name, roots, decisions, target["sha"], include_ignored=True)
        for entry in state.pop("entries"):
            entries.append(entry)
        preserved_head = state["head"]
        if operation.get("run_id") == task["run_id"]:
            previous_source = operation.get("cleanup_plan", {}).get("source", {}).get(name, {})
            if previous_source and operation.get("status") != "done":
                if state["head"] not in (previous_source["head"], previous_source["neutral"]["sha"]):
                    raise ValueError("源码归位后 Head 已变化，不能补充清理")
                preserved_head = previous_source["preserved_head"]
            neutral_step = operation.get("steps", {}).get("neutral:" + name)
            if neutral_step:
                prior = neutral_step["expected"]
                if state["head"] not in (prior["preserved_head"], prior["head"]):
                    raise ValueError("源码归位后 Head 已变化，不能补充清理")
                preserved_head = prior["preserved_head"]
        state.update(origin=row["origin"], neutral=target, preserved_head=preserved_head,
                     preserved_ref="refs/agenticops/archive/" + task["run_id"] + "/" + baseline.digest(name)[:24])
        from workflow import station_reset_result
        state["directories"] = station_reset_result.source_directories(repository)
        state["refs"] = station_reset_result.refs(repository)
        state["checkout_branch"] = None  # 清理只检出精确 SHA，不移动任何命名分支。
        branch_ref = "refs/heads/" + state["checkout_branch"] if state["checkout_branch"] else None
        if branch_ref in state["refs"] and state["refs"][branch_ref] != target["sha"]:
            raise ValueError("受管清理基线分支已指向不同提交")
        states[name] = state
    external = []
    for item in inventory(base, task):
        if item.get("kind") == "external":
            if item.get("resource_type") in ("git-branch", "pull-request"):
                action = item.get("action", "retain")
                expected = "delete" if item["resource_type"] == "git-branch" else "close"
                if action not in ("retain", expected):
                    raise ValueError("分支/PR 处置动作不匹配：" + item["id"])
                if action != "retain" and (item["before"].get("protected") is not False or not item.get("decision_ref")):
                    raise ValueError("分支/PR 删除须有保护回读与用户决定：" + item["id"])
                external.append(dict({k: v for k, v in item.items() if k not in ("status", "readback_ref")}, action=action))
            else:
                external.append({k: v for k, v in item.items() if k not in ("status", "readback_ref")})
    logs = ["logs", "reports"]
    for relative in logs:
        directories.path_at(base, "runtime/" + relative)
    value = {"schema_version": 6, "run_id": task["run_id"], "directories": [dict(entry, observed_missing_before_intent=not directories.path_at(base, entry["path"]).exists()) for entry in roots.values()], "archive_runtime": logs,
             "entries": entries, "source": states, "external": external,
             "active_state": {"files": active_files(base), "unbind_run": task["run_id"], "task_digest": task_fingerprint(task)},
             "retained": ["config", "source repositories and refs", "Product Root .archive", ".agenticops binding and operation"]}
    from workflow import station_cleanup_stages
    for entry in external:
        if entry["action"] == "delete" and station_cleanup_stages.local(entry):
            station_cleanup_stages.local_identity(base, task, {"cleanup_plan": value}, entry)
    from workflow import station_clean_rules
    init = json.loads((store.state_path(base) / "init.json").read_text())
    value["rules"] = station_clean_rules.inspect(base, [e["path"] for e in init.get("artifacts", [])], roots)
    for entry in value["directories"]:
        if entry["kind"] == "station-generated" and value["rules"]["objects"].get(entry["path"], {}).get("action", "remove") != "remove":
            raise ValueError("保留名单与登记目录回收冲突：" + entry["path"])
    verify_station_inventory(base, value["rules"], allow_pending=True)
    # 内部证据/回执不属于删除授权范围；源码成果、目录归属、开发目标才属于。
    value["digest"] = baseline.digest(value)
    from workflow import quality_contract
    quality_contract.validate(value, "station-reset.schema.json")
    from workflow.station_clean_rules import validate_snapshot
    validate_snapshot(value)
    return value


def verify_known_external(base, task):
    from workflow import external_sync
    if any(row["status"] == "unknown" for row in external_sync.warnings(base, task)):
        raise ValueError("当前 run 有外部写入结果未知，须回读原操作；不能改 retain 绕过")


def clean(base, task, cleanup_plan, confirmed_digest, operation, source_only=False, directories_only=False, only_repository=None):
    payload = {key: value for key, value in cleanup_plan.items() if key != "digest"}
    if (cleanup_plan.get("schema_version") != 6 or cleanup_plan.get("run_id") != task["run_id"]
            or baseline.digest(payload) != confirmed_digest or cleanup_plan.get("digest") != confirmed_digest):
        raise ValueError("清理确认与当前 run 或精确清单不匹配")
    if not task.get("archive_ref"):
        raise ValueError("正式归档未绑定，不能清理")
    verify_active(base, cleanup_plan, operation)
    verify_stopped(base, task, require_cleaned=not source_only)
    verify_known_external(base, task)
    terminal = [item for item in inventory(base, task) if item.get("kind") == "external" and item.get("resource_type") not in ("git-branch", "pull-request")]
    if terminal and not source_only:
        if project_rules.scan_sensitive(project_rules.load_admission(station=base), json.dumps(terminal, ensure_ascii=False)):
            raise ValueError("外部资源最终回执含敏感信息，请先脱敏")
        record = {"run_id": task["run_id"], "archive_digest": task["archive_ref"]["digest"], "resources": terminal}
        from workflow import archive_store
        receipt = archive_store.receipts(base, task["archive_ref"], create=True) / ("external-terminal-" + baseline.digest(record) + ".json")
        if receipt.parent.is_symlink() or receipt.is_symlink():
            raise ValueError("外部资源回执不能为链接")
        if not receipt.exists():
            store._write_json_atomic(receipt, record)
        if json.loads(receipt.read_text()) != record:
            raise ValueError("外部资源最终回执回读不一致")
    artifacts.verify_coverage(base, task, cleanup_plan)
    from workflow import station_logs
    station_logs.verify_current(base, task, cleanup_plan)
    grouped = {}
    for entry in cleanup_plan["entries"]:
        if directories_only:
            break
        if only_repository is None or entry["repository"] == only_repository:
            grouped.setdefault(entry["repository"], []).append(entry)
    for repository_name, entries in grouped.items():
        repository = source.repository_path(base, repository_name)
        head = entries[0]["head"]
        observed_head = source.git(repository, "rev-parse", "HEAD").stdout.strip()
        if observed_head != head:
            from workflow import station_reset_result
            station_reset_result.source_result(base, task, repository_name, cleanup_plan["source"][repository_name])
            continue
        name = "source-reset:%s:%s:%s" % (len(operation.get("plan_revisions", [])), cleanup_plan["digest"], repository_name)
        expected = {"repository": repository_name, "head": head, "entries_digest": baseline.digest(entries)}
        step = operations.intent(base, operation, name, {}, expected)
        if source.git(repository, "rev-parse", "HEAD").stdout.strip() != head:
            raise ValueError("源码 Head 已变化，拒绝恢复")
        if step["receipt"] is not None:
            if artifacts.snapshot(base, repository_name, {e["path"]: e for e in cleanup_plan["directories"]}, {}, include_ignored=True)["entries"]:
                raise ValueError("源码清理后再次变化，拒绝重删")
            continue
        from workflow import station_reset_result
        original = {entry["path"]: entry for entry in entries}
        for observed in artifacts.snapshot(base, repository_name, {}, {}, include_ignored=True)["entries"]:
            prior = original.get(observed["path"])
            unchanged = prior and all(observed[k] == prior[k] for k in
                ("head", "before", "before_index", "index_patch", "worktree_patch", "file_identity"))
            if not unchanged and not station_reset_result._pending_added_file(repository, operation, repository_name, prior, observed):
                raise ValueError("清理确认后源码内容变化：" + observed["path"])
        # 在任何写入前核对整仓快照；执行只使用明确路径，不进行宽泛 clean。
        tracked = set(filter(None, artifacts.git_bytes(repository, "ls-tree", "-r", "--name-only", "-z", head).decode().split("\0")))
        restore, additions, remove = [], [], []
        for entry in entries:
            target = resource_path(base, entry["path"], leaf_link=True)
            before = fingerprint(target)
            info = target.lstat() if target.exists() or target.is_symlink() else None
            identity = {"device": info.st_dev, "inode": info.st_ino, "mtime_ns": info.st_mtime_ns} if info else None
            if entry["action"] == "delete":
                if before is not None and (before != entry["before"] or identity != entry["file_identity"]):
                    raise ValueError("清理确认后文件变化：" + entry["path"])
                remove.append(target)
            elif entry["action"] == "restore":
                if entry["file"] in tracked:
                    restore.append(entry["file"])
                else:
                    additions.append(entry["file"])
                    if before is not None and (before != entry["before"] or identity != entry["file_identity"]):
                        raise ValueError("暂存新增文件在清理期间变化：" + entry["path"])
                    remove.append(target)
            else:
                raise ValueError("未知清理动作")
        # 有界批次避免 ARG_MAX；literal pathspec 防止文件名被当成 glob 或 magic。
        for offset in range(0, len(restore), 128):
            source.git(repository, "--literal-pathspecs", "restore", "--source=" + head,
                       "--staged", "--worktree", "--", *restore[offset:offset + 128])
        for offset in range(0, len(additions), 128):
            source.git(repository, "--literal-pathspecs", "rm", "--cached", "--ignore-unmatch",
                       "--", *additions[offset:offset + 128])
        for target in remove:
            if target.exists() or target.is_symlink():
                target.unlink()
                directories.sync(target.parent)
        operations.receipt(base, operation, name, expected)
    if source_only:
        return
    for entry in cleanup_plan["directories"]:
        if entry["kind"] == "source-generated":
            raise ValueError("版本 6 源码必须按文件快照处理，不能整目录回收")
        print("[station-reset] 回收目录 " + entry["path"], file=sys.stderr, flush=True)
        directories.reset(base, task, entry, operation)


def _partial_repositories(base):
    operation = operations.read(base) or {}
    operation = operation.get("previous_operation", operation)
    repositories = {}
    for name, step in operation.get("steps", {}).items():
        if not name.startswith("clone:"):
            continue
        repository = name[len("clone:"):]
        path = source.repository_path(base, repository)
        if not path.exists():
            continue
        origin = step["expected"]["origin"]
        source.identity(path, origin)
        initial_checkout = (not step["before"].get("exists", True)
                            and not (path / ".git/index").exists()
                            and {item.name for item in path.iterdir()} == {".git"})
        if not initial_checkout:
            source.require_clean(path)
        if source.git(path, "ls-files", "--others", "--ignored", "--exclude-standard").stdout:
            raise ValueError("未完成接管的源码含未知产物，需人工核验")
        repositories[repository] = {"origin": origin, "initial_checkout": initial_checkout}
    return repositories


def neutral(base, task, operation, only_repository=None):
    plan = operation["cleanup_plan"]
    require_cleanup_version(plan.get("schema_version"))
    catalog = project_rules.load_repository_catalog(station=base)["repositories"]
    if source.check_station_layout(base, catalog, plan["source"], task.get("replan_preserved")) != task.get("retained_repositories", {}):
        raise ValueError("未选择的持久仓库状态变化")
    for name, entry in plan["source"].items():
        if only_repository is not None and name != only_repository:
            continue
        path = source.repository_path(base, name)
        source.identity(path, entry["origin"])
        if entry.get("initial_checkout"):
            if {p.name for p in path.iterdir()} != {".git"}:
                raise ValueError("未完成检出仓库出现未知内容")
            continue
        from workflow import station_reset_result
        try:
            station_reset_result.source_result(base, task, name, entry)
        except ValueError:
            pass
        else:
            continue
        source.require_clean(path)
        if source.git(path, "ls-files", "--others", "--ignored", "--exclude-standard").stdout:
            raise ValueError("源码仍含未清理 ignored 产物")
        ref = entry["preserved_ref"]
        target = entry["neutral"]["sha"]
        checkout_branch = entry["checkout_branch"]
        artifacts.verify_special_entries(path, target)
        preserved_head = entry["preserved_head"]
        expected = {"head": target, "checkout_mode": "managed_branch" if checkout_branch else "detached",
                    "checkout_branch": checkout_branch, "preserved_ref": ref, "preserved_head": preserved_head}
        step = operations.intent(base, operation, "neutral:" + name, {"head": preserved_head}, expected)
        head = source.git(path, "rev-parse", "HEAD").stdout.strip()
        if head not in (entry["head"], target):
            raise ValueError("源码归位前 Head 已变化")
        previous = source.git(path, "rev-parse", "--verify", ref, check=False)
        if previous.returncode == 0 and previous.stdout.strip() != preserved_head:
            raise ValueError("源码保留引用已存在且指向不同成果")
        if previous.returncode:
            source.git(path, "update-ref", ref, preserved_head, "0" * len(preserved_head))
        if step["receipt"] is not None and (head != target or source.git(path, "branch", "--show-current").stdout.strip() != (checkout_branch or "")):
            raise ValueError("源码归位后被再次改变")
        if checkout_branch:
            current = source.git(path, "rev-parse", "--verify", "refs/heads/" + checkout_branch, check=False)
            if current.returncode:
                source.git(path, "branch", checkout_branch, target)
            elif current.stdout.strip() != target:
                raise ValueError("受管清理基线分支已变化")
            source.git(path, "checkout", checkout_branch)
        else:
            source.git(path, "-c", "submodule.recurse=false", "checkout", "--detach", target)
        artifacts.verify_special_entries(path, target)
        operations.receipt(base, operation, "neutral:" + name, expected)


def delivery_head_matches(base, name, observed, expected_head, operation):
    """交付候选保持不变；已执行归位时核验原候选引用及已记录的开发提交。"""
    if observed["head"] == expected_head:
        return True
    plan = operation.get("cleanup_plan", {})
    if plan.get("schema_version") == 6:
        entry = plan.get("source", {}).get(name, {})
        if entry.get("preserved_head") != expected_head or entry.get("neutral", {}).get("sha") != observed["head"]:
            return False
        actual = source.git(source.repository_path(base, name), "rev-parse", "--verify", entry["preserved_ref"], check=False)
        return actual.returncode == 0 and actual.stdout.strip() == expected_head
    return False


def registration_state(base, task, expected_operation_id=None):
    run_id = task["run_id"]
    archived = bool(task.get("archive_ref"))
    closed = archived or task.get("outcome") in ("completed", "interrupted")
    operation = operations.read(base)
    draft_recovery = (not archived and operation and operation.get("kind") in ("archive", "clean", "release")
                      and operation.get("status") in ("running", "failed"))
    recovery = ((closed or draft_recovery) and expected_operation_id is not None and operation
                and operation.get("operation_id") == expected_operation_id
                and operation.get("run_id") == run_id
                and (draft_recovery or (operation.get("kind") in ("clean", "release") and operation.get("status") in ("running", "failed"))
                     or (operation.get("status") == "done" and (operation.get("kind") == "archive" or task.get("outcome") == "completed"))))
    if expected_operation_id is not None and not recovery:
        raise ValueError("资源恢复登记必须绑定当前归档退出或未完成清理操作")
    if not archived and not recovery:
        store.require_development(base, task)
    return closed, recovery


def register(base, issue, run_id, entries, expected_operation_id=None):
    with store.task_state_lock(base):
        task = store.check_expected_run(base, issue, run_id)
        closed, recovery = registration_state(base, task, expected_operation_id)
        if not isinstance(entries, list):
            raise ValueError("资源登记必须是数组")
        existing = inventory(base, task)
        merged = {}
        def key(entry):
            return (entry.get("kind"), entry.get("path") if entry.get("kind") in ("file", "directory", "source-disposition") else
                    (entry.get("pid"), entry.get("started_at")) if entry.get("kind") == "process" else entry.get("id"))
        for entry in existing:
            merged[key(entry)] = entry
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("producer"):
                raise ValueError("资源必须登记生产来源")
            if (closed or recovery) and not (recovery and entry.get("kind") in ("file", "source-disposition")):
                previous = merged.get(key(entry))
                terminal = "retained" if entry.get("action") == "retain" else "cleaned"
                if (entry.get("kind") != "external" or not previous or entry.get("status") != terminal
                        or {k: v for k, v in entry.items() if k not in ("status", "readback_ref")}
                        != {k: v for k, v in previous.items() if k not in ("status", "readback_ref")}):
                    raise ValueError("归档后只能回读已登记外部资源的精确清理结果")
            if entry.get("kind") == "directory":
                directories.create(base, task, entry["path"], entry["producer"], entry.get("adopt_empty", False))
                continue
            elif entry.get("kind") == "source-disposition":
                resource_path(base, entry["path"], leaf_link=True)
                if entry.get("preservation", {}).get("action") not in ("archive", "export", "discard"):
                    raise ValueError("源码成果必须选择 archive/export/discard")
            elif entry.get("kind") == "file":
                target = resource_path(base, entry["path"])
                fingerprint(target)
                if entry["path"].startswith("source/"):
                    engineering = task.get("engineering_baseline", {})
                    repositories = engineering.get("repositories", {}) if task.get("source_prepared") else _partial_repositories(base)
                    names = [name for name in repositories if entry["path"].startswith("source/" + name + "/")]
                    if len(names) != 1:
                        raise ValueError("源码产物不属于当前工程")
                    name = names[0]
                    repository = source.repository_path(base, name)
                    source.identity(repository, repositories[name]["origin"])
                    if source.git(repository, "ls-files", "--error-unmatch", "--", str(target.relative_to(repository)), check=False).returncode == 0:
                        raise ValueError("已跟踪源码不能登记为生成产物")
            elif entry.get("kind") == "process":
                if type(entry.get("pid")) is not int or not entry.get("started_at") or not entry.get("cwd") or not entry.get("executable"):
                    raise ValueError("进程必须登记 PID、启动时间、工作目录及可执行程序")
            elif entry.get("kind") not in ("external", "source-disposition"):
                raise ValueError("未知资源类型")
            elif entry.get("kind") == "external" and (not entry.get("id") or (entry.get("status") in ("quiesced", "cleaned", "retained") and not entry.get("readback_ref"))):
                raise ValueError("外部资源必须有身份，清理完成必须提供回读依据")
            if entry.get("kind") == "external" and entry.get("resource_type") in ("git-branch", "pull-request"):
                before = entry.get("before", {})
                required = ("repository", "ref", "sha") if entry["resource_type"] == "git-branch" else ("repository", "number", "state", "head_sha")
                if any(not before.get(field) for field in required):
                    raise ValueError("分支/PR 登记缺少精确观察身份")
            if entry.get("kind") == "external" and entry.get("resource_type") not in ("git-branch", "pull-request"):
                if entry.get("action") not in ("retain", "delete", "close"):
                    raise ValueError("外部资源处置必须为 retain/delete/close")
                if not isinstance(entry.get("before"), dict) or not entry["before"]:
                    raise ValueError("外部资源处置必须记录操作前身份和 SHA/状态")
                if entry["action"] != "retain" and entry["before"].get("protected") is not False:
                    raise ValueError("删除前必须核验对象不受保护")
            merged[key(entry)] = entry
        store._write_json_atomic(store.task_directory(base, issue) / "resources.json",
                                 {"schema_version": 1, "run_id": run_id, "entries": list(merged.values())})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=".")
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--expected-operation-id", help="仅用于已归档任务退出或当前清理操作的资源恢复登记")
    args = parser.parse_args()
    try:
        register(args.dir, args.issue_key, args.expected_run_id, json.loads(Path(args.input).read_text()), args.expected_operation_id)
        return 0
    except (OSError, ValueError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
