"""Jira 评论写入的本地意图/回执/回读账本；外部调用由原生工具负责。"""
from workflow import quality, task_store
import json
import re


STATE_FILE = re.compile(r"quality-[0-9a-f]{24}\.json$")


def snapshot(model, rules, ctx, checkpoint=None):
    if checkpoint:
        view = quality.checkpoint_view(model, checkpoint, rules, ctx)
        return quality.digest([view["digest"], view["decision"], view["reviewed"]])
    return quality.digest({"items": model["items"], "checkpoints": model["checkpoints"],
                           "verification": model.get("verification", {}),
                           "context": ctx, "rules": rules})


def checkpoint_body(model, checkpoint, rules, ctx):
    view = quality.checkpoint_view(model, checkpoint, rules, ctx)
    if not view["reviewed"]:
        raise ValueError("检查点尚未有效确认，不能生成已确认评论")
    if rules.get("comment_format") == "human-text-v2":
        return stage_summary(model, checkpoint, rules, ctx, view)
    if rules.get("comment_format") == "human-text-v1":
        decision = (view.get("decision") or {}).get("decision", {})
        facts = ctx["facts"]
        lines = ["%s：%s" % (ctx["issue_key"], view["handoff"]["title"]),
                 "处置：%s；%s" % (decision.get("outcome", "observed"), decision.get("reason", "已核验首轮执行事实"))]
        if any(decision.get(key) for key in ("follow_up", "owner", "deadline")):
            lines.append("检查点后续：%s；责任人：%s；期限：%s" % (
                decision.get("follow_up", "待确认"), decision.get("owner", "待确认"), decision.get("deadline", "待确认")))
        legacy_fields = (("problem_version", "本地问题版本"), ("problem_symptom", "问题现象"),
                           ("problem_branch", "问题分支"), ("reproduce_path", "复现路径"),
                           ("acceptance_criteria", "验收标准"), ("fix_plan", "修复方案"))
        for key, label in legacy_fields:
            if key == "fix_plan" and checkpoint == rules["checkpoints"][0]["id"]:
                continue
            if facts.get(key):
                if key == "fix_plan" and isinstance(facts[key], dict) and facts[key].get("format") == "structured-v1":
                    plan = facts[key]
                    lines.append("修复方案：结构化方案 structured-v1")
                    lines.extend("问题 %s：%s（来源：%s）" % (item.get("id", "?"), item.get("text", "待补充"), item.get("source_ref", "待补充"))
                                 for item in plan.get("problem_statements", []) if isinstance(item, dict))
                    lines.extend("根因假设 %s：覆盖 %s；状态 %s" % (item.get("id", "?"), "、".join(item.get("explains", [])), item.get("status", "待补充"))
                                 for item in plan.get("hypotheses", []) if isinstance(item, dict))
                    if plan.get("blocking_inputs"):
                        lines.append("仍缺输入：" + "；".join(str(item.get("request", "待补充")) for item in plan["blocking_inputs"] if isinstance(item, dict)))
                    lines.extend("Test 关联意图：验收项 %s，%s" % (item.get("item_id", "?"), item.get("case_status", "待补充"))
                                 for item in plan.get("test_links", []) if isinstance(item, dict))
                    lines.append("回滚：%s" % plan.get("rollback", "待补充"))
                else:
                    lines.append("%s：%s" % (label, facts[key]))
        # 保留既有缺陷正文；项目新增的方案事实完整输出，不在 Q1 提前发布方案。
        checkpoints = [point["id"] for point in rules["checkpoints"]]
        if checkpoints.index(checkpoint) >= checkpoints.index(rules["selection_checkpoint"]):
            rendered = {key for key, _ in legacy_fields}
            for key in rules.get("plan_fact_keys", []):
                if key not in rendered and key in facts:
                    lines.append("方案事实 %s：%s" % (key, json.dumps(facts[key], ensure_ascii=False, sort_keys=True)))
                    rendered.add(key)
        plan = facts.get("issue_version_plan", {})
        if plan.get("primary_branch"):
            lines.append("实施分支：%s" % plan["primary_branch"])
        if plan.get("sync_status") == "pending":
            lines.append("本地确认版本与 Jira 初始值存在差异；字段是否已同步以最新回读为准。")
        if plan.get("release_follow_up"):
            lines.append("后续：%s" % plan["release_follow_up"])
        for key, item in model["items"].items():
            selected = item["plan"]
            due = key in view["due"] or (view.get("mode") == "automatic" and selected["timing"] == "after_fix")
            if not due and checkpoint == rules["checkpoints"][0]["id"]:
                continue
            disposition = ((item.get("decision") or {}).get("decision", {}) if due else {})
            lines.append("验证 %s：%s；版本 %s；处置 %s；%s" % (
                selected["case_ref"], selected["method"], selected["target_revision"],
                disposition.get("outcome", "待验收"), disposition.get("reason", "")))
            assessment = quality.item_view(item, rules, ctx).get("jira_status")
            if assessment:
                lines.append("Jira 状态判定：%s；来源：%s；仅表示关联联调条件，本地旧报告不证明当前代码。" % (
                    "满足" if assessment["passed"] else "未满足", assessment.get("source_ref", "待回读")))
            executions = []
            if due:
                evidence_id = disposition.get("evidence_id") if view.get("mode") != "automatic" else None
                executions = [execution for execution in item["executions"]
                              if (execution["id"] == evidence_id if evidence_id else
                                  all(execution[field] == selected[field] for field in
                                      ("case_ref", "case_version", "method", "repository", "target_revision")))]
            for execution in executions[-1:]:
                lines.append("结果：%s；版本：%s；证据：%s" % (
                    execution["raw_result"], execution["target_revision"], execution["source_ref"]))
            if disposition.get("follow_up"):
                lines.append("后续：%s；责任人：%s；期限：%s" % (
                    disposition["follow_up"], disposition.get("owner", "待确认"), disposition.get("deadline", "待确认")))
        lines.append("记录：AO-" + quality.digest([ctx["issue_key"], ctx["run_id"], checkpoint, view["digest"]])[:20])
        for repo, entries in model.get("verification", {}).items():
            for kind in rules.get("verification_checkpoints", {}).get(checkpoint, []):
                if kind not in entries:
                    continue
                material = entries[kind]["data"]
                lines.append("验证 %s / %s：代码 %s；来源 %s" %
                             (repo, kind, material["target_revision"], material["source_ref"]))
                for item in material.get("results", []):
                    lines.append("范围 %s：%s；用例数 %s，失败 %s，错误 %s，跳过 %s；报告 %s；缺口处置 %s" %
                                 (item["scope"], item["result"], item.get("tests", "未知"), item.get("failures", "未知"),
                                  item.get("errors", "未知"), item.get("skipped", "未知"), item["report_ref"],
                                  json.dumps(item.get("decision"), ensure_ascii=False) if item.get("decision") else "无"))
        return "\n\n".join(lines)
    fact_keys = list(rules.get("intake_fact_keys", ctx["facts"]))
    if checkpoint != rules["checkpoints"][0]["id"]:
        fact_keys += rules.get("plan_fact_keys", [])
    facts = {k: v for k, v in ctx["facts"].items() if k in fact_keys}
    disposition = (view["decision"] or {}).get("decision")
    if view.get("mode") == "automatic":
        disposition = {"outcome": "observed", "reason": (view["decision"] or {}).get("reason"),
                       "basis": "已确认方案的全部修复后检查项在当前完整提交 SHA 上得到预期结果"}
    lines = ["%s / %s：%s" % (ctx["issue_key"], ctx["run_id"], view["handoff"]["title"]),
             "任务事实：" + json.dumps(facts, ensure_ascii=False, sort_keys=True),
             "检查点处置：" + json.dumps(disposition, ensure_ascii=False, sort_keys=True)]
    for key, item in model["items"].items():
        if key in view["due"] or (view.get("mode") == "automatic" and item["plan"]["timing"] == "after_fix"):
            content = {"plan": item["plan"], "executions": item["executions"], "decision": item["decision"]}
        elif checkpoint != rules["checkpoints"][0]["id"]:
            content = {"plan": quality.selection_plan(item["plan"], rules), "尚未到验收点": True}
        else:
            continue
        lines.append("检查项 %s：%s" % (key, json.dumps(content, ensure_ascii=False, sort_keys=True)))
    return "\n\n".join(lines)


