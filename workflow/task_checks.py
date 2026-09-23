"""共享阶段检查；只读核对现役规则，不承担 CLI 或生命周期写入。"""
from gate import engine
from workflow import authorization, issue_versions, project_rules, quality, station_source


def check_advance(task, target, base, spec):
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
                raise ValueError("完整工程源码尚未准备完成")
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
        elif auth.get("repositories") != authorization.repository_bindings(task.get("repositories", [])):
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
