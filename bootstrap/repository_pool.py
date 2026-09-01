#!/usr/bin/env python3
"""Product Root 的 Source Pool 配置与路径边界校验。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


SCHEMA_VERSION = 1
CONFIG_NAME = "repository-pool.json"


def config_path(product_root):
    return Path(product_root).resolve() / ".local" / CONFIG_NAME


def default_root(product_root):
    root = Path(product_root).resolve()
    return Path(str(root) + "-repos")


def validate_root(product_root, value, create=False):
    product = Path(product_root).resolve()
    root = Path(value).expanduser().resolve()
    if root == product or product in root.parents or root in product.parents:
        raise ValueError("Source Pool 与 Product Root 不能互相嵌套：%s" % root)
    if root.exists() and not root.is_dir():
        raise ValueError("Source Pool 路径不是目录：%s" % root)
    if create:
        root.mkdir(parents=True, exist_ok=True)
    if root.is_dir() and not os.access(str(root), os.R_OK | os.W_OK | os.X_OK):
        raise ValueError("Source Pool 目录不可读写：%s" % root)
    return root


def load(product_root, required=True):
    path = config_path(product_root)
    if not path.is_file():
        if required:
            raise ValueError(
                "Product Root 尚未配置 Source Pool；请执行 agenticops setup，"
                "或安装时传入 --repository-pool"
            )
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Source Pool 配置无法读取：%s" % error) from error
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Source Pool 配置版本不支持：%s" % path)
    root = document.get("root")
    provisioning = document.get("provisioning")
    if not isinstance(root, str) or not root:
        raise ValueError("Source Pool 配置缺少 root")
    if provisioning not in ("manual", "auto-clone"):
        raise ValueError("Source Pool provisioning 无效")
    document["root"] = str(validate_root(product_root, root, create=False))
    return document


def write(product_root, root=None, provisioning="auto-clone"):
    product = Path(product_root).resolve()
    selected = validate_root(product, root or default_root(product), create=True)
    if provisioning not in ("manual", "auto-clone"):
        raise ValueError("Source Pool provisioning 必须是 manual 或 auto-clone")
    document = {
        "schema_version": SCHEMA_VERSION,
        "root": str(selected),
        "provisioning": provisioning,
    }
    path = config_path(product)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    os.replace(str(temporary), str(path))
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--root")
    configure.add_argument("--provisioning", choices=("manual", "auto-clone"), default="auto-clone")
    read = commands.add_parser("read")
    read.add_argument("--field", choices=("root", "provisioning"))
    args = parser.parse_args()
    try:
        if args.command == "configure":
            document = write(args.product_root, args.root, args.provisioning)
        else:
            document = load(args.product_root)
        if getattr(args, "field", None):
            print(document[args.field])
        else:
            print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
