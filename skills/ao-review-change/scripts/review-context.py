#!/usr/bin/env python3
"""只读摘要现有 Story Gate 输出；不判定规则、不创建验收或批准。"""
import argparse
import json
from pathlib import Path
import subprocess


FIELDS = (
    "ok", "operation", "code", "status", "message", "required_human_action",
    "change_source", "changed_paths", "impacted_story_ids", "unmapped_paths",
    "impact_id", "current_branch", "target_branch", "comparison_base",
    "candidate_tree", "change_fingerprint", "commit_sha", "pr_url", "pr_head_sha",
    "acceptance_checks", "acceptance_status", "acceptance_evidence", "approved", "approval_ready",
    "confirmation_required", "review_channel", "review_report_digest",
)


def collect(root, source, base=None, head=None):
    root = Path(root).resolve()
    entry = root / "internal/bin/story-gate"
    if not (root / ".agentic-ops-source").is_file() or not entry.is_file():
        raise ValueError("只支持包含现役 Story Gate 的源码产品根")
    command = [str(entry), "impact", "--change-source", source]
    if base is not None:
        command.extend(["--base", base])
    if head is not None:
        command.extend(["--head", head])
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
    try:
        payload = json.loads(result.stdout if result.returncode == 0 else result.stderr)
    except (ValueError, UnicodeError) as error:
        raise ValueError("Story Gate 未返回可读 JSON；请直接运行原入口排查") from error
    if not isinstance(payload, dict) or type(payload.get("ok")) is not bool:
        raise ValueError("Story Gate 结果缺少有效 ok 字段")
    summary = {key: payload[key] for key in FIELDS if key in payload}
    report = payload.get("review_report")
    if isinstance(report, dict):
        for key in ("review_object", "confirmation_items", "risks"):
            if key in report:
                summary[key] = report[key]
    summary["source_root"] = str(root)
    return summary, result.returncode or (0 if payload["ok"] else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--change-source", choices=("staged", "worktree", "range"), required=True)
    parser.add_argument("--base")
    parser.add_argument("--head")
    args = parser.parse_args()
    if args.change_source == "range" and (not args.base or not args.head):
        parser.error("range 必须提供 --base 与 --head")
    try:
        summary, code = collect(Path(__file__).resolve().parents[3], args.change_source, args.base, args.head)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        summary, code = {"ok": False, "code": "review_context_unavailable", "message": str(error)}, 1
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
