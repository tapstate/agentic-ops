#!/usr/bin/env python3
"""读取并校验 AgenticOps 源码仓库的资源合同。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_KINDS = {"tool", "product_state"}
REQUIRED_FIELDS = {
    "schema_version",
    "allowed_root_entries",
    "ignored_root_entries",
    "forbidden_ignored_root_entries",
    "other_gitignore_patterns",
}


def fail(message):
    raise ValueError("资源合同无效：%s" % message)


def root_name(value, field):
    if not isinstance(value, str) or not value or "/" in value or value in {".", ".."}:
        fail("%s 必须是单个根目录项" % field)
    return value


def ignored_entry(entry, field, require_kind=False):
    if not isinstance(entry, dict):
        fail("%s 必须是对象" % field)
    expected = {"path", "gitignore_pattern"}
    if require_kind:
        expected.add("kind")
    if set(entry) != expected:
        fail("%s 字段不正确" % field)
    path = root_name(entry.get("path"), "%s.path" % field)
    pattern = entry.get("gitignore_pattern")
    if not isinstance(pattern, str) or pattern != path + "/":
        fail("%s.gitignore_pattern 必须精确为 %s/" % (field, path))
    if require_kind and entry.get("kind") not in ROOT_KINDS:
        fail("%s.kind 无效" % field)
    return entry


def load(path):
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("资源合同无法读取：%s" % error) from error
    if not isinstance(document, dict) or set(document) != REQUIRED_FIELDS:
        fail("顶层字段不正确")
    if document.get("schema_version") != 1:
        fail("schema_version 必须为 1")
    allowed = document["allowed_root_entries"]
    if not isinstance(allowed, list):
        fail("allowed_root_entries 必须是数组")
    allowed = [root_name(item, "allowed_root_entries") for item in allowed]
    ignored = [
        ignored_entry(item, "ignored_root_entries", require_kind=True)
        for item in document["ignored_root_entries"]
    ]
    forbidden = [
        ignored_entry(item, "forbidden_ignored_root_entries")
        for item in document["forbidden_ignored_root_entries"]
    ]
    other = document["other_gitignore_patterns"]
    if not isinstance(other, list) or not all(isinstance(item, str) and item for item in other):
        fail("other_gitignore_patterns 必须是非空字符串数组")

    allowed_roots = allowed + [item["path"] for item in ignored]
    forbidden_roots = [item["path"] for item in forbidden]
    patterns = [item["gitignore_pattern"] for item in ignored + forbidden] + other
    if len(allowed_roots) != len(set(allowed_roots)):
        fail("允许的根目录项重复")
    if len(forbidden_roots) != len(set(forbidden_roots)):
        fail("禁止的忽略根目录项重复")
    if set(allowed_roots) & set(forbidden_roots):
        fail("同一根目录项不能同时允许和禁止")
    if len(patterns) != len(set(patterns)):
        fail("gitignore 模式重复")
    return {
        "allowed_roots": allowed_roots,
        "tool_roots": [item["path"] for item in ignored if item["kind"] == "tool"],
        "patterns": patterns,
    }


def gitignore_patterns(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(".gitignore 无法读取：%s" % error) from error
    return [line for line in lines if line and not line.startswith("#")]


def validate_gitignore(contract, path):
    actual = gitignore_patterns(path)
    if set(actual) != set(contract["patterns"]) or len(actual) != len(set(actual)):
        fail(".gitignore 与资源合同的模式集合不一致")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument("--gitignore", required=True)
    subcommands.add_parser("allowed-root")
    subcommands.add_parser("tool-root")
    args = parser.parse_args()
    try:
        contract = load(args.contract)
        if args.command == "validate":
            validate_gitignore(contract, args.gitignore)
            return 0
        if args.command == "allowed-root":
            print("\n".join(sorted(contract["allowed_roots"])))
            return 0
        print("\n".join(sorted(contract["tool_roots"])))
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
