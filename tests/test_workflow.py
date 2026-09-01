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
from workflow import ci, evidence, repository_worktree, task as workflow_task  # noqa: E402
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
        retry_clone_path = ws / "retry-clone"
        clone_attempts = []

        def retrying_clone(arguments, *, check=True):
            clone_attempts.append(arguments)
            if len(clone_attempts) == 1:
                retry_clone_path.mkdir()
                return SimpleNamespace(
                    returncode=128,
                    stdout="",
                    stderr="fatal: Could not resolve host: example.test",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with contextlib.redirect_stdout(io.StringIO()) as output, mock.patch.object(
            repository_worktree, "_run", side_effect=retrying_clone
        ), mock.patch.object(repository_worktree.time, "sleep") as sleep:
            repository_worktree._clone_main(
                "tapdata/retry", {"baseline_branch": "develop", "origin": "unused"}, retry_clone_path
            )
        check("网络 clone 失败后重试", len(clone_attempts), 2)
        check("网络 clone 重试前清理残留目录", retry_clone_path.exists(), False)
        check("网络 clone 按固定间隔重试", sleep.call_args.args, (2,))
        check("网络 clone 输出重试步骤日志", "第 1/3 次克隆失败" in output.getvalue(), True)

        timeout_clone_path = ws / "timeout-clone"
        timeout_attempts = []

        def timeout_then_success(arguments, *, check=True):
            timeout_attempts.append(arguments)
            if len(timeout_attempts) == 1:
                timeout_clone_path.mkdir()
                raise subprocess.TimeoutExpired(arguments, 120)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with contextlib.redirect_stdout(io.StringIO()) as output, mock.patch.object(
            repository_worktree, "_run", side_effect=timeout_then_success
        ), mock.patch.object(repository_worktree.time, "sleep") as sleep:
            repository_worktree._clone_main(
                "tapdata/timeout", {"baseline_branch": "develop", "origin": "unused"}, timeout_clone_path
            )
        check("clone 超时后重试", len(timeout_attempts), 2)
        check("clone 超时重试前清理残留目录", timeout_clone_path.exists(), False)
        check("clone 超时按固定间隔重试", sleep.call_args.args, (2,))
        check("clone 超时输出重试步骤日志", "第 1/3 次克隆失败" in output.getvalue(), True)

        non_network_clone_path = ws / "non-network-clone"
        non_network_attempts = []

        def rejected_clone(arguments, *, check=True):
            non_network_attempts.append(arguments)
            non_network_clone_path.mkdir()
            return SimpleNamespace(returncode=128, stdout="", stderr="fatal: Authentication failed")

        try:
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
                repository_worktree, "_run", side_effect=rejected_clone
            ):
                repository_worktree._clone_main(
                    "tapdata/rejected",
                    {"baseline_branch": "develop", "origin": "unused"},
                    non_network_clone_path,
                )
        except ValueError:
            pass
        check("非网络 clone 错误不重试", len(non_network_attempts), 1)
        check("非网络 clone 失败也清理残留目录", non_network_clone_path.exists(), False)

        remote_root = ws / "remotes"
        pool_root = ws / "source-pool"
        product_root = ws / "product-root"
        remote_root.mkdir()
        pool_root.mkdir()
        shutil.copytree(ROOT / "projects", product_root / "projects")
        (product_root / ".local").mkdir()
        (product_root / ".local" / "repository-pool.json").write_text(
            json.dumps({
                "schema_version": 1,
                "root": str(pool_root),
                "provisioning": "auto-clone",
            }),
            encoding="utf-8",
        )
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
            if name == "tapdata":
                main_worktree = pool_root / "tapdata" / name
                main_worktree.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["git", "clone", "-q", str(remote_root / (name + ".git")), str(main_worktree)],
                    check=True,
                )
        catalog_path = product_root / "projects" / "tapdata" / "repositories.json"
        catalog_document = json.loads(catalog_path.read_text(encoding="utf-8"))
        for name in ("tapdata", "tapdata-common-lib"):
            catalog_document["repositories"]["tapdata/" + name]["origin"] = str(
                remote_root / (name + ".git")
            )
        catalog_path.write_text(
            json.dumps(catalog_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
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
        code, out = run_tool("task.py", "status", "--issue", "TAP-123", "--dir", str(ws), cwd=ws)
        check("task CLI 拒绝 issue-key 缩写", code, 2)
        code, out = run_tool("repository_worktree.py", "roots", "--issue-key", "TAP-123", "--di", str(ws), cwd=ws)
        check("repository_worktree CLI 拒绝 dir 缩写", code, 2)
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

        original_task_lock = task_store.task_run_lock

        @contextlib.contextmanager
        def rejected_task_lock(base, issue_key):
            raise ValueError("获得任务状态锁后工作空间绑定无法读取：binding 已删除")
            yield

        task_store.task_run_lock = rejected_task_lock
        init_error = io.StringIO()
        try:
            with contextlib.redirect_stderr(init_error):
                rejected_init = workflow_task.cmd_init(
                    SimpleNamespace(
                        issue_key="TAP-777",
                        task_class="technical_task",
                        dir=str(ws),
                        force=False,
                    )
                )
        finally:
            task_store.task_run_lock = original_task_lock
        check("cmd_init 结构化处理任务锁进入失败", rejected_init, 2)
        check("cmd_init 输出锁后 binding 失效原因", "binding 已删除" in init_error.getvalue(), True)
        check("cmd_init 锁失败不创建任务状态", task_store.task_path(ws, "TAP-777").exists(), False)

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
        check("status 明确展示 active 注册状态", "注册状态：active" in out, True)
        code, out = run_tool("task.py", "deactivate", "--issue-key", "TAP-999", cwd=ws)
        check("任务可停用但保留状态", code, 0)
        code, out = run_tool("task.py", "status", "--issue-key", "TAP-999", cwd=ws)
        check("status 明确展示 inactive 注册状态", "注册状态：inactive" in out, True)
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
        code, out = run_tool("task.py", "advance", "--note", "准入齐备但还没有本地基线", cwd=ws)
        check("没有登记仓库禁止离开 task_intake", code, 3)
        check("基线门禁提示先登记仓库", "repository add" in out, True)

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
        remote_only = json.loads(
            task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8")
        )
        expected_endpoints = {
            "tapdata/tapdata": str((remote_root / "tapdata.git").resolve()),
            "tapdata/tapdata-common-lib": str(
                (remote_root / "tapdata-common-lib.git").resolve()
            ),
        }
        check(
            "repository add 从 Project catalog 固化 canonical endpoint",
            {
                item["repository"]: item.get("authorized_endpoint")
                for item in remote_only["repositories"]
            },
            expected_endpoints,
        )
        for item in remote_only["repositories"]:
            item["base_sha"] = "a" * 40
            item["catalog_digest"] = "b" * 64
        remote_only["repositories"][1].pop("authorized_endpoint")
        task_store.task_path(ws, "TAP-123").write_text(
            json.dumps(remote_only, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        code, out = run_tool(
            "task.py", "advance", "--note", "试图用远程 SHA 冒充本地基线", cwd=ws
        )
        check("仅登记远程 SHA 不能进入 design_review", code, 3)
        check("设计基线门禁要求受控 worktree", "受控 worktree" in out, True)

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
        code, out = run_tool(
            "task.py", "repository", "prepare", "--issue-key", "TAP-999", cwd=ws
        )
        check("waiting_takeover 阶段禁止 repository prepare", code, 2)
        check("prepare 返回明确阶段限制", "当前阶段 waiting_takeover" in out, True)
        run_tool("task.py", "deactivate", "--issue-key", "TAP-999", cwd=ws)
        with task_store.task_run_lock(ws, "TAP-999"):
            activate_process = subprocess.Popen(
                [
                    sys.executable, str(ROOT / "workflow" / "task.py"), "activate",
                    "--issue-key", "TAP-999", "--dir", str(ws),
                ],
                cwd=ws,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                activate_process.wait(timeout=0.2)
                activate_blocked = False
            except subprocess.TimeoutExpired:
                activate_blocked = True
            activate_process.terminate()
            activate_process.wait(timeout=2)
        check("activate 受 task_run_lock 串行保护", activate_blocked, True)

        code, out = run_tool(
            "task.py", "repository", "prepare", "--issue-key", "TAP-999", cwd=ws
        )
        check("inactive 任务禁止 repository prepare", code, 2)
        check("prepare 返回明确 active 限制", "只允许 active" in out, True)

        code, out = grant(ws, issue="TAP-999")
        check("授权工具拒绝其它任务号", code, 2)

        with task_store.task_run_lock(ws, "TAP-123"):
            prepare_process = subprocess.Popen(
                [
                    sys.executable, str(ROOT / "workflow" / "task.py"),
                    "repository", "prepare", "--issue-key", "TAP-123",
                    "--dir", str(ws),
                ],
                cwd=ws,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                prepare_process.wait(timeout=0.2)
                prepare_blocked = False
            except subprocess.TimeoutExpired:
                prepare_blocked = True
            prepare_process.terminate()
            prepare_process.wait(timeout=2)
        check("repository prepare 受 task_run_lock 串行保护", prepare_blocked, True)

        tapdata_main = pool_root / "tapdata" / "tapdata"
        trusted_tapdata_origin = str(remote_root / "tapdata.git")
        endpoint_tampered = json.loads(
            task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8")
        )
        endpoint_tampered["repositories"][0]["authorized_endpoint"] = (
            "evil.test/tapdata/tapdata"
        )
        task_store.task_path(ws, "TAP-123").write_text(
            json.dumps(endpoint_tampered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("任务 endpoint 与 catalog 不一致时 prepare 失败", code, 2)
        check("prepare endpoint 错配给出重新登记指引", "授权 endpoint" in out, True)
        endpoint_tampered["repositories"][0]["authorized_endpoint"] = expected_endpoints[
            "tapdata/tapdata"
        ]
        task_store.task_path(ws, "TAP-123").write_text(
            json.dumps(endpoint_tampered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git", "-C", str(tapdata_main), "config", "--add",
                "remote.origin.url", "git@evil.test:tapdata/tapdata.git",
            ],
            check=True,
        )
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("多个 raw remote.origin.url 时 prepare 失败", code, 2)
        check("raw origin 多值给出唯一性错误", "raw remote.origin.url 不唯一" in out, True)
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--unset-all", "remote.origin.url"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--add", "remote.origin.url", "not-a-url"],
            check=True,
        )
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("无法识别的 raw origin 时 prepare 失败", code, 2)
        check("raw origin 异常给出信任链错误", "无法识别的 endpoint" in out, True)
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--unset-all", "remote.origin.url"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--add", "remote.origin.url", trusted_tapdata_origin],
            check=True,
        )
        rewrite_key = "url.git@evil.test:tapdata/tapdata.git.insteadOf"
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--add", rewrite_key, trusted_tapdata_origin],
            check=True,
        )
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("insteadOf 同时改写 fetch/push 时 prepare 失败", code, 2)
        check("URL 改写被 raw/fetch/push 信任链识别", "信任链不一致" in out, True)
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--unset-all", rewrite_key],
            check=True,
        )

        subprocess.run(
            [
                "git", "-C", str(tapdata_main), "remote", "set-url", "--add",
                "--push", "origin", "git@evil.test:tapdata/tapdata.git",
            ],
            check=True,
        )
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("fetch origin 正常但 pushurl 错配时 prepare 失败", code, 2)
        check("pushurl 错配提示修复 remote 配置", "实际 push URL 与项目仓库目录不匹配" in out, True)
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--unset-all", "remote.origin.pushurl"],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(tapdata_main), "remote", "set-url", "--add",
                "--push", "origin", str(remote_root / "tapdata.git"),
            ],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(tapdata_main), "remote", "set-url", "--add",
                "--push", "origin", "git@evil.test:tapdata/tapdata.git",
            ],
            check=True,
        )
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("多个 pushurl 时 prepare 失败关闭", code, 2)
        check("多个 pushurl 给出唯一性错误", "实际 push URL 不唯一" in out, True)
        subprocess.run(
            ["git", "-C", str(tapdata_main), "config", "--unset-all", "remote.origin.pushurl"],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(tapdata_main), "remote", "set-url", "--add",
                "--push", "origin", str(remote_root / "tapdata.git"),
            ],
            check=True,
        )

        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("唯一且匹配 catalog 的 push URL 可准备 worktree", code, 0)
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
            "全新 Source Pool 仓库由 prepare 自动 clone",
            (pool_root / "tapdata" / "tapdata-common-lib" / ".git").is_dir(),
            True,
        )
        check(
            "准备后固化 base_sha",
            all(len(item["base_sha"]) == 40 for item in prepared_task["repositories"]),
            True,
        )
        check(
            "准备后固化 catalog_digest",
            all(len(item["catalog_digest"]) == 64 for item in prepared_task["repositories"]),
            True,
        )
        check(
            "准备后保留 catalog canonical endpoint",
            {
                item["repository"]: item.get("authorized_endpoint")
                for item in prepared_task["repositories"]
            },
            expected_endpoints,
        )
        code, out = run_tool(
            "task.py", "repository", "context", "--issue-key", "TAP-123", "--json", cwd=ws
        )
        check("当前会话可读取已校验任务上下文", code, 0)
        context = json.loads(out) if code == 0 else {}
        check("任务上下文保持当前 issue/run", (context.get("issue_key"), context.get("run_id")), ("TAP-123", prepared_task["run_id"]))
        check(
            "任务上下文只返回当前任务 worktree",
            [item.get("worktree") for item in context.get("repositories", [])],
            [item["worktree"]["path"] for item in prepared_task["repositories"]],
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

        frozen_bindings = {
            item["repository"]: (item["base_sha"], item["catalog_digest"])
            for item in prepared_task["repositories"]
        }
        (stale_seed / "REMOTE-AFTER-PREPARE").write_text("after prepare\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(stale_seed), "add", "REMOTE-AFTER-PREPARE"], check=True
        )
        subprocess.run(
            ["git", "-C", str(stale_seed), "commit", "-qm", "remote after prepare"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(stale_seed), "push", "-q", str(remote_root / "tapdata.git"), "develop"],
            check=True,
        )
        advanced_remote_sha = subprocess.check_output(
            ["git", "-C", str(stale_seed), "rev-parse", "HEAD"], text=True
        ).strip()
        code, out = run_tool("task.py", "repository", "prepare", cwd=ws)
        check("远程推进后重复 prepare 保持幂等", code, 0)
        repeated_task = json.loads(
            task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8")
        )
        check(
            "重复 prepare 保留已冻结 base_sha/catalog_digest",
            {
                item["repository"]: (item["base_sha"], item["catalog_digest"])
                for item in repeated_task["repositories"]
            },
            frozen_bindings,
        )
        repeated_pool_head = subprocess.check_output(
            ["git", "-C", str(pool_root / "tapdata" / "tapdata"), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        check("重复 prepare 可同步 Source Pool 最新基线", repeated_pool_head, advanced_remote_sha)
        code, out = run_tool("task.py", "advance", "--note", "准入与受控本地基线齐备", cwd=ws)
        check("重复 prepare 后冻结基线仍可 advance 到 design_review", code, 0)
        code, out = run_tool(
            "task.py", "reset", "--expected-run-id", first_run, "--stage", "implementation",
            "--note", "试图用 reset 跳过设计评审", cwd=ws,
        )
        check("reset 禁止向后续阶段跳转", code, 2)
        code, out = run_tool("task.py", "advance", "--note", "错误任务的授权", cwd=ws)
        check("错误授权未生成，仍拒绝进入实现", code, 3)
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
            "task.py", "advance", "--issue-key", "TAP-321", "--note", "接管核对通过",
            "--dir", str(ws2), cwd=ws2,
        )
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
        legacy_task = json.loads(
            task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8")
        )
        legacy_task["repositories"][0].pop("authorized_endpoint")
        task_store.task_path(ws, "TAP-123").write_text(
            json.dumps(legacy_task, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        code, out = grant(ws, issue="TAP-123")
        check("authorization grant 拒绝缺 endpoint 的旧任务状态", code, 2)
        legacy_task["repositories"][0]["authorized_endpoint"] = expected_endpoints[
            "tapdata/tapdata"
        ]
        task_store.task_path(ws, "TAP-123").write_text(
            json.dumps(legacy_task, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        code, out = grant(ws, issue="TAP-123")
        check("可信 endpoint 可形成任务授权", code, 0)
        issued_authorization = json.loads(
            task_store.authorization_path(ws, "TAP-123").read_text(encoding="utf-8")
        )
        check(
            "authorization 固化任务的 canonical endpoint",
            {
                item["repository"]: item.get("authorized_endpoint")
                for item in issued_authorization["repositories"]
            },
            expected_endpoints,
        )
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
            "--stage", "design_review", "--note", "回到设计阶段恢复受控基线", cwd=ws,
        )
        code, out = run_tool("authorization.py", "show", "--issue-key", "TAP-123", "--dir", str(ws), cwd=ws)
        check("任务 reset 后旧授权被撤销", '"status": "revoked"' in out, True)
        reset_task = json.loads(task_store.task_path(ws, "TAP-123").read_text(encoding="utf-8"))
        check("任务重做生成新 run_id", reset_task["run_id"] != old_run, True)
        code, out = run_tool(
            "task.py", "reset", "--expected-run-id", old_run,
            "--stage", "design_review", "--note", "过期 subagent 重复 reset", cwd=ws,
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
        code, out = run_tool(
            "task.py", "repository", "cleanup", "--delete-branches", cwd=ws
        )
        check("复用分支 cleanup 成功但不删除分支", code, 0)
        check("复用分支明确报告 retained-reused", "retained-reused" in out, True)
        reused_branch = subprocess.run(
            [
                "git", "-C", str(pool_root / "tapdata" / "tapdata"),
                "show-ref", "--verify", "--quiet", "refs/heads/feature/x",
            ],
            check=False,
        )
        check("branch_reused 分支保持存在", reused_branch.returncode, 0)

        subprocess.run(
            [
                "git", "-C", str(pool_root / "tapdata" / "tapdata"),
                "branch", "feature/task-999", expected_tapdata_base,
            ],
            check=True,
        )
        code, out = run_tool(
            "task.py", "repository", "cleanup", "--issue-key", "TAP-999",
            "--delete-branches", cwd=ws,
        )
        check("未 prepare 仓库 cleanup 不删除分支", code, 0)
        check("未 prepare 分支明确报告 retained-unowned", "retained-unowned" in out, True)
        unowned_branch = subprocess.run(
            [
                "git", "-C", str(pool_root / "tapdata" / "tapdata"),
                "show-ref", "--verify", "--quiet", "refs/heads/feature/task-999",
            ],
            check=False,
        )
        check("未 prepare 分支保持存在", unowned_branch.returncode, 0)

        # ---- 任务级 purge：inactive + run 绑定 + clean worktree ----------
        run_tool(
            "task.py", "init", "--issue-key", "TAP-456", "--task-class", "technical_task", cwd=ws
        )
        purge_task = json.loads(
            task_store.task_path(ws, "TAP-456").read_text(encoding="utf-8")
        )
        purge_run = purge_task["run_id"]
        run_tool(
            "task.py", "advance", "--issue-key", "TAP-456",
            "--note", "接管核对通过", cwd=ws,
        )
        run_tool(
            "task.py", "repository", "add", "--issue-key", "TAP-456",
            "--repo", "tapdata/tapdata", "--work-branch", "feature/purge-unmerged",
            "--scope", "purge 测试", "--verification", "test", cwd=ws,
        )
        code, out = run_tool(
            "task.py", "repository", "prepare", "--issue-key", "TAP-456", cwd=ws
        )
        check("purge 测试任务准备受控 worktree", code, 0)
        purge_task = json.loads(
            task_store.task_path(ws, "TAP-456").read_text(encoding="utf-8")
        )
        purge_worktree = Path(purge_task["repositories"][0]["worktree"]["path"])
        original_purge_state = json.loads(json.dumps(purge_task))
        purge_task["repositories"][0]["worktree"]["path"] = str(
            pool_root / "tapdata" / "tapdata"
        )
        task_store.task_path(ws, "TAP-456").write_text(
            json.dumps(purge_task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        code, out = run_tool(
            "task.py", "repository", "cleanup", "--issue-key", "TAP-456", cwd=ws
        )
        check("cleanup 拒绝被篡改的 state.worktree.path", code, 2)
        check("路径篡改失败关闭且真实 worktree 保留", purge_worktree.is_dir(), True)
        task_store.task_path(ws, "TAP-456").write_text(
            json.dumps(original_purge_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        lease_path = product_root / ".local" / "repository-worktrees.json"
        original_leases = json.loads(lease_path.read_text(encoding="utf-8"))
        tampered_leases = json.loads(json.dumps(original_leases))
        for lease in tampered_leases["leases"]:
            if lease.get("issue_key") == "TAP-456":
                lease["run_id"] = "run-tampered"
        lease_path.write_text(
            json.dumps(tampered_leases, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        code, out = run_tool(
            "task.py", "repository", "cleanup", "--issue-key", "TAP-456", cwd=ws
        )
        check("cleanup 拒绝被篡改的 task/run 租约", code, 2)
        check("租约篡改失败关闭且真实 worktree 保留", purge_worktree.is_dir(), True)
        lease_path.write_text(
            json.dumps(original_leases, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        with task_store.task_run_lock(ws, "TAP-456"):
            cleanup_process = subprocess.Popen(
                [
                    sys.executable, str(ROOT / "workflow" / "task.py"), "repository", "cleanup",
                    "--issue-key", "TAP-456", "--dir", str(ws),
                ],
                cwd=ws,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                cleanup_process.wait(timeout=0.2)
                cleanup_blocked = False
            except subprocess.TimeoutExpired:
                cleanup_blocked = True
            cleanup_process.terminate()
            cleanup_process.wait(timeout=2)
        check("直接 repository cleanup 受 task_run_lock 串行保护", cleanup_blocked, True)
        subprocess.run(
            ["git", "-C", str(purge_worktree), "config", "user.email", "test@example.test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(purge_worktree), "config", "user.name", "Test"], check=True
        )
        (purge_worktree / "PURGE-COMMIT").write_text("unmerged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(purge_worktree), "add", "PURGE-COMMIT"], check=True)
        subprocess.run(
            ["git", "-C", str(purge_worktree), "commit", "-qm", "unmerged purge branch"],
            check=True,
        )
        code, out = run_tool(
            "task.py", "purge", "--issue-key", "TAP-456",
            "--expected-run-id", purge_run, "--yes", cwd=ws,
        )
        check("active 任务禁止 purge", code, 2)
        run_tool("task.py", "deactivate", "--issue-key", "TAP-456", cwd=ws)
        inactive_purge_state = json.loads(
            task_store.task_path(ws, "TAP-456").read_text(encoding="utf-8")
        )
        tampered_purge_state = json.loads(json.dumps(inactive_purge_state))
        tampered_purge_state["repositories"][0]["worktree"]["path"] = str(
            pool_root / "tapdata" / "tapdata"
        )
        task_store.task_path(ws, "TAP-456").write_text(
            json.dumps(tampered_purge_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        code, out = run_tool(
            "task.py", "purge", "--issue-key", "TAP-456",
            "--expected-run-id", purge_run, "--yes", cwd=ws,
        )
        check("purge 同样拒绝被篡改的 state.worktree.path", code, 2)
        check("purge 路径篡改后任务与真实 worktree 均保留", (
            task_store.task_path(ws, "TAP-456").is_file() and purge_worktree.is_dir()
        ), True)
        task_store.task_path(ws, "TAP-456").write_text(
            json.dumps(inactive_purge_state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        code, out = run_tool(
            "task.py", "purge", "--issue-key", "TAP-456",
            "--expected-run-id", purge_run, cwd=ws,
        )
        check("purge 必须显式 --yes", code, 2)
        code, out = run_tool(
            "task.py", "purge", "--issue-key", "TAP-456",
            "--expected-run-id", "run-stale", "--yes", cwd=ws,
        )
        check("purge 拒绝过期 run_id", code, 2)
        (purge_worktree / "DIRTY").write_text("dirty\n", encoding="utf-8")
        code, out = run_tool(
            "task.py", "purge", "--issue-key", "TAP-456",
            "--expected-run-id", purge_run, "--yes", cwd=ws,
        )
        check("purge 遇到脏 worktree 失败关闭", code, 2)
        check("purge 失败后任务目录仍保留", task_store.task_path(ws, "TAP-456").is_file(), True)
        (purge_worktree / "DIRTY").unlink()
        code, out = run_tool(
            "task.py", "purge", "--issue-key", "TAP-456",
            "--expected-run-id", purge_run, "--yes", cwd=ws,
        )
        check("inactive clean 任务可安全 purge", code, 0)
        check("purge 删除任务注册", "TAP-456" in task_store.registered_issues(ws), False)
        check("purge 删除任务目录", task_store.task_directory(ws, "TAP-456").exists(), False)
        check("purge 明确报告保留未合并分支", "保留无充分删除证明的本地分支" in out, True)
        branch_result = subprocess.run(
            [
                "git", "-C", str(pool_root / "tapdata" / "tapdata"),
                "show-ref", "--verify", "--quiet", "refs/heads/feature/purge-unmerged",
            ],
            check=False,
        )
        check("purge 不强删未合并残留分支", branch_result.returncode, 0)

        run_tool(
            "task.py", "init", "--issue-key", "TAP-998", "--task-class", "technical_task", cwd=ws
        )
        completed_task_path = task_store.task_path(ws, "TAP-998")
        completed_task = json.loads(completed_task_path.read_text(encoding="utf-8"))
        completed_task["stage"] = "completed"
        completed_task_path.write_text(
            json.dumps(completed_task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        task_store.set_status(ws, "TAP-998", "completed")
        code, out = run_tool("task.py", "status", "--issue-key", "TAP-998", cwd=ws)
        check("status 明确展示 completed 注册状态", "注册状态：completed" in out, True)

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
