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


def check_project_boundaries(base):
    product = base / "project-boundary-product"
    project = product / "projects/demo"
    project.mkdir(parents=True)
    station = base / "project-boundary-station"
    binding = station / ".agenticops/station.json"
    binding.parent.mkdir(parents=True)
    for name in ("../policies", "/tmp", "demo/other", "Demo", "demo_name", "demo\\other", "demo\n", "", None):
        binding.write_text(json.dumps({"project": name, "product_root": str(product)}))
        for label, read in (
            ("规则目录", lambda: project_rules.project_root(product, name)),
            ("工位规则", lambda: project_rules.project_from_station(station)),
            ("状态工位", lambda: task_store.station_project(station)),
        ):
            try:
                read()
            except ValueError:
                rejected = True
            else:
                rejected = False
            check("%s 拒绝非法项目 %r" % (label, name), rejected, True)
    binding.write_text(json.dumps({"project": "demo", "product_root": str(product)}))
    check("合法旧工位保留项目读取", task_store.station_project(station), "demo")
    check("合法项目目录不变", project_rules.project_root(product, "demo"), project)
    (project / "admission.json").write_text(json.dumps({"project_marker": "demo"}))
    check("显式第二项目读取隔离", project_rules.load_admission(product, "demo"), {"project_marker": "demo"})
    check("工位继续使用旧绑定", project_rules.load_admission(station=station), {"project_marker": "demo"})
    for reader in (project_rules.project_root, project_rules.load_admission, project_rules.load_profile,
                   project_rules.load_repository_catalog, project_rules.repository_catalog_path):
        try:
            reader(root=ROOT)
        except ValueError as error:
            rejected = "显式指定 project" in str(error)
        else:
            rejected = False
        check("缺省项目拒绝 " + reader.__name__, rejected, True)
    for command, options in (("render", []), ("branch", ["--repo", "tapdata/tapdata"]), ("workflow", [])):
        code, out = run_tool("project_rules.py", command, "--root", str(product), *options, cwd=ROOT)
        check("CLI 缺项目拒绝 " + command, code, 2)
        check("CLI 指出项目参数 " + command, "--project" in out, True)
    check("缺项目不生成视图", (project / "admission").exists(), False)
    for name, target in (("alias", project), ("external", base)):
        (product / "projects" / name).symlink_to(target, target_is_directory=True)
        try:
            project_rules.project_root(product, name)
        except ValueError:
            rejected = True
        else:
            rejected = False
        check("项目目录拒绝链接 " + name, rejected, True)
    render_station = base / "invalid-project-init"
    result = subprocess.run([sys.executable, str(ROOT / "bootstrap/render.py"),
                             "--install-home", str(ROOT), "--station", str(render_station),
                             "--project", "/tmp", "--source-pool", str(base / "pool"), "--agent", "codex"],
                            capture_output=True, text=True)
    check("初始化拒绝路径型项目", result.returncode != 0 and "项目 ID" in result.stderr, True)
    check("非法项目不生成工位绑定", (render_station / ".agenticops/station.json").exists(), False)


