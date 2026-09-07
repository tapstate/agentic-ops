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
    """导入初始观察与本次用户决定；版本名称不推导 Git 分支。"""
    spec = rules(base, task)
    if not spec or not isinstance(payload, dict):
        raise ValueError("当前任务缺少影响版本规则或输入对象")
    issue = payload.get("issue", {})
    if (not isinstance(issue, dict) or issue.get("key") != task["issue_key"]
            or not isinstance(issue.get("fields"), dict)
            or not isinstance(payload.get("source_ref"), str) or not payload["source_ref"].strip()):
        raise ValueError("必须提供当前 Jira 任务快照及 source_ref")
    previous = task.get("facts", {}).get(FACT, {})
    if previous.get("run_id") != task["run_id"]:
        previous = {}
    observed = previous.get("observed", task.get("facts", {}).get("jira_snapshot", {"issue": issue, "source_ref": payload["source_ref"]}))
    raw = observed["issue"].get("fields", {}).get(spec["field"])
    if raw is not None and not isinstance(raw, list):
        raise ValueError("Jira 影响版本读取格式无效")
    effective = payload.get("effective", {})
    if not isinstance(effective, dict):
        raise ValueError("effective 必须包含本地有效事实和确认来源")
    versions = effective.get("versions", raw)
    if not isinstance(versions, list) or not versions:
        raise ValueError("影响版本缺失，需要用户确认本次采用的版本")
    if any(not isinstance(v, dict) or not isinstance(v.get("name"), str) or not v["name"].strip() for v in versions):
        raise ValueError("本地有效版本必须包含非空 name，无需 Jira ID")
    if len({v["name"] for v in versions}) != len(versions):
        raise ValueError("本地有效版本名称重复")
    from workflow import quality
    proof = effective.get("proof")
    if (not isinstance(proof, dict) or any(not isinstance(proof.get(key), str) or not proof[key].strip()
            for key in ("actor", "source", "reference", "at"))
            or proof["source"] not in ("user_message", "jira_comment", "review")):
        raise ValueError("需要真实用户确认来源：actor/source/reference/at")
    quality.check_proof(proof)
    branch = effective.get("execution_branch")
    if not isinstance(branch, str) or not branch.strip():
        raise ValueError("必须独立确认 execution_branch，不能由版本名推导")
    develop = payload.get("develop", {})
    if (not isinstance(develop, dict) or develop.get("status") not in ("present", "absent")
            or not isinstance(develop.get("source_ref"), str) or not develop["source_ref"].strip()):
        raise ValueError("先核验优先分支是否存在同一缺陷，unknown 不能作为 absent")
    preferred = spec["preferred_branch"]
    if develop["status"] == "present" and branch != preferred:
        raise ValueError("优先分支存在同一缺陷，应在该分支修复")
    profile = project_rules.load_profile(workspace=base)
    origin = project_rules.resolve_branches(profile, spec["product_repository"])["origin"]
    refs = remote_refs(origin, {preferred, branch})
    if {preferred, branch} - refs.keys():
        raise ValueError("实际选择的实施分支或优先分析分支不存在")
    if develop.get("revision") != refs[preferred]:
        raise ValueError("优先分支证据必须绑定当前完整 SHA")
    observed_names = sorted(v["name"] for v in raw) if isinstance(raw, list) and all(isinstance(v, dict) and isinstance(v.get("name"), str) for v in raw) else None
    return {"run_id": task["run_id"], "rules_digest": digest(spec),
            "observed": observed, "source_ref": observed["source_ref"],
            "versions": versions, "confirmation": effective["proof"],
            "primary_branch": branch, "develop": develop,
            "release_follow_up": effective.get("release_follow_up", "其它影响版本的合并与验证待确认"),
            "sync_status": "pending" if sorted(v["name"] for v in versions) != observed_names else "not_needed",
            "refs": refs, "refs_verified_at": datetime.now(timezone.utc).isoformat(),
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
