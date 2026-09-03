#!/usr/bin/env python3
"""AgenticOps 证据总结：汇总任务状态、门禁审计和 CI 记录。

输出到 stdout（markdown）。检查项事实与用户处置分别呈现，不把本地记录当作
外部认证。启用质量检查时由 quality.py draft/confirm/prepare_write/receipt/readback
管理回写恢复；外部发送仍由 Agent 原生工具执行。Jira 状态不由本工具改变。

用法：
  python3 workflow/evidence.py --issue-key TAP-123 [--dir .] [--verification "实际执行的验证命令及结果"]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import ci, project_rules, quality, task_store  # noqa: E402


def load_json(path):
    if path.is_file():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None


def load_events(path):
    events = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError("门禁事件损坏，保留现场，不能静默省略") from error
    return events


def _admission_coverage(task, spec):
    """准入必填项覆盖情况：齐备就一行带过，缺项必须显式列出。"""
    try:
        cls = project_rules.class_spec(spec, task.get("task_class"))
    except ValueError:
        return []
    facts = task.get("facts") or {}
    missing = project_rules.missing_required(spec, task.get("task_class"), facts)
    lines = [""]
    total = len(cls.get("required_facts", []))
    if missing:
        lines.append("*准入必填项*：%d/%d 齐备，仍缺：%s"
                     % (total - len(missing), total, "、".join(f["label"] for f in missing)))
    else:
        lines.append("*准入必填项*：%d/%d 齐备（%s）"
                     % (total, total, "、".join(f["label"] for f in cls.get("required_facts", []))))
    return lines


def build_summary(task, auth, events, ci_states, spec, verification=None, quality_report=None):
    lines = []
    issue = (task or {}).get("issue_key", "（未初始化任务）")
    lines.append("### AI 执行证据总结：%s" % issue)
    lines.append("")

    if task:
        lines.append("*任务类型*：%s　*当前阶段*：%s" % (task.get("task_class"), task.get("stage")))
        lines.append("")
        lines.append("*阶段轨迹*：")
        for h in task.get("history", []):
            if h.get("event") == "advance":
                lines.append("- %s 进入 %s：%s" % (h.get("ts"), h.get("stage"), h.get("note")))
            elif h.get("event") == "block":
                lines.append("- %s 阻塞：%s" % (h.get("ts"), h.get("reason")))
        if task.get("facts"):
            lines.append("")
            lines.append("*已记录事实（不代表用户确认）*：")
            for k, v in task["facts"].items():
                lines.append("- %s：%s" % (k, v))
        lines.extend(_admission_coverage(task, spec))
        lines.append("")

    if auth:
        lines.append(
            "*执行授权*：%s（计划 %s，状态 %s）"
            % (
                auth.get("agentic_run_id"),
                auth.get("approved_plan_version"),
                auth.get("status"),
            )
        )
        lines.append("*授权仓库*：")
        for repository in auth.get("repositories", []):
            lines.append(
                "- %s：%s -> %s；范围：%s；验证：%s"
                % (
                    repository.get("repository"),
                    repository.get("base_branch"),
                    repository.get("work_branch"),
                    repository.get("approved_scope"),
                    repository.get("verification_method"),
                )
            )
        lines.append("")

    if task and task.get("repositories"):
        lines.append("*各仓库交付结果*：")
        for repository in task["repositories"]:
            lines.append(
                "- %s：PR %s；CI %s"
                % (
                    repository.get("repository"),
                    repository.get("pull_request") or "未记录",
                    repository.get("ci") or "未记录",
                )
            )
        lines.append("")

    if verification:
        lines.append("*验证结果*：%s" % verification)
        lines.append("")

    if events:
        decisions = Counter(e.get("decision") for e in events)
        lines.append(
            "*门禁审计（任务累计记录）*：共 %d 次判定（放行 %d / 请求确认 %d / 拒绝 %d）"
            % (len(events), decisions.get("allow", 0), decisions.get("ask", 0), decisions.get("deny", 0))
        )
        denied = [e for e in events if e.get("decision") == "deny"]
        if denied:
            lines.append("*被拒绝的操作*（记录门禁判定，不证明操作未曾执行）：")
            for e in denied:
                lines.append("- %s：%s" % (e.get("operations"), e.get("note", "")[:120]))
        lines.append("")

    for st in ci_states:
        attempts = st.get("fix_attempts", 0)
        last = st.get("history", [])[-1] if st.get("history") else {}
        lines.append(
            "*CI（PR #%s）*：最近记录 %s，修复记账 %d 次" % (st.get("pr"), last.get("verdict", "无记录"), attempts)
        )
    if ci_states:
        lines.append("")

    if quality_report:
        lines.append("*质量检查（run %s，revision %s）*：" % (quality_report["run_id"], quality_report["revision"]))
        for key, item in quality_report["items"].items():
            plan = item["plan"]
            decision = (item.get("decision") or {}).get("decision", {})
            lines.append("- %s：%s / %s / %s；用户处置 %s（%s）；理由：%s" % (
                key, plan["case_ref"], plan["method"], plan["timing"], decision.get("outcome", "待决定"),
                "有效" if item["decision_valid"] else "未确认或已失效", decision.get("reason", "未记录")))
            lines.append("  - 用例版本 %s；目标代码 %s；范围 %s；预期 %s（%s）；方案%s选择" % (
                plan["case_version"], plan["target_revision"], plan["scope"], plan["criterion"],
                plan["expected_result"], "已" if item["selected"] else "尚未有效"))
            for execution in item["executions"]:
                lines.append("  - 执行 %s：原始 %s，来源 %s，版本 %s，证据 %s；环境 %s；观察 %s（%s）" % (
                    execution["id"], execution["raw_result"], execution["origin"],
                    execution["target_revision"], execution["source_ref"], execution["environment"],
                    execution["observation"], execution["observed_at"]))
            if decision.get("proof"):
                p = decision["proof"]
                lines.append("  - 决定者 %s；来源 %s / %s（%s）；选择执行 %s" % (
                    p["actor"], p["source"], p["reference"], p["at"], decision.get("evidence_id", "无")))
            if decision.get("owner"):
                lines.append("  - 责任人 %s；后续 %s；期限 %s" % (
                    decision["owner"], decision.get("follow_up", "未记录"), decision.get("deadline", "未设置")))
        for cp, view in quality_report["checkpoints"].items():
            lines.append("- 检查点 %s：%s；未到期 %s" % (
                cp, "已记录处置" if view["reviewed"] else "待核对", ", ".join(view["not_due"]) or "无"))
            decision = (view.get("decision") or {}).get("decision", {})
            if decision:
                p = decision["proof"]
                lines.append("  - %s：%s；决定者 %s；来源 %s（%s）；责任人 %s；后续 %s；期限 %s" % (
                    decision["outcome"], decision["reason"], p["actor"], p["reference"], p["at"],
                    decision.get("owner", "不适用"), decision.get("follow_up", "不适用"), decision.get("deadline", "未设置")))
        lines.append("")
    lines.append("*边界声明*：以上是本地执行记录及导入证据；合并、发布和 Jira 状态以外部回读为准。用户接受风险不等于测试通过。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=".")
    parser.add_argument("--issue-key")
    parser.add_argument("--verification", default=None)
    args = parser.parse_args()

    try:
        task_store.workspace_project(args.dir)
        task_store.migrate_legacy(args.dir)
        issue = task_store.resolve_issue(args.dir, args.issue_key)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 2
    try:
        task_dir = task_store.task_directory(args.dir, issue)
        task = load_json(task_store.task_path(args.dir, issue))
        auth = load_json(task_dir / "authorization.json")
        events = load_events(task_dir / "events.jsonl")
        ci_states = ci.current_states(args.dir, task)
        spec = project_rules.load_admission(workspace=args.dir)
        flexible = project_rules.class_spec(spec, task["task_class"]).get("quality_mode") == "recorded_decision"
        if args.verification is not None and not flexible:
            problems = project_rules.check_verification(spec, args.verification)
            if problems:
                raise ValueError("验证结论不合规：" + "；".join(problems))
        rules = quality.config(args.dir)
        if flexible and not quality.enabled(task, rules):
            raise ValueError("当前任务启用了质量处置，但缺少匹配的质量配置")
        quality_report = (quality.report(quality.load(args.dir, task), rules, quality.context(args.dir, task))
                          if quality.enabled(task, rules) else None)
        summary = build_summary(task, auth, events, ci_states, spec,
                                verification=args.verification, quality_report=quality_report)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("拒绝生成证据：%s" % error, file=sys.stderr)
        return 4
    hits = project_rules.scan_sensitive(spec, summary)
    if hits:
        print("拒绝生成证据：命中敏感内容规则（当前 Project admission.json evidence_rules）",
              file=sys.stderr)
        for lineno, line, reason in hits:
            print("  第 %d 行：%s（正文已隐藏）" % (lineno, reason), file=sys.stderr)
        print("请先修正任务事实/验证文本中的敏感内容再重跑；不要手工删改本工具输出。",
              file=sys.stderr)
        return 4

    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
