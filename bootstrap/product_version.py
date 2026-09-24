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


def describe(product_root, allow_dirty=False):
    """默认要求干净产品；诊断可显示带 dirty 后缀的版本，接管水印保持严格。"""
    root = Path(product_root).resolve()
    if not root.is_dir():
        raise ValueError("Product Root 不存在：%s" % root)
    dirty = bool(_git(root, "status", "--porcelain"))
    if dirty and not allow_dirty:
        raise ValueError("Product Root 存在未提交改动，不能作为可追溯接管版本")
    branch = _git(root, "branch", "--show-current") or "detached"
    commit = _git(root, "rev-parse", "--short=8", "HEAD")
    try:
        tag = _git(root, "describe", "--tags", "--abbrev=0")
        count = _git(root, "rev-list", "--count", "%s..HEAD" % tag)
    except ValueError:
        tag = "untagged"
        count = _git(root, "rev-list", "--count", "HEAD")
    return "%s-%s-%s-%s%s" % (branch, tag, count, commit, "-dirty" if dirty else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--allow-dirty", action="store_true", help="显示未提交修改的版本，并附加 -dirty")
    args = parser.parse_args()
    try:
        print(describe(args.product_root, allow_dirty=args.allow_dirty))
        return 0
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
