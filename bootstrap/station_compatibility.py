#!/usr/bin/env python3
"""在切换产品版本前检查项目工位的本地状态兼容性。"""
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


MANIFEST_PATH = "contracts/station-state-compatibility.json"
MANIFEST_SCHEMA_VERSION = 1
# 本版开始保障后续升级；旧安装须原版解绑后重新安装。
UPDATER_PROTOCOL_VERSION = 2
INIT_PATH = Path(".agenticops/init.json")


def validate_manifest(document, label):
    required = {
        "schema_version",
        "minimum_updater_protocol_version",
        "station_state_epoch",
        "legacy_station_state_epoch",
        "supported_station_state_epochs",
    }
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("%s结构无效" % label)
    if document.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("%s schema_version 不支持" % label)
    for field in (
        "minimum_updater_protocol_version",
        "station_state_epoch",
        "legacy_station_state_epoch",
    ):
        if not isinstance(document.get(field), int) or document[field] < 1:
            raise ValueError("%s %s 无效" % (label, field))
    supported = document.get("supported_station_state_epochs")
    if (
        not isinstance(supported, list)
        or not supported
        or any(not isinstance(item, int) or item < 1 for item in supported)
        or len(set(supported)) != len(supported)
        or document["station_state_epoch"] not in supported
    ):
        raise ValueError("%s supported_station_state_epochs 无效" % label)
    return document


def load_manifest(product_root):
    path = Path(product_root).resolve() / MANIFEST_PATH
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工位兼容性清单无法读取：%s" % error) from error
    return validate_manifest(document, "工位兼容性清单")


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
                "station_state_epoch": missing_epoch,
                "legacy_station_state_epoch": missing_epoch,
                "supported_station_state_epochs": [missing_epoch],
            }
        raise ValueError("目标版本缺少工位兼容性清单：%s" % reference)
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("目标版本工位兼容性清单不是有效 JSON：%s" % reference) from error
    return validate_manifest(document, "版本 %s 的工位兼容性清单" % reference)


def load_station_registry(product_root):
    path = Path(product_root).resolve() / ".local" / "stations.json"
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("工位提示索引无法读取：%s" % error) from error
    stations = document.get("stations") if isinstance(document, dict) else None
    if document.get("schema_version") != 1 or not isinstance(stations, list):
        raise ValueError("工位提示索引结构无效：%s" % path)
    if not all(isinstance(item, str) and item for item in stations):
        raise ValueError("工位提示索引包含无效路径：%s" % path)
    return sorted(set(stations))


def read_json(path, label):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("%s无法读取：%s" % (label, error)) from error


def station_epoch(station, manifest):
    init = read_json(station / INIT_PATH, "工位初始化清单")
    epoch = init.get("station_state_epoch")
    if epoch is None:
        return manifest["legacy_station_state_epoch"]
    if not isinstance(epoch, int) or epoch < 1:
        raise ValueError("工位状态代际无效：%s" % (station / INIT_PATH))
    return epoch


def station_blockers(
    product_root, station, target_manifest, current_manifest=None
):
    path = Path(station)
    if not path.exists():
        return ["工位路径不存在，无法核验：%s" % path]
    if not path.is_dir():
        return ["工位路径不是目录：%s" % path]
    binding_path = path / ".agenticops" / "station.json"
    if not binding_path.is_file():
        return ["工位绑定缺失：%s" % binding_path]
    binding = read_json(binding_path, "工位绑定")
    if binding.get("product_root") != str(Path(product_root).resolve()):
        return ["工位已绑定到其它 Product Root：%s" % path]
    # 缺少 epoch 的历史 init 必须按当前产品的既有约定解释，不能由目标版本
    # 修改 legacy 映射后把旧状态重新标成另一个代际。
    epoch = station_epoch(path, current_manifest or target_manifest)
    if epoch in target_manifest["supported_station_state_epochs"]:
        return []
    return [
        "状态代际 %s 不受目标代际 %s 支持" % (
            epoch,
            target_manifest["station_state_epoch"],
        ),
    ]


def check_upgrade(product_root, current_ref, target_ref, allow_legacy_target=False):
    current = manifest_at_ref(product_root, current_ref)
    target = manifest_at_ref(
        product_root,
        target_ref,
        missing_epoch=(
            current["legacy_station_state_epoch"] if allow_legacy_target else None
        ),
    )
    if target["minimum_updater_protocol_version"] > UPDATER_PROTOCOL_VERSION:
        raise ValueError(
            "目标版本需要更新的升级协议；请使用原版本受控解绑后重新安装，不能直接跨越此升级协议"
        )
    if (
        not allow_legacy_target
        and target["station_state_epoch"] < current["station_state_epoch"]
    ):
        raise ValueError("update 不允许降低工位状态代际；需要回退时使用 agenticops rollback")
    current_supported = set(current["supported_station_state_epochs"])
    target_supported = set(target["supported_station_state_epochs"])
    if (
        current["station_state_epoch"] == target["station_state_epoch"]
        and current["legacy_station_state_epoch"]
        == target["legacy_station_state_epoch"]
        and current_supported <= target_supported
    ):
        return []
    blocked = []
    for station in load_station_registry(product_root):
        try:
            with task_store.task_state_lock(
                station,
                allow_product_lifecycle=True,
                allow_incompatible_station=True,
            ):
                reasons = station_blockers(
                    product_root, station, target, current_manifest=current
                )
        except ValueError as error:
            reasons = ["工位无法锁定并核验：%s" % error]
        if reasons:
            blocked.append((station, reasons))
    if blocked:
        lines = [
            "目标版本包含不兼容的工位状态变更，升级已停止。",
            "产品版本尚未切换：%s -> %s。" % (current_ref, target_ref),
            "请先在当前版本完成或明确停用任务，导出并核验需保留材料，按确认范围清理运行现场，将旧工位受控解绑并重建；这些操作不修改 Jira。",
            "先使用当前版本执行受控 station purge 注销旧绑定；即使任务为空也不能直接跨代际采用。升级成功后再明确初始化新工位，不自动删除 source、config、archive 或导出材料。",
        ]
        for station, reasons in blocked:
            lines.append("工位：%s" % station)
            lines.extend("- %s" % reason for reason in reasons)
        lines.append("处理完成后重新执行 agenticops %s。" % (
            "rollback" if allow_legacy_target else "update"))
        raise ValueError("\n".join(lines))
    return []


def require_station_can_adopt(product_root, station, target_manifest=None):
    """repair 仅保留受支持代际；不把空的旧绑定在线转成新工位。"""
    target = target_manifest or load_manifest(product_root)
    current_epoch = station_epoch(Path(station), target)
    if current_epoch in target["supported_station_state_epochs"]:
        return current_epoch
    reasons = station_blockers(product_root, station, target)
    raise ValueError(
        "工位状态与当前产品不兼容，repair 不执行跨代际采用。"
        "请恢复可处理该状态的原产品版本，保存材料后将这个旧工位受控解绑并重建；"
        "不要手改 epoch 或任务状态，这些操作不修改 Jira。\n- %s"
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
