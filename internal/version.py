#!/usr/bin/env python3
"""维护仓库的兼容入口；安装面版本实现位于 bootstrap/product_version.py。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bootstrap.product_version import describe  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    try:
        print(describe(args.repo))
    except ValueError as exc:
        print("AgenticOps：无法读取版本：%s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