def reduce(model, command, rules, ctx):
    action, p = command["action"], command["payload"]
    records = model["publications"]
    key = p["id"]
    if action == "draft":
        previous = records.get(key)
        if previous and previous["status"] in ("intent", "unknown", "created", "verified"):
            raise ValueError("该草稿已准备发送或已发送，保留现场；不得覆盖")
        cp = p.get("checkpoint")
        if cp and p["body"] != checkpoint_body(model, cp, rules, ctx):
            raise ValueError("检查点评论必须使用 status 返回的完整 publication_body，不能省略已确认方案或风险")
        records[key] = {"body": p["body"], "status": "draft", "snapshot": snapshot(model, rules, ctx, cp),
                        "site": rules["jira"]["site"], "issue_key": ctx["issue_key"]}
        if cp:
            records[key]["checkpoint"] = cp
        records[key]["digest"] = quality.digest(records[key])
        return
    record = records.get(key)
    if not record:
        raise ValueError("草稿不存在")
    if action in ("confirm", "prepare_write"):
        if p["digest"] != record["digest"] or record["snapshot"] != snapshot(model, rules, ctx, record.get("checkpoint")):
            raise ValueError("草稿正文或质量事实已变化，需要重新生成并确认")
        if action == "confirm":
            if record["status"] not in ("draft", "confirmed"):
                raise ValueError("草稿已进入发送流程")
            quality.check_proof(p["proof"])
            record["proof"] = p["proof"]
            record["status"] = "confirmed"
        else:
            if record["status"] != "confirmed":
                raise ValueError("只能准备已确认且尚未发送的草稿；未知结果不得重试")
            for other in records.values():
                if other is record:
                    continue
                if other["status"] in ("intent", "unknown", "created") and other["body"] == record["body"]:
                    raise ValueError("已有外部写入待核对，请先回读，不得并行重试")
                if other["status"] == "verified" and other["body"] == record["body"]:
                    raise ValueError("相同正文已回读确认，不重复发送")
            record["operation_id"] = quality.digest([ctx["issue_key"], ctx["run_id"], key, record["digest"]])
            record["status"] = "intent"
    elif action in ("receipt", "readback"):
        if p["operation_id"] != record.get("operation_id"):
            raise ValueError("外部操作编号不匹配")
        if record["status"] not in ("intent", "unknown", "created", "deferred"):
            raise ValueError("操作未准备发送或已经回读完成")
        if action == "receipt":
            if p["result"] == "deferred" and (not p.get("reason") or record["status"] != "intent"):
                raise ValueError("deferred 只用于已知未写入的 intent，必须说明 reason；未知结果先回读")
            if record["status"] == "created":
                raise ValueError("已有评论回执，下一步只能回读确认")
            if p["result"] == "created":
                if not p.get("comment_id"):
                    raise ValueError("成功回执必须有远端评论 ID")
                record["comment_id"] = p["comment_id"]
            elif p.get("comment_id"):
                raise ValueError("不明回执不得声称已知评论 ID")
            record["status"] = p["result"]
            if p.get("reason"):
                record["reason"] = p["reason"]
        else:
            if any(p[k] != record[k] for k in ("site", "issue_key")) or not body_matches(record["body"], p["body"], p.get("body_representation", "exact-text")):
                raise ValueError("回读目标或正文不匹配，保持待核对，不得重发")
            if record.get("comment_id") and p["comment_id"] != record["comment_id"]:
                raise ValueError("回读评论 ID 与回执不匹配")
            record.update({"status": "verified", "comment_id": p["comment_id"], "source_ref": p["source_ref"]})
    else:
        raise ValueError("未知质量操作")


