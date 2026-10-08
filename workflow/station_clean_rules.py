"""清理规则的统一判定与冻结边界；不执行删除，不选择生命周期顺序。"""
from __future__ import annotations
import fnmatch
import hashlib
import json
import re
import stat
from pathlib import Path
from workflow import project_rules, station_layout

ACTIONS = {"preserve", "remove", "source-reset", "clear-children", "lifecycle-clean", "archive", "discard"}
COMBINATIONS = {
    ("station-root", "directory"): {"preserve", "remove", "source-reset", "clear-children", "lifecycle-clean"},
    ("station-root", "file"): {"preserve", "discard"},
    ("source-layout", "file"): {"preserve", "discard"},
    ("repository", "file"): {"archive", "discard"},
}


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("清理配置包含重复键：" + key)
        value[key] = item
    return value


def pattern(value):
    if (not isinstance(value, str) or not value or value in (".", "..")
            or any(c in value for c in ("/", "\\", "!", "[", "]", "\0")) or "**" in value):
        raise ValueError("清理规则只支持名称、* 和 ?，不支持路径表达式")
    return value


def validate_layer(value):
    if (not isinstance(value, dict) or set(value) != {"version", "rules"}
            or type(value["version"]) is not int or value["version"] != 2 or not isinstance(value["rules"], list)):
        raise ValueError("清理配置结构或版本无效；版本 1 必须由原版本退出")
    seen = set()
    for row in value["rules"]:
        if not isinstance(row, dict) or set(row) != {"scope", "pattern", "type", "action"}:
            raise ValueError("清理规则必须包含 scope/pattern/type/action")
        if any(not isinstance(row[k], str) for k in row):
            raise ValueError("清理规则字段类型无效")
        pattern(row["pattern"])
        if row["action"] not in COMBINATIONS.get((row["scope"], row["type"]), set()):
            raise ValueError("清理动作与作用域或对象类型不匹配")
        key = tuple(row[k] for k in ("scope", "pattern", "type", "action"))
        if key in seen:
            raise ValueError("清理规则重复")
        seen.add(key)


def load(base):
    root, project = project_rules.station_context(base)
    paths = [root / "policies/station-clean.json", project_rules.project_root(root, project) / "station-clean.json"]
    layers, hashes = [], []
    for index, path in enumerate(paths):
        if path.is_symlink():
            raise ValueError("清理配置不能为符号链接")
        if index and not path.exists():
            layers.append({"version": 2, "rules": []}); hashes.append(None)
            continue
        raw = path.read_bytes()
        value = json.loads(raw, object_pairs_hook=_object)
        validate_layer(value)
        layers.append(value); hashes.append(hashlib.sha256(raw).hexdigest())
    return {"version": 2, "layers": layers, "digests": hashes,
            "structure": station_layout.load(root)}


def classify(config, name, directory=False, scope="station-root"):
    kind = "directory" if directory else "file"
    for index in (1, 0):
        matches = [row for row in config["layers"][index]["rules"]
                   if row["scope"] == scope and row["type"] == kind and fnmatch.fnmatchcase(name, row["pattern"])]
        if matches:
            actions = {r["action"] for r in matches}
            protected = actions & {"preserve", "archive"}
            if protected:
                actions = protected
            if len(actions) != 1:
                raise ValueError("同层清理动作冲突：" + name)
            return {"action": actions.pop(), "layer": "central" if index == 0 else "project"}
    return {"action": "archive" if scope == "repository" else "block", "layer": "unmatched"}


def regular_file(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode) and not path.is_mount()
    except FileNotFoundError:
        return False


def file_action(config, path, scope):
    return classify(config, Path(path).name, scope=scope)["action"] if regular_file(path) else "block"


def inspect(base, owned=(), registered=(), partial=False, config=None):
    root = Path(base).resolve()
    config = config or load(base)
    structures = {r["path"]: r for r in config["structure"]["roots"]}
    result, errors = {}, []
    for name, row in structures.items():
        if classify(config, name, True)["action"] not in row["actions"]:
            errors.append("规则与工位生命周期冲突：" + name)
    for path in sorted(root.iterdir()):
        name, mode = path.name, path.lstat().st_mode
        managed = name in owned or any(item.startswith(name + "/") for item in owned)
        if managed:
            if classify(config, name, stat.S_ISDIR(mode))["action"] not in ("block", "preserve"):
                errors.append("名单不能清理初始化接线：" + name)
            continue
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)) or path.is_mount():
            errors.append("工位对象不是普通文件或目录：" + name)
            result[name] = {"action": "block", "layer": "unsafe"}; continue
        try:
            decision = classify(config, name, stat.S_ISDIR(mode))
            action = decision["action"]
            if name in structures and (action not in structures[name]["actions"] or not stat.S_ISDIR(mode)):
                errors.append("工位核心目录规则或类型异常：" + name)
            elif action in ("source-reset", "clear-children", "lifecycle-clean") and name not in structures:
                errors.append("专用处理器目标不匹配：" + name)
            elif action == "block":
                errors.append("工位存在未知材料：" + name)
            elif action == "remove" and name not in registered:
                errors.append("清理对象缺少当前任务目录归属：" + name)
            result[name] = decision
        except ValueError as exc:
            errors.append(str(exc)); result[name] = {"action": "block", "layer": "invalid"}
    value = {**config, "objects": result}
    if partial:
        return {**value, "errors": errors}
    if errors:
        raise ValueError("；".join(errors))
    return value


