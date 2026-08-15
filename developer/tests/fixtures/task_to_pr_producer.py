#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ao_work.config import load_project_profile
from ao_work.config.model import JiraConnection
from ao_work.jira.adf import markdown_to_adf
from ao_work.jira.model import JiraComment, JiraIssue, JiraWorklog, plain_text
from ao_work.jira.service import JiraService, WritePlan, build_write_attempt
from ao_work.output import RuntimeErrorResult
from ao_work.task_run.protocol import manifest_digest
from ao_work.task_run.service import TaskRunProtocol
from ao_work.workspace import resolve_developer_workspace


ISSUE_KEY = "TAP-12289"
ISSUE_ID = "12289"
AGENT_ID = "harsen-mini-test-bot"
CONFIRMED_BY = "harsen"
ACCOUNT_ID = "jira-account-1"
QUALITY_CATEGORIES = [
    "automation_gap",
    "manual_friction",
    "output_quality",
    "unreasonable_process",
]


class FakeJiraClient:
    def __init__(self, issue: JiraIssue) -> None:
        self.issue = issue
        self.comment_records: list[JiraComment] = []
        self.worklog_records: list[JiraWorklog] = []

    def current_user_details(self) -> dict[str, str]:
        return {"account_id": ACCOUNT_ID, "display_name": "测试研发员"}

    def current_user(self) -> str:
        return ACCOUNT_ID

    def field_metadata(self) -> list[dict[str, str]]:
        return [{"id": "customfield_10001", "name": "Agentic ID"}]

    def get_issue(self, _issue_key: str) -> JiraIssue:
        return self.issue

    def comments(self, _issue_key: str) -> list[JiraComment]:
        return list(self.comment_records)

    def worklogs(self, _issue_key: str) -> list[JiraWorklog]:
        return list(self.worklog_records)

    def add_comment(self, _issue_key: str, markdown: str) -> str:
        comment_id = str(9000 + len(self.comment_records) + 1)
        self.comment_records.append(
            JiraComment(
                comment_id=comment_id,
                body=plain_text(markdown_to_adf(markdown)),
                standalone_lines=frozenset(
                    line.strip() for line in markdown.splitlines() if line.strip()
                ),
            )
        )
        return comment_id

    def add_worklog(
        self,
        _issue_key: str,
        *,
        time_spent_seconds: int,
        started: str,
        markdown: str,
    ) -> str:
        worklog_id = str(9100 + len(self.worklog_records) + 1)
        self.worklog_records.append(
            JiraWorklog(
                worklog_id=worklog_id,
                body=plain_text(markdown_to_adf(markdown)),
                time_spent_seconds=time_spent_seconds,
                started=started,
                standalone_lines=frozenset(
                    line.strip() for line in markdown.splitlines() if line.strip()
                ),
            )
        )
        return worklog_id


