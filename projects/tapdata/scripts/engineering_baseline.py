"""把现役 TapData 分支解析结果转换为完整工程基线；不准备或修改仓库。"""
from __future__ import annotations

from pathlib import Path
import tempfile

from workflow import engineering_baseline as baseline
from projects.tapdata.scripts.align_branches import build_plan


def preflight(station, version, config, catalog, profile, *, optional=(), explicit_branches=None):
    """不写工位任务或源码；必要主仓对象在缓存及临时 bare 库中解析。"""
    from workflow import station_source as source, source_pool
    baseline.ref_name(version)
    selected = baseline.selected_repositories(profile, catalog, optional)
    observations = source.probe_repositories(station, catalog, selected)
    overrides = explicit_branches or {}
    product = config["derivation"]["product_repository"]
    # main/develop 或逐仓显式指定无需下载主仓图。其它输入先通过远端主仓核验。
    need_objects = (version not in ("main", "develop") and version not in config.get("versions", {})
                    and any(name != product and name not in overrides for name in selected)
                    and product in observations
                    and observations[product]["refs"]["verification"] == "verified"
                    and version in observations[product]["_refs"])
    with tempfile.TemporaryDirectory(prefix="ao-takeover-probe-") as directory:
        if need_objects:
            try:
                with source_pool.refreshed(station, product, catalog[product]["origin"], source.git) as cache:
                    source.git(directory, "init", "--bare", "--quiet")
                    source.git(directory, "fetch", "--quiet", str(cache),
                               "+refs/heads/*:refs/remotes/origin/*", "refs/tags/*:refs/tags/*")
                    source.git(directory, "remote", "add", "origin", catalog[product]["origin"])
                observations[product]["_path"] = Path(directory)
                actual = source.git(directory, "rev-parse", "refs/remotes/origin/" + version).stdout.strip()
                if actual != observations[product]["_refs"][version]:
                    raise ValueError("主仓引用在预检期间变化")
            except (OSError, ValueError):
                observations[product]["error"] = "主仓推导对象无法核验或引用已变化，请重试预检"
                observations[product]["refs"]["verification"] = "unknown"
                observations[product]["_path"] = None
        return resolve(version, config, catalog, profile, observations,
                       optional=optional, explicit_branches=explicit_branches)


def resolve(version, config, catalog, profile, observations, *, optional=(),
            explicit_branches=None):
    """复用现役 align_branches.build_plan，保留 Project 分支判定唯一来源。

    observations 由接管编排传入；返回值仍需逐仓核验本地 Git 对象并在持锁操作
    中持久化。这里只接受已核验引用，不把缓存命中或展示回退当执行依据。
    """
    baseline.text(version, "version")
    if version == "current":
        raise ValueError("current 展示矩阵不能作为任务工程版本")
    selected = baseline.selected_repositories(profile, catalog, optional)
    overrides = explicit_branches if explicit_branches is not None else {}
    if not isinstance(overrides, dict) or not set(overrides) <= set(selected):
        raise ValueError("显式分支必须属于完整工程 Profile")
    product = config.get("derivation", {}).get("product_repository")
    if product in overrides and overrides[product] != version:
        raise ValueError("主仓显式分支必须与工程版本一致；请修改 version 后重新解析完整工程")
    if not isinstance(observations, dict) or not set(selected) <= set(observations):
        raise ValueError("缺少完整工程仓库的引用观测")
    rows = build_plan(version, config, {name: catalog[name] for name in selected},
                      {name: observations[name] for name in selected})
    resolutions, problems = {}, []
    accepted = {"exact_profile", "product_branch", "same_name", "plugin_release",
                "tag_fallback", "fixed", "main_rule", "develop_rule",
                "license_release", "license_fallback"}
    rule_version = baseline.digest(config)
    for row in rows:
        name = row["repository"]
        if name not in selected or name in resolutions:
            raise ValueError("分支解析器返回未知或重复仓库")
        observation = observations[name]
        if observation.get("refs", {}).get("verification") != "verified":
            problems.append("%s：%s" % (name, observation.get("error") or "远端引用未核验"))
            continue
        branch, sha, source = row.get("target_branch"), row.get("target_sha"), row.get("resolution")
        if name in overrides:
            branch = baseline.ref_name(overrides[name])
            sha = observation.get("_refs", {}).get(branch)
            source = "explicit_branch"
        elif source not in accepted or row.get("target_status") != "verified_exists":
            problems.append("%s：工程仓库需要明确且已核验的分支 %s（%s）" %
                            (name, branch or "未确定", row.get("reason", "无法解析")))
            continue
        if not sha:
            problems.append("%s：显式分支 %s 不存在" % (name, branch))
            continue
        resolutions[name] = {
            "verification": "verified", "ref_kind": "branch", "ref_name": branch,
            "commit_sha": sha, "resolution_source": source, "rule_version": rule_version,
        }
    if problems:
        raise ValueError("接管分支核验未通过（已检查 %s 个仓库）：\n%s\n--version 表示 tapdata/tapdata 主仓分支；模块分支使用 --explicit-branch 仓库=分支" %
                         (len(selected), "\n".join(problems)))
    return baseline.freeze(profile, catalog, resolutions, {
        "version": version, "explicit_branches": dict(overrides),
        "optional_repositories": sorted(optional),
    }, optional)