def check_project_json_objects(base):
    product = base / "json-product"
    project = product / "projects/demo"
    project.mkdir(parents=True)
    profile = json.loads((ROOT / "projects/tapdata/profile.json").read_text())
    (project / "profile.json").write_text(json.dumps(profile))
    station = base / "json-station"
    binding = station / ".agenticops/station.json"
    binding.parent.mkdir(parents=True)
    readers = (
        (project / "admission.json", lambda: project_rules.load_admission(product, "demo")),
        (project / "repositories.json", lambda: project_rules.load_profile(product, "demo")),
        (binding, lambda: project_rules.project_from_station(station)),
        (binding, lambda: project_rules.product_root_from_station(station)),
    )
    for path, read in readers:
        for value in ([], None, 1, True, "fixture-private-content"):
            path.write_text(json.dumps(value))
            try:
                read()
            except ValueError as error:
                message = str(error)
                rejected = path.name in message and "顶层必须是对象" in message and "fixture-private-content" not in message
            else:
                rejected = False
            check("JSON 对象校验 %s %s" % (path.name, type(value).__name__), rejected, True)
    for path, read in readers:
        for raw in ('{"fixture-private-content":1,"fixture-private-content":2}',
                    '{"rules":{"fixture-private-content":true,"fixture-private-content":false}}'):
            path.write_text(raw)
            try:
                read()
            except ValueError as error:
                message = str(error)
                rejected = "重复键" in message and path.name in message and "fixture-private-content" not in message
            else:
                rejected = False
            check("重复 JSON 键拒绝 " + path.name, rejected, True)
    (project / "admission.json").write_text('{"a":{"required":true},"b":{"required":false}}')
    check("不同对象同名键有效", project_rules.load_admission(product, "demo"),
          {"a": {"required": True}, "b": {"required": False}})
    (project / "admission.json").write_text('{"rules":{},"rules":{}}')
    code, out = run_tool("project_rules.py", "render", "--root", str(product), "--project", "demo", cwd=ROOT)
    check("重复键 CLI 配置错误", code == 2 and "重复键" in out and "Traceback" not in out, True)
    check("重复键不生成视图", (project / "admission").exists(), False)
    (project / "profile.json").write_text('["fixture-private-content"]')
    (project / "admission.json").write_text('null')
    for command, options in (("render", []), ("branch", ["--repo", "owner/repo"]), ("workflow", ["--issue-type-id", "1"])):
        code, out = run_tool("project_rules.py", command, "--root", str(product), "--project", "demo", *options, cwd=ROOT)
        check("CLI 配置错误退出 " + command, code, 2)
        check("CLI 不泄漏栈或内容 " + command, "Traceback" not in out and "fixture-private-content" not in out, True)
    check("无效配置不生成准入视图", (project / "admission").exists(), False)
    (project / "admission.json").unlink()
    code, out = run_tool("project_rules.py", "render", "--root", str(product), "--project", "demo", cwd=ROOT)
    check("CLI 缺文件也按配置错误处理", code, 2)
    check("CLI 缺文件不泄漏栈", "Traceback" not in out, True)


def check_catalog_reference(base):
    product = base / "catalog-reference-product"
    project = product / "projects/demo"
    project.mkdir(parents=True)
    profile_path = project / "profile.json"
    profile = json.loads((ROOT / "projects/tapdata/profile.json").read_text())
    readers = (project_rules.load_profile, project_rules.repository_catalog_path)
    for value in (None, [], "private-fixture", 1, True, {}, {"catalog": []}, {"catalog": "   "}):
        profile["repositories"] = value
        profile_path.write_text(json.dumps(profile))
        for reader in readers:
            try:
                reader(product, "demo")
            except ValueError as error:
                rejected = "repositories" in str(error) and "private-fixture" not in str(error)
            else:
                rejected = False
            check("目录引用类型 " + reader.__name__ + repr(value), rejected, True)
    for command, options in (("branch", ["--repo", "owner/repo"]), ("workflow", ["--issue-type-id", "1"])):
        profile["repositories"] = []
        profile_path.write_text(json.dumps(profile))
        code, out = run_tool("project_rules.py", command, "--root", str(product), "--project", "demo", *options, cwd=ROOT)
        check("目录引用 CLI 错误 " + command, code, 2)
        check("目录引用 CLI 无堆栈 " + command, "Traceback" not in out and "repositories" in out, True)
    catalog = project / "nested/catalog.json"
    catalog.parent.mkdir()
    catalog.write_text(json.dumps({"schema_version": 1, "repositories": {}}))
    (project / "alias.json").symlink_to(catalog)
    for reference in ("nested/catalog.json", "alias.json"):
        profile["repositories"] = {"catalog": reference}
        profile_path.write_text(json.dumps(profile))
        check("合法目录路径 " + reference, project_rules.repository_catalog_path(product, "demo"), catalog.resolve())
        check("合法目录加载 " + reference, project_rules.load_profile(product, "demo")["repositories"]["repositories"], {})
    outside = product / "outside.json"
    outside.write_text(catalog.read_text())
    (project / "escape.json").symlink_to(outside)
    for reference in ("../../outside.json", str(outside), "escape.json"):
        profile["repositories"] = {"catalog": reference}
        profile_path.write_text(json.dumps(profile))
        for reader in readers:
            try:
                reader(product, "demo")
            except ValueError as error:
                rejected = "路径越界" in str(error)
            else:
                rejected = False
            check("目录引用越界 " + reader.__name__ + reference, rejected, True)


