#!/usr/bin/env python3
"""AgenticOps 场景测试：验证标准 Gate 与 Claude/Codex Adapter 语义一致。

运行：python3 tests/test_gate.py
无第三方依赖。若本机存在 opa，会额外做 Python 评估器与 Rego 的一致性校验。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_HOOK = ROOT / "adapters" / "agents" / "claude" / "hook.py"
CODEX_HOOK = ROOT / "adapters" / "agents" / "codex" / "hook.py"
GATE_RUNNER = ROOT / "gate" / "runner.py"
sys.path.insert(0, str(ROOT))
from gate import engine  # noqa: E402
from workflow import task_store  # noqa: E402

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    ok = actual == expected
    PASS += ok
    FAIL += not ok
    mark = "PASS" if ok else "FAIL"
    print("[%s] %-58s -> %s (期望 %s)" % (mark, name, actual, expected))


def run_hook(tool_name, tool_input, cwd, env_extra=None):
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(cwd),
    }
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(CLAUDE_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    hso = out.get("hookSpecificOutput")
    return hso["permissionDecision"] if hso else "passthrough"


def run_codex(tool_name, tool_input, cwd):
    payload = {
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(cwd),
    }
    proc = subprocess.run(
        [sys.executable, str(CODEX_HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    if out.get("passthrough"):
        return "passthrough"
    return out.get("original_decision") or out["decision"]


def run_standard(request):
    proc = subprocess.run(
        [sys.executable, str(GATE_RUNNER)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def make_workspace(branch="feature/TAP-123", origin="git@github.com:acme/widget.git"):
    ws = Path(tempfile.mkdtemp(prefix="aogate-ws-"))
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=ws, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=ws, check=True)
    (ws / ".agenticops.json").write_text(
        json.dumps({"project": "tapdata"}), encoding="utf-8"
    )
    (ws / "README.md").write_text("poc\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=ws, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=ws,
        check=True,
    )
    return ws


def grant(ws, **overrides):
    repositories = [{
        "repository": overrides.get("target_repo", "acme/widget"),
        "work_branch": overrides.get("work_branch", "feature/TAP-123"),
        "base_branch": "develop",
        "approved_scope": "v1 测试范围",
        "verification_method": "python3 tests/test_gate.py",
        "pull_request": None,
        "ci": None,
    }]
    repositories.extend(overrides.get("extra_repositories", []))
    issue = overrides.get("issue_key", "TAP-123")
    task_store.register(ws, issue, status="active")
    gate = task_store.task_directory(ws, issue)
    gate.mkdir(parents=True, exist_ok=True)
    (gate / "task.json").write_text(
        json.dumps({
            "issue_key": issue,
            "task_class": "defect_fix",
            "stage": "design_review",
            "facts": {},
            "repositories": repositories,
            "pending": None,
            "history": [],
        }),
        encoding="utf-8",
    )
    subprocess.run(
        [
            sys.executable, str(ROOT / "workflow" / "authorization.py"), "grant",
            "--issue-key", issue,
            "--agent-id", "dev-bot-1",
            "--plan-version", "v1",
            "--dir", str(ws),
        ],
        check=True,
        capture_output=True,
    )


def main():
    ws = make_workspace()
    try:
        invalid = run_standard({})
        check("无效标准请求保守拒绝", invalid["decision"], "deny")
        unknown = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["future_external_write"],
        })
        check("未知标准操作转人工", unknown["decision"], "ask")

        # ---- 未授权阶段 -------------------------------------------------
        check("只读 bash（git status）不受控", run_hook("Bash", {"command": "git status"}, ws), "passthrough")
        check("Jira 评论（free）放行", run_hook("mcp__atlassian__add_comment", {"issueKey": "TAP-123"}, ws), "allow")
        check("无授权时 git commit 需确认", run_hook("Bash", {"command": "git commit -m 'x'"}, ws), "ask")
        check("无授权时 git push 需确认", run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws), "ask")
        check("无授权时 Jira transition 需确认", run_hook("mcp__atlassian__transition_issue", {"issueKey": "TAP-123"}, ws), "ask")

        # ---- 签发授权后 -------------------------------------------------
        grant(ws)
        check("授权后 git commit 放行", run_hook("Bash", {"command": "git commit -m 'x'"}, ws), "allow")
        check("授权后 push 工作分支放行", run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws), "allow")
        check("授权后 MCP 建 PR 放行", run_hook("mcp__github__create_pull_request", {"title": "t"}, ws), "allow")
        check(
            "MCP 显式指定未授权仓库时收回放行",
            run_hook(
                "mcp__github__create_pull_request",
                {"owner": "acme", "repo": "other-repo", "head": "feature/TAP-123"},
                ws,
            ),
            "ask",
        )
        check("授权后 gh pr create 放行", run_hook("Bash", {"command": "gh pr create --title t --body b"}, ws), "allow")

        # ---- 同一项目空间多个 active 任务按仓库+分支解析 -----------------
        grant(ws, issue_key="TAP-999", work_branch="feature/TAP-999")
        subprocess.run(["git", "checkout", "-q", "-b", "feature/TAP-999"], cwd=ws, check=True)
        check("第二个 active 任务使用自己的授权", run_hook("Bash", {"command": "git commit -m x"}, ws), "allow")
        subprocess.run(["git", "checkout", "-q", "feature/TAP-123"], cwd=ws, check=True)
        check("切回分支后恢复第一个任务授权", run_hook("Bash", {"command": "git commit -m x"}, ws), "allow")
        grant(ws, issue_key="TAP-999", work_branch="feature/TAP-123")
        check("两个 active 任务上下文冲突时保守停止", run_hook("Bash", {"command": "git commit -m x"}, ws), "ask")
        grant(ws, issue_key="TAP-999", work_branch="feature/TAP-999")

        # ---- 授权伞永不覆盖的高危操作 -----------------------------------
        check("merge 始终需单独确认", run_hook("Bash", {"command": "git merge develop"}, ws), "ask")
        check("gh pr merge 始终需单独确认", run_hook("Bash", {"command": "gh pr merge 42 --squash"}, ws), "ask")
        check("gh release 始终需单独确认", run_hook("Bash", {"command": "gh release create v1.0"}, ws), "ask")
        check("Jira transition 不在伞内，需确认", run_hook("mcp__atlassian__transition_issue", {"issueKey": "TAP-123"}, ws), "ask")

        # ---- forbidden --------------------------------------------------
        check("强推直接拒绝", run_hook("Bash", {"command": "git push --force origin feature/TAP-123"}, ws), "deny")
        check("commit --amend（改历史）拒绝", run_hook("Bash", {"command": "git commit --amend"}, ws), "deny")
        check("push 保护分支拒绝", run_hook("Bash", {"command": "git push origin main"}, ws), "deny")
        check("push release/* 保护分支拒绝", run_hook("Bash", {"command": "git push origin HEAD:release/v0.7"}, ws), "deny")

        # ---- 绑定失效 ---------------------------------------------------
        subprocess.run(["git", "checkout", "-q", "-b", "other-branch"], cwd=ws, check=True)
        check("切到未授权分支后 push 收回放行", run_hook("Bash", {"command": "git push origin other-branch"}, ws), "ask")
        subprocess.run(["git", "checkout", "-q", "feature/TAP-123"], cwd=ws, check=True)
        check("切回授权分支恢复放行", run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws), "allow")

        second = ws / "service-api"
        second.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "feature/TAP-123-api"], cwd=second, check=True)
        subprocess.run(["git", "remote", "add", "origin", "git@github.com:acme/service-api.git"], cwd=second, check=True)
        (second / "README.md").write_text("api\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=second, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
            cwd=second,
            check=True,
        )
        grant(ws, extra_repositories=[{
            "repository": "acme/service-api",
            "work_branch": "feature/TAP-123-api",
            "base_branch": "develop",
            "approved_scope": "API 配套修改",
            "verification_method": "python3 -m unittest",
            "pull_request": None,
            "ci": None,
        }])
        check("同一任务第二仓库 commit 放行", run_hook("Bash", {"command": "git commit -m x"}, second), "allow")
        check("第二仓库审计写到 TAP-123 任务目录", task_store.events_path(ws, "TAP-123").is_file(), True)

        grant(ws, target_repo="acme/other-repo")
        check("授权仓库不匹配收回放行", run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws), "ask")

        grant(ws)
        subprocess.run([sys.executable, str(ROOT / "workflow" / "authorization.py"), "revoke", "--issue-key", "TAP-123", "--dir", str(ws)], check=True, capture_output=True)
        check("撤销授权后收回放行", run_hook("Bash", {"command": "git commit -m x"}, ws), "ask")

        # ---- 未知外部写 -------------------------------------------------
        check("未知 mcp 写操作需确认", run_hook("mcp__github__run_secret_scanning", {}, ws), "ask")
        check("gh api POST 需确认", run_hook("Bash", {"command": "gh api -X POST /repos/a/b/issues -f title=x"}, ws), "ask")
        check("复合命令取最严格（status; push --force）", run_hook("Bash", {"command": "git status && git push -f origin feature/TAP-123"}, ws), "deny")

        # ---- Agent Adapter 语义一致性 -----------------------------------
        parity_cases = [
            ("Bash", {"command": "git commit -m x"}),
            ("Bash", {"command": "git push origin feature/TAP-123"}),
            ("Bash", {"command": "git push origin main"}),
            ("Bash", {"command": "git merge develop"}),
            ("mcp__atlassian__transition_issue", {"issueKey": "TAP-123"}),
            ("mcp__atlassian__add_comment", {"issueKey": "TAP-123"}),
        ]
        for tool, tool_input in parity_cases:
            claude = run_hook(tool, tool_input, ws)
            codex = run_codex(tool, tool_input, ws)
            check("Claude/Codex 标准语义一致：%s" % tool.split("__")[-1], codex, claude)

        # ---- 审计留痕 ---------------------------------------------------
        events = task_store.events_path(ws, "TAP-123").read_text(encoding="utf-8").strip().splitlines()
        check("审计事件已记录（>=20 条）", len(events) >= 20, True)

        # ---- OPA 一致性 -------------------------------------------------
        if shutil.which("opa"):
            grant(ws)
            parity_cases = [
                ("Bash", {"command": "git commit -m x"}),
                ("Bash", {"command": "git push origin feature/TAP-123"}),
                ("Bash", {"command": "git push origin main"}),
                ("Bash", {"command": "git merge develop"}),
                ("mcp__atlassian__transition_issue", {"issueKey": "TAP-123"}),
                ("mcp__atlassian__add_comment", {"issueKey": "TAP-123"}),
            ]
            for tool, tin in parity_cases:
                py = run_hook(tool, tin, ws)
                opa = run_hook(tool, tin, ws, env_extra={"AO_GATE_USE_OPA": "1"})
                check("OPA 一致性：%s %s" % (tool.split("__")[-1], tin.get("command", "")), opa, py)
        else:
            print("[SKIP] 未安装 opa，跳过一致性校验")
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
