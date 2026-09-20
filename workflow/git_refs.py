#!/usr/bin/env python3
"""只读采集 Git 远端 heads/tags，并提供按仓库、按查询范围的本地缓存。

本模块不解释分支名称、版本或产品关系。调用方只能把返回值当作带
``as_of`` 的 Git 事实；最终工程基线仍须由工位接管核验并冻结。

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
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


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


def repository_identity(repository, remote, repository_id=None, source_root=None):
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
    if source_root is not None:
        identity["source_root"] = str(Path(source_root).resolve())
    metadata = {"repository_path": str(path), "git_common_dir": str(common_path)}
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), dict(identity, **metadata)


def _empty_cache():
    return {"schema_version": 2, "roots": {}}


def _read_cache(path):
    if not path.is_file():
        return _empty_cache()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GitRefsError("Git refs 缓存损坏：%s" % error) from error
    if not isinstance(document, dict) or document.get("schema_version") != 2 or not isinstance(document.get("roots"), dict):
        raise GitRefsError("Git refs 缓存 schema 不兼容；请使用或重建 schema_version=2 的缓存文件")
    return document


def _cache_root(value):
    if value is None:
        raise GitRefsError("使用文件缓存必须提供 cache_root")
    return str(Path(value).expanduser().resolve())


def _root_repositories(document, cache_root, create=False):
    roots = document["roots"]
    if cache_root not in roots:
        if not create:
            return {}
        roots[cache_root] = {"repositories": {}}
    root = roots[cache_root]
    if not isinstance(root, dict) or not isinstance(root.get("repositories"), dict):
        raise GitRefsError("Git refs 缓存根目录分区无效")
    return root["repositories"]


def _cache_record(repositories, key):
    """只校验本次读取的仓库，不将损坏记录静默替换为空快照。"""
    if key not in repositories:
        return None
    record = repositories[key]
    if (not isinstance(record, dict) or not isinstance(record.get("identity"), dict)
            or not isinstance(record.get("scopes"), dict)):
        raise GitRefsError("Git refs 缓存仓库记录无效")
    for entry in record["scopes"].values():
        if not isinstance(entry, dict) or not isinstance(entry.get("refs"), dict):
            raise GitRefsError("Git refs 缓存查询范围记录无效")
    return record


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


@contextlib.contextmanager
def _cache_write_scope(path):
    """工位缓存先持状态锁，再持缓存锁，禁止与解绑/重建交错写回。

    查询期间保留工位锁，使 purge 不会移除仍有缓存写入者的状态目录。
    非工位的显式缓存路径不受工位生命周期约束。
    """
    original = Path(os.path.abspath(os.path.expanduser(str(path))))
    state = next((parent for parent in original.parents if parent.name == ".agenticops"), None)
    if state is None:
        original = original.resolve()
        state = next((parent for parent in original.parents if parent.name == ".agenticops"), None)
    if state is None:
        yield lambda: None
        return
    from workflow import task_store
    relative = original.relative_to(state)
    station = state.parent.resolve()
    state = station / ".agenticops"
    target = state / relative

    def identity():
        for candidate in (target, *target.parents):
            if candidate == station:
                break
            if candidate.is_symlink():
                raise GitRefsError("工位缓存路径不能是符号链接")
        details = state.stat()
        return (details.st_dev, details.st_ino,
                (state / "station.json").read_bytes(), (state / "init.json").read_bytes())

    try:
        with task_store.task_state_lock(station):
            expected = identity()

            def verify():
                if identity() != expected:
                    raise GitRefsError("工位身份已变化，拒绝旧缓存写回")

            yield verify
    except (OSError, ValueError) as error:
        raise GitRefsError("工位缓存刷新已停止，未恢复旧工位：%s" % error) from error


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


def parse_head_response(output, heads):
    """解析精确查询结果；格式错误不能被解释为分支不存在。"""
    requested = set(heads)
    result = {}
    for line in output.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if (len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", fields[0])
                or not fields[1].startswith("refs/") or any(char.isspace() for char in fields[1])):
            raise GitRefsError("远端引用响应格式无效")
        sha, ref = fields
        if not ref.startswith("refs/heads/") or ref[len("refs/heads/"):] not in requested:
            continue
        head = ref[len("refs/heads/"):]
        if head in result:
            raise GitRefsError("远端分支回读不唯一：" + head)
        result[head] = sha
    return result


def query_heads(origin, heads):
    """一次无缓存查询字面分支名；返回已存在的请求项，不解释产品分支语义。"""
    requested = tuple(heads)
    if not requested or any(not isinstance(head, str) or not head or "\x00" in head for head in requested):
        raise GitRefsError("远端查询需要非空分支名")
    requested = tuple(dict.fromkeys(requested))
    result = _run(["git", "ls-remote", "--heads", origin,
                   *["refs/heads/" + head for head in requested]])
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise GitRefsError(detail[-1] if detail else "Git 远端查询失败")
    return parse_head_response(result.stdout, requested)


def probe(origin, heads):
    """无缓存地精确查询远端 heads，失败绝不把网络问题解释为不存在。"""
    requested = tuple(dict.fromkeys(str(head).strip() for head in heads if str(head).strip()))
    if not requested:
        raise GitRefsError("至少提供一个 --head")
    if any(head.startswith(("refs/", "origin/")) or "\x00" in head for head in requested):
        raise GitRefsError("--head 必须是裸分支名，不接受 refs/ 或 origin/ 前缀")
    refs = query_heads(origin, requested)
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
                  max_age_seconds=300, now=None, repository_id=None, source_root=None,
                  cache_root=None):
    """严格只读地加载缓存；不会联网、加锁、创建目录或写回文件。"""
    if cache_file is None:
        raise GitRefsError("只读缓存必须提供 cache_file")
    root = _cache_root(cache_root)
    requested = tuple(dict.fromkeys(scopes))
    if not requested or not set(requested) <= SCOPES:
        raise GitRefsError("scopes 只支持 heads/tags")
    moment = time.time() if now is None else now
    key, identity = repository_identity(repository, remote, repository_id, source_root)
    document = _read_cache(Path(cache_file).resolve())
    record = _cache_record(_root_repositories(document, root), key)
    if record is None or record.get("identity") != identity:
        record = {"identity": identity, "scopes": {}}
    return _cached_result(record, requested, moment, max_age_seconds)


def snapshot(repository, remote="origin", scopes=("heads",), cache_file=None,
             refresh="auto", max_age_seconds=300, now=None, repository_id=None, source_root=None,
             cache_root=None):
    """返回单仓库 raw refs 快照；缓存只加速远端查询，不解释业务含义。"""
    if refresh not in ("auto", "always"):
        raise GitRefsError("refresh 必须是 auto/always")
    requested = tuple(dict.fromkeys(scopes))
    if not requested or not set(requested) <= SCOPES:
        raise GitRefsError("scopes 只支持 heads/tags")
    if not isinstance(max_age_seconds, int) or max_age_seconds < 0:
        raise GitRefsError("max_age_seconds 必须是非负整数")
    moment = time.time() if now is None else now
    key, identity = repository_identity(repository, remote, repository_id, source_root)
    path = Path(repository).resolve()
    cache_path = Path(cache_file).resolve() if cache_file else None
    root = _cache_root(cache_root) if cache_path else None

    def collect(document):
        repositories = _root_repositories(document, root, create=True)
        record = _cache_record(repositories, key)
        if record is None or record.get("identity") != identity:
            record = {"identity": identity, "scopes": {}, "last_attempt": None}
            repositories[key] = record
        result = {"identity": identity, "scopes": {}, "network_used": False}
        for scope in requested:
            previous = record["scopes"].get(scope, {})
            should_refresh = refresh == "always" or (refresh == "auto" and not _fresh(previous, moment, max_age_seconds))
            if should_refresh:
                # 此标志表示已尝试远端查询，不等价于查询成功。
                result["network_used"] = True
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
            else:
                freshness = "cached" if _fresh(previous, moment, max_age_seconds) else "stale"
                result["scopes"][scope] = {
                    "refs": previous.get("refs", {}), "freshness": freshness,
                    "last_success_at": previous.get("last_success_at"), "coverage": scope,
                    "snapshot_digest": previous.get("snapshot_digest"),
                }
        return result

    if cache_path is None:
        document = _empty_cache()
        root = "__memory__"
        return collect(document)
    # TTL 内的自动命中是纯读操作：不创建锁、不改目录、不重写缓存。
    if refresh == "auto":
        document = _read_cache(cache_path)
        existing = _cache_record(_root_repositories(document, root), key)
        if existing is not None and existing.get("identity") == identity and all(
            _fresh(existing.get("scopes", {}).get(scope, {}), moment, max_age_seconds)
            for scope in requested
        ):
            return _cached_result(existing, requested, moment, max_age_seconds)
    with _cache_write_scope(cache_file) as verify_station:
        with _cache_lock(cache_path):
            document = _read_cache(cache_path)
            result = collect(document)
            verify_station()
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
    snapshot_parser.add_argument("--cache-root", required=True, help="当前缓存分区的规范化根目录")
    snapshot_parser.add_argument("--refresh", action="store_true", help="强制查询远端并更新缓存；默认按 TTL 自动刷新")
    snapshot_parser.add_argument("--repository-id", required=True, help="<owner>/<repo>，作为缓存仓库映射")
    snapshot_parser.add_argument("--source-root", help="绑定当前缓存的 source 工程目录 根目录")
    snapshot_parser.add_argument("--max-age", type=int, default=300)
    probe_parser = sub.add_parser("probe", help="无缓存精确查询远端指定 head")
    probe_parser.add_argument("--origin", required=True)
    probe_parser.add_argument("--head", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = snapshot(args.repository, args.remote, args.scope or ("heads",), args.cache_file,
                              "always" if args.refresh else "auto", args.max_age,
                              repository_id=args.repository_id, source_root=args.source_root,
                              cache_root=args.cache_root)
        else:
            result = probe(args.origin, args.head)
    except GitRefsError as error:
        print("错误：%s" % error, file=os.sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
