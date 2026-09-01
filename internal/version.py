#!/usr/bin/env python3
"""输出 AgenticOps 源码版本：<分支>-<标签>-<提交数>-<提交编号>。"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def git(repo, *args):
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=10
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git 命令失败")
    return proc.stdout.strip()


def version(repo):
    branch = git(repo, "branch", "--show-current") or "detached"
    commit = git(repo, "rev-parse", "--short=8", "HEAD")
    try:
        tag = git(repo, "describe", "--tags", "--abbrev=0")
        count = git(repo, "rev-list", "--count", "%s..HEAD" % tag)
    except RuntimeError:
        tag = "untagged"
        count = git(repo, "rev-list", "--count", "HEAD")
    return "%s-%s-%s-%s" % (branch, tag, count, commit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    try:
        print(version(args.repo))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print("AgenticOps：无法读取版本：%s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
