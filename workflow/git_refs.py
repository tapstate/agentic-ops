#!/usr/bin/env python3
"""只读采集 Git 远端 heads/tags，并提供按仓库、按查询范围的本地缓存。

本模块不解释分支名称、版本或产品关系。调用方只能把返回值当作带
``as_of`` 的 Git 事实；最终 worktree 基线仍须由 repository prepare 冻结。

外部调用分两类：

* ``snapshot``：带 ``--cache-file`` 的缓存读取/刷新，适合分支分析；
* ``probe``：不读取或写入缓存，直接查询当前远端的指定 heads，适合需要
  当前精确事实的操作前核验。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


SCOPES = {"heads", "tags"}
TIMEOUT_SECONDS = 30


class GitRefsError(ValueError):
    pass


def _run(arguments, cwd=None):
    try:
        return subprocess.run(arguments, cwd=cwd, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise GitRefsError("Git 远端查询超时") from error
    except OSError as error:
        raise GitRefsError("无法启动 Git：%s" % error) from error


def _output(arguments, cwd):
    result = _run(arguments, cwd=cwd)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise GitRefsError(detail[-1] if detail else "Git 命令失败")
    return result.stdout.strip()


def normalize_origin(value):
    """缓存中不保留远端 URL 里的用户名或凭据。"""
    text = str(value).strip()
    if "://" in text:
        scheme, rest = text.split("://", 1)
        rest = rest.split("@", 1)[-1]
        return scheme.lower() + "://" + rest.rstrip("/")
    if "@" in text and ":" in text:
        text = text.split("@", 1)[1]
    return text.rstrip("/")


def repository_identity(repository, remote, repository_id=None, source_pool_root=None):
    path = Path(repository).resolve()
    top = Path(_output(["git", "rev-parse", "--show-toplevel"], path)).resolve()
    if top != path:
        raise GitRefsError("repository 必须是 Git 根目录：%s" % path)
    common = _output(["git", "rev-parse", "--git-common-dir"], path)
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = (path / common_path).resolve()
    origin = _output(["git", "remote", "get-url", remote], path)
    if repository_id is not None and not re.fullmatch(r"[^/\\\s]+/[^/\\\s]+", str(repository_id)):
        raise GitRefsError("repository_id 必须为 <owner>/<repo>")
    identity = {
        "repository_id": str(repository_id) if repository_id else str(path),
        "remote": remote,
        "origin": normalize_origin(origin),
    }
    if source_pool_root is not None:
        identity["source_pool_root"] = str(Path(source_pool_root).resolve())
    metadata = {"repository_path": str(path), "git_common_dir": str(common_path)}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), dict(identity, **metadata)


def _read_cache(path):
    if not path.is_file():
        return {"schema_version": 1, "repositories": {}}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitRefsError("Git refs 缓存损坏：%s" % error) from error
    if document.get("schema_version") != 1 or not isinstance(document.get("repositories"), dict):
        raise GitRefsError("Git refs 缓存 schema 无效")
    return document


def _write_cache(path, document):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent))
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise GitRefsError("无法写入 Git refs 缓存：%s" % error) from error
    finally:
        if temporary and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


@contextlib.contextmanager
def _cache_lock(path):
    """同一缓存文件的刷新严格串行；锁由 OS 在进程结束时释放。"""
    import fcntl
    try:
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as error:
        raise GitRefsError("无法锁定 Git refs 缓存：%s" % error) from error


def _parse_heads(output):
    result = {}
    for line in output.splitlines():
        sha, _, ref = line.partition("\t")
        if re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha) and ref.startswith("refs/heads/"):
            result[ref[len("refs/heads/"):]] = sha
    return result


def _parse_tags(output):
    result = {}
    for line in output.splitlines():
        sha, _, ref = line.partition("\t")
        if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha) or not ref.startswith("refs/tags/"):
            continue
        name = ref[len("refs/tags/"):]
        if name.endswith("^{}"):
            result.setdefault(name[:-3], {})["peeled"] = sha
        else:
            result.setdefault(name, {})["object"] = sha
    return result


def _query(path, remote, scope):
    arguments = ["git", "ls-remote", "--heads" if scope == "heads" else "--tags", remote]
    result = _run(arguments, cwd=path)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise GitRefsError(detail[-1] if detail else "Git 远端查询失败")
    return _parse_heads(result.stdout) if scope == "heads" else _parse_tags(result.stdout)


def probe(origin, heads):
    """无缓存地精确查询远端 heads，失败绝不把网络问题解释为不存在。"""
    requested = tuple(dict.fromkeys(str(head).strip() for head in heads if str(head).strip()))
    if not requested:
        raise GitRefsError("至少提供一个 --head")
    if any(head.startswith(("refs/", "origin/")) or "\x00" in head for head in requested):
        raise GitRefsError("--head 必须是裸分支名，不接受 refs/ 或 origin/ 前缀")
    result = _run(["git", "ls-remote", "--heads", origin,
                   *["refs/heads/" + head for head in requested]])
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise GitRefsError(detail[-1] if detail else "Git 远端查询失败")
    refs = _parse_heads(result.stdout)
    return {
        "origin": normalize_origin(origin),
        "heads": {head: refs.get(head) for head in requested},
        "verification": "verified",
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
    }


def _fresh(entry, now, max_age):
    stamp = entry.get("last_success_epoch") if isinstance(entry, dict) else None
    return isinstance(stamp, (int, float)) and 0 <= now - stamp <= max_age


def _cached_result(record, requested, moment, max_age_seconds):
    result = {"identity": record.get("identity"), "scopes": {}, "network_used": False}
    for scope in requested:
        previous = record.get("scopes", {}).get(scope, {})
        freshness = "cached" if _fresh(previous, moment, max_age_seconds) else "stale"
        result["scopes"][scope] = {
            "refs": previous.get("refs", {}), "freshness": freshness,
            "last_success_at": previous.get("last_success_at"), "coverage": scope,
            "snapshot_digest": previous.get("snapshot_digest"),
        }
    return result


def read_snapshot(repository, remote="origin", scopes=("heads",), cache_file=None,
                  max_age_seconds=300, now=None, repository_id=None, source_pool_root=None):
    """严格只读地加载缓存；不会联网、加锁、创建目录或写回文件。"""
    if cache_file is None:
        raise GitRefsError("只读缓存必须提供 cache_file")
    requested = tuple(dict.fromkeys(scopes))
    if not requested or not set(requested) <= SCOPES:
        raise GitRefsError("scopes 只支持 heads/tags")
    moment = time.time() if now is None else now
    key, identity = repository_identity(repository, remote, repository_id, source_pool_root)
    document = _read_cache(Path(cache_file).resolve())
    record = document["repositories"].get(key)
    if record is None or record.get("identity") != identity:
        record = {"identity": identity, "scopes": {}}
    return _cached_result(record, requested, moment, max_age_seconds)


def snapshot(repository, remote="origin", scopes=("heads",), cache_file=None,
             refresh="auto", max_age_seconds=300, now=None, repository_id=None, source_pool_root=None):
    """返回单仓库 raw refs 快照；缓存只加速远端查询，不解释业务含义。"""
    if refresh not in ("auto", "always"):
        raise GitRefsError("refresh 必须是 auto/always")
    requested = tuple(dict.fromkeys(scopes))
    if not requested or not set(requested) <= SCOPES:
        raise GitRefsError("scopes 只支持 heads/tags")
    if not isinstance(max_age_seconds, int) or max_age_seconds < 0:
        raise GitRefsError("max_age_seconds 必须是非负整数")
    moment = time.time() if now is None else now
    key, identity = repository_identity(repository, remote, repository_id, source_pool_root)
    path = Path(repository).resolve()
    cache_path = Path(cache_file).resolve() if cache_file else None

    def collect(document):
        record = document["repositories"].setdefault(key, {"identity": identity, "scopes": {}, "last_attempt": None})
        if record.get("identity") != identity:
            record = {"identity": identity, "scopes": {}, "last_attempt": None}
            document["repositories"][key] = record
        result = {"identity": identity, "scopes": {}, "network_used": False}
        for scope in requested:
            previous = record["scopes"].get(scope, {})
            should_refresh = refresh == "always" or (refresh == "auto" and not _fresh(previous, moment, max_age_seconds))
            if should_refresh:
                try:
                    refs = _query(path, remote, scope)
                except GitRefsError as error:
                    record["last_attempt"] = {"at": moment, "scope": scope, "error": str(error)}
                    result["scopes"][scope] = {
                        "refs": previous.get("refs", {}), "freshness": "refresh_failed",
                        "last_success_at": previous.get("last_success_at"), "coverage": scope,
                        "error": str(error),
                    }
                    continue
                entry = {"refs": refs, "last_success_epoch": moment,
                         "last_success_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(moment)),
                         "coverage": scope, "snapshot_digest": hashlib.sha256(
                             json.dumps(refs, sort_keys=True).encode("utf-8")).hexdigest()}
                record["scopes"][scope] = entry
                result["scopes"][scope] = dict(entry, freshness="refreshed")
                result["network_used"] = True
            else:
                freshness = "cached" if _fresh(previous, moment, max_age_seconds) else "stale"
                result["scopes"][scope] = {
                    "refs": previous.get("refs", {}), "freshness": freshness,
                    "last_success_at": previous.get("last_success_at"), "coverage": scope,
                    "snapshot_digest": previous.get("snapshot_digest"),
                }
        return result

    if cache_path is None:
        document = {"schema_version": 1, "repositories": {}}
        return collect(document)
    # TTL 内的自动命中是纯读操作：不创建锁、不改目录、不重写缓存。
    if refresh == "auto":
        document = _read_cache(cache_path)
        existing = document["repositories"].get(key)
        if existing is not None and existing.get("identity") == identity and all(
            _fresh(existing.get("scopes", {}).get(scope, {}), moment, max_age_seconds)
            for scope in requested
        ):
            return _cached_result(existing, requested, moment, max_age_seconds)
    with _cache_lock(cache_path):
        document = _read_cache(cache_path)
        result = collect(document)
        _write_cache(cache_path, document)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读查询 Git remote heads/tags，并按仓库缓存")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot_parser = sub.add_parser("snapshot", help="带缓存读取或刷新本地 Git 仓库的 heads/tags")
    snapshot_parser.add_argument("--repository", required=True)
    snapshot_parser.add_argument("--remote", default="origin")
    snapshot_parser.add_argument("--scope", action="append", choices=sorted(SCOPES), default=[])
    snapshot_parser.add_argument("--cache-file", required=True)
    snapshot_parser.add_argument("--refresh", action="store_true", help="强制查询远端并更新缓存；默认按 TTL 自动刷新")
    snapshot_parser.add_argument("--repository-id", required=True, help="<owner>/<repo>，作为缓存仓库映射")
    snapshot_parser.add_argument("--source-pool", help="绑定当前缓存的 Source Pool 根目录")
    snapshot_parser.add_argument("--max-age", type=int, default=300)
    probe_parser = sub.add_parser("probe", help="无缓存精确查询远端指定 head")
    probe_parser.add_argument("--origin", required=True)
    probe_parser.add_argument("--head", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot(args.repository, args.remote, args.scope or ("heads",), args.cache_file,
                              "always" if args.refresh else "auto", args.max_age,
                              repository_id=args.repository_id, source_pool_root=args.source_pool)
        else:
            result = probe(args.origin, args.head)
    except GitRefsError as error:
        print("错误：%s" % error, file=os.sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
