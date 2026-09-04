#!/usr/bin/env python3
"""项目规则机读入口：准入清单、验证规则、证据敏感词、分支与 Jira 工作流解析。

设计原则：**规则要么可执行，要么不是规则。**
`projects/<project>/admission.json` 是准入与验证规则的唯一事实源，
`projects/<project>/admission/*.md` 由本模块生成（人读视图，勿手工编辑），
强制点在 workflow/task.py（阶段推进）与 workflow/evidence.py（证据输出）。

用法：
  python3 workflow/project_rules.py render            # 由 admission.json 重新生成三张清单 md
  python3 workflow/project_rules.py render --check    # 只校验 md 与 json 是否漂移（漂移 exit 1）
  python3 workflow/project_rules.py branch --repo tapdata/tapdata   # 查表解析分支，查不到 exit 2
  python3 workflow/project_rules.py workflow --issue-type-id 10008 --issue-type-name 任务 --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def canonical_repository_endpoint(value):
    """把 Project catalog origin 规范化为保留 host 身份的稳定 endpoint。"""
    text = (value or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("/"):
        return str(Path(text).resolve())
    if text.startswith("file://"):
        return "file://" + str(Path(text[7:]).resolve())
    scp = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", text)
    if scp:
        host, path = scp.groups()
        endpoint = "%s/%s" % (host.lower(), path.lstrip("/"))
    else:
        remote = re.fullmatch(
            r"(?:ssh|https?|git)://(?:[^@/]+@)?([^/]+)/(.+)", text
        )
        if not remote:
            return ""
        host, path = remote.groups()
        endpoint = "%s/%s" % (host.lower(), path.lstrip("/"))
    return endpoint[:-4] if endpoint.endswith(".git") else endpoint


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def project_from_workspace(workspace):
    path = Path(workspace).resolve() / ".agenticops" / "workspace.json"
    if not path.is_file():
        raise ValueError("工作空间缺少 .agenticops/workspace.json，请先执行 agenticops init")
    binding = _read_json(path)
    project = binding.get("project")
    if not isinstance(project, str) or not project:
        raise ValueError("工作空间绑定缺少 project")
    return project


def product_root_from_workspace(workspace):
    path = Path(workspace).resolve() / ".agenticops" / "workspace.json"
    binding = _read_json(path)
    root = binding.get("product_root")
    if not isinstance(root, str) or not root:
        raise ValueError("工作空间绑定缺少 product_root")
    product = Path(root).resolve()
    if not product.is_dir():
        raise ValueError("工作空间绑定的 Product Root 不存在：%s" % product)
    return product


def project_root(root=ROOT, project="tapdata"):
    path = Path(root) / "projects" / project
    if not path.is_dir():
        raise ValueError("未安装项目适配：%s" % project)
    return path


def load_admission(root=ROOT, project="tapdata", workspace=None):
    selected = project_from_workspace(workspace) if workspace is not None else project
    selected_root = product_root_from_workspace(workspace) if workspace is not None else root
    return _read_json(project_root(selected_root, selected) / "admission.json")


def validate_takeover_watermark(profile):
    """每个现役 Jira Project Package 都必须声明接管版本水印。"""
    jira = profile.get("jira")
    value = jira.get("takeover_watermark") if isinstance(jira, dict) else None
    if not isinstance(value, dict):
        raise ValueError("项目 Profile 缺少 jira.takeover_watermark 必要配置")
    required = {
        "schema_version", "required", "logical_key", "field_id", "field_name",
        "field_type", "issue_type_ids", "write_mode",
    }
    if set(value) != required or value.get("schema_version") != 1:
        raise ValueError("jira.takeover_watermark 结构无效")
    if value.get("required") is not True:
        raise ValueError("jira.takeover_watermark 必须 required=true")
    if value.get("logical_key") != "agenticops_version":
        raise ValueError("jira.takeover_watermark.logical_key 必须为 agenticops_version")
    if not isinstance(value.get("field_id"), str) or not re.fullmatch(r"customfield_[1-9][0-9]*", value["field_id"]):
        raise ValueError("jira.takeover_watermark.field_id 必须是 Jira customfield_<数字>")
    if not isinstance(value.get("field_name"), str) or not value["field_name"].strip():
        raise ValueError("jira.takeover_watermark.field_name 无效")
    if value.get("field_type") != "string":
        raise ValueError("jira.takeover_watermark.field_type 必须为 string")
    issue_types = value.get("issue_type_ids")
    if (not isinstance(issue_types, list) or not issue_types or
            any(not isinstance(item, str) or not re.fullmatch(r"[1-9][0-9]*", item) for item in issue_types) or
            len(issue_types) != len(set(issue_types))):
        raise ValueError("jira.takeover_watermark.issue_type_ids 必须是非空且不重复的 Jira 事务类型 ID")
    if value.get("write_mode") != "overwrite":
        raise ValueError("jira.takeover_watermark.write_mode 必须为 overwrite")
    return value


def load_profile(root=ROOT, project="tapdata", workspace=None):
    selected = project_from_workspace(workspace) if workspace is not None else project
    selected_root = product_root_from_workspace(workspace) if workspace is not None else root
    profile = _read_json(project_root(selected_root, selected) / "profile.json")
    validate_takeover_watermark(profile)
    reference = profile.get("repositories", {}).get("catalog")
    if not isinstance(reference, str) or not reference:
        raise ValueError("项目 Profile 缺少 repositories.catalog")
    catalog_path = (project_root(selected_root, selected) / reference).resolve()
    try:
        catalog_path.relative_to(project_root(selected_root, selected).resolve())
    except ValueError as error:
        raise ValueError("项目仓库目录路径越界：%s" % reference) from error
    catalog = _read_json(catalog_path)
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("repositories"), dict):
        raise ValueError("项目仓库目录结构无效：%s" % catalog_path)
    for repository, entry in catalog["repositories"].items():
        parts = repository.split("/") if isinstance(repository, str) else []
        if len(parts) != 2 or any(part in ("", ".", "..") for part in parts):
            raise ValueError("项目仓库目录存在无效 owner/repo：%s" % repository)
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry.get(key)
            for key in ("origin", "baseline_branch", "dev_branch")
        ):
            raise ValueError("项目仓库目录条目不完整：%s" % repository)
    profile["repositories"] = catalog
    return profile


def load_repository_catalog(root=ROOT, project="tapdata", workspace=None):
    return load_profile(root=root, project=project, workspace=workspace)["repositories"]


def repository_catalog_path(root=ROOT, project="tapdata", workspace=None):
    selected = project_from_workspace(workspace) if workspace is not None else project
    selected_root = product_root_from_workspace(workspace) if workspace is not None else root
    base = project_root(selected_root, selected).resolve()
    profile = _read_json(base / "profile.json")
    reference = profile.get("repositories", {}).get("catalog")
    if not isinstance(reference, str) or not reference:
        raise ValueError("项目 Profile 缺少 repositories.catalog")
    path = (base / reference).resolve()
    try:
        path.relative_to(base)
    except ValueError as error:
        raise ValueError("项目仓库目录路径越界：%s" % reference) from error
    return path


def validate_project_issue(profile, issue_key):
    expected = str(profile.get("jira", {}).get("project_key") or "").upper()
    actual = str(issue_key).split("-", 1)[0].upper()
    if not expected:
        raise ValueError("项目 Profile 缺少 jira.project_key")
    if actual != expected:
        raise ValueError("任务 %s 不属于当前项目（期望 %s-*）" % (issue_key, expected))


def resolve_issue_type_workflow(profile, issue_type_id=None, issue_type_name=None):
    """按 Jira 事务类型精确选择工作流；没有唯一映射时失败关闭。"""
    issue_type_id = str(issue_type_id or "").strip()
    issue_type_name = str(issue_type_name or "").strip()
    if not issue_type_id and not issue_type_name:
        raise ValueError("必须提供 Jira 事务类型 ID 或名称")
    workflows = profile.get("workflows_by_issue_type")
    if not isinstance(workflows, list):
        raise ValueError("项目 Profile 缺少 workflows_by_issue_type")
    matches = []
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        issue_type = workflow.get("issue_type")
        if not isinstance(issue_type, dict):
            continue
        configured_id = str(issue_type.get("id") or "").strip()
        configured_name = str(issue_type.get("name") or "").strip()
        if not configured_id or not configured_name:
            continue
        if issue_type_id and issue_type_id != configured_id:
            continue
        if issue_type_name and issue_type_name != configured_name:
            continue
        matches.append(workflow)
    if len(matches) != 1:
        supplied = "ID=%s，名称=%s" % (issue_type_id or "未提供", issue_type_name or "未提供")
        if not matches:
            raise LookupError("事务类型没有工作流映射：%s" % supplied)
        raise ValueError("事务类型工作流映射不唯一：%s" % supplied)
    workflow = matches[0]
    statuses = workflow.get("statuses")
    transitions = workflow.get("transitions")
    if not isinstance(statuses, list) or not isinstance(transitions, dict):
        raise ValueError("事务类型工作流结构无效")
    for status in statuses:
        if not isinstance(status, dict) or not all(
            isinstance(status.get(key), str) and status[key] for key in ("id", "name", "stage")
        ):
            raise ValueError("事务类型工作流状态结构无效")
    return workflow


def class_spec(spec, task_class):
    classes = spec.get("task_classes", {})
    if task_class not in classes:
        raise ValueError("未知任务类型 %s（可选：%s）" % (task_class, "/".join(sorted(classes))))
    return classes[task_class]


def known_fact_keys(spec, task_class):
    cls = class_spec(spec, task_class)
    keys = [f["key"] for f in cls.get("required_facts", [])]
    keys += [f["key"] for f in cls.get("optional_facts", [])]
    keys += list(spec.get("common_fact_keys", []))
    return sorted(set(keys))


def missing_required(spec, task_class, facts):
    """返回缺失（未记录或空值）的必填项定义列表。"""
    facts = facts or {}
    out = []
    for f in class_spec(spec, task_class).get("required_facts", []):
        value = facts.get(f["key"])
        if value is None or not str(value).strip():
            out.append(f)
    return out


def _compile(rule):
    flags = re.I if "i" in (rule.get("flags") or "") else 0
    return re.compile(rule["pattern"], flags | re.M)


def check_verification(spec, text):
    """返回验证结论不合规的原因列表（空列表 = 合规）。"""
    rules = spec.get("verification_rules", {})
    problems = []
    body = (text or "").strip()
    if not body:
        return ["未记录验证结论（%s）" % rules.get("required_fact", "verification")]
    if len(body) < int(rules.get("min_length", 0)):
        problems.append("验证结论过短（少于 %d 字符），需包含实际命令及退出结果" % rules["min_length"])
    for rule in rules.get("forbidden_patterns", []):
        if _compile(rule).search(body):
            problems.append(rule["reason"])
    return problems


def scan_sensitive(spec, text):
    """扫描证据文本中的敏感内容，返回 [(行号, 行内容, 原因)]。"""
    hits = []
    rules = spec.get("evidence_rules", {}).get("forbidden_patterns", [])
    compiled = [(_compile(r), r["reason"]) for r in rules]
    for idx, line in enumerate((text or "").splitlines(), start=1):
        for regex, reason in compiled:
            if regex.search(line):
                hits.append((idx, line.strip(), reason))
                break
    return hits


def resolve_branches(profile, repo):
    """按 profile.json 查表解析仓库分支；查不到就报错，绝不猜测。"""
    repos = profile.get("repositories", {})
    entry = repos.get("repositories", {}).get(repo)
    if entry is None:
        raise LookupError(
            "repositories.json 未登记仓库 %s；禁止从当前分支或隐式 main 猜测，"
            "请先在当前 Project Package 补齐后重试" % repo
        )
    return {
        "repository": repo,
        "origin": entry.get("origin"),
        "baseline_branch": entry.get("baseline_branch"),
        "dev_branch": entry.get("dev_branch"),
    }


# ---- 人读视图生成 ---------------------------------------------------------


def _fact_rows(facts, with_example):
    lines = []
    head = "| 项 | fact key | 到哪里找 | 示例 | 说明 |" if with_example else "| 项 | fact key | 到哪里找 | 说明 |"
    sep = "|---|---|---|---|---|" if with_example else "|---|---|---|---|"
    lines.append(head)
    lines.append(sep)
    for f in facts:
        if with_example:
            lines.append(
                "| %s | `%s` | %s | %s | %s |"
                % (f["label"], f["key"], f.get("source", ""), f.get("example", ""), f.get("note", ""))
            )
        else:
            lines.append("| %s | `%s` | %s | %s |" % (f["label"], f["key"], f.get("source", ""), f.get("note", "")))
    return lines


def render_admission_markdown(spec, task_class, project="tapdata"):
    cls = class_spec(spec, task_class)
    L = []
    L.append("# %s任务准入检查清单（%s）" % (cls["title"], project))
    L.append("")
    L.append(
        "> **本文件由 `projects/%s/admission.json` 生成，请勿手工编辑。**\n"
        "> 改规则请改 JSON，然后执行 `python3 workflow/project_rules.py render --project %s`。"
        % (project, project)
    )
    L.append("")
    L.append("执行时不要读本文件，直接用机读接口：")
    L.append("")
    L.append("```sh")
    L.append("python3 workflow/task.py checklist --task-class %s          # 人读" % task_class)
    L.append("python3 workflow/task.py checklist --task-class %s --json   # 机读" % task_class)
    L.append("python3 workflow/task.py record --issue-key <JIRA-KEY> --key <fact key> --value <值>")
    L.append("```")
    L.append("")
    flexible = cls.get("quality_mode") == "recorded_decision"
    L.append("## 核对项（缺项披露，在质量检查点记录处置）" if flexible else "## 必填项（缺一不可，`task.py advance` 硬拦）")
    L.append("")
    L.extend(_fact_rows(cls.get("required_facts", []), with_example=True))
    L.append("")
    proposable = [f["label"] for f in cls.get("required_facts", []) if f.get("agent_may_propose")]
    if proposable:
        L.append(
            "以下项 agent 可结合卡片/日志/源码给出**建议值**，但必须由研发工程师确认后再 record，"
            "不得替确认：%s。" % "、".join(proposable)
        )
        L.append("")
    optional = cls.get("optional_facts", [])
    if optional:
        L.append("## 可选项（有则记录）")
        L.append("")
        L.extend(_fact_rows(optional, with_example=False))
        L.append("")
    on_missing = cls.get("on_missing", spec.get("on_missing", {}))
    L.append("## 准入失败流程（强制点：%s）" % on_missing.get("enforced_by", ""))
    L.append("")
    for i, step in enumerate(on_missing.get("steps", []), start=1):
        L.append("%d. %s" % (i, step))
    L.append("")
    gate = spec.get("pre_implementation_gate", {})
    L.append("## 修复前门禁（强制点：%s）" % gate.get("enforced_by", ""))
    L.append("")
    L.append("授权作用域 `%s`，由 %s。%s" % (gate.get("authorization_scope"), gate.get("issued_by"), gate.get("note")))
    L.append("")
    vr = spec.get("verification_rules", {})
    L.append("## 验证结论规则（强制点：%s）" % vr.get("enforced_by", ""))
    L.append("")
    if flexible:
        L.append("由 `workflow/quality.py` 核对用例、实际执行及用户处置；用户可选择补测、不适用、延期或接受风险。原始结果不改写，不要求全绿。文本 verification 仅为记录，不代替用例验收。")
        L.append("仓库基线、实施授权及外部 Jira Validator 仍独立生效。详见 [质量检查与证据](../../../docs/usage/quality-checkpoints.md)。")
        L.append("")
        return "\n".join(L)
    L.append("离开 implementation 前必须 `record --key %s`，且不得命中：" % vr.get("required_fact"))
    L.append("")
    for rule in vr.get("forbidden_patterns", []):
        L.append("- `%s` —— %s" % (rule["pattern"], rule["reason"]))
    L.append("")
    return "\n".join(L)


DOC_NAMES = {
    "defect_fix": "defect-fix.md",
    "feature_change": "feature-change.md",
    "technical_task": "technical-task.md",
}


def cmd_render(args):
    spec = load_admission(args.root, project=args.project)
    outdir = Path(args.root) / "projects" / args.project / "admission"
    drift = []
    for task_class in spec.get("task_classes", {}):
        path = outdir / DOC_NAMES[task_class]
        body = render_admission_markdown(spec, task_class, project=args.project)
        if args.check:
            current = path.read_text(encoding="utf-8") if path.is_file() else ""
            if current != body:
                drift.append(str(path.relative_to(args.root)))
        else:
            path.write_text(body, encoding="utf-8")
            print("已生成 %s" % path.relative_to(args.root))
    if args.check:
        if drift:
            print("清单 md 与 admission.json 已漂移：%s（跑 workflow/project_rules.py render）" % "、".join(drift), file=sys.stderr)
            return 1
        print("清单 md 与 admission.json 一致。")
    return 0


def cmd_branch(args):
    try:
        info = resolve_branches(load_profile(args.root, project=args.project), args.repo)
    except (LookupError, ValueError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print("仓库：%s" % info["repository"])
        print("基线分支（baseline）：%s" % (info["baseline_branch"] or "未登记"))
        print("开发分支（dev）：%s" % (info["dev_branch"] or "未登记"))
    return 0


def cmd_workflow(args):
    try:
        workflow = resolve_issue_type_workflow(
            load_profile(args.root, project=args.project),
            issue_type_id=args.issue_type_id,
            issue_type_name=args.issue_type_name,
        )
    except (LookupError, ValueError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(workflow, ensure_ascii=False))
    else:
        issue_type = workflow["issue_type"]
        print("事务类型：%s（%s）" % (issue_type["name"], issue_type["id"]))
        print("状态：%s" % "；".join(
            "%s（%s）→ %s" % (item["name"], item["id"], item["stage"])
            for item in workflow["statuses"]
        ))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("render", help="由 admission.json 生成人读清单")
    p.add_argument("--check", action="store_true", help="只校验漂移，不写文件")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--project", default="tapdata")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("branch", help="查表解析仓库分支（禁止猜测）")
    p.add_argument("--repo", required=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--project", default="tapdata")
    p.set_defaults(func=cmd_branch)

    p = sub.add_parser("workflow", help="按 Jira 事务类型精确解析工作流（未知类型失败关闭）")
    p.add_argument("--issue-type-id")
    p.add_argument("--issue-type-name")
    p.add_argument("--json", action="store_true")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--project", default="tapdata")
    p.set_defaults(func=cmd_workflow)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
