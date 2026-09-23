#!/usr/bin/env python3
"""AgenticOps 场景测试：验证显式标准 Gate 的授权、策略和审计边界。

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
GATE_RUNNER = ROOT / "gate" / "runner.py"
sys.path.insert(0, str(ROOT))
from gate import engine  # noqa: E402
from workflow import jira_watermark, quality, task_store  # noqa: E402
from station_fixture import save_task as save_station_task

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    ok = actual == expected
    PASS += ok
    FAIL += not ok
    mark = "PASS" if ok else "FAIL"
    print("[%s] %-58s -> %s (期望 %s)" % (mark, name, actual, expected))


def ready_watermark(issue_key, run_id, value):
    field_id = "customfield_10421"
    return {
        "schema_version": 1, "issue_key": issue_key, "run_id": run_id,
        "watermark": {
            "at": "2026-09-04T00:00:00+0000", "source_ref": "fixture:jira/" + issue_key,
            "issue_key": issue_key, "field_id": field_id, "field_name": "AgenticOps Version",
            "logical_key": "agenticops_version", "issue_type_id": "10011", "version": value,
            "write_mode": "overwrite", "payload_digest": jira_watermark.payload_digest(field_id, value),
            "outcome": "ready", "reason": "watermark_prepared",
            "native_request": {"issue_key": issue_key, "fields": {field_id: value}},
        },
    }


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


def make_station(branch="feature/TAP-123", origin="git@github.com:acme/widget.git", initialize_git=True):
    ws = Path(tempfile.mkdtemp(prefix="aogate-ws-"))
    if initialize_git:
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
    (ws / ".agenticops" / "station.json").write_text(
        json.dumps({
            "schema_version": 1, "product_root": str(product_root),
            "project": "tapdata", "agents": ["claude", "codex"],
        }), encoding="utf-8"
    )
    (ws / "README.md").write_text("poc\n", encoding="utf-8")
    if initialize_git:
        subprocess.run(["git", "add", "."], cwd=ws, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
            cwd=ws,
            check=True,
        )
    task_store.initialize_current(ws)
    return ws


def occupy(ws, issue):
    save_station_task(ws, {"issue_key": issue, "run_id": "run-fixture", "task_class": "technical_task",
                          "stage": "task_intake", "facts": {}, "repositories": [], "history": [], "pending": None})


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
    run_id = "run-" + ("1" if issue == "TAP-123" else "9") * 12
    facts = {
        "fix_plan": {
            "format": "structured-v1",
            "problem_statements": [{"id": "P1", "text": "固定 Gate 场景的预期行为", "source_ref": "fixture:jira"}],
            "evidence": [{"id": "F1", "source_ref": "fixture:source", "observation": "夹具源码与基线已核对"}],
            "hypotheses": [{"id": "H1", "explains": ["P1"], "evidence_ids": ["F1"],
                            "status": "confirmed", "falsifier": "针对性验证不再复现"}],
            "blocking_inputs": [], "changes": [{"scope": "固定 Gate 夹具范围", "hypothesis_ids": ["H1"]}],
            "risks": ["仅覆盖夹具"], "rollback": "回退夹具变更",
            "test_links": [{"item_id": "fixture-after-fix", "case_status": "proposed", "owner": "fixture-tester"}],
        },
    }
    save_station_task(ws, {
            "issue_key": issue,
            "run_id": run_id,
            "task_class": "defect_fix",
            "stage": "design_review",
            "facts": facts,
            "repositories": repositories,
            "pending": None,
            "history": [],
        })
    task = task_store.read_task(ws, issue)
    rules = quality.config(ws)
    view = quality.report(quality.load(ws, task), rules, quality.context(ws, task), base=ws, task=task)

    def apply(action, payload):
        nonlocal view
        view = quality.apply(ws, issue, run_id, view["revision"], {"action": action, "payload": payload})

    proof = {"actor": "fixture-user", "source": "user_message", "reference": "fixture:plan",
             "at": "2026-09-03T10:00:00+08:00"}
    apply("checkpoint", {"checkpoint": "q1-intake", "digest": view["checkpoints"]["q1-intake"]["digest"],
                           "decision": {"outcome": "not_applicable", "reason": "Gate 夹具不覆盖接管质量场景", "proof": proof}})
    plan = {"id": "fixture-after-fix", "checkpoint": "q4-acceptance", "timing": "after_fix",
            "case_ref": "fixture:after-fix", "case_version": "fixture-v1", "case_status": "proposed",
            "method": "integration", "repository": target_repo, "target_revision": "1" * 40,
            "criterion": "固定 Gate 验收夹具应通过", "steps": "执行固定 Gate 验收夹具",
            "expected_result": "PASS", "scope": "仅验证 Gate 基础能力"}
    apply("item", {"plan": plan, "reason": "Q2 定义修复后验证意图"})
    apply("select", {"item_id": plan["id"], "digest": view["items"][plan["id"]]["plan_digest"], "proof": proof})
    apply("checkpoint", {"checkpoint": "q2-plan", "digest": view["checkpoints"]["q2-plan"]["digest"],
                           "decision": {"outcome": "accept", "reason": "夹具已确认方案及 Test 关联意图", "proof": proof}})
    subprocess.run(
        [
            sys.executable, str(ROOT / "workflow" / "authorization.py"), "grant",
            "--issue-key", issue,
            "--expected-run-id", run_id,
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
    (path / "README.md").write_text("工位 source\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=path,
        check=True,
    )
    return path


def prepare_task_worktree(ws, issue_key="TAP-123"):
    task = task_store.read_task(ws, issue_key)
    worktree = make_git_repository(
        ws / "source" / "acme" / "widget"
    )
    task["repositories"][0]["worktree"] = {"status": "prepared", "path": str(worktree)}
    save_station_task(ws, task)
    return worktree


def decision(operation, ws, **target):
    """只构造标准请求，不解析原生工具或命令。"""
    return run_standard({
        "protocol_version": 1, "event": "before_operation",
        "source": {"agent": "test", "adapter": "standard-api", "adapter_version": 1},
        "cwd": str(ws), "operations": [operation], "target": target,
    })["decision"]


def push(ws, **target):
    refs = {"push_source_ref": "feature/TAP-123",
            "push_destination_ref": "refs/heads/feature/TAP-123", "push_target_branch": "feature/TAP-123"}
    refs.update(target)
    return decision("git_push", ws, **refs)


def main():
    ws = make_station()
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
            check("源码产品根目录不生成工位状态", (product_root / ".agenticops").exists(), False)

        lifecycle = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["manage_station"],
        })
        check("工位操作需人工确认", lifecycle["decision"], "ask")
        check("工位操作不借用任务授权", lifecycle["reason_code"], "station_confirmation_required")
        check("归档不授权删除", "归档不授予删除权限" in lifecycle["required_action"], True)
        occupy(ws, "TAP-123")
        missing_auth = run_standard({
            "protocol_version": 1,
            "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws),
            "operations": ["git_commit"],
            "target": {"issue_key": "TAP-123"},
        })
        check("当前任务无授权使用独立原因码", missing_auth["reason_code"], "authorization_missing")
        check("无授权响应给出处理方式", bool(missing_auth.get("required_action")), True)

        authorization_path = task_store.authorization_path(ws, "TAP-123")
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
        check("无 当前任务使用独立原因码", no_task["reason_code"], "no_active_task")

        # ---- 签发授权后 -------------------------------------------------
        grant(ws)
        task_worktree = prepare_task_worktree(ws)
        check(
            "当前会话通过已绑定 git -C worktree commit 放行",
            decision("git_commit", ws, git_cwd=str(task_worktree)),
            "allow",
        )
        check(
            "当前会话通过已绑定 git -C worktree push 放行",
            push(ws, git_cwd=str(task_worktree)),
            "allow",
        )
        other_worktree = make_git_repository(ws / "other-worktree")
        check(
            "同仓同分支的其它 git -C 路径不得借用授权",
            decision("git_commit", ws, git_cwd=str(other_worktree)),
            "ask",
        )
        check("授权后 git commit 放行", decision("git_commit", ws), "allow")
        check("未配置独立 pushurl 时按 fetch URL 放行", push(ws), "allow")
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
            push(ws),
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
            push(ws),
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
            push(ws),
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
            push(ws),
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
            push(ws),
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
        check("唯一且匹配授权的 pushurl 放行", push(ws), "allow")
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
            push(ws),
            "deny",
        )
        subprocess.run(
            ["git", "remote", "set-url", "origin", "git@github.com:acme/widget.git"],
            cwd=ws,
            check=True,
        )
        check(
            "正常 GitHub fallback 四方 endpoint 放行",
            push(ws),
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
                push(ws),
                "deny",
            )
        legacy_authorization = json.loads(json.dumps(original_authorization))
        legacy_authorization["repositories"][0].pop("authorized_endpoint", None)
        authorization_path.write_text(json.dumps(legacy_authorization), encoding="utf-8")
        check(
            "旧授权缺 endpoint 的非 push 操作保持 v1 兼容",
            decision("git_commit", ws),
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
        check("HEAD 推送授权工作分支放行", push(ws, push_source_ref="HEAD"), "allow")
        check("完整 heads source 推送同名授权分支放行", push(ws, push_source_ref="refs/heads/feature/TAP-123"), "allow")
        check("push destination 越过授权分支时拒绝", push(ws, push_source_ref="HEAD", push_destination_ref="feature/TAP-999", push_target_branch="feature/TAP-999"), "deny")
        check("任意 source 不得写入授权分支", push(ws, push_source_ref="evil"), "deny")
        check("push destination 禁止 tags namespace", push(ws, push_source_ref="HEAD", push_destination_ref="refs/tags/v1", push_target_branch="refs/tags/v1"), "deny")
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

        # 平台事件退役不改变显式 Gate API 的操作与授权策略。
        for operation in ("git_merge", "pr_merge", "release", "git_tag", "manage_repository_worktree"):
            check("高风险操作单独确认：" + operation, decision(operation, ws), "ask")
        for operation in ("force_push", "history_rewrite"):
            check("禁止操作拒绝：" + operation, decision(operation, ws), "deny")
        for branch in ("main", "release/v1"):
            check("保护分支拒绝：" + branch, push(ws, push_destination_ref=branch, push_target_branch=branch), "deny")
        check("评论 free 不依赖授权", decision("write_jira_comment", ws), "allow")
        check("未覆盖 Jira 转换不借用授权", decision("transition_jira_status", ws, issue_key="TAP-123", branch_relevant=False), "ask")
        for operation in ("create_pr", "update_pr", "fix_pr_comments"):
            check("授权内 PR 操作：" + operation, decision(operation, ws, repository="acme/widget"), "allow")
            check("PR 不跨仓库借授权：" + operation, decision(operation, ws, repository="acme/other-repo"), "ask")

        # 显式 API 的一次性意图语义继续保留，但不宣称原生调用被拦截。
        task_store._write_json_atomic(
            task_store.task_directory(ws, "TAP-123") / "jira-status-fixture.json",
            {"schema_version": 1, "issue_key": "TAP-123", "run_id": "run-111111111111",
             "attempts": {"takeover": {"outcome": "ready", "transition_id": "421"}}},
        )
        for expected in ("allow", "ask"):
            check("标准 Jira 意图只消费一次", decision("transition_jira_status", ws,
                  issue_key="TAP-123", jira_transition_id="421", branch_relevant=False), expected)
        value = "develop-v1.0-99-1234abcd"
        task_store._write_json_atomic(
            task_store.task_directory(ws, "TAP-123") / "jira-watermark-fixture.json",
            ready_watermark("TAP-123", "run-111111111111", value),
        )
        for expected in ("allow", "ask"):
            check("标准水印意图只消费一次", decision("edit_jira_issue", ws,
                  issue_key="TAP-123", jira_watermark_field="customfield_10421",
                  jira_watermark_digest=jira_watermark.payload_digest("customfield_10421", value),
                  branch_relevant=False), expected)

        original_authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        for field, value in (("status", "revoked"), ("expires_at_epoch", 1)):
            malformed = dict(original_authorization, **{field: value})
            authorization_path.write_text(json.dumps(malformed), encoding="utf-8")
            check("失效授权收回放行：" + field, decision("git_commit", ws), "ask")
        authorization_path.write_text(json.dumps(original_authorization), encoding="utf-8")
        subprocess.run(["git", "checkout", "-q", "-b", "other-branch"], cwd=ws, check=True)
        check("分支变化收回授权", decision("git_commit", ws), "ask")
        subprocess.run(["git", "checkout", "-q", "feature/TAP-123"], cwd=ws, check=True)
        check("分支恢复授权", decision("git_commit", ws), "allow")

        branchless_ws = make_station(initialize_git=False)
        try:
            grant(branchless_ws)
            source = prepare_task_worktree(branchless_ws)
            check("工位根缺分支不放行 PR", decision("update_pr", branchless_ws, repository="acme/widget"), "ask")
            check("已绑定 source 提供 PR 分支上下文", decision("update_pr", branchless_ws,
                  git_cwd=str(source), repository="acme/widget"), "allow")
            other_source = make_git_repository(branchless_ws / "other-source")
            check("同仓同分支的未绑定路径不借授权", decision("git_commit", branchless_ws,
                  git_cwd=str(other_source)), "ask")
            relocated = branchless_ws / "relocated-source"
            source.rename(relocated)
            source.symlink_to(relocated, target_is_directory=True)
            try:
                check("source 链接漂移不借授权", decision("git_commit", branchless_ws, git_cwd=str(source)), "ask")
            finally:
                source.unlink()
                relocated.rename(source)
        finally:
            shutil.rmtree(branchless_ws)

        other_ws = make_station(branch="feature/TAP-999")
        try:
            grant(other_ws, issue_key="TAP-999", work_branch="feature/TAP-999")
            check("第二工位使用自己的授权", decision("git_commit", other_ws), "allow")
            check("第一工位授权保持独立", decision("git_commit", ws), "allow")
            check("跨任务不借用授权", decision("git_commit", ws, issue_key="TAP-999"), "ask")
            before = task_store.current_path(ws).read_bytes()
            try:
                task_store.write_task(ws, task_store.read_task(other_ws))
                rejected = False
            except ValueError:
                rejected = True
            check("拒绝覆盖当前任务且保留材料", rejected and task_store.current_path(ws).read_bytes() == before, True)
        finally:
            shutil.rmtree(other_ws)

        multi_ws = make_station()
        try:
            second = make_git_repository(multi_ws / "service-api")
            subprocess.run(["git", "remote", "set-url", "origin", "git@github.com:acme/service-api.git"], cwd=second, check=True)
            subprocess.run(["git", "checkout", "-q", "-b", "feature/TAP-123-api"], cwd=second, check=True)
            grant(multi_ws, extra_repositories=[{
                "repository": "acme/service-api", "authorized_endpoint": "github.com/acme/service-api",
                "work_branch": "feature/TAP-123-api", "base_branch": "develop", "base_sha": "2" * 40,
                "approved_scope": "API 配套修改", "verification_method": "python3 -m unittest",
                "pull_request": None, "ci": None,
            }])
            check("同一任务第二仓库授权放行", decision("git_commit", second), "allow")
            check("第二仓库审计回到任务目录", task_store.events_path(multi_ws, "TAP-123").is_file(), True)
        finally:
            shutil.rmtree(multi_ws)

        # 畸形授权的 Python 验证无条件运行，OPA 存在时才额外比较。
        use_opa = bool(shutil.which("opa"))
        if not use_opa:
            print("[SKIP] 未安装 opa，跳过一致性校验")
        def parity(request):
            py = run_standard(request)
            if use_opa:
                opa = run_standard(request, env_extra={"AO_GATE_USE_OPA": "1"})
                check("OPA 与 Python 完整判定一致",
                      tuple(opa.get(k) for k in ("decision", "reason_code", "required_action")),
                      tuple(py.get(k) for k in ("decision", "reason_code", "required_action")))
                check("OPA 不发生回退", bool(opa["warnings"]), False)
            return py

        auth_request = {
            "protocol_version": 1, "event": "before_operation",
            "source": {"agent": "test", "adapter": "test", "adapter_version": 1},
            "cwd": str(ws), "operations": ["git_commit"], "target": {"issue_key": "TAP-123"},
        }
        # free 是本地策略分级，不是用户或 Jira 服务端的写权限授权。
        comment_request = dict(auth_request, operations=["write_jira_comment"])
        for label, raw in (
            ("缺失", None), ("有效", json.dumps(original_authorization)),
            ("过期", json.dumps(dict(original_authorization, expires_at_epoch=1))),
            ("撤销", json.dumps(dict(original_authorization, status="revoked"))),
            ("损坏", "{bad-json"),
        ):
            if raw is None:
                authorization_path.unlink(missing_ok=True)
            else:
                authorization_path.write_text(raw, encoding="utf-8")
            result = parity(comment_request)
            check("评论 free 不依赖授权状态：" + label,
                  (result["decision"], result["reason_code"]), ("allow", "operation_free"))
        authorization_path.write_text(json.dumps(original_authorization), encoding="utf-8")
        for field in ("issue_key", "agentic_run_id", "agent_id", "approved_plan_version"):
            for value in ("", None, False, 123):
                malformed = dict(original_authorization, **{field: value})
                authorization_path.write_text(json.dumps(malformed), encoding="utf-8")
                check("无效必需授权绑定：" + field, parity(auth_request)["reason_code"], "authorization_invalid")
        repository = original_authorization["repositories"][0]
        malformed_repositories = [[repository, repository], [repository, "invalid"], {"repository": repository}, []]
        for field in ("repository", "work_branch", "base_branch", "base_sha", "approved_scope", "verification_method"):
            for value in ("", None, False, 123):
                malformed_repositories.append([dict(repository, **{field: value})])
        for repositories in malformed_repositories:
            malformed = dict(original_authorization, repositories=repositories)
            authorization_path.write_text(json.dumps(malformed), encoding="utf-8")
            check("无效仓库授权绑定", parity(auth_request)["reason_code"], "authorization_invalid")
        authorization_path.write_text(json.dumps(original_authorization), encoding="utf-8")
        for operation in ("git_commit", "create_pr", "write_jira_comment", "transition_jira_status",
                          "git_merge", "force_push", "history_rewrite", "unknown_external_write", "manage_station"):
            parity(dict(auth_request, operations=[operation]))
        for endpoint in (None, "", ["github.com/acme/widget"], "evil.test/acme/widget"):
            malformed = json.loads(json.dumps(original_authorization))
            malformed["repositories"][0]["authorized_endpoint"] = endpoint
            authorization_path.write_text(json.dumps(malformed), encoding="utf-8")
            request = dict(auth_request, operations=["git_push"], target={
                "issue_key": "TAP-123", "push_source_ref": "HEAD",
                "push_destination_ref": "refs/heads/feature/TAP-123", "push_target_branch": "feature/TAP-123"})
            check("push endpoint 失败关闭", parity(request)["reason_code"], "untrusted_push_repository")
        authorization_path.write_text(json.dumps(original_authorization), encoding="utf-8")
        events = [json.loads(line) for line in task_store.events_path(ws, "TAP-123").read_text(encoding="utf-8").splitlines()]
        check("标准 API 审计持续记录", len(events) >= 20, True)
        check("审计含原因码", all(event.get("reason_code") for event in events), True)
        check("人工处理审计含下一步", any(event.get("required_action") for event in events if event["decision"] != "allow"), True)
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
