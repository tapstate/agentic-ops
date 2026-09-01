#!/usr/bin/env python3
"""AgenticOps CI 观察循环：按预算观察 PR 必需检查，超时或超预算转人工。

预算（与 agentic-ops development_change_v2 语义一致）：
  - 轮询间隔 15 秒
  - 5 分钟内检查未开始 -> 转人工（exit 3）
  - 开始后 10 分钟内未结束 -> 转人工（exit 3）
  - 自动修复最多 3 次（record-fix 记账，超出拒绝并转人工）

退出码：0=全部通过；2=有失败检查（可进入修复流程）；3=需人工介入；4=参数/环境错误。

用法：
  python3 workflow/ci.py watch --issue-key TAP-123 --repo owner/repo --pr 42 [--dir .]
  python3 workflow/ci.py record-fix --issue-key TAP-123 --pr 42 [--dir .]
  python3 workflow/ci.py status --issue-key TAP-123 --pr 42 [--dir .]

依赖 gh CLI（已登录）。判定逻辑与 gh 解耦，可单测（classify / budget_left）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workflow import task_store  # noqa: E402

POLL_INTERVAL = 15
START_TIMEOUT = 300
FINISH_TIMEOUT = 600
MAX_FIX_ATTEMPTS = 3

SUCCESS_STATES = {"SUCCESS", "NEUTRAL", "SKIPPED"}
FAILURE_STATES = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def classify(checks):
    """checks: statusCheckRollup 列表 -> ("none"|"pending"|"success"|"failure", failing_names)。"""
    if not checks:
        return "none", []
    failing = []
    pending = False
    for c in checks:
        state = (c.get("conclusion") or c.get("state") or "").upper()
        status = (c.get("status") or "").upper()
        if state in FAILURE_STATES:
            failing.append(c.get("name") or c.get("context") or "unknown")
        elif state in SUCCESS_STATES:
            continue
        elif status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "") and not state:
            pending = True
        elif state in ("PENDING", "EXPECTED"):
            pending = True
    if failing:
        return "failure", failing
    if pending:
        return "pending", []
    return "success", []


def budget_left(state, max_attempts=MAX_FIX_ATTEMPTS):
    return max(0, max_attempts - int(state.get("fix_attempts", 0)))


def state_path(base, issue_key, pr):
    return task_store.ci_path(base, issue_key, pr)


def load_state(base, issue_key, pr):
    path = state_path(base, issue_key, pr)
    if path.is_file():
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return {"pr": pr, "fix_attempts": 0, "history": []}


def save_state(base, issue_key, pr, state):
    path = state_path(base, issue_key, pr)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def fetch_rollup(repo, pr):
    proc = subprocess.run(
        ["gh", "pr", "view", str(pr), "--repo", repo, "--json", "statusCheckRollup,headRefOid"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError("gh 调用失败：%s" % proc.stderr.strip())
    doc = json.loads(proc.stdout)
    return doc.get("statusCheckRollup") or [], doc.get("headRefOid", "")


def cmd_watch(args):
    issue = task_store.resolve_active_issue(args.dir, args.issue_key)
    state = load_state(args.dir, issue, args.pr)
    started = time.time()
    first_seen = None
    head = ""
    while True:
        try:
            checks, head = fetch_rollup(args.repo, args.pr)
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as err:
            print("观察失败：%s" % err, file=sys.stderr)
            return 4
        verdict, failing = classify(checks)
        elapsed = time.time() - started
        print("[%ds] head=%s 检查=%d 判定=%s" % (elapsed, head[:8], len(checks), verdict))

        if verdict == "success":
            _log(state, args, "success", head, [])
            save_state(args.dir, issue, args.pr, state)
            print("全部必需检查通过。")
            return 0
        if verdict == "failure":
            _log(state, args, "failure", head, failing)
            save_state(args.dir, issue, args.pr, state)
            print("失败检查：%s" % ", ".join(failing))
            left = budget_left(state)
            if left <= 0:
                print("修复预算已用尽（%d 次），转人工。" % MAX_FIX_ATTEMPTS)
                return 3
            print("可进入修复流程（剩余预算 %d 次；修复前先 record-fix 记账）。" % left)
            return 2
        if verdict == "none":
            if elapsed > args.start_timeout:
                _log(state, args, "start_timeout", head, [])
                save_state(args.dir, issue, args.pr, state)
                print("%d 秒内检查未开始，转人工（可能无适用 Workflow 或判定未知——不得降级为无需 CI）。" % args.start_timeout)
                return 3
        else:  # pending
            if first_seen is None:
                first_seen = time.time()
            if time.time() - first_seen > args.finish_timeout:
                _log(state, args, "finish_timeout", head, [])
                save_state(args.dir, issue, args.pr, state)
                print("检查开始后 %d 秒未结束，转人工。" % args.finish_timeout)
                return 3
        time.sleep(args.interval)


def _log(state, args, verdict, head, failing):
    state["history"].append(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "verdict": verdict,
            "head": head,
            "failing": failing,
        }
    )


def cmd_record_fix(args):
    issue = task_store.resolve_active_issue(args.dir, args.issue_key)
    state = load_state(args.dir, issue, args.pr)
    if budget_left(state) <= 0:
        print("拒绝：修复预算已用尽（%d 次），必须转人工。" % MAX_FIX_ATTEMPTS, file=sys.stderr)
        return 3
    state["fix_attempts"] = int(state.get("fix_attempts", 0)) + 1
    state["history"].append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "verdict": "fix_attempt", "n": state["fix_attempts"]})
    save_state(args.dir, issue, args.pr, state)
    print("已记账第 %d 次修复（剩余 %d 次）。" % (state["fix_attempts"], budget_left(state)))
    return 0


def cmd_status(args):
    issue = task_store.resolve_issue(args.dir, args.issue_key)
    state = load_state(args.dir, issue, args.pr)
    print(json.dumps(state, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("watch")
    p.add_argument("--repo", required=True)
    p.add_argument("--pr", required=True)
    p.add_argument("--interval", type=int, default=POLL_INTERVAL)
    p.add_argument("--start-timeout", type=int, default=START_TIMEOUT)
    p.add_argument("--finish-timeout", type=int, default=FINISH_TIMEOUT)
    p.add_argument("--dir", default=".")
    p.add_argument("--issue-key")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("record-fix")
    p.add_argument("--pr", required=True)
    p.add_argument("--dir", default=".")
    p.add_argument("--issue-key")
    p.set_defaults(func=cmd_record_fix)

    p = sub.add_parser("status")
    p.add_argument("--pr", required=True)
    p.add_argument("--dir", default=".")
    p.add_argument("--issue-key")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    try:
        task_store.workspace_project(args.dir)
        task_store.migrate_legacy(args.dir)
        return args.func(args)
    except ValueError as error:
        print("错误：%s" % error, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
