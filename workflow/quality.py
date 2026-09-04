#!/usr/bin/env python3
"""质量检查记录：原始执行、用户选择及处置分别保存，不执行测试或外部写入。

status 输出当前摘要和各确认对象 digest；apply 从 --input JSON 读取单个契约操作。
所有写入要求 --expected-run-id 和 --expected-revision，复用任务锁并原子追加日志。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import project_rules, quality_contract, task_store  # noqa: E402


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def config(base):
    root = project_rules.product_root_from_workspace(base)
    project = project_rules.project_from_workspace(base)
    path = project_rules.project_root(root, project) / "quality.json"
    if not path.exists():
        return None
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("schema_version") != 1:
        raise ValueError("不支持的质量配置版本")
    ids = [c["id"] for c in result["checkpoints"]]
    if not ids or len(ids) != len(set(ids)) or result["selection_checkpoint"] not in ids:
        raise ValueError("质量检查点配置无效")
    result["jira"]["site"] = project_rules.load_profile(workspace=base)["jira"]["site"]
    return result


def enabled(task, rules):
    return bool(rules and task["task_class"] in rules["task_classes"])


def state_path(base, task):
    return task_store.task_directory(base, task["issue_key"]) / ("quality-%s.json" % digest(task["run_id"])[:24])


def load(base, task):
    path = state_path(base, task)
    if not path.exists():
        return {"schema_version": 1, "issue_key": task["issue_key"], "run_id": task["run_id"],
                "revision": 0, "events": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        quality_contract.validate(state, "quality-state.schema.json")
        if state["issue_key"] != task["issue_key"] or state["run_id"] != task["run_id"]:
            raise ValueError("质量日志任务/run 不匹配")
        if state["revision"] != len(state["events"]):
            raise ValueError("质量日志 revision 与事件数不一致")
        replay(state)
        return state
    except (KeyError, TypeError, OSError, ValueError) as error:
        raise ValueError("质量日志损坏或不兼容，保留现场并人工恢复：%s" % error) from error


def git_revision(path):
    """只读工作目录指纹；只保存哈希，绝不把文件或 diff 内容写入质量证据。"""
    def git(*args):
        p = subprocess.run(["git", "-C", str(path), *args], capture_output=True, timeout=30)
        if p.returncode:
            raise ValueError("无法核对质量记录所对应的本地代码")
        return p.stdout
    head = git("rev-parse", "HEAD").decode().strip()
    diff = git("diff", "HEAD", "--binary", "--no-ext-diff")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z")
    h = hashlib.sha256(diff)
    for name in sorted(untracked.split(b"\0")):
        if not name:
            continue
        file = Path(path) / os.fsdecode(name)
        h.update(name)
        if file.is_symlink():
            h.update(os.readlink(file).encode())
        else:
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1048576), b""):
                    h.update(chunk)
    return head if not diff and not untracked else head + ":worktree:" + h.hexdigest()


def context(base, task):
    from workflow import ci
    ci_states = ci.current_states(base, task)
    repos = {}
    for repo in task.get("repositories", []):
        entry = {k: repo.get(k) for k in ("repository", "base_branch", "work_branch", "base_sha",
                  "catalog_digest", "approved_scope", "verification_method")}
        wt = repo.get("worktree") or {}
        # cleanup removes a worktree after validation; the source revision remains in the item.
        if wt.get("status") == "prepared":
            entry["live_revision"] = git_revision(wt["path"])
        elif wt.get("status") == "removed" and wt.get("final_revision"):
            entry["live_revision"] = wt["final_revision"]
        repos[repo["repository"]] = entry
        entry["ci_digest"] = digest([s for s in ci_states if s["repository"] == repo["repository"]])
    return {"issue_key": task["issue_key"], "run_id": task["run_id"], "facts": task.get("facts", {}),
            "repositories": repos,
            "missing_facts": [f["key"] for f in project_rules.missing_required(
                project_rules.load_admission(workspace=base), task["task_class"], task.get("facts", {}))]}


def plan_digest(item, rules, ctx):
    repo = dict(ctx["repositories"].get(item["plan"]["repository"], {}))
    repo.pop("live_revision", None)
    repo.pop("ci_digest", None)
    plan = selection_plan(item["plan"], rules)
    return digest([plan, item["plan_version"], rules, repo])


def selection_plan(plan, rules):
    plan = dict(plan)
    if rules.get("contract_revision", 1) >= 2 and plan["timing"] == "after_fix":
        plan.pop("target_revision", None)
    return plan


def exact_commit(revision):
    return bool(re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", revision))


def exact_worktree(revision):
    return bool(re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?:worktree:[0-9a-f]{64}", revision))


def item_digest(item, rules, ctx):
    runtime_keys = ("live_revision", "ci_digest") if item["plan"]["timing"] == "after_fix" else ()
    value = [plan_digest(item, rules, ctx), item["executions"],
                   {k: ctx["repositories"].get(item["plan"]["repository"], {}).get(k)
                    for k in runtime_keys}]
    if rules.get("contract_revision", 1) >= 2:
        value.append(item["plan"]["target_revision"])
    return digest(value)


def is_valid(record, expected):
    return bool(record and record["digest"] == expected)


def item_view(item, rules, ctx):
    pd, ed = plan_digest(item, rules, ctx), item_digest(item, rules, ctx)
    selected = is_valid(item["selection"], pd)
    decided = selected and is_valid(item["decision"], ed)
    return {"plan": item["plan"], "plan_digest": pd, "digest": ed, "selected": selected,
            "decision_valid": decided, "decision": item["decision"], "executions": item["executions"]}


def automatic_checkpoint_problems(model, checkpoint, rules, ctx):
    """Q3 是已授权执行的事实记录，不把它伪装成第二次用户验收。"""
    point = next(c for c in rules["checkpoints"] if c["id"] == checkpoint)
    if point.get("confirmation") != "automatic":
        return ["检查点 %s 不允许自动记录" % checkpoint]
    views = {key: item_view(item, rules, ctx) for key, item in model["items"].items()}
    after_fix = {key: view for key, view in views.items() if view["plan"]["timing"] == "after_fix"}
    if not after_fix:
        return ["首轮验证没有已定义的修复后检查项，不能自动推进"]
    problems = []
    for key, view in after_fix.items():
        plan = view["plan"]
        if not view["selected"]:
            problems.append("%s 的验收方式尚未经 Q2 确认" % key)
            continue
        if not exact_commit(plan["target_revision"]):
            problems.append("%s 尚未绑定最终完整提交 SHA" % key)
            continue
        keys = ("case_ref", "case_version", "method", "repository", "target_revision")
        executions = [e for e in view["executions"] if all(e[k] == plan[k] for k in keys)]
        if not executions:
            problems.append("%s 尚无当前提交上的首轮执行证据" % key)
        elif executions[-1]["raw_result"] != plan["expected_result"]:
            problems.append("%s 的首轮结果为 %s，未满足预期 %s" %
                            (key, executions[-1]["raw_result"], plan["expected_result"]))
    return problems


def automatic_checkpoint_digest(model, checkpoint, rules, ctx):
    # ``checkpoint_view(..., checking_automatic=True)`` deliberately omits the
    # automatic record's validity check so this digest has no recursion.
    base = checkpoint_view(model, checkpoint, rules, ctx, checking_automatic=True)["digest"]
    after_fix = {key: item_digest(item, rules, ctx) for key, item in model["items"].items()
                 if item["plan"]["timing"] == "after_fix"}
    return digest({"checkpoint": checkpoint, "base": base, "after_fix": after_fix})


def checkpoint_outcome(view):
    if view.get("mode") == "automatic":
        return "observed"
    return ((view.get("decision") or {}).get("decision") or {}).get("outcome")


def checkpoint_view(model, checkpoint, rules, ctx, checking_automatic=False):
    ids = [c["id"] for c in rules["checkpoints"]]
    if checkpoint not in ids:
        raise ValueError("未知检查点")
    index = ids.index(checkpoint)
    views = {k: item_view(v, rules, ctx) for k, v in model["items"].items()}
    due = {k: v for k, v in views.items() if ids.index(v["plan"]["checkpoint"]) <= index}
    problems = []
    for key, view in due.items():
        if not view["decision_valid"]:
            problems.append("%s 尚无有效处置" % key)
        elif view["decision"]["decision"]["outcome"] == "rework":
            problems.append("%s 用户要求补测/返工" % key)
    if index >= ids.index(rules["selection_checkpoint"]):
        problems += ["%s 方案待用户选择" % k for k, v in views.items() if not v["selected"]]
        problems += ["修复方案事实 %s 尚未记录；请说明根因、范围、修复方式和风险" % k
                     for k in rules.get("plan_fact_keys", []) if not ctx["facts"].get(k)]
    snapshot = {"checkpoint": checkpoint, "items": views, "due": list(due),
                "not_due": [k for k in views if k not in due], "context": ctx, "rules": rules}
    if rules.get("contract_revision", 1) >= 2:
        scoped = copy.deepcopy(ctx)
        fact_keys = rules["intake_fact_keys"] + (rules.get("plan_fact_keys", []) if index else [])
        scoped["facts"] = {k: v for k, v in ctx["facts"].items() if k in fact_keys}
        for repo in scoped["repositories"].values():
            repo.pop("live_revision", None)
            repo.pop("ci_digest", None)
        # Intake never depends on future test plans. Plan review includes their selection,
        # but not their future execution results, revisions, CI or acceptance decisions.
        future = {} if index == 0 else {
            k: {"plan_digest": v["plan_digest"], "selected": v["selected"]}
            for k, v in views.items() if k not in due}
        snapshot.update(context=scoped, items=dict(future, **due))
        if index == 0:
            snapshot["not_due"] = []
    record = model["checkpoints"].get(checkpoint)
    configured_automatic = rules["checkpoints"][index].get("confirmation") == "automatic"
    result = {"digest": digest(snapshot), "due": list(due), "not_due": snapshot["not_due"],
              "problems": problems, "decision": record, "mode": "automatic" if configured_automatic else "user"}
    if configured_automatic and not checking_automatic:
        result["automatic_digest"] = automatic_checkpoint_digest(model, checkpoint, rules, ctx)
        result["problems"] += automatic_checkpoint_problems(model, checkpoint, rules, ctx)
        result["reviewed"] = (record or {}).get("digest") == result["automatic_digest"] and not result["problems"]
    else:
        result["reviewed"] = is_valid(record, result["digest"]) and not problems
    result["outcome"] = checkpoint_outcome(result)
    point = rules["checkpoints"][index]
    handoff_request = "核对列出的用例、范围、预期与缺口；选择验收、补测/返工、不适用、延期或接受风险。"
    handoff_return = "执行人、环境、精确提交 SHA、步骤、实际结果及可回查日志/报告；未执行须说明原因。"
    if checkpoint == "q4-acceptance":
        handoff_request = ("编码完成后，由用户与 Agent 在 Jira 创建或复用 Test，并通过「已链接工作项」关联缺陷；"
                           "重新读取 Test Type、用例版本和链接。TapCE 当前不纳管，若无法形成受管用例请调整 Jira 或验收方案后重试。")
        handoff_return = ("每个 Manual、TapTest、Unit Test 都需返回精确提交 SHA（当前完整 SHA）的 PASS 证据及用户逐项确认；"
                          "需要本地环境时先提供可操作启动步骤、前置条件和失败日志要求。")
    result["handoff"] = {
        "title": point["title"],
        "verify": [{"item_id": k, "case_ref": v["plan"]["case_ref"],
                    "method": v["plan"]["method"], "repository": v["plan"]["repository"],
                    "target_revision": v["plan"]["target_revision"],
                    "steps": v["plan"].get("steps", "打开用例引用并按用例步骤执行；缺少步骤时先补齐"),
                    "expected": v["plan"]["criterion"], "expected_result": v["plan"]["expected_result"],
                    "decision_valid": v["decision_valid"]} for k, v in due.items()],
        "request": handoff_request,
        "return": handoff_return,
    }
    return result


def check_proof(proof):
    try:
        at = datetime.fromisoformat(proof["at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("确认 at 必须为带时区 ISO 时间") from error
    if at.tzinfo is None:
        raise ValueError("确认时间缺少时区")
    # Provenance is an auditable assertion, not authentication of the named person.


def check_decision(decision):
    check_proof(decision["proof"])
    outcome = decision["outcome"]
    if outcome in ("defer", "accept_risk", "rework"):
        if not decision.get("owner") or not decision.get("follow_up"):
            raise ValueError("延期、风险或返工需责任人和后续动作")
    if outcome == "defer" or "deadline" in decision:
        try:
            deadline = datetime.fromisoformat(decision.get("deadline", "").replace("Z", "+00:00"))
            confirmed = datetime.fromisoformat(decision["proof"]["at"].replace("Z", "+00:00"))
            if deadline.tzinfo is None or deadline <= confirmed:
                raise ValueError()
        except ValueError as error:
            raise ValueError("deadline 必须带时区且晚于确认时间；延期必须提供 deadline") from error


def reduce(model, command, rules, ctx):
    """确定性归约；重放时使用当时规则和上下文，不重新解释旧决定。"""
    action, p = command["action"], command["payload"]
    if action == "item":
        plan = p["plan"]
        points = {c["id"]: c for c in rules["checkpoints"]}
        if plan["checkpoint"] not in points or plan["timing"] != points[plan["checkpoint"]]["timing"]:
            raise ValueError("检查项阶段与检查点不一致")
        if plan["method"] not in rules["methods"]:
            raise ValueError("验证方式未在项目配置登记")
        if plan["repository"] not in ctx["repositories"]:
            raise ValueError("先登记所属任务仓库")
        if plan["expected_result"] == "FAIL" and plan["timing"] != "before_fix":
            raise ValueError("预期失败仅用于修复前复现")
        if rules.get("contract_revision", 1) >= 2:
            if plan["method"] == "manual" and not plan.get("steps"):
                raise ValueError("手工用例必须提供可操作 steps，不能只要求用户确认检查点")
            revision = plan["target_revision"]
            if not (exact_commit(revision) or exact_worktree(revision) or
                    (revision == "pending" and plan["timing"] == "after_fix")):
                raise ValueError("方案目标必须是精确代码；修复后尚未编码时用 pending，不能用分支名")
        item = model["items"].setdefault(plan["id"], {"plan": plan, "plan_version": 1,
                                                  "executions": [], "selection": None, "decision": None})
        if selection_plan(item["plan"], rules) != selection_plan(plan, rules):
            item["plan_version"] += 1
        item["plan"] = plan
    elif action in ("select", "execute", "decide"):
        if p["item_id"] not in model["items"]:
            raise ValueError("检查项不存在")
        item = model["items"][p["item_id"]]
        if action == "select":
            check_proof(p["proof"])
            if p["digest"] != plan_digest(item, rules, ctx):
                raise ValueError("方案已变化，请重新展示给用户确认")
            item["selection"] = p
        elif action == "execute":
            execution = p["execution"]
            method = rules["methods"].get(execution["method"], {})
            if execution["origin"] not in method.get("origins", []):
                raise ValueError("执行来源与验证方式不匹配")
            if any(e["id"] == execution["id"] for e in item["executions"]):
                raise ValueError("执行编号已存在；重试须使用新编号，历史不可覆盖")
            if execution["raw_result"] != "FAIL" and execution["failure_kind"] != "none":
                raise ValueError("非失败执行不能声明 failure_kind")
            if rules.get("contract_revision", 1) >= 2:
                revision = execution["target_revision"]
                if not exact_commit(revision) and not (exact_worktree(revision) and execution["origin"] == "local_maven"):
                    raise ValueError("执行证据必须绑定完整提交 SHA；仅本地自动验证可使用精确 worktree 指纹，不接受分支名或短 SHA")
            item["executions"].append(execution)
        else:
            if p["digest"] != item_digest(item, rules, ctx):
                raise ValueError("用例或执行证据已变化，请重新确认")
            if not is_valid(item["selection"], plan_digest(item, rules, ctx)):
                raise ValueError("用例及验证方式尚未经用户选择")
            decision = p["decision"]
            check_decision(decision)
            if decision.get("evidence_id") and not any(e["id"] == decision["evidence_id"] for e in item["executions"]):
                raise ValueError("处置引用的执行记录不存在")
            if decision["outcome"] == "accept":
                selected = next((e for e in item["executions"] if e["id"] == decision.get("evidence_id")), None)
                plan = item["plan"]
                commit_point = rules.get("commit_evidence_checkpoint")
                ids = [c["id"] for c in rules["checkpoints"]]
                if commit_point and ids.index(plan["checkpoint"]) >= ids.index(commit_point) and not exact_commit(plan["target_revision"]):
                    raise ValueError("最终验收必须绑定完整提交 SHA；工作区测试不能直接充当提交后的验收证据")
                keys = ("case_ref", "case_version", "method", "repository", "target_revision")
                applicable = [e for e in item["executions"] if all(e[k] == plan[k] for k in keys)]
                if not selected or not applicable or selected != applicable[-1]:
                    raise ValueError("通过处置必须关联当前用例、代码和方式的最后一次执行")
                live = ctx["repositories"][plan["repository"]].get("live_revision")
                if live and plan["timing"] == "after_fix" and live != plan["target_revision"]:
                    raise ValueError("本地代码已变化，执行证据与当前代码不一致")
                if selected["raw_result"] != plan["expected_result"]:
                    raise ValueError("原始结果不满足预期；只能补测或明确处置风险")
                if plan["expected_result"] == "FAIL" and selected["failure_kind"] != "assertion":
                    raise ValueError("环境失败或未知失败不能作为成功复现")
            item["decision"] = p
    elif action == "checkpoint":
        point = next((c for c in rules["checkpoints"] if c["id"] == p["checkpoint"]), None)
        if point and point.get("confirmation") == "automatic":
            raise ValueError("该检查点由系统依据执行证据自动记录，不接受重复用户确认")
        report = checkpoint_view(model, p["checkpoint"], rules, ctx)
        if p["digest"] != report["digest"]:
            raise ValueError("检查点内容已变化，请重新展示确认")
        if report["problems"]:
            raise ValueError("；".join(report["problems"]))
        check_decision(p["decision"])
        if not model["items"] and p["checkpoint"] != rules["checkpoints"][0]["id"] and p["decision"]["outcome"] == "accept":
            raise ValueError("没有用例时不得宣称验收通过，需明确不适用或风险处置")
        model["checkpoints"][p["checkpoint"]] = p
    elif action == "auto_checkpoint":
        point = next((c for c in rules["checkpoints"] if c["id"] == p["checkpoint"]), None)
        if not point or point.get("confirmation") != "automatic":
            raise ValueError("当前检查点不允许自动记录")
        expected = automatic_checkpoint_digest(model, p["checkpoint"], rules, ctx)
        if p["digest"] != expected:
            raise ValueError("首轮验证事实已变化，请重新读取执行证据")
        problems = automatic_checkpoint_problems(model, p["checkpoint"], rules, ctx)
        if problems:
            raise ValueError("；".join(problems))
        model["checkpoints"][p["checkpoint"]] = dict(p, mode="automatic")
    else:
        from workflow import quality_write
        quality_write.reduce(model, command, rules, ctx)


def replay(state):
    model = {"items": {}, "checkpoints": {}, "publications": {}}
    for event in state["events"]:
        reduce(model, event["command"], event["rules"], event["context"])
    return model


def report(state, rules, ctx):
    model = replay(state)
    from workflow.quality_write import snapshot, checkpoint_body
    publications = {key: dict(record, snapshot_current=record["snapshot"] == snapshot(model, rules, ctx, record.get("checkpoint")))
                    for key, record in model["publications"].items()}
    checkpoints = {c["id"]: checkpoint_view(model, c["id"], rules, ctx) for c in rules["checkpoints"]}
    for cp, view in checkpoints.items():
        view["published"] = any(r.get("checkpoint") == cp and r["snapshot_current"] and r["status"] == "verified"
                                for r in publications.values())
        if view["reviewed"]:
            view["publication_body"] = checkpoint_body(model, cp, rules, ctx)
    return {"issue_key": state["issue_key"], "run_id": state["run_id"], "revision": state["revision"],
            "context": ctx, "methods": rules["methods"],
            "items": {k: item_view(v, rules, ctx) for k, v in model["items"].items()},
            "checkpoints": checkpoints,
            "publications": publications,
            "boundary": "处置完成不等于测试全通过；本地确认来源由调用者提交，不能认证操作者身份。Jira 状态需外部回读。"}


def apply(base, issue, run_id, revision, command):
    quality_contract.validate(command, "quality-action.schema.json")
    with task_store.task_run_lock(base, issue):
        task_store.resolve_issue(base, issue)
        task = json.loads(task_store.task_path(base, issue).read_text(encoding="utf-8"))
        archived = task["run_id"] != run_id
        recovery = command["action"] in ("receipt", "readback")
        if task_store.task_status(base, issue) != "active" and command["action"] not in (
                "draft", "confirm", "prepare_write", "receipt", "readback"):
            raise ValueError("非 active 任务只允许证据回写及回读")
        if archived and recovery:
            task = dict(task, run_id=run_id)
            if not state_path(base, task).exists():
                raise ValueError("旧 run 无可恢复记录")
        if task["run_id"] != run_id:
            raise ValueError("任务 run 已变化，拒绝旧请求")
        rules = config(base)
        if not enabled(task, rules):
            raise ValueError("当前任务类型未启用质量检查")
        state = load(base, task)
        if type(revision) is not int or state["revision"] != revision:
            raise ValueError("质量 revision 已变化，先刷新再提交")
        if archived:
            rules = state["events"][-1]["rules"]
            ctx = state["events"][-1]["context"]
        else:
            ctx = context(base, task)
        if command["action"] == "prepare_write":
            from workflow.quality_write import check_unresolved_runs
            check_unresolved_runs(base, task)
        model = replay(state)
        reduce(model, command, rules, ctx)
        event = {"at": datetime.now(timezone.utc).isoformat(), "command": copy.deepcopy(command),
                 "rules": rules, "context": ctx}
        text = json.dumps(event, ensure_ascii=False)
        if project_rules.scan_sensitive(project_rules.load_admission(workspace=base), text):
            raise ValueError("质量输入含敏感内容，请脱敏后重新提交；未保存正文")
        state["events"].append(event)
        state["revision"] += 1
        quality_contract.validate(state, "quality-state.schema.json")
        path = state_path(base, task)
        # fsync before rename and fsync directory: intent must survive a process crash before external send.
        temporary = path.with_name(".%s.%s.tmp" % (path.name, os.getpid()))
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary.exists():
                temporary.unlink()
        return report(state, rules, ctx)


def advance_problems(base, task, target):
    rules = config(base)
    if not rules and project_rules.class_spec(project_rules.load_admission(workspace=base), task["task_class"]).get("quality_mode") == "recorded_decision":
        raise ValueError("质量模式已启用但 quality.json 缺失，不能降级为无检查")
    if not enabled(task, rules):
        return []
    points = rules["stage_checkpoints"].get(target, [])
    if not points:
        return []
    current = report(load(base, task), rules, context(base, task))
    problems = []
    for cp in points:
        view = current["checkpoints"][cp]
        if not view["reviewed"] or checkpoint_outcome(view) == "rework":
            problems.append("质量检查点 %s（%s）尚未确认处置：%s；%s 使用 quality.py status 的 handoff 展示具体用例、步骤、预期、版本和证据要求。"
                            % (cp, view["handoff"]["title"], "；".join(view["problems"]), view["handoff"]["request"]))
        if rules.get("require_checkpoint_publication") and view["reviewed"] and not view["published"]:
            problems.append("检查点 %s 已确认但 Jira 评论尚未回读；按 publication_body 执行 draft/confirm/prepare_write、原生发送及 receipt/readback，已有授权无需重复询问。" % cp)
        checkpoint_decision = (view["decision"] or {}).get("decision", {})
        if checkpoint_decision.get("deadline") and datetime.fromisoformat(
                checkpoint_decision["deadline"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
            problems.append("检查点 %s 延期已到期，需重新处置" % cp)
        for key in view["due"]:
            d = current["items"][key].get("decision")
            if d and d["decision"].get("deadline"):
                if datetime.fromisoformat(d["decision"]["deadline"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                    problems.append("%s 延期已到期，需重新处置" % key)
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "apply"))
    parser.add_argument("--dir", default=".")
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--input")
    parser.add_argument("--expected-run-id")
    parser.add_argument("--expected-revision", type=int)
    args = parser.parse_args()
    try:
        issue = task_store.resolve_issue(args.dir, args.issue_key)
        if args.command == "apply":
            if args.input is None or args.expected_run_id is None or args.expected_revision is None:
                raise ValueError("apply 需要 input、expected-run-id 和 expected-revision")
            result = apply(args.dir, issue, args.expected_run_id, args.expected_revision,
                           json.loads(Path(args.input).read_text(encoding="utf-8")))
        else:
            task = json.loads(task_store.task_path(args.dir, issue).read_text(encoding="utf-8"))
            rules = config(args.dir)
            if not enabled(task, rules):
                raise ValueError("当前任务未启用质量检查")
            result = report(load(args.dir, task), rules, context(args.dir, task))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        print("质量记录失败：%s" % error, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
