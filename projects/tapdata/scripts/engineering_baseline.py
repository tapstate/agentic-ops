"""把现役 TapData 分支解析结果转换为完整工程基线；不准备或修改仓库。"""
from __future__ import annotations

from workflow import engineering_baseline as baseline
from projects.tapdata.scripts.align_branches import build_plan


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
    resolutions = {}
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
            raise ValueError("完整工程仓库的远端引用未核验：%s" % name)
        branch, sha, source = row.get("target_branch"), row.get("target_sha"), row.get("resolution")
        if name in overrides:
            branch = baseline.ref_name(overrides[name])
            sha = observation.get("_refs", {}).get(branch)
            source = "explicit_branch"
        elif source not in accepted or row.get("target_status") != "verified_exists":
            raise ValueError("工程仓库需要明确且已核验的分支：%s" % name)
        resolutions[name] = {
            "verification": "verified", "ref_kind": "branch", "ref_name": branch,
            "commit_sha": sha, "resolution_source": source, "rule_version": rule_version,
        }
    return baseline.freeze(profile, catalog, resolutions, {
        "version": version, "explicit_branches": dict(overrides),
        "optional_repositories": sorted(optional),
    }, optional)
