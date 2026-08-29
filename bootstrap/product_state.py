#!/usr/bin/env python3
"""Product Root 的统一本地生命周期配置。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


SCHEMA_VERSION = 1


def state_path(root):
    return Path(root).resolve() / ".local" / "product.json"


def validate(document):
    required = {
        "schema_version", "mode", "repository", "tracking_branch",
        "current_ref", "previous_ref",
    }
    if set(document) != required or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Product Root 本地配置结构无效")
    if document.get("mode") not in ("source", "installed"):
        raise ValueError("Product Root mode 无效")
    for field in ("repository", "tracking_branch", "current_ref"):
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise ValueError("Product Root %s 无效" % field)
    previous = document.get("previous_ref")
    if previous is not None and (not isinstance(previous, str) or not previous.strip()):
        raise ValueError("Product Root previous_ref 无效")
    return document


def load(root):
    path = state_path(root)
    if not path.is_file():
        raise ValueError("Product Root 尚未初始化本地配置，请先执行 agenticops setup 或 install")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Product Root 本地配置无法读取：%s" % error) from error
    return validate(document)


def save(root, document):
    validate(document)
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(str(temporary), str(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-root", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write")
    write.add_argument("--mode", choices=("source", "installed"), required=True)
    write.add_argument("--repository", required=True)
    write.add_argument("--branch", required=True)
    write.add_argument("--current-ref", required=True)
    write.add_argument("--previous-ref")
    read = sub.add_parser("read")
    read.add_argument("--field", choices=("mode", "repository", "tracking_branch", "current_ref", "previous_ref"))
    update = sub.add_parser("update-ref")
    update.add_argument("--current-ref", required=True)
    update.add_argument("--previous-ref")
    args = parser.parse_args()
    try:
        if args.command == "write":
            save(args.product_root, {
                "schema_version": SCHEMA_VERSION,
                "mode": args.mode,
                "repository": args.repository,
                "tracking_branch": args.branch,
                "current_ref": args.current_ref,
                "previous_ref": args.previous_ref,
            })
            return 0
        document = load(args.product_root)
        if args.command == "read":
            if args.field:
                value = document[args.field]
                print("" if value is None else value)
            else:
                print(json.dumps(document, ensure_ascii=False, indent=2))
            return 0
        previous = args.previous_ref if args.previous_ref is not None else document["current_ref"]
        document["previous_ref"] = previous
        document["current_ref"] = args.current_ref
        save(args.product_root, document)
        return 0
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