def check_admission_documents(base):
    product = base / "render-product"
    project = product / "projects/demo"
    project.mkdir(parents=True)
    config = project / "admission.json"
    spec = {"task_classes": {"audit_task": {"title": "审计", "doc": "projects/demo/admission/audit-task.md"}}}
    def render(value, checking=False):
        config.write_text(json.dumps(value))
        return run_tool("project_rules.py", "render", "--root", str(product), "--project", "demo", *(["--check"] if checking else []), cwd=ROOT)
    code, _ = render(spec, True)
    check("新任务类缺文档报告漂移", code, 1)
    check("只检查不创建文档目录", (project / "admission").exists(), False)
    code, _ = render(spec)
    document = project / "admission/audit-task.md"
    check("配置化新任务类生成成功", code, 0)
    check("正文绑定新任务类", "--task-class audit_task" in document.read_text(), True)
    check("配置化文档检查通过", render(spec, True)[0], 0)
    original = document.read_text()
    for reference in (None, [], "projects/other/admission/audit.md", "../escape.md", str(base / "escape.md"),
                      "projects/demo/admission/../escape.md", "projects/demo/admission/UPPER.md", "projects/demo/admission/audit-task.md"):
        invalid = {"task_classes": {"audit_task": dict(spec["task_classes"]["audit_task"], title="不应写入"),
                                    "second_task": {"title": "第二任务", "doc": reference}}}
        code, out = render(invalid)
        check("错误或重复文档目标拒绝 " + repr(reference), code, 2)
        check("错误文档目标不产生堆栈 " + repr(reference), "Traceback" not in out, True)
        check("后续目标错误不部分写入 " + repr(reference), document.read_text(), original)
    document.unlink()
    outside = product / "outside.md"
    outside.write_text("preserved")
    document.symlink_to(outside)
    for checking in (False, True):
        check("拒绝文档链接 " + str(checking), render(spec, checking)[0], 2)
    check("链接目标未被修改", outside.read_text(), "preserved")
    document.unlink()
    document.mkdir()
    check("拒绝目录型文档", render(spec)[0], 2)
    document.rmdir()
    (project / "admission").rmdir()
    (project / "admission").symlink_to(product, target_is_directory=True)
    check("拒绝文档目录链接", render(spec)[0], 2)
    check("目录链接未写入外部", (product / "audit-task.md").exists(), False)
    code, _ = run_tool("project_rules.py", "render", "--project", "tapdata", "--check", cwd=ROOT)
    check("TapData 现有三类文档逐字兼容", code, 0)


