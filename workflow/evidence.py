#!/usr/bin/env python3
"""AgenticOps 证据总结：汇总任务状态、门禁审计和 CI 记录。

输出到 stdout（markdown）。回写方式：由 agent 通过 Atlassian MCP 的 add_comment
发布（write_jira_comment 属 free 操作，门禁直接放行）；发布前必须把全文展示给
研发工程师过目——证据本身是评论，但"宣布任务完成"的 Jira transition 仍是 gated。

硬约束（执行 projects/tapdata/admission.json 的规则，不依赖 agent 自觉）：
  - 证据全文过敏感内容扫描（token/口令/连接串/本机绝对路径）；命中即 exit 4，
    不输出任何证据正文——先把事实里的敏感内容改掉再重跑。
  - --verification 文本受 verification_rules 约束（禁 -DskipTests、禁占位词）。
  - 证据包含准入必填项覆盖情况，缺项直接显式列出，不允许静默省略。

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
from workflow import project_rules, task_store  # noqa: E402


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
                except json.JSONDecodeError:
                    continue
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


def build_summary(task, auth, events, ci_states, spec, verification=None):
    lines = []
    issue = (task or {}).get("issue_key", "（未初始化任务）")
    lines.append("h3. AI 执行证据总结：%s" % issue)
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
            lines.append("*已确认事实*：")
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
            "*门禁审计*：共 %d 次判定（放行 %d / 人工确认 %d / 拒绝 %d）"
            % (len(events), decisions.get("allow", 0), decisions.get("ask", 0), decisions.get("deny", 0))
        )
        denied = [e for e in events if e.get("decision") == "deny"]
        if denied:
            lines.append("*被拒绝的操作*（agent 未执行）：")
            for e in denied:
                lines.append("- %s：%s" % (e.get("operations"), e.get("note", "")[:120]))
        lines.append("")

    for st in ci_states:
        attempts = st.get("fix_attempts", 0)
        last = st.get("history", [])[-1] if st.get("history") else {}
        lines.append(
            "*CI（PR #%s）*：最终判定 %s，自动修复 %d 次" % (st.get("pr"), last.get("verdict", "无记录"), attempts)
        )
    if ci_states:
        lines.append("")

    lines.append("*边界声明*：合并、发布、Jira 完成态流转均未由 AI 执行，等待人工节点处理。")
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
    gate = task_store.task_directory(args.dir, issue)
    task = load_json(gate / "task.json")
    auth = load_json(gate / "authorization.json")
    events = load_events(gate / "events.jsonl")
    ci_states = []
    if gate.is_dir():
        for p in sorted(gate.glob("ci-*.json")):
            doc = load_json(p)
            if doc:
                ci_states.append(doc)

    spec = project_rules.load_admission(workspace=args.dir)
    if args.verification is not None:
        problems = project_rules.check_verification(spec, args.verification)
        if problems:
            for reason in problems:
                print("拒绝生成证据：验证结论不合规——%s" % reason, file=sys.stderr)
            return 4

    summary = build_summary(
        task, auth, events, ci_states, spec, verification=args.verification
    )
    hits = project_rules.scan_sensitive(spec, summary)
    if hits:
        print("拒绝生成证据：命中敏感内容规则（当前 Project admission.json evidence_rules）",
              file=sys.stderr)
        for lineno, line, reason in hits:
            print("  第 %d 行 %s：%s" % (lineno, reason, line[:120]), file=sys.stderr)
        print("请先修正任务事实/验证文本中的敏感内容再重跑；不要手工删改本工具输出。",
              file=sys.stderr)
        return 4

    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