def canonical_text(body):
    """仅规范化换行与行尾空白；不猜测 Markdown 转义、不忽略正文差异。"""
    return "\n".join(line.rstrip() for line in body.replace("\r\n", "\n").split("\n")).strip()


def body_matches(expected, actual, representation="exact-text"):
    """只允许经核验的字面文本转换；原文反斜杠、链接和代码不解码。"""
    expected, actual = canonical_text(expected), canonical_text(actual)
    if expected == actual:
        return True
    if representation != "literal-markdown-v1":
        return False
    # Jira Markdown 回读会为字面 [, ], _ 和 * 加反斜杠。
    # 向前逐字符匹配而非反转义，保留原文已有反斜杠及所有其它差异。
    index = 0
    for char in expected:
        if char in "[]_*" and actual[index:index + 2] == "\\" + char:
            index += 2
        elif actual[index:index + 1] == char:
            index += 1
        else:
            return False
    return index == len(actual)


def stage_summary(model, checkpoint, rules, ctx, view):
    """评论只承载阶段结论；方案及修复正文分别回填 Jira 专用字段。"""
    decision = (view.get("decision") or {}).get("decision", {})
    lines = ["%s：%s" % (ctx["issue_key"], view["handoff"]["title"]),
             "处置：%s；%s" % (decision.get("outcome", "observed"),
                                  decision.get("reason", "已核验首轮执行事实"))]
    if checkpoint == rules["selection_checkpoint"]:
        lines.append("实施方案：写入 Issue Analysis 后回读核验；本评论不代替字段同步。")
    if checkpoint == rules.get("tests_passed", {}).get("checkpoint"):
        lines.append("修复总结：写入 Fix Details 后回读核验；本评论不代替字段同步。")
    for repo, binding in sorted(ctx.get("repositories", {}).items()):
        lines.append("仓库 %s；版本 %s" % (repo, binding.get("live_revision", binding.get("base_sha", "待核验"))))
    for key, item in model["items"].items():
        plan = item["plan"]
        due = key in view["due"] or (view.get("mode") == "automatic" and plan["timing"] == "after_fix")
        if not due:
            continue
        disposition = (item.get("decision") or {}).get("decision", {})
        lines.append("验证 %s：%s；版本 %s；处置 %s；%s" % (
            plan["case_ref"], plan["method"], plan["target_revision"],
            disposition.get("outcome", "待验收"), disposition.get("reason", "")))
        executions = [e for e in item["executions"] if (
            e["id"] == disposition["evidence_id"] if disposition.get("evidence_id") else
            all(e[field] == plan[field] for field in ("case_ref", "case_version", "method", "repository", "target_revision")))]
        for execution in executions[-1:]:
            lines.append("结果：%s；版本：%s；证据：%s" % (
                execution["raw_result"], execution["target_revision"], execution["source_ref"]))
        assessment = quality.item_view(item, rules, ctx).get("jira_status")
        if assessment:
            lines.append("Jira 状态判定：%s；来源：%s" % (
                "满足" if assessment["passed"] else "未满足", assessment.get("source_ref", "待回读")))
        _follow_up(lines, disposition)
    for repo, entries in sorted(model.get("verification", {}).items()):
        for kind in rules.get("verification_checkpoints", {}).get(checkpoint, []):
            if kind not in entries:
                continue
            material = entries[kind]["data"]
            lines.append("验证 %s / %s；版本 %s；证据 %s" % (
                repo, kind, material["target_revision"], material["source_ref"]))
            for result in material.get("results", []):
                lines.append("范围 %s：%s；用例/失败/错误/跳过计数 %s/%s/%s/%s；报告 %s" % (
                    result["scope"], result["result"], result.get("tests", "未知"), result.get("failures", "未知"),
                    result.get("errors", "未知"), result.get("skipped", "未知"), result["report_ref"]))
                risk = result.get("decision") or {}
                if risk:
                    lines.append("缺口：%s；决定：%s" % (risk.get("uncovered", result["scope"]), risk.get("reason", "待核验")))
                    _follow_up(lines, risk)
    _follow_up(lines, decision)
    lines.append("记录：AO-" + quality.digest([ctx["issue_key"], ctx["run_id"], checkpoint, view["digest"]])[:20])
    return "\n\n".join(lines)