class DeveloperProducer:
    """Developer-owned harness; all audit artifacts are produced by public Runtime APIs."""

    def __init__(self, root: Path, *, run_id: str) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True)
        self.workspace = self.root / "developer-workspace"
        self.repository = self.root / "business-repository"
        self.install = self.root / "agentic-ops-install"
        self.workspace.mkdir()
        self.repository.mkdir()
        self._prepare_install()
        self._prepare_workspace()
        self.issue = JiraIssue(
            issue_id=ISSUE_ID,
            key=ISSUE_KEY,
            project_key="TAP",
            summary="跨工作面合同测试任务",
            status="正在进行",
            issue_type="任务",
            assignee=ACCOUNT_ID,
            description={
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "验证真实产包验收。"}],
                    }
                ],
            },
            fields={
                "status": {
                    "name": "正在进行",
                    "statusCategory": {
                        "key": "indeterminate",
                        "name": "In Progress",
                    },
                },
                "customfield_10001": AGENT_ID,
            },
        )
        self.run_id = run_id
        self.manifest_relative = "inputs/manifest.json"
        self._prepare_manifest()

    @property
    def managed_manifest_path(self) -> Path:
        return (
            self.workspace
            / ".agentic-ops"
            / "tasks"
            / ISSUE_KEY
            / "runs"
            / self.run_id
            / "task-to-pr"
            / "manifest.json"
        )

    def protocol(self) -> TaskRunProtocol:
        return TaskRunProtocol(
            resolve_developer_workspace(str(self.workspace)),
            install_root=self.install,
            lock_timeout=1,
        )

    def produce_early_blocked(self) -> dict[str, object]:
        protocol = self.protocol()
        protocol.open(self.manifest_relative)
        live_identity = {
            "account_id": "different-jira-account",
            "display_name": "错误账户",
        }
        fake_client = SimpleNamespace(
            current_user_details=lambda: live_identity,
            get_issue=lambda _key: self.issue,
        )
        with (
            mock.patch(
                "ao_work.task_run.service.load_jira_context",
                return_value=self.jira_context(),
            ),
            mock.patch(
                "ao_work.task_run.service.UrllibJiraTransport",
                return_value=object(),
            ),
            mock.patch(
                "ao_work.task_run.service.JiraClient",
                return_value=fake_client,
            ),
        ):
            try:
                protocol.probe_jira(self.manifest_relative)
            except RuntimeErrorResult as error:
                probe_error = error
            else:
                raise AssertionError("Jira 账户漂移必须由 developer Runtime 阻断")

        failure_id = self.record_pair(
            protocol,
            step_id="jira-precondition",
            action="failure",
            action_data={
                "code": probe_error.code,
                "detail": "Jira 当前授权账户与工作空间固化账户不一致。",
                "retry_safe": True,
            },
        )
        self.record_pair(
            protocol,
            step_id="retrospective",
            action="retrospective",
            action_data={
                "reviewed_categories": QUALITY_CATEGORIES,
                "category_reviews": self.category_reviews(
                    failure_id,
                    {"automation_gap": [failure_id]},
                ),
                "quality_finding_event_ids": [],
                "human_intervention_event_ids": [],
                "failure_event_ids": [failure_id],
                "retry_event_ids": [],
                "waiting_event_ids": [],
                "ordered_improvement_event_ids": [],
                "residual_risks": ["Jira 授权账户尚未恢复为工作空间绑定账户。"],
                "summary": "Jira 身份前置检查失败，未执行任何外部写入。",
            },
        )
        protocol.record_unverified_prohibitions(self.manifest_relative)
        finalized = protocol.finalize(
            self.manifest_relative,
            "blocked",
            "请重新授权当前业务项目工作空间的 Jira 账户后，新建运行重试。",
        )
        return {
            "case": "early-blocked",
            "probe_error_code": probe_error.code,
            "producer_result_status": finalized["result_status"],
            "manifest_path": str(self.managed_manifest_path),
            "result_path": str(finalized["result_path"]),
        }

    def produce_ready(self) -> dict[str, object]:
        base_sha = self._prepare_git_repository()
        head_sha: str | None = None
        remote_task_sha: str | None = None
        pr_exists = False
        protocol = self.protocol()
        protocol.open(self.manifest_relative)
        fake_client = FakeJiraClient(self.issue)
        context = self.jira_context()
        profile = context.profile
        jira_service = JiraService(profile, fake_client)
        original_git_result = protocol._git_result
        original_run_command = protocol._run_command
        verification_attempt = 0

        def fake_git_result(
            root: Path, *arguments: str
        ) -> subprocess.CompletedProcess[str]:
            if arguments[:2] == ("ls-remote", "--tags"):
                return subprocess.CompletedProcess(arguments, 0, "", "")
            if arguments[:2] == ("ls-remote", "--heads"):
                refs = {
                    "refs/heads/main": base_sha,
                    "refs/heads/develop": base_sha,
                }
                if remote_task_sha is not None:
                    refs[
                        f"refs/heads/{self.manifest['repository']['task_branch']}"  # type: ignore[index]
                    ] = remote_task_sha
                output = "".join(
                    f"{refs[reference]}\t{reference}\n"
                    for reference in arguments[3:]
                    if reference in refs
                )
                return subprocess.CompletedProcess(arguments, 0, output, "")
            return original_git_result(root, *arguments)

        def fake_run_command(
            argv: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            nonlocal verification_attempt
            verification_command = self.manifest["verification"][0]["command"]  # type: ignore[index]
            if argv == verification_command:
                verification_attempt += 1
                return subprocess.CompletedProcess(
                    argv,
                    1 if verification_attempt == 1 else 0,
                    "",
                    "首次验证失败" if verification_attempt == 1 else "",
                )
            if argv[:3] == ["gh", "release", "list"]:
                return subprocess.CompletedProcess(argv, 0, "[]", "")
            if argv[:3] == ["gh", "pr", "list"]:
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    "[]" if not pr_exists else json.dumps([self._pr_payload(head_sha)]),
                    "",
                )
            if argv[:3] == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(argv, 0, "harsen-test\n", "")
            if argv[:3] == ["gh", "pr", "view"]:
                if not pr_exists:
                    return subprocess.CompletedProcess(argv, 1, "", "PR 不存在")
                payload = self._pr_payload(head_sha)
                return subprocess.CompletedProcess(
                    argv, 0, json.dumps(payload), ""
                )
            if (
                argv[:2] == ["gh", "api"]
                and len(argv) > 2
                and "/compare/" in argv[2]
            ):
                return subprocess.CompletedProcess(argv, 0, "behind\n", "")
            if argv and argv[0] == "gh":
                raise AssertionError(f"未模拟的 GitHub 调用：{argv}")
            return original_run_command(argv, **kwargs)

        git_environment = {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }
        with (
            mock.patch.dict(os.environ, git_environment, clear=False),
            mock.patch(
                "ao_work.task_run.service.load_jira_context",
                return_value=context,
            ),
            mock.patch(
                "ao_work.task_run.service.UrllibJiraTransport",
                return_value=object(),
            ),
            mock.patch(
                "ao_work.task_run.service.JiraClient",
                return_value=fake_client,
            ),
            mock.patch.object(
                protocol, "_git_result", side_effect=fake_git_result
            ),
            mock.patch.object(
                protocol, "_run_command", side_effect=fake_run_command
            ),
        ):
            protocol.probe_prohibition_baseline(self.manifest_relative)
            jira_readback = protocol.probe_jira(self.manifest_relative)
            self._apply_and_probe_jira_writes(
                protocol,
                jira_service,
            )
            head_sha = self._create_task_commit()
            protocol.verify(self.manifest_relative, "unit")
            failure_id = self.record_pair(
                protocol,
                step_id="verification-failure",
                action="failure",
                action_data={
                    "code": "verification_failed",
                    "detail": "首次固定验证返回非零退出码。",
                    "retry_safe": True,
                },
            )
            protocol.verify(self.manifest_relative, "unit")
            retry_id = self.record_pair(
                protocol,
                step_id="verification-retry",
                action="retry",
                action_data={
                    "failure_event_id": failure_id,
                    "attempt": 1,
                    "outcome": "succeeded",
                },
            )
            retry_finding_id = self.record_pair(
                protocol,
                step_id="verification-retry-finding",
                action="quality_finding",
                action_data={
                    "category": "automation_gap",
                    "detail": "固定验证首次失败后才发现并重试，缺少更早的确定性前置检查。",
                    "evidence_reference": failure_id,
                    "impact": "产生一次额外失败和重试，拉长任务交付路径。",
                    "root_cause_hypothesis": "任务执行前没有覆盖该失败条件的轻量预检。",
                    "reproduction": "按已确认 manifest 首次执行 unit 验证并观察非零退出码。",
                    "sanitized_example": "unit attempt=1 failed; attempt=2 passed",
                    "improvement_candidate": "在 Skill 中增加与固定验证一致的轻量前置检查，并保留失败审计。",
                    "suggested_asset": "skill",
                    "benefit": "更早发现可恢复问题，减少完整验证的失败重跑。",
                    "risk": "前置检查若偏离固定验证可能产生错误通过。",
                    "frequency": "首次验证失败并成功重试时",
                },
            )
            # 离线 fixture 在此模拟受控 push 已完成；Runtime 随后从远端回读变化。
            remote_task_sha = head_sha
            protocol.probe_git(
                self.manifest_relative,
                ("git_commit", "git_push_task_branch"),
            )
            # PR 只在 Git readback 之后出现，基线明确证明此前没有 open PR。
            pr_exists = True
            protocol.probe_pr(
                self.manifest_relative,
                ("github_pr_create_or_update",),
            )
            self.record_pair(
                protocol,
                step_id="retrospective",
                action="retrospective",
                action_data={
                    "reviewed_categories": QUALITY_CATEGORIES,
                    "category_reviews": self.category_reviews(
                        str(jira_readback["event_id"]),
                        {
                            "automation_gap": [
                                retry_finding_id,
                                failure_id,
                                retry_id,
                            ]
                        },
                    ),
                    "quality_finding_event_ids": [retry_finding_id],
                    "human_intervention_event_ids": [],
                    "failure_event_ids": [failure_id],
                    "retry_event_ids": [retry_id],
                    "waiting_event_ids": [],
                    "ordered_improvement_event_ids": [retry_finding_id],
                    "residual_risks": [
                        "GitHub actor 只证明 probe 时的 gh 会话，不证明远端 push actor。"
                    ],
                    "summary": "真实 Jira 双写、验证重试、Git 和 PR 回读均已完成。",
                },
            )
            protocol.probe_prohibitions(self.manifest_relative)
            finalized = protocol.finalize(
                self.manifest_relative,
                "ready_for_pr_review",
                "请研发工程师审查 PR，不执行合并。",
            )
        return {
            "case": "ready",
            "producer_result_status": finalized["result_status"],
            "manifest_path": str(self.managed_manifest_path),
            "result_path": str(finalized["result_path"]),
            "verification_attempts": verification_attempt,
        }

    def _apply_and_probe_jira_writes(
        self,
        protocol: TaskRunProtocol,
        jira_service: JiraService,
    ) -> None:
        comment_plan = jira_service.plan_comment(
            ISSUE_KEY,
            "cross-plane:comment:1",
            "evidence",
            "已完成跨工作面真实结果包验证并提交审查证据。",
            agentic_run_id=self.run_id,
        )
        comment_file = self._write_jira_plan("comment.json", comment_plan.to_dict())
        jira_service.apply_comment(
            comment_plan,
            comment_plan.plan_id,
            begin_create=self._write_jira_attempt(comment_file),
        )
        protocol.probe_jira_write(
            self.manifest_relative,
            comment_file,
            comment_plan.plan_id,
        )

        worklog_plan = jira_service.plan_worklog(
            ISSUE_KEY,
            "cross-plane:worklog:1",
            "实现与验证",
            "完成跨工作面产包、验收和失败重试验证。",
            1800,
            "2026-08-14T01:00:00+00:00",
            True,
            agentic_run_id=self.run_id,
            included_work=[
                {"description": "完成代码实现", "seconds": 1200},
                {"description": "完成验证", "seconds": 600},
            ],
            excluded_waiting_categories=["等待人工确认", "等待 CI"],
        )
        worklog_file = self._write_jira_plan(
            "worklog.json", worklog_plan.to_dict()
        )
        jira_service.apply_worklog(
            worklog_plan,
            worklog_plan.plan_id,
            begin_create=self._write_jira_attempt(worklog_file),
        )
        protocol.probe_jira_write(
            self.manifest_relative,
            worklog_file,
            worklog_plan.plan_id,
        )

    def _write_jira_plan(self, name: str, payload: object) -> str:
        relative = (
            Path(".agentic-ops")
            / "tasks"
            / ISSUE_KEY
            / "runs"
            / self.run_id
            / "jira-plans"
            / name
        )
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, payload)
        return relative.as_posix()

    def _write_jira_attempt(self, plan_file: str):
        def begin(plan: WritePlan):
            attempt = build_write_attempt(
                plan,
                str(self.manifest["authorization"]["reference"]),  # type: ignore[index]
                request_started_at="2026-08-14T01:30:00+00:00",
            )
            self._write_json(
                self.workspace / f"{plan_file}.attempt.json",
                attempt.to_dict(),
            )
            return attempt

        return begin

    def _prepare_git_repository(self) -> str:
        environment = {
            **{
                key: os.environ[key]
                for key in ("PATH", "TMPDIR", "LANG", "LC_ALL")
                if key in os.environ
            },
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }

        def git(*arguments: str) -> str:
            completed = subprocess.run(
                ["git", "-C", str(self.repository), *arguments],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            return completed.stdout.strip()

        git("init", "-b", "develop")
        git("config", "user.name", "Harsen Test")
        git("config", "user.email", "harsen.test@example.test")
        (self.repository / "README.md").write_text(
            "# Fixture\n", encoding="utf-8"
        )
        git("add", "README.md")
        git("commit", "-m", "建立测试基线")
        base_sha = git("rev-parse", "HEAD")
        git("branch", "main")
        task_branch = str(self.manifest["repository"]["task_branch"])  # type: ignore[index]
        git("checkout", "-b", task_branch)
        git("remote", "add", "origin", "git@github.com:tapdata/tapdata.git")
        return base_sha

    def _create_task_commit(self) -> str:
        environment = {
            **{
                key: os.environ[key]
                for key in ("PATH", "TMPDIR", "LANG", "LC_ALL")
                if key in os.environ
            },
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
        }

        def git(*arguments: str) -> str:
            completed = subprocess.run(
                ["git", "-C", str(self.repository), *arguments],
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            )
            return completed.stdout.strip()

        source = self.repository / "src"
        source.mkdir()
        (source / "change.py").write_text(
            "VALUE = 'cross-workplane'\n", encoding="utf-8"
        )
        git("add", "src/change.py")
        git("commit", "-m", "完成跨工作面测试变更")
        return git("rev-parse", "HEAD")

    def _pr_payload(self, head_sha: str | None) -> dict[str, object]:
        if head_sha is None:
            raise AssertionError("PR fixture 必须绑定已创建的最终提交")
        return {
            "number": 12289,
            "url": "https://github.com/tapdata/tapdata/pull/12289",
            "state": "OPEN",
            "isDraft": False,
            "mergedAt": None,
            "headRefName": self.manifest["repository"]["task_branch"],  # type: ignore[index]
            "headRefOid": head_sha,
            "baseRefName": "develop",
            "reviewDecision": "",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        }

    @staticmethod
    def category_reviews(
        evidence_reference: str,
        source_by_category: dict[str, list[str]] | None = None,
    ) -> list[dict[str, object]]:
        source_by_category = source_by_category or {}
        rationales = {
            "automation_gap": "本次运行未发现除已记录失败外的自动化能力缺口。",
            "manual_friction": "本次运行未发生额外人工干预。",
            "output_quality": "阻塞结果已完整记录失败事实和下一步动作。",
            "unreasonable_process": "前置身份检查在外部写入前停止，流程符合预期。",
        }
        return [
            {
                "category": category,
                "outcome": (
                    "finding" if source_by_category.get(category) else "no_finding"
                ),
                "rationale": (
                    "已逐项记录该分类的来源事件和改进候选。"
                    if source_by_category.get(category)
                    else rationales[category]
                ),
                "evidence_references": (
                    source_by_category.get(category) or [evidence_reference]
                ),
                "source_event_ids": source_by_category.get(category, []),
            }
            for category in QUALITY_CATEGORIES
        ]

    def jira_context(self) -> SimpleNamespace:
        profile = load_project_profile(
            self.install,
            "tapdata",
            workspace_root=self.workspace,
        )
        return SimpleNamespace(
            connection=JiraConnection(
                connection_id="test-jira",
                base_url="https://jira.example.test",
                email_env="TEST_JIRA_EMAIL",
                token_env="TEST_JIRA_TOKEN",
            ),
            profile=profile,
            require_credentials=lambda: (
                "developer@example.test",
                "test-only-redacted-token",
            ),
        )

    def record_pair(
        self,
        protocol: TaskRunProtocol,
        *,
        step_id: str,
        action: str,
        action_data: dict[str, object],
        terminal_status: str = "completed",
    ) -> str:
        authorization = self.manifest["authorization"]["reference"]
        recorded_at = datetime.now(timezone.utc).isoformat()
        started = {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "event_id": f"event-{step_id}-started",
            "agentic_run_id": self.run_id,
            "step_id": step_id,
            "recorded_at": recorded_at,
            "status": "started",
            "actor": "ai",
            "action": "step",
            "duration_seconds": 0,
            "summary": f"开始：{step_id}",
            "authorization_reference": authorization,
            "action_data": {},
            "evidence_origin": "imported",
        }
        terminal_id = f"event-{step_id}-{terminal_status}"
        terminal = {
            **started,
            "event_id": terminal_id,
            "status": terminal_status,
            "action": action,
            "duration_seconds": 1,
            "summary": f"完成：{step_id}",
            "action_data": action_data,
        }
        for suffix, event in (("started", started), (terminal_status, terminal)):
            relative = f"inputs/event-{step_id}-{suffix}.json"
            self._write_json(self.workspace / relative, event)
            protocol.record(self.manifest_relative, relative)
        return terminal_id

    def _prepare_install(self) -> None:
        connection = (
            self.install
            / "developer"
            / "standards"
            / "connections"
            / "test-jira.yaml"
        )
        profile = (
            self.install
            / "developer"
            / "standards"
            / "projects"
            / "tapdata"
            / "profile.yaml"
        )
        connection.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        connection.write_text(
            "schema_version: 1\n"
            "connection_id: test-jira\n"
            "base_url: https://jira.example.test\n"
            "auth:\n"
            "  email_env: TEST_JIRA_EMAIL\n"
            "  token_env: TEST_JIRA_TOKEN\n",
            encoding="utf-8",
        )
        profile.write_text(
            "schema_version: 1\n"
            "profile_id: tapdata\n"
            "connection_id: test-jira\n"
            "jira:\n"
            "  project_key: TAP\n"
            "  issue_types: [任务]\n"
            "  task_query: project = TAP\n"
            "repositories:\n"
            "  default: tapdata/tapdata\n"
            "fields:\n"
            "  agentic_id:\n"
            "    source: jira_field\n"
            "    jira_field: customfield_10001\n"
            "    state: active\n"
            "    writable: false\n"
            "statuses:\n"
            "  正在进行: implementation\n",
            encoding="utf-8",
        )

    def _prepare_workspace(self) -> None:
        state = self.workspace / ".agentic-ops"
        inputs = self.workspace / "inputs"
        state.mkdir()
        inputs.mkdir()
        self._write_json(
            state / "agent.json",
            {
                "schema_version": 3,
                "workplane": "developer",
                "agent_id": AGENT_ID,
                "project_profile": "tapdata",
                "connection_id": "test-jira",
                "jira_base_url": "https://jira.example.test",
                "jira_site": "jira.example.test",
                "jira_account_id": ACCOUNT_ID,
                "jira_project": "TAP",
                "source_root": str(self.repository.resolve()),
                "repository": "tapdata/tapdata",
            },
        )
        overlay = state / "profiles" / "tapdata.local.yaml"
        overlay.parent.mkdir()
        overlay.write_text(
            "workspace:\n"
            f"  source_root: {self.repository.resolve()}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )

    def _prepare_manifest(self) -> None:
        approved_plan = self.workspace / "inputs" / "approved-plan.md"
        approved_plan.write_text(
            "# 已确认实施计划\n\n- 完成跨工作面合同验证。\n",
            encoding="utf-8",
        )
        approved_plan_sha256 = hashlib.sha256(
            approved_plan.read_bytes()
        ).hexdigest()
        issue_content_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "assignee_account_id": self.issue.assignee,
                    "description": self.issue.description,
                    "issue_id": self.issue.issue_id,
                    "issue_type": self.issue.issue_type,
                    "key": self.issue.key,
                    "project_key": self.issue.project_key,
                    "status": self.issue.status,
                    "summary": self.issue.summary,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        authorization_reference = (
            f"user-confirmation:{ISSUE_KEY}:{self.run_id}:{approved_plan_sha256}"
        )
        self.manifest: dict[str, object] = {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "workspace": {"root": str(self.workspace.resolve())},
            "issue": {"key": ISSUE_KEY, "id": ISSUE_ID, "project_key": "TAP"},
            "jira": {
                "base_url": "https://jira.example.test",
                "account_id": ACCOUNT_ID,
                "assignee_account_id": ACCOUNT_ID,
                "status_mapping": {"正在进行": "implementation"},
                "allowed_status_categories": ["indeterminate"],
                "agentic_id_field": "customfield_10001",
            },
            "agent": {
                "agent_id": AGENT_ID,
                "project_profile": "tapdata",
                "agentic_run_id": self.run_id,
            },
            "task_binding": {
                "issue_content_sha256": issue_content_sha256,
                "approved_plan_file": "inputs/approved-plan.md",
                "approved_plan_sha256": approved_plan_sha256,
            },
            "execution_identity": {
                "git_author_name": "Harsen Test",
                "git_author_email": "harsen.test@example.test",
                "git_committer_name": "Harsen Test",
                "git_committer_email": "harsen.test@example.test",
                "github_actor_login": "harsen-test",
            },
            "repository": {
                "root": str(self.repository.resolve()),
                "slug": "tapdata/tapdata",
                "remote_name": "origin",
                "base_branch": "develop",
                "task_branch": f"{AGENT_ID}/{ISSUE_KEY}/cross-plane-test",
                "target_branch": "develop",
                "protected_branches": ["main", "develop"],
            },
            "scope": {"included": ["src/**"], "excluded": []},
            "verification": [
                {
                    "id": "unit",
                    "command": ["python3", "-m", "unittest", "discover"],
                    "working_directory": ".",
                    "timeout_seconds": 30,
                }
            ],
            "pr_endpoint": {
                "provider": "github",
                "repository_slug": "tapdata/tapdata",
                "target_branch": "develop",
                "ci_policy": "require_passed",
            },
            "permitted_external_actions": [
                "jira_read",
                "jira_comment",
                "jira_worklog",
                "git_commit",
                "git_remote_read",
                "git_push_task_branch",
                "github_pr_create_or_update",
                "github_pr_read",
            ],
            "authorization": {
                "reference": authorization_reference,
                "confirmed_by": CONFIRMED_BY,
                "confirmed_at": "2026-08-14T00:00:00+08:00",
                "confirmed_manifest_sha256": "",
            },
        }
        self.manifest["authorization"]["confirmed_manifest_sha256"] = manifest_digest(  # type: ignore[index]
            self.manifest
        )
        self._write_json(self.workspace / self.manifest_relative, self.manifest)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("early-blocked", "ready"), required=True)
    parser.add_argument("--root", required=True)
    arguments = parser.parse_args()
    run_id = (
        "run-TAP-12289-cross-plane-ready"
        if arguments.case == "ready"
        else "run-TAP-12289-cross-plane-blocked"
    )
    producer = DeveloperProducer(
        Path(arguments.root),
        run_id=run_id,
    )
    result = (
        producer.produce_ready()
        if arguments.case == "ready"
        else producer.produce_early_blocked()
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
