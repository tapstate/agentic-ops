from __future__ import annotations

import json
import fnmatch
import hashlib
import os
import selectors
import signal
import subprocess
import tempfile
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from ao_work.config import (
    load_jira_connection,
    load_jira_context,
    load_project_profile,
    validate_workspace_jira_binding,
    validate_workspace_project_binding,
)
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.jira.cli import read_bound_jira_attempt, read_bound_jira_plan
from ao_work.jira.service import JiraService
from ao_work.managed_io import read_managed_text
from ao_work.git_security import parse_github_repository_url
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_run.protocol import (
    PROHIBITED_ACTIONS,
    PROTOCOL,
    QUALITY_CATEGORIES,
    SCHEMA_VERSION,
    blocked,
    digest,
    event_envelope,
    load_json_object,
    manifest_digest,
    parse_json_text,
    reject_sensitive_content,
    result_digest,
    validate_event,
    validate_manifest,
    validate_verification_command,
    verification_digest,
    IMPORTED_ACTIONS,
)
from ao_work.task_state.io import append_ndjson, atomic_write_json, read_json, read_text
from ao_work.task_state.locking import TaskLock
from ao_work.workspace import Workspace

MAX_COMMAND_OUTPUT_BYTES = 4_194_304


class TaskRunProtocol:
    """可信 task→PR 审计；关键事实只能由 Runtime 的确定性 probe 追加。"""

    def __init__(
        self, workspace: Workspace, *, install_root: Path, lock_timeout: float
    ) -> None:
        self.workspace = workspace
        self.install_root = install_root
        self.lock_timeout = lock_timeout

    def open(self, manifest_value: str) -> dict[str, Any]:
        manifest_path = self._input_file(manifest_value, "manifest")
        manifest = validate_manifest(load_json_object(manifest_path, "manifest"))
        self._validate_workspace_binding(manifest)
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            if paths["result"].exists():
                read_json(paths["result"])
                raise blocked(
                    "task_run_already_finalized",
                    "当前 task-run 已生成不可变结果包",
                    "请读取现有 result.json；新的执行必须使用新的 agentic_run_id",
                )
            if paths["manifest"].exists():
                existing = validate_manifest(read_json(paths["manifest"]))
                if manifest_digest(existing) != manifest_digest(manifest):
                    raise blocked(
                        "manifest_changed_after_open",
                        "task-run 打开后 manifest 发生变化",
                        "请使用新的 agentic_run_id 重新确认完整 manifest",
                    )
                return self._open_result(paths, manifest, created=False)
            paths["root"].mkdir(parents=True, exist_ok=False)
            atomic_write_json(paths["manifest"], manifest)
            atomic_write_json(
                paths["state"],
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "status": "open",
                    "manifest_sha256": manifest_digest(manifest),
                },
            )
            return self._open_result(paths, manifest, created=True)

    def record(self, manifest_value: str, event_value: str) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        event_path = self._input_file(event_value, "event")
        event = validate_event(load_json_object(event_path, "event"))
        if event["evidence_origin"] != "imported" or event["actor"] == "runtime":
            raise blocked(
                "trusted_event_import_forbidden",
                "task-run record 只能导入非 Runtime 的人工/AI/项目工具过程事件",
                "请使用 task-run probe-jira、probe-git、probe-pr、verify 或 probe-prohibitions 采集关键事实",
            )
        if event["action"] not in IMPORTED_ACTIONS:
            raise blocked(
                "trusted_fact_import_forbidden",
                f"关键事实 {event['action']} 不能通过 record 导入",
                "请调用对应 Runtime probe；不得把外部 JSON 伪装为可信事实",
            )
        if event["agentic_run_id"] != manifest["agent"]["agentic_run_id"]:
            raise blocked(
                "event_run_mismatch",
                "事件 agentic_run_id 与 manifest 不一致",
                "请使用当前 task-run 的 agentic_run_id 重新生成事件",
            )
        if event["authorization_reference"] != manifest["authorization"]["reference"]:
            raise blocked(
                "event_authorization_mismatch",
                "事件没有引用当前 manifest 的明确授权",
                "请为每个 started/completed/blocked 事件记录同一授权引用",
            )
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            self._assert_open_state(paths)
            envelopes = self._read_journal(paths["events"])
            for envelope in envelopes:
                recorded = envelope["event"]
                if recorded["event_id"] != event["event_id"]:
                    continue
                if digest(recorded) != digest(event):
                    raise blocked(
                        "event_id_conflict",
                        f"event_id {event['event_id']} 已绑定不同内容",
                        "请保留原事件，使用新的 event_id 记录新事实",
                    )
                return {
                    "recorded": False,
                    "event_id": event["event_id"],
                    "sequence": envelope["sequence"],
                    "event_sha256": envelope["event_sha256"],
                    "journal_path": str(paths["events"]),
                }
            self._validate_event_transition(event, envelopes, manifest)
            previous = envelopes[-1]["event_sha256"] if envelopes else None
            envelope = event_envelope(event, len(envelopes) + 1, previous)
            append_ndjson(paths["events"], envelope)
            return {
                "recorded": True,
                "event_id": event["event_id"],
                "sequence": envelope["sequence"],
                "event_sha256": envelope["event_sha256"],
                "journal_path": str(paths["events"]),
            }

    def probe_prohibition_baseline(self, manifest_value: str) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        for permission in ("jira_read", "git_remote_read", "github_pr_read"):
            self._require_probe_permission(
                manifest, permission, "probe-prohibition-baseline"
            )
        completed = self._completed_events(manifest)
        if any(event["action"] == "prohibition_baseline" for event in completed):
            raise blocked(
                "prohibition_baseline_already_recorded",
                "本次运行已经记录不可替换的禁止动作基线",
                "请继续当前运行；需要新基线时必须使用新的 agentic_run_id",
            )
        if any(
            event["action"] == "external_action"
            and event["action_data"]["status"] in {"applied", "unknown"}
            for event in completed
        ):
            raise blocked(
                "prohibition_baseline_too_late",
                "运行已记录外部动作，不能事后补造禁止动作基线",
                "请生成 blocked 结果；新的真实运行须在任何外部写入前采集基线",
            )

        issue_manifest = manifest["issue"]
        jira_manifest = manifest["jira"]
        context = load_jira_context(self.workspace, self.install_root)
        email, token = context.require_credentials()
        if context.connection.base_url.rstrip("/") != jira_manifest["base_url"].rstrip(
            "/"
        ):
            raise blocked(
                "jira_probe_binding_mismatch",
                "禁止动作基线的 Jira 站点与 manifest 不一致",
                "请停止执行并重新确认当前工作空间身份",
            )
        jira_client = JiraClient(
            context.profile,
            UrllibJiraTransport(context.connection, email, token),
        )
        live_identity = jira_client.current_user_details()
        validate_workspace_jira_binding(
            self.workspace,
            context.connection,
            account_id=live_identity["account_id"],
        )
        live_issue = jira_client.get_issue(issue_manifest["key"])
        if (
            live_identity["account_id"] != jira_manifest["account_id"]
            or live_issue.issue_id != issue_manifest["id"]
            or live_issue.project_key != issue_manifest["project_key"]
            or live_issue.assignee != jira_manifest["assignee_account_id"]
        ):
            raise blocked(
                "prohibition_baseline_jira_mismatch",
                "禁止动作基线的 Jira 账户、任务或负责人不匹配",
                "请停止执行并核对 manifest 与工作空间身份",
            )
        if (
            self._jira_issue_content_digest(live_issue)
            != manifest["task_binding"]["issue_content_sha256"]
        ):
            raise blocked(
                "jira_issue_content_changed",
                "禁止动作基线发现 Jira 任务内容已偏离用户确认摘要",
                "请停止执行并使用新的 agentic_run_id 重新确认任务与计划",
            )
        jira_category = self._jira_status_category(live_issue)
        if jira_category.casefold() == "done":
            raise blocked(
                "prohibition_baseline_jira_done",
                "基线采集时 Jira 已处于 Done 分类，不能开始本次任务到 PR 运行",
                "请由研发工程师核对任务状态并使用新的运行",
            )

        repository = manifest["repository"]
        root = Path(repository["root"])
        remote = repository["remote_name"]
        top = self._git(root, "rev-parse", "--show-toplevel").strip()
        if Path(top).resolve() != root:
            raise blocked(
                "git_probe_repository_mismatch",
                "禁止动作基线的 Git 顶层目录与 manifest 不一致",
                "请停止执行并核对业务源码仓库绑定",
            )
        self._validate_git_remote_identity(root, repository)
        baseline_branch = self._git(
            root, "symbolic-ref", "--quiet", "--short", "HEAD"
        ).strip()
        if baseline_branch != repository["task_branch"]:
            raise blocked(
                "prohibition_baseline_branch_mismatch",
                "写入前基线必须在 manifest 任务分支采集",
                "请切换到已授权任务分支，保持干净工作树后使用新的 agentic_run_id",
            )
        baseline_status = self._git(
            root, "status", "--porcelain=v1", "--untracked-files=all"
        )
        if baseline_status:
            raise blocked(
                "prohibition_baseline_worktree_dirty",
                "写入前基线的工作树或索引不干净，无法证明本轮变更起点",
                "请清理或保存既有变更，再使用新的 agentic_run_id 采集基线",
            )
        local_head_sha = self._git(root, "rev-parse", "HEAD").strip()
        self._git(root, "cat-file", "-e", f"{local_head_sha}^{{commit}}")
        tag_refs = self._parse_remote_refs(
            self._git(root, "ls-remote", "--tags", remote), "refs/tags/"
        )
        task_branch_refs = self._remote_heads(
            self._git(
                root,
                "ls-remote",
                "--heads",
                remote,
                f"refs/heads/{repository['task_branch']}",
            )
        )
        task_branch_remote_sha = task_branch_refs.get(repository["task_branch"])
        protected_refs = self._remote_heads(
            self._git(
                root,
                "ls-remote",
                "--heads",
                remote,
                *[
                    f"refs/heads/{branch}"
                    for branch in repository["protected_branches"]
                ],
            )
        )
        protected_heads = [
            {"branch": branch, "sha": protected_refs.get(branch)}
            for branch in sorted(repository["protected_branches"])
        ]
        if any(item["sha"] is None for item in protected_heads):
            raise blocked(
                "prohibition_baseline_protected_ref_missing",
                "manifest 中至少一个保护分支在远端不存在",
                "请核对保护分支清单，不得省略或猜测远端基线",
            )
        target_head_sha = protected_refs[repository["target_branch"]]
        self._validate_baseline_start(
            local_head_sha,
            task_branch_remote_sha,
            target_head_sha,
        )

        releases = self._github_json_array(
            root,
            [
                "gh",
                "release",
                "list",
                "--repo",
                repository["slug"],
                "--limit",
                "1000",
                "--json",
                "tagName,publishedAt",
            ],
            "禁止动作基线无法读取 GitHub releases",
        )
        release_records = sorted(
            [
                {
                    "tag_name": str(item.get("tagName", "")).strip(),
                    "published_at": (
                        str(item["publishedAt"]).strip()
                        if item.get("publishedAt")
                        else None
                    ),
                }
                for item in releases
                if str(item.get("tagName", "")).strip()
            ],
            key=lambda item: (item["tag_name"], item["published_at"] or ""),
        )
        task_prs = self._github_json_array(
            root,
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repository["slug"],
                "--head",
                repository["task_branch"],
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "number,url,state,isDraft,mergedAt,headRefName,headRefOid,baseRefName",
            ],
            "禁止动作基线无法读取任务分支 open PR",
        )
        if len(task_prs) > 1:
            raise blocked(
                "prohibition_baseline_pr_ambiguous",
                "任务分支在基线采集时存在多个 open PR，无法唯一归因",
                "请先由研发工程师处理重复 PR，再使用新的 agentic_run_id",
            )
        task_open_pr = None
        if task_prs:
            item = task_prs[0]
            number = item.get("number")
            state = str(item.get("state", "")).upper()
            url = str(item.get("url", ""))
            if (
                isinstance(number, bool)
                or not isinstance(number, int)
                or number < 1
                or state != "OPEN"
                or bool(item.get("isDraft"))
                or item.get("mergedAt") is not None
                or item.get("headRefName") != repository["task_branch"]
                or item.get("baseRefName") != repository["target_branch"]
                or not str(item.get("headRefOid", ""))
                or not url
            ):
                raise blocked(
                    "prohibition_baseline_pr_invalid",
                    "任务分支既有 open PR 基线字段不完整或与 manifest 不一致",
                    "请修复 PR 事实后使用新的 agentic_run_id",
                )
            task_open_pr = {
                "number": number,
                "url": url,
                "head_sha": str(item["headRefOid"]),
                "base_branch": str(item["baseRefName"]),
            }
        baseline_target = "prohibition-baseline"
        return self._append_runtime_readback(
            manifest,
            [
                ("jira_read", f"jira:{issue_manifest['key']}:{baseline_target}"),
                (
                    "git_remote_read",
                    f"git:{repository['slug']}:{baseline_target}",
                ),
                (
                    "github_pr_read",
                    f"github:{repository['slug']}:{baseline_target}",
                ),
            ],
            "prohibition_baseline",
            {
                "issue_key": issue_manifest["key"],
                "repository_slug": repository["slug"],
                "remote_name": remote,
                "jira_status": live_issue.status,
                "jira_status_category": jira_category,
                "tag_refs": tag_refs,
                "release_records": release_records,
                "protected_heads": protected_heads,
                "local_head_sha": local_head_sha,
                "task_branch_remote_sha": task_branch_remote_sha,
                "task_open_pr": task_open_pr,
                "observed_at": self._now(),
                "reference": (
                    f"runtime-prohibition-baseline:{issue_manifest['key']}:"
                    f"{manifest['agent']['agentic_run_id']}"
                ),
            },
            "Runtime 在任何写入前采集 Jira/Git/GitHub 禁止动作基线",
        )

    def probe_jira(self, manifest_value: str) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        self._require_probe_permission(manifest, "jira_read", "probe-jira")
        context = load_jira_context(self.workspace, self.install_root)
        email, token = context.require_credentials()
        expected = manifest["jira"]
        if context.connection.base_url.rstrip("/") != expected["base_url"].rstrip("/"):
            raise blocked(
                "jira_probe_binding_mismatch",
                "当前工作空间 Jira base_url 与 manifest 不一致",
                "请停止执行并重新确认绑定当前工作空间身份的 manifest",
            )
        if context.profile.status_mapping != expected["status_mapping"]:
            raise blocked(
                "jira_probe_profile_changed",
                "Project Profile 状态映射与 manifest 已确认版本不一致",
                "请重新生成并确认 manifest，不得沿用旧状态映射",
            )
        client = JiraClient(
            context.profile,
            UrllibJiraTransport(context.connection, email, token),
        )
        identity = client.current_user_details()
        validate_workspace_jira_binding(
            self.workspace,
            context.connection,
            account_id=identity["account_id"],
        )
        issue = client.get_issue(manifest["issue"]["key"])
        account_id = identity["account_id"]
        if account_id != expected["account_id"]:
            raise blocked(
                "jira_probe_account_mismatch",
                "Jira 当前登录 accountId 与 manifest 不一致",
                "请重新授权当前研发员工作空间，不能跨工作空间借用账户",
            )
        if issue.key != manifest["issue"]["key"] or issue.issue_id != manifest["issue"]["id"]:
            raise blocked(
                "jira_probe_issue_mismatch",
                "Jira 实时回读 issue 身份与 manifest 不一致",
                "请停止执行并核对 Jira 任务",
            )
        if issue.project_key != manifest["issue"]["project_key"]:
            raise blocked(
                "jira_probe_project_mismatch",
                "Jira 实时回读项目与 manifest 不一致",
                "请停止执行并核对 Project Profile",
            )
        issue_content_sha256 = self._jira_issue_content_digest(issue)
        if issue_content_sha256 != manifest["task_binding"]["issue_content_sha256"]:
            raise blocked(
                "jira_issue_content_changed",
                "Jira 任务摘要、描述、类型、状态或负责人已偏离用户确认内容",
                "请停止执行，重新审阅任务内容并使用新的 agentic_run_id 确认 manifest",
            )
        if issue.assignee != expected["assignee_account_id"] or issue.assignee != account_id:
            raise blocked(
                "jira_probe_assignee_mismatch",
                "Jira 当前 assignee 不是 manifest 绑定的研发员账户",
                "请由研发工程师处理 Jira 负责人后重新 probe",
            )
        status_payload = issue.fields.get("status", {})
        category_payload = status_payload.get("statusCategory", {}) if isinstance(status_payload, dict) else {}
        category = str(
            category_payload.get("key") or category_payload.get("name") or ""
        ).strip()
        if category.casefold() == "done" or category not in expected["allowed_status_categories"]:
            raise blocked(
                "jira_probe_status_forbidden",
                f"Jira 状态分类 {category or '<empty>'} 不允许继续 task→PR",
                "请停止自动化；Done 或未授权状态必须由研发工程师核对",
            )
        mapped_status = expected["status_mapping"].get(issue.status)
        if not mapped_status or mapped_status == "completed":
            raise blocked(
                "jira_probe_status_unmapped",
                f"Jira 状态 {issue.status or '<empty>'} 未安全映射或已完成",
                "请先修复 Project Profile 状态映射并重新确认 manifest",
            )
        takeover_marker_prefix = (
            f"[agentic-ops-takeover:{issue.key}:"
            f"{manifest['agent']['agentic_run_id']}:"
        )
        takeover_comment = next(
            (
                comment
                for comment in reversed(client.comments(issue.key))
                if takeover_marker_prefix in comment.body
                and comment.author == account_id
            ),
            None,
        )
        takeover_comment_id = (
            takeover_comment.comment_id if takeover_comment is not None else None
        )
        formal_takeover_verified = takeover_comment_id is not None
        readback = self._append_runtime_readback(
            manifest,
            [("jira_read", f"jira:{issue.key}")],
            "jira_readback",
            {
                "provider": "jira",
                "reference": f"jira:{issue.key}:live:{int(time.time())}",
                "url": f"{expected['base_url'].rstrip('/')}/browse/{issue.key}",
                "issue_key": issue.key,
                "issue_id": issue.issue_id,
                "project_key": issue.project_key,
                "status": issue.status,
                "assignee": issue.assignee,
                "account_id": account_id,
                "assignee_account_id": issue.assignee,
                "status_category": category,
                "mapped_status": mapped_status,
                "takeover_comment_id": takeover_comment_id,
                "formal_takeover_verified": formal_takeover_verified,
                "issue_content_sha256": issue_content_sha256,
                "approved_plan_sha256": manifest["task_binding"][
                    "approved_plan_sha256"
                ],
                "observed_at": self._now(),
            },
            "Runtime 实时核对 Jira 账户、任务、负责人和状态",
        )
        if formal_takeover_verified:
            return readback
        gap = self._append_runtime_fact(
            manifest,
            "quality_finding",
            {
                "category": "automation_gap",
                "detail": "当前 Jira 任务缺少与本次运行绑定的受管接管评论",
                "evidence_reference": str(readback["event_id"]),
                "impact": "本次只证明当前 Jira 账户等于 assignee，不声称正式接管留痕已完成",
                "root_cause_hypothesis": "接管评论未写入、未回读，或评论不属于当前 agentic_run_id",
                "reproduction": "在当前 Jira 任务缺少受管接管评论时执行 probe-jira",
                "sanitized_example": "formal_takeover_verified=false; takeover_comment_id=null",
                "improvement_candidate": "通过 ao-work task takeover 写入并回读当前运行的结构化接管评论",
                "suggested_asset": "python_runtime",
                "benefit": "无需项目自定义字段即可核对正式接管审计轨迹",
                "risk": "评论缺失时可能无法区分本地准备与正式接管",
                "frequency": "每次未完成接管评论闭环的真实 task→PR 测试",
            },
            "Runtime 记录接管评论缺失能力缺口",
        )
        return {"readback": readback, "automation_gap": gap}

    def probe_jira_write(
        self,
        manifest_value: str,
        plan_file: str,
        confirm_plan_id: str,
    ) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        self._require_probe_permission(manifest, "jira_read", "probe-jira-write")
        issue_key = str(manifest["issue"]["key"])
        agentic_run_id = str(manifest["agent"]["agentic_run_id"])
        plan_path, plan = read_bound_jira_plan(
            self.workspace.root,
            plan_file,
            issue_key=issue_key,
            agentic_run_id=agentic_run_id,
        )
        if plan.plan_id != confirm_plan_id:
            raise blocked(
                "jira_write_plan_mismatch",
                "probe-jira-write 的 confirm-plan-id 与受管计划不一致",
                "请使用 plan 输出的 plan_id 原样重试，不得人工改写计划",
            )
        if plan.operation not in {"jira_comment", "jira_worklog"}:
            raise blocked(
                "jira_write_probe_operation_invalid",
                f"task-run 专用写后回读不支持 {plan.operation}",
                "请只对 Jira 评论或 Worklog 的既有受管计划执行该 probe",
            )
        if plan.operation not in manifest["permitted_external_actions"]:
            raise blocked(
                "external_action_not_authorized",
                f"外部动作 {plan.operation} 不在 manifest 授权范围内",
                "请停止执行；范围变化需要重新确认 manifest",
            )
        attempt_path, attempt = read_bound_jira_attempt(plan_path, plan)
        if attempt is not None and (
            attempt.authorization_reference != manifest["authorization"]["reference"]
        ):
            raise blocked(
                "jira_write_attempt_authorization_mismatch",
                "Jira create 尝试没有绑定当前 manifest 授权引用",
                "请停止执行；不得复用其它授权或运行的写入尝试",
            )

        context = load_jira_context(self.workspace, self.install_root)
        email, token = context.require_credentials()
        expected = manifest["jira"]
        if context.connection.base_url.rstrip("/") != expected["base_url"].rstrip("/"):
            raise blocked(
                "jira_probe_binding_mismatch",
                "Jira 写后回读站点与 manifest 不一致",
                "请停止执行并重新确认绑定当前工作空间身份的 manifest",
            )
        client = JiraClient(
            context.profile,
            UrllibJiraTransport(context.connection, email, token),
        )
        account_id = client.current_user()
        validate_workspace_jira_binding(
            self.workspace,
            context.connection,
            account_id=account_id,
        )
        if account_id != expected["account_id"]:
            raise blocked(
                "jira_probe_account_mismatch",
                "Jira 写后回读账户与 manifest 不一致",
                "请停止执行并恢复 manifest 绑定的 Jira 研发员账户",
            )
        service = JiraService(context.profile, client)
        service.validate_no_credentials(plan, email, token)
        if plan.operation == "jira_comment":
            result = service.readback_comment(plan, attempt=attempt)
            worklog = {
                "title": None,
                "details_sha256": None,
                "time_spent_seconds": None,
                "started": None,
                "excludes_waiting": None,
                "included_work": None,
                "excluded_waiting_categories": None,
            }
        else:
            result = service.readback_worklog(plan, attempt=attempt)
            worklog = {
                "title": result["title"],
                "details_sha256": result["details_sha256"],
                "time_spent_seconds": result["time_spent_seconds"],
                "started": datetime.fromisoformat(
                    str(result["started"]).replace("Z", "+00:00")
                ).isoformat(timespec="milliseconds"),
                "excludes_waiting": result["excludes_waiting"],
                "included_work": result["included_work"],
                "excluded_waiting_categories": result[
                    "excluded_waiting_categories"
                ],
            }
        relative_plan = plan_path.relative_to(self.workspace.root).as_posix()
        external_id = str(result["external_id"])
        target = f"jira:{issue_key}:{plan.operation}:{external_id}"
        created = bool(result["created"])
        return self._append_runtime_readback(
            manifest,
            [(plan.operation, target)] if created else [],
            "jira_write_readback",
            {
                "provider": "jira",
                "issue_key": issue_key,
                "agentic_run_id": agentic_run_id,
                "operation": plan.operation,
                "plan_file": relative_plan,
                "attempt_file": (
                    attempt_path.relative_to(self.workspace.root).as_posix()
                    if attempt_path is not None
                    else None
                ),
                "plan_id": plan.plan_id,
                "idempotency_key": plan.idempotency_key,
                "external_id": external_id,
                "created": created,
                "write_precondition": result["write_precondition"],
                "write_attempt_id": result["write_attempt_id"],
                "write_attempt_started_at": result[
                    "write_attempt_started_at"
                ],
                "content_sha256": plan.content_sha256,
                "body_sha256": str(result["body_sha256"]),
                **worklog,
                "observed_at": self._now(),
                "reference": f"jira:{issue_key}:{plan.operation}:{external_id}:readback",
            },
            f"Runtime 实时回读并绑定 Jira 写入：{plan.operation}",
        )

    def execute_git_commit(
        self,
        manifest_value: str,
        *,
        message: str,
        authorization_reference: str,
    ) -> dict[str, Any]:
        """受控提交：在 manifest 授权范围与执行身份约束内创建任务提交。

        - 前置：manifest open + prohibition_baseline（写入前可信基线）。
        - 校验：工作树在任务分支、变更路径在授权范围内、执行身份完整。
        - 执行：git add（授权范围）+ git commit（per-worktree 身份）。
        - 回读：新 HEAD 是基线后代、工作树干净、author/committer 匹配 manifest。
        - 记录：external_action(git_commit) + remote_branch_readback 事件。
        """
        manifest = self._load_open_manifest(manifest_value)
        self._require_probe_permission(manifest, "git_commit", "execute-git-commit")
        if authorization_reference != manifest["authorization"]["reference"]:
            raise blocked(
                "authorization_reference_mismatch",
                "git_commit 授权引用与 manifest 明确授权不一致",
                "请使用与 task-run open 相同的授权引用",
            )
        completed = self._completed_events(manifest)
        baseline_event = self._latest_action(completed, "prohibition_baseline")
        if baseline_event is None or baseline_event["evidence_origin"] != "runtime_probe":
            raise blocked(
                "git_commit_baseline_missing",
                "execute-git-commit 前缺少写入前 Runtime 可信基线",
                "请先执行 task-run probe-prohibition-baseline",
            )
        baseline = baseline_event["action_data"]
        repository = manifest["repository"]
        root = Path(repository["root"])
        remote = repository["remote_name"]

        top = self._git(root, "rev-parse", "--show-toplevel").strip()
        if Path(top).resolve() != root:
            raise blocked(
                "git_commit_repository_mismatch",
                "Git 顶层目录与 manifest repository.root 不一致",
                "请停止执行并核对业务源码仓库绑定",
            )
        branch = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        if branch != repository["task_branch"]:
            raise blocked(
                "git_commit_branch_mismatch",
                f"当前分支 {branch or '<detached>'} 不是 manifest 任务分支",
                "请切换到已授权任务分支后重试",
            )
        self._reject_git_url_rewrites(root)

        status = self._git(root, "status", "--porcelain=v1", "--untracked-files=all")
        if not status:
            raise blocked(
                "git_commit_no_changes",
                "任务分支工作树没有待提交变更",
                "请先完成授权范围内实现后再提交",
            )
        changed_paths = _porcelain_paths(status)
        outside = [
            path
            for path in changed_paths
            if not any(
                fnmatch.fnmatchcase(path, pattern)
                for pattern in manifest["scope"]["included"]
            )
            or any(
                fnmatch.fnmatchcase(path, pattern)
                for pattern in manifest["scope"]["excluded"]
            )
        ]
        if outside:
            raise blocked(
                "git_commit_scope_violation",
                f"待提交路径越出 manifest 授权范围：{', '.join(outside[:10])}",
                "请停止执行；范围变化必须重新确认 manifest",
            )
        identity_names = (
            "git_author_name",
            "git_author_email",
            "git_committer_name",
            "git_committer_email",
        )
        expected_identity = {
            field: manifest["execution_identity"][field] for field in identity_names
        }

        local_head_before = self._git(root, "rev-parse", "HEAD").strip()

        add_result = self._git_result(
            root, "add", "--", *manifest["scope"]["included"]
        )
        if add_result.returncode != 0:
            raise blocked(
                "git_commit_stage_failed",
                f"git add 失败：{_stderr_tail(add_result.stderr)}",
                "请检查授权范围内路径是否可读后重试",
            )
        author_spec = (
            f"{expected_identity['git_author_name']} "
            f"<{expected_identity['git_author_email']}>"
        )
        commit_result = self._git_result(
            root,
            "-c",
            f"user.name={expected_identity['git_committer_name']}",
            "-c",
            f"user.email={expected_identity['git_committer_email']}",
            "commit",
            "--author",
            author_spec,
            "-m",
            message,
        )
        if commit_result.returncode != 0:
            self._git_result(root, "reset", "--mixed", "HEAD")
            raise blocked(
                "git_commit_failed",
                f"git commit 失败：{_stderr_tail(commit_result.stderr)}",
                "已回滚暂存区；请修复提交信息或状态后重试",
            )
        local_head_after = self._git(root, "rev-parse", "HEAD").strip()
        if local_head_after == local_head_before:
            raise blocked(
                "git_commit_readback_mismatch",
                "提交后 HEAD 未变化，无法证明提交创建成功",
                "请检查提交钩子与仓库状态后重试",
            )
        self._git(root, "cat-file", "-e", f"{local_head_after}^{{commit}}")
        ancestor = self._git_result(
            root, "merge-base", "--is-ancestor", local_head_before, local_head_after
        )
        if ancestor.returncode != 0:
            raise blocked(
                "git_commit_baseline_not_ancestor",
                "提交前 HEAD 不是提交后 HEAD 的祖先，禁止历史改写",
                "请停止执行并检查是否发生 force 操作",
            )
        commit_identity = self._git(
            root,
            "log",
            "--format=%H%x00%an%x00%ae%x00%cn%x00%ce",
            "-1",
        ).rstrip("\n").split("\0")
        if len(commit_identity) != 5:
            raise blocked(
                "git_commit_identity_invalid",
                "新提交缺少完整 author/committer 身份",
                "请检查提交配置后重试",
            )
        actual_identity = dict(
            zip(identity_names, commit_identity[1:], strict=True)
        )
        if actual_identity != expected_identity:
            raise blocked(
                "git_commit_identity_mismatch",
                "新提交 author/committer 与 manifest 显式身份不一致",
                "请停止执行并按已确认身份重建提交",
            )
        status_after = self._git(
            root, "status", "--porcelain=v1", "--untracked-files=no"
        )
        if status_after:
            raise blocked(
                "git_commit_worktree_dirty_after",
                "提交后仍有已跟踪变更未提交",
                "请核对暂存范围；git commit 应只包含授权范围内变更",
            )
        commit_sha = local_head_after
        target = f"git:{repository['slug']}:{branch}@{commit_sha}"
        origin_url = self._git(root, "config", "--get-all", f"remote.{remote}.url").splitlines()[0]
        commit_identity_values = {
            "git_author_name": actual_identity["git_author_name"],
            "git_author_email": actual_identity["git_author_email"],
            "git_committer_name": actual_identity["git_committer_name"],
            "git_committer_email": actual_identity["git_committer_email"],
        }
        return self._append_runtime_readback(
            manifest,
            [("git_commit", target)],
            "remote_branch_readback",
            {
                "provider": "git",
                "reference": target,
                "url": f"https://github.com/{repository['slug']}/commit/{commit_sha}",
                "repository_slug": repository["slug"],
                "remote_name": remote,
                "branch": branch,
                "sha": commit_sha,
                "status": "exists",
                "protected": branch in repository["protected_branches"],
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "origin_url": origin_url,
                "base_sha": local_head_before,
                "head_sha": commit_sha,
                "baseline_event_id": baseline_event["event_id"],
                "baseline_local_head_sha": baseline["local_head_sha"],
                "baseline_remote_sha": baseline.get("task_branch_remote_sha"),
                "baseline_local_is_ancestor": True,
                "baseline_remote_is_ancestor": None,
                "attributed_actions": ["git_commit"],
                "verification_event_ids": [],
                "changed_paths": changed_paths,
                "worktree_clean": True,
                **commit_identity_values,
                "commit_count": 1,
                "commit_identity_sha256": digest(actual_identity),
                "approved_plan_sha256": manifest["task_binding"]["approved_plan_sha256"],
            },
            f"Runtime 完成受控提交并回读：{commit_sha[:12]}",
        )

    def execute_git_push_task_branch(
        self,
        manifest_value: str,
        *,
        authorization_reference: str,
    ) -> dict[str, Any]:
        """受控推送：在任务级授权内推送任务分支并回读远端 SHA。

        - 前置：manifest open + prohibition_baseline + execute-git-commit 已成功。
        - 校验：任务分支不是保护分支、工作树干净、远端无同名分支或可快进。
        - 执行：git push origin <task_branch>。
        - 回读：ls-remote 远端 SHA == 本地 HEAD，记录 remote_branch_readback。
        """
        manifest = self._load_open_manifest(manifest_value)
        self._require_probe_permission(
            manifest, "git_push_task_branch", "execute-git-push-task-branch"
        )
        if authorization_reference != manifest["authorization"]["reference"]:
            raise blocked(
                "authorization_reference_mismatch",
                "git_push 授权引用与 manifest 明确授权不一致",
                "请使用与 task-run open 相同的授权引用",
            )
        completed = self._completed_events(manifest)
        baseline_event = self._latest_action(completed, "prohibition_baseline")
        if baseline_event is None or baseline_event["evidence_origin"] != "runtime_probe":
            raise blocked(
                "git_push_baseline_missing",
                "execute-git-push 前缺少写入前 Runtime 可信基线",
                "请先执行 task-run probe-prohibition-baseline",
            )
        commit_readback = self._latest_action(completed, "remote_branch_readback")
        if commit_readback is None:
            raise blocked(
                "git_push_commit_missing",
                "execute-git-push 前缺少受控提交回读",
                "请先执行 task-run execute-git-commit",
            )
        baseline = baseline_event["action_data"]
        repository = manifest["repository"]
        root = Path(repository["root"])
        remote = repository["remote_name"]
        task_branch = repository["task_branch"]

        if task_branch in repository["protected_branches"]:
            raise blocked(
                "push_protected_branch_forbidden",
                f"任务分支 {task_branch} 是保护分支，禁止推送",
                "任务分支必须是独立工作分支，不得直接推送 main/develop",
            )
        top = self._git(root, "rev-parse", "--show-toplevel").strip()
        if Path(top).resolve() != root:
            raise blocked(
                "git_push_repository_mismatch",
                "Git 顶层目录与 manifest repository.root 不一致",
                "请停止执行并核对业务源码仓库绑定",
            )
        branch = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        if branch != task_branch:
            raise blocked(
                "git_push_branch_mismatch",
                f"当前分支 {branch or '<detached>'} 不是 manifest 任务分支",
                "请切换到已授权任务分支后重试",
            )
        self._reject_git_url_rewrites(root)
        local_head = self._git(root, "rev-parse", "HEAD").strip()
        status = self._git(root, "status", "--porcelain=v1", "--untracked-files=no")
        if status:
            raise blocked(
                "git_push_worktree_dirty",
                "推送前任务分支仍有未提交变更",
                "请先完成受控提交再推送",
            )
        refs = self._git(
            root,
            "ls-remote",
            "--heads",
            remote,
            f"refs/heads/{task_branch}",
        )
        remote_sha = ""
        for line in refs.splitlines():
            if "\t" in line:
                remote_sha = line.split("\t", 1)[0]
                break
        if remote_sha:
            fast_forward = self._git_result(
                root, "merge-base", "--is-ancestor", remote_sha, local_head
            )
            if fast_forward.returncode != 0:
                raise blocked(
                    "push_non_fast_forward_blocked",
                    "远端任务分支存在本地不包含的提交，非快进推送被阻断",
                    "请先 fetch 远端并处理冲突，禁止 force push",
                )
        push_result = self._git_result(
            root, "push", remote, f"refs/heads/{task_branch}"
        )
        if push_result.returncode != 0:
            raise blocked(
                "push_failed",
                f"git push 失败：{_stderr_tail(push_result.stderr)}",
                "请检查远端权限与网络后重试",
            )
        refs_after = self._git(
            root,
            "ls-remote",
            "--heads",
            remote,
            f"refs/heads/{task_branch}",
        )
        remote_sha_after = ""
        for line in refs_after.splitlines():
            if "\t" in line:
                remote_sha_after = line.split("\t", 1)[0]
                break
        if remote_sha_after != local_head:
            raise blocked(
                "push_readback_mismatch",
                "推送后远端任务分支 SHA 与本地 HEAD 不一致",
                "请停止执行并核对推送结果，不得用本地 SHA 代替远端事实",
            )
        target = f"git:{repository['slug']}:{task_branch}@{remote_sha_after}"
        return self._append_runtime_readback(
            manifest,
            [("git_push_task_branch", target)],
            "remote_branch_readback",
            {
                "provider": "git",
                "reference": target,
                "url": f"https://github.com/{repository['slug']}/tree/{task_branch}",
                "repository_slug": repository["slug"],
                "remote_name": remote,
                "branch": task_branch,
                "sha": remote_sha_after,
                "status": "exists",
                "protected": False,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "origin_url": self._git(
                    root, "config", "--get-all", f"remote.{remote}.url"
                ).splitlines()[0],
                "base_sha": commit_readback["action_data"]["base_sha"],
                "head_sha": remote_sha_after,
                "baseline_event_id": baseline_event["event_id"],
                "baseline_local_head_sha": baseline["local_head_sha"],
                "baseline_remote_sha": baseline.get("task_branch_remote_sha"),
                "baseline_local_is_ancestor": True,
                "baseline_remote_is_ancestor": None,
                "attributed_actions": ["git_push_task_branch"],
                "verification_event_ids": [],
                "changed_paths": commit_readback["action_data"]["changed_paths"],
                "worktree_clean": True,
                "git_author_name": commit_readback["action_data"]["git_author_name"],
                "git_author_email": commit_readback["action_data"]["git_author_email"],
                "git_committer_name": commit_readback["action_data"]["git_committer_name"],
                "git_committer_email": commit_readback["action_data"]["git_committer_email"],
                "commit_count": 1,
                "commit_identity_sha256": commit_readback["action_data"][
                    "commit_identity_sha256"
                ],
                "approved_plan_sha256": manifest["task_binding"][
                    "approved_plan_sha256"
                ],
            },
            f"Runtime 完成受控推送并回读：{remote_sha_after[:12]}",
        )

    def execute_github_pr_create(
        self,
        manifest_value: str,
        *,
        title: str,
        body: str,
        authorization_reference: str,
    ) -> dict[str, Any]:
        """受控 PR 创建：在任务级授权内创建 GitHub PR 并回读事实。

        - 前置：manifest open + baseline + git push 回读。
        - 校验：GitHub actor 身份匹配 manifest、无已存在 PR、禁止 merge。
        - 执行：gh pr create（head=任务分支、base=target_branch）。
        - 回读：gh pr view 校验 number/head/base/url，记录 pr_readback。
        """
        manifest = self._load_open_manifest(manifest_value)
        self._require_probe_permission(
            manifest, "github_pr_create_or_update", "execute-github-pr-create"
        )
        if authorization_reference != manifest["authorization"]["reference"]:
            raise blocked(
                "authorization_reference_mismatch",
                "github_pr_create 授权引用与 manifest 明确授权不一致",
                "请使用与 task-run open 相同的授权引用",
            )
        completed = self._completed_events(manifest)
        baseline_event = self._latest_action(completed, "prohibition_baseline")
        if baseline_event is None or baseline_event["evidence_origin"] != "runtime_probe":
            raise blocked(
                "pr_create_baseline_missing",
                "execute-github-pr-create 前缺少写入前 Runtime 可信基线",
                "请先执行 task-run probe-prohibition-baseline",
            )
        if baseline_event["action_data"].get("task_open_pr") is not None:
            raise blocked(
                "pr_already_exists",
                "写入前已存在 open PR，不允许重复创建",
                "请使用现有 PR 或人工处理；当前不支持自动更新 PR",
            )
        git_readback = self._latest_action(completed, "remote_branch_readback")
        if git_readback is None:
            raise blocked(
                "pr_create_git_readback_missing",
                "execute-github-pr-create 前缺少 Git push 回读",
                "请先执行 task-run execute-git-push-task-branch",
            )
        repository = manifest["repository"]
        root = Path(repository["root"])
        actor_result = self._run_command(
            ["gh", "api", "user", "--jq", ".login"],
            cwd=root,
            timeout=60,
        )
        github_actor = actor_result.stdout.strip()
        if (
            actor_result.returncode != 0
            or github_actor != manifest["execution_identity"]["github_actor_login"]
        ):
            raise blocked(
                "github_actor_identity_mismatch",
                "当前 GitHub 登录身份与 manifest 显式 actor 不一致",
                "请切换到已确认 GitHub 账户后重试，不得借用其它登录会话",
            )
        create_result = self._run_command(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                repository["slug"],
                "--head",
                repository["task_branch"],
                "--base",
                repository["target_branch"],
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=root,
            timeout=120,
        )
        if create_result.returncode != 0:
            raise blocked(
                "pr_create_failed",
                f"gh pr create 失败：{_stderr_tail(create_result.stderr)}",
                "请检查 GitHub 授权、分支与目标分支后重试",
            )
        view_result = self._run_command(
            [
                "gh",
                "pr",
                "view",
                repository["task_branch"],
                "--repo",
                repository["slug"],
                "--json",
                "number,url,state,isDraft,mergedAt,headRefName,headRefOid,baseRefName,reviewDecision,statusCheckRollup",
            ],
            cwd=root,
            timeout=60,
        )
        if view_result.returncode != 0:
            raise blocked(
                "pr_readback_failed",
                "gh 无法回读刚创建的 PR",
                "请人工核对 PR 创建结果；Runtime 不假设创建成功",
            )
        try:
            payload = json.loads(view_result.stdout)
        except json.JSONDecodeError as error:
            raise blocked(
                "pr_readback_invalid",
                "gh pr view 响应不是有效 JSON",
                "请修复 gh 工具后重试",
            ) from error
        number = payload.get("number")
        url = payload.get("url")
        head_ref = payload.get("headRefName")
        base_ref = payload.get("baseRefName")
        head_oid = payload.get("headRefOid")
        if (
            not isinstance(number, int)
            or not isinstance(url, str)
            or head_ref != repository["task_branch"]
            or base_ref != repository["target_branch"]
            or not isinstance(head_oid, str)
        ):
            raise blocked(
                "pr_readback_mismatch",
                "回读 PR 的 head/base/编号与 manifest 不一致",
                "请人工核对 PR 创建结果；不得假设创建成功",
            )
        merged = bool(payload.get("mergedAt"))
        if merged:
            raise blocked(
                "pr_auto_merge_forbidden",
                "刚创建的 PR 已被合并，Runtime 禁止自动合并 PR",
                "请停止执行并人工调查；PR 生命周期必须人工控制",
            )
        review_decision = str(payload.get("reviewDecision") or "").upper()
        review_state = {
            "APPROVED": "approved",
            "CHANGES_REQUESTED": "changes_requested",
        }.get(review_decision, "awaiting_review")
        ci_status = self._ci_status(payload.get("statusCheckRollup") or [])
        readback_event_id = str(git_readback["event_id"])
        target = str(url)
        return self._append_runtime_readback(
            manifest,
            [("github_pr_create_or_update", target)],
            "pr_readback",
            {
                "provider": "github",
                "reference": target,
                "url": url,
                "repository_slug": repository["slug"],
                "number": number,
                "status": "open",
                "merged": merged,
                "head_branch": head_ref,
                "head_sha": head_oid,
                "base_branch": base_ref,
                "review_state": review_state,
                "ci_status": ci_status,
                "draft": bool(payload.get("isDraft")),
                "github_actor_login": github_actor,
                "approved_plan_sha256": manifest["task_binding"][
                    "approved_plan_sha256"
                ],
                "baseline_event_id": baseline_event["event_id"],
                "git_readback_event_id": readback_event_id,
                "attributed_actions": ["github_pr_create_or_update"],
                "creation_proof": True,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            },
            f"Runtime 完成受控 PR 创建并回读：#{number}",
        )

    def probe_git(
        self, manifest_value: str, bind_actions: Iterable[str] = ()
    ) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        self._require_probe_permission(manifest, "git_remote_read", "probe-git")
        selected_actions = self._validate_probe_bind_actions(
            manifest,
            bind_actions,
            {"git_commit", "git_push_task_branch"},
            "probe-git",
        )
        repository = manifest["repository"]
        completed = self._completed_events(manifest)
        baseline_event = self._latest_action(completed, "prohibition_baseline")
        if baseline_event is None or baseline_event["evidence_origin"] != "runtime_probe":
            self._incomplete("probe-git 前缺少写入前 Runtime 可信基线")
        baseline = baseline_event["action_data"]
        root = Path(repository["root"])
        remote = repository["remote_name"]

        top = self._git(root, "rev-parse", "--show-toplevel").strip()
        if Path(top).resolve() != root:
            raise blocked(
                "git_probe_repository_mismatch",
                "Git 顶层目录与 manifest repository.root 不一致",
                "请停止执行并核对业务源码仓库绑定",
            )
        branch = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD").strip()
        if branch != repository["task_branch"]:
            raise blocked(
                "git_probe_branch_mismatch",
                f"当前分支 {branch or '<detached>'} 不是 manifest 任务分支",
                "请切换到已授权任务分支后重新 probe",
            )
        self._reject_git_url_rewrites(root)
        raw_fetch_urls = self._git(
            root, "config", "--get-all", f"remote.{remote}.url"
        ).splitlines()
        effective_fetch_urls = self._git(
            root, "remote", "get-url", "--all", remote
        ).splitlines()
        effective_push_urls = self._git(
            root, "remote", "get-url", "--push", "--all", remote
        ).splitlines()
        raw_push = self._git_result(root, "config", "--get-all", f"remote.{remote}.pushurl")
        if raw_push.returncode == 0:
            raw_push_urls = raw_push.stdout.splitlines()
        elif raw_push.returncode != 1:
            raise blocked(
                "git_probe_failed",
                "Git 无法读取 raw push URL",
                "请修复 remote 配置后重新 probe",
            )
        else:
            raw_push_urls = []
        urls = [
            *raw_fetch_urls,
            *effective_fetch_urls,
            *effective_push_urls,
            *raw_push_urls,
        ]
        if (
            len(raw_fetch_urls) != 1
            or len(effective_fetch_urls) != 1
            or len(effective_push_urls) != 1
            or len(raw_push_urls) > 1
            or any(
                parse_github_repository_url(url) != repository["slug"] for url in urls
            )
        ):
            raise blocked(
                "git_probe_origin_mismatch",
                "Git raw/effective fetch/push URL 数量或仓库身份与 manifest 不一致",
                "请停止执行并核对仓库 origin/pushurl；不接受 URL 改写后的等价地址",
            )
        origin_url = raw_fetch_urls[0]
        head_sha = self._git(root, "rev-parse", "HEAD").strip()
        status = self._git(root, "status", "--porcelain=v1", "--untracked-files=all")
        if status:
            raise blocked(
                "git_probe_worktree_dirty",
                "业务源码工作树仍有未提交或未跟踪变更",
                "请先按项目规则完成提交或清理，再采集可信 Git 事实",
            )
        refs = self._git(
            root,
            "ls-remote",
            "--heads",
            remote,
            f"refs/heads/{repository['base_branch']}",
            f"refs/heads/{repository['task_branch']}",
        )
        remote_refs = {
            ref.removeprefix("refs/heads/"): sha
            for line in refs.splitlines()
            if "\t" in line
            for sha, ref in [line.split("\t", 1)]
        }
        base_sha = remote_refs.get(repository["base_branch"], "")
        remote_sha = remote_refs.get(repository["task_branch"], "")
        if not base_sha or remote_sha != head_sha:
            raise blocked(
                "git_probe_remote_mismatch",
                "远端基线不存在，或远端任务分支 SHA 与本地 HEAD 不一致",
                "请核对推送结果后重新 probe；不得用本地 SHA 代替远端事实",
            )
        baseline_local_sha = baseline["local_head_sha"]
        baseline_remote_sha = baseline["task_branch_remote_sha"]
        baseline_local_is_ancestor = (
            self._git_result(
                root, "merge-base", "--is-ancestor", baseline_local_sha, head_sha
            ).returncode
            == 0
        )
        baseline_remote_is_ancestor = (
            None
            if baseline_remote_sha is None
            else self._git_result(
                root, "merge-base", "--is-ancestor", baseline_remote_sha, head_sha
            ).returncode
            == 0
        )
        if "git_commit" in selected_actions:
            if baseline_local_sha == head_sha:
                raise blocked(
                    "git_commit_not_attributable",
                    "本地 HEAD 与写入前基线相同，无法证明本轮创建了提交",
                    "请不要绑定 git_commit；需要提交时须在新运行基线后真实创建提交",
                )
            if not baseline_local_is_ancestor:
                raise blocked(
                    "git_commit_baseline_not_ancestor",
                    "写入前本地 HEAD 不是最终 HEAD 的祖先，无法证明增量提交",
                    "请停止执行；不得用历史改写或无关提交伪装本轮动作",
                )
        if "git_push_task_branch" in selected_actions:
            if baseline_remote_sha == head_sha:
                raise blocked(
                    "git_push_not_attributable",
                    "远端任务分支在写入前已指向最终 HEAD，无法证明本轮发生推送",
                    "请不要绑定 git_push_task_branch；使用新的运行采集真实变化",
                )
            if baseline_remote_sha is not None:
                if not baseline_remote_is_ancestor:
                    raise blocked(
                        "git_push_non_fast_forward",
                        "写入前远端任务分支不是最终 HEAD 的祖先，检测到非快进变化",
                        "请停止执行并由研发工程师核对远端历史，不得把强推归为正常推送",
                    )
        latest_verifications: list[dict[str, Any]] = []
        for verification in manifest["verification"]:
            attempts = [
                event
                for event in completed
                if event["action"] == "verification"
                and event["action_data"]["id"] == verification["id"]
            ]
            if not attempts:
                if "git_commit" in selected_actions:
                    self._incomplete(
                        f"git_commit 归因缺少最终 HEAD 验证：{verification['id']}"
                    )
                continue
            latest = attempts[-1]
            if (
                latest["action_data"]["status"] != "passed"
                or latest["action_data"]["head_sha"] != head_sha
            ):
                if "git_commit" in selected_actions:
                    self._incomplete(
                        f"git_commit 归因的最新验证未通过或未绑定最终 HEAD：{verification['id']}"
                    )
                continue
            latest_verifications.append(latest)
        commit_identity = self._git(
            root,
            "log",
            "--format=%H%x00%an%x00%ae%x00%cn%x00%ce",
            f"{baseline_local_sha if 'git_commit' in selected_actions else base_sha}..{head_sha}",
        )
        commit_records: list[list[str]] = []
        for line in commit_identity.splitlines():
            parts = line.split("\0")
            if len(parts) != 5 or any(not item for item in parts):
                raise blocked(
                    "git_commit_identity_invalid",
                    "任务提交范围包含缺少完整 author/committer 身份的提交",
                    "请按已确认执行身份重新创建任务分支提交",
                )
            commit_records.append(parts)
        if not commit_records:
            raise blocked(
                "git_commit_identity_invalid",
                "任务提交范围为空或缺少完整 author/committer 身份",
                "请按已确认执行身份重新创建提交",
            )
        identity_names = (
            "git_author_name",
            "git_author_email",
            "git_committer_name",
            "git_committer_email",
        )
        expected_git_identity = {
            field: manifest["execution_identity"][field] for field in identity_names
        }
        for record in commit_records:
            actual = dict(zip(identity_names, record[1:], strict=True))
            if actual == expected_git_identity:
                continue
            raise blocked(
                "git_commit_identity_mismatch",
                f"任务提交 {record[0]} 的 author/committer 与 manifest 显式身份不一致",
                "请停止执行并按已确认身份重建任务分支提交，不得临时改写证据",
            )
        identity_fields = expected_git_identity
        commit_identity_sha256 = digest(
            [
                {
                    "sha": record[0],
                    **dict(zip(identity_names, record[1:], strict=True)),
                }
                for record in commit_records
            ]
        )
        self._git(root, "cat-file", "-e", f"{base_sha}^{{commit}}")
        ancestor = self._git_result(root, "merge-base", "--is-ancestor", base_sha, head_sha)
        if ancestor.returncode != 0:
            raise blocked(
                "git_probe_base_not_ancestor",
                "manifest 远端基线不是任务提交祖先",
                "请按项目分支规则重新对齐基线，不得继续创建或更新 PR",
            )
        changed_raw = self._git(root, "diff", "--name-only", "-z", f"{base_sha}...{head_sha}")
        changed_paths = [item for item in changed_raw.split("\0") if item]
        if not changed_paths:
            raise blocked(
                "git_probe_no_changes",
                "任务分支相对远端基线没有代码变更",
                "请完成授权范围内实现后重新 probe",
            )
        outside = [
            path
            for path in changed_paths
            if not any(fnmatch.fnmatchcase(path, pattern) for pattern in manifest["scope"]["included"])
            or any(fnmatch.fnmatchcase(path, pattern) for pattern in manifest["scope"]["excluded"])
        ]
        if outside:
            raise blocked(
                "git_probe_scope_violation",
                f"变更路径越出 manifest 授权范围：{', '.join(outside[:10])}",
                "请停止执行；范围变化必须重新确认 manifest",
            )
        git_target = f"git:{repository['slug']}:{branch}@{remote_sha}"
        return self._append_runtime_readback(
            manifest,
            [
                ("git_remote_read", git_target),
                *((action, git_target) for action in selected_actions),
            ],
            "remote_branch_readback",
            {
                "provider": "git",
                "reference": f"git:{repository['slug']}:{branch}@{remote_sha}",
                "url": f"https://github.com/{repository['slug']}/tree/{branch}",
                "repository_slug": repository["slug"],
                "remote_name": remote,
                "branch": branch,
                "sha": remote_sha,
                "status": "exists",
                "protected": branch in repository["protected_branches"],
                "origin_url": origin_url,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "baseline_event_id": baseline_event["event_id"],
                "baseline_local_head_sha": baseline_local_sha,
                "baseline_remote_sha": baseline_remote_sha,
                "baseline_local_is_ancestor": baseline_local_is_ancestor,
                "baseline_remote_is_ancestor": baseline_remote_is_ancestor,
                "attributed_actions": sorted(selected_actions),
                "verification_event_ids": [
                    event["event_id"] for event in latest_verifications
                ],
                "changed_paths": changed_paths,
                "worktree_clean": True,
                **identity_fields,
                "commit_count": len(commit_records),
                "commit_identity_sha256": commit_identity_sha256,
                "approved_plan_sha256": manifest["task_binding"][
                    "approved_plan_sha256"
                ],
                "observed_at": self._now(),
            },
            "Runtime 核对 Git origin、提交、远端分支、基线祖先和变更范围",
        )

    def probe_pr(
        self, manifest_value: str, bind_actions: Iterable[str] = ()
    ) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        self._require_probe_permission(manifest, "github_pr_read", "probe-pr")
        selected_actions = self._validate_probe_bind_actions(
            manifest,
            bind_actions,
            {"github_pr_create_or_update"},
            "probe-pr",
        )
        repository = manifest["repository"]
        completed = self._completed_events(manifest)
        baseline_event = self._latest_action(completed, "prohibition_baseline")
        if baseline_event is None or baseline_event["evidence_origin"] != "runtime_probe":
            self._incomplete("probe-pr 前缺少写入前 Runtime 可信基线")
        if selected_actions and baseline_event["action_data"]["task_open_pr"] is not None:
            raise blocked(
                "github_pr_update_proof_not_supported",
                "写入前已有 open PR，现阶段 Runtime 无法可靠证明本轮更新动作",
                "请不要绑定 github_pr_create_or_update；当前仅支持新建 PR 的归因证明，并记录能力缺口",
            )
        branch_event = self._latest_action(completed, "remote_branch_readback")
        if branch_event is None or branch_event["evidence_origin"] != "runtime_probe":
            self._incomplete("probe-pr 前缺少 Runtime 可信 Git probe")
        actor_result = self._run_command(
            ["gh", "api", "user", "--jq", ".login"],
            cwd=Path(repository["root"]),
            timeout=60,
        )
        github_actor = actor_result.stdout.strip()
        if (
            actor_result.returncode != 0
            or github_actor
            != manifest["execution_identity"]["github_actor_login"]
        ):
            raise blocked(
                "github_actor_identity_mismatch",
                "当前 GitHub 登录身份与 manifest 显式 actor 不一致",
                "请切换到已确认 GitHub 账户后重试，不得借用其它登录会话",
            )
        result = self._run_command(
            [
                "gh", "pr", "view", repository["task_branch"],
                "--repo", repository["slug"],
                "--json", "number,url,state,isDraft,mergedAt,headRefName,headRefOid,baseRefName,reviewDecision,statusCheckRollup",
            ],
            cwd=Path(repository["root"]),
            timeout=60,
        )
        if result.returncode != 0:
            raise blocked(
                "github_pr_probe_failed",
                "gh 无法实时读取任务 PR",
                "请检查 GitHub 授权和 PR 是否存在后重试",
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise blocked(
                "github_pr_probe_invalid",
                "gh PR 回读不是有效 JSON",
                "请升级或修复项目认可的 gh 工具",
            ) from error
        state = str(payload.get("state", "")).upper()
        draft = bool(payload.get("isDraft"))
        merged = payload.get("mergedAt") is not None or state == "MERGED"
        head_sha = str(payload.get("headRefOid", ""))
        branch_data = branch_event["action_data"]
        if (
            state != "OPEN"
            or draft
            or merged
            or payload.get("headRefName") != repository["task_branch"]
            or payload.get("baseRefName") != repository["target_branch"]
            or head_sha != branch_data["sha"]
        ):
            raise blocked(
                "github_pr_probe_mismatch",
                "PR 必须 open、非 draft、未合并，且 head/base/SHA 与可信 Git probe 一致",
                "请修复 PR 事实后重新 probe，不得手工导入通过状态",
            )
        ci_status = self._ci_status(payload.get("statusCheckRollup"))
        policy = manifest["pr_endpoint"]["ci_policy"]
        if policy == "require_passed" and ci_status != "passed":
            raise blocked(
                "github_pr_ci_not_passed",
                f"manifest 要求 CI passed，当前为 {ci_status}",
                "请等待或修复 CI 后重新 probe",
            )
        if policy == "allow_pending" and ci_status == "failed":
            raise blocked(
                "github_pr_ci_failed",
                "CI 已失败，不满足 manifest CI 策略",
                "请修复 CI 后重新 probe",
            )
        review = str(payload.get("reviewDecision", "")).upper()
        review_state = {
            "APPROVED": "approved",
            "CHANGES_REQUESTED": "changes_requested",
        }.get(review, "awaiting_review")
        number = payload.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise blocked(
                "github_pr_probe_invalid",
                "gh PR 回读缺少有效 number",
                "请核对 GitHub 返回格式",
            )
        pr_url = str(payload.get("url", ""))
        return self._append_runtime_readback(
            manifest,
            [
                ("github_pr_read", pr_url),
                *((action, pr_url) for action in selected_actions),
            ],
            "pr_readback",
            {
                "provider": "github",
                "reference": f"github:{repository['slug']}:pull:{number}",
                "url": pr_url,
                "repository_slug": repository["slug"],
                "number": number,
                "status": "open",
                "merged": False,
                "draft": False,
                "head_branch": repository["task_branch"],
                "head_sha": head_sha,
                "base_branch": repository["target_branch"],
                "review_state": review_state,
                "ci_status": ci_status,
                "github_actor_login": github_actor,
                "approved_plan_sha256": manifest["task_binding"][
                    "approved_plan_sha256"
                ],
                "baseline_event_id": baseline_event["event_id"],
                "git_readback_event_id": branch_event["event_id"],
                "attributed_actions": sorted(selected_actions),
                "creation_proof": bool(selected_actions),
                "observed_at": self._now(),
            },
            "Runtime 实时核对 GitHub PR 与 CI 策略",
        )

    def verify(self, manifest_value: str, verification_id: str) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        selected = [item for item in manifest["verification"] if item["id"] == verification_id]
        if len(selected) != 1:
            raise blocked(
                "verification_not_authorized",
                f"manifest 未定义验证 {verification_id}",
                "只能执行已确认 manifest 中的精确 argv",
            )
        item = selected[0]
        validate_verification_command(
            item["command"],
            item["working_directory"],
            label=f"verification[{verification_id}]",
        )
        repository_root = Path(manifest["repository"]["root"])
        head_before = self._git(repository_root, "rev-parse", "HEAD").strip()
        cwd = (repository_root / item["working_directory"]).resolve()
        try:
            cwd.relative_to(repository_root)
        except ValueError as error:
            raise blocked(
                "verification_workdir_escape",
                "验证工作目录越出业务仓库",
                "请重新确认 manifest 验证目录",
            ) from error
        if not cwd.is_dir() or cwd.is_symlink():
            raise blocked(
                "verification_workdir_invalid",
                "验证工作目录不存在或是符号链接",
                "请修复业务仓库目录后重试",
            )
        started = time.monotonic()
        workspace_identity = read_json(self.workspace.config_path)
        connection_id = workspace_identity.get("connection_id")
        if not isinstance(connection_id, str) or not connection_id:
            raise blocked(
                "workspace_jira_identity_upgrade_required",
                "工作空间缺少已固化 Jira Connection 身份",
                "请重新初始化业务项目工作空间",
            )
        jira_connection = load_jira_connection(
            self.install_root,
            connection_id,
            workspace_root=self.workspace.root,
        )
        validate_workspace_jira_binding(self.workspace, jira_connection)
        denied_environment_keys = {
            jira_connection.email_env,
            jira_connection.token_env,
        }
        try:
            result = self._run_command(
                list(item["command"]),
                cwd=cwd,
                timeout=item["timeout_seconds"],
                denied_environment_keys=denied_environment_keys,
                verification_repository_root=repository_root,
            )
            exit_code = result.returncode
            stdout = result.stdout
            stderr = result.stderr
            status = "passed" if exit_code == 0 else "failed"
        except subprocess.TimeoutExpired as error:
            exit_code = 124
            stdout = self._stream_text(error.stdout)
            stderr = self._stream_text(error.stderr)
            status = "blocked"
        duration = max(0.0, time.monotonic() - started)
        head_after = self._git(repository_root, "rev-parse", "HEAD").strip()
        if head_after != head_before:
            raise blocked(
                "verification_head_changed",
                "验证执行期间 Git HEAD 发生变化，结果不能绑定确定提交",
                "请停止并在稳定 HEAD 上重新执行验证",
            )
        recorded = self._append_runtime_fact(
            manifest,
            "verification",
            {
                "id": verification_id,
                "status": status,
                "command_sha256": verification_digest(item),
                "evidence_reference": f"runtime-verification:{verification_id}:{int(time.time())}",
                "exit_code": exit_code,
                "duration_seconds": round(duration, 6),
                "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(stderr.encode()).hexdigest(),
                "output_summary": (
                    f"exit={exit_code}; stdout_bytes={len(stdout.encode())}; "
                    f"stderr_bytes={len(stderr.encode())}; "
                    "network_policy=allowlist-only-no-sandbox"
                ),
                "head_sha": head_after,
            },
            f"Runtime 执行 manifest 验证：{verification_id}",
            duration=duration,
        )
        return {
            **recorded,
            "verification_status": status,
            "exit_code": exit_code,
            "head_sha": head_after,
        }

    def probe_prohibitions(self, manifest_value: str) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        for permission in ("jira_read", "git_remote_read", "github_pr_read"):
            self._require_probe_permission(manifest, permission, "probe-prohibitions")
        completed = self._completed_events(manifest)
        baselines = [
            event for event in completed if event["action"] == "prohibition_baseline"
        ]
        if len(baselines) != 1:
            self._incomplete(
                "禁止动作最终 probe 前必须有且只有一条写入前可信基线"
            )
        if any(event["action"] == "prohibition_check" for event in completed):
            raise blocked(
                "prohibition_check_already_recorded",
                "五项禁止动作已经完成最终核验，不能覆盖或追加第二组结论",
                "请继续 finalize；新一轮核验必须使用新的 agentic_run_id",
            )
        baseline = baselines[0]
        baseline_data = baseline["action_data"]
        jira = self._latest_action(completed, "jira_readback")
        branch = self._latest_action(completed, "remote_branch_readback")
        pr = self._latest_action(completed, "pr_readback")
        if not jira or not branch or not pr:
            self._incomplete("禁止动作 probe 前必须完成 Jira、Git、PR 可信 probe")
        repository = manifest["repository"]
        root = Path(repository["root"])
        head_sha = branch["action_data"]["head_sha"]
        context = load_jira_context(self.workspace, self.install_root)
        email, token = context.require_credentials()
        if context.connection.base_url.rstrip("/") != manifest["jira"]["base_url"].rstrip("/"):
            raise blocked(
                "jira_probe_binding_mismatch",
                "禁止动作复核时当前 Jira 站点与 manifest 不一致",
                "请停止自动化并核对工作空间身份",
            )
        jira_client = JiraClient(
            context.profile,
            UrllibJiraTransport(context.connection, email, token),
        )
        live_identity = jira_client.current_user_details()
        validate_workspace_jira_binding(
            self.workspace,
            context.connection,
            account_id=live_identity["account_id"],
        )
        live_issue = jira_client.get_issue(manifest["issue"]["key"])
        if (
            live_identity["account_id"] != manifest["jira"]["account_id"]
            or live_issue.assignee != manifest["jira"]["assignee_account_id"]
            or live_issue.issue_id != manifest["issue"]["id"]
        ):
            raise blocked(
                "jira_probe_identity_changed",
                "禁止动作复核时 Jira 账户、负责人或 issue 身份发生变化",
                "请停止自动化并由研发工程师处理身份变化",
            )
        live_category = self._jira_status_category(live_issue)
        live_pr_result = self._run_command(
            [
                "gh", "pr", "view", repository["task_branch"],
                "--repo", repository["slug"],
                "--json", "state,isDraft,mergedAt,headRefName,headRefOid,baseRefName",
            ],
            cwd=root,
            timeout=60,
        )
        if live_pr_result.returncode != 0:
            raise blocked(
                "github_pr_probe_failed",
                "禁止动作复核时无法实时读取 PR",
                "请修复 GitHub 只读授权后重新 probe",
            )
        try:
            live_pr = json.loads(live_pr_result.stdout)
        except json.JSONDecodeError as error:
            raise blocked(
                "github_pr_probe_invalid",
                "禁止动作复核的 PR 响应不是有效 JSON",
                "请修复项目认可的 gh 工具",
            ) from error
        live_merged = live_pr.get("mergedAt") is not None or str(
            live_pr.get("state", "")
        ).upper() == "MERGED"
        if not live_merged and (
            str(live_pr.get("state", "")).upper() != "OPEN"
            or bool(live_pr.get("isDraft"))
            or live_pr.get("headRefName") != repository["task_branch"]
            or live_pr.get("headRefOid") != head_sha
            or live_pr.get("baseRefName") != repository["target_branch"]
        ):
            raise blocked(
                "github_pr_probe_mismatch",
                "禁止动作复核时 PR 已不再满足 open、非 draft 和 head/base/SHA 绑定",
                "请停止自动化并重新核对 PR 事实",
            )
        tag_refs = self._parse_remote_refs(
            self._git(root, "ls-remote", "--tags", repository["remote_name"]),
            "refs/tags/",
        )
        protected_ref_map = self._remote_heads(self._git(
            root,
            "ls-remote",
            "--heads",
            repository["remote_name"],
            *[f"refs/heads/{name}" for name in repository["protected_branches"]],
        ))
        protected_heads = [
            {"branch": branch, "sha": protected_ref_map.get(branch)}
            for branch in sorted(repository["protected_branches"])
        ]
        releases = self._github_json_array(
            root,
            [
                "gh",
                "release",
                "list",
                "--repo",
                repository["slug"],
                "--limit",
                "1000",
                "--json",
                "tagName,publishedAt",
            ],
            "无法实时核对 GitHub release 禁止动作",
        )
        release_records = sorted(
            [
                {
                    "tag_name": str(item.get("tagName", "")).strip(),
                    "published_at": (
                        str(item["publishedAt"]).strip()
                        if item.get("publishedAt")
                        else None
                    ),
                }
                for item in releases
                if str(item.get("tagName", "")).strip()
            ],
            key=lambda item: (item["tag_name"], item["published_at"] or ""),
        )
        protected_contains_head = False
        for item in protected_heads:
            protected_sha = item["sha"]
            if protected_sha is None:
                protected_contains_head = True
                continue
            compare = self._run_command(
                [
                    "gh",
                    "api",
                    f"repos/{repository['slug']}/compare/{head_sha}...{protected_sha}",
                    "--jq",
                    ".status",
                ],
                cwd=root,
                timeout=60,
            )
            relation = compare.stdout.strip().casefold()
            if compare.returncode != 0 or relation not in {
                "ahead",
                "behind",
                "diverged",
                "identical",
            }:
                raise blocked(
                    "github_compare_probe_failed",
                    f"无法核对任务 HEAD 与保护分支 {item['branch']} 的祖先关系",
                    "请修复 GitHub 只读授权后重新执行禁止动作 probe",
                )
            if relation in {"ahead", "identical"}:
                protected_contains_head = True
        observations = {
            "merge_pr": live_merged,
            "jira_done": live_category.casefold() == "done",
            "release": release_records != baseline_data["release_records"],
            "create_tag": tag_refs != baseline_data["tag_refs"],
            "push_protected_branch": (
                protected_heads != baseline_data["protected_heads"]
                or protected_contains_head
            ),
        }
        outputs = []
        for action in PROHIBITED_ACTIONS:
            outputs.append(
                self._append_runtime_fact(
                    manifest,
                    "prohibition_check",
                    {
                        "action": action,
                        "observed": observations[action],
                        "evidence_reference": (
                            f"runtime-prohibition:{action}:"
                            f"baseline={baseline['event_id']}:head={head_sha}"
                        ),
                    },
                    f"Runtime 实时核对禁止动作：{action}",
                )
            )
        return {"checks": outputs, "observed": [key for key, value in observations.items() if value]}

    def record_unverified_prohibitions(self, manifest_value: str) -> dict[str, Any]:
        manifest = self._load_open_manifest(manifest_value)
        outputs = []
        for action in PROHIBITED_ACTIONS:
            outputs.append(
                self._append_runtime_fact(
                    manifest,
                    "prohibition_check",
                    {
                        "action": action,
                        "observed": "not_verified",
                        "evidence_reference": f"runtime-prohibition:{action}:not-verified",
                    },
                    f"Runtime 记录尚未到达可实时核验阶段的禁止动作：{action}",
                )
            )
        return {"checks": outputs, "observed": [], "not_verified": list(PROHIBITED_ACTIONS)}

    def finalize(
        self, manifest_value: str, status: str, next_action: str
    ) -> dict[str, Any]:
        if status not in {"ready_for_pr_review", "blocked", "failed"}:
            raise blocked(
                "result_status_invalid",
                "finalize 必须显式指定 ready_for_pr_review、blocked 或 failed",
                "请依据真实执行结论选择状态，不得默认猜测",
            )
        next_action = next_action.strip()
        if not next_action:
            raise blocked(
                "result_next_action_required",
                "结果包必须明确下一步动作",
                "请填写基于真实执行状态的脱敏 next_action",
            )
        reject_sensitive_content(next_action)
        manifest = self._load_open_manifest(manifest_value)
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            if paths["result"].is_file():
                existing = read_json(paths["result"])
                if existing.get("status") != status or existing.get("next_action") != next_action:
                    raise blocked(
                        "task_run_already_finalized",
                        "当前 task-run 已按不同结论生成不可变结果包",
                        "请读取现有结果；新结论必须使用新的 agentic_run_id",
                    )
                if existing.get("result_sha256") != result_digest(existing):
                    raise blocked(
                        "result_digest_mismatch",
                        "现有结果包摘要校验失败",
                        "请停止使用该结果包并交给 AgenticOps 维护者调查",
                    )
                return self._finalize_result(paths, existing, created=False)
            self._assert_open_state(paths)
            envelopes = self._read_journal(paths["events"])
            result = self._build_result(manifest, envelopes, status, next_action)
            atomic_write_json(paths["result"], result)
            atomic_write_json(
                paths["state"],
                {
                    "schema_version": 1,
                    "protocol": PROTOCOL,
                    "status": status,
                    "manifest_sha256": manifest_digest(manifest),
                    "result_sha256": result["result_sha256"],
                },
            )
            return self._finalize_result(paths, result, created=True)

    def _build_result(
        self,
        manifest: Mapping[str, Any],
        envelopes: list[dict[str, Any]],
        status: str,
        next_action: str,
    ) -> dict[str, Any]:
        if not envelopes:
            self._incomplete("审计事件为空")
        events = [envelope["event"] for envelope in envelopes]
        self._validate_closed_steps(events)
        completed = [event for event in events if event["status"] == "completed"]
        by_action = {
            action: [event for event in completed if event["action"] == action]
            for action in (
                "external_action",
                "jira_readback",
                "jira_write_readback",
                "prohibition_baseline",
                "remote_branch_readback",
                "pr_readback",
                "verification",
                "human_intervention",
                "failure",
                "retry",
                "waiting",
                "quality_finding",
                "retrospective",
                "prohibition_check",
            )
        }
        event_index = {event["event_id"]: event for event in completed}
        event_sequences = {
            envelope["event"]["event_id"]: envelope["sequence"]
            for envelope in envelopes
        }
        self._validate_external_actions(
            manifest,
            by_action["external_action"],
            event_index,
            event_sequences,
            status,
        )
        for retry_event in by_action["retry"]:
            failure_id = retry_event["action_data"]["failure_event_id"]
            failure = event_index.get(failure_id)
            if (
                failure is None
                or failure["action"] != "failure"
                or event_sequences[failure_id] >= event_sequences[retry_event["event_id"]]
            ):
                self._incomplete(
                    f"retry {retry_event['event_id']} 必须引用更早的 failure 事件"
                )

        prohibitions = self._validate_prohibitions(by_action["prohibition_check"], status)
        baselines = by_action["prohibition_baseline"]
        if status == "ready_for_pr_review" and len(baselines) != 1:
            self._incomplete(
                "ready_for_pr_review 必须包含且只包含一条外部写入前禁止动作基线"
            )
        if baselines:
            first_external_write_sequence = min(
                (
                    event_sequences[event["event_id"]]
                    for event in by_action["external_action"]
                    if event["action_data"]["action"]
                    not in {"jira_read", "git_remote_read", "github_pr_read"}
                    and event["action_data"]["status"] in {"applied", "unknown"}
                ),
                default=None,
            )
            baseline_sequence = event_sequences[baselines[0]["event_id"]]
            if (
                len(baselines) != 1
                or (
                    first_external_write_sequence is not None
                    and baseline_sequence >= first_external_write_sequence
                )
            ):
                self._incomplete("禁止动作基线必须唯一且早于任何外部写入")
        retrospective = self._validate_retrospective(by_action, event_index)

        jira_event = self._latest(by_action["jira_readback"])
        branch_event = self._latest(by_action["remote_branch_readback"])
        pr_event = self._latest(by_action["pr_readback"])
        verification_events = by_action["verification"]
        if status == "ready_for_pr_review":
            self._validate_ready_facts(
                manifest,
                jira_event,
                branch_event,
                pr_event,
                verification_events,
                by_action["jira_write_readback"],
                by_action["external_action"],
                by_action["failure"],
                by_action["retry"],
            )
            if (
                jira_event is not None
                and not jira_event["action_data"]["formal_takeover_verified"]
            ):
                automation_gaps = [
                    event
                    for event in by_action["quality_finding"]
                    if event["action_data"]["category"] == "automation_gap"
                ]
                if not automation_gaps or not retrospective["action_data"]["residual_risks"]:
                    self._incomplete(
                        "接管评论未完成正式核对时，必须记录 automation_gap 和残留风险"
                    )
        else:
            if status == "failed" and not by_action["failure"]:
                self._incomplete("failed 结果缺少 failure 事件")
            blocked_terminals = [event for event in events if event["status"] == "blocked"]
            if status == "blocked" and not (blocked_terminals or by_action["failure"]):
                self._incomplete("blocked 结果缺少 blocked 步骤或 failure 事件")

        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "status": status,
            "delivery_passed": status == "ready_for_pr_review",
            "manifest_sha256": manifest_digest(manifest),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "facts": {
                "jira_readback": jira_event["action_data"] if jira_event else None,
                "remote_branch_readback": branch_event["action_data"] if branch_event else None,
                "pr_readback": pr_event["action_data"] if pr_event else None,
                "verifications": [event["action_data"] for event in verification_events],
                "external_actions": [
                    event["action_data"] for event in by_action["external_action"]
                ],
            },
            "timeline": envelopes,
            "human_interventions": self._envelopes_for(
                envelopes, by_action["human_intervention"]
            ),
            "waitings": self._envelopes_for(envelopes, by_action["waiting"]),
            "failures": self._envelopes_for(envelopes, by_action["failure"]),
            "quality_findings": self._envelopes_for(
                envelopes, by_action["quality_finding"]
            ),
            "retrospective": self._envelope_for(envelopes, retrospective),
            "prohibitions": self._envelopes_for(envelopes, prohibitions),
            "next_action": next_action,
            "result_sha256": "",
        }
        reject_sensitive_content(result)
        result["result_sha256"] = result_digest(result)
        return result

    def _validate_ready_facts(
        self,
        manifest: Mapping[str, Any],
        jira: Mapping[str, Any] | None,
        branch: Mapping[str, Any] | None,
        pr: Mapping[str, Any] | None,
        verification_events: list[dict[str, Any]],
        jira_write_events: list[dict[str, Any]],
        external_actions: list[dict[str, Any]],
        failure_events: list[dict[str, Any]],
        retry_events: list[dict[str, Any]],
    ) -> None:
        if jira is None or branch is None or pr is None:
            self._incomplete("ready_for_pr_review 缺少 Jira、远端分支或真实 PR 回读")
        issue = manifest["issue"]
        repository = manifest["repository"]
        jira_data = jira["action_data"]
        branch_data = branch["action_data"]
        pr_data = pr["action_data"]
        if (
            jira_data["issue_key"] != issue["key"]
            or jira_data["issue_id"] != issue["id"]
            or jira_data["project_key"] != issue["project_key"]
        ):
            self._incomplete("Jira 回读身份与 manifest 不一致")
        for event in (jira, branch, pr):
            if event["evidence_origin"] != "runtime_probe" or event["actor"] != "runtime":
                self._incomplete("ready_for_pr_review 的 Jira/Git/PR 事实必须由 Runtime probe 生成")
        expected_jira = manifest["jira"]
        if (
            jira_data["account_id"] != expected_jira["account_id"]
            or jira_data["assignee_account_id"] != expected_jira["assignee_account_id"]
            or jira_data["account_id"] != jira_data["assignee_account_id"]
            or jira_data["status_category"] not in expected_jira["allowed_status_categories"]
            or jira_data["status_category"].casefold() == "done"
            or jira_data["mapped_status"]
            != expected_jira["status_mapping"].get(jira_data["status"])
        ):
            self._incomplete("Jira 账户、负责人、状态分类或 Profile 映射与 manifest 不一致")
        task_binding = manifest["task_binding"]
        if (
            jira_data["issue_content_sha256"]
            != task_binding["issue_content_sha256"]
            or jira_data["approved_plan_sha256"]
            != task_binding["approved_plan_sha256"]
        ):
            self._incomplete("Jira 任务内容或批准计划摘要与 manifest 不一致")
        current_run = manifest["agent"]["agentic_run_id"]
        if (
            len(jira_write_events) != 2
            or {event["action_data"]["operation"] for event in jira_write_events}
            != {"jira_comment", "jira_worklog"}
        ):
            self._incomplete(
                "ready_for_pr_review 必须且只能包含本运行各一条 Jira Comment/Worklog 写后回读"
            )
        for event in jira_write_events:
            write = event["action_data"]
            if (
                event["actor"] != "runtime"
                or event["evidence_origin"] != "runtime_probe"
                or write["issue_key"] != issue["key"]
                or write["agentic_run_id"] != current_run
                or write["created"] is not True
                or write["write_precondition"] != "absent"
                or write["attempt_file"] is None
                or write["write_attempt_id"] is None
                or write["write_attempt_started_at"] is None
            ):
                self._incomplete(
                    "ready_for_pr_review 的 Jira Comment/Worklog 必须由 Runtime 证明已为本运行创建"
                )
        if jira_data["status"].strip().casefold() in {
            "done",
            "closed",
            "resolved",
            "完成",
            "已完成",
        }:
            raise blocked(
                "prohibited_action_observed",
                "Jira 回读显示任务已进入完成终态",
                "请停止自动化并由研发工程师核对越权流转",
            )
        if (
            branch_data["repository_slug"] != repository["slug"]
            or branch_data["remote_name"] != repository["remote_name"]
            or branch_data["branch"] != repository["task_branch"]
            or branch_data["sha"] != branch_data["head_sha"]
            or branch_data["protected"]
            or set(branch_data["attributed_actions"])
            != {"git_commit", "git_push_task_branch"}
            or branch_data["baseline_local_head_sha"] == branch_data["head_sha"]
            or branch_data["baseline_local_is_ancestor"] is not True
            or branch_data["baseline_remote_sha"] == branch_data["head_sha"]
            or (
                branch_data["baseline_remote_sha"] is not None
                and branch_data["baseline_remote_is_ancestor"] is not True
            )
        ):
            self._incomplete("远端分支回读与 manifest 任务分支不一致或分支受保护")
        execution_identity = manifest["execution_identity"]
        for field in (
            "git_author_name",
            "git_author_email",
            "git_committer_name",
            "git_committer_email",
        ):
            if branch_data[field] != execution_identity[field]:
                self._incomplete(f"Git 提交身份 {field} 与 manifest 不一致")
        if branch_data["approved_plan_sha256"] != task_binding["approved_plan_sha256"]:
            self._incomplete("Git probe 未绑定已确认批准计划摘要")
        endpoint = manifest["pr_endpoint"]
        if (
            pr_data["repository_slug"] != endpoint["repository_slug"]
            or pr_data["head_branch"] != repository["task_branch"]
            or pr_data["base_branch"] != endpoint["target_branch"]
            or pr_data["head_sha"] != branch_data["sha"]
            or pr_data["merged"]
            or pr_data["draft"]
            or pr_data["status"] != "open"
            or pr_data["github_actor_login"]
            != execution_identity["github_actor_login"]
            or pr_data["approved_plan_sha256"]
            != task_binding["approved_plan_sha256"]
            or set(pr_data["attributed_actions"])
            != {"github_pr_create_or_update"}
            or pr_data["creation_proof"] is not True
        ):
            self._incomplete("PR 回读与 manifest/远端任务分支不一致，或 PR 已合并")
        ci_policy = endpoint["ci_policy"]
        if (
            (ci_policy == "require_passed" and pr_data["ci_status"] != "passed")
            or (ci_policy == "allow_pending" and pr_data["ci_status"] == "failed")
        ):
            self._incomplete("PR CI 事实不满足 manifest ci_policy")

        expected = {
            item["id"]: verification_digest(item) for item in manifest["verification"]
        }
        observed: dict[str, list[dict[str, Any]]] = {}
        for event in verification_events:
            if event["evidence_origin"] != "runtime_probe" or event["actor"] != "runtime":
                self._incomplete(f"验证 {event['event_id']} 不是 Runtime 可信执行结果")
            data = event["action_data"]
            if data["id"] not in expected:
                self._incomplete(f"验证 {data['id']} 不在 manifest 中")
            if data["command_sha256"] != expected[data["id"]]:
                self._incomplete(f"验证 {data['id']} 的命令摘要与 manifest 不一致")
            if (data["status"] == "passed") != (data["exit_code"] == 0):
                self._incomplete(f"验证 {data['id']} 的状态与 exit_code 不一致")
            observed.setdefault(data["id"], []).append(event)
        if set(observed) != set(expected):
            self._incomplete("验证结果未完整覆盖 manifest 命令摘要")
        superseded_failures = 0
        for verification_id, attempts in observed.items():
            if attempts[-1]["action_data"]["status"] != "passed":
                self._incomplete(f"验证 {verification_id} 的最新尝试未通过")
            if attempts[-1]["action_data"]["head_sha"] != branch_data["head_sha"]:
                self._incomplete(
                    f"验证 {verification_id} 的最新通过尝试未绑定最终 Git/PR head_sha"
                )
            superseded_failures += sum(
                attempt["action_data"]["status"] != "passed"
                for attempt in attempts[:-1]
            )
        successful_retries = [
            event
            for event in retry_events
            if event["action_data"]["outcome"] == "succeeded"
        ]
        if superseded_failures and (
            len(failure_events) < superseded_failures
            or len(successful_retries) < superseded_failures
        ):
            self._incomplete(
                "验证失败后重测虽已通过，但缺少逐次 failure/retry(succeeded) 审计"
            )

        applied = {
            event["action_data"]["action"]
            for event in external_actions
            if event["action_data"]["status"] == "applied"
        }
        required = {
            "jira_read",
            "jira_comment",
            "jira_worklog",
            "git_commit",
            "git_push_task_branch",
            "github_pr_create_or_update",
        }
        if not required <= applied:
            missing = sorted(required - applied)
            self._incomplete(
                "缺少真实 Jira 读取、Comment、Worklog、任务分支推送或 PR "
                f"创建/更新动作记录：{missing}"
            )

    def _validate_external_actions(
        self,
        manifest: Mapping[str, Any],
        external_actions: list[dict[str, Any]],
        event_index: Mapping[str, Mapping[str, Any]],
        event_sequences: Mapping[str, int],
        result_status: str,
    ) -> None:
        permissions = set(manifest["permitted_external_actions"])
        authorization = manifest["authorization"]["reference"]
        allowed_readbacks = {
            "jira_read": {"jira_readback", "prohibition_baseline"},
            "jira_comment": {"jira_write_readback"},
            "jira_worklog": {"jira_write_readback"},
            "git_commit": {"remote_branch_readback"},
            "git_remote_read": {"remote_branch_readback", "prohibition_baseline"},
            "git_push_task_branch": {"remote_branch_readback"},
            "github_pr_create_or_update": {"pr_readback"},
            "github_pr_read": {"pr_readback", "prohibition_baseline"},
        }
        for event in external_actions:
            data = event["action_data"]
            if data["action"] not in permissions:
                raise blocked(
                    "external_action_not_authorized",
                    f"外部动作 {data['action']} 不在 manifest 授权范围内",
                    "请停止执行；范围变化需要重新确认 manifest",
                )
            if data["status"] in {"applied", "unknown"} and event["authorization_reference"] != authorization:
                raise blocked(
                    "external_action_authorization_mismatch",
                    f"外部动作 {data['action']} 未引用 manifest 授权",
                    "请补充真实授权引用，不得从聊天或隐式配置推断",
                )
            reference = data["readback_event_id"]
            if data["status"] == "applied":
                if not reference or reference not in event_index:
                    self._incomplete(f"已执行外部动作 {data['action']} 缺少真实回读事件")
                if event_index[reference]["action"] not in allowed_readbacks[data["action"]]:
                    self._incomplete(f"外部动作 {data['action']} 引用的回读类型不匹配")
                readback = event_index[reference]
                if readback.get("evidence_origin") != "runtime_probe":
                    self._incomplete(f"外部动作 {data['action']} 未引用 Runtime 可信回读")
                if event_sequences[reference] <= event_sequences[event["event_id"]]:
                    self._incomplete(f"外部动作 {data['action']} 的回读没有发生在动作之后")
                expected_target = self._external_action_target(data["action"], readback)
                if expected_target is None or data["target"] != expected_target:
                    self._incomplete(f"外部动作 {data['action']} 的目标未与回读事实绑定")
                if (
                    data["action"] in {"git_commit", "git_push_task_branch"}
                    and data["action"]
                    not in readback["action_data"]["attributed_actions"]
                ):
                    self._incomplete(
                        f"外部动作 {data['action']} 的 Git 回读缺少本运行归因证明"
                    )
                if (
                    data["action"] == "github_pr_create_or_update"
                    and data["action"]
                    not in readback["action_data"]["attributed_actions"]
                ):
                    self._incomplete("PR 动作回读缺少本运行新建归因证明")
            if data["status"] == "unknown" and result_status == "ready_for_pr_review":
                self._incomplete(f"外部动作 {data['action']} 结果仍为 unknown")

    def _validate_prohibitions(
        self, events: list[dict[str, Any]], result_status: str
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for action in PROHIBITED_ACTIONS:
            matching = [event for event in events if event["action_data"]["action"] == action]
            if len(matching) != 1:
                self._incomplete(f"禁止动作 {action} 必须且只能有一条完成审计")
            selected.append(matching[0])
        observed = [
            event["action_data"]["action"]
            for event in selected
            if event["action_data"]["observed"] is True
        ]
        if result_status == "ready_for_pr_review" and any(
            event["action_data"]["observed"] == "not_verified" for event in selected
        ):
            self._incomplete("ready_for_pr_review 的五项禁止动作必须全部实时核验")
        for event in selected:
            if event["evidence_origin"] != "runtime_probe" or event["actor"] != "runtime":
                self._incomplete("禁止动作结论必须由 Runtime probe 生成")
        if observed and result_status != "failed":
            raise blocked(
                "prohibited_action_requires_failed_result",
                f"观察到禁止动作：{', '.join(observed)}",
                "请停止自动化并显式生成 failed 事故结果包，保留越权证据",
            )
        return selected

    def _validate_retrospective(
        self,
        by_action: Mapping[str, list[dict[str, Any]]],
        event_index: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        events = by_action["retrospective"]
        if len(events) != 1:
            self._incomplete("必须且只能有一条 completed retrospective 事件")
        retrospective = events[0]
        data = retrospective["action_data"]
        expected = {
            "quality_finding_event_ids": self._ids(by_action["quality_finding"]),
            "human_intervention_event_ids": self._ids(by_action["human_intervention"]),
            "failure_event_ids": self._ids(by_action["failure"]),
            "retry_event_ids": self._ids(by_action["retry"]),
            "waiting_event_ids": self._ids(by_action["waiting"]),
        }
        for field, ids in expected.items():
            if set(data[field]) != ids or len(data[field]) != len(ids):
                self._incomplete(f"retrospective.{field} 未完整引用审计事件")
        quality_ids = expected["quality_finding_event_ids"]
        if (
            set(data["ordered_improvement_event_ids"]) != quality_ids
            or len(data["ordered_improvement_event_ids"]) != len(quality_ids)
        ):
            self._incomplete("retrospective 未对全部改进候选排序")
        if set(data["reviewed_categories"]) != set(QUALITY_CATEGORIES):
            self._incomplete("retrospective 未逐项审查全部质量分类")
        reviews = data["category_reviews"]
        review_by_category = {item["category"]: item for item in reviews}
        if len(review_by_category) != len(QUALITY_CATEGORIES):
            self._incomplete("retrospective.category_reviews 未唯一覆盖四类质量问题")
        process_event_ids = {
            event["event_id"]
            for action in ("failure", "retry", "human_intervention", "waiting")
            for event in by_action[action]
        }
        reviewed_process_ids: set[str] = set()
        for category in QUALITY_CATEGORIES:
            findings = [
                event
                for event in by_action["quality_finding"]
                if event["action_data"]["category"] == category
            ]
            review = review_by_category.get(category)
            if review is None:
                self._incomplete(f"retrospective 缺少 {category} 分类结论")
            source_ids = set(review["source_event_ids"])
            for event_id in source_ids:
                source = event_index.get(event_id)
                if source is None or source["action"] not in {
                    "quality_finding",
                    "failure",
                    "retry",
                    "human_intervention",
                    "waiting",
                }:
                    self._incomplete(
                        f"retrospective {category} 引用了不允许的来源事件 {event_id}"
                    )
                if (
                    source["action"] == "quality_finding"
                    and source["action_data"]["category"] != category
                ):
                    self._incomplete(
                        f"retrospective {category} 引用了其它分类的 finding {event_id}"
                    )
            expected_outcome = "finding" if findings or source_ids else "no_finding"
            if review["outcome"] != expected_outcome:
                self._incomplete(
                    f"retrospective {category} 的 finding/no_finding 与实际发现不一致"
                )
            finding_ids = {event["event_id"] for event in findings}
            if finding_ids and not finding_ids <= source_ids:
                self._incomplete(
                    f"retrospective {category} 未把全部 finding 事件列为来源"
                )
            if source_ids and not source_ids <= set(review["evidence_references"]):
                self._incomplete(
                    f"retrospective {category} 的证据未覆盖全部来源事件"
                )
            if review["outcome"] == "finding":
                reviewed_process_ids.update(source_ids & process_event_ids)
        uncovered = process_event_ids - reviewed_process_ids
        if uncovered:
            self._incomplete(
                "failure/retry/human_intervention/waiting 必须逐事件被 finding 分类复盘引用："
                f"{sorted(uncovered)}"
            )
        for event_id in self._all_retrospective_refs(data):
            if event_id not in event_index:
                self._incomplete(f"retrospective 引用了不存在的事件 {event_id}")
        return retrospective

    def _validate_event_transition(
        self,
        event: Mapping[str, Any],
        envelopes: list[dict[str, Any]],
        manifest: Mapping[str, Any],
    ) -> None:
        events = [envelope["event"] for envelope in envelopes]
        same_step = [item for item in events if item["step_id"] == event["step_id"]]
        if event["status"] == "started":
            if same_step:
                raise blocked(
                    "event_step_conflict",
                    f"步骤 {event['step_id']} 已经开始或结束",
                    "请使用新 step_id，不得覆盖已有步骤",
                )
        else:
            started = [item for item in same_step if item["status"] == "started"]
            terminal = [
                item for item in same_step if item["status"] in {"completed", "blocked"}
            ]
            if len(started) != 1 or terminal:
                raise blocked(
                    "event_transition_invalid",
                    f"步骤 {event['step_id']} 必须先有且只有一条 started，且尚未结束",
                    "请按 started -> completed|blocked 顺序记录步骤",
                )
        if event["action"] == "external_action":
            data = event["action_data"]
            if (
                data["action"] in {"jira_comment", "jira_worklog"}
                and data["status"] == "applied"
            ):
                raise blocked(
                    "jira_write_readback_probe_required",
                    f"外部动作 {data['action']} 尚无协议内专用 Jira 写后回读 probe",
                    "请保留为 not_applied 或 blocked；实现并验收绑定 operation、plan_id、幂等键、external_id 与内容摘要的 Runtime probe 后才能记录 applied",
                )
            if data["action"] not in manifest["permitted_external_actions"]:
                raise blocked(
                    "external_action_not_authorized",
                    f"外部动作 {data['action']} 不在 manifest 授权范围内",
                    "请停止执行；范围变化需要重新确认 manifest",
                )
            if data["status"] in {"applied", "unknown"} and event["authorization_reference"] != manifest["authorization"]["reference"]:
                raise blocked(
                    "external_action_authorization_mismatch",
                    "外部动作没有引用 manifest 的明确授权",
                    "请记录真实授权引用，不得读取隐式配置或会话猜测",
                )

    def _append_runtime_fact(
        self,
        manifest: Mapping[str, Any],
        action: str,
        action_data: Mapping[str, Any],
        summary: str,
        *,
        duration: float = 0.0,
    ) -> dict[str, Any]:
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            self._assert_open_state(paths)
            envelopes = self._read_journal(paths["events"])
            ordinal = len(envelopes) + 1
            step_id = f"runtime-{action}-{ordinal}"
            authorization = manifest["authorization"]["reference"]
            started = {
                "schema_version": SCHEMA_VERSION,
                "protocol": PROTOCOL,
                "event_id": f"{step_id}-started",
                "agentic_run_id": manifest["agent"]["agentic_run_id"],
                "step_id": step_id,
                "recorded_at": self._now(),
                "status": "started",
                "actor": "runtime",
                "action": "step",
                "duration_seconds": 0.0,
                "summary": f"开始：{summary}",
                "authorization_reference": authorization,
                "action_data": {},
                "evidence_origin": "runtime_probe",
            }
            completed = {
                "schema_version": SCHEMA_VERSION,
                "protocol": PROTOCOL,
                "event_id": f"{step_id}-completed",
                "agentic_run_id": manifest["agent"]["agentic_run_id"],
                "step_id": step_id,
                "recorded_at": self._now(),
                "status": "completed",
                "actor": "runtime",
                "action": action,
                "duration_seconds": round(max(0.0, duration), 6),
                "summary": summary,
                "authorization_reference": authorization,
                "action_data": dict(action_data),
                "evidence_origin": "runtime_probe",
            }
            validate_event(started)
            validate_event(completed)
            previous = envelopes[-1]["event_sha256"] if envelopes else None
            first = event_envelope(started, ordinal, previous)
            second = event_envelope(completed, ordinal + 1, first["event_sha256"])
            append_ndjson(paths["events"], first)
            append_ndjson(paths["events"], second)
            return {
                "recorded": True,
                "event_id": completed["event_id"],
                "sequence": second["sequence"],
                "event_sha256": second["event_sha256"],
                "journal_path": str(paths["events"]),
            }

    def _append_runtime_readback(
        self,
        manifest: Mapping[str, Any],
        observed_actions: Iterable[tuple[str, str]],
        action: str,
        action_data: Mapping[str, Any],
        summary: str,
        *,
        duration: float = 0.0,
    ) -> dict[str, Any]:
        """Atomically journal observed actions immediately before their Runtime readback."""
        observations = list(observed_actions)
        names = [name for name, _ in observations]
        if len(names) != len(set(names)):
            raise blocked(
                "external_action_binding_duplicate",
                "同一次 Runtime probe 不能重复绑定同一外部动作",
                "请移除重复 --bind-action 后重试",
            )
        for observed, target in observations:
            if observed not in manifest["permitted_external_actions"]:
                raise blocked(
                    "external_action_not_authorized",
                    f"外部动作 {observed} 不在 manifest 授权范围内",
                    "请停止执行；范围变化需要重新确认 manifest",
                )
            if not target.strip():
                raise blocked(
                    "external_action_target_required",
                    f"外部动作 {observed} 缺少脱敏目标",
                    "请停止并核对 Runtime probe 的目标绑定",
                )
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            self._assert_open_state(paths)
            envelopes = self._read_journal(paths["events"])
            ordinal = len(envelopes) + 1
            readback_ordinal = ordinal + 2 * len(observations)
            readback_event_id = f"runtime-{action}-{readback_ordinal}-completed"
            events: list[dict[str, Any]] = []
            for index, (observed, target) in enumerate(observations):
                events.extend(
                    self._runtime_event_pair(
                        manifest,
                        "external_action",
                        {
                            "action": observed,
                            "target": target,
                            "status": "applied",
                            "readback_event_id": readback_event_id,
                        },
                        f"Runtime 登记并等待写后回读：{observed}",
                        ordinal + 2 * index,
                    )
                )
            events.extend(
                self._runtime_event_pair(
                    manifest,
                    action,
                    action_data,
                    summary,
                    readback_ordinal,
                    duration=duration,
                )
            )
            previous = envelopes[-1]["event_sha256"] if envelopes else None
            appended: list[dict[str, Any]] = []
            for sequence, event in enumerate(events, start=ordinal):
                envelope = event_envelope(event, sequence, previous)
                append_ndjson(paths["events"], envelope)
                appended.append(envelope)
                previous = envelope["event_sha256"]
            completed = appended[-1]
            return {
                "recorded": True,
                "event_id": completed["event"]["event_id"],
                "sequence": completed["sequence"],
                "event_sha256": completed["event_sha256"],
                "bound_external_actions": names,
                "journal_path": str(paths["events"]),
            }

    def _runtime_event_pair(
        self,
        manifest: Mapping[str, Any],
        action: str,
        action_data: Mapping[str, Any],
        summary: str,
        ordinal: int,
        *,
        duration: float = 0.0,
    ) -> list[dict[str, Any]]:
        step_id = f"runtime-{action}-{ordinal}"
        common = {
            "schema_version": SCHEMA_VERSION,
            "protocol": PROTOCOL,
            "agentic_run_id": manifest["agent"]["agentic_run_id"],
            "step_id": step_id,
            "actor": "runtime",
            "authorization_reference": manifest["authorization"]["reference"],
            "evidence_origin": "runtime_probe",
        }
        started = {
            **common,
            "event_id": f"{step_id}-started",
            "recorded_at": self._now(),
            "status": "started",
            "action": "step",
            "duration_seconds": 0.0,
            "summary": f"开始：{summary}",
            "action_data": {},
        }
        completed = {
            **common,
            "event_id": f"{step_id}-completed",
            "recorded_at": self._now(),
            "status": "completed",
            "action": action,
            "duration_seconds": round(max(0.0, duration), 6),
            "summary": summary,
            "action_data": dict(action_data),
        }
        validate_event(started)
        validate_event(completed)
        return [started, completed]

    @staticmethod
    def _require_probe_permission(
        manifest: Mapping[str, Any], permission: str, probe: str
    ) -> None:
        if permission not in manifest["permitted_external_actions"]:
            raise blocked(
                "probe_permission_required",
                f"{probe} 需要 manifest 显式允许 {permission}",
                "请在任何外部读取前重新确认包含该只读动作的 manifest",
            )

    @staticmethod
    def _validate_probe_bind_actions(
        manifest: Mapping[str, Any],
        actions: Iterable[str],
        allowed: set[str],
        probe: str,
    ) -> list[str]:
        selected = list(actions)
        if len(selected) != len(set(selected)) or not set(selected) <= allowed:
            raise blocked(
                "probe_bind_action_invalid",
                f"{probe} 包含重复或不匹配的 --bind-action",
                "请只绑定本 probe 能通过后置回读证明的动作",
            )
        for action in selected:
            if action not in manifest["permitted_external_actions"]:
                raise blocked(
                    "external_action_not_authorized",
                    f"外部动作 {action} 不在 manifest 授权范围内",
                    "请停止执行；范围变化需要重新确认 manifest",
                )
        return selected

    @staticmethod
    def _external_action_target(
        action: str, readback: Mapping[str, Any]
    ) -> str | None:
        data = readback["action_data"]
        if action == "jira_read" and readback["action"] == "jira_readback":
            return f"jira:{data['issue_key']}"
        if action == "jira_read" and readback["action"] == "prohibition_baseline":
            return f"jira:{data['issue_key']}:prohibition-baseline"
        if action in {"jira_comment", "jira_worklog"} and readback[
            "action"
        ] == "jira_write_readback" and data["operation"] == action:
            return f"jira:{data['issue_key']}:{action}:{data['external_id']}"
        if action in {"git_commit", "git_remote_read", "git_push_task_branch"} and readback[
            "action"
        ] == "remote_branch_readback":
            return f"git:{data['repository_slug']}:{data['branch']}@{data['head_sha']}"
        if action == "git_remote_read" and readback["action"] == "prohibition_baseline":
            return f"git:{data['repository_slug']}:prohibition-baseline"
        if action in {"github_pr_create_or_update", "github_pr_read"} and readback[
            "action"
        ] == "pr_readback":
            return str(data["url"])
        if action == "github_pr_read" and readback["action"] == "prohibition_baseline":
            return f"github:{data['repository_slug']}:prohibition-baseline"
        return None

    def _completed_events(self, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
        paths = self._paths(manifest)
        with TaskLock(paths["lock"], timeout=self.lock_timeout):
            self._assert_open_state(paths)
            return [
                envelope["event"]
                for envelope in self._read_journal(paths["events"])
                if envelope["event"]["status"] == "completed"
            ]

    @staticmethod
    def _latest_action(events: list[dict[str, Any]], action: str) -> dict[str, Any] | None:
        matches = [event for event in events if event["action"] == action]
        return matches[-1] if matches else None

    @staticmethod
    def _validate_baseline_start(
        local_head_sha: str,
        task_branch_remote_sha: str | None,
        target_head_sha: str,
    ) -> None:
        expected_local_head_sha = task_branch_remote_sha or target_head_sha
        if local_head_sha == expected_local_head_sha:
            return
        start_source = (
            "远端任务分支"
            if task_branch_remote_sha is not None
            else "远端目标分支"
        )
        raise blocked(
            "prohibition_baseline_preexisting_commits",
            f"写入前本地任务分支 HEAD 不等于{start_source}，存在无法归因于本运行的预置提交",
            "请从对应远端 SHA 创建干净任务分支，并使用新的 agentic_run_id 重新确认 manifest",
        )

    def _git(self, root: Path, *arguments: str) -> str:
        result = self._git_result(root, *arguments)
        if result.returncode != 0:
            raise blocked(
                "git_probe_failed",
                f"Git 只读检查失败：git {' '.join(arguments[:2])}",
                "请修复业务仓库或远端访问后重新 probe",
            )
        return result.stdout

    def _git_result(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self._run_command(
            ["git", "-C", str(root), *arguments],
            cwd=root,
            timeout=60,
        )

    @staticmethod
    def _run_command(
        argv: list[str],
        *,
        cwd: Path,
        timeout: int | float,
        denied_environment_keys: set[str] | None = None,
        verification_repository_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        denied = {key.upper() for key in (denied_environment_keys or set())}
        home_context = (
            tempfile.TemporaryDirectory(prefix="ao-work-verification-home-")
            if verification_repository_root is not None
            else nullcontext(None)
        )
        with home_context as isolated_home:
            if verification_repository_root is not None:
                safe_environment = TaskRunProtocol._verification_environment(
                    verification_repository_root,
                    Path(isolated_home),
                )
            else:
                safe_environment = {
                    key: value
                    for key, value in os.environ.items()
                    if key.upper() not in denied
                    and not any(
                        term in key.upper()
                        for term in (
                            "TOKEN",
                            "SECRET",
                            "PASSWORD",
                            "CREDENTIAL",
                            "PRIVATE_KEY",
                        )
                    )
                }
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=safe_environment,
                start_new_session=True,
            )
            assert process.stdout is not None and process.stderr is not None
            streams = {process.stdout: bytearray(), process.stderr: bytearray()}
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            deadline = time.monotonic() + float(timeout)
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        TaskRunProtocol._terminate_process_group(process)
                        raise subprocess.TimeoutExpired(
                            argv,
                            timeout,
                            output=bytes(streams[process.stdout]),
                            stderr=bytes(streams[process.stderr]),
                        )
                    for key, _ in selector.select(min(remaining, 0.1)):
                        stream = key.fileobj
                        chunk = os.read(stream.fileno(), 65_536)
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        total = sum(len(buffer) for buffer in streams.values())
                        if total + len(chunk) > MAX_COMMAND_OUTPUT_BYTES:
                            TaskRunProtocol._terminate_process_group(process)
                            raise blocked(
                                "command_output_too_large",
                                "确定性命令输出超过 4 MiB 安全上限，Runtime 已终止整个命令进程组",
                                "请缩小项目工具输出；Runtime 不保存或回显原始超限内容",
                            )
                        streams[stream].extend(chunk)
                return subprocess.CompletedProcess(
                    argv,
                    process.wait(),
                    bytes(streams[process.stdout]).decode("utf-8", errors="replace"),
                    bytes(streams[process.stderr]).decode("utf-8", errors="replace"),
                )
            except BaseException:
                if process.poll() is None:
                    TaskRunProtocol._terminate_process_group(process)
                raise
            finally:
                selector.close()
                for stream in (process.stdout, process.stderr):
                    if not stream.closed:
                        stream.close()

    @staticmethod
    def _verification_environment(repository_root: Path, isolated_home: Path) -> dict[str, str]:
        root = repository_root.resolve()
        path_entries: list[str] = []
        for relative in (".venv/bin", "venv/bin", "node_modules/.bin"):
            candidate = root / relative
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            try:
                candidate.resolve().relative_to(root)
            except ValueError:
                continue
            path_entries.append(str(candidate.resolve()))
        for candidate in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"):
            if Path(candidate).is_dir():
                path_entries.append(candidate)

        xdg_config = isolated_home / ".config"
        xdg_cache = isolated_home / ".cache"
        xdg_data = isolated_home / ".local" / "share"
        for path in (xdg_config, xdg_cache, xdg_data):
            path.mkdir(parents=True, exist_ok=True)
        return {
            "AGENTIC_OPS_VERIFICATION_NETWORK_POLICY": "allowlist-only-no-sandbox",
            "ALL_PROXY": "http://127.0.0.1:9",
            "CARGO_NET_OFFLINE": "true",
            "CI": "true",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GONOSUMDB": "*",
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
            "GH_PROMPT_DISABLED": "1",
            "HOME": str(isolated_home),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "LANG": "C",
            "LC_ALL": "C",
            "NO_COLOR": "1",
            "NO_PROXY": "",
            "NPM_CONFIG_OFFLINE": "true",
            "PAGER": "cat",
            "PATH": os.pathsep.join(path_entries),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PNPM_CONFIG_OFFLINE": "true",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TERM": "dumb",
            "TZ": "UTC",
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_DATA_HOME": str(xdg_data),
            "YARN_ENABLE_NETWORK": "false",
        }

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (AttributeError, ProcessLookupError, PermissionError):
            process.terminate()
        try:
            process.wait(timeout=0.2)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, ProcessLookupError, PermissionError):
            process.kill()
        process.wait()

    def _reject_git_url_rewrites(self, root: Path) -> None:
        result = self._git_result(
            root,
            "config",
            "--show-origin",
            "--get-regexp",
            r"^url\..*\.(insteadOf|pushInsteadOf)$",
        )
        if result.returncode == 0 and result.stdout.strip():
            raise blocked(
                "git_url_rewrite_forbidden",
                "检测到 Git url.*.insteadOf/pushInsteadOf 改写，可信 Git probe 已停止",
                "请移除 URL 改写，确保 raw/effective remote 均直接指向 github.com",
            )
        if result.returncode not in {0, 1}:
            raise blocked(
                "git_probe_failed",
                "Git URL 改写配置检查失败",
                "请修复 Git 配置读取后重新 probe",
            )

    def _validate_git_remote_identity(
        self, root: Path, repository: Mapping[str, Any]
    ) -> str:
        remote = str(repository["remote_name"])
        self._reject_git_url_rewrites(root)
        raw_fetch_urls = self._git(
            root, "config", "--get-all", f"remote.{remote}.url"
        ).splitlines()
        effective_fetch_urls = self._git(
            root, "remote", "get-url", "--all", remote
        ).splitlines()
        effective_push_urls = self._git(
            root, "remote", "get-url", "--push", "--all", remote
        ).splitlines()
        raw_push = self._git_result(
            root, "config", "--get-all", f"remote.{remote}.pushurl"
        )
        if raw_push.returncode == 0:
            raw_push_urls = raw_push.stdout.splitlines()
        elif raw_push.returncode == 1:
            raw_push_urls = []
        else:
            raise blocked(
                "git_probe_failed",
                "Git 无法读取 raw push URL",
                "请修复 remote 配置后重新 probe",
            )
        urls = [
            *raw_fetch_urls,
            *effective_fetch_urls,
            *effective_push_urls,
            *raw_push_urls,
        ]
        if (
            len(raw_fetch_urls) != 1
            or len(effective_fetch_urls) != 1
            or len(effective_push_urls) != 1
            or len(raw_push_urls) > 1
            or any(
                parse_github_repository_url(url) != repository["slug"]
                for url in urls
            )
        ):
            raise blocked(
                "git_probe_origin_mismatch",
                "Git raw/effective fetch/push URL 数量或仓库身份与 manifest 不一致",
                "请停止执行并核对仓库 origin/pushurl；不接受 URL 改写后的等价地址",
            )
        return raw_fetch_urls[0]

    @staticmethod
    def _parse_remote_refs(output: str, required_prefix: str) -> list[dict[str, str]]:
        refs: dict[str, str] = {}
        for line in output.splitlines():
            if "\t" not in line:
                continue
            sha, name = line.split("\t", 1)
            if not name.startswith(required_prefix) or name in refs:
                raise blocked(
                    "prohibition_snapshot_invalid",
                    "远端 ref 快照包含重复或越界引用",
                    "请核对 Git 远端返回，不得使用不完整快照继续",
                )
            refs[name] = sha
        return [{"name": name, "sha": refs[name]} for name in sorted(refs)]

    @staticmethod
    def _remote_heads(output: str) -> dict[str, str]:
        heads: dict[str, str] = {}
        for line in output.splitlines():
            if "\t" not in line:
                continue
            sha, ref = line.split("\t", 1)
            if not ref.startswith("refs/heads/"):
                continue
            branch = ref.removeprefix("refs/heads/")
            if branch in heads:
                raise blocked(
                    "prohibition_snapshot_invalid",
                    "远端保护分支快照包含重复引用",
                    "请核对 Git 远端返回，不得使用不完整快照继续",
                )
            heads[branch] = sha
        return heads

    def _github_json_array(
        self, root: Path, argv: list[str], failure_message: str
    ) -> list[dict[str, Any]]:
        result = self._run_command(argv, cwd=root, timeout=60)
        if result.returncode != 0:
            raise blocked(
                "github_prohibition_probe_failed",
                failure_message,
                "请修复 GitHub 只读授权后重新执行可信 probe",
            )
        try:
            payload = json.loads(
                result.stdout,
                object_pairs_hook=self._reject_duplicate_json_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"invalid JSON constant: {value}")
                ),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise blocked(
                "github_prohibition_probe_invalid",
                f"{failure_message}：响应不是严格 JSON",
                "请修复项目认可的 gh 工具后重试",
            ) from error
        if not isinstance(payload, list) or not all(
            isinstance(item, dict) for item in payload
        ):
            raise blocked(
                "github_prohibition_probe_invalid",
                f"{failure_message}：响应结构不是对象数组",
                "请修复项目认可的 gh 工具后重试",
            )
        return payload

    @staticmethod
    def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    @staticmethod
    def _jira_status_category(issue: Any) -> str:
        status = issue.fields.get("status", {}) if isinstance(issue.fields, dict) else {}
        category = status.get("statusCategory", {}) if isinstance(status, dict) else {}
        if not isinstance(category, dict):
            return ""
        return str(category.get("key") or category.get("name") or "").strip()

    @staticmethod
    def _ci_status(raw: object) -> str:
        if not isinstance(raw, list) or not raw:
            return "not_configured"
        conclusions: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            value = str(item.get("conclusion") or item.get("state") or item.get("status") or "").upper()
            conclusions.append(value)
        failed = {
            "FAILURE",
            "FAILED",
            "ERROR",
            "CANCELLED",
            "TIMED_OUT",
            "ACTION_REQUIRED",
            "STALE",
            "STARTUP_FAILURE",
        }
        passed = {"SUCCESS", "NEUTRAL", "SKIPPED"}
        pending = {"PENDING", "QUEUED", "IN_PROGRESS", "EXPECTED", "WAITING", "REQUESTED"}
        if any(value in failed for value in conclusions):
            return "failed"
        if conclusions and all(value in passed for value in conclusions):
            return "passed"
        if conclusions and all(value in passed | pending for value in conclusions):
            return "pending"
        # Unknown non-empty conclusions must never be downgraded to pending.
        return "failed"

    @staticmethod
    def _stream_text(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value if isinstance(value, str) else ""

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _validate_closed_steps(self, events: list[dict[str, Any]]) -> None:
        steps: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            steps.setdefault(event["step_id"], []).append(event)
        for step_id, values in steps.items():
            started = [event for event in values if event["status"] == "started"]
            terminal = [
                event for event in values if event["status"] in {"completed", "blocked"}
            ]
            if len(started) != 1 or len(terminal) != 1:
                self._incomplete(f"步骤 {step_id} 未形成 started -> completed|blocked 闭环")

    def _load_open_manifest(self, manifest_value: str) -> dict[str, Any]:
        manifest_path = self._input_file(manifest_value, "manifest")
        supplied = validate_manifest(load_json_object(manifest_path, "manifest"))
        self._validate_workspace_binding(supplied)
        paths = self._paths(supplied)
        if not paths["manifest"].is_file():
            raise blocked(
                "task_run_not_open",
                "当前 task-run 尚未 open",
                "请先用同一份用户确认 manifest 执行 task-run open",
            )
        stored = validate_manifest(read_json(paths["manifest"]))
        if manifest_digest(stored) != manifest_digest(supplied):
            raise blocked(
                "manifest_changed_after_open",
                "传入 manifest 与 task-run 打开时的内容摘要不同",
                "请恢复已确认 manifest；任何范围变化都要使用新的 agentic_run_id",
            )
        return stored

    def _validate_workspace_binding(self, manifest: Mapping[str, Any]) -> None:
        try:
            config = read_json(self.workspace.config_path)
        except (OSError, ValueError) as error:
            raise blocked(
                "workspace_config_invalid",
                f"无法读取 developer 工作空间身份：{error}",
                "请修复 .agentic-ops/agent.json 后重试",
            ) from error
        workspace_root = Path(str(manifest["workspace"]["root"])).resolve()
        if workspace_root != self.workspace.root or str(workspace_root) != manifest["workspace"]["root"]:
            raise blocked(
                "manifest_workspace_mismatch",
                "manifest workspace.root 不是当前 developer 工作空间规范路径",
                "请使用 resolve_developer_workspace 返回的绝对 root 重新确认 manifest",
            )
        source = config.get("source_root")
        if not isinstance(source, str) or not source.strip():
            raise blocked(
                "workspace_source_root_missing",
                "agent.json 缺少 source_root",
                "请重新初始化 developer 业务项目工作空间",
            )
        resolved_source = Path(source).expanduser().resolve()
        repository_root = Path(str(manifest["repository"]["root"])).resolve()
        if repository_root != resolved_source or str(repository_root) != manifest["repository"]["root"]:
            raise blocked(
                "manifest_repository_mismatch",
                "manifest repository.root 与 agent.json source_root 不一致",
                "请以当前业务工作空间显式 source_root 重新确认 manifest",
            )
        expected = {
            ("agent", "agent_id"): config.get("agent_id"),
            ("agent", "project_profile"): config.get("project_profile"),
            ("issue", "project_key"): config.get("jira_project"),
            ("repository", "slug"): config.get("repository"),
        }
        for (section, field), configured in expected.items():
            if manifest[section][field] != configured:
                raise blocked(
                    "manifest_workspace_identity_mismatch",
                    f"manifest {section}.{field} 与 agent.json 不一致",
                    "请基于当前工作空间身份重新确认 manifest",
                )
        configured_execution_identity = config.get("execution_identity")
        if configured_execution_identity is not None and (
            not isinstance(configured_execution_identity, dict)
            or manifest["execution_identity"] != configured_execution_identity
        ):
            raise blocked(
                "manifest_execution_identity_mismatch",
                "manifest execution_identity 与工作空间研发员身份不一致",
                "请使用工作空间初始化时已确认的 Git/GitHub 执行身份重新生成 manifest",
            )
        profile = load_project_profile(
            self.install_root,
            str(config.get("project_profile", "")),
            workspace_root=self.workspace.root,
        )
        validate_workspace_project_binding(self.workspace, profile)
        self._validate_approved_plan_binding(manifest)

    def _validate_approved_plan_binding(self, manifest: Mapping[str, Any]) -> None:
        task_binding = manifest["task_binding"]
        approved_path = self._input_file(
            str(task_binding["approved_plan_file"]), "approved plan"
        )
        content = read_managed_text(
            approved_path,
            label="task_run_approved_plan",
            max_bytes=4 * 1024 * 1024,
        )
        assert content is not None
        observed = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if observed != task_binding["approved_plan_sha256"]:
            raise blocked(
                "approved_plan_digest_mismatch",
                "批准计划文件内容与 manifest 摘要不一致",
                "请停止执行；计划变化必须重新审阅并使用新的 agentic_run_id",
            )

    @staticmethod
    def _jira_issue_content_digest(issue: Any) -> str:
        return digest(
            {
                "issue_id": issue.issue_id,
                "key": issue.key,
                "project_key": issue.project_key,
                "summary": issue.summary,
                "status": issue.status,
                "issue_type": issue.issue_type,
                "assignee_account_id": issue.assignee,
                "description": issue.description,
            }
        )

    def _input_file(self, value: str, label: str) -> Path:
        supplied = Path(value)
        if supplied.is_absolute() or ".." in supplied.parts:
            raise blocked(
                "workspace_path_escape",
                f"{label} 路径必须是当前工作空间内相对路径：{value}",
                f"请把 {label} 放入业务项目 AI 工作空间，并传入相对路径",
            )
        candidate = self.workspace.root / supplied
        current = self.workspace.root
        for part in supplied.parts:
            current = current / part
            if current.is_symlink():
                raise blocked(
                    "workspace_symlink_forbidden",
                    f"{label} 路径包含符号链接：{value}",
                    "请使用工作空间内真实普通文件，不得通过 symlink 间接读取",
                )
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.workspace.root)
        except ValueError as error:
            raise blocked(
                "workspace_path_escape",
                f"{label} 路径越出当前工作空间：{value}",
                f"请把 {label} 放入业务项目 AI 工作空间",
            ) from error
        if not resolved.is_file():
            raise blocked(
                "workspace_file_not_found",
                f"{label} 文件不存在：{value}",
                f"请检查 {label} 相对路径后重试",
            )
        return resolved

    def _paths(self, manifest: Mapping[str, Any]) -> dict[str, Path]:
        root = (
            self.workspace.root
            / ".agentic-ops"
            / "tasks"
            / str(manifest["issue"]["key"])
            / "runs"
            / str(manifest["agent"]["agentic_run_id"])
            / "task-to-pr"
        )
        paths = {
            "root": root,
            "lock": root.parent / ".task-to-pr.lock",
            "manifest": root / "manifest.json",
            "events": root / "events.ndjson",
            "state": root / "state.json",
            "result": root / "result.json",
        }
        self._validate_storage_paths(paths)
        return paths

    def _validate_storage_paths(self, paths: Mapping[str, Path]) -> None:
        for label, candidate in paths.items():
            try:
                candidate.resolve().relative_to(self.workspace.root)
            except ValueError as error:
                raise blocked(
                    "workspace_path_escape",
                    f"task-run 受管路径越出工作空间：{label}",
                    "请停止使用该工作空间并交给 AgenticOps 维护者调查",
                ) from error
            relative = candidate.relative_to(self.workspace.root)
            current = self.workspace.root
            for part in relative.parts:
                current = current / part
                if current.is_symlink():
                    raise blocked(
                        "workspace_symlink_forbidden",
                        f"task-run 受管路径包含符号链接：{current}",
                        "请移除受管状态目录中的 symlink，并核对是否发生路径篡改",
                    )

    def _read_journal(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        envelopes: list[dict[str, Any]] = []
        previous: str | None = None
        try:
            lines = read_text(path).splitlines()
        except OSError as error:
            raise blocked(
                "audit_journal_invalid",
                f"无法读取 task-run 审计日志：{error}",
                "请停止使用该运行并交给 AgenticOps 维护者调查",
            ) from error
        for index, line in enumerate(lines, start=1):
            try:
                envelope = parse_json_text(line)
            except (json.JSONDecodeError, ValueError) as error:
                raise blocked(
                    "audit_journal_invalid",
                    f"审计日志第 {index} 行不是有效 JSON",
                    "请停止使用该运行并交给 AgenticOps 维护者调查",
                ) from error
            if not isinstance(envelope, dict) or set(envelope) != {
                "sequence",
                "previous_event_sha256",
                "event_sha256",
                "event",
            }:
                self._invalid_journal(index)
            event = validate_event(envelope["event"])
            expected = event_envelope(event, index, previous)
            if envelope != expected:
                self._invalid_journal(index)
            envelopes.append(expected)
            previous = expected["event_sha256"]
        return envelopes

    def _assert_open_state(self, paths: Mapping[str, Path]) -> None:
        state = read_json(paths["state"])
        if state.get("status") != "open":
            raise blocked(
                "task_run_already_finalized",
                "当前 task-run 不再接受新事件",
                "请读取现有 result.json；新执行必须使用新的 agentic_run_id",
            )

    def _invalid_journal(self, line: int) -> None:
        raise blocked(
            "audit_journal_invalid",
            f"审计日志第 {line} 行的 hash chain 或字段不合法",
            "请停止使用该运行并交给 AgenticOps 维护者调查",
        )

    def _incomplete(self, detail: str) -> None:
        raise RuntimeErrorResult(
            code="task_run_incomplete",
            message=f"task-run 尚不能 finalize：{detail}",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            retry_safe=True,
            required_human_action="请补齐真实事件、回读和复盘后再次 finalize；不要伪造外部事实",
        )

    @staticmethod
    def _latest(events: list[dict[str, Any]]) -> dict[str, Any] | None:
        return events[-1] if events else None

    @staticmethod
    def _ids(events: Iterable[Mapping[str, Any]]) -> set[str]:
        return {str(event["event_id"]) for event in events}

    @staticmethod
    def _all_retrospective_refs(data: Mapping[str, Any]) -> set[str]:
        result: set[str] = set()
        for field in (
            "quality_finding_event_ids",
            "human_intervention_event_ids",
            "failure_event_ids",
            "retry_event_ids",
            "waiting_event_ids",
            "ordered_improvement_event_ids",
        ):
            result.update(data[field])
        for review in data["category_reviews"]:
            result.update(review["source_event_ids"])
        return result

    @staticmethod
    def _envelopes_for(
        envelopes: list[dict[str, Any]], events: Iterable[Mapping[str, Any]]
    ) -> list[dict[str, Any]]:
        event_ids = {event["event_id"] for event in events}
        return [
            envelope
            for envelope in envelopes
            if envelope["event"]["event_id"] in event_ids
        ]

    @staticmethod
    def _envelope_for(
        envelopes: list[dict[str, Any]], event: Mapping[str, Any]
    ) -> dict[str, Any]:
        for envelope in envelopes:
            if envelope["event"]["event_id"] == event["event_id"]:
                return envelope
        raise AssertionError("validated retrospective envelope is missing")

    @staticmethod
    def _open_result(
        paths: Mapping[str, Path], manifest: Mapping[str, Any], *, created: bool
    ) -> dict[str, Any]:
        return {
            "created": created,
            "manifest_sha256": manifest_digest(manifest),
            "protocol_root": str(paths["root"]),
            "journal_path": str(paths["events"]),
            "external_execution": False,
        }

    @staticmethod
    def _finalize_result(
        paths: Mapping[str, Path], result: Mapping[str, Any], *, created: bool
    ) -> dict[str, Any]:
        return {
            "created": created,
            "result_status": result["status"],
            "result_sha256": result["result_sha256"],
            "result_path": str(paths["result"]),
            "external_execution": False,
        }


def _porcelain_paths(status: str) -> list[str]:
    """解析 git status --porcelain=v1 输出为变更路径（含 rename 目标）。"""
    paths: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        # rename/copy: "R  old -> new" 或 "R  old -> new (similarity N%)"
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip()
        if entry:
            paths.append(entry)
    return paths


def _stderr_tail(stderr: str | None, limit: int = 400) -> str:
    return (stderr or "")[-limit:]
