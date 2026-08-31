#!/usr/bin/env python3
"""TapData 多仓版本分支只读解析器。

`versions.<key>` 是已确认的显式版本矩阵；其它主仓分支按
version-branch-alignments.json 的项目规则推导。脚本只读取 Git 远程和可选的
Source Pool，绝不 checkout、fetch、创建 worktree 或修改仓库。

用法：
  python3 projects/tapdata/scripts/align_branches.py plan current --json
  python3 projects/tapdata/scripts/align_branches.py plan release-v3.8.0 \
      --source-pool <pool> --json
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
TAP_RE = re.compile(r"(?<![A-Za-z0-9])TAP-\d+(?![A-Za-z0-9])")


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
    if not all(isinstance(name, str) and isinstance(item, dict) for name, item in repositories.items()):
        raise AlignmentError("仓库目录包含无效条目")
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
        if not all(isinstance(branch, str) and branch for branch in branches.values()):
            raise AlignmentError("版本矩阵 %s 含空分支" % key)
    rules = config.get("derivation")
    if not isinstance(rules, dict):
        raise AlignmentError("分支对齐配置缺少 derivation")
    listed = set()
    for name in ("linked_repositories", "keep_current_repositories", "independent_repositories"):
        values = rules.get(name)
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise AlignmentError("derivation.%s 必须是仓库数组" % name)
        listed.update(values)
    if listed != known:
        raise AlignmentError("derivation 三类仓库必须覆盖且只覆盖仓库目录")
    if any(rules.get(name) not in known for name in ("product_repository", "license_repository")):
        raise AlignmentError("derivation 的主仓或 license 仓库未在目录登记")
    linked = set(rules["linked_repositories"])
    for name in ("same_name_repositories", "plugin_release_repositories"):
        values = rules.get(name)
        if not isinstance(values, list) or not set(values) <= linked:
            raise AlignmentError("derivation.%s 必须是 linked_repositories 的子集" % name)


def command(arguments, cwd=None):
    return subprocess.run(arguments, cwd=cwd, capture_output=True, text=True, timeout=30)


def remote_branch_status(origin, branch):
    try:
        result = command(["git", "ls-remote", "--exit-code", "--heads", origin, "refs/heads/%s" % branch])
    except subprocess.TimeoutExpired:
        return "unverified", "git ls-remote 超时"
    if result.returncode == 0:
        sha = result.stdout.split()[0] if result.stdout.split() else ""
        return "exists", sha
    if result.returncode == 2:
        return "missing", ""
    detail = (result.stderr or result.stdout).strip().splitlines()
    return "unverified", detail[-1] if detail else "git ls-remote 失败"


def remote_branches(origin):
    try:
        result = command(["git", "ls-remote", "--heads", origin])
    except subprocess.TimeoutExpired as error:
        raise AlignmentError("读取远程分支超时") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise AlignmentError(detail[-1] if detail else "无法读取远程分支")
    prefix = "refs/heads/"
    return [
        ref[len(prefix):]
        for line in result.stdout.splitlines()
        if line.strip()
        for ref in [line.split(None, 1)[1] if len(line.split(None, 1)) == 2 else ""]
        if ref.startswith(prefix)
    ]


def version_key(branch):
    values = [int(value) for value in re.findall(r"\d+", branch)][:4]
    return tuple((values + [0, 0, 0, 0])[:4])


def first_release_ge(origin, minimum):
    choices = [branch for branch in remote_branches(origin) if RELEASE_RE.fullmatch(branch)]
    for branch in sorted(choices, key=version_key):
        if version_key(branch) >= version_key(minimum):
            return branch
    return None


def pool_repository(pool_root, repository):
    if pool_root is None:
        return None
    owner, name = repository.split("/", 1)
    candidate = Path(pool_root).resolve() / owner / name
    return candidate if (candidate / ".git").exists() else None


def plugin_release(branch, pool_root, rules):
    source = pool_repository(pool_root, rules["product_repository"])
    if source is None:
        return None, "缺少主仓 Source Pool；无法只读读取 PluginKit 版本"
    plugin = rules.get("plugin_version_source", {})
    path = plugin.get("path")
    key = plugin.get("key")
    if not isinstance(path, str) or not isinstance(key, str):
        return None, "PluginKit 配置不完整"
    try:
        result = command(["git", "show", "origin/%s:%s" % (branch, path)], cwd=source)
    except subprocess.TimeoutExpired:
        return None, "读取 PluginKit 超时"
    if result.returncode != 0:
        return None, "无法从 origin/%s 读取 %s" % (branch, path)
    for line in result.stdout.splitlines():
        if line.strip().startswith(key + "="):
            version = line.split("=", 1)[1].strip().removesuffix("-SNAPSHOT")
            return "release-v" + version, "PluginKit %s" % version
    return None, "%s 未包含 %s" % (path, key)


def marker_branches(origin, marker):
    if not marker:
        return []
    token = re.compile(
        r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(marker)
    )
    return [branch for branch in remote_branches(origin) if token.search(branch)]


def derived_target(repository, product_branch, repositories, rules, pool_root, overrides, plugin_cache):
    if repository in rules["keep_current_repositories"]:
        return None, "keep_current", "该仓库不参与产品版本分支对齐"
    if repository in rules["independent_repositories"]:
        return None, "independent", "该仓库单独管理，不参与产品版本分支对齐"
    if product_branch == "main":
        return "main", "main_rule", "main 的 linked 仓统一使用 main"
    if product_branch == "develop":
        if repository == rules["license_repository"]:
            return "main", "develop_rule", "develop 时 license 固定使用 main"
        return "develop", "develop_rule", "develop 的 linked 仓使用 develop"
    if repository == rules["product_repository"]:
        return product_branch, "product_branch", "主仓输入分支"
    explicit = overrides.get(repository)
    if explicit:
        return explicit, "explicit", "调用方显式指定"
    origin = repositories[repository].get("origin")
    marker = TAP_RE.search(product_branch)
    if marker:
        matches = marker_branches(origin, marker.group(0))
        if len(matches) == 1:
            return matches[0], "tap_marker", "按完整 %s 标记唯一匹配" % marker.group(0)
        if not matches:
            return (
                None,
                "unresolved",
                "未找到完整 Jira 标记 %s 的远程分支；需要显式 override"
                % marker.group(0),
            )
        return (
            None,
            "unresolved",
            "完整 Jira 标记 %s 匹配多个远程分支：%s；需要显式 override"
            % (marker.group(0), "、".join(sorted(matches))),
        )
    if not RELEASE_RE.fullmatch(product_branch):
        status, _ = remote_branch_status(origin, product_branch)
        if status == "exists":
            return product_branch, "same_name", "非标准分支同名匹配"
    if repository in rules["same_name_repositories"]:
        return product_branch, "same_name", "enterprise/web 需要同名远程分支"
    if repository in rules["plugin_release_repositories"]:
        if "release" not in plugin_cache:
            plugin_cache["release"] = plugin_release(product_branch, pool_root, rules)
        minimum, evidence = plugin_cache["release"]
        if not minimum:
            return None, "unresolved", evidence
        try:
            target = first_release_ge(origin, minimum)
        except AlignmentError as error:
            return None, "unresolved", "无法列出远程 release 分支：%s" % error
        if target:
            return target, "plugin_release", "%s，取首个不低于 %s 的分支" % (evidence, minimum)
        return None, "unresolved", "未找到不低于 %s 的 release 分支" % minimum
    if repository == rules["license_repository"]:
        if RELEASE_RE.fullmatch(product_branch):
            try:
                target = first_release_ge(origin, product_branch)
            except AlignmentError as error:
                return None, "unresolved", "无法列出 license release 分支：%s" % error
            if target:
                return target, "license_release", "取首个不低于主仓版本的 license release"
        return "main", "license_fallback", "license 无对应 release，回退 main"
    return None, "unresolved", "没有该仓库的分支推导规则"


def build_plan(product_branch, config, repositories, pool_root=None, overrides=None):
    overrides = overrides or {}
    profile = config["versions"].get(product_branch)
    rules = config["derivation"]
    product_repo = rules["product_repository"]
    if profile is None:
        source_status, source_evidence = remote_branch_status(
            repositories[product_repo]["origin"], product_branch
        )
        if source_status != "exists":
            raise AlignmentError("主仓分支 %s %s：%s" % (product_branch, source_status, source_evidence))
    rows = []
    plugin_cache = {}
    for repository in sorted(repositories):
        if profile is not None:
            target = profile["branches"][repository]
            resolution, reason = "exact_profile", "已确认版本矩阵 %s" % product_branch
        else:
            target, resolution, reason = derived_target(
                repository, product_branch, repositories, rules, pool_root, overrides, plugin_cache
            )
        row = {
            "repository": repository,
            "domains": repositories[repository].get("domains", []),
            "target_branch": target,
            "resolution": resolution,
            "reason": reason,
        }
        if target is None:
            remote_status = "unresolved" if resolution == "unresolved" else "not_applicable"
            row.update({"remote_status": remote_status, "evidence": ""})
        else:
            status, evidence = remote_branch_status(repositories[repository]["origin"], target)
            row.update({"remote_status": status, "evidence": evidence})
        rows.append(row)
    return rows


def print_table(rows):
    print("repository\ttarget_branch\tresolution\tremote_status\treason")
    for row in rows:
        print("%s\t%s\t%s\t%s\t%s" % (
            row["repository"], row["target_branch"] or "-", row["resolution"],
            row["remote_status"], row["reason"],
        ))


def main(argv=None):
    parser = argparse.ArgumentParser(description="TapData 多仓版本分支只读解析器")
    parser.add_argument("--product-root", default=Path(__file__).resolve().parents[3])
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="按版本键或主仓分支生成全仓对齐矩阵")
    plan.add_argument("product_branch", help="current、main、develop、release-vX.Y.Z 或任务分支")
    plan.add_argument("--source-pool", help="仅在 release/任务分支推导 PluginKit 时读取主仓 origin ref")
    plan.add_argument("--enterprise-branch", help="显式覆盖 tapdata-enterprise 分支")
    plan.add_argument("--web-branch", help="显式覆盖 tapdata-web 分支")
    plan.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)
    try:
        config, repositories = load_configuration(args.product_root)
        overrides = {
            key: value for key, value in {
                "tapdata/tapdata-enterprise": args.enterprise_branch,
                "tapdata/tapdata-web": args.web_branch,
            }.items() if value
        }
        rows = build_plan(args.product_branch, config, repositories, args.source_pool, overrides)
        if args.json:
            print(json.dumps({"product_branch": args.product_branch, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "rows": rows}, ensure_ascii=False))
        else:
            print_table(rows)
        return 0
    except AlignmentError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
