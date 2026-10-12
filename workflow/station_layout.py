"""中央结构合同的只读访问；不选择清理动作或保存工位状态。"""
import hashlib
import json
from pathlib import Path


def load(product_root=None):
    root = Path(product_root) if product_root else Path(__file__).resolve().parents[1]
    path = root / "contracts/station-layout.json"
    if path.is_symlink():
        raise ValueError("工位结构合同不能是符号链接")
    raw = path.read_bytes()
    from workflow.station_context import _unique_object
    value = json.loads(raw, object_pairs_hook=_unique_object)
    validate(value)
    return {**value, "digest": hashlib.sha256(raw).hexdigest()}


def validate(value):
    if not isinstance(value, dict) or set(value) != {"version", "roots"} or type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["roots"], list):
        raise ValueError("工位结构合同无效")
    paths, roles = set(), set()
    for row in value["roots"]:
        if (not isinstance(row, dict) or set(row) != {"path", "role", "actions", "create"}
                or not isinstance(row["path"], str) or row["path"] in ("", ".", "..")
                or any(c in row["path"] for c in ("/", "\\", "*", "?", "\0"))
                or not isinstance(row["role"], str) or not row["role"]
                or type(row["create"]) is not bool or not isinstance(row["actions"], list)
                or not row["actions"] or not all(isinstance(a, str) for a in row["actions"])
                or row["path"] in paths or row["role"] in roles):
            raise ValueError("工位结构角色重复或无效")
        paths.add(row["path"]); roles.add(row["role"])


def name(role, product_root=None):
    found = [row["path"] for row in load(product_root)["roots"] if row["role"] == role]
    if len(found) != 1:
        raise ValueError("工位结构缺少唯一角色：" + role)
    return found[0]


def station_name(base, role):
    from workflow import project_rules
    return name(role, project_rules.product_root_from_station(base))


def station_roots(base):
    from workflow import project_rules
    return {row["path"]: row for row in load(project_rules.product_root_from_station(base))["roots"]}


def relative(base, role, suffix):
    return station_name(base, role) + "/" + suffix
