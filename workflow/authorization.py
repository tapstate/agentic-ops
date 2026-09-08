#!/usr/bin/env python3
"""任务授权工具：按当前任务的多仓范围签发或撤销授权伞。

签发即模拟"设计审查通过"这一人工节点：授权绑定任务、仓库、分支和计划版本，
写入 `.agenticops/tasks/<issue-key>/authorization.json`。Workflow 在实现和验收检查点
重新核验确认绑定；它不代表 Git/Jira/PR 每次原生调用均经过 AgenticOps 授权。

用法：
  python3 workflow/authorization.py grant --issue-key TAP-123 --agent-id dev-bot-1 \
      --expected-run-id <当前-run-id> --plan-version v1 [--ttl-hours 8] [--dir <workspace>]
  python3 workflow/authorization.py revoke --issue-key TAP-123 --expected-run-id <当前-run-id> [--dir <workspace>]
  python3 workflow/authorization.py show   --issue-key TAP-123 [--dir <workspace>]
  python3 workflow/authorization.py show --issue-key TAP-123 --digest [--dir <workspace>]
  python3 workflow/authorization.py renew --issue-key TAP-123 --expected-run-id <当前-run-id> \
      --expected-authorization-digest <确认前摘要> --confirmed-by <决定者> --confirmation-ref <确认来源> [--ttl-hours 8]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import project_rules, quality, task_store  # noqa: E402
from gate import engine  # noqa: E402


def repository_bindings(repositories):
    keys = (
        "repository",
        "authorized_endpoint",
        "work_branch",
        "base_branch",
        "approved_scope",
        "verification_method",
        "base_sha",
    )
    return [{key: item.get(key) for key in keys} for item in repositories]


def plan_digest(task):
    value = json.dumps(task.get("facts", {}).get("fix_plan"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def record_digest(record):
    return hashlib.sha256(json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def check_catalog_bindings(base, repositories):
    catalog = project_rules.load_repository_catalog(workspace=base)
    for item in repositories:
        entry = catalog.get("repositories", {}).get(item["repository"])
        endpoint = project_rules.canonical_repository_endpoint(entry.get("origin") if isinstance(entry, dict) else None)
        if not endpoint or item.get("authorized_endpoint") != endpoint:
            raise ValueError("授权仓库 endpoint 与当前 Project catalog 不一致：%s" % item["repository"])


@task_store.task_mutation
def cmd_renew(args):
    issue = task_store.resolve_active_issue(args.dir, args.issue_key)
    task = task_store.check_expected_run(args.dir, issue, args.expected_run_id)
    if task.get("stage") not in ("design_review", "implementation", "pr_review", "ci_validation"):
        raise ValueError("当前阶段不允许续签")
    if not args.confirmed_by.strip() or not args.confirmation_ref.strip():
        raise ValueError("续签需要明确决定者和可回查的人工确认来源")
    if not math.isfinite(args.ttl_hours) or args.ttl_hours <= 0:
        raise ValueError("续签 ttl-hours 必须为有限正数")
    path = task_store.authorization_path(args.dir, issue)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("原授权无法读取，拒绝续签") from error
    if not isinstance(record, dict) or record_digest(record) != args.expected_authorization_digest:
        raise ValueError("原授权已变化，重新核对并确认后续签")
    q1_digest, q2_digest = record.get("approved_q1_digest"), record.get("approved_q2_digest")
    if not isinstance(q1_digest, str) or not q1_digest or not isinstance(q2_digest, str) or not q2_digest:
        raise ValueError("旧授权缺少有效 Q1/Q2 方案确认摘要；可在原有效期内继续，但续签前必须重新确认方案")
    expiry = record.get("expires_at_epoch")
    if type(expiry) not in (int, float) or not math.isfinite(expiry):
        raise ValueError("原授权有效期无效，拒绝续签")
    # 只忽略已经到期这一项，其余既有授权约束继续核验。
    valid, reasons = engine.check_authorization(record, {"issue_key": issue, "branch_relevant": False},
                                                engine.load_policy(), now=expiry)
    if not valid:
        raise ValueError("原授权不能续签：%s" % "；".join(reasons))
    if (record.get("agentic_run_id") != task["run_id"]
            or record.get("repositories") != repository_bindings(task.get("repositories", []))
            or record.get("approved_plan_digest") != plan_digest(task)
            or q1_digest != quality.q1_digest(args.dir, task)
            or q2_digest != quality.q2_digest(args.dir, task)):
        raise ValueError("方案、run 或仓库绑定已变化（或旧授权缺少方案摘要），必须重新设计确认")
    check_catalog_bindings(args.dir, record["repositories"])
    renewed_expiry = time.time() + args.ttl_hours * 3600
    if not math.isfinite(renewed_expiry) or renewed_expiry <= expiry:
        raise ValueError("续签有效期必须晚于原有效期")
    history = record.setdefault("renewals", [])
    if not isinstance(history, list):
        raise ValueError("原授权续签历史无效")
    history.append({"confirmed_by": args.confirmed_by, "confirmation_ref": args.confirmation_ref,
                    "renewed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "previous_expires_at_epoch": expiry, "expires_at_epoch": renewed_expiry,
                    "previous_authorization_digest": args.expected_authorization_digest})
    record["expires_at_epoch"] = renewed_expiry
    task_store._write_json_atomic(path, record)
    print("已续签当前 run 的原方案确认：%s" % path)
    return 0


@task_store.task_mutation
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
    try:
        approved_q1_digest = quality.q1_digest(args.dir, task)
        approved_q2_digest = quality.q2_digest(args.dir, task)
    except ValueError as error:
        print("错误：签发授权前必须完成 Q1/Q2 且方案无缺口：%s" % error, file=sys.stderr)
        return 2
    repositories = task.get("repositories")
    if not isinstance(repositories, list) or not repositories:
        print("错误：授权前至少确认一个任务仓库", file=sys.stderr)
        return 2
    try:
        catalog = project_rules.load_repository_catalog(workspace=args.dir)
    except ValueError as error:
        print("错误：无法核验授权仓库 endpoint：%s" % error, file=sys.stderr)
        return 2
    for item in repositories:
        if not isinstance(item, dict):
            print("错误：任务仓库绑定不是对象", file=sys.stderr)
            return 2
        repository = item.get("repository")
        entry = catalog.get("repositories", {}).get(repository)
        expected_endpoint = project_rules.canonical_repository_endpoint(
            entry.get("origin") if isinstance(entry, dict) else None
        )
        if not expected_endpoint or item.get("authorized_endpoint") != expected_endpoint:
            print(
                "错误：任务仓库 %s 缺少当前 Project catalog 的可信 authorized_endpoint；"
                "请先执行受控 repository prepare 迁移，或清理后重新登记仓库"
                % repository,
                file=sys.stderr,
            )
            return 2
    path = task_store.authorization_path(args.dir, issue)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "scope": "task_execution",
        "status": "active",
        "issue_key": issue,
        "agentic_run_id": task["run_id"],
        "enforcement": "workflow_checkpoints",
        "agent_id": args.agent_id,
        "approved_plan_version": args.plan_version,
        "approved_plan_digest": plan_digest(task),
        "approved_q1_digest": approved_q1_digest,
        "approved_q2_digest": approved_q2_digest,
        "repositories": repository_bindings(repositories),
        "granted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "expires_at_epoch": time.time() + args.ttl_hours * 3600,
    }
    task_store._write_json_atomic(path, record)
    print("已签发授权：%s" % path)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


@task_store.task_mutation
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
    task_store._write_json_atomic(path, record)
    print("已撤销授权：%s" % path)
    return 0


def cmd_show(args):
    issue = task_store.resolve_issue(args.dir, args.issue_key)
    path = task_store.authorization_path(args.dir, issue)
    if not path.is_file():
        print("无授权文件：%s" % path)
        return 0
    content = path.read_text(encoding="utf-8")
    print(record_digest(json.loads(content)) if getattr(args, "digest", False) else content)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_grant = sub.add_parser("grant")
    p_grant.add_argument("--issue-key", required=True)
    p_grant.add_argument("--expected-run-id", required=True)
    p_grant.add_argument("--agent-id", required=True)
    p_grant.add_argument("--plan-version", required=True)
    p_grant.add_argument("--ttl-hours", type=float, default=8)
    p_grant.add_argument("--dir", default=".")
    p_grant.set_defaults(func=cmd_grant)

    p_renew = sub.add_parser("renew")
    p_renew.add_argument("--issue-key", required=True)
    p_renew.add_argument("--expected-run-id", required=True)
    p_renew.add_argument("--expected-authorization-digest", required=True)
    p_renew.add_argument("--confirmed-by", required=True)
    p_renew.add_argument("--confirmation-ref", required=True)
    p_renew.add_argument("--ttl-hours", type=float, default=8)
    p_renew.add_argument("--dir", default=".")
    p_renew.set_defaults(func=cmd_renew)

    p_revoke = sub.add_parser("revoke")
    p_revoke.add_argument("--issue-key")
    p_revoke.add_argument("--expected-run-id", required=True)
    p_revoke.add_argument("--dir", default=".")
    p_revoke.set_defaults(func=cmd_revoke)

    p_show = sub.add_parser("show")
    p_show.add_argument("--issue-key")
    p_show.add_argument("--digest", action="store_true")
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
