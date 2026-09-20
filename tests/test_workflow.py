#!/usr/bin/env python3
"""任务状态机 / 准入规格 / CI 预算 / 证据规则 的测试。运行：python3 tests/test_workflow.py"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from bootstrap import product_version  # noqa: E402
from workflow import ci, evidence, jira_watermark, project_rules, task as workflow_task  # noqa: E402
from workflow import task_store, quality  # noqa: E402

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    ok = actual == expected
    PASS += ok
    FAIL += not ok
    print("[%s] %-58s -> %s (期望 %s)" % ("PASS" if ok else "FAIL", name, actual, expected))


def run_tool(tool, *args, cwd):
    # 旧场景显式从夹具快照绑定请求；过期/缺失参数由 test_checkpoints 独立验证。
    args = list(args)
    command = args[0] if args else ""
    mutation = (tool == "task.py" and (command in ("record", "advance", "block")
                or command == "repository" and args[1] in ("add", "record-result"))
                or tool == "authorization.py" and command in ("grant", "revoke")
                or tool == "ci.py" and command in ("watch", "record-fix"))
    if mutation:
        base = args[args.index("--dir") + 1] if "--dir" in args else cwd
        issue = args[args.index("--issue-key") + 1] if "--issue-key" in args else None
        try:
            issue = task_store.resolve_issue(base, issue)
            snapshot = task_store.read_task(base, issue)
        except (ValueError, OSError):
            snapshot = {"run_id": "run-unresolved", "stage": "waiting_takeover"}
        if "--expected-run-id" not in args:
            args += ["--expected-run-id", snapshot["run_id"]]
        if command == "advance" and "--expected-stage" not in args:
            args += ["--expected-stage", snapshot["stage"]]
    proc = subprocess.run(
        [sys.executable, str(ROOT / "workflow" / tool), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    return proc.returncode, proc.stdout + proc.stderr


def run_station_tool(product_root, *args, cwd):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bootstrap" / "station_registry.py"),
         "--product-root", str(product_root), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    return proc.returncode, proc.stdout + proc.stderr


def main():
    # 生命周期的资源安全、双工位、恢复与精确清理在独立同版测试覆盖；
    # 本文件保留通用 CLI、CI、证据脱敏和项目规则合同。
    from station_fixture import save_task
    ws = Path(tempfile.mkdtemp(prefix="aogate-wf-"))
    try:
        task_store._write_json_atomic(ws / ".agenticops/station.json", {
            "schema_version": 4, "station_id": "a" * 32, "product_root": str(ROOT), "source_pool": str(ws / "pool"),
            "project": "tapdata", "agents": ["codex"]})
        task_store._write_json_atomic(ws / ".agenticops/init.json", {
            "station_state_epoch": 11})
        state = {"issue_key": "TAP-123", "run_id": "run-workflow-test", "task_class": "defect_fix",
            "stage": "task_intake", "facts": {"acceptance_criteria": "fixture",
            "target_repo": "tapdata/tapdata", "verification_method": "fixture"},
            "repositories": [], "pending": None, "history": []}
        save_task(ws, state)
        code, out = run_tool("task.py", "status", "--dir", str(ws), cwd=ws)
        check("唯一当前任务状态可读", code == 0 and "TAP-123" in out, True)
        code, out = run_tool("task.py", "record", "--key", "note", "--value", "正常记录", cwd=ws)
        check("当前任务可写事实", code, 0)
        snapshot = (ws / ".agenticops/current-task.json").read_bytes()
        for removed in ("init", "reset", "activate", "deactivate", "purge", "list"):
            code, out = run_tool("task.py", removed, "--dir", str(ws), cwd=ws)
            check("旧入口拒绝：" + removed, code, 2)
        check("旧入口不改变当前任务", (ws / ".agenticops/current-task.json").read_bytes(), snapshot)
        legacy = ws / ".agenticops/tasks"
        legacy.mkdir()
        (legacy / "index.json").write_text("old-format-not-parsed")
        code, out = run_tool("task.py", "status", "--dir", str(ws), cwd=ws)
        check("旧状态拒绝而非在线迁移", code, 2)
        check("旧状态未被修改", (legacy / "index.json").read_text(), "old-format-not-parsed")
        (legacy / "index.json").unlink()
        legacy.rmdir()
        # ---- CI 判定与预算（纯函数） ------------------------------------
        check("CI 无检查 -> none", ci.classify([])[0], "none")
        check(
            "CI 进行中 -> pending",
            ci.classify([{"name": "build", "status": "IN_PROGRESS", "conclusion": ""}])[0],
            "pending",
        )
        check(
            "CI 含跳过 -> skipped",
            ci.classify([
                {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "lint", "status": "COMPLETED", "conclusion": "SKIPPED"},
            ])[0],
            "skipped",
        )
        verdict, failing = ci.classify([
            {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"},
        ])
        check("CI 有失败 -> failure+定位", (verdict, failing), ("failure", ["test"]))

        code, out = run_tool("ci.py", "record-fix", "--repo", "tapdata/tapdata", "--pr", "42", "--dir", str(ws), cwd=ws)
        check("旧 PR 独立预算入口已移除", code, 2)

        # ---- 证据生成 ---------------------------------------------------
        task_store.events_path(ws, "TAP-123").write_text(
            "\n".join(
                json.dumps(e)
                for e in [
                    {"decision": "allow", "operations": ["git_commit"], "note": "git commit"},
                    {"decision": "ask", "operations": ["git_merge"], "note": "git merge"},
                    {"decision": "deny", "operations": ["force_push"], "note": "git push -f"},
                ]
            ),
            encoding="utf-8",
        )
        code, out = run_tool("evidence.py", "--dir", str(ws), "--verification", "mvn test 全部通过", cwd=ws)
        check("evidence 生成成功", code, 0)
        for needle, label in [
            ("TAP-123", "含任务号"),
            ("放行 1 / 请求确认 1 / 拒绝 1", "含门禁统计"),
            ("被拒绝的操作", "列出 deny 项"),
            ("mvn test 全部通过", "含验证结果"),
            ("边界声明", "含边界声明"),
        ]:
            check("evidence %s" % label, needle in out, True)
        check("evidence 不再显示独立 PR 修复预算", "修复记账" in out, False)

        # ---- 证据敏感内容与验证规则 --------------------------------------
        code, out = run_tool("evidence.py", "--dir", str(ws), "--verification", "mvn package -DskipTests", cwd=ws)
        check("缺陷证据保留 skipTests 事实但不代替用例验收", code, 0)
        run_tool("task.py", "record", "--key", "note", "--value", "日志在 /Users/someone/logs/tm.log", cwd=ws)
        code, out = run_tool("evidence.py", "--dir", str(ws), "--verification", "mvn -pl x test 通过 exit=0", cwd=ws)
        check("证据拒绝本机绝对路径", code, 4)
        check("证据指出命中原因", "本机绝对路径" in out, True)
        run_tool("task.py", "record", "--key", "note", "--value", "日志见 PR 附件", cwd=ws)
        code, out = run_tool("evidence.py", "--dir", str(ws), "--verification", "mvn -pl x test 通过 exit=0", cwd=ws)
        check("清理后证据可生成", code, 0)
        check("证据含准入覆盖", "准入必填项" in out, True)
        run_tool("task.py", "record", "--key", "note", "--value", "token=ghp_abcdefghijklmnop", cwd=ws)
        code, out = run_tool("evidence.py", "--dir", str(ws), cwd=ws)
        check("证据拒绝疑似 token", code, 4)
        run_tool("task.py", "record", "--key", "note", "--value", "无", cwd=ws)

        # ---- 生成视图与机读规格不漂移 ------------------------------------
        code, out = run_tool("project_rules.py", "render", "--check", cwd=ROOT)
        check("admission md 与 json 无漂移", code, 0)

        # ---- profile 完整性 --------------------------------------------
        profile = json.loads((ROOT / "projects" / "tapdata" / "profile.json").read_text(encoding="utf-8"))
        repositories = json.loads((ROOT / "projects" / "tapdata" / "repositories.json").read_text(encoding="utf-8"))
        check("仓库目录基线分支含 common-lib=develop", repositories["repositories"]["tapdata/tapdata-common-lib"]["baseline_branch"], "develop")
        waiting_takeover_statuses = sorted(
            status for status, stage in profile["statuses"].items() if stage == "waiting_takeover"
        )
        check("TapData 仅 Analyzed 映射 waiting_takeover", waiting_takeover_statuses, ["Analyzed"])
        check("TapData 接管水印只覆盖 Bug/Task/Story", sorted(profile["jira"]["takeover_watermark"]["issue_type_ids"]), ["10008", "10010", "10011"])
        check("TapData 接管水印配置通过加载校验", project_rules.validate_takeover_watermark(profile)["field_id"], "customfield_10421")
        task_workflow = project_rules.resolve_issue_type_workflow(
            profile, issue_type_id="10008", issue_type_name="任务"
        )
        check("TapData 任务类型按稳定 ID 解析工作流", task_workflow["issue_type"]["id"], "10008")
        check("TapData 任务待办映射接管等待", task_workflow["statuses"][0], {
            "id": "10029", "name": "待办", "stage": "waiting_takeover"
        })
        check("TapData 任务开始流转精确映射", task_workflow["transitions"]["start_progress"], {
            "name": "Work started",
            "id": "61",
            "from": {"id": "10029", "name": "待办"},
            "to": {"id": "3", "name": "正在进行"},
        })
        code, out = run_tool(
            "project_rules.py", "workflow", "--issue-type-id", "10008", "--issue-type-name", "任务", "--json", cwd=ROOT
        )
        check("工作流 CLI 输出任务类型映射", code, 0)
        check("工作流 CLI 输出可机读", json.loads(out)["transitions"]["start_progress"]["id"], "61")
        code, out = run_tool("project_rules.py", "workflow", "--issue-type-id", "99999", cwd=ROOT)
        check("未知 Jira 事务类型失败关闭", code, 2)
        code, out = run_tool(
            "project_rules.py", "workflow", "--issue-type-id", "10008", "--issue-type-name", "Bug", cwd=ROOT
        )
        check("不一致的 Jira 事务类型 ID/名称失败关闭", code, 2)
        check("profile transition 291 标记禁止", profile["transitions"]["pr_approved"]["agent_forbidden"], True)
        check("admission 三张表就位", sorted(p.name for p in (ROOT / "projects/tapdata/admission").glob("*.md")), ["defect-fix.md", "feature-change.md", "technical-task.md"])
        check("runbook 已就位", len(list((ROOT / "projects/tapdata/runbooks").glob("*.md"))) >= 2, True)
        check("profile 只引用统一仓库目录", profile["repositories"]["catalog"], "repositories.json")
        check("仓库目录不重复维护顶层 domains", "domains" in repositories, False)
        check("仓库使用 domains 数组标签", repositories["repositories"]["tapdata/tapdata"]["domains"], ["product"])
        check("仓库目录分支解析规则已结构化", repositories["branch_resolution"]["forbidden_sources"][0], "current_branch")
        admission = json.loads((ROOT / "projects/tapdata/admission.json").read_text(encoding="utf-8"))
        check("admission 覆盖三类任务", sorted(admission["task_classes"]), ["defect_fix", "feature_change", "technical_task"])

    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
