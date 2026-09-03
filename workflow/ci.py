#!/usr/bin/env python3
"""AgenticOps CI 观察循环：按预算观察 PR 返回的检查，超时或超预算转人工。

预算（与 agentic-ops development_change_v2 语义一致）：
  - 轮询间隔 15 秒
  - 5 分钟内检查未开始 -> 转人工（exit 3）
  - 开始后 10 分钟内未结束 -> 转人工（exit 3）
  - 自动修复最多 3 次（record-fix 记账，超出拒绝并转人工）

退出码：0=所有返回的检查明确成功（不证明目标用例运行）；2=有失败检查（可进入修复流程）；3=需人工介入；4=参数/环境错误。

用法：
  python3 workflow/ci.py watch --issue-key TAP-123 --repo owner/repo --pr 42 [--dir .]
  python3 workflow/ci.py record-fix --issue-key TAP-123 --pr 42 [--dir .]
  python3 workflow/ci.py status --issue-key TAP-123 --pr 42 [--dir .]

依赖 gh CLI（已登录）。判定逻辑与 gh 解耦，可单测（classify / budget_left）。
"""
from __future__ import annotations

import argparse
import hashlib
import re
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

SUCCESS_STATES = {"SUCCESS"}
FAILURE_STATES = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}


def classify(checks):
    """只对明确成功返回 success；未知和跳过需要处置，不能证明用例通过。"""
    if not isinstance(checks, list):
        return "unknown", []
    if not checks:
        return "none", []
    failing = []
    pending = False
    unknown = False
    skipped = False
    for c in checks:
        if not isinstance(c, dict):
            unknown = True
            continue
        state = c.get("conclusion") or c.get("state") or ""
        status = c.get("status") or ""
        if not isinstance(state, str) or not isinstance(status, str):
            unknown = True
            continue
        state, status = state.upper(), status.upper()
        if state in FAILURE_STATES:
            failing.append(c.get("name") or c.get("context") or "unknown")
        elif status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING"):
            pending = True
        elif state in SUCCESS_STATES:
            continue
        elif status in ("QUEUED", "IN_PROGRESS", "PENDING", "WAITING", "") and not state:
            pending = True
        elif state in ("PENDING", "EXPECTED"):
            pending = True
        elif state in ("SKIPPED", "NEUTRAL"):
            skipped = True
        else:
            unknown = True
    if failing:
        return "failure", failing
    if pending:
        return "pending", []
    if unknown:
        return "unknown", []
    if skipped:
        return "skipped", []
    return "success", []


def budget_left(state, max_attempts=MAX_FIX_ATTEMPTS):
    return max(0, max_attempts - int(state.get("fix_attempts", 0)))


def identity(base, issue, repo, pr):
    task = json.loads(task_store.task_path(base, issue).read_text(encoding="utf-8"))
    known = [r["repository"] for r in task.get("repositories", [])]
    if repo is None and len(known) == 1:
        repo = known[0]
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("CI 需要明确的 --repo owner/repo")
    if repo not in known:
        raise ValueError("CI 仓库不属于当前任务")
    if not re.fullmatch(r"[1-9][0-9]*", str(pr)):
        raise ValueError("PR 编号必须为正整数")
    return {"schema_version": 2, "issue_key": issue, "run_id": task["run_id"], "repository": repo, "pr": str(pr)}


def state_path(base, issue_key, pr, repo=None):
    key = identity(base, issue_key, repo, pr)
    suffix = hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24]
    return task_store.task_directory(base, issue_key) / ("ci-%s.json" % suffix)


def load_state(base, issue_key, pr, repo=None):
    key = identity(base, issue_key, repo, pr)
    path = state_path(base, issue_key, pr, repo)
    if path.is_file():
        state = json.loads(path.read_text(encoding="utf-8"))
        if any(state.get(k) != v for k, v in key.items()):
            raise ValueError("CI 状态身份或版本不匹配")
        if type(state.get("revision")) is not int or type(state.get("fix_attempts")) is not int or not isinstance(state.get("history"), list):
            raise ValueError("CI 状态损坏")
        return state
    return dict(key, revision=0, fix_attempts=0, history=[])


