from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


POLICY_PATH = "maintainer/standards/git/story-review-policy.yaml"


@dataclass(frozen=True)
class BranchReview:
    branch: str
    channel: str
    target_branch: str


@dataclass(frozen=True)
class PullRequestFact:
    number: int | None = None
    url: str = ""
    head_sha: str = ""
    head_branch: str = ""
    base_branch: str = ""
    approved_for_head: bool = False
    reviewers: tuple[str, ...] = ()


def resolve_branch_review(root: Path, *, head: str | None = None) -> BranchReview:
    payload = _load_policy(root)
    branch = os.environ.get("AGENTIC_OPS_STORY_BRANCH", "").strip()
    if not branch:
        branch = _git(root, "branch", "--show-current")
    if not branch and head:
        candidates = tuple(
            line.strip().removeprefix("*").strip()
            for line in _git(root, "branch", "--format=%(refname:short)", "--contains", head).splitlines()
            if line.strip() and not line.strip().startswith("(")
        )
        if len(candidates) == 1:
            branch = candidates[0]
    if not branch:
        raise ValueError("当前 Git 分支无法确定，故事审查通道失败关闭")

    matches: list[tuple[str, str]] = []
    if branch in payload["protected_branches"]:
        matches.append(("protected", branch))
    if branch in payload["commit_review_branches"]:
        matches.append(("commit_review", payload["default_target_branch"]))
    for entry in payload["pr_review_branches"]:
        if re.fullmatch(entry["pattern"], branch):
            matches.append(("pr_review", entry["target_branch"]))
    for entry in payload["special_branch_patterns"]:
        if re.fullmatch(entry["pattern"], branch):
            matches.append(("special", entry["target_branch"]))
    if len(matches) != 1:
        reason = "没有命中规则" if not matches else "同时命中多个规则"
        raise ValueError(f"分支 {branch} {reason}，故事审查通道失败关闭")
    channel, target = matches[0]
    return BranchReview(branch=branch, channel=channel, target_branch=target)


def read_pull_request_fact(
    root: Path,
    review: BranchReview,
    commit_sha: str,
    *,
    require_current_head_approval: bool = False,
) -> PullRequestFact:
    if review.channel != "pr_review" or not commit_sha:
        return PullRequestFact()
    repository = _repository_slug(root)
    gh = os.environ.get("AGENTIC_OPS_GH_BIN", "gh")
    completed = subprocess.run(
        [
            gh,
            "pr",
            "list",
            "--repo",
            repository,
            "--base",
            review.target_branch,
            "--head",
            review.branch,
            "--state",
            "open",
            "--json",
            "number,url,state,headRefOid,headRefName,baseRefName",
        ],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip() or "unknown error"
        raise ValueError(f"GitHub PR 回读失败：{diagnostic}")
    raw = json.loads(completed.stdout or "[]")
    matches = [
        item
        for item in raw
        if item.get("headRefOid") == commit_sha
        and item.get("headRefName") == review.branch
        and item.get("baseRefName") == review.target_branch
    ]
    if len(matches) > 1:
        raise ValueError("当前 Head 同时命中多个开放 PR，故事审查失败关闭")
    if not matches:
        return PullRequestFact()
    item = matches[0]
    fact = PullRequestFact(
        number=int(item["number"]),
        url=str(item["url"]),
        head_sha=str(item["headRefOid"]),
        head_branch=str(item["headRefName"]),
        base_branch=str(item["baseRefName"]),
    )
    if not require_current_head_approval:
        return fact
    reviewers = _read_current_head_reviewers(
        root, gh, repository, fact.number or 0, commit_sha
    )
    return PullRequestFact(
        **{**fact.__dict__, "approved_for_head": bool(reviewers), "reviewers": reviewers}
    )


def _read_current_head_reviewers(
    root: Path, gh: str, repository: str, number: int, commit_sha: str
) -> tuple[str, ...]:
    owner, name = repository.split("/", 1)
    query = """
query($owner:String!,$name:String!,$number:Int!){
  repository(owner:$owner,name:$name){
    pullRequest(number:$number){
      reviews(last:100){nodes{state author{login} commit{oid}}}
    }
  }
  viewer{login}
}
""".strip()
    completed = subprocess.run(
        [
            gh,
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        ],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.strip() or "unknown error"
        raise ValueError(f"GitHub PR Review 回读失败：{diagnostic}")
    payload = json.loads(completed.stdout)
    viewer = payload["data"]["viewer"]["login"]
    nodes = payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
    latest: dict[str, str] = {}
    for node in nodes:
        author = (node.get("author") or {}).get("login")
        if author and (node.get("commit") or {}).get("oid") == commit_sha:
            latest[author] = str(node.get("state", ""))
    return tuple(sorted(login for login, state in latest.items() if state == "APPROVED" and login != viewer))


def _load_policy(root: Path) -> dict[str, Any]:
    path = root / POLICY_PATH
    if not path.is_file():
        raise ValueError(f"故事审查分支策略不存在：{POLICY_PATH}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("故事审查分支策略 schema_version 必须为 1")
    required_lists = (
        "protected_branches",
        "commit_review_branches",
        "pr_review_branches",
        "special_branch_patterns",
    )
    if not isinstance(payload.get("default_target_branch"), str) or not payload["default_target_branch"].strip():
        raise ValueError("故事审查分支策略缺少 default_target_branch")
    if any(not isinstance(payload.get(key), list) for key in required_lists):
        raise ValueError("故事审查分支策略的分支规则必须为列表")
    for key in ("protected_branches", "commit_review_branches"):
        if any(not isinstance(item, str) or not item.strip() for item in payload[key]):
            raise ValueError(f"故事审查分支策略 {key} 只能包含非空分支名")
    if set(payload["protected_branches"]) & set(payload["commit_review_branches"]):
        raise ValueError("保护分支与 commit_review 分支不能重叠")
    for entry in (*payload["pr_review_branches"], *payload["special_branch_patterns"]):
        if not isinstance(entry, dict) or set(entry) != {"pattern", "target_branch"}:
            raise ValueError("模式分支条目必须包含 pattern 和 target_branch")
        if any(not isinstance(entry[key], str) or not entry[key].strip() for key in ("pattern", "target_branch")):
            raise ValueError("模式分支条目的 pattern 和 target_branch 必须为非空字符串")
        re.compile(entry["pattern"])
    return payload


def _repository_slug(root: Path) -> str:
    remote = _git(root, "remote", "get-url", "origin")
    match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?$", remote)
    if match is None:
        raise ValueError("origin 不是可识别的 GitHub 仓库地址")
    return match.group(1)


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "Git 命令失败")
    return completed.stdout.strip()