def freeze(base, task, config, catalog, states, protected=()):
    """盘点来源固定为已核验现场；执行不得重新按 Catalog 扩仓。"""
    from workflow import station_directories as directories, station_source as source
    from workflow.station_reset_result import source_directories
    root = Path(base).resolve()
    batches = {"station-root": {"scope": "station-root", "directories": {".": directories.identity(root)}}}
    tree = root / station_layout.name("repositories", project_rules.product_root_from_station(base))
    layouts = {}
    if tree.exists():
        layouts[tree.relative_to(root).as_posix()] = directories.identity(tree)
        for owner in tree.iterdir():
            if owner.is_dir() and not owner.is_symlink():
                layouts[owner.relative_to(root).as_posix()] = directories.identity(owner)
                for repo in owner.iterdir():
                    name = owner.name + "/" + repo.name
                    if name not in catalog or name in task.get("replan_preserved", {}) or not repo.is_dir():
                        continue
                    source.identity(repo, catalog[name]["origin"])
                    if states.get(name, {}).get("initial_checkout"):
                        continue
                    prefix = repo.relative_to(root).as_posix()
                    all_dirs = {prefix: directories.identity(repo)}
                    observed_dirs = states[name]["directories"] if name in states else source_directories(repo)
                    all_dirs.update({prefix + "/" + p: i for p, i in observed_dirs.items()})
                    batches["repository:" + name] = {"scope": "repository", "repository": name,
                        "origin": catalog[name]["origin"], "directories": all_dirs}
    batches["source-layout"] = {"scope": "source-layout", "directories": layouts}
    for batch in batches.values():
        found = []
        if batch["scope"] == "repository":
            repo = source.repository_path(base, batch["repository"])
            reset_sha = states.get(batch["repository"], {}).get("neutral", {}).get("sha", "HEAD")
            for args in (("--others", "--exclude-standard"), ("--others", "--ignored", "--exclude-standard")):
                for filename in filter(None, source.git(repo, "ls-files", *args, "-z").stdout.split("\0")):
                    relative = (repo / filename).relative_to(root).as_posix()
                    if relative not in protected and source.untracked_discard(repo, filename, config, reset_sha):
                        found.append(relative)
        else:
            for directory in batch["directories"]:
                for path in (root if directory == "." else root/directory).iterdir():
                    relative = path.relative_to(root).as_posix()
                    if relative not in protected and file_action(config, path, batch["scope"]) == "discard":
                        found.append(relative)
        batch["files"] = sorted(set(found))
    return {**config, "batches": batches}


def validate_snapshot(plan):
    """新 epoch 只读取当前规则快照，不重解释旧计划。"""
    snapshot = plan.get("rules")
    if plan.get("schema_version") != 6 or not isinstance(snapshot, dict) or set(snapshot) != {"version", "layers", "digests", "structure", "objects", "batches"} or snapshot.get("version") != 2:
        raise ValueError("清理计划版本与规则快照不匹配")
    if not isinstance(snapshot["layers"], list) or len(snapshot["layers"]) != 2:
        raise ValueError("清理规则层无效")
    for layer in snapshot["layers"]:
        validate_layer(layer)
    hashes = snapshot["digests"]
    valid_hash = lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
    if not isinstance(hashes, list) or len(hashes) != 2 or not valid_hash(hashes[0]) or (hashes[1] is not None and not valid_hash(hashes[1])):
        raise ValueError("清理规则摘要无效")
    if not isinstance(snapshot["structure"], dict) or not valid_hash(snapshot["structure"].get("digest")):
        raise ValueError("工位结构快照无效")
    station_layout.validate({k:v for k,v in snapshot["structure"].items() if k != "digest"})
    if not isinstance(snapshot["objects"], dict) or not isinstance(snapshot["batches"], dict):
        raise ValueError("清理规则对象或边界无效")
    for name, item in snapshot["objects"].items():
        pattern(name)
        if not isinstance(item, dict) or set(item) != {"action", "layer"} or not isinstance(item["action"], str) or item["action"] not in ACTIONS or item["layer"] not in ("central", "project"):
            raise ValueError("清理规则判定无效")
    if not {"station-root", "source-layout"} <= set(snapshot["batches"]):
        raise ValueError("清理计划缺少必要边界批次")
    for key, batch in snapshot["batches"].items():
        if not isinstance(batch, dict):
            raise ValueError("清理扫描批次无效")
        scope = batch.get("scope")
        if scope not in ("station-root", "source-layout", "repository") or not isinstance(batch.get("directories"), dict) or not isinstance(batch.get("files"), list):
            raise ValueError("清理扫描边界无效")
        fields = {"scope", "directories", "files"} | ({"repository", "origin"} if scope == "repository" else set())
        if set(batch) != fields or (scope != "repository" and key != scope):
            raise ValueError("清理扫描批次字段无效")
        if scope == "repository" and (key != "repository:" + str(batch.get("repository")) or not isinstance(batch.get("origin"), str)):
            raise ValueError("清理仓库边界无效")
        for path, identity in batch["directories"].items():
            if path != "." and (not isinstance(path, str) or any(p in ("", ".", "..", ".git") for p in path.split("/"))):
                raise ValueError("清理边界路径无效")
            if not isinstance(identity, dict) or set(identity) != {"device", "inode"} or any(type(v) is not int for v in identity.values()) or identity["device"] < 0 or identity["inode"] < 1:
                raise ValueError("清理边界身份无效")
        for filename in batch["files"]:
            if (not isinstance(filename, str) or any(p in ("", ".", "..", ".git") for p in filename.split("/"))
                    or str(Path(filename).parent) not in batch["directories"]):
                raise ValueError("清理文件盘点越过冻结边界")
