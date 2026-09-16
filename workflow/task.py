#!/usr/bin/env python3
"""单任务研发工位：takeover/archive/release/clean 及确定性任务检查点。"""
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
from workflow import authorization, issue_versions, jira_watermark, project_rules, quality, repair_strategy, station_source, task_store  # noqa: E402

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
    return task_store.read_task(base, issue_key)


def save(base, task):
    task_store.write_task(base, task)


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


def require(base, issue_key=None):
    issue = task_store.resolve_issue(base, issue_key)
    task = load(base, issue)
    if task is None:
        print("错误：没有任务状态（先执行 task.py takeover）", file=sys.stderr)
        sys.exit(2)
    return task


def cmd_takeover(args):
    from workflow import station
    overrides = {}
    for value in args.explicit_branch:
        if "=" not in value:
            raise ValueError("显式分支格式为 repository=branch")
        name, branch = value.split("=", 1)
        if name in overrides:
            raise ValueError("重复显式分支")
        overrides[name] = branch
    request = {"issue_key": args.issue_key, "task_class": args.task_class, "version": args.version,
               "profile": args.profile, "optional_repositories": args.optional_repository,
               "explicit_branches": overrides}
    if args.continuation_input:
        request["continuations"] = json.loads(Path(args.continuation_input).read_text(encoding="utf-8"))
    print(json.dumps(station.takeover(args.dir, request, args.operation_id, args.expected_revision), ensure_ascii=False, indent=2))
    return 0


def cmd_lifecycle(args):
    from workflow import station
    request = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = station.execute(args.dir, args.cmd, args.issue_key, args.expected_run_id,
                             args.expected_revision, args.operation_id, request)
    summary = {key: result.get(key) for key in ("operation_id", "kind", "run_id", "phase", "status", "archive_ref")}
    plan = result.get("cleanup_plan", {})
    summary.update(directory_count=len(plan.get("directories", [])), source_artifact_count=len(plan.get("entries", [])),
                   plan_digest=plan.get("digest"), retained=plan.get("retained", []))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_cleanup_plan(args):
    from workflow import station_resources, station_source, station_operation, engineering_baseline
    with task_store.task_state_lock(args.dir):
        task = task_store.check_expected_run(args.dir, args.issue_key, args.expected_run_id)
        result = station_resources.plan(args.dir, task)
        operation = station_operation.read(args.dir)
        result["plan_revision"] = len(operation.get("plan_revisions", [])) if operation else 0
        if task.get("engineering_baseline", {}).get("status") == "frozen":
            result["candidate_digest"] = engineering_baseline.digest(station_source.inspect(args.dir, task["engineering_baseline"]))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_cleanup_preflight(args):
    from workflow import station_resources
    with task_store.task_state_lock(args.dir):
        task = task_store.check_expected_run(args.dir, args.issue_key, args.expected_run_id)
        print(json.dumps(station_resources.preflight(args.dir, task), ensure_ascii=False, indent=2))
    return 0


def cmd_source_readiness(args):
    with task_store.task_state_lock(args.dir):
        task = task_store.check_expected_run(args.dir, args.issue_key, args.expected_run_id)
        task_store.require_development(args.dir, task)
        if task["stage"] not in ("task_intake", "design_review"):
            raise ValueError("仓库就绪检查只在开始编码前执行")
        if args.confirm_digest:
            path = task_store.task_directory(args.dir, task["issue_key"]) / "source-readiness.json"
            if path.is_symlink():
                raise ValueError("就绪证据不能是符号链接")
            record = json.loads(path.read_text())
            snapshot = station_source.readiness_snapshot(args.dir, task)
            if record.get("status") != "observed" or record.get("snapshot") != snapshot or args.confirm_digest != snapshot["digest"] or not args.decision_ref:
                raise ValueError("就绪确认缺失或仓库事实变化")
            record.update(accepted_digest=args.confirm_digest, decision_ref=args.decision_ref)
            task_store._write_json_atomic(path, record)
        else:
            record = station_source.prepare_readiness(args.dir, task)
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def cmd_cleanup_amend(args):
    from workflow import station
    request = json.loads(Path(args.input).read_text(encoding="utf-8"))
    request["expected_plan_revision"] = args.expected_plan_revision
    print(json.dumps(station.amend_cleanup(args.dir, args.issue_key, args.expected_run_id,
        args.expected_revision, args.operation_id, args.expected_plan_digest, request), ensure_ascii=False, indent=2))
    return 0


