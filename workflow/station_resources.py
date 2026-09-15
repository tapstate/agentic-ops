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
from workflow import engineering_baseline as baseline, station_operation as operations
from workflow import station_source as source, task_store as store


def resource_path(base, relative):
    if not isinstance(relative, str):
        raise ValueError("资源路径必须是相对路径")
    parts = relative.split("/")
    if any(part in ("", ".", "..", ".git") for part in parts) or parts[0] not in ("runtime", "source"):
        raise ValueError("资源只能位于 runtime 或 source，不能包括 Git 元数据")
    root = Path(base).resolve()
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("资源路径不能包含符号链接：%s" % relative)
    return path


def fingerprint(path):
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
            accepted = ("cleaned",) if require_cleaned else ("quiesced", "cleaned")
            if item.get("status") not in accepted or not item.get("readback_ref"):
                raise ValueError("外部资源尚未回读%s，请人工处理：%s" % (
                    "清理完成" if require_cleaned else "停止写入", item.get("id")))


def plan(base, task):
    verify_stopped(base, task)
    managed = {}
    for item in inventory(base, task):
        if item.get("kind") != "file":
            continue
        relative = item["path"]
        path = resource_path(base, relative)
        managed[relative] = path
    engineering = task.get("engineering_baseline")
    repositories = (engineering["repositories"] if engineering and engineering.get("status") == "frozen"
                    else _partial_repositories(base))
    for relative in managed:
        if relative.startswith("source/") and not any(relative.startswith("source/" + name + "/") for name in repositories):
            raise ValueError("源码产物不属于当前任务工程仓库：%s" % relative)
    runtime = Path(base).resolve() / "runtime"
    if runtime.is_symlink():
        raise ValueError("runtime 不能是符号链接")
    if runtime.exists():
        for path in runtime.rglob("*"):
            if path.is_symlink():
                raise ValueError("runtime 包含未知符号链接")
            if path.is_file() and str(path.relative_to(Path(base).resolve())) not in managed:
                raise ValueError("runtime 含未登记产物，请先登记生产来源：%s" % path)
    entries = {}
    for relative, path in managed.items():
        entries[relative] = {"path": relative, "action": "delete", "before": fingerprint(path)}
    engineering = task.get("engineering_baseline")
    if not engineering or engineering.get("status") != "frozen":
        _partial_repositories(base)
    if engineering and engineering.get("status") == "frozen":
        for name, entry in engineering["repositories"].items():
            path = source.repository_path(base, name)
            source.identity(path, entry["origin"])
            head = source.git(path, "rev-parse", "HEAD").stdout.strip()
            for relative in managed:
                prefix = "source/" + name + "/"
                if relative.startswith(prefix) and source.git(path, "ls-files", "--error-unmatch", "--", relative[len(prefix):], check=False).returncode == 0:
                    raise ValueError("不能将已跟踪源码登记为可删除产物：%s" % relative)
            changed = (source.git(path, "diff", "--name-only", "-z", "--").stdout.split("\0")
                       + source.git(path, "diff", "--cached", "--name-only", "-z", "HEAD", "--").stdout.split("\0"))
            untracked = source.git(path, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0")
            ignored = source.git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout.split("\0")
            for name_in_repo in ignored:
                if name_in_repo and "source/" + name + "/" + name_in_repo not in managed:
                    raise ValueError("源码仓含未登记的 ignored 产物：%s" % name_in_repo)
            for filename in filter(None, changed + untracked):
                relative = "source/" + name + "/" + filename
                if name not in task.get("task_repositories", {}) and relative not in managed:
                    raise ValueError("配套仓存在未登记源码修改：%s" % relative)
                target = resource_path(base, relative)
                action = "restore" if filename in changed else "delete"
                entries[relative] = {"path": relative, "action": action,
                                     "before": fingerprint(target), "repository": name,
                                     "file": filename, "head": head,
                                     "before_index": source.git(path, "ls-files", "--stage", "-z", "--", filename).stdout}
    value = {"schema_version": 1, "run_id": task["run_id"],
             "external": [item for item in inventory(base, task) if item.get("kind") == "external"],
             "entries": [entries[key] for key in sorted(entries)]}
    value["digest"] = baseline.digest(value)
    return value


def clean(base, task, cleanup_plan, confirmed_digest, operation):
    payload = {key: value for key, value in cleanup_plan.items() if key != "digest"}
    if (cleanup_plan.get("run_id") != task["run_id"]
            or baseline.digest(payload) != confirmed_digest
            or cleanup_plan.get("digest") != confirmed_digest):
        raise ValueError("清理确认与当前 run 或精确清单不匹配")
    if not task.get("archive_ref"):
        raise ValueError("正式归档未绑定，不能清理")
    verify_stopped(base, task, require_cleaned=True)
    external = {item["id"]: item for item in inventory(base, task) if item.get("kind") == "external"}
    for entry in cleanup_plan.get("external", []):
        name = "external:%s:%s:%s" % (len(operation.get("plan_revisions", [])), cleanup_plan["digest"], baseline.digest(entry["id"]))
        step = operations.intent(base, operation, name, entry, {"id": entry["id"], "status": "cleaned"})
        actual = external.get(entry["id"])
        if actual is None and step["receipt"] is not None:
            continue  # 活动登记已在清理末尾回收，持久回执仍随操作留存。
        if (not actual or actual.get("status") != "cleaned" or not actual.get("readback_ref")
                or {k: v for k, v in actual.items() if k not in ("status", "readback_ref")}
                != {k: v for k, v in entry.items() if k not in ("status", "readback_ref")}):
            raise ValueError("外部资源清理回读与确认清单不一致")
        operations.receipt(base, operation, name, actual)
    for entry in cleanup_plan["entries"]:
        target = resource_path(base, entry["path"])
        name = "resource:%s:%s:%s" % (len(operation.get("plan_revisions", [])), cleanup_plan["digest"], entry["path"])
        step = operations.intent(base, operation, name, entry["before"], entry)
        if step["receipt"] is not None:
            if fingerprint(target) != step["receipt"].get("after"):
                raise ValueError("清理后资源再次变化，拒绝解绑")
            if entry["action"] == "restore":
                repository = source.repository_path(base, entry["repository"])
                if source.git(repository, "ls-files", "--stage", "-z", "--", entry["file"]).stdout != step["receipt"]["after_index"]:
                    raise ValueError("清理后暂存区再次变化，拒绝解绑")
            continue
        before = fingerprint(target)
        if entry["action"] == "delete":
            if before is not None and before != entry["before"]:
                raise ValueError("清理确认后文件变化：%s" % entry["path"])
            if before is not None:
                target.unlink()
        elif entry["action"] == "restore":
            repository = source.repository_path(base, entry["repository"])
            if source.git(repository, "rev-parse", "HEAD").stdout.strip() != entry["head"]:
                raise ValueError("源码 Head 已变化，拒绝恢复")
            restored = [source.git(repository, "diff", *args, "--quiet", "--", entry["file"], check=False).returncode
                        for args in ((), ("--cached", "HEAD"))]
            if any(code not in (0, 1) for code in restored):
                raise ValueError("不能核验恢复结果")
            index = source.git(repository, "ls-files", "--stage", "-z", "--", entry["file"]).stdout
            if (before != entry["before"] or index != entry["before_index"]) and restored != [0, 0]:
                raise ValueError("待恢复文件变化，需重新确认")
            source.git(repository, "restore", "--source=" + entry["head"], "--staged", "--worktree", "--", entry["file"])
        else:
            raise ValueError("未知清理动作")
        receipt = {"after": fingerprint(target)}
        if entry["action"] == "restore":
            receipt["after_index"] = source.git(repository, "ls-files", "--stage", "-z", "--", entry["file"]).stdout
        operations.receipt(base, operation, name, receipt)
    # 不删除未知产物；中断后的空目录可以安全重复回收。
    runtime = Path(base).resolve() / "runtime"
    if runtime.exists():
        for directory in sorted(runtime.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("清理后 runtime 仍有未处理产物")
            directory.rmdir()


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


def neutral(base, task, operation):
    value = task.get("engineering_baseline")
    repositories = value["repositories"] if value and value.get("status") == "frozen" else _partial_repositories(base)
    for name, entry in repositories.items():
        path = source.repository_path(base, name)
        source.identity(path, entry["origin"])
        if not entry.get("initial_checkout"):
            source.require_clean(path)
        if source.git(path, "ls-files", "--others", "--ignored", "--exclude-standard").stdout:
            raise ValueError("源码仍含未清理 ignored 产物")
        head = source.git(path, "rev-parse", "HEAD").stdout.strip()
        step = "neutral:" + name
        operations.intent(base, operation, step, {"head": head}, {"head": head, "detached": True})
        source.git(path, "checkout", "--detach", head)
        if source.git(path, "branch", "--show-current").stdout.strip():
            raise ValueError("未成功脱离任务分支")
        operations.receipt(base, operation, step, {"head": head, "detached": True})


def register(base, issue, run_id, entries, expected_operation_id=None):
    with store.task_state_lock(base):
        task = store.check_expected_run(base, issue, run_id)
        archived = bool(task.get("archive_ref"))
        closed = archived or task.get("outcome") in ("completed", "interrupted")
        operation = operations.read(base)
        draft_recovery = (not archived and operation and operation.get("kind") == "archive"
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
        if not isinstance(entries, list):
            raise ValueError("资源登记必须是数组")
        existing = inventory(base, task)
        merged = {}
        def key(entry):
            return (entry.get("kind"), entry.get("path") if entry.get("kind") == "file" else
                    (entry.get("pid"), entry.get("started_at")) if entry.get("kind") == "process" else entry.get("id"))
        for entry in existing:
            merged[key(entry)] = entry
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("producer"):
                raise ValueError("资源必须登记生产来源")
            if (closed or recovery) and not (recovery and entry.get("kind") == "file"):
                previous = merged.get(key(entry))
                if (entry.get("kind") != "external" or not previous or entry.get("status") != "cleaned"
                        or {k: v for k, v in entry.items() if k not in ("status", "readback_ref")}
                        != {k: v for k, v in previous.items() if k not in ("status", "readback_ref")}):
                    raise ValueError("归档后只能回读已登记外部资源的精确清理结果")
            if entry.get("kind") == "file":
                target = resource_path(base, entry["path"])
                fingerprint(target)
                if entry["path"].startswith("source/"):
                    engineering = task.get("engineering_baseline", {})
                    repositories = engineering.get("repositories", {}) if engineering.get("status") == "frozen" else _partial_repositories(base)
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
            elif entry.get("kind") != "external":
                raise ValueError("未知资源类型")
            elif not entry.get("id") or (entry.get("status") in ("quiesced", "cleaned") and not entry.get("readback_ref")):
                raise ValueError("外部资源必须有身份，清理完成必须提供回读依据")
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