def save_state(base, issue_key, pr, state, repo=None):
    repo = repo or state.get("repository")
    with task_store.task_run_lock(base, issue_key):
        task_store.resolve_active_issue(base, issue_key)
        current = load_state(base, issue_key, pr, repo)
        if state["run_id"] != current["run_id"] or state["revision"] != current["revision"]:
            raise ValueError("CI run 或 revision 已变化，重新读取后操作")
        state["revision"] += 1
        task_store._write_json_atomic(state_path(base, issue_key, pr, repo), state)


def current_states(base, task):
    states = []
    for path in sorted(task_store.task_directory(base, task["issue_key"]).glob("ci-*.json")):
        st = json.loads(path.read_text(encoding="utf-8"))
        # Legacy entries do not identify repository or run, and cannot be current evidence.
        if "schema_version" not in st:
            continue
        if st.get("schema_version") != 2:
            raise ValueError("不支持的 CI 状态版本，保留原文件")
        if st.get("run_id") != task["run_id"]:
            continue
        checked = load_state(base, task["issue_key"], st.get("pr"), st.get("repository"))
        if checked != st:
            raise ValueError("CI 状态路径与身份不一致")
        states.append(st)
    return states


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
    state = load_state(args.dir, issue, args.pr, getattr(args, "repo", None))
    started = time.time()
    first_seen = None
    head = ""
    while True:
        try:
            checks, head = fetch_rollup(args.repo, args.pr)
        except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as err:
            print("观察失败：%s" % err, file=sys.stderr)
            return 4
        if not head:
            print("PR Head 未知，无法关联验证证据。", file=sys.stderr)
            return 4
        verdict, failing = classify(checks)
        elapsed = time.time() - started
        print("[%ds] head=%s 检查=%d 判定=%s" % (elapsed, head[:8], len(checks), verdict))

        if verdict == "success":
            _log(state, args, "success", head, [], checks)
            save_state(args.dir, issue, args.pr, state)
            print("所有返回的检查明确成功；仍须核对目标用例是否实际执行及必需检查配置。")
            return 0
        if verdict in ("unknown", "skipped"):
            _log(state, args, verdict, head, [], checks)
            save_state(args.dir, issue, args.pr, state)
            print("检查含未知或跳过结果，需用户核对目标用例并决定处理。")
            return 3
        if verdict == "failure":
            _log(state, args, "failure", head, failing, checks)
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
                _log(state, args, "start_timeout", head, [], checks)
                save_state(args.dir, issue, args.pr, state)
                print("%d 秒内检查未开始，转人工（可能无适用 Workflow 或判定未知——不得降级为无需 CI）。" % args.start_timeout)
                return 3
        else:  # pending
            if first_seen is None:
                first_seen = time.time()
            if time.time() - first_seen > args.finish_timeout:
                _log(state, args, "finish_timeout", head, [], checks)
                save_state(args.dir, issue, args.pr, state)
                print("检查开始后 %d 秒未结束，转人工。" % args.finish_timeout)
                return 3
        time.sleep(args.interval)


def _log(state, args, verdict, head, failing, checks=None):
    state["history"].append(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "verdict": verdict,
            "head": head,
            "failing": failing,
            "checks": checks or [],
            "source": "gh.statusCheckRollup",
        }
    )


def cmd_record_fix(args):
    issue = task_store.resolve_active_issue(args.dir, args.issue_key)
    state = load_state(args.dir, issue, args.pr, getattr(args, "repo", None))
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
    state = load_state(args.dir, issue, args.pr, getattr(args, "repo", None))
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
    p.add_argument("--repo")
    p.add_argument("--pr", required=True)
    p.add_argument("--dir", default=".")
    p.add_argument("--issue-key")
    p.set_defaults(func=cmd_record_fix)

    p = sub.add_parser("status")
    p.add_argument("--repo")
    p.add_argument("--pr", required=True)
    p.add_argument("--dir", default=".")
    p.add_argument("--issue-key")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args()
    try:
        task_store.workspace_project(args.dir)
        task_store.migrate_legacy(args.dir)
        return args.func(args)
    except (ValueError, OSError) as error:
        print("错误：%s" % error, file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
