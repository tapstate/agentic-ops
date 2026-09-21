#!/usr/bin/env python3
"""在切换产品版本前检查不兼容标记与已绑定工位。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MANIFEST_PATH = "contracts/station-state-compatibility.json"
MANIFEST_SCHEMA_VERSION = 1
# 清单格式由既有升级器读取；目标 Runtime 不兼容旧工位状态。
UPDATER_PROTOCOL_VERSION = 2


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
    if type(document.get("schema_version")) is not int or document["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ValueError("%s schema_version 不支持" % label)
    for field in (
        "minimum_updater_protocol_version",
        "station_state_epoch",
        "legacy_station_state_epoch",
    ):
        if type(document.get(field)) is not int or document[field] < 1:
            raise ValueError("%s %s 无效" % (label, field))
    supported = document.get("supported_station_state_epochs")
    if (
        not isinstance(supported, list) or any(type(epoch) is not int for epoch in supported)
        or document["legacy_station_state_epoch"] != document["station_state_epoch"]
        or supported != [document["station_state_epoch"]]
    ):
        raise ValueError("%s 不得声明旧工位状态兼容性" % label)
    return document


def load_manifest(product_root):
    path = Path(product_root).resolve() / MANIFEST_PATH
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("工位兼容性清单无法读取：%s" % error) from error
    return validate_manifest(document, "工位兼容性清单")


def manifest_at_ref(product_root, reference):
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(product_root).resolve()), "show", "%s:%s" % (reference, MANIFEST_PATH)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except UnicodeError as error:
        raise ValueError("目标版本工位兼容性清单编码无效：%s" % reference) from error
    if result.returncode:
        raise ValueError("目标版本缺少工位兼容性清单：%s" % reference)
    try:
        document = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("目标版本工位兼容性清单不是有效 JSON：%s" % reference) from error
    return validate_manifest(document, "版本 %s 的工位兼容性清单" % reference)


def load_station_registry(product_root, required=False):
    path = Path(product_root).resolve() / ".local" / "stations.json"
    if not path.is_file():
        if required:
            raise ValueError("工位登记清单缺失，无法确认全部工位已解绑：%s" % path)
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("工位提示索引无法读取：%s" % error) from error
    stations = document.get("stations") if isinstance(document, dict) else None
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or not isinstance(stations, list)):
        raise ValueError("工位提示索引结构无效：%s" % path)
    if not all(isinstance(item, str) and item for item in stations):
        raise ValueError("工位提示索引包含无效路径：%s" % path)
    return sorted(set(stations))


def check_upgrade(product_root, current_ref, target_ref, operation="update"):
    current = manifest_at_ref(product_root, current_ref)
    target = manifest_at_ref(product_root, target_ref)
    if target["minimum_updater_protocol_version"] > UPDATER_PROTOCOL_VERSION:
        raise ValueError(
            "目标版本需要更新的升级协议；请使用原版本受控解绑后重新安装，不能直接跨越此升级协议"
        )
    if current["station_state_epoch"] == target["station_state_epoch"]:
        return []
    stations = load_station_registry(product_root, required=True)
    if not stations:
        return []
    lines = [
        "目标版本包含不兼容变更，产品版本尚未切换：%s -> %s。" % (
            current_ref, target_ref
        ),
        "请继续使用当前版本完成或终止现有任务，并对以下工位执行 station purge。",
        "目标版本不会读取、迁移或修复旧工位状态；全部工位解绑后重新执行 agenticops %s。" % operation,
        "仍有绑定工位：",
    ]
    lines.extend("- %s" % station for station in stations)
    raise ValueError("\n".join(lines))


def require_station_can_adopt(product_root, station, target_manifest=None):
    """只接受当前产品 epoch；不采用旧工位状态。"""
    target = target_manifest or load_manifest(product_root)
    init_path = Path(station) / ".agenticops" / "init.json"
    try:
        init = json.loads(init_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("工位初始化标记无法读取，请使用原版本解绑并重建：%s" % error) from error
    current_epoch = init.get("station_state_epoch") if isinstance(init, dict) else None
    if type(current_epoch) is int and current_epoch == target["station_state_epoch"]:
        return current_epoch
    raise ValueError(
        "工位状态与当前产品不兼容，repair 不执行跨代际采用。"
        "请使用原版本完成任务后受控解绑并重建；不要手改 epoch 或任务状态。"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-upgrade")
    check.add_argument("--current-ref", required=True)
    check.add_argument("--target-ref", required=True)
    check.add_argument("--operation", choices=("update", "rollback"), default="update")
    args = parser.parse_args(argv)
    try:
        check_upgrade(
            args.product_root,
            args.current_ref,
            args.target_ref,
            operation=args.operation,
        )
        return 0
    except ValueError as error:
        print("AgenticOps：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
