"""按 run 登记的资源与精确清理清单；不扫描或停止未知进程。"""
from __future__ import annotations

# 产品入口：先取得租约，再重新加载本版脚本；导入库模块不启动 CLI。
if __name__ == "__main__" and not globals().get("_AO_PRODUCT_ENTRY"):
    from pathlib import Path as _AoPath
    _ao_loader = _AoPath(__file__).resolve().parents[1] / "bootstrap/product_access.py"
    _ao_namespace = {"__file__": str(_ao_loader), "_AO_LOADER_SOURCE": _ao_loader.read_bytes()}
    exec(compile(_ao_namespace["_AO_LOADER_SOURCE"], str(_ao_loader), "exec"), _ao_namespace)
    raise SystemExit(_ao_namespace["python_entry"](__file__))


import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from workflow import engineering_baseline as baseline, station_operation as operations, project_rules
from workflow import station_source as source, task_store as store
from workflow import station_directories as directories, station_artifacts as artifacts, station_clean_rules, station_layout


def resource_path(base, relative, leaf_link=False):
    if not isinstance(relative, str):
        raise ValueError("资源路径必须是相对路径")
    parts = relative.split("/")
    managed = {station_layout.station_name(base, role) for role in ("task-runtime", "repositories")}
    if any(part in ("", ".", "..", ".git") for part in parts) or parts[0] not in managed:
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
    if document.get("run_id") != task["run_id"] or not isinstance(document.get("entries"), list) or any(not isinstance(row, dict) for row in document.get("entries", [])):
        raise ValueError("资源登记不属于当前 run")
    return document["entries"]


def verify_station_inventory(base, rules=None, allow_pending=False):
    """保留工位根 IDE 配置；其它未知对象阻止复用，不猜测其所有权。"""
    root = Path(base).resolve()
    state = store.state_path(base)
    init = json.loads((state / "init.json").read_text())
    owned = {entry["path"]: entry for entry in init.get("artifacts", [])}
    task = store.read_task(base)
    registered = directories.load(base, task) if task else {}
    observed = station_clean_rules.inspect(base, owned, registered, config=rules)
    if rules is not None and (station_clean_rules.load(base)["digests"] != rules["digests"]
                              or station_layout.load(project_rules.product_root_from_station(base))["digest"] != rules["structure"]["digest"]):
        raise ValueError("清理配置已变化，需要重新确认")
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
    from workflow.station_clean import check
    return check(base, task)


def require_cleanup_version(version):
    if version != 7:
        raise ValueError("仅支持清理计划版本 7；旧工位由原版本退出")


def plan(base, task, version=None, **options):
    require_cleanup_version(7 if version is None else version)
    return station_clean_rules.plan(base, task, options)


def export_plan(base, task, decisions_override=None, **unused):
    decisions = {row['path']: row['preservation'] for row in inventory(base, task) if row.get('kind') == 'source-disposition'}
    decisions.update(decisions_override or {})
    entries, states = [], {}
    for name, row in task.get("engineering_baseline", {}).get("repositories", {}).items():
        repository = source.repository_path(base, name)
        source.identity(repository, row["origin"])
        state = artifacts.snapshot(base, name, {}, decisions, "HEAD", include_ignored=True)
        entries.extend(state.pop("entries"))
        state["origin"] = row["origin"]
        state["preserved_ref"] = None
        states[name] = state
    return {"entries": entries, "source": states}


def verify_known_external(base, task):
    from workflow import external_sync
    if any(row["status"] == "unknown" for row in external_sync.existing_warnings(base, task)):
        raise ValueError("当前 run 有外部写入结果未知，须回读原操作；不能改 retain 绕过")


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
        if store.read_task(base) is None:
            operation = operations.read(base)
            store.validate_run_id(issue, run_id)
            if not operation:
                raise ValueError('退出后的资源回读必须绑定原操作')
            results = [{'operation_id': operation['operation_id'], 'run_id': operation['run_id'], 'result': operation.get('cleanup_result', {})}] + operation.get('retained_results', [])
            result = next((row['result'] for row in results if row['operation_id'] == expected_operation_id and row['run_id'] == run_id), None)
            if result is None:
                raise ValueError('退出后的资源回读必须绑定原操作')
            pending = result.get('resources', [])
            if not isinstance(entries, list) or not entries:
                raise ValueError('资源回读必须为非空数组')
            for entry in entries:
                original = next((row for row in pending if row.get('resource', {}).get('kind') == 'external' and row['resource'].get('id') == entry.get('id')), None)
                if original is None or entry.get('kind') != 'external':
                    raise ValueError('回读对象不属于原操作残留')
                previous = original['resource']
                if ({k: v for k, v in entry.items() if k not in ('status', 'readback_ref')} !=
                        {k: v for k, v in previous.items() if k not in ('status', 'readback_ref')}
                        or not entry.get('readback_ref') or entry.get('status') != 'cleaned'):
                    raise ValueError('只允许原身份对象的清理完成回读')
                pending.remove(original)
                operation.setdefault('resource_readbacks', []).append(entry)
            operations.save(base, operation)
            return
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
                if entry["path"].startswith(station_layout.station_name(base, "repositories") + "/"):
                    engineering = task.get("engineering_baseline", {})
                    repositories = engineering.get("repositories", {}) if task.get("source_prepared") else _partial_repositories(base)
                    names = [name for name in repositories if entry["path"].startswith(station_layout.relative(base, "repositories", name + "/"))]
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


