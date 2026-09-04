"""按 Project 配置解析 Jira 影响版本及单一修复线；不实现 Jira 客户端。"""
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

from workflow import project_rules


FACT = "issue_version_plan"


def rules(base, task):
    return project_rules.class_spec(project_rules.load_admission(workspace=base),
                                   task["task_class"]).get("issue_versions")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def branch_for(name, spec):
    if name in spec["literal_branches"]:
        return name
    match = re.fullmatch(spec["version_pattern"], name)
    if not match:
        raise ValueError("影响版本没有明确的主仓分支映射：%s；不得回退 develop" % name)
    return spec["branch_template"].format(**match.groupdict())


def remote_refs(origin, branches):
    """一次精确查询；连接失败与不存在分别报告，不修改任何工作树/ref。"""
    print("正在核验主仓远端分支：%s（最长 30 秒）" % "、".join(sorted(branches)), file=sys.stderr, flush=True)
    try:
        result = subprocess.run(["git", "ls-remote", "--heads", origin,
                                 *["refs/heads/" + b for b in sorted(branches)]],
                                capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired as error:
        raise ValueError("主仓远端核验超时，事实未核验；不能认定版本不存在或 develop 不受影响") from error
    if result.returncode:
        raise ValueError("主仓远端核验失败（网络或权限），不能认定分支不存在")
    refs = {}
    for line in result.stdout.splitlines():
        sha, ref = line.split()
        if ref.startswith("refs/heads/") and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", sha):
            refs[ref[len("refs/heads/"):]] = sha
    return refs


def resolve(base, task, payload):
    spec = rules(base, task)
    if not spec:
        raise ValueError("当前任务类型未配置影响版本规则")
    if not isinstance(payload, dict):
        raise ValueError("影响版本输入必须是对象")
    issue = payload.get("issue", {})
    if not isinstance(issue, dict) or issue.get("key") != task["issue_key"] or not isinstance(payload.get("source_ref"), str) or not payload["source_ref"].strip():
        raise ValueError("必须提供当前 Jira 任务回读及可回查 source_ref")
    fields = issue.get("fields", {})
    raw = fields.get(spec["field"]) if isinstance(fields, dict) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError("Jira 影响版本为空或未读取；不得用描述、修复版本或 develop 猜测代替")
    versions = []
    for value in raw:
        if not isinstance(value, dict) or not value.get("id") or not isinstance(value.get("name"), str):
            raise ValueError("影响版本必须有 Jira id 和 name")
        versions.append({"id": str(value["id"]), "name": value["name"],
                         "branch": branch_for(value["name"], spec)})
    if len({v["id"] for v in versions}) != len(versions):
        raise ValueError("影响版本 ID 重复")
    develop = payload.get("develop", {})
    if not isinstance(develop, dict) or develop.get("status") not in ("present", "absent") or not isinstance(develop.get("source_ref"), str) or not develop["source_ref"].strip():
        raise ValueError("先核验优先分支是否存在同一缺陷并给出源码/复现证据；unknown 不能作为 absent")
    preferred = spec["preferred_branch"]
    branches = {preferred, *[v["branch"] for v in versions]}
    profile = project_rules.load_profile(workspace=base)
    origin = project_rules.resolve_branches(profile, spec["product_repository"])["origin"]
    refs = remote_refs(origin, branches)
    missing = sorted(branches - refs.keys())
    if missing:
        raise ValueError("主仓不存在对应分支，拒绝本次缺陷规划：%s" % "、".join(missing))
    if develop.get("revision") != refs[preferred]:
        raise ValueError("优先分支核验必须绑定当前主仓完整 SHA；代码已变或证据不精确，请重新分析")
    selected = payload.get("selected_version_id")
    if selected is not None and str(selected) not in {v["id"] for v in versions}:
        raise ValueError("选择的版本不属于 Jira 影响版本")
    if develop["status"] == "present":
        primary = preferred
        selected = None
    else:
        candidates = [v for v in versions if v["branch"] != preferred]
        if selected is None and len(candidates) == 1:
            selected = candidates[0]["id"]
        match = next((v for v in candidates if v["id"] == str(selected)), None)
        if not match:
            raise ValueError("优先分支不受影响：必须明确选择一个受影响版本，不能同时编码多条修复线")
        selected, primary = match["id"], match["branch"]
    return {"run_id": task["run_id"], "rules_digest": digest(spec),
            "source_ref": payload["source_ref"], "versions": versions,
            "develop": {k: develop[k] for k in ("status", "revision", "source_ref")},
            "selected_version_id": selected, "primary_branch": primary,
            "manual_merge": [dict(v, action="研发人工合并修复并单独验证") for v in versions if v["branch"] != primary],
            "refs": {b: refs[b] for b in sorted(branches)},
            "refs_verified_at": datetime.now(timezone.utc).isoformat(),
            "origin": origin}


def problems(base, task):
    spec = rules(base, task)
    if not spec:
        return []
    plan = task.get("facts", {}).get(FACT)
    if not isinstance(plan, dict) or plan.get("run_id") != task["run_id"] or plan.get("rules_digest") != digest(spec):
        return ["影响版本与优先修复线尚未核验：使用 task.py issue-versions 导入 Jira fields.versions 及 develop 核验证据"]
    profile = project_rules.load_profile(workspace=base)
    result = []
    current_origin = project_rules.resolve_branches(profile, spec["product_repository"])["origin"]
    if plan["origin"] != current_origin:
        result.append("主仓 origin 已变化，影响版本核验失效，需重新规划")
    for repo in task.get("repositories", []):
        if repo["repository"] == spec["product_repository"] and repo["base_branch"] != plan["primary_branch"]:
            result.append("主仓基线与本次唯一修复线不一致：%s" % plan["primary_branch"])
    if plan["primary_branch"] == spec["preferred_branch"]:
        for repo in task.get("repositories", []):
            expected = project_rules.resolve_branches(profile, repo["repository"])["baseline_branch"]
            if repo["base_branch"] != expected:
                result.append("%s 应按优先修复线对齐 %s，不能在影响版本另起修复" % (repo["repository"], expected))
    return result
