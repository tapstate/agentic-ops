#!/usr/bin/env python3
"""任务状态机 / 准入规格 / CI 预算 / 证据规则 的测试。运行：python3 tests/test_workflow.py"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workflow import ci, evidence  # noqa: E402
from workflow import task_store  # noqa: E402

PASS = 0
FAIL = 0


def check(name, actual, expected):
    global PASS, FAIL
    ok = actual == expected
    PASS += ok
    FAIL += not ok
    print("[%s] %-58s -> %s (期望 %s)" % ("PASS" if ok else "FAIL", name, actual, expected))


def run_tool(tool, *args, cwd):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "workflow" / tool), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    return proc.returncode, proc.stdout + proc.stderr


def grant(ws, issue="TAP-123"):
    return run_tool(
        "authorization.py", "grant", "--issue-key", issue, "--agent-id", "dev-bot-1",
        "--plan-version", "v1", "--dir", str(ws),
        cwd=ws,
    )


def main():
    ws = Path(tempfile.mkdtemp(prefix="aogate-wf-"))
    try:
        remote_root = ws / "remotes"
        pool_root = ws / "source-pool"
        product_root = ws / "product-root"
        remote_root.mkdir()
        pool_root.mkdir()
        shutil.copytree(ROOT / "projects", product_root / "projects")
        os.environ["GIT_CONFIG_COUNT"] = "1"
        os.environ["GIT_CONFIG_KEY_0"] = "url.file://%s/.insteadOf" % remote_root
        os.environ["GIT_CONFIG_VALUE_0"] = "git@github.com:tapdata/"
        for name in ("tapdata", "tapdata-common-lib"):
            seed = ws / ("seed-" + name)
            subprocess.run(["git", "init", "-q", "-b", "develop", str(seed)], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.email", "test@example.test"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.name", "Test"], check=True)
            (seed / "README.md").write_text(name + "\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(seed), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-qm", "initial"], check=True)
            subprocess.run(
                ["git", "clone", "-q", "--bare", str(seed), str(remote_root / (name + ".git"))],
                check=True,
            )
            main_worktree = pool_root / "tapdata" / name
            main_worktree.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "-q", "git@github.com:tapdata/%s.git" % name, str(main_worktree)],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(main_worktree), "remote", "set-url", "origin", "git@github.com:tapdata/%s.git" % name],
                check=True,
            )
        stale_seed = ws / "seed-tapdata"
        (stale_seed / "REMOTE-NEXT").write_text("next\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(stale_seed), "add", "REMOTE-NEXT"], check=True)
        subprocess.run(["git", "-C", str(stale_seed), "commit", "-qm", "remote next"], check=True)
        subprocess.run(
            ["git", "-C", str(stale_seed), "push", "-q", str(remote_root / "tapdata.git"), "develop"],
            check=True,
        )
        expected_tapdata_base = subprocess.check_output(
            ["git", "-C", str(stale_seed), "rev-parse", "HEAD"], text=True
        ).strip()
        (ws / ".agenticops").mkdir()
        (ws / ".agenticops" / "workspace.json").write_text(
            json.dumps({
                "schema_version": 2, "product_root": str(product_root),
                "workspace_id": "1" * 32,
                "project": "tapdata", "agents": ["claude", "codex"],
                "repository_pool": {"root": str(pool_root), "source": "workspace-override"},
            }), encoding="utf-8"
        )
        # ---- 任务状态机 -------------------------------------------------
        code, out = run_tool("task.py", "init", "--issue-key", "TAP-123", "--task-class", "defect_fix", cwd=ws)
        check("task init 成功", code, 0)
        first_run = json.loads(task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8"))["run_id"]
        code, out = run_tool("task.py", "init", "--issue-key", "TAP-123", "--task-class", "defect_fix", cwd=ws)
        check("重复接管要求用户选择继续或清理", code, 3)
        check("重复接管提示当前 run 与两种处理方式", first_run in out and "继续现有 run" in out and "清理重做" in out, True)
        check(
            "重复接管不会生成新 run_id",
            json.loads(task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8"))["run_id"],
            first_run,
        )
        concurrent_ws = ws / "concurrent-workspace"
        (concurrent_ws / ".agenticops").mkdir(parents=True)
        shutil.copy2(ws / ".agenticops" / "workspace.json", concurrent_ws / ".agenticops" / "workspace.json")
        concurrent_command = [
            sys.executable, str(ROOT / "workflow" / "task.py"), "init",
            "--issue-key", "TAP-555", "--task-class", "technical_task",
            "--dir", str(concurrent_ws),
        ]
        processes = [
            subprocess.Popen(concurrent_command, cwd=concurrent_ws, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        results = [process.communicate() + (process.returncode,) for process in processes]
        check("并发接管只有一个调用创建 run", sorted(item[2] for item in results), [0, 3])
        concurrent_task = json.loads(task_store.task_path(concurrent_ws, "TAP-555").read_text(encoding="utf-8"))
        check("并发接管只持久化一个 Workflow run", len([item for item in concurrent_task["history"] if item["event"] == "init"]), 1)
        code, out = run_tool("task.py", "init", "--issue-key", "TAP-999", "--task-class", "defect_fix", cwd=ws)
        check("同一项目空间可激活第二个任务", code, 0)
        code, out = run_tool("task.py", "init", "--issue-key", "AO-1", "--task-class", "defect_fix", cwd=ws)
        check("项目空间拒绝接管其它 Jira 项目的任务", code, 2)
        code, out = run_tool("task.py", "list", cwd=ws)
        check("统一任务注册表列出两个 active 任务", "TAP-123：active" in out and "TAP-999：active" in out, True)
        code, out = run_tool("task.py", "status", cwd=ws)
        check("多个 active 任务未指定 issue 时拒绝歧义", code, 2)
        code, out = run_tool(
            "task.py", "record", "--issue-key", "TAP-999",
            "--key", "problem_branch", "--value", "release-v1", cwd=ws,
        )
        check("第二个任务可独立记录状态", code, 0)
        code, out = run_tool("task.py", "status", "--issue-key", "TAP-123", cwd=ws)
        check("第一个任务未被第二个任务状态污染", "release-v1" not in out, True)
        code, out = run_tool("task.py", "deactivate", "--issue-key", "TAP-999", cwd=ws)
        check("任务可停用但保留状态", code, 0)
        code, out = run_tool(
            "task.py", "record", "--issue-key", "TAP-999",
            "--key", "problem_version", "--value", "develop", cwd=ws,
        )
        check("inactive 任务禁止继续变更状态", code, 2)
        code, out = run_tool("task.py", "record", "--key", "problem_branch", "--value", "develop", cwd=ws)
        check("record 事实", code, 0)
        code, out = run_tool("task.py", "advance", "--note", "接管核对通过", cwd=ws)
        check("advance 到 task_intake", code, 0)

        # ---- 准入必填项：机读规格 + 硬拦 ---------------------------------
        code, out = run_tool("task.py", "record", "--key", "not_a_real_key", "--value", "x", cwd=ws)
        check("record 拒绝清单外 fact key", code, 2)
        code, out = run_tool("task.py", "record", "--key", "problem_version", "--value", "  ", cwd=ws)
        check("record 拒绝空值", code, 2)
        code, out = run_tool("task.py", "advance", "--note", "准入未齐就想推进", cwd=ws)
        check("准入缺项禁止离开 task_intake", code, 3)
        check("缺项提示列出缺失项", "问题版本" in out and "问题现象" in out, True)
        check("缺项提示给出补卡建议", "请补充「问题现象」" in out, True)
        code, out = run_tool("task.py", "checklist", "--json", cwd=ws)
        check("checklist --json 输出缺失项", sorted(json.loads(out)["missing"]), ["problem_symptom", "problem_version"])
        run_tool("task.py", "record", "--key", "problem_version", "--value", "develop", cwd=ws)
        run_tool("task.py", "record", "--key", "problem_symptom", "--value", "TM 启动持续输出 ES health check refused", cwd=ws)
        code, out = run_tool("task.py", "advance", "--note", "准入齐备", cwd=ws)
        check("准入齐备后 advance 到 design_review", code, 0)
        code, out = run_tool(
            "task.py", "reset", "--expected-run-id", first_run, "--stage", "implementation",
            "--note", "试图用 reset 跳过设计评审", cwd=ws,
        )
        check("reset 禁止向后续阶段跳转", code, 2)
        code, out = run_tool("task.py", "advance", "--note", "试图跳过授权", cwd=ws)
        check("无授权禁止进入 implementation", code, 3)

        code, out = run_tool(
            "task.py", "repository", "add",
            "--repo", "tapdata/tapdata", "--work-branch", "feature/x",
            "--scope", "任务实现", "--verification", "mvn test", cwd=ws,
        )
        check("登记第一个任务仓库", code, 0)
        code, out = run_tool(
            "task.py", "repository", "add",
            "--repo", "tapdata/tapdata-common-lib", "--work-branch", "feature/x-common",
            "--scope", "公共库配套修改", "--verification", "mvn test", cwd=ws,
        )
        check("同一任务登记第二个仓库", code, 0)

        run_tool("task.py", "activate", "--issue-key", "TAP-999", cwd=ws)
        code, out = run_tool(
            "task.py", "repository", "add", "--issue-key", "TAP-999",
            "--repo", "tapdata/tapdata", "--work-branch", "feature/x",
            "--scope", "另一任务", "--verification", "mvn test", cwd=ws,
        )
        check("两个 active 任务禁止绑定相同仓库和工作分支", code, 2)
        code, out = run_tool(
            "task.py", "repository", "add", "--issue-key", "TAP-999",
            "--repo", "tapdata/tapdata", "--work-branch", "feature/task-999",
            "--scope", "另一任务", "--verification", "mvn test", cwd=ws,
        )
        check("两个 active 任务允许同仓库不同工作分支", code, 0)
        run_tool("task.py", "deactivate", "--issue-key", "TAP-999", cwd=ws)

        code, out = grant(ws, issue="TAP-999")
        check("授权工具拒绝其它任务号", code, 2)
        code, out = run_tool("task.py", "advance", "--note", "错误任务的授权", cwd=ws)
        check("错误授权未生成，仍拒绝进入实现", code, 3)

        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("任务仓库创建隔离 worktree 并固化基线", code, 0)
        if code != 0:
            print(out)
            return 1
        prepared_task = json.loads(task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8"))
        check(
            "两个任务 worktree 均位于当前 run 的工作空间目录",
            all(
                Path(item["worktree"]["path"]).is_relative_to(
                    (ws / ".agenticops" / "worktrees" / "TAP-123" / prepared_task["run_id"]).resolve()
                )
                for item in prepared_task["repositories"]
            ),
            True,
        )
        check(
            "准备后固化 base_sha",
            all(len(item["base_sha"]) == 40 for item in prepared_task["repositories"]),
            True,
        )
        synced_tapdata_head = subprocess.check_output(
            ["git", "-C", str(pool_root / "tapdata" / "tapdata"), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        check("准备前 fetch 并 fast-forward 过期主工作树", synced_tapdata_head, expected_tapdata_base)
        leases = json.loads(
            (product_root / ".local" / "repository-worktrees.json").read_text(encoding="utf-8")
        )["leases"]
        check("Product Root 统一记录双仓 worktree 租约", len(leases), 2)
        ws2 = ws / "second-workspace"
        (ws2 / ".agenticops").mkdir(parents=True)
        (ws2 / ".agenticops" / "workspace.json").write_text(
            json.dumps({
                "schema_version": 2,
                "product_root": str(product_root),
                "workspace_id": "2" * 32,
                "project": "tapdata",
                "agents": ["codex"],
                "repository_pool": {"root": str(pool_root), "source": "product-default"},
            }),
            encoding="utf-8",
        )
        run_tool("task.py", "init", "--issue-key", "TAP-321", "--task-class", "technical_task", "--dir", str(ws2), cwd=ws2)
        run_tool(
            "task.py", "repository", "add", "--issue-key", "TAP-321",
            "--repo", "tapdata/tapdata", "--work-branch", "feature/x",
            "--scope", "并发冲突测试", "--verification", "mvn test", "--dir", str(ws2), cwd=ws2,
        )
        code, out = run_tool(
            "task.py", "repository", "prepare", "--issue-key", "TAP-321", "--dir", str(ws2), cwd=ws2
        )
        check("第二工作空间相同仓库分支被中央租约阻断", code, 2)
        check("租约冲突返回持有者信息", "worktree 租约冲突" in out, True)
        grant(ws, issue="TAP-123")
        code, out = run_tool("task.py", "advance", "--note", "设计已确认+授权签发", cwd=ws)
        check("正确授权后进入 implementation", code, 0)
        run_tool("task.py", "activate", "--issue-key", "TAP-999", cwd=ws)
        code, out = run_tool("authorization.py", "show", "--issue-key", "TAP-999", "--dir", str(ws), cwd=ws)
        check("第二个 active 任务不会看到第一个任务授权", code == 0 and "无授权文件" in out, True)
        run_tool("task.py", "deactivate", "--issue-key", "TAP-999", cwd=ws)

        code, out = run_tool(
            "task.py", "repository", "record-result", "--repo", "tapdata/tapdata",
            "--pr", "https://github.com/tapdata/tapdata/pull/1", "--ci", "success", cwd=ws,
        )
        check("记录 PR/CI 不改变授权绑定", code, 0)

        # ---- 验证结论规则 ------------------------------------------------
        code, out = run_tool("task.py", "advance", "--note", "没记验证就想开 PR", cwd=ws)
        check("未记 verification 禁止离开 implementation", code, 3)
        run_tool("task.py", "record", "--key", "verification", "--value", "mvn -pl iengine test -DskipTests 打包成功", cwd=ws)
        code, out = run_tool("task.py", "advance", "--note", "拿 skipTests 冒充验证", cwd=ws)
        check("-DskipTests 不被接受为验证结果", code, 3)
        run_tool("task.py", "record", "--key", "verification", "--value", "未验证", cwd=ws)
        code, out = run_tool("task.py", "advance", "--note", "占位词冒充验证", cwd=ws)
        check("占位词不被接受为验证结果", code, 3)
        run_tool("task.py", "record", "--key", "verification", "--value", "mvn -pl iengine -am test 通过，exit=0", cwd=ws)
        code, out = run_tool("task.py", "advance", "--note", "验证通过", cwd=ws)
        check("合规验证后进入 pr_review", code, 0)
        code, out = run_tool("task.py", "repository", "cleanup", cwd=ws)
        check("任务清理同步移除 worktree", code, 0)
        remaining_leases = json.loads(
            (product_root / ".local" / "repository-worktrees.json").read_text(encoding="utf-8")
        )["leases"]
        check("任务清理同步释放中央租约", remaining_leases, [])
        old_run = json.loads(task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8"))["run_id"]
        run_tool(
            "task.py", "reset", "--expected-run-id", old_run,
            "--stage", "implementation", "--note", "回到实现阶段继续测试", cwd=ws,
        )
        code, out = run_tool("authorization.py", "show", "--issue-key", "TAP-123", "--dir", str(ws), cwd=ws)
        check("任务 reset 后旧授权被撤销", '"status": "revoked"' in out, True)
        reset_task = json.loads(task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8"))
        check("任务重做生成新 run_id", reset_task["run_id"] != old_run, True)
        code, out = run_tool(
            "task.py", "reset", "--expected-run-id", old_run,
            "--stage", "implementation", "--note", "过期 subagent 重复 reset", cwd=ws,
        )
        check("过期 subagent 不能再次生成 run", code, 3)
        check(
            "过期 reset 后当前 run_id 保持不变",
            json.loads(task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8"))["run_id"],
            reset_task["run_id"],
        )
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("残留本地任务分支不会被静默复用", code, 2)
        check("残留分支错误给出显式复用指引", "--reuse-existing-branch" in out, True)
        code, out = run_tool(
            "task.py", "repository", "prepare", "--reuse-existing-branch", cwd=ws
        )
        check("用户显式确认后可复用残留任务分支", code, 0)
        run_tool("task.py", "repository", "cleanup", cwd=ws)

        # ---- 分支解析：查表，禁止猜测 ------------------------------------
        code, out = run_tool("task.py", "branch", "--repo", "tapdata/tapdata-license", cwd=ws)
        check("已登记仓库解析出基线分支", code == 0 and "main" in out, True)
        code, out = run_tool("task.py", "branch", "--repo", "acme/unknown", cwd=ws)
        check("未登记仓库拒绝猜测分支", code, 2)

        code, out = run_tool("task.py", "block", "--reason", "缺问题版本", cwd=ws)
        check("block 记录 pending", code, 0)
        code, out = run_tool("task.py", "status", cwd=ws)
        check("status 展示 pending 正文", "缺问题版本" in out, True)
        check("status 展示恢复指引", "runbooks" in out or "下一步" in out, True)

        # ---- CI 判定与预算（纯函数） ------------------------------------
        check("CI 无检查 -> none", ci.classify([])[0], "none")
        check(
            "CI 进行中 -> pending",
            ci.classify([{"name": "build", "status": "IN_PROGRESS", "conclusion": ""}])[0],
            "pending",
        )
        check(
            "CI 全绿 -> success",
            ci.classify([
                {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "lint", "status": "COMPLETED", "conclusion": "SKIPPED"},
            ])[0],
            "success",
        )
        verdict, failing = ci.classify([
            {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"},
        ])
        check("CI 有失败 -> failure+定位", (verdict, failing), ("failure", ["test"]))

        check("预算初始 3 次", ci.budget_left({"fix_attempts": 0}), 3)
        check("预算用尽为 0", ci.budget_left({"fix_attempts": 5}), 0)
        code, out = run_tool("ci.py", "record-fix", "--pr", "42", "--dir", str(ws), cwd=ws)
        check("record-fix 第 1 次", code, 0)
        for _ in range(2):
            run_tool("ci.py", "record-fix", "--pr", "42", "--dir", str(ws), cwd=ws)
        code, out = run_tool("ci.py", "record-fix", "--pr", "42", "--dir", str(ws), cwd=ws)
        check("第 4 次 record-fix 拒绝转人工", code, 3)

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
            ("放行 1 / 人工确认 1 / 拒绝 1", "含门禁统计"),
            ("被拒绝的操作", "列出 deny 项"),
            ("mvn test 全部通过", "含验证结果"),
            ("修复 3 次", "含 CI 修复次数"),
            ("边界声明", "含边界声明"),
        ]:
            check("evidence %s" % label, needle in out, True)

        # ---- 证据敏感内容与验证规则 --------------------------------------
        code, out = run_tool("evidence.py", "--dir", str(ws), "--verification", "mvn package -DskipTests", cwd=ws)
        check("证据拒绝 skipTests 验证", code, 4)
        run_tool("task.py", "record", "--key", "note", "--value", "日志在 /Users/someone/logs/tm.log", cwd=ws)
        code, out = run_tool("evidence.py", "--dir", str(ws), "--verification", "mvn -pl x test 通过 exit=0", cwd=ws)
        check("证据拒绝本机绝对路径", code, 4)
        check("证据指出命中原因", "本机绝对路径" in out, True)
        run_tool("task.py", "record", "--key", "note", "--value", "日志见 PR 附件", cwd=ws)
        code, out = run_tool("evidence.py", "--dir", str(ws), "--verification", "mvn -pl x test 通过 exit=0", cwd=ws)
        check("清理后证据可生成", code, 0)
        check("证据含准入覆盖", "*准入必填项*：3/3 齐备" in out, True)
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
        check("profile transition 291 标记禁止", profile["transitions"]["pr_approved"]["agent_forbidden"], True)
        check("admission 三张表就位", sorted(p.name for p in (ROOT / "projects/tapdata/admission").glob("*.md")), ["defect-fix.md", "feature-change.md", "technical-task.md"])
        check("runbook 两份就位", len(list((ROOT / "projects/tapdata/runbooks").glob("*.md"))), 2)
        check("profile 只引用统一仓库目录", profile["repositories"]["catalog"], "repositories.json")
        check("仓库目录不重复维护顶层 domains", "domains" in repositories, False)
        check("仓库使用 domains 数组标签", repositories["repositories"]["tapdata/tapdata"]["domains"], ["product"])
        check("仓库目录分支解析规则已结构化", repositories["branch_resolution"]["forbidden_sources"][0], "current_branch")
        admission = json.loads((ROOT / "projects/tapdata/admission.json").read_text(encoding="utf-8"))
        check("admission 覆盖三类任务", sorted(admission["task_classes"]), ["defect_fix", "feature_change", "technical_task"])

        # ---- 旧版单任务状态兼容迁移 ------------------------------------
        legacy = ws / "legacy-workspace"
        legacy_gate = legacy / ".gate"
        legacy_gate.mkdir(parents=True)
        (legacy / ".agenticops").mkdir()
        (legacy / ".agenticops" / "workspace.json").write_text(
            json.dumps({
                "schema_version": 1, "product_root": str(ROOT),
                "project": "tapdata", "agents": ["claude"],
            }), encoding="utf-8"
        )
        (legacy_gate / "task.json").write_text(
            json.dumps({
                "issue_key": "TAP-777", "task_class": "defect_fix",
                "stage": "waiting_takeover", "facts": {}, "repositories": [],
                "pending": None, "history": [],
            }),
            encoding="utf-8",
        )
        (legacy_gate / "events.jsonl").write_text("{}\n", encoding="utf-8")
        code, out = run_tool("task.py", "status", "--issue-key", "TAP-777", "--dir", str(legacy), cwd=legacy)
        check("旧版单任务状态自动迁移", code, 0)
        check("旧 task.json 迁入任务目录", task_store.task_path(legacy, "TAP-777").is_file(), True)
        check("旧 events.jsonl 随任务迁移", task_store.events_path(legacy, "TAP-777").is_file(), True)
        check("迁移后不存在旧任务事实源", (legacy_gate / "task.json").exists(), False)

        # ---- 开发期多任务状态兼容迁移 ----------------------------------
        legacy_multi = ws / "legacy-multi-workspace"
        old_tasks = legacy_multi / ".gate" / "tasks"
        old_tasks.mkdir(parents=True)
        (legacy_multi / ".agenticops").mkdir()
        (legacy_multi / ".agenticops" / "workspace.json").write_text(
            json.dumps({
                "schema_version": 1, "product_root": str(ROOT),
                "project": "tapdata", "agents": ["claude"],
            }), encoding="utf-8"
        )
        (legacy_multi / ".gate" / "tasks.json").write_text(
            json.dumps({
                "schema_version": 1, "project": "tapdata",
                "tasks": {
                    "TAP-888": {
                        "status": "active",
                        "created_at": "2026-08-29T00:00:00+0800",
                        "updated_at": "2026-08-29T00:00:00+0800",
                    }
                },
            }), encoding="utf-8"
        )
        (old_tasks / "TAP-888").mkdir()
        (old_tasks / "TAP-888" / "task.json").write_text(
            json.dumps({
                "issue_key": "TAP-888", "task_class": "technical_task",
                "stage": "waiting_takeover", "facts": {}, "repositories": [],
                "pending": None, "history": [],
            }), encoding="utf-8"
        )
        code, out = run_tool(
            "task.py", "status", "--issue-key", "TAP-888",
            "--dir", str(legacy_multi), cwd=legacy_multi,
        )
        check("开发期多任务状态自动迁移", code, 0)
        check("旧多任务注册表迁入 tasks/index.json",
              task_store.registry_path(legacy_multi).is_file(), True)
    finally:
        for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
            os.environ.pop(key, None)
        shutil.rmtree(ws, ignore_errors=True)

    print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
