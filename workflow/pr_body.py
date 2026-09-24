#!/usr/bin/env python3
"""只读预检 PR 正文，或与原生工具回读的 JSON 比对；不执行外部写入。"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def normalize(body):
    # 不使用 universal-newline 读取、strip 或反转义，避免隐藏内容损坏。
    return body.replace("\r\n", "\n")


def read_utf8(path):
    return Path(path).read_bytes().decode("utf-8")


def preflight(body):
    body = normalize(body)
    errors = []
    if not body.strip():
        errors.append("empty_body")
    if "\x00" in body or "\r" in body or body.startswith("\ufeff"):
        errors.append("unsupported_control_character")
    warnings = []
    for match in re.finditer(r"\\n(?=[ \t]*(?:#{1,6} |[-*+] |\d+[.)] |\\n|$))", body):
        warnings.append({
            "code": "suspected_escaped_layout",
            "line": body.count("\n", 0, match.start()) + 1,
            "column": match.start() - body.rfind("\n", 0, match.start()),
        })
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "actual_newlines": body.count("\n"),
            "guidance": "疑似字面转义仅提示；核对原文意图，不自动替换。通过仅证明格式可读取，不证明内容正确。"}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def compare(body, snapshot, repository, number, host="github.com"):
    result = preflight(body)
    result["operation"] = "compare"
    result["matched"] = False
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("invalid_repository")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or number < 1:
        raise ValueError("invalid_target")
    if not isinstance(snapshot, dict):
        raise ValueError("invalid_snapshot")
    url = snapshot.get("url")
    if not isinstance(url, str):
        result["errors"].append("missing_pr_url")
    else:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc.lower() != host.lower()
                or parsed.path.lower() != "/%s/pull/%s" % (repository.lower(), number)
                or parsed.query or parsed.fragment):
            result["errors"].append("pr_target_mismatch")
    if type(snapshot.get("number")) is not int or snapshot["number"] != number:
        result["errors"].append("pr_number_mismatch")
    actual = snapshot.get("body")
    if not isinstance(actual, str):
        result["errors"].append("missing_pr_body")
    elif normalize(body) != normalize(actual):
        result["errors"].append("body_mismatch")
        result["actual_body_sha256"] = hashlib.sha256(normalize(actual).encode("utf-8")).hexdigest()
    result["ok"] = not result["errors"]
    result["matched"] = result["ok"]
    result["guidance"] = "仅核对所提供快照的目标与正文；来源、新鲜度及内容正确性由 Agent 核验。未知外部结果先回读原 PR，不重建或盲目重发。"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("preflight", help="预检 UTF-8 Markdown 正文")
    check.add_argument("--body-file", required=True)
    verify = commands.add_parser("compare", help="比对 gh pr view --json number,url,body 的原生回读")
    verify.add_argument("--body-file", required=True)
    verify.add_argument("--readback", required=True)
    verify.add_argument("--repository", required=True)
    verify.add_argument("--pr", required=True, type=int)
    verify.add_argument("--host", default="github.com")
    args = parser.parse_args(argv)
    try:
        body = read_utf8(args.body_file)
        if args.command == "preflight":
            result = dict(preflight(body), operation="preflight")
        else:
            snapshot = json.loads(read_utf8(args.readback), object_pairs_hook=unique_object)
            result = compare(body, snapshot, args.repository, args.pr, args.host)
        exit_code = 0 if result["ok"] else 3
    except (OSError, ValueError, UnicodeError) as error:
        # 不回显输入正文、路径或底层异常中的敏感片段。
        result = {"ok": False, "operation": args.command, "errors": ["input_unreadable_or_invalid"],
                  "error_type": type(error).__name__, "guidance": "检查输入文件、UTF-8、JSON 与目标参数；未取得有效回读不能宣称匹配。"}
        exit_code = 4
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
