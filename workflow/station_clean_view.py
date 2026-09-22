"""清理计划的只读决策视图；不产生另一份执行摘要或删除授权。"""
import json
import subprocess

from workflow import station_clean_rules, station_directories, station_source, task_store


def describe(base, task=None, plan=None, operation=None, blockers=()):
    operation = operation or {}
    result = {"complete": bool(plan), "executable": bool(plan) and not blockers,
              "remove_or_reset": [], "preserve_before_clear": [], "retain": [], "unknown": [],
              "blockers": list(blockers), "boundary": "仅展示范围；执行仍须核验原计划摘要与确认，不扩大删除权限"}

    def row(group, target, action, reason, choices, **details):
        result[group].append({"target": target, "action": action, "reason": reason,
                              "allowed_decisions": choices, **details})

    roots = {}
    try:
        if task:
            roots = station_directories.load(base, task)
        init = json.loads((task_store.state_path(base) / "init.json").read_text())
        owned = [entry["path"] for entry in init.get("artifacts", [])]
        for name in owned:
            row("retain", name, "retain", "初始化接线，任务退出不卸载工位", ["retain"])
        observed = station_clean_rules.inspect(base, owned, roots, partial=True)
        result["blockers"].extend(observed["errors"])
        # 观测只列根，不遍历配置、保留材料或正式档案。
        for name, decision in observed["objects"].items():
            action = decision["action"]
            if action == "preserve":
                row("retain", name, "retain", "保留规则：" + decision["layer"], ["retain", "separate_request"])
            elif action == "block" or (action == "remove" and name not in roots):
                row("unknown", name, "no_action", "类型、归属或清理规则未核验", ["clarify", "cancel"])
        if not plan:
            for name in roots:
                row("unknown", name, "unverified", "已登记但完整清理计划尚未生成；不授权删除", ["clarify", "cancel"])
    except (ValueError, OSError, KeyError, TypeError) as error:
        result["blockers"].append(str(error))
        row("unknown", "工位范围", "unverified", "范围观测未完成", ["clarify", "cancel"])
    row("retain", "Product Root .archive", "retain", "正式档案不在任务清理范围，不遍历历史档案", ["retain", "separate_request"])
    row("retain", ".agenticops 工位绑定及操作记录", "retain", "仅撤销活动任务材料；工位卸载是独立操作", ["retain"])
    row("retain", "source 独立仓库", "retain", "保留仓库不等于保留当前工作区修改；复位与成果处置见下列清单", ["retain", "separate_request"])
    row("retain", "runtime 根目录", "retain", "只回收任务独占内容，保留空根供后续任务使用", ["retain"])
    if plan:
        result["plan_digest"] = plan["digest"]
        result["confirmation"] = "展示不代表确认；以原确认请求及回执为准"
        for relative in plan["active_state"]["files"]:
            row("remove_or_reset", ".agenticops/" + relative, "clear_active", "必要证据归档后清除活动副本并撤销授权", ["confirm", "cancel"])
        for entry in plan["directories"]:
            row("remove_or_reset", entry["path"], entry["disposition"], "当前 run 的受管目录", ["confirm", "cancel"],
                identity=entry, recovery="仅列入成果或报告保全的内容可恢复；缓存及其它生成物可重建")
        for entry in plan["entries"]:
            row("preserve_before_clear", entry["path"], entry["preservation"]["action"], "源码成果处置", ["archive", "export", "discard_with_exact_confirmation"], evidence=entry)
        for relative in plan["archive_runtime"]:
            row("preserve_before_clear", "runtime/" + relative, "archive", "仅保存符合归档规则的脱敏日志和报告，不备份整个 runtime", ["confirm", "cancel"])
        for name, state in plan["source"].items():
            row("remove_or_reset", "source/" + name, "checkout_baseline", "保全成果后回到已核验开发 SHA，不移动命名分支", ["confirm", "cancel"], source=state)
            if state.get("preserved_ref"):
                row("retain", name + ":" + state["preserved_ref"], "retain", "计划保留任务 Head", ["retain", "separate_request"], sha=state["preserved_head"])
            try:
                repo = station_source.repository_path(base, name)
                refs = station_source.git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/remotes").stdout.splitlines()
                row("retain", name + " refs", "retain", "本地观测；远端现状及保护规则未经在线核验", ["retain", "separate_request"], refs=refs)
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                row("unknown", name + " refs", "unverified", str(error), ["clarify"])
        for entry in plan["external"]:
            group = "retain" if entry["action"] == "retain" else "remove_or_reset"
            row(group, entry["id"], entry["action"], "已登记外部资源；不是本次已执行结果", ["retain", "separate_request"] if group == "retain" else ["confirm", "cancel"], evidence=entry)
    if task:
        for name, state in task.get("retained_repositories", {}).items():
            row("retain", "source/" + name, "retain", "未选中持久仓库，保持接管前状态", ["retain", "separate_request"], observation=state)
    if operation:
        result["operation"] = {"id": operation.get("operation_id"), "status": operation.get("status"), "phase": operation.get("phase"),
            "completed_steps": [key for key, step in operation.get("steps", {}).items() if step.get("receipt") is not None],
            "pending_steps": [key for key, step in operation.get("steps", {}).items() if step.get("receipt") is None],
            "receipts": {key: step["receipt"] for key, step in operation.get("steps", {}).items() if step.get("receipt") is not None},
            "archive_ref": operation.get("archive_ref"), "plan_revisions": len(operation.get("plan_revisions", []))}
        result["executable"] = False  # 恢复原操作，不把观测转换成新授权。
        result["scope_state"] = "已按原计划核验完成；具体结果见步骤回执" if operation.get("status") == "done" else "原确认范围；未完成不代表已清理"
    if result["blockers"] or result["unknown"]:
        result["executable"] = False
        result["complete"] = False
    return result


def safe_describe(*args, **kwargs):
    try:
        return describe(*args, **kwargs)
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        return {"complete": False, "executable": False, "blockers": [str(error)],
                "boundary": "清单展示失败；不得据此重试已成功的生命周期操作"}
