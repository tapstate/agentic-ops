from __future__ import annotations

from pathlib import Path
from typing import Any

from ao_work.config import load_jira_context, validate_workspace_jira_binding
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.service import JiraService
from ao_work.jira.task_facts import read_task_facts
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state import TaskStore
from ao_work.workspace import Workspace


def collect_task_facts(client: JiraClient, issue: Any, profile: Any) -> dict[str, Any]:
    try:
        comments = client.comments(issue.key)
    except RuntimeErrorResult as error:
        raise _blocked(
            "jira_task_comment_read_failed",
            "无法读取 Jira 任务评论以提取执行事实",
            "请检查 Jira 评论读取权限和网络后重试；不要以猜测替代评论事实",
            cause_code=error.code,
        ) from error
    return read_task_facts(issue, comments, profile)


def execute_task_facts(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str,
) -> dict[str, Any]:
    """公开的只读任务事实操作；不写 Jira 或本地任务状态。"""

    context = load_jira_context(workspace, install_root)
    if not issue_key.startswith(f"{context.profile.project_key}-"):
        raise _blocked(
            "jira_workspace_mismatch",
            "任务不属于当前 developer 工作空间绑定的 Jira Project",
            "请切换到对应业务项目工作空间后重试",
        )
    email, token = context.require_credentials()
    client = JiraClient(
        context.profile,
        UrllibJiraTransport(context.connection, email, token),
    )
    account = client.current_user_details()
    validate_workspace_jira_binding(
        workspace,
        context.connection,
        account_id=account["account_id"],
        install_root=install_root,
    )
    issue = JiraService(context.profile, client).inspect_issue(issue_key)
    if issue.assignee != account["account_id"]:
        raise _blocked(
            "assignee_mismatch",
            "当前业务工作空间 Jira 账户不是任务经办人",
            "请切换到任务经办人的工作空间后重试",
        )
    task = store.inspect(issue_key)["task"]
    if task.get("jira_issue_id") != issue.issue_id:
        raise _blocked(
            "task_identity_mismatch",
            "本地任务状态与 Jira 任务身份不一致",
            "请停止并核对当前任务运行后重试",
        )
    return {
        "issue_key": issue.key,
        "agentic_run_id": str(task["agentic_run_id"]),
        "task_facts": collect_task_facts(client, issue, context.profile),
        "side_effects": [],
    }


def execute_task_inspect(
    workspace: Workspace,
    install_root: Path,
    store: TaskStore,
    issue_key: str,
) -> dict[str, Any]:
    """读取本地任务状态及受控 Jira 任务详情，不输出原始正文。"""

    local_state = store.inspect(issue_key)
    facts_state = execute_task_facts(workspace, install_root, store, issue_key)
    return {
        **local_state,
        "task_facts": facts_state["task_facts"],
        "side_effects": facts_state["side_effects"],
    }


def _blocked(code: str, message: str, action: str, **details: Any) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
        details=details,
    )
