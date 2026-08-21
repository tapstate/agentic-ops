from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from ao_work.jira.adf import markdown_to_adf
from ao_work.work_cli import main
from install_auth_fixture import configure_install_authorization, v4_agent
from test_jira_service import FakeTransport


class JiraCliTest(unittest.TestCase):
    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), mock.patch(
            "ao_work.work_cli.validate_install_root",
            return_value=self.install_root,
        ):
            exit_code = main(arguments)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(1, len(lines), stdout.getvalue())
        return exit_code, json.loads(lines[0]), stderr.getvalue()

    def prepare(self, root: Path) -> tuple[Path, Path]:
        install = root / "install"
        self.install_root = install.resolve()
        workspace = root / "workspace"
        connection = install / "developer" / "standards" / "connections" / "tap-cloud.yaml"
        profile = install / "developer" / "standards" / "projects" / "demo" / "profile.yaml"
        connection.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        connection.write_text(
            """\
connection_id: tap-cloud
base_url: https://jira.example.test
auth:
  email_env: TEST_JIRA_EMAIL
  token_env: TEST_JIRA_TOKEN
""",
            encoding="utf-8",
        )
        profile.write_text(
            """\
profile_id: demo
connection_id: tap-cloud
jira:
  project_key: TAP
  issue_types: [任务]
  task_query: project = TAP
fields:
  issue_analysis:
    source: jira_field
    jira_field: customfield_10092
    state: active
  fix_details:
    source: jira_field
    jira_field: customfield_10093
    state: active
  problem_analysis:
    source: jira_description_section
    section: 问题分析
    state: active
    writable: true
  raw_defect_log:
    source: jira_description_section
    section: 原始缺陷日志
    state: read_only
    writable: false
statuses: {}
transitions: {}
repositories:
  default: tapdata/tapdata
""",
            encoding="utf-8",
        )
        agent = workspace / ".agentic-ops" / "agent.json"
        agent.parent.mkdir(parents=True)
        source = root / "source"
        overlay = workspace / ".agentic-ops/profiles/demo.local.yaml"
        overlay.parent.mkdir()
        overlay.write_text(
            "workspace:\n"
            f"  source_root: {source}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )
        configure_install_authorization(
            install,
            jira_email="owner@example.test",
            token="secret-token",
        )
        agent.write_text(
            json.dumps(
                v4_agent(
                    install,
                    project_profile="demo",
                    connection_id="tap-cloud",
                    jira_base_url="https://jira.example.test",
                    jira_site="jira.example.test",
                    jira_project="TAP",
                    source_root=str(source),
                    repository="tapdata/tapdata",
                )
            ),
            encoding="utf-8",
        )
        return install, workspace

    def initialize_task(
        self,
        workspace: Path,
        *,
        issue_key: str = "TAP-123",
        agentic_run_id: str = "run-1",
    ) -> tuple[str, ...]:
        common = ("--workspace-root", str(workspace))
        initialized = self.run_cli(
            *common,
            "task",
            "init",
            "--connection-id",
            "tap-cloud",
            "--jira-issue-id",
            "10001",
            "--issue-key",
            issue_key,
            "--project-key",
            "TAP",
            "--agentic-run-id",
            agentic_run_id,
        )
        self.assertEqual(0, initialized[0])
        return common

    def test_cli_comment_plan_apply_readback_and_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = (
                "--workspace-root",
                str(workspace),
            )
            initialized = self.run_cli(
                *common,
                "task",
                "init",
                "--connection-id",
                "tap-cloud",
                "--jira-issue-id",
                "10001",
                "--issue-key",
                "TAP-123",
                "--project-key",
                "TAP",
                "--agentic-run-id",
                "run-1",
            )
            self.assertEqual(0, initialized[0])
            content = workspace / "comment.md"
            content.write_text("## 分析\n\n确认需要补充 Jira 写入回读。\n", encoding="utf-8")
            plan_file = ".agentic-ops/tasks/TAP-123/runs/run-1/jira-plans/comment-plan.json"
            transport = FakeTransport()
            env_file = workspace / ".agentic-ops" / ".env"
            env_file.write_text(
                "TEST_JIRA_EMAIL=owner@example.test\nTEST_JIRA_TOKEN=secret-token\n",
                encoding="utf-8",
            )
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                planned = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "plan",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-analysis",
                    "--category",
                    "analysis",
                    "--content-file",
                    "comment.md",
                    "--plan-file",
                    plan_file,
                )
                self.assertEqual(0, planned[0])
                self.assertEqual(
                    0o600,
                    (workspace / plan_file).stat().st_mode & 0o777,
                )
                plan_id = str(planned[1]["plan_id"])
                authorization_reference = str(
                    planned[1]["authorization_user_confirmation_reference"]
                )
                self.assertEqual(
                    f"user-confirmation:TAP-123:run-1:{plan_id}",
                    authorization_reference,
                )
                applied = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                    "--authorization-reference",
                    authorization_reference,
                )
                self.assertEqual(0, applied[0], applied)
                self.assertEqual(True, applied[1]["created"])
                self.assertEqual(True, applied[1]["decision_recorded"])
                readback = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "readback",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-analysis",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                )
                self.assertEqual(0, readback[0], readback)
            sync_path = workspace / ".agentic-ops" / "tasks" / "TAP-123" / "sync.json"
            sync = json.loads(sync_path.read_text(encoding="utf-8"))
            self.assertIn("jira_comment:run-1-analysis", sync["external_writes"])
            sync_record = sync["external_writes"]["jira_comment:run-1-analysis"]
            self.assertEqual(plan_id, sync_record["plan_id"])
            self.assertEqual(planned[1]["content_sha256"], sync_record["content_sha256"])
            self.assertEqual(readback[1]["body_sha256"], sync_record["evidence"]["body_sha256"])
            self.assertTrue(sync_record["evidence"]["created"])
            decisions_path = workspace / ".agentic-ops" / "tasks" / "TAP-123" / "decisions.ndjson"
            decisions = decisions_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(decisions))
            self.assertEqual(
                authorization_reference, json.loads(decisions[0])["reference"]
            )

    def test_cli_create_plan_apply_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = (
                "--workspace-root",
                str(workspace),
            )
            transport = FakeTransport()
            transport.create_meta_payload = {
                "projects": [
                    {
                        "key": "TAP",
                        "name": "tapdata",
                        "issuetypes": [
                            {
                                "id": "10100",
                                "name": "任务",
                                "subtask": False,
                                "fields": {
                                    "summary": {"required": True, "name": "摘要"},
                                    "project": {"required": True, "name": "项目"},
                                    "issuetype": {"required": True, "name": "事务类型"},
                                    "reporter": {"required": True, "name": "报告人"},
                                    "description": {"required": False, "name": "描述"},
                                },
                            }
                        ],
                    }
                ]
            }
            env_file = workspace / ".agentic-ops" / ".env"
            env_file.parent.mkdir(parents=True, exist_ok=True)
            env_file.write_text(
                "TEST_JIRA_EMAIL=owner@example.test\nTEST_JIRA_TOKEN=secret-token\n",
                encoding="utf-8",
            )
            desc = workspace / "desc.md"
            desc.write_text("为研发面新增 Jira 建卡能力。\n", encoding="utf-8")
            plan_file = ".agentic-ops/tasks/TAP/runs/run-create/jira-plans/create.json"
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                planned = self.run_cli(
                    *common,
                    "jira",
                    "create",
                    "plan",
                    "--project-key",
                    "TAP",
                    "--issuetype",
                    "任务",
                    "--summary",
                    "新增建卡能力",
                    "--description-file",
                    "desc.md",
                    "--idempotency-key",
                    "idem-create",
                    "--run-id",
                    "run-create",
                    "--plan-file",
                    plan_file,
                )
                self.assertEqual(0, planned[0])
                plan_id = str(planned[1]["plan_id"])
                authorization_reference = str(
                    planned[1]["authorization_user_confirmation_reference"]
                )
                applied = self.run_cli(
                    *common,
                    "jira",
                    "create",
                    "apply",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                    "--authorization-reference",
                    authorization_reference,
                    "--decision-summary",
                    "确认建卡",
                )
                self.assertEqual(0, applied[0])
                issue_key = str(applied[1]["external_id"])
                readback = self.run_cli(
                    *common,
                    "jira",
                    "create",
                    "readback",
                    "--issue-key",
                    issue_key,
                    "--idempotency-key",
                    "idem-create",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                )
                self.assertEqual(0, readback[0])
                self.assertEqual(issue_key, str(readback[1]["external_id"]))
                self.assertTrue(applied[1]["created"])

    def test_apply_rejects_unbound_authorization_before_decision_or_jira_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = (
                "--workspace-root",
                str(workspace),
            )
            self.assertEqual(
                0,
                self.run_cli(
                    *common,
                    "task",
                    "init",
                    "--connection-id",
                    "tap-cloud",
                    "--jira-issue-id",
                    "10001",
                    "--issue-key",
                    "TAP-123",
                    "--project-key",
                    "TAP",
                    "--agentic-run-id",
                    "run-1",
                )[0],
            )
            (workspace / "comment.md").write_text("确认严格校验授权引用。", encoding="utf-8")
            (workspace / ".agentic-ops" / ".env").write_text(
                "TEST_JIRA_EMAIL=owner@example.test\nTEST_JIRA_TOKEN=secret-token\n",
                encoding="utf-8",
            )
            plan_file = ".agentic-ops/tasks/TAP-123/runs/run-1/jira-plans/comment-plan.json"
            transport = FakeTransport()
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                planned = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "plan",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-authorization",
                    "--category",
                    "decision",
                    "--content-file",
                    "comment.md",
                    "--plan-file",
                    plan_file,
                )
                plan_id = str(planned[1]["plan_id"])
                invalid_references = (
                    "",
                    "arbitrary-approval",
                    f"user-confirmation:TAP-999:run-1:{plan_id}",
                    f"user-confirmation:TAP-123:run-2:{plan_id}",
                    f"user-confirmation:TAP-123:run-1:{plan_id}x",
                    f"jira-comment:TAP-123:0:{plan_id}",
                    f"jira-comment:TAP-123:42:{plan_id}x",
                )
                for reference in invalid_references:
                    with self.subTest(reference=reference):
                        blocked = self.run_cli(
                            *common,
                            "jira",
                            "comment",
                            "apply",
                            "--plan-file",
                            plan_file,
                            "--confirm-plan-id",
                            plan_id,
                            "--authorization-reference",
                            reference,
                        )
                        self.assertEqual(2, blocked[0])
                        self.assertEqual(
                            "jira_authorization_reference_invalid", blocked[1]["code"]
                        )

                valid_reference = str(
                    planned[1]["authorization_user_confirmation_reference"]
                )
                wrong_plan = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    "plan-wrong",
                    "--authorization-reference",
                    valid_reference,
                )
                self.assertEqual("jira_write_plan_mismatch", wrong_plan[1]["code"])

            decisions = (
                workspace
                / ".agentic-ops"
                / "tasks"
                / "TAP-123"
                / "decisions.ndjson"
            ).read_text(encoding="utf-8")
            self.assertEqual("", decisions)
            self.assertEqual([], transport.comments)

    def test_jira_comment_authorization_must_exist_and_bind_run_and_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = (
                "--workspace-root",
                str(workspace),
            )
            self.run_cli(
                *common,
                "task",
                "init",
                "--connection-id",
                "tap-cloud",
                "--jira-issue-id",
                "10001",
                "--issue-key",
                "TAP-123",
                "--project-key",
                "TAP",
                "--agentic-run-id",
                "run-1",
            )
            (workspace / "comment.md").write_text("执行经过 Jira 评论确认的写入。", encoding="utf-8")
            (workspace / ".agentic-ops" / ".env").write_text(
                "TEST_JIRA_EMAIL=owner@example.test\nTEST_JIRA_TOKEN=secret-token\n",
                encoding="utf-8",
            )
            plan_file = ".agentic-ops/tasks/TAP-123/runs/run-1/jira-plans/comment-plan.json"
            transport = FakeTransport()
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                planned = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "plan",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-confirmed",
                    "--category",
                    "decision",
                    "--content-file",
                    "comment.md",
                    "--plan-file",
                    plan_file,
                )
                plan_id = str(planned[1]["plan_id"])
                reference = f"jira-comment:TAP-123:42:{plan_id}"
                missing = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                    "--authorization-reference",
                    reference,
                )
                self.assertEqual("jira_authorization_reference_not_found", missing[1]["code"])
                self.assertEqual([], transport.comments)
                self.assertEqual(
                    ("GET", "/rest/api/3/issue/TAP-123/comment/42"),
                    transport.requests[-1],
                )

                marker = str(planned[1]["authorization_comment_marker"])
                transport.comments.append(
                    {
                        "id": "42",
                        "body": markdown_to_adf(
                            f"普通业务说明中提到了 {marker}，但它不是独立确认标记。"
                        ),
                        "author": {"accountId": "reviewer-1"},
                    }
                )
                ordinary_comment = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                    "--authorization-reference",
                    reference,
                )
                self.assertEqual(
                    "jira_authorization_reference_not_found",
                    ordinary_comment[1]["code"],
                )
                self.assertEqual(1, len(transport.comments))
                transport.comments[0]["body"] = markdown_to_adf(
                    f"研发工程师确认当前写入计划。\n\n{marker}"
                )
                applied = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    plan_file,
                    "--confirm-plan-id",
                    plan_id,
                    "--authorization-reference",
                    reference,
                )
            self.assertEqual(0, applied[0], applied)
            self.assertEqual("jira_comment", applied[1]["authorization_type"])
            self.assertEqual(2, len(transport.comments))

    def test_replanned_content_invalidates_old_authorization_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = (
                "--workspace-root",
                str(workspace),
            )
            self.run_cli(
                *common,
                "task",
                "init",
                "--connection-id",
                "tap-cloud",
                "--jira-issue-id",
                "10001",
                "--issue-key",
                "TAP-123",
                "--project-key",
                "TAP",
                "--agentic-run-id",
                "run-1",
            )
            content_path = workspace / "comment.md"
            content_path.write_text("第一版计划已确认。", encoding="utf-8")
            (workspace / ".agentic-ops" / ".env").write_text(
                "TEST_JIRA_EMAIL=owner@example.test\nTEST_JIRA_TOKEN=secret-token\n",
                encoding="utf-8",
            )
            plan_file = ".agentic-ops/tasks/TAP-123/runs/run-1/jira-plans/comment-plan.json"
            transport = FakeTransport()
            plan_arguments = (
                *common,
                "jira",
                "comment",
                "plan",
                "--issue-key",
                "TAP-123",
                "--idempotency-key",
                "run-1-replan",
                "--category",
                "plan",
                "--content-file",
                "comment.md",
                "--plan-file",
                plan_file,
            )
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                first = self.run_cli(*plan_arguments)
                old_reference = str(
                    first[1]["authorization_user_confirmation_reference"]
                )
                content_path.write_text("第二版计划改变了写入内容。", encoding="utf-8")
                second_plan_file = (
                    ".agentic-ops/tasks/TAP-123/runs/run-1/"
                    "jira-plans/comment-plan-v2.json"
                )
                second_arguments = tuple(
                    second_plan_file if value == plan_file else value
                    for value in plan_arguments
                )
                second = self.run_cli(*second_arguments)
                self.assertNotEqual(first[1]["plan_id"], second[1]["plan_id"])
                blocked = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "apply",
                    "--plan-file",
                    second_plan_file,
                    "--confirm-plan-id",
                    str(second[1]["plan_id"]),
                    "--authorization-reference",
                    old_reference,
                )
            self.assertEqual(2, blocked[0])
            self.assertEqual("jira_authorization_reference_invalid", blocked[1]["code"])
            self.assertEqual([], transport.comments)
            decisions_path = (
                workspace
                / ".agentic-ops"
                / "tasks"
                / "TAP-123"
                / "decisions.ndjson"
            )
            self.assertEqual("", decisions_path.read_text(encoding="utf-8"))

    def test_plan_file_outside_runtime_state_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            content = workspace / "comment.md"
            content.write_text("分析内容使用中文。", encoding="utf-8")
            common = (
                "--workspace-root",
                str(workspace),
            )
            (workspace / ".agentic-ops" / ".env").write_text(
                "TEST_JIRA_EMAIL=owner@example.test\nTEST_JIRA_TOKEN=secret-token\n",
                encoding="utf-8",
            )
            self.run_cli(
                *common,
                "task",
                "init",
                "--connection-id",
                "tap-cloud",
                "--jira-issue-id",
                "10001",
                "--issue-key",
                "TAP-123",
                "--project-key",
                "TAP",
                "--agentic-run-id",
                "run-1",
            )
            with mock.patch("ao_work.jira.cli.UrllibJiraTransport", return_value=FakeTransport()):
                blocked = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "plan",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-1-analysis",
                    "--category",
                    "analysis",
                    "--content-file",
                    "comment.md",
                    "--plan-file",
                    "tracked-plan.json",
                )
            self.assertEqual(2, blocked[0])
            self.assertEqual("jira_plan_path_not_bound", blocked[1]["code"])

    def test_plan_path_rejects_reserved_cross_task_cross_run_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            common = self.initialize_task(workspace)
            content = workspace / "comment.md"
            content.write_text("验证计划路径严格绑定当前任务运行。", encoding="utf-8")
            agent_sentinel = (workspace / ".agentic-ops" / "agent.json").read_text(
                encoding="utf-8"
            )
            outside = Path(temporary) / "outside-plan.json"
            outside.write_text("outside-unchanged\n", encoding="utf-8")
            linked_plan = (
                workspace
                / ".agentic-ops/tasks/TAP-123/runs/run-1/jira-plans/linked.json"
            )
            linked_plan.parent.mkdir(parents=True)
            linked_plan.symlink_to(outside)

            candidates = {
                "agent": ".agentic-ops/agent.json",
                "env": ".agentic-ops/.env",
                "cross-task": (
                    ".agentic-ops/tasks/TAP-999/runs/run-1/"
                    "jira-plans/comment.json"
                ),
                "cross-run": (
                    ".agentic-ops/tasks/TAP-123/runs/run-2/"
                    "jira-plans/comment.json"
                ),
                "symlink": str(linked_plan),
            }
            transport = FakeTransport()
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                for label, candidate in candidates.items():
                    with self.subTest(label=label):
                        blocked = self.run_cli(
                            *common,
                            "jira",
                            "comment",
                            "plan",
                            "--issue-key",
                            "TAP-123",
                            "--idempotency-key",
                            f"run-path-{label}",
                            "--category",
                            "analysis",
                            "--content-file",
                            "comment.md",
                            "--plan-file",
                            candidate,
                        )
                        self.assertEqual(2, blocked[0])
                        self.assertIn(
                            blocked[1]["code"],
                            {"jira_plan_path_not_bound", "jira_plan_path_unsafe"},
                        )
            self.assertEqual(
                agent_sentinel,
                (workspace / ".agentic-ops" / "agent.json").read_text(
                    encoding="utf-8"
                ),
            )
            self.assertFalse((workspace / ".agentic-ops/.env").exists())
            self.assertIn("secret-token", (install / "user/.env").read_text())
            self.assertEqual("outside-unchanged\n", outside.read_text(encoding="utf-8"))

    def test_plan_never_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, workspace = self.prepare(Path(temporary))
            common = self.initialize_task(workspace)
            (workspace / "comment.md").write_text("首次计划内容。", encoding="utf-8")
            plan_file = (
                ".agentic-ops/tasks/TAP-123/runs/run-1/"
                "jira-plans/no-overwrite.json"
            )
            transport = FakeTransport()
            arguments = (
                *common,
                "jira",
                "comment",
                "plan",
                "--issue-key",
                "TAP-123",
                "--idempotency-key",
                "run-no-overwrite",
                "--category",
                "analysis",
                "--content-file",
                "comment.md",
                "--plan-file",
                plan_file,
            )
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                first = self.run_cli(*arguments)
                before = (workspace / plan_file).read_bytes()
                second = self.run_cli(*arguments)
            self.assertEqual(0, first[0])
            self.assertEqual(2, second[0])
            self.assertEqual("jira_plan_file_exists", second[1]["code"])
            self.assertEqual(before, (workspace / plan_file).read_bytes())

    def test_outbound_inputs_reject_managed_git_symlink_hardlink_and_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, workspace = self.prepare(Path(temporary))
            common = self.initialize_task(workspace)
            git_secret = workspace / ".git/config"
            git_secret.parent.mkdir(parents=True)
            git_secret.write_text("# 中文\npassword=git-secret\n", encoding="utf-8")
            root_env = workspace / ".env"
            root_env.write_text("# 中文\nAPI_TOKEN=root-secret\n", encoding="utf-8")
            assigned = workspace / "comment.md"
            assigned.write_text("# 中文\nAPI_TOKEN=plain-secret\n", encoding="utf-8")
            bare_token = workspace / "token-comment.md"
            bare_token.write_text(
                "中文说明 ghp_0123456789abcdefghijklmnopqrstuvwxyzABCD\n",
                encoding="utf-8",
            )
            linked = workspace / "linked-comment.md"
            linked.symlink_to(workspace / ".agentic-ops/.env")
            external_hardlink_target = Path(temporary) / "outside-comment.md"
            external_hardlink_target.write_text("中文普通内容。\n", encoding="utf-8")
            hardlinked = workspace / "hardlinked-comment.md"
            os.link(external_hardlink_target, hardlinked)
            fifo = workspace / "fifo-comment.md"
            os.mkfifo(fifo)
            candidates = {
                "managed": ".agentic-ops/.env",
                "git": ".git/config",
                "root-env": ".env",
                "assigned-secret": "comment.md",
                "token-family": "token-comment.md",
                "symlink": "linked-comment.md",
                "hardlink": "hardlinked-comment.md",
                "fifo": "fifo-comment.md",
            }
            transport = FakeTransport()
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                for index, (label, candidate) in enumerate(candidates.items()):
                    with self.subTest(label=label):
                        blocked = self.run_cli(
                            *common,
                            "jira",
                            "comment",
                            "plan",
                            "--issue-key",
                            "TAP-123",
                            "--idempotency-key",
                            f"run-secret-{index}",
                            "--category",
                            "analysis",
                            "--content-file",
                            candidate,
                            "--plan-file",
                            (
                                ".agentic-ops/tasks/TAP-123/runs/run-1/"
                                f"jira-plans/secret-{index}.json"
                            ),
                        )
                        self.assertEqual(2, blocked[0])
                        self.assertIn(
                            blocked[1]["code"],
                            {
                                "workspace_outbound_file_forbidden",
                                "workspace_outbound_file_unsafe",
                                "workspace_outbound_secret_forbidden",
                            },
                        )
            self.assertEqual([], transport.comments)
            self.assertEqual([], list(
                (workspace / ".agentic-ops/tasks/TAP-123/runs/run-1/jira-plans").glob(
                    "secret-*.json"
                )
            ))

    def test_payload_matching_current_credentials_is_blocked_before_plan_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, workspace = self.prepare(Path(temporary))
            common = self.initialize_task(workspace)
            (workspace / "comment.md").write_text(
                "中文说明中误含当前凭证 secret-token。",
                encoding="utf-8",
            )
            plan_file = (
                ".agentic-ops/tasks/TAP-123/runs/run-1/"
                "jira-plans/credential.json"
            )
            transport = FakeTransport()
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                blocked = self.run_cli(
                    *common,
                    "jira",
                    "comment",
                    "plan",
                    "--issue-key",
                    "TAP-123",
                    "--idempotency-key",
                    "run-current-credential",
                    "--category",
                    "analysis",
                    "--content-file",
                    "comment.md",
                    "--plan-file",
                    plan_file,
                )
            self.assertEqual(2, blocked[0])
            self.assertEqual("jira_credential_exposure_forbidden", blocked[1]["code"])
            self.assertFalse((workspace / plan_file).exists())

    def test_report_write_rejects_managed_and_secret_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, workspace = self.prepare(Path(temporary))
            common = self.initialize_task(workspace)
            secret = workspace / "analysis.md"
            secret.write_text("# 中文\nPRIVATE_KEY=do-not-export\n", encoding="utf-8")
            for label, candidate in (
                ("managed", ".agentic-ops/.env"),
                ("secret", "analysis.md"),
            ):
                with self.subTest(label=label):
                    blocked = self.run_cli(
                        *common,
                        "report",
                        "write",
                        "--issue-key",
                        "TAP-123",
                        "--agentic-run-id",
                        "run-1",
                        "--kind",
                        "analysis",
                        "--content-file",
                        candidate,
                    )
                    self.assertEqual(2, blocked[0])
            self.assertFalse(
                (workspace / ".agentic-ops/tasks/TAP-123/reports/analysis.md").exists()
            )

    def test_cli_jira_list_returns_tasks_with_total(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, workspace = self.prepare(Path(temporary))
            common = self.initialize_task(workspace)
            transport = FakeTransport()
            transport.search_issues = [
                {
                    "id": "10001",
                    "key": "TAP-1",
                    "fields": {
                        "project": {"key": "TAP"},
                        "summary": "任务一",
                        "status": {"name": "打开"},
                        "issuetype": {"name": "任务"},
                        "assignee": {"accountId": "owner-1"},
                        "priority": {"name": "Highest"},
                        "updated": "2026-08-19T01:00:00.000+0800",
                    },
                },
                {
                    "id": "10002",
                    "key": "TAP-2",
                    "fields": {
                        "project": {"key": "TAP"},
                        "summary": "任务二",
                        "status": {"name": "正在进行"},
                        "issuetype": {"name": "缺陷"},
                        "assignee": {"accountId": "owner-1"},
                        "priority": {"name": "Low"},
                        "updated": "2026-08-19T02:00:00.000+0800",
                    },
                },
            ]
            transport.search_total = 5
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport", return_value=transport
            ):
                exit_code, result, _ = self.run_cli(
                    *common,
                    "jira",
                    "list",
                )
            self.assertEqual(0, exit_code)
            self.assertEqual("jira_list", result["operation"])
            self.assertEqual(5, result["total"])
            self.assertEqual(2, result["returned"])
            self.assertEqual(
                "project = TAP ORDER BY priority DESC, updated ASC",
                result["jql"],
            )
            tasks: list[dict[str, object]] = result["tasks"]  # type: ignore[assignment]
            self.assertEqual("TAP-1", tasks[0]["issue_key"])
            self.assertEqual("任务一", tasks[0]["summary"])
            self.assertEqual("Highest", tasks[0]["priority"])
            self.assertEqual("正在进行", tasks[1]["status"])
            # 走 /rest/api/3/search/jql（tapdata /search 已移除）。
            self.assertEqual(
                ("GET", "/rest/api/3/search/jql"),
                transport.requests[-1],
            )
            # 一页默认 10 个任务。
            self.assertEqual("10", transport.queries[-1]["maxResults"])

    def test_cli_jira_list_falls_back_to_default_jql_when_task_query_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace = self.prepare(Path(temporary))
            profile = install / "developer" / "standards" / "projects" / "demo" / "profile.yaml"
            profile.write_text(
                "profile_id: demo\n"
                "connection_id: tap-cloud\n"
                "jira:\n"
                "  project_key: TAP\n"
                "  issue_types: [任务]\n"
                "statuses: {}\n"
                "transitions: {}\n"
                "repositories:\n"
                "  default: tapdata/tapdata\n",
                encoding="utf-8",
            )
            common = self.initialize_task(workspace)
            transport = FakeTransport()
            transport.search_issues = [
                {
                    "id": "10001",
                    "key": "TAP-1",
                    "fields": {
                        "project": {"key": "TAP"},
                        "summary": "任务一",
                        "status": {"name": "打开"},
                        "issuetype": {"name": "任务"},
                        "assignee": {"accountId": "owner-1"},
                        "priority": {"name": "Highest"},
                        "updated": "2026-08-19T01:00:00.000+0800",
                    },
                }
            ]
            with mock.patch(
                "ao_work.jira.cli.UrllibJiraTransport",
                return_value=transport,
            ):
                exit_code, result, _ = self.run_cli(*common, "jira", "list")
            self.assertEqual(0, exit_code)
            self.assertIn("assignee = currentUser()", str(result["jql"]))
            self.assertTrue(
                str(result["jql"]).endswith(
                    "ORDER BY priority DESC, updated ASC"
                ),
                result["jql"],
            )
            self.assertEqual(1, result["returned"])
