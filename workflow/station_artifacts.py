"""退出前保存源码成果并在临时仓库验证；不恢复历史任务或执行远端操作。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import tempfile

from workflow import project_rules, station_source as source

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git_bytes(repository, *args, data=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")
    result = subprocess.run(["git", "-C", str(repository), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=120)
    if result.returncode:
        raise ValueError("源码成果 Git 校验失败：%s" % args[0])
    return result.stdout


def patch(repository, filenames, cached=False):
    if not filenames:
        return b""
    return git_bytes(repository, "diff", "--no-ext-diff", "--no-textconv", "--binary", "--full-index", "--no-renames",
                     *( ["--cached", "HEAD"] if cached else []), "--", *filenames)


def safe_source(base, name, filename):
    from workflow.station_resources import resource_path
    return resource_path(base, "source/" + name + "/" + filename)


def verify_special_entries(repository, reset_sha="HEAD"):
    """仅保留索引、当前提交及归位提交完全一致的空未初始化 gitlink。"""
    def special(raw, tree=False):
        result = {}
        for row in filter(None, raw.split(b"\0")):
            metadata, filename = row.split(b"\t", 1)
            fields = metadata.split()
            if fields[0] == b"120000":
                raise ValueError("源码含跟踪链接，不支持自动重置")
            if fields[0] == b"160000":
                if not tree and fields[2] != b"0":
                    raise ValueError("submodule 索引存在冲突")
                result[filename] = fields[2] if tree else fields[1]
        return result

    index = special(git_bytes(repository, "ls-files", "--stage", "-z"))
    head = special(git_bytes(repository, "ls-tree", "-r", "-z", "HEAD"), True)
    target = special(git_bytes(repository, "ls-tree", "-r", "-z", reset_sha), True)
    if index != head or head != target:
        raise ValueError("submodule 与当前或归位基线不一致，不支持自动重置")
    for filename in index:
        parts = os.fsdecode(filename).split("/")
        if any(part in ("", ".", "..", ".git") for part in parts):
            raise ValueError("submodule 路径不安全")
        path = repository
        for part in parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("submodule 路径含符号链接")
            if path.exists() and not path.is_dir():
                raise ValueError("submodule 路径不是目录")
        if path.exists() and any(path.iterdir()):
            raise ValueError("submodule 已初始化或包含内容，不支持自动重置")


def snapshot(base, name, roots, decisions, reset_sha="HEAD"):
    from workflow.station_resources import fingerprint
    from workflow.station_directories import covered
    repository = source.repository_path(base, name)
    if git_bytes(repository, "ls-files", "--unmerged"):
        raise ValueError("冲突索引不支持自动重置，请先明确处理")
    index = git_bytes(repository, "ls-files", "--stage", "-z")
    verify_special_entries(repository, reset_sha)
    changed = set(filter(None, (git_bytes(repository, "diff", "--name-only", "--no-renames", "-z").decode().split("\0")
                  + git_bytes(repository, "diff", "--cached", "--name-only", "--no-renames", "-z", "HEAD").decode().split("\0"))))
    untracked = set(filter(None, git_bytes(repository, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")))
    ignored = filter(None, git_bytes(repository, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").decode().split("\0"))
    for filename in ignored:
        if not covered("source/" + name + "/" + filename, roots):
            raise ValueError("源码含未登记 ignored 产物：%s" % filename)
    entries = []
    head = source.git(repository, "rev-parse", "HEAD").stdout.strip()
    for filename in sorted(changed | untracked):
        relative = "source/" + name + "/" + filename
        if covered(relative, roots):
            if filename in changed:
                raise ValueError("生成目录包含源码修改")
            continue
        target = safe_source(base, name, filename)
        before = fingerprint(target)
        before_index = source.git(repository, "ls-files", "--stage", "-z", "--", filename).stdout
        choice = decisions.get(relative, {"action": "archive"})
        if choice.get("action") not in ("archive", "export", "discard"):
            raise ValueError("源码成果必须选择 archive/export/discard")
        proof = {"head": head, "before": before, "before_index": before_index,
                 "index_patch": digest(patch(repository, [filename], True)), "worktree_patch": digest(patch(repository, [filename]))}
        if choice["action"] != "archive" and choice.get("snapshot") != proof:
            raise ValueError("源码导出或丢弃决定未绑定当前完整成果指纹：%s" % relative)
        info = target.stat() if target.exists() else None
        entries.append({"path": relative, "repository": name, "file": filename,
                        "file_identity": {"device": info.st_dev, "inode": info.st_ino, "mtime_ns": info.st_mtime_ns} if info else None,
                        "action": "restore" if filename in changed else "delete", **proof, "preservation": choice})
    return {"head": head, "branch": source.git(repository, "branch", "--show-current").stdout.strip(),
            "index_sha256": digest(index), "entries": entries}


def material(base, task, plan, private_export=False):
    """只将明确选择 archive 的字节写进档案；export/discard 只保存安全指纹。"""
    admission = project_rules.load_admission(station=base)
    result = {"schema_version": 1, "run_id": task["run_id"], "repositories": {}}
    total = 0
    for name, state in plan["source"].items():
        if state.get("initial_checkout"):
            continue
        repository = source.repository_path(base, name)
        entries = [e for e in plan["entries"] if e.get("repository") == name and e["preservation"]["action"] == "archive"]
        tracked = [e["file"] for e in entries if e["action"] == "restore"]
        patches = {"index_patch": patch(repository, tracked, True), "worktree_patch": patch(repository, tracked)}
        new_files = {}
        for entry in entries:
            path = safe_source(base, name, entry["file"])
            data = path.read_bytes() if path.exists() else b""
            if len(data) > (512 * 1024 * 1024 if private_export else MAX_FILE_BYTES):
                raise ValueError("源码成果超过归档大小上限，请精确导出或确认丢弃")
            # 检查实际内容而非 base64；二进制 patch 也不能掩盖索引中的秘密。
            checked = [data]
            if entry["before_index"]:
                checked.append(git_bytes(repository, "show", ":" + entry["file"]))
            for value in checked:
                if not private_export and project_rules.scan_sensitive(admission, value.decode("utf-8", errors="replace")):
                    raise ValueError("源码成果含敏感内容，不能归档；请安全导出或明确丢弃")
            if entry["action"] == "delete":
                new_files[entry["file"]] = {"data": base64.b64encode(data).decode(), "fingerprint": entry["before"]}
            total += sum(map(len, checked))
        for value in patches.values():
            if not private_export and project_rules.scan_sensitive(admission, value.decode("utf-8", errors="replace")):
                raise ValueError("源码差异含敏感内容，不能归档")
            total += len(value)
        if total > (512 * 1024 * 1024 if private_export else MAX_ARCHIVE_BYTES):
            raise ValueError("源码成果超过归档总量上限，请精确导出或确认丢弃")
        bundle = {"head": state["head"], "branch": state["branch"], "origin": state["origin"],
                  "preserved_ref": state["preserved_ref"], "entries": entries,
                  **{key: base64.b64encode(value).decode() for key, value in patches.items()}, "untracked": new_files}
        if entries:
            verify_reconstruction(repository, bundle)
        result["repositories"][name] = bundle
    return json.dumps(result, ensure_ascii=False, sort_keys=True).encode()


def verify_reconstruction(repository, bundle):
    from workflow.station_resources import fingerprint
    for filename in [e["file"] for e in bundle["entries"]] + list(bundle["untracked"]):
        if not isinstance(filename, str) or filename.startswith("/") or any(p in ("", ".", "..", ".git") for p in filename.split("/")):
            raise ValueError("源码成果包含越界路径")
    with tempfile.TemporaryDirectory(prefix="agenticops-source-check-") as temporary:
        check = Path(temporary).resolve() / "repo"
        git_bytes(repository, "clone", "--shared", "--no-checkout", str(repository), str(check))
        git_bytes(check, "checkout", "--detach", bundle["head"])
        for key, options in (("index_patch", ["--index"]), ("worktree_patch", [])):
            content = base64.b64decode(bundle[key])
            if content:
                git_bytes(check, "apply", "--binary", *options, "-", data=content)
        for filename, entry in bundle["untracked"].items():
            path = check / filename
            if any((check / Path(*Path(filename).parts[:i])).is_symlink() for i in range(1, len(Path(filename).parts))):
                raise ValueError("源码成果路径包含符号链接")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(entry["data"]))
            path.chmod(entry["fingerprint"]["mode"])
        for entry in bundle["entries"]:
            if fingerprint(check / entry["file"]) != entry["before"] or source.git(check, "ls-files", "--stage", "-z", "--", entry["file"]).stdout != entry["before_index"]:
                raise ValueError("源码成果重建内容或索引不一致，拒绝重置")


def verify_coverage(base, task, plan):
    """正式档案不可变；每个实际被移除的成果必须被档案、导出或精确丢弃覆盖。"""
    path = Path(base).resolve() / task["archive_ref"]["path"] / "source-artifacts.json"
    archived = json.loads(path.read_text())
    for entry in plan["entries"]:
        choice = entry["preservation"]
        if choice["action"] == "archive":
            existing = archived["repositories"].get(entry["repository"], {}).get("entries", [])
            if entry not in existing:
                raise ValueError("正式档案未覆盖当前源码成果，需精确导出或丢弃")
        elif choice["action"] == "export":
            from workflow import station_export
            station_export.verify_receipt(base, task, entry)
            target = Path(choice.get("path", ""))
            if not target.is_absolute() or target.is_symlink() or Path(base).resolve() == target or Path(base).resolve() in target.resolve().parents:
                raise ValueError("源码导出必须位于工位外的受控普通文件")
            for parent in target.parents:
                if parent.is_symlink():
                    raise ValueError("导出祖先不能是符号链接")
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 768 * 1024 * 1024:
                raise ValueError("导出必须是当前用户持有、权限 0600 的有限大小普通文件")
            if not choice.get("readback_ref") or digest(target.read_bytes()) != choice.get("sha256"):
                raise ValueError("源码导出回读尚未核验")
            exported = json.loads(target.read_text())
            if entry["path"] != exported.get("path") or exported.get("snapshot") != {k: entry[k] for k in ("head", "before", "before_index", "index_patch", "worktree_patch")}:
                raise ValueError("源码导出未绑定当前成果")
            # 导出文件采用同样的 bundle 格式，验证实际字节而非仅信任声明。
            bundle = exported["bundle"]
            if bundle.get("head") != entry["head"] or len(bundle.get("entries", [])) != 1:
                raise ValueError("导出 bundle 必须是同一 Head 的单份源码成果")
            if any(bundle.get(key) != plan["source"][entry["repository"]][key] for key in ("origin", "preserved_ref")):
                raise ValueError("导出仓库身份或保留引用不匹配")
            if any(digest(base64.b64decode(bundle[key])) != entry[key] for key in ("index_patch", "worktree_patch")):
                raise ValueError("导出差异正文与源码快照不一致")
            if not any(e["path"] == entry["path"] and all(e.get(k) == entry[k] for k in ("head", "before", "before_index", "index_patch", "worktree_patch")) for e in bundle["entries"]):
                raise ValueError("导出 bundle 缺少当前成果")
            verify_reconstruction(source.repository_path(base, entry["repository"]), bundle)