def cleanup_declared(base, config, operation, observe_only=False):
    """固定类型处理器，仅处理本工位标签的容器和网络；不删除镜像或卷。"""
    from workflow import station_context
    station_id = station_context.read_binding(base)['station_id']
    pending = []
    def call(args):
        result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError('Docker 操作未完成：' + args[0])
        return result.stdout
    for spec in (row for layer in config['layers'] for row in layer.get('resources', [])):
        project = spec['project_prefix'] + station_id
        step_name = 'docker:' + baseline.digest(spec)
        try:
            path = resource_path(base, spec['path'])
            # 缺失 runtime 不能证明外部对象不存在；可用驱动始终按工位标签发现。
            # 从未配置环境、也没有本机 Docker 的空工位无需安装可选驱动。
            configured = Path(base) / station_layout.station_name(base, 'configuration') / 'environments'
            if (not path.exists() and not path.is_symlink() and not configured.exists()
                    and not configured.is_symlink() and step_name not in operation['steps']
                    and shutil.which('docker') is None):
                continue
            ids = call(['ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).split()
            networks = call(['network', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + project]).split()
            rows = []
            for kind, identifiers in (('container', ids), ('network', networks)):
                for identifier in identifiers:
                    observation = json.loads(call(['inspect', identifier] if kind == 'container' else ['network', 'inspect', identifier]))
                    if (not isinstance(observation, list) or len(observation) != 1
                            or not isinstance(observation[0], dict)
                            or not isinstance(observation[0].get('Id'), str)
                            or not observation[0]['Id'].startswith(identifier)):
                        raise ValueError('Docker 对象回读结构或身份无效，先由 Agent 核实原对象')
                    info = observation[0]
                    config_value = info.get('Config', {}) if kind == 'container' else info
                    labels = config_value.get('Labels') if isinstance(config_value, dict) else None
                    if not isinstance(labels, dict) or labels.get(spec['identity_label']) != station_id or labels.get('com.docker.compose.project') != project:
                        raise ValueError('Docker 对象身份标签不符，保留')
                    rows.append({'kind': kind, 'id': info['Id']})
            if observe_only:
                if rows:
                    raise ValueError('Docker 资源仍存在')
                continue
            previous = operation['steps'].get(step_name)
            if previous:
                if (previous['receipt'] or {}).get('status') == 'removed' and rows:
                    raise ValueError('已完成资源重新出现，保留并交接')
                if any(row not in previous['expected']['objects'] for row in rows):
                    raise ValueError('资源范围发生变化，保留并交接')
            else:
                operations.intent(base, operation, step_name, {}, {'objects': rows})
            for row in rows:
                if row['kind'] == 'container':
                    call(['stop', '--time', '10', row['id']])
                    call(['rm', row['id']])
                else:
                    call(['network', 'rm', row['id']])
            if call(['ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project]).strip() or call(['network', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + project]).strip():
                raise ValueError('Docker 资源回读仍存在')
            if operation['steps'][step_name]['receipt'] is None:
                operations.receipt(base, operation, step_name, {'status': 'removed'})
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            pending.append({'resource': spec, 'reason': str(error)})
            if step_name in operation['steps'] and operation['steps'][step_name]['receipt'] is None:
                operations.receipt(base, operation, step_name, {'status': 'retained', 'reason': str(error)})
    return pending


def observe_retained_resources(base, pending_rows):
    """只读核验已退出运行的残留；未知类型保留原始身份和处理事项。"""
    from workflow import station_context
    pending = []
    for row in pending_rows:
        item = row.get('resource', {})
        try:
            if item.get('kind') == 'process':
                pid = item.get('pid')
                if type(pid) is not int or not item.get('started_at'):
                    raise ValueError('前次退出进程身份未知，需要原生回读')
                check = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True, timeout=5)
                if check.returncode not in (0, 1) or check.stdout.strip() == item['started_at']:
                    raise ValueError('前次任务写入者仍存在，先处理受影响资源')
            elif item.get('kind') == 'docker-project':
                project = item['project_prefix'] + station_context.read_binding(base)['station_id']
                for args in (['ps', '-aq'], ['network', 'ls', '-q']):
                    check = subprocess.run(['docker', *args, '--filter', 'label=com.docker.compose.project=' + project], capture_output=True, text=True, timeout=30)
                    if check.returncode or check.stdout.strip():
                        raise ValueError('前次 Docker 资源尚未确认回收')
            else:
                raise ValueError(row['reason'])
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            pending.append({'resource': item, 'reason': str(error)})
    return pending


def check_retained_resources(base, operation):
    """新接管前只回读退出遗留，不停止资源或创建悬空操作。"""
    previous = operation or {}
    results = [previous.get('cleanup_result', {})] + [r['result'] for r in previous.get('retained_results', [])]
    pending = observe_retained_resources(base, [row for result in results for row in result.get('resources', [])])
    if pending:
        raise ValueError(pending[0]['reason'])


if __name__ == "__main__":
    raise SystemExit(main())
