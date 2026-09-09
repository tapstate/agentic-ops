#!/usr/bin/env python3
"""在切换产品版本前检查项目工作空间的本地状态兼容性。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from workflow import task_store  # noqa: E402


MANIFEST_PATH = "contracts/workspace-state-compatibility.json"
MANIFEST_SCHEMA_VERSION = 1
# 新协议必须先在可由旧 Updater 到达的过渡版本中提升这里的能力值；该过渡版本的
# minimum_updater_protocol_version 仍保持旧值，后续不兼容版本才能提升最低要求。
UPDATER_PROTOCOL_VERSION = 1
INIT_PATH = Path(".agenticops/init.json")
TASKS_PATH = Path(".agenticops/tasks")


def validate_manifest(document, label):
    required = {
        "schema_version",
        "minimum_updater_protocol_version",
        "workspace_state_epoch",
        "legacy_workspace_state_epoch",
        "supported_workspace_state_epochs",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("%s结构无效" % label)
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("%s schema_version 不支持" % label)
    for field in (
        "minimum_updater_protocol_version",
        "workspace_state_epoch",
        "legacy_workspace_state_epoch",
    ):
        if not isinstance(document.get(field), int) or document[field] < 1:
            raise ValueError("%s %s 无效" % (label, field))
    supported = document.get("supported_workspace_state_epochs")
    if (
        not isinstance(supported, list)
        or not supported
        or any(not isinstance(item, int) or item < 1 for item in supported)
        or len(set(supported)) != len(supported)
        or document["workspace_state_epoch"] not in supported
    ):
        raise ValueError("%s supported_workspace_state_epochs 无效" % label)
    return document


def load_manifest(product_root):
    path = Path(product_root).resolve() / MANIFEST_PATH
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工作空间兼容性清单无法读取：%s" % error) from error
    return validate_manifest(document, "工作空间兼容性清单")


def manifest_at_ref(product_root, reference, missing_epoch=None):
    result = subprocess.run(
        ["git", "-C", str(Path(product_root).resolve()), "show", "%s:%s" % (reference, MANIFEST_PATH)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        if missing_epoch is not None:
            return {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "minimum_updater_protocol_version": 1,
                "workspace_state_epoch": missing_epoch,
                "legacy_workspace_state_epoch": missing_epoch,
                "supported_workspace_state_epochs": [missing_epoch],
            }
        raise ValueError("目标版本缺少工作空间兼容性清单：%s" % reference)
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("目标版本工作空间兼容性清单不是有效 JSON：%s" % reference) from error
    return validate_manifest(document, "版本 %s 的工作空间兼容性清单" % reference)


def load_workspace_registry(product_root):
    path = Path(product_root).resolve() / ".local" / "workspaces.json"
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工作空间提示索引无法读取：%s" % error) from error
    workspaces = document.get("workspaces") if isinstance(document, dict) else None
    if document.get("schema_version") != 1 or not isinstance(workspaces, list):
        raise ValueError("工作空间提示索引结构无效：%s" % path)
    if not all(isinstance(item, str) and item for item in workspaces):
        raise ValueError("工作空间提示索引包含无效路径：%s" % path)
    return sorted(set(workspaces))


def read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("%s无法读取：%s" % (label, error)) from error


def workspace_epoch(workspace, manifest):
    init = read_json(workspace / INIT_PATH, "工作空间初始化清单")
    epoch = init.get("workspace_state_epoch")
    if epoch is None:
        return manifest["legacy_workspace_state_epoch"]
    if not isinstance(epoch, int) or epoch < 1:
        raise ValueError("工作空间状态代际无效：%s" % (workspace / INIT_PATH))
    return epoch


def task_blockers(workspace):
    tasks_root = workspace / TASKS_PATH
    if tasks_root.is_symlink():
        return ["任务状态目录是符号链接"]
    if not tasks_root.exists():
        return []
    if not tasks_root.is_dir():
        return ["任务状态路径不是目录"]
    blockers = []
    registry_path = tasks_root / "index.json"
    if registry_path.is_symlink():
        blockers.append("任务注册表是符号链接")
    elif registry_path.exists():
        registry = read_json(registry_path, "任务注册表")
        tasks = registry.get("tasks") if isinstance(registry, dict) else None
        if not isinstance(tasks, dict):
            blockers.append("任务注册表结构无效")
        else:
            for issue, entry in sorted(tasks.items()):
                status = entry.get("status", "unknown") if isinstance(entry, dict) else "unknown"
                state_path = tasks_root / issue / "state.json"
                run_id = "unknown"
                if state_path.is_file():
                    state = read_json(state_path, "任务状态")
                    run_id = state.get("run_id", "unknown") if isinstance(state, dict) else "unknown"
                blockers.append("任务 %s（status=%s，run=%s）" % (issue, status, run_id))
    try:
        entries = sorted(tasks_root.iterdir())
    except OSError as error:
        raise ValueError("任务状态目录无法读取：%s" % error) from error
    for path in entries:
        if path.name == "index.json":
            continue
        if not any(item.startswith("任务 %s（" % path.name) for item in blockers):
            blockers.append("未清理的任务状态：%s" % path)
    return blockers


def root_state_blockers(workspace):
    state_root = workspace / ".agenticops"
    allowed = {
        "init.json",
        "workspace.json",
        "tasks",
        "tasks.lock",
        "events.jsonl",
        "git-ref-cache-v1.json",
        "git-ref-cache-v1.json.lock",
        "git-ref-cache-v2.json",
        "git-ref-cache-v2.json.lock",
    }
    try:
        entries = sorted(state_root.iterdir())
    except OSError as error:
        raise ValueError("工作空间状态目录无法读取：%s" % error) from error
    return [
        "未识别的旧状态文件：%s" % path
        for path in entries
        if path.name not in allowed
    ]


def workspace_blockers(
    product_root, workspace, target_manifest, current_manifest=None
):
    path = Path(workspace)
    if not path.exists():
        return ["工作空间路径不存在，无法核验：%s" % path]
    if not path.is_dir():
        return ["工作空间路径不是目录：%s" % path]
    binding_path = path / ".agenticops" / "workspace.json"
    if not binding_path.is_file():
        return ["工作空间绑定缺失：%s" % binding_path]
    binding = read_json(binding_path, "工作空间绑定")
    if binding.get("product_root") != str(Path(product_root).resolve()):
        return ["工作空间已绑定到其它 Product Root：%s" % path]
    # 缺少 epoch 的历史 init 必须按当前产品的既有约定解释，不能由目标版本
    # 修改 legacy 映射后把旧状态重新标成另一个代际。
    epoch = workspace_epoch(path, current_manifest or target_manifest)
    if epoch in target_manifest["supported_workspace_state_epochs"]:
        return []
    return [
        "状态代际 %s 不受目标代际 %s 支持" % (
            epoch,
            target_manifest["workspace_state_epoch"],
        ),
        *task_blockers(path),
        *root_state_blockers(path),
    ]


def check_upgrade(product_root, current_ref, target_ref, allow_legacy_target=False):
    current = manifest_at_ref(product_root, current_ref)
    target = manifest_at_ref(
        product_root,
        target_ref,
        missing_epoch=(
            current["legacy_workspace_state_epoch"] if allow_legacy_target else None
        ),
    )
    if target["minimum_updater_protocol_version"] > UPDATER_PROTOCOL_VERSION:
        raise ValueError(
            "目标版本需要更新的升级协议；请先升级到官方指定的过渡版本后再继续"
        )
    if (
        not allow_legacy_target
        and target["workspace_state_epoch"] < current["workspace_state_epoch"]
    ):
        raise ValueError("update 不允许降低工作空间状态代际；需要回退时使用 agenticops rollback")
    current_supported = set(current["supported_workspace_state_epochs"])
    target_supported = set(target["supported_workspace_state_epochs"])
    if (
        current["workspace_state_epoch"] == target["workspace_state_epoch"]
        and current["legacy_workspace_state_epoch"]
        == target["legacy_workspace_state_epoch"]
        and current_supported <= target_supported
    ):
        return []
    blocked = []
    for workspace in load_workspace_registry(product_root):
        try:
            with task_store.task_state_lock(
                workspace,
                allow_product_lifecycle=True,
                allow_incompatible_workspace=True,
            ):
                reasons = workspace_blockers(
                    product_root, workspace, target, current_manifest=current
                )
        except ValueError as error:
            reasons = ["工作空间无法锁定并核验：%s" % error]
        if reasons and not (
            len(reasons) == 1 and reasons[0].startswith("状态代际 ")
        ):
            blocked.append((workspace, reasons))
    if blocked:
        lines = [
            "目标版本包含不兼容的工作空间状态变更，升级已停止。",
            "请先在当前版本完成或停用任务、清理 linked worktree，并显式 purge 本地任务状态；这些操作不修改 Jira。",
        ]
        for workspace, reasons in blocked:
            lines.append("工作空间：%s" % workspace)
            lines.extend("- %s" % reason for reason in reasons)
        lines.append("处理完成后重新执行 agenticops update。")
        raise ValueError("\n".join(lines))
    return []


def require_workspace_can_adopt(product_root, workspace, target_manifest=None):
    """返回 repair 后应记录的代际；跨代际采用前要求工作空间已清空。"""
    target = target_manifest or load_manifest(product_root)
    current_epoch = workspace_epoch(Path(workspace), target)
    if current_epoch in target["supported_workspace_state_epochs"]:
        return current_epoch
    reasons = workspace_blockers(product_root, workspace, target)
    if len(reasons) == 1 and reasons[0].startswith("状态代际 "):
        return target["workspace_state_epoch"]
    raise ValueError(
        "工作空间状态与当前产品不兼容。请先在原版本完成或停用任务、清理 linked worktree，"
        "并显式 purge 本地任务状态；这些操作不修改 Jira。\n- %s"
        % "\n- ".join(reasons)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-upgrade")
    check.add_argument("--current-ref", required=True)
    check.add_argument("--target-ref", required=True)
    check.add_argument("--allow-legacy-target", action="store_true")
    args = parser.parse_args(argv)
    try:
        check_upgrade(
            args.product_root,
            args.current_ref,
            args.target_ref,
            allow_legacy_target=args.allow_legacy_target,
        )
        return 0
    except ValueError as error:
        print("AgenticOps：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
