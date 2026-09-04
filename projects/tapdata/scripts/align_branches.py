#!/usr/bin/env python3
"""不改工作树地解析 TapData 模块根目录中的多仓分支关系。

``--version`` 始终表示 ``tapdata/tapdata`` 的分支名。脚本会刷新 TapData 模块根目录
中每个仓库的 raw remote refs 缓存，再使用这些引用解析关系；它绝不 checkout、切换、
合并或修改任何工作树。默认优先读单仓库缓存；显式 ``--refresh`` 才强制逐仓查询远端。
需要操作前的无缓存精确远端事实，请直接使用
``workflow/git_refs.py probe``，不要把本脚本的缓存结果当作最终基线。

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

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from workflow import git_refs  # noqa: E402

RELEASE_RE = re.compile(r"^release-v\d+(?:\.\d+){2,}$")
AUTO_REFRESH_MAX_AGE_SECONDS = 300
FETCH_TIMEOUT_SECONDS = 30
FETCH_PROGRESS_INTERVAL_SECONDS = 10


class AlignmentError(ValueError):
    pass


def classify_fetch_error(detail):
    """将 Git 的不稳定错误文本收敛为可消费类别，不把认证材料回显到报告。"""
    normalized = detail.lower()
    if "git fetch 超时" in normalized or "timed out" in normalized or "connection timed out" in normalized:
        return "fetch_timeout"
    if any(token in normalized for token in ("permission denied (publickey)", "authentication failed", "authentication required", "no supported authentication methods")):
        return "ssh_auth_failed"
    if any(token in normalized for token in ("repository not found", "access denied", "not authorized", "http 403", "error 403")):
        return "repository_access_denied"
    if any(token in normalized for token in ("could not resolve host", "network is unreachable", "connection refused", "no route to host", "failed to connect")):
        return "network_unreachable"
    return "remote_refresh_failed"


def fetch_prompt(repository, error_kind):
    prompts = {
        "fetch_timeout": "远端刷新超时；请检查网络、VPN 或 Git 服务状态后重试。",
        "ssh_auth_failed": "SSH 身份认证失败；请确认当前 ssh-agent 已加载可读取该仓库的密钥。",
        "repository_access_denied": "当前身份可能没有该仓库的读取权限；请确认仓库访问授权与 origin 地址。",
        "network_unreachable": "无法连接 Git 远端；请检查网络、VPN、DNS 或代理配置。",
        "remote_refresh_failed": "远端刷新失败；请检查当前 Git 身份、仓库访问权限和网络连接。",
    }
    return "%s：%s" % (repository, prompts[error_kind])


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
    feature = rules.get("feature_branch")
    if not isinstance(feature, dict):
        raise AlignmentError("derivation.feature_branch 必须是对象")
    feature_listed = set()
    for name in ("same_name_repositories", "plugin_release_repositories", "tag_fallback_repositories", "unchanged_repositories"):
        values = feature.get(name)
        if not isinstance(values, list) or not all(isinstance(item, str) and item in known for item in values):
            raise AlignmentError("derivation.feature_branch.%s 必须是已登记仓库数组" % name)
        feature_listed.update(values)
    if feature_listed != known - {rules["product_repository"]}:
        raise AlignmentError("derivation.feature_branch 必须覆盖且只覆盖除主仓外的全部仓库")
    if not set(feature["plugin_release_repositories"]) <= set(feature["same_name_repositories"]):
        raise AlignmentError("feature_branch.plugin_release_repositories 必须先参与同名匹配")
    if set(feature["tag_fallback_repositories"]) & set(feature["plugin_release_repositories"]):
        raise AlignmentError("PluginKit 与 Tag 回退仓库不能重叠")
    if feature.get("plugin_release_policy") != "exact" or feature.get("tag_selection") != "first_parent_nearest":
        raise AlignmentError("feature_branch 只支持 exact PluginKit 与 first_parent_nearest Tag 选择")
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
        detail = (stderr or stdout).strip()
        # 完整 stderr 仅用于本地分类；报告层只输出脱敏后的类别和处理提示。
        raise AlignmentError(detail if detail else "Git fetch 失败：%s" % repository)
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


def workspace_binding(start):
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        binding = directory / ".agenticops" / "workspace.json"
        if binding.is_file():
            return directory, binding, read_json(binding)
    return None, None, None


def git_refs_cache_file(execution_directory, explicit=None):
    """缓存属于可写工作空间；Source Pool 身份来自其绑定，不能由路径猜测。"""
    if explicit:
        return Path(explicit).expanduser().resolve(), None
    workspace, binding, document = workspace_binding(execution_directory)
    if workspace is None:
        raise AlignmentError("缺少工作空间绑定；请提供 --cache-file，不能从 tapdata-root 父目录猜测 Source Pool")
    pool = document.get("repository_pool", {})
    root = pool.get("root") if isinstance(pool, dict) else None
    if not isinstance(root, str) or not root:
        raise AlignmentError("工作空间配置缺少 repository_pool.root：%s" % binding)
    return workspace / ".agenticops" / "git-ref-cache-v1.json", Path(root).expanduser().resolve()


def local_repository_state(path):
    """检查本地目录，不把缺失仓库混同为远端或分支缺失。"""
    if not path.is_dir() or not (path / ".git").exists():
        return "missing", None
    try:
        top = git_output(["git", "rev-parse", "--show-toplevel"], path)
    except AlignmentError as error:
        return "invalid_git_root", str(error)
    if Path(top).resolve() != path.resolve():
        return "invalid_git_root", "目录不是 Git 根目录"
    return "available", None


def resolve_scope(repositories, product_repository, requested):
    """目录是完整规则集合；严格性只由主仓和本次显式范围决定。"""
    if product_repository not in repositories:
        raise AlignmentError("TapData 主仓未在仓库目录登记：%s" % product_repository)
    requested = requested or []
    selected = []
    for repository in requested:
        if repository not in repositories:
            raise AlignmentError("未登记的目标仓库：%s" % repository)
        if repository not in selected:
            selected.append(repository)
    required = [product_repository] + [repository for repository in selected if repository != product_repository]
    return {"requested_repositories": selected, "required_repositories": required, "reported_repositories": sorted(repositories)}


def inspect_repositories(tapdata_root, repositories, scope, refresh_mode, cache_file=None, source_pool_root=None):
    """逐仓检查并刷新引用；未选中的缺失仓库只降低覆盖度。"""
    required = set(scope["required_repositories"])
    product_repository = scope["required_repositories"][0]
    names = sorted(repositories)
    observations = {}
    for index, repository in enumerate(names, start=1):
        progress("检查本地仓库 %s/%s：%s" % (index, len(names), repository))
        path = module_repository(tapdata_root, repository)
        local_status, local_error = local_repository_state(path)
        observations[repository] = {
            "repository": repository,
            "selection": "required" if repository in required else "catalog_optional",
            "local": {"status": local_status, "path": str(path), "error": local_error},
            "refs": {
                "freshness": "unavailable",
                "fetch_status": "not_applicable",
                "verification": "unavailable",
                "last_refresh_at": None,
                "last_refresh_evidence": None,
                "fetch_duration_seconds": 0.0,
                "error_kind": None,
                "error": None,
                "prompt": None,
            },
            "_refs": {},
            "_path": path if local_status == "available" else None,
        }

    # 主仓是所有分支推导的唯一全局前置；失败时不触发任何远端写入。
    if observations[product_repository]["local"]["status"] != "available":
        return observations, 0.0

    # 新路径：通用 Git refs 缓存按单仓库、单范围控制 GitHub 访问。
    # 显式仓库只刷新主仓和它自身；无显式范围时才分析完整目录。
    if cache_file is not None:
        refresh_started = time.monotonic()
        requested = set(scope["required_repositories"] if scope["requested_repositories"] else names)
        for index, repository in enumerate(names, start=1):
            observation = observations[repository]
            if observation["local"]["status"] != "available":
                continue
            mode = refresh_mode if repository in requested else None
            progress("读取 Git refs 缓存 %s/%s：%s（%s）" % (index, len(names), repository, mode or "read-only"))
            started = time.monotonic()
            try:
                scopes = ("heads",)
                if mode:
                    snapshot = git_refs.snapshot(
                        observation["_path"], scopes=scopes, cache_file=cache_file, refresh=mode,
                        max_age_seconds=AUTO_REFRESH_MAX_AGE_SECONDS, repository_id=repository,
                        source_pool_root=source_pool_root,
                    )
                else:
                    snapshot = git_refs.read_snapshot(
                        observation["_path"], scopes=scopes, cache_file=cache_file,
                        max_age_seconds=AUTO_REFRESH_MAX_AGE_SECONDS, repository_id=repository,
                        source_pool_root=source_pool_root,
                    )
                cached = snapshot["scopes"]["heads"]
            except git_refs.GitRefsError as error:
                cached = {"refs": {}, "freshness": "refresh_failed", "last_success_at": None,
                          "error": str(error), "coverage": "heads"}
            state = observation["refs"]
            freshness = cached["freshness"]
            observation["_refs"] = cached.get("refs", {})
            state.update({
                "last_refresh_at": cached.get("last_success_at"),
                "last_refresh_evidence": "git_refs_cache:%s" % cached.get("coverage", "heads"),
                "fetch_duration_seconds": round(time.monotonic() - started, 3),
            })
            if freshness == "refreshed":
                state.update({"freshness": "refreshed_during_run", "fetch_status": "refreshed", "verification": "verified"})
            elif freshness == "cached":
                state.update({"freshness": "cached_git_refs", "fetch_status": "not_requested", "verification": "cached"})
            elif freshness == "stale":
                state.update({"freshness": "stale_git_refs", "fetch_status": "not_requested", "verification": "cached_unverified"})
            else:
                kind = classify_fetch_error(cached.get("error", ""))
                state.update({"freshness": "refresh_failed", "fetch_status": "failed", "verification": "cached_unverified",
                              "error_kind": kind, "error": "远端刷新未成功（%s）" % kind,
                              "prompt": fetch_prompt(repository, kind)})
        return observations, time.monotonic() - refresh_started

    refresh_started = time.monotonic()
    for index, repository in enumerate(names, start=1):
        observation = observations[repository]
        if observation["local"]["status"] != "available":
            continue
        path = observation["_path"]
        metadata = fetch_head_metadata(path)
        fetched = refresh_required(refresh_mode, metadata, time.time())
        duration = 0.0
        fetch_error = None
        if fetched:
            progress("刷新远端引用 %s/%s：%s" % (index, len(names), repository))
            try:
                duration = fetch_origin(path, repository)
            except AlignmentError as error:
                fetch_error = str(error)
            else:
                metadata = fetch_head_metadata(path)
        else:
            progress("复用本地引用 %s/%s：%s（最后刷新：%s）" % (index, len(names), repository, metadata["last_refresh_at"] or "未知"))
        try:
            refs = local_remote_refs(path)
        except AlignmentError as error:
            refs = {}
            refs_error = str(error)
        else:
            refs_error = None
        observation["_refs"] = refs
        state = observation["refs"]
        state["last_refresh_at"] = metadata["last_refresh_at"]
        state["last_refresh_evidence"] = metadata["evidence"]
        state["fetch_duration_seconds"] = round(duration, 3)
        if fetch_error:
            error_kind = classify_fetch_error(fetch_error)
            state.update({
                "freshness": "cached_local_refs",
                "fetch_status": "failed",
                "verification": "cached_unverified",
                "error_kind": error_kind,
                "error": "远端刷新未成功（%s）" % error_kind,
                "prompt": fetch_prompt(repository, error_kind),
            })
        elif refs_error:
            state.update({
                "fetch_status": "failed" if fetch_error else "not_applicable",
                "verification": "unavailable",
                "error_kind": "origin_refs_unreadable",
                "error": "本地 origin/* 引用不可读",
                "prompt": "%s：无法读取本地 origin/*；请检查该目录的 Git 元数据。" % repository,
            })
        elif fetched:
            state.update({"freshness": "refreshed_during_run", "fetch_status": "refreshed", "verification": "verified"})
        else:
            state.update({"freshness": "cached_local_refs", "fetch_status": "not_requested", "verification": "cached_unverified"})
    return observations, time.monotonic() - refresh_started


def refresh_branch_cache(tapdata_root, repositories, product_repository, refresh_mode):
    """兼容旧调用：返回所有可用仓库，缺失仓库不再使目录检查失败。"""
    scope = resolve_scope(repositories, product_repository, [])
    observations, elapsed = inspect_repositories(tapdata_root, repositories, scope, refresh_mode)
    paths = {repository: item["_path"] for repository, item in observations.items() if item["_path"] is not None}
    states = {repository: item["refs"] for repository, item in observations.items()}
    if observations[product_repository]["local"]["status"] != "available":
        raise AlignmentError("TapData 模块根目录缺少主仓：%s（期望 %s）" % (product_repository, module_repository(tapdata_root, product_repository)))
    return paths, states, elapsed


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


def local_feature_branch_ready(product_path, branch, expected_sha):
    """确认 Source Pool 已有与远端引用缓存一致的功能分支对象。

    分支分析不能自行 fetch 或改写 Source Pool；缺少对象时由受控的
    Source Pool 刷新流程同步，再重新执行分析。
    """
    try:
        local_sha = git_output(["git", "rev-parse", "--verify", "origin/%s" % branch], product_path)
    except AlignmentError:
        return "本地 Source Pool 未同步功能分支 origin/%s；请通过受控 Source Pool 刷新流程同步分支后重试" % branch
    if local_sha != expected_sha:
        return "本地 Source Pool 的 origin/%s 与远端引用缓存 SHA 不一致；请通过受控 Source Pool 刷新流程同步分支后重试" % branch
    return None


def feature_tag_branch(product_path, branch, product_sha):
    """从功能分支 first-parent 最近的产品 Tag 得到标准分支，保留可回查证据。"""
    local_error = local_feature_branch_ready(product_path, branch, product_sha)
    if local_error:
        return None, "无法确定功能分支的 first-parent 最近产品 Tag：%s" % local_error
    try:
        try:
            tag = git_output(["git", "describe", "--first-parent", "--tags", "--abbrev=0", "--match", "[0-9]*.[0-9]*.[0-9]*", "origin/%s" % branch], product_path)
        except AlignmentError:
            raise AlignmentError("本地 Source Pool 的 Tag 图不足；请通过受控 Source Pool 刷新流程同步 Tag 后重试")
    except AlignmentError as error:
        return None, "无法确定功能分支的 first-parent 最近产品 Tag：%s" % error
    match = re.fullmatch(r"v?(\d+\.\d+\.\d+)(-dev)?", tag)
    if not match:
        return None, "最近 Tag %s 不是支持的 X.Y.Z 或 X.Y.Z-dev 格式" % tag
    target = "develop" if match.group(2) else "release-v%s" % match.group(1)
    return target, "tapdata Tag %s（first-parent 最近）推导 %s" % (tag, target)


def feature_branch_name(branch):
    if branch in ("main", "develop") or RELEASE_RE.fullmatch(branch):
        return False
    if branch.startswith("release-v"):
        raise AlignmentError("release-v 前缀的分支必须为 release-vX.Y.Z：%s" % branch)
    if not branch or branch.startswith("/") or ".." in branch or any(char.isspace() for char in branch):
        raise AlignmentError("不是有效的功能分支名：%s" % branch)
    return True


def refs_can_prove_absence(repository, ref_verifications):
    return ref_verifications is None or ref_verifications.get(repository) in ("verified", "cached")


def derived_target(repository, version, rules, refs, product_path, plugin_cache, ref_verifications=None):
    if repository == rules["product_repository"]:
        return version, "product_branch", "tapdata 输入分支"
    feature = rules.get("feature_branch")
    if feature and feature_branch_name(version):
        if repository in feature["unchanged_repositories"]:
            return None, "unchanged", "该仓库未参与功能分支推导"
        if repository in feature["same_name_repositories"]:
            if version in refs.get(repository, {}):
                return version, "same_name", "远端分支完全同名匹配"
            if not refs_can_prove_absence(repository, ref_verifications):
                return None, "unresolved", "该仓库远端引用未核验，不能断言不存在同名分支"
        if repository in feature["plugin_release_repositories"]:
            if "plugin_release" not in plugin_cache:
                if product_path is None:
                    plugin_cache["plugin_release"] = (None, "主仓本地目录不可用，无法读取 PluginKit")
                else:
                    product_sha = refs.get(rules["product_repository"], {}).get(version)
                    local_error = local_feature_branch_ready(product_path, version, product_sha)
                    plugin_cache["plugin_release"] = (None, local_error) if local_error else plugin_release(product_path, version, rules)
            target, evidence = plugin_cache["plugin_release"]
            if not target:
                return None, "unresolved", evidence
            if target in refs.get(repository, {}):
                return target, "plugin_release", "%s，精确匹配 %s" % (evidence, target)
            return None, "unresolved", "%s，但该仓库不存在精确目标 %s" % (evidence, target)
        if repository in feature["tag_fallback_repositories"]:
            if "feature_tag" not in plugin_cache:
                product_sha = refs.get(rules["product_repository"], {}).get(version)
                plugin_cache["feature_tag"] = feature_tag_branch(product_path, version, product_sha) if product_path and product_sha else (None, "主仓功能分支没有可用 SHA")
            target, evidence = plugin_cache["feature_tag"]
            if not target:
                return None, "unresolved", evidence
            if target in refs.get(repository, {}):
                return target, "tag_fallback", evidence
            return None, "unresolved", "%s，但该仓库不存在目标分支" % evidence
        return None, "unresolved", "功能分支仓库未配置推导规则"
    fixed_branches = rules.get("fixed_branches", {})
    if repository in fixed_branches:
        return fixed_branches[repository], "fixed", "%s 固定使用 %s" % (repository, fixed_branches[repository])
    fallbacks = rules["display_fallback_branches"]
    if repository == "tapdata/tapdata-application":
        return fallbacks[repository], "fixed", "tapdata-application 固定使用 main"
    if repository == "tapdata/t-layer3-test":
        if version in refs.get(repository, {}):
            return version, "same_name", "远端分支完全同名匹配"
        if refs_can_prove_absence(repository, ref_verifications):
            return fallbacks[repository], "fallback", "未找到同名分支，使用 develop"
        return None, "unresolved", "该仓库远端引用未核验，不能据缓存缺失回退 develop"
    if repository in rules["keep_current_repositories"] or repository in rules["independent_repositories"]:
        return None, "unchanged", "该仓库不参与 TapData 分支关系推导"
    if version == "main":
        return "main", "main_rule", "main 的 linked 仓统一使用 main"
    if version == "develop":
        return ("main", "develop_rule", "develop 时 license 固定使用 main") if repository == rules["license_repository"] else ("develop", "develop_rule", "develop 的 linked 仓使用 develop")
    if repository in rules["same_name_repositories"]:
        if version in refs.get(repository, {}):
            return version, "same_name", "远端分支完全同名匹配"
        if refs_can_prove_absence(repository, ref_verifications):
            return None, "unresolved", "未找到完全同名分支"
        return None, "unresolved", "该仓库远端引用未核验，不能断言不存在同名分支"
    if repository in rules["plugin_release_repositories"]:
        if "plugin_release" not in plugin_cache:
            if product_path is None:
                plugin_cache["plugin_release"] = (None, "主仓本地目录不可用，无法读取 PluginKit")
            else:
                plugin_cache["plugin_release"] = plugin_release(product_path, version, rules)
        minimum, evidence = plugin_cache["plugin_release"]
        if not minimum:
            return None, "unresolved", evidence
        target = first_release_ge(refs.get(repository, {}), minimum)
        if target:
            return target, "plugin_release", "%s，取首个不低于 %s 的分支" % (evidence, minimum)
        if refs_can_prove_absence(repository, ref_verifications):
            return None, "unresolved", "未找到不低于 %s 的 release 分支" % minimum
        return None, "unresolved", "该仓库远端引用未核验，不能断言不存在不低于 %s 的 release 分支" % minimum
    if repository == rules["license_repository"]:
        if RELEASE_RE.fullmatch(version):
            target = first_release_ge(refs.get(repository, {}), version)
            if target:
                return target, "license_release", "取首个不低于主仓版本的 license release"
            if not refs_can_prove_absence(repository, ref_verifications):
                return None, "unresolved", "license 远端引用未核验，不能据缓存缺失回退 main"
        return "main", "license_fallback", "license 无对应 release，回退 main"
    return None, "unresolved", "没有该仓库的分支推导规则"


def branch_status(target, resolution, observation):
    if observation["local"]["status"] != "available":
        return ("unavailable" if observation["selection"] == "required" else "not_covered"), None
    if target is None:
        return ("unresolved" if resolution == "unresolved" else "unchanged"), None
    refs = observation["_refs"]
    verification = observation["refs"]["verification"]
    sha = refs.get(target)
    if sha:
        return ("verified_exists" if verification == "verified" else "cached_exists"), sha
    return ("verified_missing" if verification == "verified" else "absence_unverified"), None


def build_plan(version, config, repositories, observations):
    rules = config["derivation"]
    refs = {repository: item["_refs"] for repository, item in observations.items()}
    paths = {repository: item["_path"] for repository, item in observations.items()}
    ref_verifications = {repository: item["refs"]["verification"] for repository, item in observations.items()}
    product_repository = rules["product_repository"]
    profile = config["versions"].get(version)
    rows, cache = [], {}
    for repository in sorted(repositories):
        observation = observations[repository]
        if profile:
            target, resolution, reason = profile["branches"][repository], "exact_profile", "已确认版本矩阵 %s" % version
        else:
            target, resolution, reason = derived_target(
                repository, version, rules, refs, paths.get(product_repository), cache, ref_verifications
            )
        status, sha = branch_status(target, resolution, observation)
        row = {
            "repository": repository,
            "domains": repositories[repository].get("domains", []),
            "selection": observation["selection"],
            "local": observation["local"],
            "target_branch": target,
            "target_status": status,
            "target_sha": sha,
            "resolution": resolution,
            "reason": reason,
            "refs": observation["refs"],
        }
        rows.append(row)
    return rows


def print_table(rows):
    print("repository\tselection\tlocal_status\ttarget_branch\ttarget_sha\tresolution\ttarget_status\trefs_verification\trefs_error_kind\tprompt\treason")
    for row in rows:
        refs = row.get("refs", {})
        print("%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s" % (row["repository"], row["selection"], row["local"]["status"], row["target_branch"] or "-", row["target_sha"] or "-", row["resolution"], row["target_status"], refs.get("verification", "-"), refs.get("error_kind") or "-", refs.get("prompt") or "-", row["reason"]))


def report_outcome(rows, scope, refresh_mode):
    by_repository = {row["repository"]: row for row in rows}
    blockers = []
    for repository in scope["required_repositories"]:
        row = by_repository[repository]
        if row["local"]["status"] != "available":
            blockers.append({"repository": repository, "kind": "local_repository_unavailable", "message": "必需仓库未接入或不是有效 Git 根目录"})
            continue
        if row["refs"]["fetch_status"] == "failed":
            blockers.append({
                "repository": repository,
                "kind": "remote_refresh_failed",
                "error_kind": row["refs"]["error_kind"],
                "message": "%s 缓存引用不能代替本次核验" % row["refs"]["prompt"],
            })
            continue
        if row["target_status"] not in ("verified_exists", "cached_exists"):
            blockers.append({"repository": repository, "kind": "target_branch_unavailable", "message": "目标分支未得到可用证据：%s" % row["target_status"]})
    counts = {}
    for row in rows:
        counts[row["target_status"]] = counts.get(row["target_status"], 0) + 1
    if blockers:
        outcome = "blocked"
    elif any(row["target_status"] != "verified_exists" for row in rows):
        outcome = "partial"
    else:
        outcome = "complete"
    return outcome, blockers, counts


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
    show = sub.add_parser("show", help="按缓存刷新策略解析 Git refs 并显示分支关系")
    show.add_argument("--version", required=True, help="tapdata/tapdata 的目标分支名")
    show.add_argument("--repository", action="append", default=[], help="本次严格核验的目标仓库；可重复。主仓始终严格核验")
    show.add_argument("--tapdata-root", help="包含全部 TapData 模块仓库的根目录；默认由最近工作空间的 Source Pool 解析为 <pool>/tapdata，其次当前执行目录")
    show.add_argument("--refresh", action="store_true", help="强制查询远端并更新缓存；默认首次或超过 5 分钟才刷新")
    show.add_argument("--cache-file", help="缓存文件；默认使用当前工作空间 .agenticops/git-ref-cache-v1.json")
    show.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    try:
        total_started = time.monotonic()
        config, repositories = load_configuration(args.product_root)
        tapdata_root, source = resolve_tapdata_root(args.tapdata_root, execution_directory or Path.cwd())
        refresh_mode = "always" if args.refresh else "auto"
        progress("开始解析：refresh=%s，TapData 根目录=%s" % (refresh_mode, tapdata_root))
        scope = resolve_scope(repositories, config["derivation"]["product_repository"], args.repository)
        cache_file, source_pool_root = git_refs_cache_file(execution_directory or Path.cwd(), args.cache_file)
        observations, fetch_seconds = inspect_repositories(
            tapdata_root, repositories, scope, refresh_mode, cache_file,
            source_pool_root=source_pool_root,
        )
        progress("开始本地 origin/* 解析")
        resolution_started = time.monotonic()
        rows = build_plan(args.version, config, repositories, observations)
        resolution_seconds = time.monotonic() - resolution_started
        total_seconds = time.monotonic() - total_started
        outcome, blockers, coverage_summary = report_outcome(rows, scope, refresh_mode)
        document = {
            "tapdata_branch": args.version,
            "tapdata_root": str(tapdata_root),
            "tapdata_root_resolution": source,
            "outcome": outcome,
            "scope": scope,
            "blockers": blockers,
            "coverage_summary": coverage_summary,
            "refresh": {"mode": refresh_mode, "auto_max_age_seconds": AUTO_REFRESH_MAX_AGE_SECONDS},
            "timing_seconds": {"fetch": round(fetch_seconds, 3), "local_resolution": round(resolution_seconds, 3), "total": round(total_seconds, 3)},
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "rows": rows,
        }
        progress("解析完成：outcome=%s，fetch=%.3fs，本地解析=%.3fs，总计=%.3fs" % (outcome, fetch_seconds, resolution_seconds, total_seconds))
        if args.json:
            print(json.dumps(document, ensure_ascii=False))
        else:
            print("tapdata_root\t%s" % tapdata_root)
            print("tapdata_root_resolution\t%s" % source)
            print("refresh_mode\t%s" % refresh_mode)
            print("timing_seconds\tfetch=%.3f\tlocal_resolution=%.3f\ttotal=%.3f" % (fetch_seconds, resolution_seconds, total_seconds))
            print_table(rows)
        return 2 if outcome == "blocked" else 0
    except AlignmentError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
