#!/usr/bin/env python3
"""仅维护面：导出指定旧版的空工位，不迁移任务，不递归删除材料。"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bootstrap.workspace_paths import WorkspaceDirectory
from bootstrap import workspace_registry

OLD_REF = "25d0b2c66298b5c00eb961d2929116846e113681"
STATE = ".agenticops"
STATE_FILES = {"workspace.json", "init.json", "tasks.lock", "tasks/index.json",
               "git-ref-cache-v2.json", "git-ref-cache-v2.json.lock"}
WIRES = {"agenticops", "AGENTS.md", "CLAUDE.md", ".mcp.json"}
WIRES.update(platform + "/skills/" + skill for platform in (".agents", ".claude")
             for skill in ("tapdata-task", "tapdata-align-branches"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fingerprint(tree, name):
    info = tree._raw_lstat(name)
    if info is None:
        return None
    if stat.S_ISLNK(info.st_mode):
        return {"kind": "symlink", "target": tree.readlink(name)}
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("不是普通文件或受管链接：" + str(name))
    return {"kind": "file", "sha256": hashlib.sha256(tree.read_text(name).encode()).hexdigest(),
            "mode": stat.S_IMODE(info.st_mode)}


def inventory(tree, prefix=STATE):
    parent, leaf = tree._parent(prefix + "/placeholder")
    result = {}
    def walk(fd, relative):
        for name in sorted(os.listdir(fd)):
            key = relative + "/" + name if relative else name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if key != "tasks":
                    raise ValueError("未知旧状态目录：" + key)
                child = os.open(name, tree._directory_flags(), dir_fd=fd)
                try:
                    walk(child, key)
                finally:
                    os.close(child)
            else:
                if key not in STATE_FILES or stat.S_ISLNK(info.st_mode):
                    raise ValueError("未知旧状态文件：" + key)
                result[key] = fingerprint(tree, prefix + "/" + key)
    walk(parent, "")
    return result


def snapshot(tree, product):
    files = inventory(tree)
    binding = tree.read_json(STATE + "/workspace.json", "旧绑定")
    init = tree.read_json(STATE + "/init.json", "旧清单")
    tasks = tree.read_json(STATE + "/tasks/index.json", "旧任务索引")
    if (binding.get("schema_version") != 2 or binding.get("product_root") != str(product)
            or binding.get("project") != "tapdata" or not binding.get("workspace_id")
            or init.get("schema_version") != 2 or init.get("workspace_state_epoch") != 2
            or init.get("product_ref") != OLD_REF
            or tasks != {"schema_version": 1, "project": "tapdata", "tasks": {}}):
        raise ValueError("只支持指定原版本、绑定一致且无任务的 TapData 工位")
    if "git-ref-cache-v2.json" in files:
        cache = tree.read_json(STATE + "/git-ref-cache-v2.json", "旧引用缓存")
        if cache.get("schema_version") != 2 or not isinstance(cache.get("roots"), dict):
            raise ValueError("旧缓存来源结构无法确认")
    wires = {}
    for item in init.get("artifacts", []):
        name = item.get("path")
        if name not in WIRES or name in wires:
            raise ValueError("初始化清单含不支持或重复的接线")
        found = fingerprint(tree, name)
        if found is None or found["kind"] != item.get("kind", "file"):
            raise ValueError("接线缺失或类型变化：" + name)
        key = "target" if found["kind"] == "symlink" else "sha256"
        if found[key] != item.get(key):
            raise ValueError("接线已被修改：" + name)
        wires[name] = found
    if set(wires) != WIRES:
        raise ValueError("不支持的旧接线集合")
    return {"state": files, "wires": wires, "workspace_id": binding["workspace_id"]}


def canonical(path):
    value = Path(path).absolute()
    if value != value.resolve() or value == Path(value.anchor):
        raise ValueError("必须使用无符号链接的精确绝对目录")
    return value


@contextlib.contextmanager
def locked(tree, state_tree):
    with contextlib.ExitStack() as stack:
        for fd in (tree._fds[()], state_tree._parent(STATE + "/placeholder")[0]):
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stack.callback(fcntl.flock, fd, fcntl.LOCK_UN)
        for name in ("tasks.lock", "git-ref-cache-v2.json.lock"):
            path = STATE + "/" + name
            if state_tree._raw_lstat(path) is not None:
                parent, leaf = state_tree._parent(path)
                fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def plan(workspace, backup, product):
    workspace, backup, product = map(canonical, (workspace, backup, product))
    if workspace == product or workspace in backup.parents or backup in workspace.parents:
        raise ValueError("备份必须在工位外，且工位不能是产品根")
    with WorkspaceDirectory(workspace) as tree, locked(tree, tree):
        value = {"schema_version": 1, "workspace": str(workspace), "product_root": str(product),
                 "backup": str(backup), "snapshot": snapshot(tree, product)}
        value["digest"] = digest(value)
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        if backup.stat().st_dev != workspace.stat().st_dev:
            raise ValueError("恢复备份必须与工位同文件系统，以便原子移动")
        with WorkspaceDirectory(backup) as target:
            target.write_json_atomic("plan.json", value)
        return value


def apply(backup, confirmed_digest, stopped):
    if not stopped:
        raise ValueError("必须确认已停止该工位的 Agent 与应用写入")
    backup = canonical(backup)
    with WorkspaceDirectory(backup) as target:
        value = target.read_json("plan.json", "恢复清单")
        actual = digest({k: v for k, v in value.items() if k != "digest"})
        if value.get("digest") != actual or actual != confirmed_digest or value["backup"] != str(backup):
            raise ValueError("恢复清单摘要不匹配")
        workspace, product = canonical(value["workspace"]), canonical(value["product_root"])
        with WorkspaceDirectory(workspace) as tree:
            state_tree = tree if tree.exists(STATE) else target
            with locked(tree, state_tree):
                expected = value["snapshot"]
                if inventory(state_tree) != expected["state"]:
                    raise ValueError("旧状态现场发生变化，保留材料并停止")
                for name, record in expected["wires"].items():
                    source, saved = fingerprint(tree, name), fingerprint(target, name)
                    if not ((source == record and saved is None) or (source is None and saved == record)):
                        raise ValueError("接线或备份发生变化：" + name)
                for name, record in expected["wires"].items():
                    if fingerprint(tree, name) is None:
                        continue
                    src_fd, src_name = tree._parent(name)
                    dst_fd, dst_name = target._parent(name, create=True)
                    if fingerprint(tree, name) != record or fingerprint(target, name) is not None:
                        raise ValueError("移动前现场变化：" + name)
                    os.rename(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
                    if fingerprint(target, name) != record:
                        raise ValueError("导出回读不一致：" + name)
                if tree.exists(STATE):
                    if inventory(tree) != expected["state"] or target.exists(STATE):
                        raise ValueError("状态导出前现场变化")
                    os.rename(STATE, STATE, src_dir_fd=tree._fds[()], dst_dir_fd=target._fds[()])
                # 删除的仅是已导出接线留下的空父目录；未知用户材料自然保留。
                for name in (".agents/skills", ".claude/skills", ".agents", ".claude"):
                    tree.rmdir_cached(name)
                workspace_registry.unregister(product, workspace)
                target.write_json_atomic("receipt.json", {"digest": actual, "status": "exported",
                    "workspace": str(workspace), "backup": str(backup)})
                return {"status": "exported", "backup": str(backup), "workspace": str(workspace)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("plan")
    for name in ("workspace", "backup", "product-root"):
        prepare.add_argument("--" + name, required=True)
    execute = sub.add_parser("apply")
    execute.add_argument("--backup", required=True)
    execute.add_argument("--confirmed-digest", required=True)
    execute.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    try:
        result = (plan(args.workspace, args.backup, args.product_root) if args.command == "plan"
                  else apply(args.backup, args.confirmed_digest, args.writers_stopped))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, "受控恢复停止：" + str(error) + "\n")


if __name__ == "__main__":
    main()