def _follow_up(lines, decision):
    if any(decision.get(key) for key in ("follow_up", "owner", "deadline")):
        lines.append("后续：%s；责任人：%s；期限：%s" % (
            decision.get("follow_up", "待确认"), decision.get("owner", "待确认"), decision.get("deadline", "待确认")))


def jira_field_body(base, task, field):
    """为现有 Jira 采集包提供人读提议；不发送、不自动确认或覆盖字段。"""
    if field == "issue_analysis":
        facts = task.get("facts", {})
        plan = facts.get("implementation_plan") or facts.get("fix_plan")
        if not plan:
            return None
        labels = {"objective": "目标", "changes": "实施变更", "acceptance": "验收安排",
                  "risks": "风险", "rollback": "回滚", "scope_rationale": "范围依据",
                  "acceptance_mapping": "验收映射", "delivery_dependencies": "交付依赖",
                  "environment_readiness": "环境准备", "integration_tests": "集成测试",
                  "reference_implementations": "同类实现", "public_layer_design": "公共层设计",
                  "problem_statements": "问题", "hypotheses": "根因假设", "blocking_inputs": "待决输入",
                  "test_links": "测试关联", "repository": "仓库", "module": "模块", "reason": "依据",
                  "expected": "预期", "steps": "步骤", "executor": "执行人", "source_ref": "证据",
                  "scope": "范围", "method": "方式", "criterion": "验收条件", "case_ids": "用例",
                  "text": "说明", "status": "状态", "rationale": "依据", "depends_on": "依赖"}
        def render(value, label="实施方案", depth=0):
            prefix = "  " * depth
            if isinstance(value, dict):
                lines = [prefix + label + "："]
                for key, child in value.items():
                    lines.extend(render(child, labels.get(key, key), depth + 1))
                return lines
            if isinstance(value, list):
                lines = [prefix + label + "："]
                for index, child in enumerate(value, 1):
                    lines.extend(render(child, str(index), depth + 1))
                return lines
            return [prefix + label + "：" + str(value)]
        lines = render(plan)
        if facts.get("scope_boundary"):
            lines += render(facts["scope_boundary"], "范围边界")
        return "\n".join(lines)
    if field == "fix_details":
        model = quality.replay(quality.load(base, task))
        if not any(item["executions"] for item in model["items"].values()) and not model.get("verification"):
            return None
        rules, ctx = quality.config(base, task), quality.context(base, task)
        checkpoint = rules.get("tests_passed", {}).get("checkpoint", "q4-acceptance")
        view = quality.checkpoint_view(model, checkpoint, rules, ctx)
        body = stage_summary(model, checkpoint, rules, ctx, view)
        return "修复总结（%s）：\n\n%s" % ("已验收" if view["reviewed"] else "待验收", body.replace(
            "修复总结：写入 Fix Details 后回读核验；本评论不代替字段同步。\n\n", ""))
    return None


def check_unresolved_runs(base, task, body):
    """reset 不抹去旧 run 的不明外部写入，避免恢复后再发一次。"""
    for path in task_store.task_directory(base, task["issue_key"]).glob("quality-*.json"):
        # 同一任务目录也保存 Agent 传给 quality.py 的输入文件，例如
        # quality-prepare-q1.json。它们不是质量状态日志，不能按状态契约解析。
        if not STATE_FILE.fullmatch(path.name):
            continue
        if path == quality.state_path(base, task):
            continue
        state = json.loads(path.read_text(encoding="utf-8"))
        quality.quality_contract.validate(state, "quality-state.schema.json")
        old = quality.replay(state)
        if any(r["status"] in ("intent", "unknown", "created") and r["body"] == body for r in old["publications"].values()):
            raise ValueError("旧 run 仍有外部写入结果待核对；保留旧记录并人工核对 Jira，不能重新发送")
