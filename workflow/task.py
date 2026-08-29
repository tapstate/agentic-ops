#!/usr/bin/env python3
"""AgenticOps 项目工作空间任务工具：归一管理多个可并行激活的任务。

阶段（简化自 agentic-ops stages.yaml）：
  waiting_takeover -> task_intake -> design_review -> implementation
  -> pr_review -> ci_validation -> completed

硬约束（全部由本工具执行，不依赖 agent 自觉）：
  - 只能推进到相邻的下一阶段（不可跳跃、不可回退；重做用 reset）。
  - 离开 task_intake 必须集齐当前 Project admission.json 中该任务类型的
    全部必填 fact；缺项时 exit 3 并打印缺失项与补卡建议文案。
  - 进入 implementation 必须存在有效的 task_execution 授权（workflow/authorization.py 签发），
    且授权 issue_key 与任务一致。
  - 离开 implementation 必须记录 verification，且不得命中 admission.json
    verification_rules 的禁止模式（如 -DskipTests、"未验证"占位词）。
  - 每次推进必须 --note 说明推进依据（人工节点的确认内容）。

用法：
  python3 workflow/task.py init --issue-key TAP-123 --task-class defect_fix [--dir .]
  python3 workflow/task.py list [--dir .]
  python3 workflow/task.py checklist --issue-key TAP-123 [--json]
  python3 workflow/task.py branch --repo tapdata/tapdata      # 查表解析分支，禁止猜测
  python3 workflow/task.py record --issue-key TAP-123 --key problem_branch --value develop
  python3 workflow/task.py advance --issue-key TAP-123 --note "准入三项必填齐备，见 Jira 评论"
  python3 workflow/task.py block --issue-key TAP-123 --reason "缺问题版本，已写补卡评论"
  python3 workflow/task.py status --issue-key TAP-123
  python3 workflow/task.py reset --issue-key TAP-123 --stage design_review --note "计划实质变更，重新确认"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gate import engine  # noqa: E402
from workflow import project_rules, task_store  # noqa: E402

STAGES = [
    "waiting_takeover",
    "task_intake",
    "design_review",
    "implementation",
    "pr_review",
    "ci_validation",
    "completed",
]

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def admission(base):
    return project_rules.load_admission(workspace=base)


def profile(base):
    return project_rules.load_profile(workspace=base)


def resolve_issue(base, issue_key=None):
    return task_store.resolve_issue(base, issue_key)


def load(base, issue_key=None):
    issue = resolve_issue(base, issue_key)
    path = task_store.task_path(base, issue)
    if not path.is_file():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save(base, task):
    path = task_store.task_path(base, task["issue_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(str(temporary), str(path))


def revoke_authorization(base, issue_key, reason):
    path = task_store.authorization_path(base, issue_key)
    if not path.is_file():
        return
    auth = json.loads(path.read_text(encoding="utf-8"))
    auth["status"] = "revoked"
    auth["revoked_at"] = now()
    auth["revoked_reason"] = reason
    temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
    temporary.write_text(
        json.dumps(auth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(str(temporary), str(path))


def activation_conflicts(base, task):
    conflicts = []
    for other_issue in task_store.registered_issues(base, statuses=("active",)):
        if other_issue == task["issue_key"]:
            continue
        path = task_store.task_path(base, other_issue)
        if not path.is_file():
            continue
        other = json.loads(path.read_text(encoding="utf-8"))
        other_bindings = {
            (item.get("repository"), item.get("work_branch"))
            for item in other.get("repositories", [])
        }
        for item in task.get("repositories", []):
            binding = (item.get("repository"), item.get("work_branch"))
            if binding in other_bindings:
                conflicts.append("%s:%s（与 %s 冲突）" % (binding[0], binding[1], other_issue))
    return conflicts


def require(base, issue_key=None):
    issue = task_store.resolve_active_issue(base, issue_key)
    task = load(base, issue)
    if task is None:
        print("错误：没有任务状态（先执行 task.py init）", file=sys.stderr)
        sys.exit(2)
    return task


def cmd_init(args):
    try:
        issue = task_store.validate_issue_key(args.issue_key)
        project_rules.validate_project_issue(profile(args.dir), issue)
        spec = admission(args.dir)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    task_classes = tuple(sorted(spec["task_classes"]))
    if args.task_class not in task_classes:
        print("错误：task-class 必须是 %s 之一" % "/".join(task_classes), file=sys.stderr)
        return 2
    existing_path = task_store.task_path(args.dir, issue)
    existing = None
    if existing_path.is_file():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    if existing and not args.force:
        if existing.get("stage") == "completed":
            print(
                "错误：任务已完成，不能通过 init 重新激活；确需重开请使用 reset 并重新授权",
                file=sys.stderr,
            )
            return 2
        conflicts = activation_conflicts(args.dir, existing)
        if conflicts:
            print("错误：任务无法激活：%s" % "；".join(conflicts), file=sys.stderr)
            return 2
        print(
            "任务已存在：%s（阶段 %s）；已保持原状态并标记为 active"
            % (existing.get("issue_key"), existing.get("stage"))
        )
        task_store.register(args.dir, issue, status="active")
        return 0
    if existing and args.force:
        print(
            "错误：禁止覆盖已有任务状态；请使用 reset 保留历史并重新授权",
            file=sys.stderr,
        )
        return 2
    task = {
        "issue_key": issue,
        "task_class": args.task_class,
        "stage": STAGES[0],
        "facts": {},
        "repositories": [],
        "pending": None,
        "history": [{"ts": now(), "event": "init", "stage": STAGES[0]}],
    }
    save(args.dir, task)
    task_store.register(args.dir, issue, status="active")
    print("任务已初始化并激活：%s（%s），阶段 %s" % (issue, args.task_class, STAGES[0]))
    _print_next(task)
    return 0


def cmd_record(args):
    task = require(args.dir, args.issue_key)
    spec = admission(args.dir)
    valid = project_rules.known_fact_keys(spec, task["task_class"])
    if args.key not in valid and not args.force:
        print(
            "错误：%s 不是 %s 的已知 fact key。可用：%s\n"
            "（确需自定义键加 --force；改规则请改当前 Project admission.json）"
            % (args.key, task["task_class"], "、".join(valid)),
            file=sys.stderr,
        )
        return 2
    if not str(args.value).strip():
        print("错误：%s 的值为空，空值等于没记录" % args.key, file=sys.stderr)
        return 2
    task["facts"][args.key] = args.value
    task["history"].append({"ts": now(), "event": "record", "key": args.key, "value": args.value})
    save(args.dir, task)
    print("已记录：%s = %s" % (args.key, args.value))
    return 0


def cmd_repository_add(args):
    task = require(args.dir, args.issue_key)
    current_profile = profile(args.dir)
    try:
        branches = project_rules.resolve_branches(current_profile, args.repo)
    except LookupError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    if any(item.get("repository") == args.repo for item in task.get("repositories", [])):
        print("错误：仓库已加入当前任务：%s" % args.repo, file=sys.stderr)
        return 2
    for other_issue in task_store.registered_issues(args.dir, statuses=("active",)):
        if other_issue == task["issue_key"]:
            continue
        other_path = task_store.task_path(args.dir, other_issue)
        if not other_path.is_file():
            continue
        other = json.loads(other_path.read_text(encoding="utf-8"))
        if any(
            item.get("repository") == args.repo
            and item.get("work_branch") == args.work_branch
            for item in other.get("repositories", [])
        ):
            print(
                "错误：激活任务 %s 已绑定相同仓库和工作分支，无法唯一解析门禁上下文"
                % other_issue,
                file=sys.stderr,
            )
            return 2
    base_branch = args.base_branch or branches.get("baseline_branch")
    if not base_branch:
        print("错误：仓库 %s 没有可验证的基线分支" % args.repo, file=sys.stderr)
        return 2
    item = {
        "repository": args.repo,
        "work_branch": args.work_branch,
        "base_branch": base_branch,
        "approved_scope": args.scope,
        "verification_method": args.verification,
        "pull_request": None,
        "ci": None,
    }
    task.setdefault("repositories", []).append(item)
    task["history"].append(
        {"ts": now(), "event": "repository_add", "repository": args.repo}
    )
    save(args.dir, task)
    print("已加入任务仓库：%s（%s -> %s）" % (args.repo, base_branch, args.work_branch))
    return 0


def cmd_repository_list(args):
    task = require(args.dir, args.issue_key)
    print(json.dumps(task.get("repositories", []), ensure_ascii=False, indent=2))
    return 0


def repository_bindings(repositories):
    """只提取会影响授权有效性的稳定仓库绑定。"""
    keys = (
        "repository",
        "work_branch",
        "base_branch",
        "approved_scope",
        "verification_method",
    )
    return [{key: item.get(key) for key in keys} for item in repositories]


def cmd_repository_record(args):
    task = require(args.dir, args.issue_key)
    item = next(
        (repo for repo in task.get("repositories", []) if repo.get("repository") == args.repo),
        None,
    )
    if item is None:
        print("错误：仓库不在当前任务中：%s" % args.repo, file=sys.stderr)
        return 2
    if args.pr is None and args.ci is None:
        print("错误：至少提供 --pr 或 --ci", file=sys.stderr)
        return 2
    if args.pr is not None:
        item["pull_request"] = args.pr
    if args.ci is not None:
        item["ci"] = args.ci
    task["history"].append(
        {"ts": now(), "event": "repository_result", "repository": args.repo}
    )
    save(args.dir, task)
    print("已记录仓库结果：%s" % args.repo)
    return 0


def _check_advance(task, target, base, spec):
    """返回阻止推进的原因列表。"""
    problems = []
    if target == "design_review":
        missing = project_rules.missing_required(spec, task["task_class"], task.get("facts"))
        if missing:
            problems.append(
                "准入必填项缺失 %d 项：%s"
                % (len(missing), "、".join("%s(%s)" % (f["label"], f["key"]) for f in missing))
            )
            problems.append("补卡建议（一次列全写进 Jira 评论）：")
            for f in missing:
                problems.append("  - %s" % f["supplement"])
            problems.append(
                "补齐后 record 对应 fact 再 advance；现在应执行："
                'task.py block --issue-key %s --reason "准入缺项：%s"'
                % (task["issue_key"], "、".join(f["label"] for f in missing))
            )
    if target == "pr_review":
        verification = (task.get("facts") or {}).get("verification")
        for reason in project_rules.check_verification(spec, verification):
            problems.append("验证结论不合规：%s" % reason)
        if not verification:
            problems.append(
                '先执行：task.py record --issue-key %s --key verification --value "<命令 + 退出结果>"'
                % task["issue_key"]
            )
    if target == "implementation":
        if not task.get("repositories"):
            problems.append("进入 implementation 前至少确认一个任务仓库")
        auth, _ = engine.load_authorization_for_issue(base, task["issue_key"])
        context = {"branch_relevant": False}
        policy = engine.load_policy()
        valid, reasons = engine.check_authorization(auth, context, policy)
        if not valid:
            problems.append("进入 implementation 需要有效授权：%s" % "；".join(reasons))
        elif auth.get("issue_key") != task["issue_key"]:
            problems.append(
                "授权 issue_key（%s）与任务（%s）不一致" % (auth.get("issue_key"), task["issue_key"])
            )
        elif auth.get("repositories") != repository_bindings(task.get("repositories", [])):
            problems.append("授权仓库集合与当前任务仓库集合不一致")
    return problems


def cmd_advance(args):
    task = require(args.dir, args.issue_key)
    idx = STAGES.index(task["stage"])
    if idx + 1 >= len(STAGES):
        print("任务已在最终阶段 completed")
        return 0
    target = STAGES[idx + 1]
    problems = _check_advance(task, target, args.dir, admission(args.dir))
    if problems:
        for p in problems:
            print("阻止推进：%s" % p, file=sys.stderr)
        return 3
    if target == "completed":
        revoke_authorization(args.dir, task["issue_key"], "task_completed")
    task["stage"] = target
    task["pending"] = None
    task["history"].append({"ts": now(), "event": "advance", "stage": target, "note": args.note})
    save(args.dir, task)
    if target == "completed":
        task_store.set_status(args.dir, task["issue_key"], "completed")
    print("已推进到阶段：%s（依据：%s）" % (target, args.note))
    _print_next(task)
    return 0


def cmd_block(args):
    task = require(args.dir, args.issue_key)
    task["pending"] = {"ts": now(), "stage": task["stage"], "reason": args.reason}
    task["history"].append({"ts": now(), "event": "block", "reason": args.reason})
    save(args.dir, task)
    print("已记录 pending 门禁并停止：%s" % args.reason)
    print("恢复时先执行 task.py status --issue-key %s 查看 pending 正文。" % task["issue_key"])
    return 0


def cmd_reset(args):
    task = load(args.dir, args.issue_key)
    if task is None:
        print("错误：没有任务状态", file=sys.stderr)
        return 2
    if args.stage not in STAGES or args.stage == "completed":
        print("错误：未知阶段 %s" % args.stage, file=sys.stderr)
        return 2
    current_index = STAGES.index(task["stage"])
    target_index = STAGES.index(args.stage)
    if target_index > current_index:
        print(
            "错误：reset 只能回退或停留在当前阶段，不能从 %s 跳到 %s"
            % (task["stage"], args.stage),
            file=sys.stderr,
        )
        return 2
    revoke_authorization(args.dir, task["issue_key"], "task_reset")
    task["history"].append(
        {"ts": now(), "event": "reset", "from": task["stage"], "to": args.stage, "note": args.note}
    )
    task["stage"] = args.stage
    task["pending"] = None
    save(args.dir, task)
    task_store.set_status(args.dir, task["issue_key"], "active")
    print("已重置到阶段：%s（%s）；旧授权已撤销，进入实现前必须重新授权" % (args.stage, args.note))
    return 0


NEXT_GUIDE = {
    "waiting_takeover": "核对负责人/状态映射后 advance 进入 task_intake",
    "task_intake": "task.py checklist 列必填项 -> 逐项 record；缺项则写 Jira 补卡评论并 block（advance 会硬拦）",
    "design_review": "把方案（范围/验证方式/风险）写入 Jira，研发工程师确认后用 workflow/authorization.py 签发授权，再 advance",
    "implementation": "在授权范围内实现+测试；完成后 record --key verification（命令+退出结果）再 advance",
    "pr_review": "创建/更新 PR，展示变更、验证结果和风险，研发工程师在 PR 上审查后 advance",
    "ci_validation": "用 workflow/ci.py watch 观察必需检查；通过后 advance",
    "completed": "用 workflow/evidence.py 生成证据总结，经确认后作为 Jira 评论回写",
}


def _print_next(task):
    print("下一步：%s" % NEXT_GUIDE.get(task["stage"], ""))


def cmd_checklist(args):
    task = load(args.dir, args.issue_key) if args.issue_key or not args.task_class else None
    task_class = args.task_class or (task or {}).get("task_class")
    spec = admission(args.dir)
    task_classes = tuple(sorted(spec["task_classes"]))
    if not task_class:
        print("错误：请用 --issue-key 或 --task-class 指定（%s）" % "/".join(task_classes), file=sys.stderr)
        return 2
    try:
        cls = project_rules.class_spec(spec, task_class)
    except ValueError as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    facts = (task or {}).get("facts", {})
    if args.json:
        payload = {
            "task_class": task_class,
            "required_facts": cls.get("required_facts", []),
            "optional_facts": cls.get("optional_facts", []),
            "recorded": facts,
            "missing": [f["key"] for f in project_rules.missing_required(spec, task_class, facts)],
            "verification_rules": spec.get("verification_rules", {}),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("准入清单：%s（%s）" % (cls["title"], task_class))
    print("必填项（缺一不可，advance 硬拦）：")
    for f in cls.get("required_facts", []):
        got = facts.get(f["key"])
        mark = "✓" if got else "✗"
        print("  %s %s（--key %s）来源：%s" % (mark, f["label"], f["key"], f.get("source", "")))
        if got:
            print("      已记录：%s" % got)
        else:
            print("      补卡建议：%s" % f["supplement"])
    optional = cls.get("optional_facts", [])
    if optional:
        print("可选项（有则记录）：")
        for f in optional:
            print("  - %s（--key %s）%s" % (f["label"], f["key"], f.get("note", "")))
    missing = project_rules.missing_required(spec, task_class, facts)
    print("结论：%s" % ("缺 %d 项，不得离开 task_intake" % len(missing) if missing else "必填项齐备"))
    return 0


def cmd_branch(args):
    try:
        info = project_rules.resolve_branches(profile(args.dir), args.repo)
    except (LookupError, ValueError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    print("仓库：%s" % info["repository"])
    print("基线分支（baseline）：%s" % (info["baseline_branch"] or "未登记"))
    print("开发分支（dev）：%s" % (info["dev_branch"] or "未登记"))
    return 0


def cmd_status(args):
    try:
        task = load(args.dir, args.issue_key)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    if task is None:
        print("无任务状态。")
        return 0
    print("任务：%s（%s）" % (task["issue_key"], task["task_class"]))
    print("阶段：%s" % task["stage"])
    if task.get("pending"):
        p = task["pending"]
        print("⚠ pending 门禁（%s @ %s）：%s" % (p["stage"], p["ts"], p["reason"]))
    if task.get("facts"):
        print("已记录事实：")
        for k, v in task["facts"].items():
            print("  - %s: %s" % (k, v))
    if task.get("repositories"):
        print("任务仓库：")
        for item in task["repositories"]:
            print(
                "  - %s：%s -> %s"
                % (item["repository"], item["base_branch"], item["work_branch"])
            )
    _print_next(task)
    return 0


def cmd_list(args):
    registry = task_store.load_registry(args.dir, create=False)
    if registry is None or not registry["tasks"]:
        print("工作空间暂无任务。")
        return 0
    print("项目：%s" % registry["project"])
    for issue, entry in sorted(registry["tasks"].items()):
        path = task_store.task_path(args.dir, issue)
        task = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        print("- %s：%s，阶段 %s" % (issue, entry.get("status"), task.get("stage", "状态缺失")))
    return 0


def cmd_activate(args):
    try:
        task = load(args.dir, args.issue_key)
        if task is None:
            raise ValueError("任务状态缺失：%s" % args.issue_key)
        if task and task.get("stage") == "completed":
            raise ValueError("任务已完成；确需重开请使用 reset 并重新授权")
        conflicts = activation_conflicts(args.dir, task)
        if conflicts:
            raise ValueError("任务无法激活：%s" % "；".join(conflicts))
        task_store.set_status(args.dir, args.issue_key, "active")
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("任务已激活：%s" % task_store.validate_issue_key(args.issue_key))
    return 0


def cmd_deactivate(args):
    try:
        registry = task_store.load_registry(args.dir, create=False)
        issue = task_store.validate_issue_key(args.issue_key)
        if registry and registry["tasks"].get(issue, {}).get("status") == "completed":
            raise ValueError("completed 任务无需停用")
        task_store.set_status(args.dir, args.issue_key, "inactive")
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("任务已停用：%s（状态和授权仍保留，但 Gate 不再加载）" % task_store.validate_issue_key(args.issue_key))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--task-class", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("record")
    p.add_argument("--issue-key")
    p.add_argument("--key", required=True)
    p.add_argument("--value", required=True)
    p.add_argument("--force", action="store_true", help="允许记录清单外的自定义 fact key")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("checklist")
    p.add_argument("--issue-key")
    p.add_argument("--task-class", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_checklist)

    p = sub.add_parser("branch")
    p.add_argument("--repo", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_branch)

    p = sub.add_parser("repository")
    repository_sub = p.add_subparsers(dest="repository_cmd", required=True)
    add = repository_sub.add_parser("add")
    add.add_argument("--issue-key")
    add.add_argument("--repo", required=True)
    add.add_argument("--work-branch", required=True)
    add.add_argument("--base-branch")
    add.add_argument("--scope", required=True)
    add.add_argument("--verification", required=True)
    add.add_argument("--dir", default=".")
    add.set_defaults(func=cmd_repository_add)
    listing = repository_sub.add_parser("list")
    listing.add_argument("--issue-key")
    listing.add_argument("--dir", default=".")
    listing.set_defaults(func=cmd_repository_list)
    record = repository_sub.add_parser("record-result")
    record.add_argument("--issue-key")
    record.add_argument("--repo", required=True)
    record.add_argument("--pr")
    record.add_argument("--ci")
    record.add_argument("--dir", default=".")
    record.set_defaults(func=cmd_repository_record)

    p = sub.add_parser("advance")
    p.add_argument("--issue-key")
    p.add_argument("--note", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_advance)

    p = sub.add_parser("block")
    p.add_argument("--issue-key")
    p.add_argument("--reason", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_block)

    p = sub.add_parser("reset")
    p.add_argument("--issue-key")
    p.add_argument("--stage", required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("status")
    p.add_argument("--issue-key")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("list")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("activate")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("deactivate")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_deactivate)

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
