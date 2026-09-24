#!/usr/bin/env python3
"""Product Root 本机工位登记与受控维护命令。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import station_operation, task_store  # noqa: E402
from workflow import project_rules  # noqa: E402
from bootstrap import shared_repositories  # noqa: E402
from bootstrap.station_compatibility import require_station_can_adopt
from bootstrap.station_paths import StationDirectory, station_artifact_path  # noqa: E402


SCHEMA_VERSION = 1
STATE_DIRECTORY = ".agenticops"
REGISTRY_NAME = "stations.json"
STATION_GATE_EVENTS = Path("events.jsonl")


def registry_path(product_root):
    return product_root / ".local" / REGISTRY_NAME


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("%s无法读取：%s" % (label, error)) from error


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(str(temporary), str(path))


def load_registry(product_root):
    path = registry_path(product_root)
    if not path.is_file():
        return []
    document = load_json(path, "工位提示索引")
    paths = document.get("stations") if isinstance(document, dict) else None
    if document.get("schema_version") != SCHEMA_VERSION or not isinstance(paths, list):
        raise ValueError("工位提示索引结构无效：%s" % path)
    if not all(isinstance(item, str) and item for item in paths):
        raise ValueError("工位提示索引包含无效路径：%s" % path)
    return sorted(set(paths))


def initialize_registry(product_root):
    path = registry_path(product_root)
    if path.exists():
        load_registry(product_root)
    else:
        save_registry(product_root, [])


def save_registry(product_root, stations):
    write_json(registry_path(product_root), {"schema_version": SCHEMA_VERSION, "stations": sorted(set(stations))})


def register(product_root, station):
    station = str(Path(station).resolve())
    stations = load_registry(product_root)
    if station not in stations:
        save_registry(product_root, [*stations, station])


def unregister(product_root, station):
    station = str(Path(station).resolve())
    stations = load_registry(product_root)
    if station in stations:
        save_registry(product_root, [item for item in stations if item != station])


def require_lifecycle_owner(product_root):
    """仅允许公共入口在持有 Product Root 生命周期锁时修改登记。"""
    owner_path = Path(product_root).resolve() / ".local" / "lifecycle.lock" / "owner"
    try:
        owner = int(owner_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise ValueError("工位登记变更需要 Product Root 生命周期锁") from error
    if owner != os.getppid():
        raise ValueError("工位登记生命周期锁不属于当前公共入口")


def binding_status(product_root, station, tree=None):
    station = Path(station)
    if not station.exists():
        return "missing", "路径不存在"
    if not station.is_dir():
        return "invalid", "路径不是目录"
    if tree is None:
        try:
            with StationDirectory(station) as opened:
                return binding_status(product_root, station, opened)
        except ValueError as error:
            return "invalid", str(error)
    relative = Path(STATE_DIRECTORY) / "station.json"
    if not tree.is_file(relative):
        return "invalid", "缺少 .agenticops/station.json"
    try:
        document = tree.read_json(relative, "工位绑定")
    except ValueError:
        return "unreadable", "工位绑定不可读取"
    if document.get("schema_version") != 4:
        return "invalid", "工位绑定版本不支持"
    if document.get("product_root") != str(product_root.resolve()):
        return "rebound", "已绑定到其它 Product Root"
    return "tracked", "绑定正常"


def require_tracked(product_root, station, tree=None):
    status, reason = binding_status(product_root, station, tree)
    if status != "tracked":
        raise ValueError("工位不可由当前 Product Root 操作：%s（%s）" % (station, reason))


def select_targets(args, product_root):
    if args.station:
        return [Path(args.station).resolve()]
    if args.all:
        return [Path(item) for item in load_registry(product_root)]
    raise ValueError("需要指定 --station <目录> 或显式指定 --all")


def show_targets(action, targets, details=None):
    print("将执行工位操作：%s" % action)
    if not targets:
        print("- 没有匹配的已登记工位")
        return
    for path in targets:
        suffix = (details or {}).get(str(path))
        print("- %s%s" % (path, "：%s" % suffix if suffix else ""))


def confirm(args):
    if args.yes:
        print("已通过 --yes 确认执行。")
        return
    if not sys.stdin.isatty():
        raise ValueError("非交互环境拒绝执行；请先核对上方列表，再在交互终端确认或显式传入 --yes")
    answer = input("确认执行？[y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        raise ValueError("用户取消工位操作")


def refresh(product_root, station):
    subprocess.run([sys.executable, str(product_root / "bootstrap" / "render.py"), "--install-home", str(product_root), "--station", str(station), "--refresh"], check=True)
    register(product_root, station)


def wiki_repository(product_root, station):
    require_tracked(product_root, station)
    profile = project_rules.load_profile(station=station)
    repository = profile.get("wiki_repository")
    if not isinstance(repository, str) or not repository:
        raise ValueError("项目 Profile 缺少 wiki_repository")
    return repository


def ensure_wiki(product_root, station):
    """按工位绑定的项目 Profile 显式准备共享 Wiki；不隐式更新已有副本。"""
    repository = wiki_repository(product_root, station)
    binding = load_json(station_artifact_path(station, Path(STATE_DIRECTORY) / "station.json"), "工位绑定")
    return shared_repositories.run(product_root, repository, "ensure", binding["source_pool"])


def owned_artifacts(station, tree=None):
    if tree is None:
        with StationDirectory(station) as opened:
            return owned_artifacts(station, opened)
    init_relative = Path(STATE_DIRECTORY) / "init.json"
    init_path = tree.path(init_relative)
    document = tree.read_json(init_relative, "工位初始化清单")
    if document.get("schema_version") not in (1, 2) or not isinstance(document.get("artifacts"), list):
        raise ValueError("工位初始化清单结构无效：%s" % init_path)
    artifacts = {}
    for item in document["artifacts"]:
        if not isinstance(item, dict):
            raise ValueError("工位初始化清单包含无效产物")
        path = item.get("path")
        kind = item.get("kind", "file")
        if not isinstance(path, str) or kind not in ("file", "symlink"):
            raise ValueError("工位初始化清单包含无效产物")
        if kind == "file":
            checksum = item.get("sha256")
            if not isinstance(checksum, str):
                raise ValueError("工位初始化清单包含无效产物")
            record = {"kind": "file", "sha256": checksum}
        else:
            target = item.get("target")
            if not isinstance(target, str) or not target or Path(target).is_absolute():
                raise ValueError("工位初始化清单包含无效 Skill 接线")
            record = {"kind": "symlink", "target": target}
        relative = Path(path)
        if relative.is_absolute() or not relative.parts or any(part == ".." for part in relative.parts):
            raise ValueError("工位初始化清单产物越界：%s" % path)
        if path in artifacts:
            raise ValueError("工位初始化清单存在重复产物：%s" % path)
        artifacts[path] = record
    return artifacts


def detach_preflight(product_root, station, purge=False, tree=None):
    if tree is None:
        with StationDirectory(station) as opened:
            return detach_preflight(product_root, station, purge=purge, tree=opened)
    require_tracked(product_root, station, tree)
    artifacts = owned_artifacts(station, tree)
    deletable = []
    for relative, recorded in artifacts.items():
        path = tree.path(relative)
        if not tree.exists(relative):
            continue
        if recorded["kind"] == "symlink":
            if not tree.is_symlink(relative) or tree.readlink(relative) != recorded["target"]:
                raise ValueError("生成 Skill 接线已被修改或异常，拒绝删除：%s" % path)
        elif (
            not tree.is_file(relative)
            or tree.is_symlink(relative)
            or hashlib.sha256(tree.read_text(relative).encode("utf-8")).hexdigest()
            != recorded["sha256"]
        ):
            raise ValueError("生成接线已被修改或异常，拒绝删除：%s" % path)
        deletable.append(relative)
    state_root = station_artifact_path(station, STATE_DIRECTORY)
    require_station_can_adopt(product_root, station)
    current = task_store.read_current(station)
    if current["current"] is not None:
        raise ValueError("工位仍被任务占用，必须先归档并释放或清理，不能解绑")
    operation_path = state_root / "operation.json"
    if operation_path.exists():
        if operation_path.is_symlink():
            raise ValueError("操作状态不能是符号链接")
        operation = station_operation.read(station)
        if operation.get("status") != "done":
            raise ValueError("工位仍有未完成操作，不能解绑")
    allowed = {"station.json", "init.json", "current-task.json", "operation.json",
               "events.jsonl", "git-ref-cache-v2.json",
               "git-ref-cache-v2.json.lock"}
    for path in state_root.iterdir():
        if path.name == "operation-data" and path.is_dir() and not path.is_symlink():
            from workflow import engineering_baseline
            for item in path.iterdir():
                if item.is_symlink() or not item.is_file() or item.suffix != ".json" or engineering_baseline.digest(json.loads(item.read_text())) != item.stem:
                    raise ValueError("操作材料包含未知或损坏对象，拒绝解绑")
            continue
        if path.name not in allowed or path.is_symlink() or not path.is_file():
            raise ValueError("工位状态包含未知文件或未清理活动证据，拒绝解绑：%s" % path)
    if not tree.is_dir("runtime") or tree.is_symlink("runtime"):
        raise ValueError("runtime 目录缺失或不安全，拒绝解绑")
    if any(tree.path("runtime").iterdir()):
        raise ValueError("runtime 仍有材料，必须先经任务清理或明确处置，不能解绑")
    return deletable, 0


def remove_empty_parents(paths, tree):
    parents = {Path(item).parent for item in paths if Path(item).parent.parts}
    for path in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        while path.parts:
            if not tree.rmdir_cached(path):
                break
            path = path.parent


def detach(product_root, station, purge=False, allow_product_lifecycle=False):
    station = Path(station).resolve()
    lock = task_store.task_state_lock(
        station, allow_product_lifecycle=allow_product_lifecycle
    )
    with lock:
        with StationDirectory(station) as tree:
            # purge 必须在持有工位状态目录锁后重新预检；命令展示阶段的预检
            # 只用于人工确认，不能作为删除事务的证据。
            deletable, _ = detach_preflight(
                product_root, station, purge=purge, tree=tree
            )
            for relative in deletable:
                tree.unlink(relative)

            for name in ("current-task.json", "operation.json", "events.jsonl",
                         "git-ref-cache-v2.json", "git-ref-cache-v2.json.lock"):
                tree.unlink(Path(STATE_DIRECTORY) / name, missing_ok=True)
            tree.remove_tree(Path(STATE_DIRECTORY) / "operation-data", missing_ok=True)
            for relative in (
                Path(STATE_DIRECTORY) / "init.json",
                Path(STATE_DIRECTORY) / "station.json",
            ):
                tree.unlink(relative, missing_ok=True)

            remove_empty_parents(deletable, tree)
            tree.rmdir_cached(STATE_DIRECTORY)
        unregister(product_root, station)


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, _type, _value, _traceback):
        return False


def command_list(args, product_root):
    stations = load_registry(product_root)
    if not stations:
        print("没有已登记工位")
        return
    print("已登记工位：")
    for item in stations:
        status, reason = binding_status(product_root, item)
        print("- %s：%s（%s）" % (item, status, reason))


def command_prune(args, product_root):
    targets = select_targets(args, product_root)
    removable, details = [], {}
    for station in targets:
        status, reason = binding_status(product_root, station)
        details[str(station)] = reason
        state = station / STATE_DIRECTORY
        if status in ("missing", "rebound") or (
            status == "invalid" and not state.exists()
        ):
            removable.append(station)
    show_targets("prune", removable, details)
    if not removable:
        return
    confirm(args)
    for station in removable:
        unregister(product_root, station)
    print("已注销 %s 个无法跟踪的工位。" % len(removable))


def command_refresh(args, product_root, action):
    targets = select_targets(args, product_root)
    for station in targets:
        require_tracked(product_root, station)
    show_targets(action, targets)
    if args.all:
        confirm(args)
    prepared_wikis = set()
    for station in targets:
        refresh(product_root, station)
        if getattr(args, "ensure_wiki", False):
            repository = wiki_repository(product_root, station)
            binding = load_json(station_artifact_path(station, Path(STATE_DIRECTORY) / "station.json"), "工位绑定")
            key = (repository, binding["source_pool"])
            if key not in prepared_wikis:
                result = shared_repositories.run(product_root, repository, "ensure", binding["source_pool"])
                print("Wiki 已就绪：%s（%s）" % (result["repository"], result["path"]))
                prepared_wikis.add(key)


def command_detach(args, product_root, purge=False):
    if purge and args.all:
        raise ValueError("station purge 不支持 --all；请逐个工位明确确认")
    targets = select_targets(args, product_root)
    details = {}
    for station in targets:
        _, task_count = detach_preflight(product_root, station, purge=purge)
        details[str(station)] = "空闲工位：只移除接线和绑定；保留 source/config、旧工位 archive（若有）及 Product Root .archive"
    show_targets("purge" if purge else "detach", targets, details)
    confirm(args)
    for station in targets:
        detach(
            product_root,
            station,
            purge=purge,
            allow_product_lifecycle=args.lifecycle_held,
        )
    print("已%s %s 个工位。" % ("彻底清理" if purge else "解绑", len(targets)))


def command_pending(args, product_root):
    pending = []
    for item in load_registry(product_root):
        station = Path(item)
        if binding_status(product_root, station)[0] != "tracked":
            continue
        try:
            document = load_json(
                station_artifact_path(
                    station, Path(STATE_DIRECTORY) / "init.json"
                ),
                "工位初始化清单",
            )
        except ValueError:
            pending.append(station)
            continue
        if document.get("product_ref") != args.product_ref:
            pending.append(station)
    if pending:
        print("AgenticOps：检测到 %s 个已知工位待刷新；请执行 agenticops station repair --all，或在使用时执行 start。" % len(pending))


def configured_git_name():
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", "user.name"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("无法读取全局 Git user.name；请配置后重试") from error
    if result.returncode != 0:
        raise ValueError(
            "当前机器未配置全局 Git user.name；请先执行 git config --global user.name <合法 Git 提交用户名>，再重试。该值会一次性保存，"
            "用于稳定生成并恢复任务工作分支"
        )
    return task_store.validate_git_name(result.stdout.strip()), "git_global_user_name"


def command_identity(args, product_root):
    station = Path(args.station).resolve()
    require_tracked(product_root, station)
    with task_store.task_state_lock(station):
        current = task_store.read_task(station)
        if current and current.get("task_repositories"):
            raise ValueError("当前任务已登记修改仓库，不能变更 git_name")
        path = station_artifact_path(station, Path(STATE_DIRECTORY) / "station.json")
        document = load_json(path, "工位配置")
        existing = document.get("branch_identity")
        if existing is not None:
            name = task_store.station_git_name(station)
            print("工位 git_name 已配置：%s" % name)
            return
        name, source = configured_git_name()
        document["branch_identity"] = {
            "schema_version": 1,
            "git_name": name,
            "source": source,
        }
        task_store._write_json_atomic(path, document)
    print("工位 git_name 已配置：%s" % name)


def parser():
    class StrictArgumentParser(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs):
            kwargs["allow_abbrev"] = False
            super().__init__(*args, **kwargs)

    result = StrictArgumentParser(description=__doc__)
    result.add_argument("--product-root", required=True)
    result.add_argument("--lifecycle-held", action="store_true", help=argparse.SUPPRESS)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("initialize")
    commands.add_parser("register").add_argument("--station", required=True)
    commands.add_parser("pending").add_argument("--product-ref", required=True)
    commands.add_parser("list")
    identity = commands.add_parser("identity")
    identity.add_argument("--station", required=True)
    for name in ("prune", "repair", "detach", "purge"):
        command = commands.add_parser(name)
        target = command.add_mutually_exclusive_group(required=True)
        target.add_argument("--station")
        target.add_argument("--all", action="store_true")
        command.add_argument("--yes", action="store_true", help="非交互环境确认已展示的目标列表")
        if name == "repair":
            command.add_argument("--ensure-wiki", action="store_true", help="显式准备项目共享 Wiki；不在初始化或接管时自动执行")
    wiki = commands.add_parser("ensure-wiki")
    wiki.add_argument("--station", required=True)
    clean = commands.add_parser("clean")
    target = clean.add_mutually_exclusive_group(required=True)
    target.add_argument("--station")
    target.add_argument("--all", action="store_true")
    clean.add_argument("--generated-only", action="store_true", required=True)
    clean.add_argument("--yes", action="store_true", help="非交互环境确认已展示的目标列表")
    return result


def main():
    args = parser().parse_args()
    product_root = Path(args.product_root).resolve()
    try:
        if args.lifecycle_held:
            require_lifecycle_owner(product_root)
        if args.command == "initialize":
            initialize_registry(product_root)
        elif args.command == "register":
            register(product_root, args.station)
        elif args.command == "pending":
            command_pending(args, product_root)
        elif args.command == "list":
            command_list(args, product_root)
        elif args.command == "identity":
            command_identity(args, product_root)
        elif args.command == "prune":
            command_prune(args, product_root)
        elif args.command == "repair":
            command_refresh(args, product_root, "repair")
        elif args.command == "ensure-wiki":
            result = ensure_wiki(product_root, Path(args.station).resolve())
            print("Wiki 已就绪：%s（%s）" % (result["repository"], result["path"]))
        elif args.command == "clean":
            command_refresh(args, product_root, "clean --generated-only")
        elif args.command == "detach":
            command_detach(args, product_root)
        elif args.command == "purge":
            command_detach(args, product_root, purge=True)
        return 0
    except (ValueError, subprocess.CalledProcessError) as error:
        print("AgenticOps：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
