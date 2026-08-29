#!/usr/bin/env python3
"""任务授权工具：按当前任务的多仓范围签发或撤销授权伞。

签发即模拟"设计审查通过"这一人工节点：授权绑定任务、仓库、分支和计划版本，
写入 `.gate/tasks/<issue-key>/authorization.json`。任何绑定不匹配时 Hook 会自动收回
放行；停用一个任务不会删除其授权，但 Gate 只加载 active 任务。

用法：
  python3 workflow/authorization.py grant --issue-key TAP-123 --agent-id dev-bot-1 \
      --plan-version v1 [--ttl-hours 8] [--dir <workspace>]
  python3 workflow/authorization.py revoke --issue-key TAP-123 [--dir <workspace>]
  python3 workflow/authorization.py show   --issue-key TAP-123 [--dir <workspace>]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import task_store  # noqa: E402


def repository_bindings(repositories):
    keys = (
        "repository",
        "work_branch",
        "base_branch",
        "approved_scope",
        "verification_method",
    )
    return [{key: item.get(key) for key in keys} for item in repositories]


def cmd_grant(args):
    issue = task_store.validate_issue_key(args.issue_key)
    if issue not in task_store.registered_issues(args.dir, statuses=("active",)):
        print("错误：只能为 active 任务签发授权：%s" % issue, file=sys.stderr)
        return 2
    current_task_path = task_store.task_path(args.dir, issue)
    if not current_task_path.is_file():
        print("错误：没有任务状态，请先初始化任务并确认仓库范围", file=sys.stderr)
        return 2
    try:
        task = json.loads(current_task_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print("错误：任务状态无法读取：%s" % exc, file=sys.stderr)
        return 2
    if task.get("issue_key") != issue:
        print("错误：授权任务与当前任务不一致", file=sys.stderr)
        return 2
    if task.get("stage") != "design_review":
        print("错误：只能在 design_review 阶段签发任务授权", file=sys.stderr)
        return 2
    repositories = task.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        print("错误：授权前至少确认一个任务仓库", file=sys.stderr)
        return 2
    path = task_store.authorization_path(args.dir, issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "scope": "task_execution",
        "status": "active",
        "issue_key": issue,
        "agentic_run_id": "run-" + uuid.uuid4().hex[:12],
        "agent_id": args.agent_id,
        "approved_plan_version": args.plan_version,
        "repositories": repository_bindings(repositories),
        "granted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "expires_at_epoch": time.time() + args.ttl_hours * 3600,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    print("已签发授权：%s" % path)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def cmd_revoke(args):
    issue = task_store.resolve_issue(args.dir, args.issue_key)
    path = task_store.authorization_path(args.dir, issue)
    if not path.is_file():
        print("没有可撤销的授权：%s" % path)
        return 0
    with open(path, "r", encoding="utf-8") as fh:
        record = json.load(fh)
    record["status"] = "revoked"
    record["revoked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
    print("已撤销授权：%s" % path)
    return 0


def cmd_show(args):
    issue = task_store.resolve_issue(args.dir, args.issue_key)
    path = task_store.authorization_path(args.dir, issue)
    if not path.is_file():
        print("无授权文件：%s" % path)
        return 0
    print(path.read_text(encoding="utf-8"))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_grant = sub.add_parser("grant")
    p_grant.add_argument("--issue-key", required=True)
    p_grant.add_argument("--agent-id", required=True)
    p_grant.add_argument("--plan-version", required=True)
    p_grant.add_argument("--ttl-hours", type=float, default=8)
    p_grant.add_argument("--dir", default=".")
    p_grant.set_defaults(func=cmd_grant)

    p_revoke = sub.add_parser("revoke")
    p_revoke.add_argument("--issue-key")
    p_revoke.add_argument("--dir", default=".")
    p_revoke.set_defaults(func=cmd_revoke)

    p_show = sub.add_parser("show")
    p_show.add_argument("--issue-key")
    p_show.add_argument("--dir", default=".")
    p_show.set_defaults(func=cmd_show)

    args = parser.parse_args()
    try:
        task_store.workspace_project(args.dir)
        task_store.migrate_legacy(args.dir)
        return args.func(args)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
