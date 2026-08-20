from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ao_work.config.model import ProjectProfile
from ao_work.jira.adf import markdown_to_adf
from ao_work.jira.model import JiraComment, JiraIssue, JiraWorklog, plain_text
from ao_work.jira.service import JiraService, build_write_attempt
from ao_work.output import RuntimeErrorResult
from ao_work.task_run.protocol import (
    QUALITY_CATEGORIES,
    manifest_digest,
    validate_event,
    validate_manifest,
    verification_digest,
)
from ao_work.task_run.service import TaskRunProtocol
from ao_work.task_state.io import read_json
from ao_work.work_cli import main


class TrustedTaskRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.source = root / "source"
        self.workspace.mkdir()
        self.source.mkdir()
        self.install = root / "install"
        state = self.workspace / ".agentic-ops"
        state.mkdir()
        (state / "agent.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "workplane": "developer",
                    "agent_id": "harsen-mini-test-bot",
                    "project_profile": "tapdata",
                    "jira_project": "TAP",
                    "connection_id": "tapdata-cloud",
                    "jira_base_url": "https://tapdata.atlassian.net",
                    "jira_site": "tapdata.atlassian.net",
                    "jira_account_id": "jira-account-1",
                    "source_root": str(self.source.resolve()),
                    "repository": "tapdata/tapdata",
                    "execution_identity": {
                        "git_author_name": "Harsen Test Bot",
                        "git_author_email": "harsen-test-bot@example.com",
                        "git_committer_name": "Harsen Test Bot",
                        "git_committer_email": "harsen-test-bot@example.com",
                        "github_actor_login": "harsen-mini-test-bot",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.workspace / "inputs").mkdir()
        self.approved_plan_path = self.workspace / "inputs" / "approved-plan.md"
        self.approved_plan_path.write_text(
            "# 已批准实施计划\n\n按确认范围完成实现、验证与 PR。\n",
            encoding="utf-8",
        )
        profile = self.install / "developer/standards/projects/tapdata/profile.yaml"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            "profile_id: tapdata\n"
            "connection_id: tapdata-cloud\n"
            "jira:\n"
            "  project_key: TAP\n"
            "  task_query: project = TAP\n"
            "repositories:\n"
            "  default: tapdata/tapdata\n",
            encoding="utf-8",
        )
        overlay = state / "profiles/tapdata.local.yaml"
        overlay.parent.mkdir()
        overlay.write_text(
            "workspace:\n"
            f"  source_root: {self.source.resolve()}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )
        self.manifest = self._manifest()
        self.manifest_path = self.workspace / "inputs" / "manifest.json"
        self._write_manifest()

    def _manifest(self) -> dict[str, object]:
        issue = JiraIssue(
            issue_id="12289",
            key="TAP-12289",
            project_key="TAP",
            summary="真实任务",
            status="正在进行",
            issue_type="任务",
            assignee="jira-account-1",
            description=None,
        )
        issue_content_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "assignee_account_id": issue.assignee,
                    "description": issue.description,
                    "issue_id": issue.issue_id,
                    "issue_type": issue.issue_type,
                    "key": issue.key,
                    "project_key": issue.project_key,
                    "status": issue.status,
                    "summary": issue.summary,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        value: dict[str, object] = {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "workspace": {"root": str(self.workspace.resolve())},
            "issue": {"key": "TAP-12289", "id": "12289", "project_key": "TAP"},
            "jira": {
                "base_url": "https://tapdata.atlassian.net",
                "account_id": "jira-account-1",
                "assignee_account_id": "jira-account-1",
                "status_mapping": {"正在进行": "implementation"},
                "allowed_status_categories": ["In Progress"],
            },
            "agent": {
                "agent_id": "harsen-mini-test-bot",
                "project_profile": "tapdata",
                "agentic_run_id": "run-TAP-12289-001",
            },
            "task_binding": {
                "issue_content_sha256": issue_content_sha256,
                "approved_plan_file": "inputs/approved-plan.md",
                "approved_plan_sha256": hashlib.sha256(
                    self.approved_plan_path.read_bytes()
                ).hexdigest(),
            },
            "execution_identity": {
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen-test-bot@example.com",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen-test-bot@example.com",
                "github_actor_login": "harsen-mini-test-bot",
            },
            "repository": {
                "root": str(self.source.resolve()),
                "slug": "tapdata/tapdata",
                "remote_name": "origin",
                "base_branch": "develop",
                "task_branch": "codex/TAP-12289/task-run-test",
                "target_branch": "develop",
                "protected_branches": ["main", "develop"],
            },
            "scope": {"included": ["src/**", "tests/**"], "excluded": ["vendor/**", ".env"]},
            "verification": [
                {
                    "id": "unit",
                    "command": ["python3", "-m", "unittest"],
                    "working_directory": ".",
                    "timeout_seconds": 60,
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
                "reference": (
                    "user-confirmation:TAP-12289:run-TAP-12289-001:"
                    + hashlib.sha256(self.approved_plan_path.read_bytes()).hexdigest()
                ),
                "confirmed_by": "harsen",
                "confirmed_at": "2026-08-13T02:00:00+00:00",
                "confirmed_manifest_sha256": "",
            },
        }
        value["authorization"]["confirmed_manifest_sha256"] = manifest_digest(value)  # type: ignore[index]
        return value

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _cli(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        git_head = "a" * 40
        with (
            mock.patch("ao_work.work_cli.validate_install_root", return_value=self.install),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = main(("--workspace-root", str(self.workspace), *arguments))
        return code, json.loads(stdout.getvalue()), stderr.getvalue()

    def _open(self) -> None:
        code, payload, stderr = self._cli("task-run", "open", "--manifest", "inputs/manifest.json")
        self.assertEqual(0, code, (payload, stderr))

    def _append_baseline(
        self,
        protocol: TaskRunProtocol,
        *,
        local_head: str,
        remote_head: str | None = None,
        task_open_pr: dict[str, object] | None = None,
    ) -> str:
        result = protocol._append_runtime_fact(
            self.manifest,
            "prohibition_baseline",
            {
                "issue_key": "TAP-12289",
                "repository_slug": "tapdata/tapdata",
                "remote_name": "origin",
                "jira_status": "正在进行",
                "jira_status_category": "In Progress",
                "tag_refs": [],
                "release_records": [],
                "protected_heads": [
                    {"branch": "develop", "sha": "b" * 40},
                    {"branch": "main", "sha": "c" * 40},
                ],
                "local_head_sha": local_head,
                "task_branch_remote_sha": remote_head,
                "task_open_pr": task_open_pr,
                "observed_at": "2026-08-13T02:00:00+00:00",
                "reference": "runtime-prohibition-baseline:TAP-12289:run-TAP-12289-001",
            },
            "测试写前基线",
        )
        return str(result["event_id"])

    def _event(self, action: str, action_data: dict[str, object], *, actor: str = "ai") -> dict[str, object]:
        return {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "event_id": f"event-{action}",
            "agentic_run_id": "run-TAP-12289-001",
            "step_id": f"step-{action}",
            "recorded_at": "2026-08-13T02:01:00+00:00",
            "status": "completed",
            "actor": actor,
            "action": action,
            "duration_seconds": 1,
            "summary": "脱敏过程记录",
            "authorization_reference": self.manifest["authorization"]["reference"],
            "action_data": action_data,
            "evidence_origin": "imported",
        }

    def test_manifest_runtime_and_shared_identity_contract_are_aligned(self) -> None:
        validate_manifest(self.manifest)
        value = copy.deepcopy(self.manifest)
        value["agent"]["agent_id"] = "_agent-1"  # type: ignore[index]
        value["authorization"]["confirmed_manifest_sha256"] = manifest_digest(value)  # type: ignore[index]
        validate_manifest(value)
        invalid = copy.deepcopy(self.manifest)
        invalid["agent"]["project_profile"] = "Tap.Data"  # type: ignore[index]
        invalid["authorization"]["confirmed_manifest_sha256"] = manifest_digest(invalid)  # type: ignore[index]
        try:
            validate_manifest(invalid)
        except Exception as error:
            self.assertEqual("protocol_schema_invalid", getattr(error, "code", None))
        else:
            self.fail("非法 project_profile 应被拒绝")
        arbitrary_authorization = copy.deepcopy(self.manifest)
        arbitrary_authorization["authorization"]["reference"] = (  # type: ignore[index]
            "arbitrary-nonempty-reference"
        )
        arbitrary_authorization["authorization"][  # type: ignore[index]
            "confirmed_manifest_sha256"
        ] = manifest_digest(arbitrary_authorization)
        with self.assertRaises(Exception) as captured:
            validate_manifest(arbitrary_authorization)
        self.assertEqual("protocol_schema_invalid", getattr(captured.exception, "code", None))

    def test_manifest_reuses_workspace_execution_identity(self) -> None:
        self.manifest["execution_identity"]["github_actor_login"] = "another-user"  # type: ignore[index]
        self.manifest["authorization"]["confirmed_manifest_sha256"] = manifest_digest(  # type: ignore[index]
            self.manifest
        )
        self._write_manifest()
        code, payload, _ = self._cli(
            "task-run", "open", "--manifest", "inputs/manifest.json"
        )
        self.assertEqual(2, code)
        self.assertEqual("manifest_execution_identity_mismatch", payload["code"])

    def test_verification_digest_binds_timeout_seconds(self) -> None:
        verification = self.manifest["verification"][0]  # type: ignore[index]
        changed = dict(verification)
        changed["timeout_seconds"] = 61
        self.assertNotEqual(
            verification_digest(verification),
            verification_digest(changed),
        )

    def test_verification_side_effect_commands_are_rejected_before_popen(self) -> None:
        safe_manifest = copy.deepcopy(self.manifest)
        harmful_commands = (
            ["bash", "-c", "touch /tmp/verification-side-effect"],
            ["python3", "-c", "raise SystemExit(0)"],
            ["git", "push", "origin", "main"],
            ["gh", "pr", "merge", "1"],
            ["curl", "https://example.com"],
            ["ao-work", "jira", "inspect", "--issue-key", "TAP-12289"],
            ["npm", "install", "example-package"],
            ["eslint", "--fix-dry-run", "src"],
            ["pytest", "--snapshot-update"],
            ["pytest", ".agentic-ops/tasks/TAP-12289"],
            ["pytest", "../outside"],
            ["pytest", "https://example.com/tests"],
        )
        try:
            for command in harmful_commands:
                with self.subTest(command=command):
                    invalid = copy.deepcopy(safe_manifest)
                    invalid["verification"][0]["command"] = command  # type: ignore[index]
                    invalid["authorization"][  # type: ignore[index]
                        "confirmed_manifest_sha256"
                    ] = manifest_digest(invalid)
                    self.manifest = invalid
                    self._write_manifest()
                    with mock.patch(
                        "ao_work.task_run.service.subprocess.Popen"
                    ) as popen:
                        code, payload, _ = self._cli(
                            "task-run",
                            "open",
                            "--manifest",
                            "inputs/manifest.json",
                        )
                    self.assertEqual(2, code, payload)
                    self.assertEqual("verification_command_forbidden", payload["code"])
                    popen.assert_not_called()
        finally:
            self.manifest = safe_manifest
            self._write_manifest()

    def test_manifest_requires_pr_and_scope_to_share_one_base_branch(self) -> None:
        invalid = copy.deepcopy(self.manifest)
        invalid["repository"]["base_branch"] = "main"  # type: ignore[index]
        invalid["authorization"]["confirmed_manifest_sha256"] = manifest_digest(invalid)  # type: ignore[index]
        with self.assertRaises(Exception) as captured:
            validate_manifest(invalid)
        self.assertEqual("protocol_schema_invalid", getattr(captured.exception, "code", None))

    def test_record_cannot_import_trusted_facts_or_impersonate_runtime(self) -> None:
        self._open()
        verification = self._event(
            "verification",
            {
                "id": "unit",
                "status": "passed",
                "command_sha256": "a" * 64,
                "evidence_reference": "fake:passed",
                "exit_code": 0,
                "duration_seconds": 1,
                "stdout_sha256": "b" * 64,
                "stderr_sha256": "c" * 64,
                "output_summary": "伪造通过",
                "head_sha": "a" * 40,
            },
        )
        path = self.workspace / "inputs" / "event.json"
        path.write_text(json.dumps(verification), encoding="utf-8")
        code, payload, _ = self._cli(
            "task-run", "record", "--manifest", "inputs/manifest.json", "--event", "inputs/event.json"
        )
        self.assertEqual(2, code)
        self.assertEqual("trusted_fact_import_forbidden", payload["code"])

        human = self._event(
            "human_intervention",
            {"reason": "需确认", "action": "人工确认", "impact_seconds": 3},
            actor="runtime",
        )
        path.write_text(json.dumps(human), encoding="utf-8")
        code, payload, _ = self._cli(
            "task-run", "record", "--manifest", "inputs/manifest.json", "--event", "inputs/event.json"
        )
        self.assertEqual(2, code)
        self.assertEqual("trusted_event_import_forbidden", payload["code"])

    def test_jira_write_cannot_claim_applied_without_dedicated_runtime_readback(self) -> None:
        manifest = copy.deepcopy(self.manifest)
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        for action in ("jira_comment", "jira_worklog"):
            started = self._event("step", {})
            started.update(
                {
                    "event_id": f"event-{action}-started",
                    "step_id": f"step-{action}",
                    "status": "started",
                    "action": "step",
                    "duration_seconds": 0,
                }
            )
            completed = self._event(
                "external_action",
                {
                    "action": action,
                    "target": "jira:TAP-12289",
                    "status": "applied",
                    "readback_event_id": "event-jira-readback",
                },
            )
            completed.update(
                {
                    "event_id": f"event-{action}-completed",
                    "step_id": f"step-{action}",
                }
            )
            with self.assertRaises(Exception) as captured:
                protocol._validate_event_transition(
                    completed,
                    [{"event": started}],
                    manifest,
                )
            self.assertEqual(
                "jira_write_readback_probe_required",
                getattr(captured.exception, "code", None),
            )

    def test_jira_write_probe_atomically_binds_comment_and_worklog_readbacks(self) -> None:
        self.manifest["authorization"]["confirmed_manifest_sha256"] = manifest_digest(  # type: ignore[index]
            self.manifest
        )
        self._write_manifest()
        self._open()
        profile = ProjectProfile(
            profile_id="tapdata",
            connection_id="tapdata-cloud",
            project_key="TAP",
            task_query="",
        )
        issue = JiraIssue(
            issue_id="12289",
            key="TAP-12289",
            project_key="TAP",
            summary="真实任务",
            status="正在进行",
            issue_type="任务",
            assignee="jira-account-1",
            description=None,
        )
        records: dict[str, list[object]] = {"comments": [], "worklogs": []}
        fake_client = SimpleNamespace(
            field_metadata=lambda: [],
            get_issue=lambda _key: issue,
            current_user=lambda: "jira-account-1",
            comments=lambda _key: records["comments"],
            worklogs=lambda _key: records["worklogs"],
        )
        jira_service = JiraService(profile, fake_client)
        comment_plan = jira_service.plan_comment(
            "TAP-12289",
            "task:comment:1",
            "evidence",
            "已完成真实任务证据回写。",
            agentic_run_id="run-TAP-12289-001",
        )
        comment_body = plain_text(markdown_to_adf(str(comment_plan.payload["markdown"])))
        records["comments"] = [
            JiraComment(
                comment_id="9001",
                body=comment_body,
                standalone_lines=frozenset(
                    {
                        "[agentic-ops-idempotency:TAP-12289:"
                        "run-TAP-12289-001:task:comment:1]"
                    }
                ),
            )
        ]
        worklog_plan = jira_service.plan_worklog(
            "TAP-12289",
            "task:worklog:1",
            "实现与验证",
            "完成代码实现、单元测试和安全回归。",
            1800,
            "2026-08-13T04:00:00+00:00",
            True,
            agentic_run_id="run-TAP-12289-001",
            included_work=[
                {"description": "完成代码实现", "seconds": 1200},
                {"description": "完成单元测试和安全回归", "seconds": 600},
            ],
            excluded_waiting_categories=["等待人工确认", "等待 CI"],
        )
        worklog_body = plain_text(markdown_to_adf(str(worklog_plan.payload["markdown"])))
        records["worklogs"] = [
            JiraWorklog(
                worklog_id="9002",
                body=worklog_body,
                time_spent_seconds=1800,
                started="2026-08-13T04:00:00.000+0000",
                standalone_lines=frozenset(
                    {
                        "[agentic-ops-idempotency:TAP-12289:"
                        "run-TAP-12289-001:task:worklog:1]"
                    }
                ),
            )
        ]
        plan_root = (
            self.workspace
            / ".agentic-ops/tasks/TAP-12289/runs/run-TAP-12289-001/jira-plans"
        )
        plan_root.mkdir()
        plans = {
            "comment.json": comment_plan,
            "worklog.json": worklog_plan,
        }
        for name, plan in plans.items():
            (plan_root / name).write_text(
                json.dumps(plan.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
            attempt = build_write_attempt(
                plan,
                str(self.manifest["authorization"]["reference"]),
                request_started_at="2026-08-13T04:00:00+00:00",
            )
            (plan_root / f"{name}.attempt.json").write_text(
                json.dumps(attempt.to_dict(), ensure_ascii=False), encoding="utf-8"
            )

        context = SimpleNamespace(
            connection=SimpleNamespace(
                connection_id="tapdata-cloud",
                base_url="https://tapdata.atlassian.net",
                timeout_seconds=20,
            ),
            profile=profile,
            require_credentials=lambda: ("developer@example.com", "hidden-token"),
        )
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        with (
            mock.patch("ao_work.task_run.service.load_jira_context", return_value=context),
            mock.patch("ao_work.task_run.service.UrllibJiraTransport", return_value=object()),
            mock.patch("ao_work.task_run.service.JiraClient", return_value=fake_client),
        ):
            for name, plan in plans.items():
                protocol.probe_jira_write(
                    "inputs/manifest.json",
                    str((plan_root / name).relative_to(self.workspace)),
                    plan.plan_id,
                )
            no_op_plan = jira_service.plan_comment(
                "TAP-12289",
                "task:comment:1",
                "evidence",
                "已完成真实任务证据回写。",
                agentic_run_id="run-TAP-12289-001",
            )
            self.assertEqual("no_op", no_op_plan.action)
            no_op_path = plan_root / "comment-no-op.json"
            no_op_path.write_text(
                json.dumps(no_op_plan.to_dict(), ensure_ascii=False), encoding="utf-8"
            )
            protocol.probe_jira_write(
                "inputs/manifest.json",
                str(no_op_path.relative_to(self.workspace)),
                no_op_plan.plan_id,
            )

        completed = [
            json.loads(line)["event"]
            for line in next(self.workspace.rglob("events.ndjson")).read_text().splitlines()
            if json.loads(line)["event"]["status"] == "completed"
        ]
        actions = [event for event in completed if event["action"] == "external_action"]
        readbacks = [
            event for event in completed if event["action"] == "jira_write_readback"
        ]
        self.assertEqual(["jira_comment", "jira_worklog"], [
            event["action_data"]["action"] for event in actions
        ])
        self.assertEqual(3, len(readbacks))
        for action, readback in zip(actions, readbacks[:2], strict=True):
            self.assertEqual(readback["event_id"], action["action_data"]["readback_event_id"])
            self.assertLess(completed.index(action), completed.index(readback))
            self.assertEqual("runtime_probe", readback["evidence_origin"])
            self.assertTrue(readback["action_data"]["created"])
            self.assertEqual("absent", readback["action_data"]["write_precondition"])
            self.assertTrue(readback["action_data"]["write_attempt_id"])
        no_op_readback = readbacks[2]["action_data"]
        self.assertFalse(no_op_readback["created"])
        self.assertEqual("preexisting", no_op_readback["write_precondition"])
        self.assertIsNone(no_op_readback["attempt_file"])
        self.assertIsNone(no_op_readback["write_attempt_id"])
        worklog = readbacks[1]["action_data"]
        self.assertEqual("实现与验证", worklog["title"])
        self.assertEqual(1800, worklog["time_spent_seconds"])
        self.assertEqual("2026-08-13T04:00:00.000+00:00", worklog["started"])
        self.assertTrue(worklog["excludes_waiting"])

    def test_jira_write_probe_rejects_missing_read_permission_before_external_call(self) -> None:
        self.manifest["permitted_external_actions"] = ["jira_comment"]
        self.manifest["authorization"]["confirmed_manifest_sha256"] = manifest_digest(  # type: ignore[index]
            self.manifest
        )
        self._write_manifest()
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        with mock.patch(
            "ao_work.task_run.service.load_jira_context",
            side_effect=AssertionError("缺权限时不得读取凭证或调用 Jira"),
        ) as external:
            with self.assertRaises(Exception) as captured:
                protocol.probe_jira_write(
                    "inputs/manifest.json", "does-not-exist.json", "plan-id"
                )
        self.assertEqual("probe_permission_required", getattr(captured.exception, "code", None))
        external.assert_not_called()

    def test_all_external_probes_check_permission_before_transport(self) -> None:
        cases = (
            ("baseline", "jira_read", "jira", "probe_prohibition_baseline"),
            ("jira", "jira_read", "jira", "probe_jira"),
            ("git", "git_remote_read", "git", "probe_git"),
            ("pr", "github_pr_read", "command", "probe_pr"),
            ("prohibitions", "github_pr_read", "jira", "probe_prohibitions"),
        )
        for index, (label, permission, boundary, method_name) in enumerate(cases):
            with self.subTest(probe=label):
                manifest = copy.deepcopy(self.manifest)
                run_id = f"run-TAP-12289-permission-{index}"
                manifest["agent"]["agentic_run_id"] = run_id  # type: ignore[index]
                manifest["permitted_external_actions"].remove(permission)  # type: ignore[union-attr]
                plan_sha = manifest["task_binding"]["approved_plan_sha256"]  # type: ignore[index]
                manifest["authorization"]["reference"] = (  # type: ignore[index]
                    f"user-confirmation:TAP-12289:{run_id}:{plan_sha}"
                )
                manifest["authorization"][  # type: ignore[index]
                    "confirmed_manifest_sha256"
                ] = manifest_digest(manifest)
                self.manifest_path.write_text(
                    json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
                )
                protocol = TaskRunProtocol(
                    SimpleNamespace(
                        root=self.workspace.resolve(),
                        config_path=self.workspace / ".agentic-ops/agent.json",
                    ),
                    install_root=self.install,
                    lock_timeout=1,
                )
                protocol.open("inputs/manifest.json")
                if boundary == "git":
                    patcher = mock.patch.object(
                        protocol,
                        "_git",
                        side_effect=AssertionError("缺权限时不得执行 Git"),
                    )
                elif boundary == "command":
                    patcher = mock.patch.object(
                        protocol,
                        "_run_command",
                        side_effect=AssertionError("缺权限时不得执行 gh"),
                    )
                else:
                    patcher = mock.patch(
                        "ao_work.task_run.service.load_jira_context",
                        side_effect=AssertionError("缺权限时不得读取凭证或调用 Jira"),
                    )
                with patcher as external, self.assertRaises(Exception) as captured:
                    getattr(protocol, method_name)("inputs/manifest.json")
                self.assertEqual(
                    "probe_permission_required",
                    getattr(captured.exception, "code", None),
                )
                external.assert_not_called()

    def test_verification_executes_exact_argv_with_isolated_environment_and_no_raw_output(self) -> None:
        self._open()
        git_head = "a" * 40
        captured: dict[str, object] = {}
        real_popen = subprocess.Popen

        def fake_popen(argv: list[str], **kwargs: object) -> subprocess.Popen[bytes]:
            captured.update({"argv": argv, **kwargs})
            return real_popen(
                [
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'sensitive-build-output')",
                ],
                **kwargs,
            )

        with (
            mock.patch.dict(
                os.environ,
                {
                    "PATH": "/attacker/bin",
                    "HOME": "/sensitive/home",
                    "SSH_AUTH_SOCK": "/sensitive/agent.sock",
                    "GH_TOKEN": "do-not-inherit",
                    "JIRA_API_TOKEN": "do-not-inherit",
                    "ATLASSIAN_EMAIL": "developer@example.com",
                    "SAFE": "yes",
                },
                clear=True,
            ),
            mock.patch(
                "ao_work.task_run.service.load_jira_connection",
                return_value=SimpleNamespace(
                    connection_id="tapdata-cloud",
                    base_url="https://tapdata.atlassian.net",
                    email_env="ATLASSIAN_EMAIL",
                    token_env="ATLASSIAN_API_TOKEN",
                ),
            ),
            mock.patch.object(
                TaskRunProtocol,
                "_git",
                return_value=git_head + "\n",
            ),
            mock.patch("ao_work.task_run.service.subprocess.Popen", side_effect=fake_popen),
        ):
            code, payload, _ = self._cli(
                "task-run", "verify", "--manifest", "inputs/manifest.json", "--verification-id", "unit"
            )
        self.assertEqual(0, code, payload)
        self.assertEqual(["python3", "-m", "unittest"], captured["argv"])
        self.assertIs(False, captured["shell"])
        self.assertNotIn("GH_TOKEN", captured["env"])  # type: ignore[operator]
        self.assertNotIn("JIRA_API_TOKEN", captured["env"])  # type: ignore[operator]
        self.assertNotIn("ATLASSIAN_EMAIL", captured["env"])  # type: ignore[operator]
        self.assertNotIn("SSH_AUTH_SOCK", captured["env"])  # type: ignore[operator]
        self.assertNotIn("SAFE", captured["env"])  # type: ignore[operator]
        self.assertNotEqual("/sensitive/home", captured["env"]["HOME"])  # type: ignore[index]
        self.assertNotIn("/attacker/bin", captured["env"]["PATH"])  # type: ignore[index]
        self.assertEqual("true", captured["env"]["CI"])  # type: ignore[index]
        self.assertEqual(
            "allowlist-only-no-sandbox",
            captured["env"]["AGENTIC_OPS_VERIFICATION_NETWORK_POLICY"],  # type: ignore[index]
        )
        journal = next(self.workspace.rglob("events.ndjson")).read_text(encoding="utf-8")
        self.assertNotIn("sensitive-build-output", journal)
        completed = json.loads(journal.splitlines()[-1])["event"]
        self.assertEqual("runtime_probe", completed["evidence_origin"])
        self.assertEqual(hashlib.sha256(b"sensitive-build-output").hexdigest(), completed["action_data"]["stdout_sha256"])
        self.assertEqual(git_head, completed["action_data"]["head_sha"])

    def test_command_output_limit_is_streamed_and_terminates_process_group(self) -> None:
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        started = time.monotonic()
        with self.assertRaises(Exception) as captured:
            protocol._run_command(
                [
                    sys.executable,
                    "-c",
                    "import os\nwhile True: os.write(1, b'x' * 65536)",
                ],
                cwd=self.source,
                timeout=10,
            )
        self.assertEqual("command_output_too_large", getattr(captured.exception, "code", None))
        self.assertLess(time.monotonic() - started, 5)

    def test_task_run_managed_leaves_reject_hardlinks(self) -> None:
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        paths = protocol._paths(self.manifest)
        for leaf in ("manifest", "state", "events", "result"):
            with self.subTest(leaf=leaf):
                managed = paths[leaf]
                if leaf == "events":
                    managed.write_text("", encoding="utf-8")
                elif leaf == "result":
                    managed.write_text("{}\n", encoding="utf-8")
                original = managed.read_bytes()
                external = Path(self.temp.name) / f"external-{leaf}"
                external.write_bytes(original)
                managed.unlink()
                os.link(external, managed)
                with self.assertRaises(Exception) as captured:
                    if leaf == "events":
                        protocol._read_journal(managed)
                    else:
                        read_json(managed)
                self.assertEqual("task_state_leaf_unsafe", getattr(captured.exception, "code", None))
                self.assertEqual(original, external.read_bytes())
                managed.unlink()
                managed.write_bytes(original)

    def test_retry_requires_an_earlier_failure_and_waiting_is_structured(self) -> None:
        waiting = self._event(
            "waiting",
            {
                "reason": "等待 CI",
                "started_at": "2026-08-13T02:00:00+00:00",
                "ended_at": "2026-08-13T02:02:00+00:00",
                "duration_seconds": 120,
            },
        )
        validate_event(waiting)
        schema = json.loads(
            (Path(__file__).resolve().parents[3] / "shared/integration/task-to-pr-event.schema.json").read_text(encoding="utf-8")
        )
        self.assertIn("waiting", schema["properties"]["action"]["enum"])
        self.assertIn("evidence_origin", schema["required"])

    def test_each_friction_event_must_be_sourced_by_a_finding_category(self) -> None:
        protocol = TaskRunProtocol(
            SimpleNamespace(root=self.workspace.resolve(), config_path=None),
            install_root=self.install,
            lock_timeout=1,
        )
        for action in ("failure", "retry", "human_intervention", "waiting"):
            with self.subTest(action=action):
                process_id = f"event-{action}-1"
                finding_id = f"event-{action}-finding"
                process_event = {
                    "event_id": process_id,
                    "action": action,
                    "action_data": {},
                }
                finding = {
                    "event_id": finding_id,
                    "action": "quality_finding",
                    "action_data": {
                        "category": "automation_gap",
                        # 单向指向过程事件仍不足以完成分类复盘承接。
                        "evidence_reference": process_id,
                    },
                }
                reviews = [
                    {
                        "category": category,
                        "outcome": (
                            "finding" if category == "automation_gap" else "no_finding"
                        ),
                        "rationale": "已检查该分类并记录明确结论。",
                        "evidence_references": [finding_id],
                        "source_event_ids": (
                            [finding_id] if category == "automation_gap" else []
                        ),
                    }
                    for category in QUALITY_CATEGORIES
                ]
                process_ids = {
                    "failure_event_ids": [],
                    "retry_event_ids": [],
                    "human_intervention_event_ids": [],
                    "waiting_event_ids": [],
                }
                process_ids[f"{action}_event_ids"] = [process_id]
                retrospective = {
                    "event_id": f"event-{action}-retrospective",
                    "action": "retrospective",
                    "action_data": {
                        "reviewed_categories": list(QUALITY_CATEGORIES),
                        "category_reviews": reviews,
                        "quality_finding_event_ids": [finding_id],
                        **process_ids,
                        "ordered_improvement_event_ids": [finding_id],
                        "residual_risks": [],
                        "summary": "已完成四类质量复盘。",
                    },
                }
                by_action = {
                    "retrospective": [retrospective],
                    "quality_finding": [finding],
                    "failure": [process_event] if action == "failure" else [],
                    "retry": [process_event] if action == "retry" else [],
                    "human_intervention": (
                        [process_event] if action == "human_intervention" else []
                    ),
                    "waiting": [process_event] if action == "waiting" else [],
                }
                event_index = {
                    event["event_id"]: event
                    for event in (process_event, finding, retrospective)
                }
                with self.assertRaises(Exception) as captured:
                    protocol._validate_retrospective(by_action, event_index)
                self.assertEqual(
                    "task_run_incomplete", getattr(captured.exception, "code", None)
                )

    def test_ready_verification_uses_latest_attempt_and_requires_failure_retry_audit(self) -> None:
        protocol = TaskRunProtocol(
            SimpleNamespace(root=self.workspace.resolve(), config_path=None),
            install_root=self.install,
            lock_timeout=1,
        )
        expected_digest = verification_digest(self.manifest["verification"][0])  # type: ignore[index]
        base = {
            "actor": "runtime",
            "evidence_origin": "runtime_probe",
        }
        failed_attempt = {
            **base,
            "event_id": "verification-unit-attempt-1",
            "action_data": {
                "id": "unit",
                "status": "failed",
                "command_sha256": expected_digest,
                "exit_code": 1,
                "head_sha": "c" * 40,
            },
        }
        passed_attempt = {
            **base,
            "event_id": "verification-unit-attempt-2",
            "action_data": {
                "id": "unit",
                "status": "passed",
                "command_sha256": expected_digest,
                "exit_code": 0,
                "head_sha": "a" * 40,
            },
        }
        failure = {"event_id": "failure-unit", "action_data": {}}
        retry = {
            "event_id": "retry-unit",
            "action_data": {"outcome": "succeeded"},
        }
        external_actions = [
            {
                "action_data": {"action": action, "status": "applied"}
            }
            for action in (
                "jira_read",
                "jira_comment",
                "jira_worklog",
                "git_commit",
                "git_push_task_branch",
                "github_pr_create_or_update",
            )
        ]
        jira_write_events = [
            {
                **base,
                "action_data": {
                    "operation": operation,
                    "issue_key": "TAP-12289",
                    "agentic_run_id": "run-TAP-12289-001",
                    "created": True,
                    "write_precondition": "absent",
                    "attempt_file": f"jira-plans/{operation}.json.attempt.json",
                    "write_attempt_id": f"attempt-{operation}",
                    "write_attempt_started_at": "2026-08-13T04:00:00+00:00",
                },
            }
            for operation in ("jira_comment", "jira_worklog")
        ]
        jira = {
            **base,
            "action_data": {
                "issue_key": "TAP-12289",
                "issue_id": "12289",
                "project_key": "TAP",
                "account_id": "jira-account-1",
                "assignee_account_id": "jira-account-1",
                "status_category": "In Progress",
                "mapped_status": "implementation",
                "status": "正在进行",
                "issue_content_sha256": self.manifest["task_binding"][
                    "issue_content_sha256"
                ],
                "approved_plan_sha256": self.manifest["task_binding"][
                    "approved_plan_sha256"
                ],
            },
        }
        branch = {
            **base,
            "action_data": {
                "repository_slug": "tapdata/tapdata",
                "remote_name": "origin",
                "branch": "codex/TAP-12289/task-run-test",
                "protected": False,
                "sha": "a" * 40,
                "head_sha": "a" * 40,
                "attributed_actions": ["git_commit", "git_push_task_branch"],
                "baseline_local_head_sha": "b" * 40,
                "baseline_local_is_ancestor": True,
                "baseline_remote_sha": None,
                "baseline_remote_is_ancestor": None,
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen-test-bot@example.com",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen-test-bot@example.com",
                "approved_plan_sha256": self.manifest["task_binding"][
                    "approved_plan_sha256"
                ],
            },
        }
        pr = {
            **base,
            "action_data": {
                "repository_slug": "tapdata/tapdata",
                "head_branch": "codex/TAP-12289/task-run-test",
                "base_branch": "develop",
                "head_sha": "a" * 40,
                "merged": False,
                "draft": False,
                "status": "open",
                "ci_status": "passed",
                "github_actor_login": "harsen-mini-test-bot",
                "approved_plan_sha256": self.manifest["task_binding"][
                    "approved_plan_sha256"
                ],
                "attributed_actions": ["github_pr_create_or_update"],
                "creation_proof": True,
            },
        }
        protocol._validate_ready_facts(
            self.manifest,
            jira,
            branch,
            pr,
            [failed_attempt, passed_attempt],
            jira_write_events,
            external_actions,
            [failure],
            [retry],
        )
        with self.assertRaises(Exception) as captured:
            protocol._validate_ready_facts(
                self.manifest,
                jira,
                branch,
                pr,
                [failed_attempt, passed_attempt],
                jira_write_events,
                external_actions,
                [],
                [],
            )
        self.assertEqual("task_run_incomplete", getattr(captured.exception, "code", None))
        jira_write_events[0]["action_data"]["created"] = False
        with self.assertRaises(Exception) as captured:
            protocol._validate_ready_facts(
                self.manifest,
                jira,
                branch,
                pr,
                [passed_attempt],
                jira_write_events,
                external_actions,
                [],
                [],
            )
        self.assertEqual("task_run_incomplete", getattr(captured.exception, "code", None))

    def test_shared_schemas_are_valid_json_and_match_new_result_collections(self) -> None:
        root = Path(__file__).resolve().parents[3] / "shared/integration"
        payloads = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in root.glob("*.json")}
        self.assertEqual(3, len(payloads))
        self.assertIn("jira", payloads["task-to-pr-manifest.schema.json"]["required"])
        self.assertIn("waitings", payloads["task-to-pr-result.schema.json"]["required"])

    def test_jira_probe_uses_current_workspace_identity_and_reports_takeover_gap(self) -> None:
        self._open()
        profile = ProjectProfile(
            profile_id="tapdata",
            connection_id="tapdata-cloud",
            project_key="TAP",
            task_query="",
            status_mapping={"正在进行": "implementation"},
        )
        context = SimpleNamespace(
            connection=SimpleNamespace(
                connection_id="tapdata-cloud",
                base_url="https://tapdata.atlassian.net",
                timeout_seconds=20,
            ),
            profile=profile,
            require_credentials=lambda: ("developer@example.com", "hidden"),
        )
        issue = JiraIssue(
            issue_id="12289",
            key="TAP-12289",
            project_key="TAP",
            summary="真实任务",
            status="正在进行",
            issue_type="任务",
            assignee="jira-account-1",
            description=None,
            fields={
                "status": {
                    "name": "正在进行",
                    "statusCategory": {"name": "In Progress"},
                }
            },
        )
        fake_client = SimpleNamespace(
            current_user_details=lambda: {
                "account_id": "jira-account-1",
                "display_name": "研发员",
            },
            get_issue=lambda key: issue,
            comments=lambda key: [],
        )
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        with (
            mock.patch("ao_work.task_run.service.load_jira_context", return_value=context),
            mock.patch("ao_work.task_run.service.UrllibJiraTransport", return_value=object()),
            mock.patch("ao_work.task_run.service.JiraClient", return_value=fake_client),
        ):
            output = protocol.probe_jira("inputs/manifest.json")
        self.assertIn("automation_gap", output)
        completed_events = [
            json.loads(line)["event"]
            for line in next(self.workspace.rglob("events.ndjson")).read_text().splitlines()
            if json.loads(line)["event"]["status"] == "completed"
        ]
        readback = next(event for event in completed_events if event["action"] == "jira_readback")
        gap = next(event for event in completed_events if event["action"] == "quality_finding")
        self.assertEqual("runtime_probe", readback["evidence_origin"])
        self.assertIsNone(readback["action_data"]["takeover_comment_id"])
        self.assertFalse(readback["action_data"]["formal_takeover_verified"])
        self.assertEqual("automation_gap", gap["action_data"]["category"])

    def test_git_probe_uses_fixed_checks_and_blocks_scope_escape(self) -> None:
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        base, head = "b" * 40, "a" * 40
        self._append_baseline(protocol, local_head=base)

        def fake_git(_root: Path, *argv: str) -> str:
            key = tuple(argv)
            values = {
                ("rev-parse", "--show-toplevel"): str(self.source.resolve()) + "\n",
                ("symbolic-ref", "--quiet", "--short", "HEAD"): "codex/TAP-12289/task-run-test\n",
                ("remote", "get-url", "--all", "origin"): "git@github.com:tapdata/tapdata.git\n",
                ("remote", "get-url", "--push", "--all", "origin"): "git@github.com:tapdata/tapdata.git\n",
                ("config", "--get-all", "remote.origin.url"): "git@github.com:tapdata/tapdata.git\n",
                ("rev-parse", "HEAD"): head + "\n",
                ("status", "--porcelain=v1", "--untracked-files=all"): "",
                ("ls-remote", "--heads", "origin", "refs/heads/develop", "refs/heads/codex/TAP-12289/task-run-test"): f"{base}\trefs/heads/develop\n{head}\trefs/heads/codex/TAP-12289/task-run-test\n",
                ("log", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce", f"{base}..{head}"): f"{head}\0Harsen Test Bot\0harsen-test-bot@example.com\0Harsen Test Bot\0harsen-test-bot@example.com\n",
                ("cat-file", "-e", f"{base}^{{commit}}"): "",
                ("diff", "--name-only", "-z", f"{base}...{head}"): "vendor/escape.py\0",
            }
            return values[key]

        def fake_git_result(_root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
            if argv in {
                ("config", "--get-all", "remote.origin.pushurl"),
                ("config", "--show-origin", "--get-regexp", r"^url\..*\.(insteadOf|pushInsteadOf)$"),
            }:
                return subprocess.CompletedProcess([], 1, "", "")
            return subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(protocol, "_git", side_effect=fake_git),
            mock.patch.object(protocol, "_git_result", side_effect=fake_git_result),
        ):
            try:
                protocol.probe_git("inputs/manifest.json")
            except Exception as error:
                self.assertEqual("git_probe_scope_violation", getattr(error, "code", None))
            else:
                self.fail("越界路径必须阻断")

    def test_git_probe_rejects_three_raw_pushurls_with_attacker_in_middle(self) -> None:
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        official = "git@github.com:tapdata/tapdata.git\n"
        self._append_baseline(protocol, local_head="b" * 40)

        def fake_git(_root: Path, *argv: str) -> str:
            values = {
                ("rev-parse", "--show-toplevel"): str(self.source.resolve()) + "\n",
                ("symbolic-ref", "--quiet", "--short", "HEAD"): "codex/TAP-12289/task-run-test\n",
                ("config", "--get-all", "remote.origin.url"): official,
                ("remote", "get-url", "--all", "origin"): official,
                ("remote", "get-url", "--push", "--all", "origin"): official,
            }
            return values[tuple(argv)]

        def fake_git_result(_root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
            if argv == ("config", "--show-origin", "--get-regexp", r"^url\..*\.(insteadOf|pushInsteadOf)$"):
                return subprocess.CompletedProcess([], 1, "", "")
            if argv == ("config", "--get-all", "remote.origin.pushurl"):
                return subprocess.CompletedProcess(
                    [],
                    0,
                    official
                    + "git@github.com:attacker/repository.git\n"
                    + official,
                    "",
                )
            return subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(protocol, "_git", side_effect=fake_git),
            mock.patch.object(protocol, "_git_result", side_effect=fake_git_result),
        ):
            with self.assertRaises(Exception) as captured:
                protocol.probe_git("inputs/manifest.json")
        self.assertEqual("git_probe_origin_mismatch", getattr(captured.exception, "code", None))

    def test_git_probe_records_the_unique_raw_origin(self) -> None:
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        base, head = "b" * 40, "a" * 40
        official = "git@github.com:tapdata/tapdata.git\n"
        self._append_baseline(protocol, local_head=base)
        commit_log = [
            f"{head}\0Harsen Test Bot\0harsen-test-bot@example.com"
            "\0Harsen Test Bot\0harsen-test-bot@example.com\n"
        ]

        def fake_git(_root: Path, *argv: str) -> str:
            values = {
                ("rev-parse", "--show-toplevel"): str(self.source.resolve()) + "\n",
                ("symbolic-ref", "--quiet", "--short", "HEAD"): "codex/TAP-12289/task-run-test\n",
                ("config", "--get-all", "remote.origin.url"): official,
                ("remote", "get-url", "--all", "origin"): official,
                ("remote", "get-url", "--push", "--all", "origin"): official,
                ("rev-parse", "HEAD"): head + "\n",
                ("status", "--porcelain=v1", "--untracked-files=all"): "",
                ("ls-remote", "--heads", "origin", "refs/heads/develop", "refs/heads/codex/TAP-12289/task-run-test"): f"{base}\trefs/heads/develop\n{head}\trefs/heads/codex/TAP-12289/task-run-test\n",
                ("log", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce", f"{base}..{head}"): commit_log[0],
                ("cat-file", "-e", f"{base}^{{commit}}"): "",
                ("diff", "--name-only", "-z", f"{base}...{head}"): "src/change.py\0",
            }
            return values[tuple(argv)]

        def fake_git_result(_root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
            if argv in {
                ("config", "--get-all", "remote.origin.pushurl"),
                ("config", "--show-origin", "--get-regexp", r"^url\..*\.(insteadOf|pushInsteadOf)$"),
            }:
                return subprocess.CompletedProcess([], 1, "", "")
            if argv == ("merge-base", "--is-ancestor", base, head):
                return subprocess.CompletedProcess([], 0, "", "")
            return subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(protocol, "_git", side_effect=fake_git),
            mock.patch.object(protocol, "_git_result", side_effect=fake_git_result),
        ):
            commit_log[0] += (
                f"{'d' * 40}\0Other Author\0other@example.com"
                "\0Harsen Test Bot\0harsen-test-bot@example.com\n"
            )
            with self.assertRaises(Exception) as captured:
                protocol.probe_git("inputs/manifest.json")
            self.assertEqual(
                "git_commit_identity_mismatch",
                getattr(captured.exception, "code", None),
            )
            commit_log[0] = commit_log[0].splitlines()[0] + "\n"
            protocol.probe_git("inputs/manifest.json")
        completed = [
            json.loads(line)["event"]
            for line in next(self.workspace.rglob("events.ndjson")).read_text().splitlines()
            if json.loads(line)["event"]["status"] == "completed"
        ]
        result = next(event for event in completed if event["action"] == "remote_branch_readback")
        self.assertEqual(
            "git@github.com:tapdata/tapdata.git",
            result["action_data"]["origin_url"],
        )

    def test_pr_probe_rejects_draft_even_when_sha_matches(self) -> None:
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        head, base = "a" * 40, "b" * 40
        baseline_id = self._append_baseline(
            protocol,
            local_head=base,
            remote_head=head,
        )
        git_readback = protocol._append_runtime_fact(
            self.manifest,
            "remote_branch_readback",
            {
                "provider": "git",
                "reference": f"git:branch@{head}",
                "url": "https://github.com/tapdata/tapdata/tree/task",
                "repository_slug": "tapdata/tapdata",
                "remote_name": "origin",
                "branch": "codex/TAP-12289/task-run-test",
                "sha": head,
                "status": "exists",
                "protected": False,
                "observed_at": "2026-08-13T02:00:00+00:00",
                "origin_url": "git@github.com:tapdata/tapdata.git",
                "base_sha": base,
                "head_sha": head,
                "baseline_event_id": baseline_id,
                "baseline_local_head_sha": base,
                "baseline_remote_sha": head,
                "baseline_local_is_ancestor": True,
                "baseline_remote_is_ancestor": True,
                "attributed_actions": [],
                "verification_event_ids": [],
                "changed_paths": ["src/change.py"],
                "worktree_clean": True,
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen-test-bot@example.com",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen-test-bot@example.com",
                "commit_count": 1,
                "commit_identity_sha256": "7" * 64,
                "approved_plan_sha256": self.manifest["task_binding"][
                    "approved_plan_sha256"
                ],
            },
            "可信 Git",
        )
        payload = {
            "number": 1,
            "url": "https://github.com/tapdata/tapdata/pull/1",
            "state": "OPEN",
            "isDraft": True,
            "mergedAt": None,
            "headRefName": "codex/TAP-12289/task-run-test",
            "headRefOid": head,
            "baseRefName": "develop",
            "reviewDecision": "",
            "statusCheckRollup": [{"conclusion": "SUCCESS"}],
        }
        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if argv[:3] == ["gh", "api", "user"]:
                return subprocess.CompletedProcess(argv, 0, "harsen-mini-test-bot\n", "")
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

        with mock.patch.object(protocol, "_run_command", side_effect=fake_run):
            try:
                protocol.probe_pr("inputs/manifest.json")
            except Exception as error:
                self.assertEqual("github_pr_probe_mismatch", getattr(error, "code", None))
            else:
                self.fail("draft PR 必须阻断")

    def test_git_action_binding_requires_baseline_delta_and_final_verification(self) -> None:
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        head, base = "a" * 40, "b" * 40
        self._append_baseline(protocol, local_head=head, remote_head=head)

        def fake_git(_root: Path, *argv: str) -> str:
            values = {
                ("rev-parse", "--show-toplevel"): str(self.source.resolve()) + "\n",
                ("symbolic-ref", "--quiet", "--short", "HEAD"): "codex/TAP-12289/task-run-test\n",
                ("config", "--get-all", "remote.origin.url"): "git@github.com:tapdata/tapdata.git\n",
                ("remote", "get-url", "--all", "origin"): "git@github.com:tapdata/tapdata.git\n",
                ("remote", "get-url", "--push", "--all", "origin"): "git@github.com:tapdata/tapdata.git\n",
                ("rev-parse", "HEAD"): head + "\n",
                ("status", "--porcelain=v1", "--untracked-files=all"): "",
                (
                    "ls-remote", "--heads", "origin", "refs/heads/develop",
                    "refs/heads/codex/TAP-12289/task-run-test",
                ): f"{base}\trefs/heads/develop\n{head}\trefs/heads/codex/TAP-12289/task-run-test\n",
            }
            return values[tuple(argv)]

        def fake_git_result(_root: Path, *argv: str) -> subprocess.CompletedProcess[str]:
            if argv in {
                ("config", "--get-all", "remote.origin.pushurl"),
                ("config", "--show-origin", "--get-regexp", r"^url\..*\.(insteadOf|pushInsteadOf)$"),
            }:
                return subprocess.CompletedProcess([], 1, "", "")
            return subprocess.CompletedProcess([], 0, "", "")

        with (
            mock.patch.object(protocol, "_git", side_effect=fake_git),
            mock.patch.object(protocol, "_git_result", side_effect=fake_git_result),
        ):
            with self.assertRaises(Exception) as captured:
                protocol.probe_git(
                    "inputs/manifest.json",
                    ("git_commit", "git_push_task_branch"),
                )
        self.assertEqual("git_commit_not_attributable", captured.exception.code)

    def test_prohibition_baseline_rejects_preexisting_local_commits(self) -> None:
        for remote_task_sha in (None, "d" * 40):
            with self.subTest(remote_task_sha=remote_task_sha):
                with self.assertRaises(Exception) as captured:
                    TaskRunProtocol._validate_baseline_start(
                        "a" * 40,
                        remote_task_sha,
                        "b" * 40,
                    )
                self.assertEqual(
                    "prohibition_baseline_preexisting_commits",
                    getattr(captured.exception, "code", None),
                )
        TaskRunProtocol._validate_baseline_start("b" * 40, None, "b" * 40)
        TaskRunProtocol._validate_baseline_start(
            "d" * 40, "d" * 40, "b" * 40
        )

    def test_pr_action_binding_fails_closed_when_baseline_already_had_open_pr(self) -> None:
        self._open()
        protocol = TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=self.workspace / ".agentic-ops/agent.json",
            ),
            install_root=self.install,
            lock_timeout=1,
        )
        head, base = "a" * 40, "b" * 40
        baseline_id = self._append_baseline(
            protocol,
            local_head=base,
            remote_head=head,
            task_open_pr={
                "number": 1,
                "url": "https://github.com/tapdata/tapdata/pull/1",
                "head_sha": head,
                "base_branch": "develop",
            },
        )
        protocol._append_runtime_fact(
            self.manifest,
            "remote_branch_readback",
            {
                "provider": "git",
                "reference": f"git:branch@{head}",
                "url": "https://github.com/tapdata/tapdata/tree/codex/TAP-12289/task-run-test",
                "repository_slug": "tapdata/tapdata",
                "remote_name": "origin",
                "branch": "codex/TAP-12289/task-run-test",
                "sha": head,
                "status": "exists",
                "protected": False,
                "observed_at": "2026-08-13T02:00:00+00:00",
                "origin_url": "git@github.com:tapdata/tapdata.git",
                "base_sha": base,
                "head_sha": head,
                "baseline_event_id": baseline_id,
                "baseline_local_head_sha": base,
                "baseline_remote_sha": head,
                "baseline_local_is_ancestor": True,
                "baseline_remote_is_ancestor": True,
                "attributed_actions": [],
                "verification_event_ids": [],
                "changed_paths": ["src/change.py"],
                "worktree_clean": True,
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen-test-bot@example.com",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen-test-bot@example.com",
                "commit_count": 1,
                "commit_identity_sha256": "7" * 64,
                "approved_plan_sha256": self.manifest["task_binding"][
                    "approved_plan_sha256"
                ],
            },
            "可信 Git",
        )
        with self.assertRaises(Exception) as captured:
            protocol.probe_pr(
                "inputs/manifest.json", ("github_pr_create_or_update",)
            )
        self.assertEqual("github_pr_update_proof_not_supported", captured.exception.code)

    def test_ci_status_uses_explicit_pending_whitelist_and_preserves_observation(self) -> None:
        protocol = TaskRunProtocol(
            SimpleNamespace(root=self.workspace.resolve(), config_path=None),
            install_root=self.install,
            lock_timeout=1,
        )
        self.assertEqual("pending", protocol._ci_status([{"status": "QUEUED"}]))
        for conclusion in ("ACTION_REQUIRED", "STALE", "STARTUP_FAILURE", "NEW_UNKNOWN"):
            with self.subTest(conclusion=conclusion):
                self.assertEqual(
                    "failed", protocol._ci_status([{"conclusion": conclusion}])
                )
        self.assertEqual("not_configured", protocol._ci_status([]))


class GitCommitExecutionTest(unittest.TestCase):
    """execute-git-commit 在真实 Git 仓库上的受控提交行为。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.source = root / "source"
        self.workspace.mkdir()
        self.source.mkdir()
        self.install = root / "install"
        state = self.workspace / ".agentic-ops"
        state.mkdir()
        (state / "agent.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "workplane": "developer",
                    "agent_id": "harsen-mini-test-bot",
                    "project_profile": "tapdata",
                    "jira_project": "TAP",
                    "connection_id": "tapdata-cloud",
                    "jira_base_url": "https://tapdata.atlassian.net",
                    "jira_site": "tapdata.atlassian.net",
                    "jira_account_id": "jira-account-1",
                    "source_root": str(self.source.resolve()),
                    "repository": "tapdata/tapdata",
                    "execution_identity": {
                        "git_author_name": "Harsen Test Bot",
                        "git_author_email": "harsen-test-bot@example.com",
                        "git_committer_name": "Harsen Test Bot",
                        "git_committer_email": "harsen-test-bot@example.com",
                        "github_actor_login": "harsen-mini-test-bot",
                    },
                }
            ),
            encoding="utf-8",
        )
        profile = self.install / "developer/standards/projects/tapdata/profile.yaml"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            "profile_id: tapdata\n"
            "connection_id: tapdata-cloud\n"
            "jira:\n"
            "  project_key: TAP\n"
            "  task_query: project = TAP\n"
            "repositories:\n"
            "  default: tapdata/tapdata\n",
            encoding="utf-8",
        )
        overlay = state / "profiles/tapdata.local.yaml"
        overlay.parent.mkdir(parents=True)
        overlay.write_text(
            "workspace:\n"
            f"  source_root: {self.source.resolve()}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )
        self._git("init", "--initial-branch", "develop")
        self._git("config", "user.name", "Baseline")
        self._git("config", "user.email", "baseline@example.com")
        (self.source / "src").mkdir()
        (self.source / "src" / "app.py").write_text("print('baseline')\n", encoding="utf-8")
        (self.source / "src" / "__init__.py").write_text("", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "baseline")
        self._git("checkout", "-b", "codex/TAP-12289/commit-test")
        self._git("remote", "add", "origin", "git@github.com:tapdata/tapdata.git")
        self.approved_plan_path = self.workspace / "inputs" / "approved-plan.md"
        self.approved_plan_path.parent.mkdir(parents=True)
        self.approved_plan_path.write_text("# 计划\n", encoding="utf-8")
        issue = JiraIssue(
            issue_id="12289",
            key="TAP-12289",
            project_key="TAP",
            summary="真实任务",
            status="正在进行",
            issue_type="任务",
            assignee="jira-account-1",
            description=None,
        )
        issue_content_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "assignee_account_id": issue.assignee,
                    "description": issue.description,
                    "issue_id": issue.issue_id,
                    "issue_type": issue.issue_type,
                    "key": issue.key,
                    "project_key": issue.project_key,
                    "status": issue.status,
                    "summary": issue.summary,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.manifest: dict[str, object] = {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "workspace": {"root": str(self.workspace.resolve())},
            "issue": {"key": "TAP-12289", "id": "12289", "project_key": "TAP"},
            "jira": {
                "base_url": "https://tapdata.atlassian.net",
                "account_id": "jira-account-1",
                "assignee_account_id": "jira-account-1",
                "status_mapping": {"正在进行": "implementation"},
                "allowed_status_categories": ["In Progress"],
            },
            "agent": {
                "agent_id": "harsen-mini-test-bot",
                "project_profile": "tapdata",
                "agentic_run_id": "run-TAP-12289-commit",
            },
            "task_binding": {
                "issue_content_sha256": issue_content_sha256,
                "approved_plan_file": "inputs/approved-plan.md",
                "approved_plan_sha256": hashlib.sha256(
                    self.approved_plan_path.read_bytes()
                ).hexdigest(),
            },
            "execution_identity": {
                "git_author_name": "Harsen Test Bot",
                "git_author_email": "harsen-test-bot@example.com",
                "git_committer_name": "Harsen Test Bot",
                "git_committer_email": "harsen-test-bot@example.com",
                "github_actor_login": "harsen-mini-test-bot",
            },
            "repository": {
                "root": str(self.source.resolve()),
                "slug": "tapdata/tapdata",
                "remote_name": "origin",
                "base_branch": "develop",
                "task_branch": "codex/TAP-12289/commit-test",
                "target_branch": "develop",
                "protected_branches": ["main", "develop"],
            },
            "scope": {"included": ["src/**"], "excluded": ["vendor/**", ".env"]},
            "verification": [
                {
                    "id": "unit",
                    "command": ["python3", "-m", "unittest"],
                    "working_directory": ".",
                    "timeout_seconds": 60,
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
                "git_commit",
                "git_remote_read",
                "git_push_task_branch",
                "github_pr_create_or_update",
                "github_pr_read",
            ],
            "authorization": {
                "reference": (
                    "user-confirmation:TAP-12289:run-TAP-12289-commit:"
                    + hashlib.sha256(self.approved_plan_path.read_bytes()).hexdigest()
                ),
                "confirmed_by": "harsen",
                "confirmed_at": "2026-08-18T02:00:00+00:00",
                "confirmed_manifest_sha256": "",
            },
        }
        self.manifest["authorization"]["confirmed_manifest_sha256"] = manifest_digest(
            self.manifest
        )  # type: ignore[index]
        self.manifest_path = self.workspace / "inputs" / "manifest.json"
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.source), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _protocol(self) -> TaskRunProtocol:
        return TaskRunProtocol(
            SimpleNamespace(
                root=self.workspace.resolve(),
                config_path=(self.workspace / ".agentic-ops" / "agent.json").resolve(),
            ),
            install_root=self.install,
            lock_timeout=1,
        )

    def _open_with_baseline(self) -> tuple[TaskRunProtocol, str]:
        protocol = self._protocol()
        protocol.open("inputs/manifest.json")
        local_head = self._git("rev-parse", "HEAD").stdout.strip()
        baseline_id = protocol._append_runtime_fact(
            self.manifest,
            "prohibition_baseline",
            {
                "issue_key": "TAP-12289",
                "repository_slug": "tapdata/tapdata",
                "remote_name": "origin",
                "jira_status": "正在进行",
                "jira_status_category": "In Progress",
                "tag_refs": [],
                "release_records": [],
                "protected_heads": [],
                "local_head_sha": local_head,
                "task_branch_remote_sha": None,
                "task_open_pr": None,
                "observed_at": "2026-08-18T02:00:00+00:00",
                "reference": "runtime-prohibition-baseline:TAP-12289:run-TAP-12289-commit",
            },
            "写前基线",
        )
        return protocol, str(baseline_id["event_id"])

    def test_execute_commit_creates_attributable_commit(self) -> None:
        protocol, _ = self._open_with_baseline()
        (self.source / "src" / "app.py").write_text(
            "print('baseline')\nprint('change')\n", encoding="utf-8"
        )
        before = self._git("rev-parse", "HEAD").stdout.strip()
        result = protocol.execute_git_commit(
            "inputs/manifest.json",
            message="Feat: 实现受控提交",
            authorization_reference=str(
                self.manifest["authorization"]["reference"]
            ),
        )
        after = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertNotEqual(before, after)
        self.assertTrue(result["recorded"])
        self.assertIn("git_commit", result["bound_external_actions"])
        readback = result["event_sha256"]
        self.assertTrue(readback)
        status = self._git("status", "--porcelain=v1", "--untracked-files=no").stdout
        self.assertEqual("", status)
        log = self._git("log", "--format=%an%x00%ae", "-1").stdout.strip()
        self.assertEqual("Harsen Test Bot\u0000harsen-test-bot@example.com", log)

    def test_execute_commit_blocks_when_authorization_mismatch(self) -> None:
        protocol, _ = self._open_with_baseline()
        (self.source / "src" / "app.py").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(RuntimeErrorResult) as captured:
            protocol.execute_git_commit(
                "inputs/manifest.json",
                message="Feat: 无授权提交",
                authorization_reference="user-confirmation:stale",
            )
        self.assertEqual(
            "authorization_reference_mismatch", captured.exception.code
        )

    def test_execute_commit_blocks_without_baseline(self) -> None:
        protocol = self._protocol()
        protocol.open("inputs/manifest.json")
        (self.source / "src" / "app.py").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(RuntimeErrorResult) as captured:
            protocol.execute_git_commit(
                "inputs/manifest.json",
                message="Feat: 无基线提交",
                authorization_reference=str(
                    self.manifest["authorization"]["reference"]
                ),
            )
        self.assertEqual("git_commit_baseline_missing", captured.exception.code)

    def test_execute_commit_blocks_out_of_scope_paths(self) -> None:
        protocol, _ = self._open_with_baseline()
        (self.source / "vendor").mkdir(exist_ok=True)
        (self.source / "vendor" / "dep.txt").write_text("x\n", encoding="utf-8")
        with self.assertRaises(RuntimeErrorResult) as captured:
            protocol.execute_git_commit(
                "inputs/manifest.json",
                message="Feat: 越界提交",
                authorization_reference=str(
                    self.manifest["authorization"]["reference"]
                ),
            )
        self.assertEqual("git_commit_scope_violation", captured.exception.code)

    def test_execute_commit_blocks_on_clean_worktree(self) -> None:
        protocol, _ = self._open_with_baseline()
        with self.assertRaises(RuntimeErrorResult) as captured:
            protocol.execute_git_commit(
                "inputs/manifest.json",
                message="Feat: 空提交",
                authorization_reference=str(
                    self.manifest["authorization"]["reference"]
                ),
            )
        self.assertEqual("git_commit_no_changes", captured.exception.code)

    def _push_origin(self) -> Path:
        """创建本地 bare origin 并让 source remote 指向它。"""
        origin = self.source.parent / "origin.git"
        subprocess.run(
            ["git", "init", "--bare", str(origin)],
            capture_output=True,
            text=True,
            check=False,
        )
        # 先推 develop 基线，使远端有 base 分支。
        self._git("push", str(origin), "develop")
        self._git("remote", "set-url", "origin", str(origin))
        return origin

    def test_execute_push_creates_remote_branch_and_reads_back(self) -> None:
        self._push_origin()
        protocol, _ = self._open_with_baseline()
        (self.source / "src" / "app.py").write_text(
            "print('baseline')\nprint('change')\n", encoding="utf-8"
        )
        protocol.execute_git_commit(
            "inputs/manifest.json",
            message="Feat: 实现受控提交",
            authorization_reference=str(
                self.manifest["authorization"]["reference"]
            ),
        )
        result = protocol.execute_git_push_task_branch(
            "inputs/manifest.json",
            authorization_reference=str(
                self.manifest["authorization"]["reference"]
            ),
        )
        self.assertTrue(result["recorded"])
        self.assertIn("git_push_task_branch", result["bound_external_actions"])
        readback = self._git(
            "ls-remote", "origin", "refs/heads/codex/TAP-12289/commit-test"
        ).stdout.strip()
        local_head = self._git("rev-parse", "HEAD").stdout.strip()
        self.assertTrue(readback.startswith(local_head))

    def test_execute_push_requires_prior_commit(self) -> None:
        protocol, _ = self._open_with_baseline()
        with self.assertRaises(RuntimeErrorResult) as captured:
            protocol.execute_git_push_task_branch(
                "inputs/manifest.json",
                authorization_reference=str(
                    self.manifest["authorization"]["reference"]
                ),
            )
        self.assertEqual("git_push_commit_missing", captured.exception.code)

    def test_execute_pr_create_creates_and_reads_back(self) -> None:
        self._push_origin()
        protocol, _ = self._open_with_baseline()
        (self.source / "src" / "app.py").write_text(
            "print('baseline')\nprint('change')\n", encoding="utf-8"
        )
        protocol.execute_git_commit(
            "inputs/manifest.json",
            message="Feat: 实现受控提交",
            authorization_reference=str(
                self.manifest["authorization"]["reference"]
            ),
        )
        protocol.execute_git_push_task_branch(
            "inputs/manifest.json",
            authorization_reference=str(
                self.manifest["authorization"]["reference"]
            ),
        )
        create_argv: list[list[str]] = []

        def fake_run_command(argv, cwd, timeout, *, denied_environment_keys=None, verification_repository_root=None):
            create_argv.append(argv)
            if argv[0] == "gh" and argv[1] == "api" and argv[2] == "user":
                return subprocess.CompletedProcess(
                    argv, 0, "harsen-mini-test-bot\n", ""
                )
            if argv[0] == "gh" and argv[1] == "pr" and argv[2] == "create":
                return subprocess.CompletedProcess(
                    argv, 0, "https://github.com/tapdata/tapdata/pull/42\n", ""
                )
            if argv[0] == "gh" and argv[1] == "pr" and argv[2] == "view":
                payload = json.dumps(
                    {
                        "number": 42,
                        "url": "https://github.com/tapdata/tapdata/pull/42",
                        "state": "OPEN",
                        "isDraft": False,
                        "mergedAt": None,
                        "headRefName": "codex/TAP-12289/commit-test",
                        "headRefOid": "a" * 40,
                        "baseRefName": "develop",
                        "reviewDecision": None,
                        "statusCheckRollup": [],
                    }
                )
                return subprocess.CompletedProcess(argv, 0, payload, "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(
            TaskRunProtocol, "_run_command", staticmethod(fake_run_command)
        ):
            result = protocol.execute_github_pr_create(
                "inputs/manifest.json",
                title="Feat: 实现受控提交",
                body="修复 AgenticOps 配置阻塞",
                authorization_reference=str(
                    self.manifest["authorization"]["reference"]
                ),
            )
        self.assertTrue(result["recorded"])
        self.assertIn(
            "github_pr_create_or_update", result["bound_external_actions"]
        )
        self.assertTrue(
            any(argv[0] == "gh" and argv[1] == "pr" and argv[2] == "create" for argv in create_argv)
        )

    def test_execute_pr_create_requires_git_push_first(self) -> None:
        protocol, _ = self._open_with_baseline()
        with self.assertRaises(RuntimeErrorResult) as captured:
            protocol.execute_github_pr_create(
                "inputs/manifest.json",
                title="Feat",
                body="body",
                authorization_reference=str(
                    self.manifest["authorization"]["reference"]
                ),
            )
        self.assertEqual(
            "pr_create_git_readback_missing", captured.exception.code
        )

    def test_execute_pr_create_blocks_merged_pr(self) -> None:
        self._push_origin()
        protocol, _ = self._open_with_baseline()
        (self.source / "src" / "app.py").write_text(
            "print('baseline')\nprint('change')\n", encoding="utf-8"
        )
        protocol.execute_git_commit(
            "inputs/manifest.json",
            message="Feat: 实现受控提交",
            authorization_reference=str(
                self.manifest["authorization"]["reference"]
            ),
        )
        protocol.execute_git_push_task_branch(
            "inputs/manifest.json",
            authorization_reference=str(
                self.manifest["authorization"]["reference"]
            ),
        )

        def fake_run_command(argv, cwd, timeout, *, denied_environment_keys=None, verification_repository_root=None):
            if argv[0] == "gh" and argv[1] == "api" and argv[2] == "user":
                return subprocess.CompletedProcess(
                    argv, 0, "harsen-mini-test-bot\n", ""
                )
            if argv[0] == "gh" and argv[1] == "pr" and argv[2] == "create":
                return subprocess.CompletedProcess(
                    argv, 0, "https://github.com/tapdata/tapdata/pull/42\n", ""
                )
            if argv[0] == "gh" and argv[1] == "pr" and argv[2] == "view":
                payload = json.dumps(
                    {
                        "number": 42,
                        "url": "https://github.com/tapdata/tapdata/pull/42",
                        "state": "OPEN",
                        "isDraft": False,
                        "mergedAt": "2026-08-18T03:00:00Z",
                        "headRefName": "codex/TAP-12289/commit-test",
                        "headRefOid": "a" * 40,
                        "baseRefName": "develop",
                        "reviewDecision": None,
                        "statusCheckRollup": [],
                    }
                )
                return subprocess.CompletedProcess(argv, 0, payload, "")
            return subprocess.CompletedProcess(argv, 0, "", "")

        with mock.patch.object(
            TaskRunProtocol, "_run_command", staticmethod(fake_run_command)
        ):
            with self.assertRaises(RuntimeErrorResult) as captured:
                protocol.execute_github_pr_create(
                    "inputs/manifest.json",
                    title="Feat",
                    body="body",
                    authorization_reference=str(
                        self.manifest["authorization"]["reference"]
                    ),
                )
        self.assertEqual("pr_auto_merge_forbidden", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
