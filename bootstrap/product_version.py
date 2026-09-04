#!/usr/bin/env python3
"""读取绑定 Product Root 的可审计版本。

版本格式固定为 ``<branch>-<tag>-<commit-count>-<short-sha>``。本模块属于
安装面；工作流不得依赖仅维护仓库可见的 ``internal/``。
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def _git(root, *arguments):
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=root, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("无法读取 Product Root 的 Git 事实：%s" % error) from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "未知 Git 错误"
        raise ValueError("无法读取 Product Root 的 Git 事实：%s" % detail)
    return result.stdout.strip()


def describe(product_root):
    """返回干净 Git Product Root 的版本；脏工作树不能作为接管水印。"""
    root = Path(product_root).resolve()
    if not root.is_dir():
        raise ValueError("Product Root 不存在：%s" % root)
    if _git(root, "status", "--porcelain"):
        raise ValueError("Product Root 存在未提交改动，不能作为可追溯接管版本")
    branch = _git(root, "branch", "--show-current") or "detached"
    commit = _git(root, "rev-parse", "--short=8", "HEAD")
    try:
        tag = _git(root, "describe", "--tags", "--abbrev=0")
        count = _git(root, "rev-list", "--count", "%s..HEAD" % tag)
    except ValueError:
        tag = "untagged"
        count = _git(root, "rev-list", "--count", "HEAD")
    return "%s-%s-%s-%s" % (branch, tag, count, commit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    try:
        print(describe(args.product_root))
        return 0
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