def cmd_repository_add(args):
    from workflow import station
    issue = task_store.resolve_issue(args.dir, args.issue_key)
    print(json.dumps(station.scope_change(args.dir, issue, args.expected_run_id,
        args.expected_revision, args.operation_id, args.repo, args.work_branch,
        args.base_branch, [args.scope], args.verification, args.expected_head), ensure_ascii=False, indent=2))
    return 0


def cmd_repository_list(args):
    print(json.dumps(load(args.dir, args.issue_key)["task_repositories"], ensure_ascii=False, indent=2))
    return 0


def repository_context(base, task):
    from workflow import station_operation
    return {"issue_key": task["issue_key"], "run_id": task["run_id"], "revision": task["_revision"],
            "operation": station_operation.read(base),
            "workspace": str(Path(base).resolve()), "engineering_baseline": task["engineering_baseline"],
            "task_repositories": task["task_repositories"],
            "repositories": task.get("repositories", []),
            "paths": {name: str(Path(base).resolve() / name) for name in ("source", "config", "runtime", "archive")}}


def cmd_repository_context(args):
    print(json.dumps(repository_context(args.dir, load(args.dir, args.issue_key)), ensure_ascii=False, indent=2))
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
    if args.key == "station_contract":
        raise ValueError("station_contract 由接管入口管理，不能通过 record 修改")
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
        rules = quality.config(args.dir, task)
        plan_keys = rules.get("plan_fact_keys", []) if quality.enabled(task, rules) else []
        if args.key != "fix_plan" and args.key not in plan_keys:
            raise ValueError("--input 仅用于以 JSON 对象记录项目声明的方案事实")
        try:
            value = json.loads(Path(args.input).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("无法读取方案 JSON：%s" % error) from error
        if not isinstance(value, dict):
            raise ValueError("方案 JSON 必须是对象")
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
        task_store.require_development(args.dir, task)
        if task["run_id"] != args.expected_run_id:
            raise ValueError("任务 run 已变化，拒绝旧的影响版本规划")
        prepared = any(r.get("base_sha") for r in task.get("repositories", []))
        if task["stage"] not in ("waiting_takeover", "task_intake", "design_review"):
            raise ValueError("已固化版本规划或已进入实现；先归档并受控 clean，再按新修复线 takeover")
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
                station_source.inspect(args.dir, task["engineering_baseline"])
            except ValueError as error:
                problems.append("已准备开发基线无效：%s" % error)
            if problems:
                raise ValueError("；".join(problems) + "；先归档并 clean，再 takeover 准备正确基线")
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
    if args.pr is None and args.ci is None and not getattr(args, "delivery_input", None):
        print("错误：至少提供 --pr 或 --ci", file=sys.stderr)
        return 2
    if args.pr is not None:
        item["pull_request"] = args.pr
    if args.ci is not None:
        item["ci"] = args.ci
    if getattr(args, "delivery_input", None):
        delivery = json.loads(Path(args.delivery_input).read_text(encoding="utf-8"))
        if not isinstance(delivery, dict) or delivery.get("repository") != args.repo:
            raise ValueError("交付回读必须绑定当前仓库")
        if project_rules.scan_sensitive(admission(args.dir), json.dumps(delivery, ensure_ascii=False)):
            raise ValueError("交付回读包含敏感内容")
        task["task_repositories"][args.repo]["deliveries"] = [delivery]
    task["history"].append(
        {"ts": now(), "event": "repository_result", "repository": args.repo}
    )
    save(args.dir, task)
    print("已记录仓库结果：%s" % args.repo)
    return 0


def _check_advance(task, target, base, spec):
    """返回阻止推进的原因列表。"""
    problems = []
    if target == "implementation":
        try:
            ready_digest = station_source.require_readiness(base, task)
            auth, _ = engine.load_authorization_for_issue(base, task["issue_key"])
            if ready_digest and (auth or {}).get("source_readiness_digest") != ready_digest:
                raise ValueError("编码授权未绑定当前仓库就绪摘要")
        except (ValueError, OSError) as error:
            problems.append("编码前仓库未就绪：%s" % error)
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
        try:
            from workflow import engineering_baseline
            engineering_baseline.validate(task.get("engineering_baseline"))
            if not task.get("source_prepared"):
                raise ValueError("完整工程尚未准备完成")
            station_source.inspect(base, task["engineering_baseline"])
        except ValueError as error:
            problems.append("完整工程基线无效：%s" % error)
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
        elif auth.get("approved_plan_digest") and auth["approved_plan_digest"] != authorization.plan_digest(task, base):
            problems.append("方案已变化，需要重新确认方案")
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
    problems.extend(quality.advance_problems(base, task, target))
    return problems


def cmd_advance(args):
    issue = task_store.resolve_issue(args.dir, args.issue_key)
    with task_store.task_run_lock(args.dir, issue):
        task = task_store.check_expected_run(args.dir, issue, args.expected_run_id)
        if not (task.get("stage") == "completed" and task.get("terminal_proof")
                and args.expected_stage in ("ci_validation", "completed")):
            task_store.require_development(args.dir, task)
        return _cmd_advance_locked(args)


def _cmd_advance_locked(args):
    task = load(args.dir, args.issue_key)
    # completed 是已核验的提交点；中断重试只收敛派生状态，不重复推进或重做验收。
    if task["stage"] == "completed" and args.expected_stage in ("ci_validation", "completed"):
        _finish_completion(args.dir, task)
        print("任务已完成，授权撤销已收敛；仍需明确 release。")
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
    if target == "completed":
        from workflow import station
        task["terminal_proof"] = station.completion_proof(args.dir, task)
        task["outcome"] = "completed"
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
    "task_intake": "checklist/record 完成准入 -> repository add 登记修改范围及工作分支（完整工程已在 takeover 准备）-> 源码分析 -> advance；Jira 状态同步失败记录警告并继续",
    "design_review": "基于 source 完整工程形成方案 -> 新任务 source-readiness 核验仓库及目标分支 -> 研发工程师确认 -> workflow/authorization.py grant -> advance；Jira 尽力回写，失败不阻断",
    "implementation": "在授权范围内实现和测试；Q2 已选修复后检查项在最终 SHA 符合预期时自动记录 Q3，继续已授权提交/推送和 Draft PR；Jira 同步失败列警告，PR 后统一总结",
    "pr_review": "完成 Q4 关联用例验收后 advance；进入 ci_validation 后用 jira_status.py 在 tests_passed 节点同步尝试一次 Tests Passed",
    "ci_validation": "完成 Tests Passed 同步尝试，用 workflow/ci.py watch 更新每个 PR Head 的 Checks，再用 pr_ready.py 核对测试任务、PR Checks 和 Q1-Q4",
    "completed": "生成脱敏任务总结；研发明确确认 cleanup-plan 与最终候选后 release，归档并释放工位；Jira 同步结果另行回读",
}


