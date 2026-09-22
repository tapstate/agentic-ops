"""工位根条目的两层名单；只判定，不执行文件操作或合并配置。"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
import stat

from workflow import project_rules

CORE = {"source": "source-reset", "runtime": "clear-children", ".agenticops": "lifecycle-clean",
        "config": "preserve", "archive": "preserve"}
ACTIONS = set(CORE.values()) | {"remove"}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("清理配置包含重复键：" + key)
        value[key] = item
    return value


def pattern(value):
    name = value[:-1] if isinstance(value, str) and value.endswith("/") else value
    if (not isinstance(name, str) or not name or name in (".", "..")
            or any(c in name for c in ("/", "\\", "!", "[", "]")) or "**" in name):
        raise ValueError("清理规则只支持根名称、*、? 和目录后缀 /")
    return value


def load(base):
    root, project = project_rules.station_context(base)
    paths = [root / "policies/station-clean.json", project_rules.project_root(root, project) / "station-clean.json"]
    layers, hashes = [], []
    for index, path in enumerate(paths):
        if path.is_symlink():
            raise ValueError("清理配置不能为符号链接")
        if index and not path.exists():
            layers.append({"version": 1, "preserve": [], "clean": []})
            hashes.append(None)
            continue
        raw = path.read_bytes()
        value = json.loads(raw, object_pairs_hook=_object)
        if (not isinstance(value, dict) or set(value) != {"version", "preserve", "clean"}
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["preserve"], list) or not isinstance(value["clean"], list)):
            raise ValueError("清理配置结构或版本无效")
        for item in value["preserve"]:
            pattern(item)
        if len(set(value["preserve"])) != len(value["preserve"]):
            raise ValueError("保留规则重复")
        seen = set()
        for item in value["clean"]:
            if (not isinstance(item, dict) or set(item) != {"pattern", "action"}
                    or not isinstance(item["action"], str) or item["action"] not in ACTIONS - {"preserve"}):
                raise ValueError("清理动作无效")
            key = pattern(item["pattern"])
            if key in seen:
                raise ValueError("清理规则重复：" + key)
            seen.add(key)
        layers.append(value)
        hashes.append(hashlib.sha256(raw).hexdigest())
    return {"layers": layers, "digests": hashes}


def classify(config, name, directory):
    def matches(value):
        return (not value.endswith("/") or directory) and fnmatch.fnmatchcase(name, value.rstrip("/"))
    for field in ("preserve", "clean"):
        for index, layer in enumerate(config["layers"]):
            found = [item for item in layer[field] if matches(item if field == "preserve" else item["pattern"])]
            if found:
                actions = {"preserve"} if field == "preserve" else {item["action"] for item in found}
                if len(actions) != 1:
                    raise ValueError("同层清理动作冲突：" + name)
                return {"action": actions.pop(), "layer": "central" if index == 0 else "project"}
    return {"action": "block", "layer": "unmatched"}


def inspect(base, owned=(), registered=(), partial=False):
    root = Path(base).resolve()
    config = load(base)
    result, errors = {}, []
    # 即使对象缺失，规则也不能取消核心生命周期。
    for name, action in CORE.items():
        if classify(config, name, True)["action"] != action:
            errors.append("规则与工位生命周期冲突：" + name)
    for path in sorted(root.iterdir()):
        name = path.name
        # 初始化接线由既有 manifest 验证，名单不能把它改成清理对象。
        managed = name in owned or any(item.startswith(name + "/") for item in owned)
        mode = path.lstat().st_mode
        if managed:
            if classify(config, name, stat.S_ISDIR(mode))["action"] not in ("block", "preserve"):
                errors.append("名单不能清理初始化接线：" + name)
            continue
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)) or path.is_mount():
            errors.append("工位对象不是普通文件或目录：" + name)
            result[name] = {"action": "block", "layer": "unsafe"}
            continue
        try:
            decision = classify(config, name, stat.S_ISDIR(mode))
            action = decision["action"]
            if name in CORE and (action != CORE[name] or not stat.S_ISDIR(mode)):
                errors.append("工位核心目录规则或类型异常：" + name)
            elif action in ("source-reset", "clear-children", "lifecycle-clean") and CORE.get(name) != action:
                errors.append("专用处理器目标不匹配：" + name)
            elif action == "block":
                errors.append("工位存在未知材料：" + name)
            elif action == "remove" and name not in registered:
                errors.append("清理对象缺少当前任务目录归属：" + name)
            result[name] = decision
        except ValueError as exc:
            errors.append(str(exc))
            result[name] = {"action": "block", "layer": "invalid"}
    if partial:
        return {"digests": config["digests"], "objects": result, "errors": errors}
    if errors:
        raise ValueError("；".join(errors))
    return {"digests": config["digests"], "objects": result}


def validate_snapshot(plan):
    """读取持久计划时校验版本边界；不加载当前配置改变旧计划。"""
    version = plan.get("schema_version")
    if version == 3 and "rules" not in plan:
        return
    snapshot = plan.get("rules")
    if version not in (4, 5, 6) or (version == 5 and not isinstance(plan.get("native_clean"), dict)) or not isinstance(snapshot, dict) or set(snapshot) != {"digests", "objects"}:
        raise ValueError("清理计划版本与规则快照不匹配")
    hashes = snapshot["digests"]
    valid_hash = lambda value: isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
    if (not isinstance(hashes, list) or len(hashes) != 2 or not valid_hash(hashes[0])
            or (hashes[1] is not None and not valid_hash(hashes[1]))):
        raise ValueError("清理规则摘要无效")
    if not isinstance(snapshot["objects"], dict):
        raise ValueError("清理规则对象无效")
    for name, item in snapshot["objects"].items():
        if (not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name
                or not isinstance(item, dict) or set(item) != {"action", "layer"}
                or not isinstance(item["action"], str) or not isinstance(item["layer"], str)
                or item["action"] not in ACTIONS or item["layer"] not in ("central", "project")):
            raise ValueError("清理规则判定无效")
