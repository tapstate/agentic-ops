#!/usr/bin/env python3
"""TapData 多仓分支对齐项目工具。

按 developer/standards/projects/tapdata/rules/development-rules.md 的定稿分支规则，
以 tapdata 主仓分支为输入推导其它联动仓的目标分支，并支持 list/status/plan/apply 四种子命令。

契约：developer/standards/projects/tapdata/tools/branch-align.yaml

用法：
  tap_align_branches.py [--root DIR] list [filter]
  tap_align_branches.py [--root DIR] status
  tap_align_branches.py [--root DIR] plan <branch_spec> [--no-fetch] [--remote-only]
  tap_align_branches.py [--root DIR] apply <branch_spec> [--no-fetch]

  <branch_spec>：tapdata 分支（develop、main、release-vX.Y.Z、任务分支），
                或 <tapdata>,<enterprise>,<web> 显式指定 enterprise/web 分支。

安全约定（见契约 hard_rules）：
  - plan 只读；apply 会 stash 脏仓库并切换分支，任一失败必须停止。
  - 脚本不 push、不写 Jira、不写 GitHub。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ── 仓库分类（与 development-rules.md 对齐）──────────────────────────────────

CORE_REPOS = [
    "tapdata",
    "tapdata-enterprise",
    "tapdata-web",
    "tapdata-enterprise-web",
    "tapdata-connectors",
    "tapdata-connectors-enterprise",
    "tapdata-license",
    "tapdata-common-lib",
]

KEEP_REPOS = [
    "tapdata-application",
    "feishu_robot",
]

PLUGIN_PATH = "iengine/iengine-app/src/main/resources/pluginKit.properties"
PLUGIN_VERSION_KEY = "tapdata.api.verison"

FETCH_BEFORE_PLAN = True
STASH_PREFIX = "hermes-branch-align"

PLAN_REPO_WIDTH = 32
PLAN_BRANCH_WIDTH = 24
STATUS_UPSTREAM_WIDTH = 30


class AlignError(Exception):
    """脚本级致命错误，带非零退出码。"""


# ── Git 基础操作 ─────────────────────────────────────────────────────────────


def _git(repo: str, *args: str, root: Path) -> str:
    path = root / repo
    if not (path / ".git").exists():
        raise AlignError(f"repo not found or not a git repo: {path}")
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AlignError(f"git {' '.join(args)} failed for {repo}: {result.stderr.strip()}")
    return result.stdout


def _git_quiet(repo: str, *args: str, root: Path) -> bool:
    """静默执行，返回是否成功（用于探测类操作）。"""
    path = root / repo
    if not (path / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _git_try(repo: str, *args: str, root: Path) -> str:
    """静默执行，成功返回 strip 后的 stdout，失败返回空串。"""
    path = root / repo
    if not (path / ".git").exists():
        return ""
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def current_branch(repo: str, root: Path) -> str:
    out = _git(repo, "rev-parse", "--abbrev-ref", "HEAD", root=root)
    return out.strip()


def dirty_state(repo: str, root: Path) -> str:
    out = _git(repo, "status", "--porcelain", root=root)
    return "dirty" if out.strip() else "clean"


def branch_list(repo: str, root: Path, remote: str) -> list[str]:
    out = _git(
        repo,
        "for-each-ref",
        "--format=%(refname:strip=3)",
        f"refs/remotes/{remote}",
        root=root,
    )
    seen: set[str] = set()
    branches: list[str] = []
    for line in out.splitlines():
        name = line.strip()
        if name and name != "HEAD" and name not in seen:
            seen.add(name)
            branches.append(name)
    return sorted(branches)


def branch_exists(
    repo: str,
    branch: str,
    root: Path,
    remote: str,
    *,
    remote_only: bool = False,
) -> bool:
    if not remote_only and _git_quiet(
        repo,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{branch}",
        root=root,
    ):
        return True
    return _git_quiet(
        repo, "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}", root=root
    )


def fetch_repo(repo: str, root: Path, remote: str) -> None:
    _git(repo, "fetch", remote, "--prune", root=root)


# ── TAP 标记与分支分类 ────────────────────────────────────────────────────────

TAP_MARKER_RE = re.compile(r"TAP-\d+")
RELEASE_BRANCH_RE = re.compile(r"^release-v\d+(\.\d+){2,}$")


def tap_marker(branch: str) -> str:
    match = TAP_MARKER_RE.search(branch)
    return match.group(0) if match else ""


def is_release_tap_branch(branch: str) -> bool:
    return bool(RELEASE_BRANCH_RE.match(branch))


def is_standard_tap_branch(branch: str) -> bool:
    return branch in ("main", "develop") or is_release_tap_branch(branch)


def branch_containing_marker(
    repo: str,
    tap_branch: str,
    marker: str,
    root: Path,
    remote: str,
    *,
    remote_only: bool = False,
) -> str:
    if not marker:
        return ""
    if branch_exists(repo, tap_branch, root, remote, remote_only=remote_only):
        return tap_branch
    for branch in branch_list(repo, root, remote):
        if marker in branch:
            return branch
    return ""


# ── 版本比较与 pluginKit 推导 ─────────────────────────────────────────────────


def version_key(branch: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", branch)][:4]
    while len(nums) < 4:
        nums.append(0)
    return tuple(nums)


def first_release_ge(repo: str, target_release: str, root: Path, remote: str) -> str:
    candidates = [b for b in branch_list(repo, root, remote) if b.startswith("release-v")]
    if not candidates:
        return ""
    target = version_key(target_release)
    for branch in sorted(candidates, key=version_key):
        if version_key(branch) >= target:
            return branch
    return ""


def _read_plugin_content(
    branch: str,
    root: Path,
    remote: str,
    *,
    remote_only: bool = False,
) -> str:
    # plan 前已 fetch，优先读取远端快照，避免本地主分支落后时推导出过期 PluginKit 版本。
    content = _git_try("tapdata", "show", f"{remote}/{branch}:{PLUGIN_PATH}", root=root)
    if content or remote_only:
        return content
    return _git_try("tapdata", "show", f"{branch}:{PLUGIN_PATH}", root=root)


def plugin_release_for(
    branch: str,
    root: Path,
    remote: str,
    *,
    remote_only: bool = False,
) -> str:
    content = _read_plugin_content(
        branch,
        root,
        remote,
        remote_only=remote_only,
    )
    if not content:
        source = f"{remote}/{branch}" if remote_only else f"本地或 {remote}/{branch}"
        print(f"警告: 无法从 {branch} 读取 {PLUGIN_PATH}（{source}）", file=sys.stderr)
        return ""
    for line in content.splitlines():
        if line.strip().startswith(PLUGIN_VERSION_KEY + "="):
            version = line.split("=", 1)[1].strip()
            if version.endswith("-SNAPSHOT"):
                version = version[: -len("-SNAPSHOT")]
            return f"release-v{version}"
    print(f"警告: {PLUGIN_VERSION_KEY} 未在 {branch} 的 {PLUGIN_PATH} 中找到", file=sys.stderr)
    return ""


# ── 分支推导（核心，与定稿规则一致）────────────────────────────────────────────


def derive_target(
    repo: str,
    tap_branch: str,
    marker: str,
    enterprise_branch: str,
    web_branch: str,
    root: Path,
    remote: str,
    plugin_cache: dict[str, str],
    *,
    remote_only: bool = False,
) -> tuple[str, str]:
    """返回 (target, reason)。target 为 UNRESOLVED 时表示阻塞。"""

    # 显式指定 enterprise/web 分支
    if repo == "tapdata-enterprise" and enterprise_branch:
        if branch_exists(
            repo,
            enterprise_branch,
            root,
            remote,
            remote_only=remote_only,
        ):
            return enterprise_branch, "user specified tapdata-enterprise branch"
        return "UNRESOLVED", f"user specified branch {enterprise_branch} not found"
    if repo == "tapdata-web" and web_branch:
        if branch_exists(
            repo,
            web_branch,
            root,
            remote,
            remote_only=remote_only,
        ):
            return web_branch, "user specified tapdata-web branch"
        return "UNRESOLVED", f"user specified branch {web_branch} not found"

    # main → 所有联动仓 main
    if tap_branch == "main":
        return "main", "main explicit alignment rule"

    # develop → 除 license/common-lib 外全部 develop
    if tap_branch == "develop":
        if repo == "tapdata-license":
            return "main", "develop uses license main"
        if repo == "tapdata-common-lib":
            release = plugin_cache.setdefault(
                "plugin",
                plugin_release_for(
                    tap_branch,
                    root,
                    remote,
                    remote_only=remote_only,
                ),
            )
            if release:
                target = first_release_ge(repo, release, root, remote)
                if target:
                    return target, f"develop common-lib pluginKit {release} inferred"
            return "main", "develop common-lib fallback main (pluginKit 取不到)"
        # tapdata / enterprise / web / enterprise-web / connectors / connectors-enterprise
        return "develop", "develop explicit alignment rule"

    # 其它分支
    if repo == "tapdata":
        return tap_branch, "user selected tapdata branch"

    # 1. TAP 标记匹配
    marker_target = branch_containing_marker(
        repo,
        tap_branch,
        marker,
        root,
        remote,
        remote_only=remote_only,
    )
    if marker_target:
        return marker_target, f"TAP marker {marker} matched branch"

    # 2. 非标准分支名 → 全名匹配
    if not is_standard_tap_branch(tap_branch) and branch_exists(
        repo,
        tap_branch,
        root,
        remote,
        remote_only=remote_only,
    ):
        return tap_branch, "non-standard tapdata branch uses same-name branch"

    # 3. 分仓推导
    if repo in ("tapdata-enterprise", "tapdata-web", "tapdata-enterprise-web"):
        if branch_exists(repo, tap_branch, root, remote, remote_only=remote_only):
            return tap_branch, "same-name branch exists"
        return "UNRESOLVED", "same-name branch missing; specify manually or confirm fallback"

    if repo in ("tapdata-connectors", "tapdata-connectors-enterprise", "tapdata-common-lib"):
        release = plugin_cache.setdefault(
            "plugin",
            plugin_release_for(
                tap_branch,
                root,
                remote,
                remote_only=remote_only,
            ),
        )
        if release:
            target = first_release_ge(repo, release, root, remote)
            if target:
                return target, f"pluginKit {release} inferred"
        return "main", f"fallback main (pluginKit 取不到)"

    if repo == "tapdata-license":
        if is_release_tap_branch(tap_branch):
            target = first_release_ge(repo, tap_branch, root, remote)
            if target:
                return target, "first license release >= tapdata branch"
            return "main", "fallback main (no license release >= tapdata branch)"
        return "main", "fallback main for non-main/develop/non-release branch"

    return "UNRESOLVED", "unhandled repository"


# ── plan ──────────────────────────────────────────────────────────────────────


def plan_rows(
    tap_branch: str,
    enterprise_branch: str,
    web_branch: str,
    root: Path,
    remote: str,
    repositories: list[str] | None = None,
    remote_only: bool = False,
) -> list[dict[str, str]]:
    if not branch_exists(
        "tapdata",
        tap_branch,
        root,
        remote,
        remote_only=remote_only,
    ):
        source = remote if remote_only else f"local or {remote}"
        raise AlignError(f"tapdata branch not found in {source}: {tap_branch}")

    marker = tap_marker(tap_branch)
    plugin_cache: dict[str, str] = {}
    rows: list[dict[str, str]] = []

    requested = set(repositories or (*CORE_REPOS, *KEEP_REPOS))
    unknown = requested.difference((*CORE_REPOS, *KEEP_REPOS))
    if unknown:
        raise AlignError(f"unsupported repositories: {', '.join(sorted(unknown))}")

    for repo in CORE_REPOS:
        if repo not in requested:
            continue
        current = current_branch(repo, root)
        dirty = dirty_state(repo, root)
        target, reason = derive_target(
            repo,
            tap_branch,
            marker,
            enterprise_branch,
            web_branch,
            root,
            remote,
            plugin_cache,
            remote_only=remote_only,
        )
        action = "blocked" if target == "UNRESOLVED" else ("skip" if target == current else "switch")
        rows.append(
            {
                "repo": repo,
                "current": current,
                "target": target,
                "action": action,
                "reason": reason,
                "dirty": dirty,
            }
        )

    for repo in KEEP_REPOS:
        if repo not in requested:
            continue
        if not (root / repo / ".git").exists():
            raise AlignError(f"repo not found or not a git repo: {root / repo}")
        rows.append(
            {
                "repo": repo,
                "current": current_branch(repo, root),
                "target": "KEEP_CURRENT",
                "action": "keep",
                "reason": "default not aligned",
                "dirty": dirty_state(repo, root),
            }
        )
    return rows


def print_plan(rows: list[dict[str, str]]) -> None:
    print(
        f"{'repo':<{PLAN_REPO_WIDTH}} {'current':<{PLAN_BRANCH_WIDTH}} "
        f"{'target':<{PLAN_BRANCH_WIDTH}} {'action':<10} {'worktree':<10} reason"
    )
    for row in rows:
        print(
            f"{row['repo']:<{PLAN_REPO_WIDTH}} {row['current']:<{PLAN_BRANCH_WIDTH}} "
            f"{row['target']:<{PLAN_BRANCH_WIDTH}} {row['action']:<10} {row['dirty']:<10} {row['reason']}"
        )


def has_blocked_plan(rows: list[dict[str, str]]) -> bool:
    return any(row["action"] == "blocked" for row in rows)


# ── apply ─────────────────────────────────────────────────────────────────────


def switch_branch(repo: str, target: str, root: Path, remote: str) -> bool:
    if current_branch(repo, root) == target:
        return True
    if _git_quiet(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{target}", root=root):
        _git(repo, "switch", target, root=root)
        return True
    if _git_quiet(
        repo, "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{target}", root=root
    ):
        _git(repo, "switch", "-c", target, "--track", f"{remote}/{target}", root=root)
        return True
    return False


def stash_if_dirty(repo: str, stamp: str, root: Path) -> bool:
    if dirty_state(repo, root) == "dirty":
        _git(repo, "stash", "push", "-u", "-m", f"{STASH_PREFIX}-{stamp}", root=root)
        print(f"{repo}: stashed {STASH_PREFIX}-{stamp}")
        return True
    return False


def apply_plan(rows: list[dict[str, str]], root: Path, remote: str) -> None:
    import datetime

    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    stashed: list[str] = []

    for row in rows:
        if row["action"] != "switch":
            continue
        repo = row["repo"]
        target = row["target"]
        if stash_if_dirty(repo, stamp, root):
            stashed.append(repo)
        if switch_branch(repo, target, root, remote):
            print(f"{repo}: switched to {target}")
        else:
            print(f"错误: {repo}: failed to switch to {target}", file=sys.stderr)
            if stashed:
                print("以下仓库已 stash 未恢复，请手动处理:", file=sys.stderr)
                for r in stashed:
                    print(f"  - {r}", file=sys.stderr)
            raise AlignError("apply 中断：部分仓库切换失败")

    for repo in stashed:
        print(f"{repo}: restoring stash")
        _git(repo, "stash", "pop", root=root)


# ── status ────────────────────────────────────────────────────────────────────


def print_status(root: Path, remote: str) -> None:
    print(
        f"{'repo':<{PLAN_REPO_WIDTH}} {'current':<{PLAN_BRANCH_WIDTH}} "
        f"{'upstream':<{STATUS_UPSTREAM_WIDTH}} {'ahead/behind':<20} worktree"
    )
    for repo in CORE_REPOS + KEEP_REPOS:
        if not (root / repo / ".git").exists():
            continue
        branch = current_branch(repo, root)
        upstream = _git_try(
            repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", root=root
        )
        if not upstream:
            ab = "-"
        else:
            parts = _git_try(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}", root=root)
            nums = parts.split()
            ab = f"ahead {nums[0]}, behind {nums[1]}" if len(nums) == 2 else "-"
        print(
            f"{repo:<{PLAN_REPO_WIDTH}} {branch:<{PLAN_BRANCH_WIDTH}} "
            f"{upstream:<{STATUS_UPSTREAM_WIDTH}} {ab:<20} {dirty_state(repo, root)}"
        )


# ── 命令入口 ──────────────────────────────────────────────────────────────────


def cmd_list(root: Path, remote: str, filter_text: str, no_fetch: bool) -> None:
    if not no_fetch and FETCH_BEFORE_PLAN:
        fetch_repo("tapdata", root, remote)
    count = 0
    for branch in branch_list("tapdata", root, remote):
        if not filter_text or filter_text.lower() in branch.lower():
            count += 1
            print(branch)
    print(f"match_count={count}")


def cmd_plan(
    root: Path,
    remote: str,
    branch_spec: str,
    no_fetch: bool,
    repositories: list[str] | None = None,
    json_output: bool = False,
    remote_only: bool = False,
) -> list[dict[str, str]]:
    tap_spec, ent_spec, web_spec = parse_branch_spec(branch_spec)
    requested = set(repositories or CORE_REPOS)
    if not no_fetch and FETCH_BEFORE_PLAN:
        for repo in CORE_REPOS:
            if repo in requested and (root / repo / ".git").exists():
                print(f"fetch: {repo}", file=sys.stderr if json_output else sys.stdout)
                fetch_repo(repo, root, remote)
    rows = plan_rows(
        tap_spec,
        ent_spec,
        web_spec,
        root,
        remote,
        repositories=repositories,
        remote_only=remote_only,
    )
    if json_output:
        print(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))
    else:
        print_plan(rows)
    return rows


def cmd_apply(root: Path, remote: str, branch_spec: str, no_fetch: bool) -> None:
    rows = cmd_plan(root, remote, branch_spec, no_fetch)
    print()
    if has_blocked_plan(rows):
        raise AlignError("plan has blocked repos; no branch was switched")
    apply_plan(rows, root, remote)
    print()
    print("Final status:")
    print_status(root, remote)


def parse_branch_spec(raw: str) -> tuple[str, str, str]:
    parts = raw.split(",")
    tap = parts[0].strip() if len(parts) > 0 else ""
    ent = parts[1].strip() if len(parts) > 1 else ""
    web = parts[2].strip() if len(parts) > 2 else ""
    if not tap:
        raise AlignError("missing branch spec")
    return tap, ent, web


# ── main ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tap_align_branches.py",
        description="TapData 多仓分支对齐项目工具（tapdata 专用，非 ao-work 命令）",
    )
    parser.add_argument("--root", default=".", help="多仓工作根目录（含各仓库的父目录）")
    parser.add_argument("--remote", default="origin", help="git remote 名称")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("filter", nargs="?", default="")
    list_parser.add_argument("--no-fetch", action="store_true", help="跳过 fetch")

    sub.add_parser("status")

    for name in ("plan", "apply"):
        p = sub.add_parser(name)
        p.add_argument("branch_spec", help="develop、main、release-vX.Y.Z、任务分支，或 <tapdata>,<enterprise>,<web>")
        p.add_argument("--no-fetch", action="store_true", help="跳过 fetch")
        if name == "plan":
            p.add_argument(
                "--repositories",
                default="",
                help="仅输出逗号分隔的指定仓库；用于按任务领域生成计划",
            )
            p.add_argument("--json", action="store_true", help="以 JSON 输出计划行")
            p.add_argument(
                "--remote-only",
                action="store_true",
                help="仅以刷新后的远端 refs 生成计划，不回退本地同名分支",
            )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"root is not a directory: {root}", file=sys.stderr)
        return 1

    try:
        if args.command == "list":
            cmd_list(root, args.remote, args.filter, args.no_fetch)
        elif args.command == "status":
            print_status(root, args.remote)
        elif args.command == "plan":
            repositories = [
                item.strip() for item in args.repositories.split(",") if item.strip()
            ]
            cmd_plan(
                root,
                args.remote,
                args.branch_spec,
                args.no_fetch,
                repositories=repositories or None,
                json_output=args.json,
                remote_only=args.remote_only,
            )
        elif args.command == "apply":
            cmd_apply(root, args.remote, args.branch_spec, args.no_fetch)
    except AlignError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