def _print_next(task):
    if task.get("archive_ref"):
        print("任务已归档，仅供审计；下一步：回读 cleanup-plan 并确认后%s。" % (" release" if task.get("outcome") == "completed" else " clean"))
        return
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
    if task.get("archive_ref"):
        print(json.dumps({"issue_key": task["issue_key"], "run_id": task["run_id"],
            "advance_ready": False, "next_stage": None, "archive_ref": task["archive_ref"],
            "guidance": "档案只供审计；回读 cleanup-plan，确认后执行 release 或 clean"}, ensure_ascii=False, indent=2))
        return 0
    index = STAGES.index(task["stage"])
    target = STAGES[index + 1] if index + 1 < len(STAGES) else None
    diagnostic = io.StringIO()
    with contextlib.redirect_stdout(diagnostic):
        blockers = _check_advance(task, target, args.dir, admission(args.dir)) if target else []
    rules = quality.config(args.dir, task)
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
    print("任务结果：%s（工位仍占用）" % task.get("outcome", "in_progress"))
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


def main():
    class StrictArgumentParser(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs):
            kwargs["allow_abbrev"] = False
            super().__init__(*args, **kwargs)

    parser = StrictArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("takeover")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--task-class", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--profile", default="full-application")
    p.add_argument("--optional-repository", action="append", default=[])
    p.add_argument("--explicit-branch", action="append", default=[])
    p.add_argument("--continuation-input")
    p.add_argument("--operation-id", required=True)
    p.add_argument("--expected-revision", required=True, type=int)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_takeover)
    for kind in ("archive", "release", "clean"):
        p = sub.add_parser(kind)
        p.add_argument("--issue-key", required=True)
        p.add_argument("--expected-run-id", required=True)
        p.add_argument("--expected-revision", required=True, type=int)
        p.add_argument("--operation-id", required=True)
        p.add_argument("--input", required=True)
        p.add_argument("--dir", default=".")
        p.set_defaults(func=cmd_lifecycle)
    p = sub.add_parser("cleanup-plan")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_cleanup_plan)
    p = sub.add_parser("cleanup-preflight", help="只读展示任务状态与清理范围，供用户决定是否继续")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_cleanup_preflight)
    p = sub.add_parser("source-readiness", help="编码前刷新引用、检查工作区及分支关系")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--confirm-digest")
    p.add_argument("--decision-ref")
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_source_readiness)
    p = sub.add_parser("cleanup-amend", help="精确补充当前清理计划；archive 仅刷新未发布草稿，不授权删除",
                       description="绑定原 operation、计划摘要和修订编号。archive 仅刷新未发布草稿，不删除资源；正式档案不可改写。clean/release 使用新精确确认后，仍以原始请求恢复。")
    p.add_argument("--issue-key", required=True)
    p.add_argument("--expected-run-id", required=True)
    p.add_argument("--expected-revision", required=True, type=int)
    p.add_argument("--operation-id", required=True)
    p.add_argument("--expected-plan-digest", required=True)
    p.add_argument("--expected-plan-revision", required=True, type=int)
    p.add_argument("--input", required=True)
    p.add_argument("--dir", default=".")
    p.set_defaults(func=cmd_cleanup_amend)

    p = sub.add_parser("record")
    p.add_argument("--issue-key")
    p.add_argument("--key", required=True)
    value = p.add_mutually_exclusive_group(required=True)
    value.add_argument("--value")
    value.add_argument("--input", help="项目声明的方案事实 JSON 对象文件")
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
    add.add_argument("--base-branch", required=True)
    add.add_argument("--operation-id", required=True)
    add.add_argument("--expected-revision", required=True, type=int)
    add.add_argument("--expected-head")
    add.add_argument("--scope", required=True)
    add.add_argument("--verification", required=True)
    add.add_argument("--dir", default=".")
    add.set_defaults(func=cmd_repository_add)
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
    record.add_argument("--delivery-input")
    record.add_argument("--dir", default=".")
    record.set_defaults(func=cmd_repository_record)
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

    for command in ("record", "advance", "block"):
        sub.choices[command].add_argument("--expected-run-id", required=True)
    for command in ("add", "record-result"):
        repository_sub.choices[command].add_argument("--expected-run-id", required=True)
    args = parser.parse_args()
    try:
        task_store.workspace_project(args.dir)
        task_store.read_current(args.dir)
        return args.func(args)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
