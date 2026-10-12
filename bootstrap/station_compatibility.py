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
from workflow.git_environment import git_environment
from workflow import station_context

MANIFEST_PATH = "contracts/station-state-compatibility.json"
def validate_manifest(document, label):
    try:
        return station_context.validate_manifest(document)
    except ValueError as error:
        raise ValueError("%s结构无效：%s" % (label, error)) from error


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
            env=git_environment(read_only=True),
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
    if current["station_state_epoch"] == target["station_state_epoch"]:
        return []
    stations = load_station_registry(product_root, required=True)
    if not stations:
        return []
    lines = [
        "目标版本包含不兼容变更，产品版本尚未切换：%s -> %s。" % (
            current_ref, target_ref
        ),
        "请继续使用当前版本将任务完成后 release，或按未完成 clean 归档；随后对以下工位执行 station purge。",
        "目标版本不会读取、迁移或修复旧工位状态；全部工位解绑后重新执行 agenticops %s。" % operation,
        "仍有绑定工位：",
    ]
    lines.extend("- %s" % station for station in stations)
    raise ValueError("\n".join(lines))


def require_station_can_adopt(product_root, station, target_manifest=None):
    """只接受当前产品 epoch；不采用旧工位状态。"""
    return station_context.require_epoch(station, product_root, target_manifest)


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