def check_station_binding_snapshot(base):
    from workflow import jira_collect, native_cleanup, repair_strategy, station, station_clean_rules, station_replan
    area = base.resolve() / "binding-snapshot"
    products = [area / "product-a", area / "product-b"]
    for root in products:
        for name in ("alpha", "beta"):
            shutil.copytree(ROOT / "projects/tapdata", root / "projects" / name)
        shutil.copytree(ROOT / "policies", root / "policies")
    bound = area / "station"
    binding = bound / ".agenticops/station.json"
    binding.parent.mkdir(parents=True)
    initial = {"product_root": str(products[0]), "project": "alpha"}
    later = {"product_root": str(products[1]), "project": "beta"}
    task = {"task_class": "defect_fix"}
    project = products[0] / "projects/alpha"
    # 赋予各配置可区分的内容，证明调用没有混用第二次绑定。
    for root in products:
        for name in ("alpha", "beta"):
            for filename in ("admission.json", "profile.json", "jira-transitions.json"):
                path = root / "projects" / name / filename
                value = json.loads(path.read_text())
                value["snapshot_marker"] = root.name + "/" + name
                if filename == "profile.json":
                    value["jira"]["site"] = "https://" + root.name + "-" + name + ".example.test"
                path.write_text(json.dumps(value))
    calls = (
        ("绑定上下文", lambda: project_rules.station_context(bound), (products[0], "alpha")),
        ("准入", lambda: project_rules.load_admission(station=bound)["snapshot_marker"], "product-a/alpha"),
        ("Profile", lambda: project_rules.load_profile(station=bound)["snapshot_marker"], "product-a/alpha"),
        ("仓库目录路径", lambda: project_rules.repository_catalog_path(station=bound), project / "repositories.json"),
        ("质量配置", lambda: quality.config(bound, task)["jira"]["site"], "https://product-a-alpha.example.test"),
        ("Jira 采集", lambda: jira_collect.config(bound, task)[0]["snapshot_marker"], "product-a/alpha"),
        ("返工项目", lambda: station_replan.project(bound)[1], project),
        ("工位项目", lambda: station._project(bound), project),
        ("原生清理", lambda: native_cleanup.configuration(bound)[0], products[0]),
        ("清理规则", lambda: bool(station_clean_rules.load(bound)["layers"]), True),
        ("修复策略", lambda: repair_strategy.resolve(bound, task)["available"], True),
    )
    original = project_rules._read_json
    for label, read, expected in calls:
        binding.write_text(json.dumps(initial))
        observed = []
        def changing_read(path):
            value = original(path)
            if path == binding:
                observed.append(value)
                binding.write_text(json.dumps(later))
            return value
        with mock.patch.object(project_rules, "_read_json", side_effect=changing_read):
            result = read()
        check("绑定快照结果 " + label, result, expected)
        check("绑定只读一次 " + label, len(observed), 1)
    binding.write_text(json.dumps({"project": "alpha"}))
    check("单字段项目旧 API 保持兼容", project_rules.project_from_station(bound), "alpha")
    binding.write_text(json.dumps({"product_root": str(products[0])}))
    check("单字段产品根旧 API 保持兼容", project_rules.product_root_from_station(bound), products[0])


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
            "station_state_epoch": 14})
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
        code, out = run_tool("project_rules.py", "render", "--project", "tapdata", "--check", cwd=ROOT)
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
            "project_rules.py", "workflow", "--project", "tapdata", "--issue-type-id", "10008", "--issue-type-name", "任务", "--json", cwd=ROOT
        )
        check("工作流 CLI 输出任务类型映射", code, 0)
        check("工作流 CLI 输出可机读", json.loads(out)["transitions"]["start_progress"]["id"], "61")
        code, out = run_tool("project_rules.py", "workflow", "--project", "tapdata", "--issue-type-id", "99999", cwd=ROOT)
        check("未知 Jira 事务类型失败关闭", code, 2)
        code, out = run_tool(
            "project_rules.py", "workflow", "--project", "tapdata", "--issue-type-id", "10008", "--issue-type-name", "Bug", cwd=ROOT
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
        check_project_boundaries(ws)
        check_project_json_objects(ws)
        check_station_binding_snapshot(ws)
        check_catalog_reference(ws)
        check_admission_documents(ws)

    finally:
        shutil.rmtree(ws, ignore_errors=True)
    print("\n结果：%d 通过，%d 失败" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
