#!/usr/bin/env python3
"""核对缺陷任务进入 PR Ready 前的测试任务、PR Checks 与本地检查项。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import ci, jira_status, quality, task_store  # noqa: E402


def jira_test_tasks(path, issue_key):
    document = jira_status.read_input(path)
    issue = document.get("issue") or {}
    if str(issue.get("key") or "").upper() != issue_key:
        raise ValueError("Jira 验收快照不是当前任务")
    tasks = document.get("linked_test_tasks")
    if not isinstance(tasks, list):
        raise ValueError("Jira 验收快照缺少 linked_test_tasks")
    problems = []
    if not tasks:
        problems.append("Jira 未返回关联测试任务；需关联测试任务，或由用户明确处理不适用场景")
    for item in tasks:
        status = item.get("status") if isinstance(item, dict) else None
        category = (status or {}).get("statusCategory") or {}
        if not isinstance(item, dict) or not item.get("key") or not isinstance(status, dict):
            problems.append("关联测试任务快照结构不完整")
        elif str(category.get("key") or "").lower() != "done":
            problems.append("测试任务 %s 尚未完成（%s）" % (item["key"], status.get("name") or "状态未知"))
    return problems, document["source_ref"]


def quality_problems(base, task, rules):
    result = quality.report(quality.load(base, task), rules, quality.context(base, task))
    accepted = set(rules["pr_ready"]["accepted_outcomes"])
    problems = []
    for checkpoint in rules["pr_ready"]["required_checkpoints"]:
        view = result["checkpoints"].get(checkpoint)
        if not view or not view["reviewed"]:
            problems.append("任务检查点 %s 尚未有效确认" % checkpoint)
            continue
        outcome = ((view.get("decision") or {}).get("decision") or {}).get("outcome")
        if outcome not in accepted:
            problems.append("任务检查点 %s 的处置为 %s，不满足 PR Ready" % (checkpoint, outcome or "缺失"))
        if rules["pr_ready"].get("require_verified_publication") and not view.get("published"):
            problems.append("任务检查点 %s 尚未完成 Jira 评论回读" % checkpoint)
    final_checkpoint = rules["pr_ready"]["required_checkpoints"][-1]
    due = set(result["checkpoints"][final_checkpoint]["due"])
    for key in due:
        item = result["items"][key]
        decision = ((item.get("decision") or {}).get("decision") or {})
        if not item.get("decision_valid") or decision.get("outcome") not in accepted:
            problems.append("任务检查项 %s 未通过或未明确不适用" % key)
    return problems


def local_head(repository):
    worktree = repository.get("worktree") or {}
    if worktree.get("status") == "prepared":
        try:
            return quality.git_revision(worktree["path"])
        except (ValueError, OSError):
            return ""
    return worktree.get("final_revision") or ""


def ci_problems(base, task):
    states = ci.current_states(base, task)
    by_repository = {state["repository"]: state for state in states}
    problems = []
    for repository in task.get("repositories", []):
        name = repository["repository"]
        pr = repository.get("pull_request")
        if not pr:
            problems.append("仓库 %s 尚未记录 PR" % name)
            continue
        state = by_repository.get(name)
        if not state or state.get("pr") != str(pr) or not state.get("history"):
            problems.append("仓库 %s 的 PR %s 尚无当前 run 的 Checks 记录" % (name, pr))
            continue
        latest = state["history"][-1]
        if latest.get("verdict") != "success":
            problems.append("仓库 %s 的 PR Checks 未全部明确成功（%s）" % (name, latest.get("verdict") or "未知"))
        head = local_head(repository)
        if ":worktree:" in head:
            problems.append("仓库 %s 存在未提交修改，不能进入 PR Ready" % name)
        elif not head or latest.get("head") != head:
            problems.append("仓库 %s 的 PR Checks Head 与当前任务代码不一致" % name)
    return problems


def check(base, issue_key, jira_input):
    task = json.loads(task_store.task_path(base, issue_key).read_text(encoding="utf-8"))
    rules = quality.config(base)
    if not rules or not isinstance(rules.get("pr_ready"), dict):
        raise ValueError("当前 Project 未配置 PR Ready 验收")
    linked_problems, source_ref = jira_test_tasks(jira_input, issue_key)
    groups = {
        "linked_test_tasks": linked_problems,
        "pr_checks": ci_problems(base, task),
        "task_checks": quality_problems(base, task, rules),
    }
    if task.get("stage") != "ci_validation":
        groups["task_checks"].append("本地任务尚未到 ci_validation，不能进入 PR Ready 核对")
    status = jira_status.load_state(base, task)
    status_todos = []
    labels = {"takeover": "In Progress", "tests_passed": "Tests Passed"}
    tests = status["attempts"].get("tests_passed")
    if not tests or tests.get("outcome") not in ("satisfied", "succeeded"):
        for trigger in ("takeover", "tests_passed"):
            attempt = status["attempts"].get(trigger)
            if attempt and attempt.get("outcome") in ("satisfied", "succeeded"):
                continue
            status_todos.append("Jira %s 未确认；请根据状态同步记录和 Jira 提示人工处理" % labels[trigger])
            if attempt:
                status_todos.extend(item["guidance"] for item in attempt.get("guidance", [])
                                    if item.get("guidance"))
    problems = [problem for values in groups.values() for problem in values]
    return {"issue_key": issue_key, "run_id": task["run_id"], "source_ref": source_ref,
            "ready": not problems,
            "checks": {key: {"passed": not value, "problems": value} for key, value in groups.items()},
            "jira_status_todos": status_todos,
            "next": "三类验收通过；由 Engineering DRI 人工执行 Pull Request Submitted。" if not problems
                    else "处理上述验收问题后重新检查；Jira 状态同步问题不阻断本地修复，但需在正式提审前人工处理。"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue-key", required=True)
    parser.add_argument("--jira-input", required=True)
    parser.add_argument("--dir", default=".")
    args = parser.parse_args()
    try:
        task_store.workspace_project(args.dir)
        issue = task_store.resolve_active_issue(args.dir, args.issue_key)
        result = check(args.dir, issue, args.jira_input)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready"] else 3
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
