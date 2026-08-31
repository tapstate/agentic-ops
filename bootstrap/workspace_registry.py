#!/usr/bin/env python3
"""Product Root 本机工作空间提示索引与受控维护命令。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import repository_worktree, task_store  # noqa: E402
from bootstrap.workspace_paths import WorkspaceDirectory, workspace_artifact_path  # noqa: E402


SCHEMA_VERSION = 1
STATE_DIRECTORY = ".agenticops"
REGISTRY_NAME = "workspaces.json"
WORKSPACE_GATE_EVENTS = Path("events.jsonl")


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
    document = load_json(path, "工作空间提示索引")
    paths = document.get("workspaces") if isinstance(document, dict) else None
    if document.get("schema_version") != SCHEMA_VERSION or not isinstance(paths, list):
        raise ValueError("工作空间提示索引结构无效：%s" % path)
    if not all(isinstance(item, str) and item for item in paths):
        raise ValueError("工作空间提示索引包含无效路径：%s" % path)
    return sorted(set(paths))


def save_registry(product_root, workspaces):
    write_json(registry_path(product_root), {"schema_version": SCHEMA_VERSION, "workspaces": sorted(set(workspaces))})


def register(product_root, workspace):
    workspace = str(Path(workspace).resolve())
    workspaces = load_registry(product_root)
    if workspace not in workspaces:
        save_registry(product_root, [*workspaces, workspace])


def unregister(product_root, workspace):
    workspace = str(Path(workspace).resolve())
    workspaces = load_registry(product_root)
    if workspace in workspaces:
        save_registry(product_root, [item for item in workspaces if item != workspace])


def binding_status(product_root, workspace, tree=None):
    workspace = Path(workspace)
    if not workspace.exists():
        return "missing", "路径不存在"
    if not workspace.is_dir():
        return "invalid", "路径不是目录"
    if tree is None:
        try:
            with WorkspaceDirectory(workspace) as opened:
                return binding_status(product_root, workspace, opened)
        except ValueError as error:
            return "invalid", str(error)
    relative = Path(STATE_DIRECTORY) / "workspace.json"
    if not tree.is_file(relative):
        return "invalid", "缺少 .agenticops/workspace.json"
    try:
        document = tree.read_json(relative, "工作空间绑定")
    except ValueError:
        return "unreadable", "工作空间绑定不可读取"
    if document.get("schema_version") not in (1, 2):
        return "invalid", "工作空间绑定版本不支持"
    if document.get("product_root") != str(product_root.resolve()):
        return "rebound", "已绑定到其它 Product Root"
    return "tracked", "绑定正常"


def require_tracked(product_root, workspace, tree=None):
    status, reason = binding_status(product_root, workspace, tree)
    if status != "tracked":
        raise ValueError("工作空间不可由当前 Product Root 操作：%s（%s）" % (workspace, reason))


def select_targets(args, product_root):
    if args.workspace:
        return [Path(args.workspace).resolve()]
    if args.all:
        return [Path(item) for item in load_registry(product_root)]
    raise ValueError("需要指定 --workspace <目录> 或显式指定 --all")


def show_targets(action, targets, details=None):
    print("将执行工作空间操作：%s" % action)
    if not targets:
        print("- 没有匹配的已登记工作空间")
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
        raise ValueError("用户取消工作空间操作")


def refresh(product_root, workspace):
    subprocess.run([sys.executable, str(product_root / "bootstrap" / "render.py"), "--install-home", str(product_root), "--workspace", str(workspace), "--refresh"], check=True)
    register(product_root, workspace)


def owned_artifacts(workspace, tree=None):
    if tree is None:
        with WorkspaceDirectory(workspace) as opened:
            return owned_artifacts(workspace, opened)
    init_relative = Path(STATE_DIRECTORY) / "init.json"
    init_path = tree.path(init_relative)
    document = tree.read_json(init_relative, "工作空间初始化清单")
    if document.get("schema_version") not in (1, 2) or not isinstance(document.get("artifacts"), list):
        raise ValueError("工作空间初始化清单结构无效：%s" % init_path)
    artifacts = {}
    for item in document["artifacts"]:
        if not isinstance(item, dict):
            raise ValueError("工作空间初始化清单包含无效产物")
        path = item.get("path")
        kind = item.get("kind", "file")
        if not isinstance(path, str) or kind not in ("file", "symlink"):
            raise ValueError("工作空间初始化清单包含无效产物")
        if kind == "file":
            checksum = item.get("sha256")
            if not isinstance(checksum, str):
                raise ValueError("工作空间初始化清单包含无效产物")
            record = {"kind": "file", "sha256": checksum}
        else:
            target = item.get("target")
            if not isinstance(target, str) or not target or Path(target).is_absolute():
                raise ValueError("工作空间初始化清单包含无效 Skill 接线")
            record = {"kind": "symlink", "target": target}
        relative = Path(path)
        if relative.is_absolute() or not relative.parts or any(part == ".." for part in relative.parts):
            raise ValueError("工作空间初始化清单产物越界：%s" % path)
        if path in artifacts:
            raise ValueError("工作空间初始化清单存在重复产物：%s" % path)
        artifacts[path] = record
    return artifacts


def detach_preflight(product_root, workspace, purge=False, tree=None):
    if tree is None:
        with WorkspaceDirectory(workspace) as opened:
            return detach_preflight(product_root, workspace, purge=purge, tree=opened)
    require_tracked(product_root, workspace, tree)
    artifacts = owned_artifacts(workspace, tree)
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
    state_root = workspace_artifact_path(workspace, STATE_DIRECTORY)
    task_root = state_root / "tasks"
    if task_root.is_symlink():
        raise ValueError("任务状态目录不能是符号链接，拒绝操作：%s" % task_root)
    task_count = sum(1 for path in task_root.iterdir() if path.is_dir()) if task_root.is_dir() else 0
    prepared_roots = []
    if task_root.is_dir():
        for task_path in sorted(task_root.glob("*/state.json")):
            task = load_json(task_path, "任务状态")
            prepared = [
                item.get("repository")
                for item in task.get("repositories", [])
                if isinstance(item, dict)
                and (item.get("worktree") or {}).get("status") == "prepared"
            ]
            if prepared and not purge:
                raise ValueError(
                    "工作空间仍有已准备的任务 worktree，detach 前必须先清理：%s"
                    % "、".join(prepared)
                )
            if prepared:
                prepared_roots.extend(
                    repository_worktree.task_roots(workspace, task.get("issue_key"))
                )
                repository_worktree.preflight_cleanup(workspace, task)
    if purge:
        allowed = {
            Path(path).relative_to(STATE_DIRECTORY)
            for path in deletable
            if Path(path).parts and Path(path).parts[0] == STATE_DIRECTORY
        }
        allowed.update(
            {
                Path("workspace.json"),
                Path("init.json"),
                Path("tasks.lock"),
                WORKSPACE_GATE_EVENTS,
            }
        )
        for root in prepared_roots:
            current = root
            while current != state_root and state_root in current.parents:
                allowed.add(current.relative_to(state_root))
                current = current.parent
        for path in state_root.rglob("*"):
            relative = path.relative_to(state_root)
            if relative.parts and relative.parts[0] == "tasks":
                if path.is_symlink():
                    raise ValueError("任务状态包含符号链接，拒绝清理：%s" % path)
                continue
            if any(path == root or root in path.parents for root in prepared_roots):
                continue
            if relative == WORKSPACE_GATE_EVENTS and (
                not tree.is_file(Path(STATE_DIRECTORY) / relative)
                or tree.is_symlink(Path(STATE_DIRECTORY) / relative)
            ):
                raise ValueError("工作空间 Gate 审计事件不是普通文件，拒绝清理：%s" % path)
            if relative not in allowed:
                raise ValueError("工作空间状态包含未知文件，拒绝清理：%s" % path)
    return deletable, task_count


def remove_empty_parents(paths, tree):
    parents = {Path(item).parent for item in paths if Path(item).parent.parts}
    for path in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        while path.parts:
            if not tree.rmdir_cached(path):
                break
            path = path.parent


def detach(product_root, workspace, purge=False):
    workspace = Path(workspace).resolve()
    lock = task_store.task_state_lock(workspace) if purge else _null_context()
    with lock:
        with WorkspaceDirectory(workspace) as tree:
            # purge 必须在持有产品级 task-state 锁后重新预检；命令展示阶段的预检
            # 只用于人工确认，不能作为删除事务的证据。
            deletable, _ = detach_preflight(
                product_root, workspace, purge=purge, tree=tree
            )
            issues = []
            if purge:
                registry = task_store.load_registry(workspace, create=False)
                issues = sorted((registry or {}).get("tasks", {}))
                for issue in issues:
                    if not task_store.task_path(workspace, issue).is_file():
                        raise ValueError("任务状态缺失，拒绝 purge：%s" % issue)
                    # 已持有 task-state 锁，必须调用不递归加锁的实现；其内部按固定顺序
                    # 获取 pool 锁并重新预检 worktree/lease。
                    repository_worktree._cleanup_task_locked(workspace, issue)

            for relative in deletable:
                tree.unlink(relative)

            if purge:
                # 锁序固定为 task-state ->（逐任务 pool）-> registry；进入这里时不再
                # 持有 pool 锁。registry 锁内回读任务集合后一次性删除任务状态。
                with task_store.registry_lock(workspace):
                    current = task_store.load_registry(workspace, create=False)
                    current_issues = sorted((current or {}).get("tasks", {}))
                    if current_issues != issues:
                        raise ValueError("purge 期间任务注册表发生变化，拒绝删除")
                    tree.remove_tree(Path(STATE_DIRECTORY) / "tasks", missing_ok=True)
                tree.unlink(Path(STATE_DIRECTORY) / "tasks.lock", missing_ok=True)
                tree.unlink(Path(STATE_DIRECTORY) / WORKSPACE_GATE_EVENTS, missing_ok=True)

            for relative in (
                Path(STATE_DIRECTORY) / "init.json",
                Path(STATE_DIRECTORY) / "workspace.json",
            ):
                tree.unlink(relative, missing_ok=True)

            remove_empty_parents(deletable, tree)
            tree.rmdir_cached(STATE_DIRECTORY)
        unregister(product_root, workspace)


class _null_context:
    def __enter__(self):
        return None

    def __exit__(self, _type, _value, _traceback):
        return False


def command_list(args, product_root):
    workspaces = load_registry(product_root)
    if not workspaces:
        print("没有已登记工作空间")
        return
    print("已登记工作空间：")
    for item in workspaces:
        status, reason = binding_status(product_root, item)
        print("- %s：%s（%s）" % (item, status, reason))


def command_prune(args, product_root):
    targets = select_targets(args, product_root)
    removable, details = [], {}
    for workspace in targets:
        status, reason = binding_status(product_root, workspace)
        details[str(workspace)] = reason
        if status in ("missing", "invalid", "rebound"):
            removable.append(workspace)
    show_targets("prune", removable, details)
    if not removable:
        return
    confirm(args)
    for workspace in removable:
        unregister(product_root, workspace)
    print("已注销 %s 个无法跟踪的工作空间。" % len(removable))


def command_refresh(args, product_root, action):
    targets = select_targets(args, product_root)
    for workspace in targets:
        require_tracked(product_root, workspace)
    show_targets(action, targets)
    if args.all:
        confirm(args)
    for workspace in targets:
        refresh(product_root, workspace)


def command_detach(args, product_root, purge=False):
    if purge and args.all:
        raise ValueError("workspace purge 不支持 --all；请逐个工作空间明确确认")
    targets = select_targets(args, product_root)
    details = {}
    for workspace in targets:
        _, task_count = detach_preflight(product_root, workspace, purge=purge)
        details[str(workspace)] = "将删除接线和绑定，并%s %s 个任务目录" % ("清理" if purge else "保留", task_count)
    show_targets("purge" if purge else "detach", targets, details)
    confirm(args)
    for workspace in targets:
        detach(product_root, workspace, purge=purge)
    print("已%s %s 个工作空间。" % ("彻底清理" if purge else "解绑", len(targets)))


def command_pending(args, product_root):
    pending = []
    for item in load_registry(product_root):
        workspace = Path(item)
        if binding_status(product_root, workspace)[0] != "tracked":
            continue
        try:
            document = load_json(
                workspace_artifact_path(
                    workspace, Path(STATE_DIRECTORY) / "init.json"
                ),
                "工作空间初始化清单",
            )
        except ValueError:
            pending.append(workspace)
            continue
        if document.get("product_ref") != args.product_ref:
            pending.append(workspace)
    if pending:
        print("AgenticOps：检测到 %s 个已知工作空间待刷新；请执行 workspace repair --all，或在使用时执行 start。" % len(pending))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--product-root", required=True)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("register").add_argument("--workspace", required=True)
    commands.add_parser("pending").add_argument("--product-ref", required=True)
    commands.add_parser("list")
    for name in ("prune", "repair", "detach", "purge"):
        command = commands.add_parser(name)
        target = command.add_mutually_exclusive_group(required=True)
        target.add_argument("--workspace")
        target.add_argument("--all", action="store_true")
        command.add_argument("--yes", action="store_true", help="非交互环境确认已展示的目标列表")
    clean = commands.add_parser("clean")
    target = clean.add_mutually_exclusive_group(required=True)
    target.add_argument("--workspace")
    target.add_argument("--all", action="store_true")
    clean.add_argument("--generated-only", action="store_true", required=True)
    clean.add_argument("--yes", action="store_true", help="非交互环境确认已展示的目标列表")
    return result


def main():
    args = parser().parse_args()
    product_root = Path(args.product_root).resolve()
    try:
        if args.command == "register":
            register(product_root, args.workspace)
        elif args.command == "pending":
            command_pending(args, product_root)
        elif args.command == "list":
            command_list(args, product_root)
        elif args.command == "prune":
            command_prune(args, product_root)
        elif args.command == "repair":
            command_refresh(args, product_root, "repair")
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
