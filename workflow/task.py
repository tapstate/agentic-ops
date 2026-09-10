#!/usr/bin/env python3
"""AgenticOps 项目工作空间任务工具：归一管理多个可并行激活的任务。

阶段（简化自 agentic-ops stages.yaml）：
  waiting_takeover -> task_intake -> design_review -> implementation
  -> pr_review -> ci_validation -> completed

硬约束（全部由本工具执行，不依赖 agent 自觉）：
  - 只能推进到相邻的下一阶段（不可跳跃、不可回退；重做用 reset）。
  - 离开 task_intake 必须集齐当前 Project admission.json 中该任务类型的
    必填 fact；recorded_decision 类型改为披露缺项并在质量检查点记录处置。
  - 进入 implementation 必须存在有效的 task_execution 授权（workflow/authorization.py 签发），
    且授权 issue_key 与任务一致。
  - 离开 implementation 必须记录 verification，且不得命中 admission.json
    verification_rules 的禁止模式；recorded_decision 类型由 quality.py 的用户处置替代文本门禁。
  - 每次推进必须 --note 说明推进依据（人工节点的确认内容）。

用法：
  python3 workflow/task.py init --issue-key TAP-123 --task-class defect_fix [--dir .]
  python3 workflow/task.py list [--dir .]
  python3 workflow/task.py checklist --issue-key TAP-123 [--json]
  python3 workflow/task.py branch --repo tapdata/tapdata      # 查表解析分支，禁止猜测
  python3 workflow/task.py repository context --issue-key TAP-123 --json
  python3 workflow/task.py record --issue-key TAP-123 --expected-run-id <当前-run-id> --key problem_branch --value develop
  python3 workflow/task.py advance --issue-key TAP-123 --expected-run-id <当前-run-id> \
    --expected-stage task_intake --note "准入三项必填齐备，见 Jira 评论"
  python3 workflow/task.py block --issue-key TAP-123 --expected-run-id <当前-run-id> --reason "缺问题版本，已写补卡评论"
  python3 workflow/task.py status --issue-key TAP-123
  python3 workflow/task.py interaction-path --issue-key TAP-123 --expected-run-id <当前-run-id> \
    --name jira-snapshot.json
  python3 workflow/task.py reset --issue-key TAP-123 --expected-run-id <当前-run-id> \
    --stage design_review --note "计划实质变更，重新确认"
  python3 workflow/task.py purge --issue-key TAP-123 --expected-run-id <当前-run-id> --yes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gate import engine  # noqa: E402
from workflow import authorization, issue_versions, jira_watermark, project_rules, quality, repair_strategy, repository_worktree, task_store  # noqa: E402

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
    if auth.get("status") == "revoked" and auth.get("revoked_reason") == reason:
        return
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
    try:
        with task_store.task_run_lock(args.dir, issue):
            existing_path = task_store.task_path(args.dir, issue)
            existing = None
            if existing_path.is_file():
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if existing and not args.force:
                if existing.get("stage") == "completed":
                    print(
                        "错误：任务已完成，不能通过 init 重新激活；确需重开请先确认并使用 reset",
                        file=sys.stderr,
                    )
                    return 2
                prepared = [
                    item.get("repository")
                    for item in existing.get("repositories", [])
                    if (item.get("worktree") or {}).get("status") == "prepared"
                ]
                print(
                    "任务已被接管：%s（阶段 %s，run=%s）"
                    % (existing.get("issue_key"), existing.get("stage"), existing.get("run_id")),
                    file=sys.stderr,
                )
                if prepared:
                    print("当前 run 已有 worktree：%s" % "、".join(prepared), file=sys.stderr)
                print(
                    "请选择：继续现有 run 请执行 task.py activate --issue-key %s；"
                    "清理重做请先执行 repository cleanup，再显式执行 reset "
                    "--expected-run-id %s。未做选择，状态未改变。"
                    % (issue, existing.get("run_id")),
                    file=sys.stderr,
                )
                return 3
            if existing and args.force:
                print(
                    "错误：禁止覆盖已有任务状态；请先清理当前 run，再使用 reset 保留历史并重新授权",
                    file=sys.stderr,
                )
                return 2
            task = {
                "issue_key": issue,
                "run_id": "run-" + uuid.uuid4().hex[:12],
                "task_class": args.task_class,
                "stage": STAGES[0],
                "facts": {},
                "repositories": [],
                "pending": None,
                "history": [{"ts": now(), "event": "init", "stage": STAGES[0]}],
            }
            save(args.dir, task)
            task_store.register(args.dir, issue, status="active")
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("任务已初始化并激活：%s（%s），阶段 %s" % (issue, args.task_class, STAGES[0]))
    _print_next(task)
    return 0


@task_store.task_mutation
def cmd_snapshot(args):
    task = require(args.dir, args.issue_key)
    if task["facts"].get("jira_snapshot"):
        print("当前 run 已保存 Jira 初始快照，保留原记录。")
        return 0
    try:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("无法读取初始快照：%s" % error) from error
    if not isinstance(payload, dict):
        raise ValueError("初始快照必须是对象")
    issue = payload.get("issue", {})
    if (not isinstance(issue, dict) or issue.get("key") != task["issue_key"]
            or not isinstance(issue.get("fields"), dict)
            or not isinstance(payload.get("source_ref"), str) or not payload["source_ref"].strip()):
        raise ValueError("初始快照必须包含当前 issue.key、fields 和 source_ref")
    if project_rules.scan_sensitive(admission(args.dir), json.dumps(payload, ensure_ascii=False)):
        raise ValueError("初始快照含敏感内容，请先脱敏")
    task["facts"]["jira_snapshot"] = payload
    task["history"].append({"ts": now(), "event": "jira_snapshot", "source_ref": payload["source_ref"]})
    save(args.dir, task)
    print("已保存当前 run 的 Jira 初始快照。")
    return 0


@task_store.task_mutation
def cmd_record(args):
    task = require(args.dir, args.issue_key)
    if args.key == repair_strategy.OVERRIDE_FACT:
        raise ValueError("修复策略只允许通过 repair-strategy set/clear 修改")
    if args.key in (issue_versions.FACT, "jira_snapshot", "agenticops_version") or (args.key == "problem_version" and issue_versions.rules(args.dir, task)):
        raise ValueError("初始快照和版本规划不允许 record 或 --force 覆盖；用 issue-versions 提交有效版本及用户确认来源")
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
    value = args.value
    if getattr(args, "input", None):
        if args.key != "fix_plan":
            raise ValueError("--input 仅用于以 JSON 对象记录 fix_plan")
        try:
            value = json.loads(Path(args.input).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("无法读取 fix_plan JSON：%s" % error) from error
        if not isinstance(value, dict):
            raise ValueError("fix_plan JSON 必须是对象")
    if not (isinstance(value, dict) or str(value).strip()):
        print("错误：%s 的值为空，空值等于没记录" % args.key, file=sys.stderr)
        return 2
    task["facts"][args.key] = value
    task["history"].append({"ts": now(), "event": "record", "key": args.key, "value": value})
    save(args.dir, task)
    print("已记录：%s = %s" % (args.key, json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value))
    return 0


def _strategy_payload(base, task):
    return repair_strategy.resolve(base, task)


def cmd_repair_strategy_list(args):
    try:
        catalog = repair_strategy.list_strategies(args.dir)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("修复策略配置不可用；原任务流程不受影响：%s" % error, file=sys.stderr)
        return 2
    print("缺陷修复策略：")
    for item in catalog["strategies"]:
        suffix = "（公司默认）" if item["id"] == catalog["default"] else ""
        print("  - %s %s%s：%s" % (item["id"], item["label"], suffix, item["description"]))
        for guidance in item["guidance"]:
            print("      · %s" % guidance)
    return 0


def cmd_repair_strategy_show(args):
    task = require(args.dir, args.issue_key)
    value = _strategy_payload(args.dir, task)
    print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else _strategy_summary(value))
    return 0


def _strategy_summary(value):
    if not value.get("applicable"):
        return "修复策略：不适用"
    if not value.get("available"):
        return "修复策略：暂不可用（%s）" % "；".join(value.get("warnings", []))
    effective = value["effective"]
    suffix = "，可在 Q2 前调整" if effective["source"] != "user_override" else ""
    source = {"company_default": "公司默认", "project_default": "项目默认",
              "user_override": "当前任务设置"}.get(effective["source"], effective["source"])
    text = "修复策略：%s（%s%s）" % (effective["label"], source, suffix)
    if value.get("warnings"):
        text += "\n策略提示：" + "；".join(value["warnings"])
    return text


@task_store.task_mutation
def cmd_repair_strategy_set(args):
    task = require(args.dir, args.issue_key)
    if task.get("task_class") != repair_strategy.TASK_CLASS:
        print("当前任务类型为 %s，缺陷修复策略不适用；任务状态未改变。" % task.get("task_class"))
        return 0
    mutable, reason = repair_strategy.can_change(args.dir, task)
    if not mutable:
        print("错误：%s" % reason, file=sys.stderr)
        return 2
    try:
        catalog = repair_strategy.list_strategies(args.dir)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("错误：修复策略配置不可用，任务状态未改变：%s" % error, file=sys.stderr)
        return 2
    known = {item["id"] for item in catalog["strategies"]}
    if args.id not in known:
        print("错误：未知修复策略 %s；可选：%s。任务状态未改变。" % (args.id, "、".join(sorted(known))), file=sys.stderr)
        return 2
    value = {"id": args.id, "source": "user", "note": args.note or ""}
    task["facts"][repair_strategy.OVERRIDE_FACT] = value
    task["history"].append({"ts": now(), "event": "repair_strategy_set", "value": value})
    save(args.dir, task)
    print("已设置当前 run 的缺陷修复策略：%s" % args.id)
    return 0


@task_store.task_mutation
def cmd_repair_strategy_clear(args):
    task = require(args.dir, args.issue_key)
    if task.get("task_class") != repair_strategy.TASK_CLASS:
        print("当前任务类型为 %s，缺陷修复策略不适用；任务状态未改变。" % task.get("task_class"))
        return 0
    mutable, reason = repair_strategy.can_change(args.dir, task)
    if not mutable:
        print("错误：%s" % reason, file=sys.stderr)
        return 2
    if repair_strategy.OVERRIDE_FACT not in task["facts"]:
        print("当前 run 没有任务级修复策略覆盖，状态未改变。")
        return 0
    del task["facts"][repair_strategy.OVERRIDE_FACT]
    task["history"].append({"ts": now(), "event": "repair_strategy_clear"})
    save(args.dir, task)
    print("已清除当前 run 的修复策略覆盖。")
    return 0


def cmd_issue_versions(args):
    with task_store.task_run_lock(args.dir, args.issue_key):
        task = require(args.dir, args.issue_key)
        if task["run_id"] != args.expected_run_id:
            raise ValueError("任务 run 已变化，拒绝旧的影响版本规划")
        prepared = any(r.get("base_sha") for r in task.get("repositories", []))
        if task["stage"] not in ("waiting_takeover", "task_intake", "design_review"):
            raise ValueError("已固化版本规划或已进入实现；先受控 cleanup/reset，再调整修复线并重新授权")
        try:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("无法读取影响版本输入文件：%s" % error) from error
        plan = issue_versions.resolve(args.dir, task, payload)
        if prepared:
            candidate = dict(task, facts=dict(task["facts"], **{issue_versions.FACT: plan}))
            problems = issue_versions.problems(args.dir, candidate)
            # resolve 已核验当前目标分支证据；冻结基线独立按受控工作树验证，
            # 不要求历史开发起点等于当前远端 Head，也不改写该起点。
            try:
                repository_worktree.task_roots(args.dir, task["issue_key"])
            except ValueError as error:
                problems.append("已准备开发基线无效：%s" % error)
            if problems:
                raise ValueError("；".join(problems) + "；先 cleanup/reset 再准备正确基线")
        if project_rules.scan_sensitive(admission(args.dir), json.dumps(plan, ensure_ascii=False)):
            raise ValueError("影响版本输入含敏感内容，脱敏后再提交")
        task["facts"][issue_versions.FACT] = plan
        task["facts"].setdefault("jira_snapshot", plan["observed"])
        task["facts"]["problem_version"] = "、".join(v["name"] for v in plan["versions"])
        task["history"].append({"ts": now(), "event": "issue_versions", "primary_branch": plan["primary_branch"]})
        revoke_authorization(args.dir, task["issue_key"], "issue_versions_changed")
        save(args.dir, task)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


@task_store.task_mutation
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
    version_rules = issue_versions.rules(args.dir, task)
    version_plan = task.get("facts", {}).get(issue_versions.FACT)
    if version_rules and isinstance(version_plan, dict):
        primary = version_plan["primary_branch"]
        if args.repo == version_rules["product_repository"]:
            if args.base_branch and args.base_branch != primary:
                raise ValueError("主仓只能使用本次唯一修复线：%s" % primary)
            base_branch = primary
        elif primary != version_rules["preferred_branch"] and not args.base_branch:
            raise ValueError("非优先修复线的模块仓库必须先做产品分支对齐，再显式传 --base-branch；不得默认回退 develop")
    if not base_branch:
        print("错误：仓库 %s 没有可验证的基线分支" % args.repo, file=sys.stderr)
        return 2
    authorized_endpoint = project_rules.canonical_repository_endpoint(
        branches.get("origin")
    )
    if not authorized_endpoint:
        print(
            "错误：仓库 %s 的 Project catalog origin 无法形成可信 endpoint"
            % args.repo,
            file=sys.stderr,
        )
        return 2
    item = {
        "repository": args.repo,
        "authorized_endpoint": authorized_endpoint,
        "work_branch": args.work_branch,
        "base_branch": base_branch,
        "approved_scope": args.scope,
        "verification_method": args.verification,
        "base_sha": None,
        "catalog_digest": None,
        "worktree": None,
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


@task_store.task_mutation
def cmd_repository_update(args):
    task = require(args.dir, args.issue_key)
    if task_store.task_status(args.dir, task["issue_key"]) != "active" or task["stage"] != "task_intake":
        raise ValueError("repository update 只允许 active 任务的 task_intake；先检查 cleanup/reset 路径")
    item = next((r for r in task.get("repositories", []) if r["repository"] == args.repo), None)
    if item is None:
        raise ValueError("仓库尚未登记：%s" % args.repo)
    if any(item.get(key) for key in ("base_sha", "catalog_digest", "worktree")):
        raise ValueError("仓库仍有基线或 worktree 记录；先 cleanup/reset，保留旧分支与 PR")
    if not all(str(value).strip() for value in (args.scope, args.verification, args.reason)):
        raise ValueError("更新必须提供非空范围、验证方式和原因")
    if args.work_branch == item["work_branch"]:
        raise ValueError("新分支处理必须使用不同的工作分支")
    if project_rules.scan_sensitive(admission(args.dir), " ".join((args.scope, args.verification, args.reason))):
        raise ValueError("仓库更新包含敏感内容，请脱敏后重试")
    repository_worktree._run(["git", "check-ref-format", "--branch", args.work_branch])
    binding, product_root, pool_root = repository_worktree.workspace_binding(args.dir)
    # 与 prepare 相同的锁顺序；登记不创建或移动任何 Git 分支。
    with repository_worktree._pool_lock(product_root):
        for other_issue in task_store.registered_issues(args.dir, statuses=("active",)):
            other = load(args.dir, other_issue)
            if any(r.get("repository") == args.repo and r.get("work_branch") == args.work_branch
                   for r in other.get("repositories", [])):
                raise ValueError("工作分支已由激活任务占用：%s" % other_issue)
        for lease in repository_worktree._load_leases(product_root):
            if lease.get("pool_root") == str(pool_root) and lease.get("repository") == args.repo:
                if (lease.get("branch") == args.work_branch or
                        lease.get("workspace_id") == binding["workspace_id"] and
                        lease.get("issue_key") == task["issue_key"]):
                    raise ValueError("仓库仍有任务租约或新分支被占用；先核对清理结果")
        if repository_worktree.task_worktree_path(args.dir, task, args.repo).exists():
            raise ValueError("当前 run 仍有未登记的 worktree 路径，请先核对现场")
        main = repository_worktree.repository_path(pool_root, args.repo)
        entry = repository_worktree._catalog(args.dir)["repositories"][args.repo]
        if item.get("authorized_endpoint") != project_rules.canonical_repository_endpoint(entry["origin"]):
            raise ValueError("仓库 endpoint 与项目目录不一致")
        if args.work_branch == item["base_branch"]:
            raise ValueError("工作分支不能使用目标基线分支")
        if main.is_dir():
            repository_worktree._validate_origin_chain(main, args.repo, entry["origin"])
            if repository_worktree._run([
                "git", "-C", str(main), "show-ref", "--verify", "--quiet", "refs/heads/" + args.work_branch
            ], check=False).returncode == 0:
                raise ValueError("新工作分支本地已存在：%s" % args.work_branch)
        remote = repository_worktree._run([
            "git", "ls-remote", "--heads", entry["origin"], "refs/heads/" + args.work_branch
        ])
        if remote.stdout.strip():
            raise ValueError("新工作分支远端已存在：%s" % args.work_branch)
        before = dict(item)
        revoke_authorization(args.dir, task["issue_key"], "repository_update")
        item.update(work_branch=args.work_branch, approved_scope=args.scope,
                    verification_method=args.verification, pull_request=None, ci=None)
        task["history"].append({
            "ts": now(), "event": "repository_update", "run_id": task["run_id"],
            "repository": args.repo, "reason": args.reason, "before": before, "after": dict(item),
        })
        save(args.dir, task)
    print("已更新仓库登记；旧分支/PR 保留，旧交付引用已归档，进入实现前重新确认方案并授权。")
    return 0


def repository_context(base, task):
    """返回当前会话继续处理任务所需的、已校验的只读上下文。"""
    roots = repository_worktree.task_roots(base, task["issue_key"])
    repositories = []
    for item, root in zip(task.get("repositories", []), roots):
        repositories.append(
            {
                "repository": item["repository"],
                "worktree": str(root),
                "work_branch": item["work_branch"],
                "base_branch": item["base_branch"],
                "base_sha": item["base_sha"],
                "approved_scope": item["approved_scope"],
                "verification_method": item["verification_method"],
            }
        )
    return {
        "issue_key": task["issue_key"],
        "run_id": task["run_id"],
        "workspace": str(Path(base).resolve()),
        "repositories": repositories,
    }


def cmd_repository_context(args):
    try:
        task = require(args.dir, args.issue_key)
        context = repository_context(args.dir, task)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return 0
    print("任务执行上下文：%s（run=%s）" % (context["issue_key"], context["run_id"]))
    for item in context["repositories"]:
        print("  - %s：%s（%s）" % (item["repository"], item["worktree"], item["work_branch"]))
    return 0


def repository_bindings(repositories):
    """只提取会影响授权有效性的稳定仓库绑定。"""
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


@task_store.task_mutation
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


@task_store.task_mutation
def cmd_repository_prepare(args):
    try:
        paths = repository_worktree.prepare_task(
            args.dir,
            args.issue_key,
            reuse_existing_branch=args.reuse_existing_branch,
            continuation=repository_worktree.continuation_bases(args.continuation_base),
            continuation_heads=repository_worktree.continuation_bases(args.continuation_head),
        )
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("已准备任务 worktree：")
    for path in paths:
        print("  - %s" % path)
    return 0


@task_store.task_mutation
def cmd_repository_cleanup(args):
    try:
        result = repository_worktree.cleanup_task(
            args.dir, args.issue_key, delete_branches=args.delete_branches
        )
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("已清理 %s 个任务 worktree。" % len(result["removed"]))
    for branch in result["branches"]:
        print(
            "分支处理：%s:%s -> %s"
            % (branch["repository"], branch["branch"], branch["status"])
        )
    return 0


def _check_advance(task, target, base, spec):
    """返回阻止推进的原因列表。"""
    problems = []
    if target in ("design_review", "implementation"):
        problems.extend(issue_versions.problems(base, task))
    flexible = project_rules.class_spec(spec, task["task_class"]).get("quality_mode") == "recorded_decision"
    if target == "design_review":
        missing = project_rules.missing_required(spec, task["task_class"], task.get("facts"))
        if missing and flexible:
            print("质量待核对：%s；继续不依赖缺项的分析，在检查点记录用户处置。" % "、".join(f["label"] for f in missing))
            for f in missing:
                print(f["supplement"])
        if missing and not flexible:
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
        repositories = task.get("repositories", [])
        if not repositories:
            problems.append(
                "离开 task_intake 前至少登记一个源码仓库；先执行 task.py repository add"
            )
        else:
            incomplete = [
                item.get("repository")
                for item in repositories
                if (item.get("worktree") or {}).get("status") != "prepared"
                or not item.get("base_sha")
                or not item.get("catalog_digest")
            ]
            if incomplete:
                problems.append(
                    "离开 task_intake 前必须为全部登记仓库准备受控 worktree 并固化 "
                    "base_sha/catalog_digest：%s；执行 task.py repository prepare"
                    % "、".join(incomplete)
                )
            else:
                try:
                    repository_worktree.task_roots(base, task["issue_key"])
                except ValueError as error:
                    problems.append("受控本地 Git 基线无效：%s" % error)
    if target == "pr_review" and not flexible:
        verification = (task.get("facts") or {}).get("verification")
        for reason in project_rules.check_verification(spec, verification):
            problems.append("验证结论不合规：%s" % reason)
        if not verification:
            problems.append(
                '先执行：task.py record --issue-key %s --key verification --value "<命令 + 退出结果>"'
                % task["issue_key"]
            )
    if target in ("implementation", "pr_review", "ci_validation", "completed"):
        if not task.get("repositories"):
            problems.append("进入 implementation 前至少确认一个任务仓库")
        auth, _ = engine.load_authorization_for_issue(base, task["issue_key"])
        context = {"branch_relevant": False, "issue_key": task["issue_key"]}
        policy = engine.load_policy()
        valid, reasons = engine.check_authorization(auth, context, policy)
        if not valid:
            problems.append("进入 %s 需要有效方案确认：%s" % (target, "；".join(reasons)))
            if reasons == ["授权已过期"]:
                problems.append("方案与绑定未变时，可经人工明确确认后使用 authorization.py show --digest / renew 续签当前 run；不得自动续签")
        elif auth.get("issue_key") != task["issue_key"]:
            problems.append(
                "授权 issue_key（%s）与任务（%s）不一致" % (auth.get("issue_key"), task["issue_key"])
            )
        elif auth.get("repositories") != repository_bindings(task.get("repositories", [])):
            problems.append("授权仓库集合与当前任务仓库集合不一致")
        elif auth.get("agentic_run_id") != task.get("run_id"):
            problems.append("方案确认的 run 与当前任务不一致")
        elif auth.get("approved_plan_digest") and auth["approved_plan_digest"] != authorization.plan_digest(task):
            problems.append("fix_plan 已变化，需要重新确认方案")
        elif "approved_q1_digest" in auth or "approved_q2_digest" in auth:
            q1_digest, q2_digest = auth.get("approved_q1_digest"), auth.get("approved_q2_digest")
            if not isinstance(q1_digest, str) or not q1_digest or not isinstance(q2_digest, str) or not q2_digest:
                problems.append("授权中的 Q1/Q2 确认摘要无效，需要重新确认方案")
            else:
                try:
                    if q1_digest != quality.q1_digest(base, task) or q2_digest != quality.q2_digest(base, task):
                        problems.append("Q1/Q2 方案或验收项已变化，需要重新确认方案")
                except ValueError as error:
                    problems.append("Q1/Q2 方案确认无效：%s" % error)
    if target == "completed":
        prepared = [
            item.get("repository")
            for item in task.get("repositories", [])
            if (item.get("worktree") or {}).get("status") == "prepared"
        ]
        if prepared:
            problems.append(
                "任务完成前必须先执行 repository cleanup：%s" % "、".join(prepared)
            )
    problems.extend(quality.advance_problems(base, task, target))
    return problems


@task_store.task_mutation
def cmd_advance(args):
    issue = task_store.resolve_issue(args.dir, args.issue_key)
    with task_store.task_run_lock(args.dir, issue):
        return _cmd_advance_locked(args)


def _cmd_advance_locked(args):
    task = load(args.dir, args.issue_key)
    # completed 是已核验的提交点；中断重试只收敛派生状态，不重复推进或重做验收。
    if task["stage"] == "completed" and args.expected_stage in ("ci_validation", "completed"):
        _finish_completion(args.dir, task)
        print("任务已完成，授权及注册状态已收敛。")
        return 0
    task_store.resolve_active_issue(args.dir, args.issue_key)
    if task.get("stage") != getattr(args, "expected_stage", None):
        raise ValueError("任务阶段已变化或缺少 --expected-stage（当前 %s）；拒绝重复推进" % task.get("stage"))
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
    task["stage"] = target
    task["pending"] = None
    task["history"].append({"ts": now(), "event": "advance", "stage": target, "note": args.note})
    save(args.dir, task)
    if target == "completed":
        _finish_completion(args.dir, task)
    print("已推进到阶段：%s（依据：%s）" % (target, args.note))
    _print_next(task)
    return 0


def _finish_completion(base, task):
    revoke_authorization(base, task["issue_key"], "task_completed")
    if task_store.task_status(base, task["issue_key"]) != "completed":
        task_store.set_status(base, task["issue_key"], "completed")


@task_store.task_mutation
def cmd_block(args):
    task = require(args.dir, args.issue_key)
    task["pending"] = {"ts": now(), "stage": task["stage"], "reason": args.reason}
    task["history"].append({"ts": now(), "event": "block", "reason": args.reason})
    save(args.dir, task)
    print("已记录 pending 门禁并停止：%s" % args.reason)
    print("恢复时先执行 task.py status --issue-key %s 查看 pending 正文。" % task["issue_key"])
    return 0


def _cmd_reset_locked(args):
    task = load(args.dir, args.issue_key)
    if task is None:
        print("错误：没有任务状态", file=sys.stderr)
        return 2
    if task.get("run_id") != args.expected_run_id:
        print(
            "错误：reset 绑定的 run 已失效（当前 %s，传入 %s）；请重新检查任务状态"
            % (task.get("run_id"), args.expected_run_id),
            file=sys.stderr,
        )
        return 3
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
    prepared = [
        item.get("repository")
        for item in task.get("repositories", [])
        if (item.get("worktree") or {}).get("status") == "prepared"
    ]
    if prepared:
        print(
            "错误：reset 前必须先清理当前 run 的 worktree：%s"
            % "、".join(prepared),
            file=sys.stderr,
        )
        return 2
    revoke_authorization(args.dir, task["issue_key"], "task_reset")
    task["history"].append(
        {"ts": now(), "event": "reset", "from": task["stage"], "to": args.stage, "note": args.note}
    )
    task["stage"] = args.stage
    task["run_id"] = "run-" + uuid.uuid4().hex[:12]
    for key in ("jira_snapshot", "agenticops_version", repair_strategy.OVERRIDE_FACT):
        if key in task["facts"]:
            task["history"].append({"ts": now(), "event": "archive_fact", "key": key, "value": task["facts"].pop(key)})
    for item in task.get("repositories", []):
        if item.get("base_sha"):
            task["history"].append({
                "ts": now(), "event": "archive_repository_baseline",
                "repository": item["repository"], "work_branch": item["work_branch"],
                "base_branch": item["base_branch"], "base_sha": item["base_sha"],
                "run_id": args.expected_run_id,
            })
        item["base_sha"] = None
        item["catalog_digest"] = None
        item["worktree"] = None
    task["pending"] = None
    save(args.dir, task)
    task_store.set_status(args.dir, task["issue_key"], "active")
    print(
        "已重置到阶段：%s（%s），新 run=%s；旧授权已撤销，进入实现前必须重新授权"
        % (args.stage, args.note, task["run_id"])
    )
    _print_next(task)
    return 0


def cmd_reset(args):
    try:
        issue = task_store.resolve_issue(args.dir, args.issue_key)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    args.issue_key = issue
    with task_store.task_run_lock(args.dir, issue):
        return _cmd_reset_locked(args)


@task_store.task_mutation
def cmd_interaction_path(args):
    task = load(args.dir, args.issue_key)
    if task is None:
        raise ValueError("任务状态缺失：%s" % args.issue_key)
    path = task_store.interaction_path(
        args.dir, task["issue_key"], task["run_id"], args.name, create=True
    )
    print(path)
    return 0


NEXT_GUIDE = {
    "waiting_takeover": "读取 Jira 初始快照并准备本地版本水印；尽力回写，失败记录警告后继续 advance 进入 task_intake",
    "task_intake": "checklist/record 完成准入 -> repository add/prepare 固化本地基线 -> 源码分析 -> advance；takeover 状态同步失败记录警告并继续",
    "design_review": "基于任务 worktree 形成方案 -> 研发工程师确认 -> workflow/authorization.py grant -> advance；Jira 尽力回写，失败不阻断",
    "implementation": "在授权范围内实现和测试；Q2 已选修复后检查项在最终 SHA 符合预期时自动记录 Q3，继续已授权提交/推送和 Draft PR；Jira 同步失败列警告，PR 后统一总结",
    "pr_review": "完成 Q4 关联用例验收后 advance；进入 ci_validation 后用 jira_status.py 在 tests_passed 节点同步尝试一次 Tests Passed",
    "ci_validation": "完成 Tests Passed 同步尝试，用 workflow/ci.py watch 更新每个 PR Head 的 Checks，再用 pr_ready.py 核对测试任务、PR Checks 和 Q1-Q4",
    "completed": "用 workflow/evidence.py 生成证据总结，经确认后作为 Jira 评论回写",
}


def _print_next(task):
    if not task.get("run_id"):
        print("旧状态缺少 run_id：可查看历史，不得直接发起流程写入；需人工确认恢复方案。")
        return
    print("请求绑定：--expected-run-id %s --expected-stage %s（expected-stage 仅用于 advance）"
          % (task["run_id"], task["stage"]))
    print("下一步：%s" % NEXT_GUIDE.get(task["stage"], ""))
    print("项目启用 recorded_decision 时，使用 quality.py status/apply 核对用例和用户处置；文本 verification 不代表通过。")


def cmd_next(args):
    """只给确定性门禁和具体接力，不代替 Agent 或自动制造授权。"""
    import contextlib
    import io
    from workflow import external_sync
    task = require(args.dir, args.issue_key)
    index = STAGES.index(task["stage"])
    target = STAGES[index + 1] if index + 1 < len(STAGES) else None
    diagnostic = io.StringIO()
    with contextlib.redirect_stdout(diagnostic):
        blockers = _check_advance(task, target, args.dir, admission(args.dir)) if target else []
    rules = quality.config(args.dir)
    current = quality.report(quality.load(args.dir, task), rules, quality.context(args.dir, task)) if quality.enabled(task, rules) else {}
    points = rules.get("stage_checkpoints", {}).get(target, []) if current else []
    payload = {"issue_key": task["issue_key"], "run_id": task["run_id"], "stage": task["stage"],
                      "next_stage": target, "advance_ready": bool(target and not blockers),
                      "blockers": blockers, "diagnostics": diagnostic.getvalue().splitlines(),
                      "pending": task.get("pending"), "guidance": NEXT_GUIDE[task["stage"]],
                      "checkpoints": {p: current["checkpoints"][p] for p in points},
                      "publications": current.get("publications", {}),
                      "warnings": external_sync.warnings(args.dir, task, current),
                      "continuity": "在现有授权内连续完成可执行步骤；只为缺少事实、必要人工决定、权限不足或外部写结果不明暂停。"}
    strategy = _strategy_payload(args.dir, task)
    if task.get("task_class") == repair_strategy.TASK_CLASS:
        payload["repair_strategy"] = strategy
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


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
            "quality_mode": cls.get("quality_mode", "text_required"),
        }
        if task_class == repair_strategy.TASK_CLASS and task:
            payload["repair_strategy"] = _strategy_payload(args.dir, task)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print("准入清单：%s（%s）" % (cls["title"], task_class))
    if task_class == repair_strategy.TASK_CLASS and task:
        print(_strategy_summary(_strategy_payload(args.dir, task)))
    flexible = cls.get("quality_mode") == "recorded_decision"
    print("核对项（缺项须披露并在质量检查点处置）：" if flexible else "必填项（缺一不可，advance 硬拦）：")
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
    print("结论：%s" % (("缺 %d 项，须披露并处置" if flexible else "缺 %d 项，不得离开 task_intake") % len(missing) if missing else "必填项齐备"))
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
    print("run：%s" % (task.get("run_id") or "旧状态未记录"))
    print("注册状态：%s" % task_store.task_status(args.dir, task["issue_key"]))
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


@task_store.task_mutation
def cmd_activate(args):
    try:
        issue = task_store.validate_issue_key(args.issue_key)
        with task_store.task_run_lock(args.dir, issue):
            task = load(args.dir, issue)
            if task is None:
                raise ValueError("任务状态缺失：%s" % issue)
            if task.get("stage") == "completed":
                raise ValueError("任务已完成；确需重开请使用 reset 并重新授权")
            conflicts = activation_conflicts(args.dir, task)
            if conflicts:
                raise ValueError("任务无法激活：%s" % "；".join(conflicts))
            task_store.set_status(args.dir, issue, "active")
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("任务已激活：%s" % task_store.validate_issue_key(args.issue_key))
    return 0


@task_store.task_mutation
def cmd_deactivate(args):
    try:
        issue = task_store.validate_issue_key(args.issue_key)
        with task_store.task_run_lock(args.dir, issue):
            registry = task_store.load_registry(args.dir, create=False)
            if registry and registry["tasks"].get(issue, {}).get("status") == "completed":
                raise ValueError("completed 任务无需停用")
            task_store.set_status(args.dir, issue, "inactive")
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("任务已停用：%s（状态和方案确认仍保留，推进前需要恢复）" % task_store.validate_issue_key(args.issue_key))
    return 0


def cmd_purge(args):
    if not args.yes:
        print("错误：purge 会删除任务注册与任务目录，必须显式传入 --yes", file=sys.stderr)
        return 2
    try:
        issue = task_store.validate_issue_key(args.issue_key)
        with task_store.task_run_lock(args.dir, issue):
            task = load(args.dir, issue)
            if task is None:
                raise ValueError("任务状态缺失：%s" % issue)
            status = task_store.task_status(args.dir, issue)
            if status != "inactive":
                raise ValueError(
                    "purge 只允许 inactive 任务：%s（当前 %s）" % (issue, status)
                )
            if task.get("run_id") != args.expected_run_id:
                raise ValueError(
                    "purge 绑定的 run 已失效（当前 %s，传入 %s）；请重新检查任务状态"
                    % (task.get("run_id"), args.expected_run_id)
                )
            cleanup = {"removed": [], "branches": []}
            if task.get("repositories"):
                cleanup = repository_worktree._cleanup_task_locked(
                    args.dir, issue, delete_branches=True
                )
                task = load(args.dir, issue)
            if task_store.task_status(args.dir, issue) != "inactive":
                raise ValueError("purge 清理后任务状态发生变化，拒绝删除：%s" % issue)
            if task.get("run_id") != args.expected_run_id:
                raise ValueError("purge 清理后 run_id 发生变化，拒绝删除：%s" % issue)
            execution_root = repository_worktree.task_execution_root(args.dir, task)
            if execution_root.exists():
                raise ValueError("purge 前任务 worktree 根目录仍存在：%s" % execution_root)
            leases = repository_worktree.remaining_task_leases(args.dir, task)
            if leases:
                raise ValueError(
                    "purge 前仍有当前 task/run 的 Source Pool 租约：%d" % len(leases)
                )
            residual = [
                "%s:%s(%s)"
                % (item["repository"], item["branch"], item["status"])
                for item in cleanup["branches"]
                if item["status"].startswith("retained")
            ]
            task_store.purge_inactive(args.dir, issue, args.expected_run_id)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    print("任务已 purge：%s（注册与任务目录已删除）" % issue)
    if residual:
        print("保留无充分删除证明的本地分支（未强制删除）：%s" % "、".join(residual))
    return 0


def main():
    class StrictArgumentParser(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs):
            kwargs["allow_abbrev"] = False
            super().__init__(*args, **kwargs)

    parser = StrictArgumentParser(description=__doc__)
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
    value = p.add_mutually_exclusive_group(required=True)
    value.add_argument("--value")
    value.add_argument("--input", help="fix_plan 的结构化 JSON 文件")
    p.add_argument("--force", action="store_true", help="允许记录清单外的自定义 fact key")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_record)

    p = sub.add_parser("issue-versions")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_issue_versions)

    p = sub.add_parser("repair-strategy")
    strategy_sub = p.add_subparsers(dest="repair_strategy_cmd", required=True)
    listing = strategy_sub.add_parser("list")
    listing.add_argument("--dir", default=".")
    listing.set_defaults(func=cmd_repair_strategy_list)
    showing = strategy_sub.add_parser("show")
    showing.add_argument("--issue-key", required=True)
    showing.add_argument("--json", action="store_true")
    showing.add_argument("--dir", default=".")
    showing.set_defaults(func=cmd_repair_strategy_show)
    setting = strategy_sub.add_parser("set")
    setting.add_argument("--issue-key", required=True)
    setting.add_argument("--expected-run-id", required=True)
    setting.add_argument("--id", required=True)
    setting.add_argument("--note")
    setting.add_argument("--dir", default=".")
    setting.set_defaults(func=cmd_repair_strategy_set)
    clearing = strategy_sub.add_parser("clear")
    clearing.add_argument("--issue-key", required=True)
    clearing.add_argument("--expected-run-id", required=True)
    clearing.add_argument("--dir", default=".")
    clearing.set_defaults(func=cmd_repair_strategy_clear)

    p = sub.add_parser("snapshot")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("next")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_next)

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
    update = repository_sub.add_parser("update")
    update.add_argument("--issue-key")
    update.add_argument("--repo", required=True)
    update.add_argument("--work-branch", required=True)
    update.add_argument("--scope", required=True)
    update.add_argument("--verification", required=True)
    update.add_argument("--reason", required=True)
    update.add_argument("--dir", default=".")
    update.set_defaults(func=cmd_repository_update)
    listing = repository_sub.add_parser("list")
    listing.add_argument("--issue-key")
    listing.add_argument("--dir", default=".")
    listing.set_defaults(func=cmd_repository_list)
    context = repository_sub.add_parser("context")
    context.add_argument("--issue-key")
    context.add_argument("--json", action="store_true")
    context.add_argument("--dir", default=".")
    context.set_defaults(func=cmd_repository_context)
    record = repository_sub.add_parser("record-result")
    record.add_argument("--issue-key")
    record.add_argument("--repo", required=True)
    record.add_argument("--pr")
    record.add_argument("--ci")
    record.add_argument("--dir", default=".")
    record.set_defaults(func=cmd_repository_record)
    prepare = repository_sub.add_parser("prepare")
    prepare.add_argument("--issue-key")
    prepare.add_argument("--reuse-existing-branch", action="store_true")
    prepare.add_argument("--continuation-base", action="append", default=[])
    prepare.add_argument("--continuation-head", action="append", default=[])
    prepare.add_argument("--dir", default=".")
    prepare.set_defaults(func=cmd_repository_prepare)
    cleanup = repository_sub.add_parser("cleanup")
    cleanup.add_argument("--issue-key")
    cleanup.add_argument("--delete-branches", action="store_true")
    cleanup.add_argument("--dir", default=".")
    cleanup.set_defaults(func=cmd_repository_cleanup)

    p = sub.add_parser("advance")
    p.add_argument("--issue-key")
    p.add_argument("--expected-stage", required=True, choices=STAGES)
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
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--note", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_reset)

    p = sub.add_parser("status")
    p.add_argument("--issue-key")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("interaction-path")
    p.add_argument("--issue-key")
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_interaction_path)

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

    p = sub.add_parser("purge")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_purge)

    for command in ("record", "advance", "block", "activate", "deactivate"):
        sub.choices[command].add_argument("--expected-run-id", required=True)
    for command in ("add", "update", "record-result", "prepare", "cleanup"):
        repository_sub.choices[command].add_argument("--expected-run-id", required=True)
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
