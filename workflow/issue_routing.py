"""项目 Jira 类型分流：仅消费原生快照，不联网、不推断状态语义。"""
def resolve(profile, issue_key, snapshot):
    rules = profile.get("jira", {}).get("routing")
    if not isinstance(rules, dict) or rules.get("schema_version") != 1:
        raise ValueError("项目未配置 Jira 类型分流")
    if not isinstance(snapshot, dict) or str(snapshot.get("site", "")).rstrip("/") != profile["jira"]["site"].rstrip("/"):
        raise ValueError("类型回读必须来自当前 Jira 站点")
    issue = snapshot.get("issue", {})
    if not isinstance(issue, dict) or not isinstance(issue.get("fields"), dict):
        raise ValueError("类型回读缺少原生 issue.fields")
    fields = issue.get("fields", {})
    kind = fields.get("issuetype", {})
    project = fields.get("project", {})
    if (issue.get("key") != issue_key or not str(issue.get("id", "")).isdigit()
            or not isinstance(project, dict) or project.get("key") != profile["jira"]["project_key"]
            or not isinstance(kind, dict) or not str(kind.get("id", "")).isdigit()
            or not isinstance(kind.get("name"), str) or not kind["name"].strip()
            or not isinstance(snapshot.get("source_ref"), str) or not snapshot["source_ref"].strip()):
        raise ValueError("类型回读缺少任务、项目、类型 ID/名称或来源；不能当作未适配类型")
    mappings = rules.get("by_issue_type_id")
    fallback = rules.get("default_task_class")
    if not isinstance(mappings, dict) or not isinstance(fallback, str) or not fallback:
        raise ValueError("Jira 类型映射或默认流程无效")
    selected = mappings.get(str(kind["id"]), fallback)
    if not isinstance(selected, str) or not selected:
        raise ValueError("Jira 专用流程映射无效，禁止降级")
    return {"issue_id": str(issue["id"]), "issue_type_id": str(kind["id"]),
            "issue_type_name": kind["name"], "task_class": selected,
            "source_ref": snapshot["source_ref"]}


def check_task(profile, task, fields):
    """已绑定 run 只核对身份，不随最新配置重解释历史流程。"""
    binding = task.get("facts", {}).get("jira_routing")
    if binding is not None:
        kind = fields.get("issuetype", {})
        if (str(kind.get("id", "")) != binding["issue_type_id"]
                or task["task_class"] != binding["task_class"]):
            raise ValueError("Jira 类型或本地流程已变化；先核实接续，不能静默切换或降级")
    return binding


def marker_type_allowed(profile, task, issue_type):
    if issue_type in profile["jira"]["takeover_watermark"]["issue_type_ids"]:
        return True
    binding = task.get("facts", {}).get("jira_routing")
    return bool(binding and binding["issue_type_id"] == issue_type
                and binding["task_class"] == task["task_class"])
