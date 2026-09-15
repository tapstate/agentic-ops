"""完整工程基线值与任务变更引用；不读写任务状态或执行 Git 副作用。

调用方负责在持锁的接管操作中采集可信 ref/SHA 并核验 Git 对象；本模块只验证
数据合同并生成不可变摘要。摘要不是授权或防篡改保证，也不是应用启动证明。
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from workflow.project_rules import canonical_repository_endpoint


REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def text(value, field):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("%s 必须是非空且无首尾空白的字符串" % field)
    if any(ord(character) < 32 for character in value):
        raise ValueError("%s 包含控制字符" % field)
    return value


def repository_id(value):
    if not isinstance(value, str) or not REPOSITORY.fullmatch(value):
        raise ValueError("repository_id 必须是安全的 owner/repo")
    return value


def ref_name(value):
    """校验裸 branch/tag 名；不接受 revision 表达式或 Git 参数。"""
    text(value, "ref_name")
    if (value.startswith(("-", "refs/", "origin/", "/")) or value == "@"
            or any(token in value for token in ("..", "@{", "//", "\\"))
            or any(c.isspace() or c in "~^:?*[" or ord(c) == 127 for c in value)
            or value.endswith(("/", "."))
            or any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))):
        raise ValueError("ref_name 必须是明确的裸分支或标签名")
    return value


def selected_repositories(profile, catalog, optional=()):
    if not isinstance(profile, dict) or not isinstance(catalog, dict):
        raise ValueError("Profile 和仓库目录必须是对象")
    text(profile.get("id"), "profile.id")
    if type(profile.get("revision")) is not int or profile["revision"] < 1:
        raise ValueError("profile.revision 必须是正整数")
    required = profile.get("repositories")
    allowed = profile.get("optional_repositories", [])
    if not isinstance(required, list) or not required or not isinstance(allowed, list):
        raise ValueError("Profile 必须声明完整必需仓库列表")
    if not isinstance(optional, (list, tuple)):
        raise ValueError("optional 必须是仓库列表")
    for values in (required, allowed, optional):
        for value in values:
            repository_id(value)
        if len(values) != len(set(values)):
            raise ValueError("仓库列表存在重复项")
    if set(required) & set(allowed) or not set(optional) <= set(allowed):
        raise ValueError("可选仓库越界或与必需仓库重叠")
    for name in required + allowed:
        if name not in catalog or not isinstance(catalog[name], dict):
            raise ValueError("Profile 引用了未知仓库：%s" % name)
        text(catalog[name].get("origin"), "catalog.origin")
    return sorted(required + list(optional))


def freeze(profile, catalog, resolutions, resolution_input, optional=()):
    """把全量、已核验解析值固化为新值；失败不返回部分 frozen 清单。"""
    selected = selected_repositories(profile, catalog, optional)
    if not isinstance(resolutions, dict) or set(resolutions) != set(selected):
        raise ValueError("解析结果必须覆盖且只覆盖完整工程仓库")
    if not isinstance(resolution_input, dict) or not resolution_input:
        raise ValueError("缺少版本解析输入")
    entries = {}
    for name in selected:
        row = resolutions[name]
        if not isinstance(row, dict) or row.get("verification") != "verified":
            raise ValueError("仓库引用未核验：%s" % name)
        if row.get("ref_kind") not in ("branch", "tag", "commit"):
            raise ValueError("不支持的 ref_kind：%s" % name)
        sha = row.get("commit_sha")
        if not isinstance(sha, str) or not SHA.fullmatch(sha):
            raise ValueError("commit_sha 必须是完整 Git SHA：%s" % name)
        ref = row.get("ref_name")
        if row["ref_kind"] == "commit":
            if ref != sha:
                raise ValueError("commit 引用必须与 SHA 完全一致")
        else:
            ref_name(ref)
        source = text(row.get("resolution_source"), "resolution_source")
        if source in ("current", "current_branch", "unchanged", "keep_current",
                      "fallback", "display_fallback", "unresolved", "implicit_main"):
            raise ValueError("展示或不确定解析不能作为工程基线")
        entry = {
            "repository_id": name, "origin": catalog[name]["origin"],
            "ref_kind": row["ref_kind"], "ref_name": ref, "commit_sha": sha,
            "resolution_source": source,
            "rule_version": text(row.get("rule_version"), "rule_version"),
            "path": "source/" + name,
        }
        entries[name] = entry
    result = {
        "schema_version": 1, "status": "frozen",
        "profile": {"id": profile["id"], "revision": profile["revision"],
                    "digest": digest(profile)},
        "resolution_input": copy.deepcopy(resolution_input),
        "repositories": entries,
    }
    result["digest"] = digest(result)
    return result


def validate(baseline):
    if not isinstance(baseline, dict) or baseline.get("status") != "frozen":
        raise ValueError("工程基线尚未冻结")
    payload = {key: value for key, value in baseline.items() if key != "digest"}
    if (type(baseline.get("schema_version")) is not int
            or baseline["schema_version"] != 1 or baseline.get("digest") != digest(payload)):
        raise ValueError("工程基线摘要或版本不匹配")
    entries = baseline.get("repositories")
    if not isinstance(entries, dict) or not entries:
        raise ValueError("工程基线缺少仓库")
    for name, entry in entries.items():
        repository_id(name)
        if not isinstance(entry, dict) or entry.get("repository_id") != name:
            raise ValueError("工程基线仓库身份不匹配")
        if entry.get("path") != "source/" + name:
            raise ValueError("工程基线仓库路径不匹配")
        if not isinstance(entry.get("commit_sha"), str) or not SHA.fullmatch(entry["commit_sha"]):
            raise ValueError("工程基线 SHA 无效")
    profile = baseline.get("profile")
    if (not isinstance(profile, dict) or set(profile) != {"id", "revision", "digest"}
            or not isinstance(profile.get("digest"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", profile["digest"])):
        raise ValueError("工程 Profile 身份无效")
    rebuilt = freeze(
        {"id": profile["id"], "revision": profile["revision"], "repositories": list(entries)},
        {name: {"origin": row.get("origin")} for name, row in entries.items()},
        {name: dict(row, verification="verified") for name, row in entries.items()},
        baseline.get("resolution_input"),
    )
    rebuilt["profile"] = profile
    rebuilt.pop("digest")
    if rebuilt != payload:
        raise ValueError("工程基线结构无效")
    return baseline


def verify_local_repository(workspace, repository, origin, ref_kind, ref, sha):
    """只读核对独立仓库及本地引用对象；不代替接管时的新鲜远端查询。

    不 fetch/clone/checkout；缺失对象由接管操作按其已登记意图准备后重试。
    """
    repository_id(repository)
    text(origin, "origin")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("缺少完整 Git SHA")
    if ref_kind not in ("branch", "tag", "commit"):
        raise ValueError("不支持的 ref_kind")
    if ref_kind == "commit":
        if ref != sha:
            raise ValueError("commit 引用与 SHA 不一致")
    else:
        ref_name(ref)
    root = Path(workspace).resolve(strict=True)
    path = root / "source" / repository
    chain = path.relative_to(root).parts
    current = root
    for part in chain + (".git",):
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as error:
            raise ValueError("缺少独立工程仓库：%s" % repository) from error
        if not stat.S_ISDIR(mode):
            raise ValueError("仓库路径必须为真实目录，拒绝 symlink/linked worktree")
    # 只读 Git 不继承调用方的 GIT_DIR、worktree、alternate 或配置注入。
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0",
                       GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1")

    def git(*arguments):
        try:
            result = subprocess.run(["git", "-C", str(path), *arguments],
                                    env=environment, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("Git 本地核验失败：%s" % repository) from error
        if result.returncode:
            raise ValueError("Git 本地核验未通过：%s (%s)" % (repository, arguments[0]))
        return result.stdout.strip()

    if Path(git("rev-parse", "--show-toplevel")).resolve() != path:
        raise ValueError("工程目录不是 Git 根目录")
    common = Path(git("rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = path / common
    if common.resolve() != path / ".git":
        raise ValueError("工程仓库不能共享 Git 元数据")
    for directory in (path / ".git" / "objects", path / ".git" / "objects" / "info"):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Git 对象目录不是独立真实目录")
    alternates = path / ".git" / "objects" / "info" / "alternates"
    if alternates.exists() or alternates.is_symlink():
        raise ValueError("独立工程仓库不能依赖 alternates")
    actual = canonical_repository_endpoint(git("remote", "get-url", "origin"))
    expected = canonical_repository_endpoint(origin)
    if not expected or actual != expected:
        raise ValueError("工程仓库 origin 与目录不匹配")
    if git("cat-file", "-t", sha) != "commit":
        raise ValueError("基线 SHA 不是 commit 对象")
    reference = {"branch": "refs/remotes/origin/", "tag": "refs/tags/", "commit": ""}[ref_kind] + ref
    if git("rev-parse", "--verify", reference + "^{commit}") != sha:
        raise ValueError("本地引用已变化或与核验 SHA 不一致")
    return {"repository_id": repository, "commit_sha": sha, "path": "source/" + repository}


def verify_local_baseline(workspace, value):
    """全量核验 Git 对象后返回独立副本；任何仓失败均不返回部分成功。"""
    validate(value)
    for name, entry in sorted(value["repositories"].items()):
        verify_local_repository(workspace, name, entry["origin"], entry["ref_kind"],
                                entry["ref_name"], entry["commit_sha"])
    return copy.deepcopy(value)


def task_repository(baseline, repository, work_branch, target_branch,
                    approved_scope, verification_method):
    """创建单仓交付绑定值；不创建分支、不签发授权、不修改基线。"""
    validate(baseline)
    repository_id(repository)
    if repository not in baseline["repositories"]:
        raise ValueError("修改仓库不在冻结的完整工程中")
    ref_name(work_branch)
    ref_name(target_branch)
    if work_branch == target_branch:
        raise ValueError("任务工作分支不能是目标分支")
    if (not isinstance(approved_scope, list) or not approved_scope
            or any(not isinstance(item, str) or not item.strip() for item in approved_scope)):
        raise ValueError("缺少明确修改范围")
    text(verification_method, "verification_method")
    return {
        "repository_id": repository,
        "baseline_entry_digest": digest(baseline["repositories"][repository]),
        "work_branch": work_branch, "target_branch": target_branch,
        "approved_scope": list(approved_scope), "verification_method": verification_method,
        "observation": None, "deliveries": [], "disposition": "pending",
    }


def validate_task_repositories(baseline, bindings):
    validate(baseline)
    if not isinstance(bindings, dict):
        raise ValueError("task_repositories 必须是对象")
    for name, binding in bindings.items():
        if (name not in baseline["repositories"] or not isinstance(binding, dict)
                or binding.get("repository_id") != name
                or binding.get("baseline_entry_digest") != digest(baseline["repositories"][name])):
            raise ValueError("任务仓库未引用当前冻结基线：%s" % name)
        if set(binding) != {"repository_id", "baseline_entry_digest", "work_branch",
                            "target_branch", "approved_scope", "verification_method",
                            "observation", "deliveries", "disposition"}:
            raise ValueError("任务仓库字段无效，不允许第二份可变基线")
        task_repository(baseline, name, binding["work_branch"], binding["target_branch"],
                        binding["approved_scope"], binding["verification_method"])
    return bindings
