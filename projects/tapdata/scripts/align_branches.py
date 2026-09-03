#!/usr/bin/env python3
"""不改工作树地解析 TapData 模块根目录中的多仓分支关系。

``--version`` 始终表示 ``tapdata/tapdata`` 的分支名。脚本会刷新 TapData 模块根目录
中每个仓库的 ``origin/*`` 远端跟踪引用，再使用这些引用解析关系；它绝不 checkout、
切换、合并或修改任何工作树。

用法：
  python3 projects/tapdata/scripts/align_branches.py show --version release-v4.21.0
  python3 projects/tapdata/scripts/align_branches.py show --tapdata-root <tapdata-root> --version fix-xxx
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


RELEASE_RE = re.compile(r"^release-v\d+(?:\.\d+){2,}$")
AUTO_REFRESH_MAX_AGE_SECONDS = 300
FETCH_TIMEOUT_SECONDS = 30
FETCH_PROGRESS_INTERVAL_SECONDS = 10


class AlignmentError(ValueError):
    pass


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AlignmentError("无法读取 %s：%s" % (path, error)) from error


def project_paths(product_root):
    base = (Path(product_root).resolve() / "projects" / "tapdata").resolve()
    config = base / "version-branch-alignments.json"
    if not config.is_file():
        raise AlignmentError("缺少 TapData 分支对齐配置：%s" % config)
    return base, config


def load_configuration(product_root):
    base, config_path = project_paths(product_root)
    config = read_json(config_path)
    if config.get("schema_version") != 1:
        raise AlignmentError("version-branch-alignments.json schema_version 必须为 1")
    catalog_ref = config.get("repository_catalog")
    if not isinstance(catalog_ref, str) or not catalog_ref:
        raise AlignmentError("分支对齐配置缺少 repository_catalog")
    catalog_path = (base / catalog_ref).resolve()
    try:
        catalog_path.relative_to(base)
    except ValueError as error:
        raise AlignmentError("repository_catalog 路径越界：%s" % catalog_ref) from error
    catalog = read_json(catalog_path)
    repositories = catalog.get("repositories")
    if catalog.get("schema_version") != 1 or not isinstance(repositories, dict):
        raise AlignmentError("仓库目录结构无效：%s" % catalog_path)
    validate_configuration(config, repositories)
    return config, repositories


def validate_configuration(config, repositories):
    versions = config.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise AlignmentError("分支对齐配置缺少 versions")
    known = set(repositories)
    for key, profile in versions.items():
        branches = profile.get("branches") if isinstance(profile, dict) else None
        if not isinstance(key, str) or not key or not isinstance(branches, dict):
            raise AlignmentError("版本矩阵条目无效：%s" % key)
        if set(branches) != known:
            raise AlignmentError("版本矩阵 %s 必须覆盖且只覆盖仓库目录中的全部仓库" % key)
    rules = config.get("derivation")
    if not isinstance(rules, dict):
        raise AlignmentError("分支对齐配置缺少 derivation")
    listed = set()
    for name in ("linked_repositories", "keep_current_repositories", "independent_repositories"):
        values = rules.get(name)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise AlignmentError("derivation.%s 必须是仓库数组" % name)
        listed.update(values)
    fixed_branches = rules.get("fixed_branches")
    if not isinstance(fixed_branches, dict) or not fixed_branches or not all(
        repository in known and isinstance(branch, str) and branch
        for repository, branch in fixed_branches.items()
    ):
        raise AlignmentError("derivation.fixed_branches 必须是已登记仓库到非空分支的映射")
    listed.update(fixed_branches)
    if listed != known:
        raise AlignmentError("derivation 仓库分类必须覆盖且只覆盖仓库目录")
    if any(rules.get(name) not in known for name in ("product_repository", "license_repository")):
        raise AlignmentError("derivation 的主仓或 license 仓库未在目录登记")
    linked = set(rules["linked_repositories"])
    for name in ("same_name_repositories", "plugin_release_repositories"):
        values = rules.get(name)
        if not isinstance(values, list) or not set(values) <= linked:
            raise AlignmentError("derivation.%s 必须是 linked_repositories 的子集" % name)
    fallbacks = rules.get("display_fallback_branches", {})
    if not isinstance(fallbacks, dict) or not all(
        repository in known and isinstance(branch, str) and branch
        for repository, branch in fallbacks.items()
    ):
        raise AlignmentError("derivation.display_fallback_branches 必须是已登记仓库到非空分支的映射")


def command(arguments, cwd=None):
    return subprocess.run(arguments, cwd=cwd, capture_output=True, text=True, timeout=30)


def git_output(arguments, cwd):
    try:
        result = command(arguments, cwd=cwd)
    except subprocess.TimeoutExpired as error:
        raise AlignmentError("Git 命令超时：%s" % " ".join(arguments[:3])) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise AlignmentError(detail[-1] if detail else "Git 命令失败：%s" % " ".join(arguments[:3]))
    return result.stdout.strip()


def progress(message):
    print("[tapdata-align] %s" % message, file=sys.stderr, flush=True)


def iso_timestamp(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch))


def fetch_head_metadata(path):
    """返回本地 FETCH_HEAD 的时间；缺失时绝不猜测远端刷新时间。"""
    try:
        fetch_head = Path(git_output(["git", "rev-parse", "--git-path", "FETCH_HEAD"], path))
        if not fetch_head.is_absolute():
            fetch_head = Path(path) / fetch_head
        modified = fetch_head.stat().st_mtime
    except (AlignmentError, OSError):
        return {"last_refresh_at": None, "last_refresh_epoch": None, "evidence": "FETCH_HEAD_missing_or_unreadable"}
    return {"last_refresh_at": iso_timestamp(modified), "last_refresh_epoch": modified, "evidence": "FETCH_HEAD_mtime"}


def refresh_required(mode, metadata, now):
    if mode == "always":
        return True
    if mode == "never":
        return False
    refreshed = metadata["last_refresh_epoch"]
    return refreshed is None or now - refreshed > AUTO_REFRESH_MAX_AGE_SECONDS


def fetch_origin(path, repository):
    arguments = ["git", "fetch", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*"]
    try:
        process = subprocess.Popen(arguments, cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as error:
        raise AlignmentError("无法启动 Git fetch：%s" % error) from error
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        remaining = FETCH_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            process.kill()
            _, stderr = process.communicate()
            detail = stderr.strip().splitlines()
            suffix = "：%s" % detail[-1] if detail else ""
            raise AlignmentError("Git fetch 超时（%ss）：%s%s" % (FETCH_TIMEOUT_SECONDS, repository, suffix))
        try:
            stdout, stderr = process.communicate(timeout=min(FETCH_PROGRESS_INTERVAL_SECONDS, remaining))
            break
        except subprocess.TimeoutExpired:
            progress("远端刷新仍在进行：%s（已 %.0fs）" % (repository, time.monotonic() - started))
    if process.returncode != 0:
        detail = (stderr or stdout).strip().splitlines()
        raise AlignmentError(detail[-1] if detail else "Git fetch 失败：%s" % repository)
    return time.monotonic() - started


def module_repository(tapdata_root, repository):
    owner, name = repository.split("/", 1)
    if owner != "tapdata":
        raise AlignmentError("TapData 仓库目录必须使用 tapdata owner：%s" % repository)
    return Path(tapdata_root).resolve() / name


def workspace_tapdata_root(start):
    """返回离执行路径最近的工作空间绑定所对应的 TapData 模块根目录。"""
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        binding = directory / ".agenticops" / "workspace.json"
        if not binding.is_file():
            continue
        document = read_json(binding)
        pool = document.get("repository_pool", {})
        root = pool.get("root") if isinstance(pool, dict) else None
        if not isinstance(root, str) or not root:
            raise AlignmentError("工作空间配置缺少 repository_pool.root：%s" % binding)
        return Path(root).expanduser().resolve() / "tapdata", binding
    return None, None


def resolve_tapdata_root(explicit, execution_directory):
    if explicit:
        return Path(explicit).expanduser().resolve(), "explicit"
    bound_root, binding = workspace_tapdata_root(execution_directory)
    if bound_root is not None:
        return bound_root, "workspace:%s" % binding
    return Path(execution_directory).resolve(), "cwd"


def refresh_branch_cache(tapdata_root, repositories, product_repository, refresh_mode):
    """按刷新策略更新 ``origin/*``，并保留每仓库的本地 refs 新鲜度事实。"""
    product_path = module_repository(tapdata_root, product_repository)
    if not (product_path / ".git").exists():
        raise AlignmentError("TapData 模块根目录缺少主仓：%s（期望 %s）" % (product_repository, product_path))
    paths = {}
    names = sorted(repositories)
    for index, repository in enumerate(names, start=1):
        progress("检查本地仓库 %s/%s：%s" % (index, len(names), repository))
        path = module_repository(tapdata_root, repository)
        if not path.is_dir() or not (path / ".git").exists():
            raise AlignmentError("TapData 模块根目录缺少仓库：%s（期望 %s）" % (repository, path))
        top = git_output(["git", "rev-parse", "--show-toplevel"], path)
        if Path(top).resolve() != path.resolve():
            raise AlignmentError("TapData 模块根目录中的仓库不是 Git 根目录：%s" % path)
        paths[repository] = path
    refresh_started = time.monotonic()
    states = {}
    for index, repository in enumerate(names, start=1):
        path = paths[repository]
        metadata = fetch_head_metadata(path)
        fetched = refresh_required(refresh_mode, metadata, time.time())
        duration = 0.0
        if fetched:
            progress("刷新远端引用 %s/%s：%s" % (index, len(names), repository))
            duration = fetch_origin(path, repository)
            metadata = fetch_head_metadata(path)
            freshness = "refreshed_during_run"
        else:
            freshness = "cached_local_refs"
            progress("复用本地引用 %s/%s：%s（最后刷新：%s）" % (index, len(names), repository, metadata["last_refresh_at"] or "未知"))
        states[repository] = {
            "freshness": freshness,
            "last_refresh_at": metadata["last_refresh_at"],
            "last_refresh_evidence": metadata["evidence"],
            "fetch_duration_seconds": round(duration, 3),
        }
    return paths, states, time.monotonic() - refresh_started


def local_remote_refs(path):
    output = git_output(["git", "for-each-ref", "--format=%(refname:strip=3) %(objectname)", "refs/remotes/origin"], path)
    return {
        branch: sha
        for line in output.splitlines()
        for branch, _, sha in [line.partition(" ")]
        if branch and sha and branch != "HEAD"
    }


def version_key(branch):
    values = [int(value) for value in re.findall(r"\d+", branch)][:4]
    return tuple((values + [0, 0, 0, 0])[:4])


def first_release_ge(refs, minimum):
    for branch in sorted((item for item in refs if RELEASE_RE.fullmatch(item)), key=version_key):
        if version_key(branch) >= version_key(minimum):
            return branch
    return None


def plugin_release(product_path, branch, rules):
    plugin = rules.get("plugin_version_source", {})
    path, key = plugin.get("path"), plugin.get("key")
    if not isinstance(path, str) or not isinstance(key, str):
        return None, "PluginKit 配置不完整"
    try:
        content = git_output(["git", "show", "origin/%s:%s" % (branch, path)], product_path)
    except AlignmentError as error:
        return None, "无法从 tapdata 的 origin/%s 读取 PluginKit：%s" % (branch, error)
    for line in content.splitlines():
        if line.strip().startswith(key + "="):
            version = line.split("=", 1)[1].strip().removesuffix("-SNAPSHOT")
            return "release-v" + version, "tapdata 的 PluginKit %s" % version
    return None, "%s 未包含 %s" % (path, key)


def derived_target(repository, version, rules, refs, product_path, plugin_cache):
    if repository == rules["product_repository"]:
        return version, "product_branch", "tapdata 输入分支"
    fixed_branches = rules.get("fixed_branches", {})
    if repository in fixed_branches:
        return fixed_branches[repository], "fixed", "%s 固定使用 %s" % (repository, fixed_branches[repository])
    fallbacks = rules["display_fallback_branches"]
    if repository == "tapdata/tapdata-application":
        return fallbacks[repository], "fixed", "tapdata-application 固定使用 main"
    if repository == "tapdata/t-layer3-test":
        if version in refs[repository]:
            return version, "same_name", "远端分支完全同名匹配"
        return fallbacks[repository], "fallback", "未找到同名分支，使用 develop"
    if repository in rules["keep_current_repositories"] or repository in rules["independent_repositories"]:
        return None, "unchanged", "该仓库不参与 TapData 分支关系推导"
    if version == "main":
        return "main", "main_rule", "main 的 linked 仓统一使用 main"
    if version == "develop":
        return ("main", "develop_rule", "develop 时 license 固定使用 main") if repository == rules["license_repository"] else ("develop", "develop_rule", "develop 的 linked 仓使用 develop")
    if repository in rules["same_name_repositories"]:
        if version in refs[repository]:
            return version, "same_name", "远端分支完全同名匹配"
        return None, "unresolved", "未找到完全同名分支"
    if repository in rules["plugin_release_repositories"]:
        if "plugin_release" not in plugin_cache:
            plugin_cache["plugin_release"] = plugin_release(product_path, version, rules)
        minimum, evidence = plugin_cache["plugin_release"]
        if not minimum:
            return None, "unresolved", evidence
        target = first_release_ge(refs[repository], minimum)
        if target:
            return target, "plugin_release", "%s，取首个不低于 %s 的分支" % (evidence, minimum)
        return None, "unresolved", "未找到不低于 %s 的 release 分支" % minimum
    if repository == rules["license_repository"]:
        if RELEASE_RE.fullmatch(version):
            target = first_release_ge(refs[repository], version)
            if target:
                return target, "license_release", "取首个不低于主仓版本的 license release"
        return "main", "license_fallback", "license 无对应 release，回退 main"
    return None, "unresolved", "没有该仓库的分支推导规则"


def build_plan(version, config, repositories, paths, ref_states=None):
    rules = config["derivation"]
    refs = {repository: local_remote_refs(path) for repository, path in paths.items()}
    product_repository = rules["product_repository"]
    profile = config["versions"].get(version)
    product_branch = profile["branches"][product_repository] if profile else version
    if product_branch not in refs[product_repository]:
        raise AlignmentError("tapdata 的 Source Pool 中不存在指定远端分支：%s" % product_branch)
    rows, cache = [], {}
    for repository in sorted(repositories):
        if profile:
            target, resolution, reason = profile["branches"][repository], "exact_profile", "已确认版本矩阵 %s" % version
        else:
            target, resolution, reason = derived_target(repository, version, rules, refs, paths[product_repository], cache)
        row = {"repository": repository, "domains": repositories[repository].get("domains", []), "target_branch": target, "resolution": resolution, "reason": reason}
        if ref_states is not None:
            row["refs"] = ref_states[repository]
        if target is None:
            row.update({"target_status": "unresolved" if resolution == "unresolved" else "unchanged", "target_sha": None})
        else:
            sha = refs[repository].get(target)
            row.update({"target_status": "exists" if sha else "missing", "target_sha": sha})
        rows.append(row)
    return rows


def print_table(rows):
    print("repository\ttarget_branch\ttarget_sha\tresolution\ttarget_status\trefs_freshness\trefs_last_refresh_at\treason")
    for row in rows:
        refs = row.get("refs", {})
        print("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (row["repository"], row["target_branch"] or "-", row["target_sha"] or "-", row["resolution"], row["target_status"], refs.get("freshness", "-"), refs.get("last_refresh_at") or "unknown", row["reason"]))


def normalize_argv(argv):
    """保留技能短命令 ``tapdata-align-branches <version>`` 的语义。"""
    if argv and argv[0] not in ("show", "-h", "--help") and not argv[0].startswith("-"):
        return ["show", "--version", argv[0], *argv[1:]]
    return argv


def main(argv=None, execution_directory=None):
    argv = normalize_argv(list(sys.argv[1:] if argv is None else argv))
    parser = argparse.ArgumentParser(description="只读解析 TapData 模块根目录的多仓分支关系")
    parser.add_argument("--product-root", default=Path(__file__).resolve().parents[3])
    sub = parser.add_subparsers(dest="command", required=True)
    show = sub.add_parser("show", help="按刷新策略解析 origin ref 并显示分支关系")
    show.add_argument("--version", required=True, help="tapdata/tapdata 的目标分支名")
    show.add_argument("--tapdata-root", help="包含全部 TapData 模块仓库的根目录；默认由最近工作空间的 Source Pool 解析为 <pool>/tapdata，其次当前执行目录")
    show.add_argument("--refresh", choices=("always", "auto", "never"), default="auto", help="always 每次刷新；auto 在本地 refs 缺失或超过 5 分钟时刷新；never 只解析现有 origin/*")
    show.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    try:
        total_started = time.monotonic()
        config, repositories = load_configuration(args.product_root)
        tapdata_root, source = resolve_tapdata_root(args.tapdata_root, execution_directory or Path.cwd())
        progress("开始解析：refresh=%s，TapData 根目录=%s" % (args.refresh, tapdata_root))
        paths, ref_states, fetch_seconds = refresh_branch_cache(tapdata_root, repositories, config["derivation"]["product_repository"], args.refresh)
        progress("开始本地 origin/* 解析")
        resolution_started = time.monotonic()
        rows = build_plan(args.version, config, repositories, paths, ref_states)
        resolution_seconds = time.monotonic() - resolution_started
        total_seconds = time.monotonic() - total_started
        document = {
            "tapdata_branch": args.version,
            "tapdata_root": str(tapdata_root),
            "tapdata_root_resolution": source,
            "refresh": {"mode": args.refresh, "auto_max_age_seconds": AUTO_REFRESH_MAX_AGE_SECONDS},
            "timing_seconds": {"fetch": round(fetch_seconds, 3), "local_resolution": round(resolution_seconds, 3), "total": round(total_seconds, 3)},
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "rows": rows,
        }
        progress("解析完成：fetch=%.3fs，本地解析=%.3fs，总计=%.3fs" % (fetch_seconds, resolution_seconds, total_seconds))
        if args.json:
            print(json.dumps(document, ensure_ascii=False))
        else:
            print("tapdata_root\t%s" % tapdata_root)
            print("tapdata_root_resolution\t%s" % source)
            print("refresh_mode\t%s" % args.refresh)
            print("timing_seconds\tfetch=%.3f\tlocal_resolution=%.3f\ttotal=%.3f" % (fetch_seconds, resolution_seconds, total_seconds))
            print_table(rows)
        return 0
    except AlignmentError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
