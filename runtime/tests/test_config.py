from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentic_ops.config import load_jira_context
from agentic_ops.output import RuntimeErrorResult
from agentic_ops.workspace import PROJECT_EXECUTION, resolve_workspace


CONNECTION = """\
schema_version: 1
connection_id: tap-cloud
base_url: https://base.example.test
auth:
  email_env: TEST_JIRA_EMAIL
  token_env: TEST_JIRA_TOKEN
"""

PROFILE = """\
schema_version: 1
profile_id: demo
connection_id: tap-cloud
jira:
  project_key: TAP
  issue_types: [任务]
  task_query: project = TAP
fields:
  analysis:
    source: jira_field
    jira_field: customfield_10001
    state: active
    writable: true
statuses:
  待办: waiting_takeover
transitions:
  start_progress:
    name: In Progress
"""


class ConfigTest(unittest.TestCase):
    def prepare(self, root: Path) -> tuple[Path, Path]:
        install = root / "install"
        workspace = root / "workspace"
        connection = install / "standards" / "connections" / "tap-cloud.yaml"
        profile = install / "standards" / "projects" / "demo" / "profile.yaml"
        connection.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        connection.write_text(CONNECTION, encoding="utf-8")
        profile.write_text(PROFILE, encoding="utf-8")
        agent = workspace / ".agentic-ops" / "agent.json"
        agent.parent.mkdir(parents=True)
        agent.write_text(
            json.dumps(
                {
                    "mode": "project_execution",
                    "project_profile": "demo",
                    "connection_id": "tap-cloud",
                }
            ),
            encoding="utf-8",
        )
        return install, workspace

    def test_loads_layered_connection_and_isolates_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            user_env = install / "user" / ".env"
            user_env.parent.mkdir(parents=True)
            user_env.write_text("TEST_JIRA_TOKEN=user-token\nTEST_JIRA_EMAIL=user@example.test\n", encoding="utf-8")
            workspace_env = workspace_root / ".agentic-ops" / ".env"
            workspace_env.write_text("TEST_JIRA_EMAIL=workspace@example.test\n", encoding="utf-8")
            override = workspace_root / ".agentic-ops" / "connections" / "tap-cloud.local.yaml"
            override.parent.mkdir()
            override.write_text("base_url: https://workspace.example.test\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                workspace = resolve_workspace(str(workspace_root), PROJECT_EXECUTION)
                context = load_jira_context(workspace, install)
            self.assertEqual("https://workspace.example.test", context.connection.base_url)
            self.assertEqual("workspace@example.test", context.email)
            self.assertEqual("user-token", context.token)
            self.assertEqual(
                {"email_configured": True, "token_configured": True},
                context.credential_status(),
            )
            self.assertEqual({"customfield_10001"}, context.profile.active_custom_field_ids())

    def test_process_environment_has_highest_secret_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            with mock.patch.dict(
                os.environ,
                {"TEST_JIRA_EMAIL": "process@example.test", "TEST_JIRA_TOKEN": "process-token"},
                clear=True,
            ):
                context = load_jira_context(
                    resolve_workspace(str(workspace_root), PROJECT_EXECUTION), install
                )
            self.assertEqual("process@example.test", context.email)
            self.assertEqual("process-token", context.token)

    def test_blocks_connection_profile_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            agent_path = workspace_root / ".agentic-ops" / "agent.json"
            agent_path.write_text(
                json.dumps(
                    {
                        "mode": "project_execution",
                        "project_profile": "demo",
                        "connection_id": "another-cloud",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_jira_context(
                    resolve_workspace(str(workspace_root), PROJECT_EXECUTION), install
                )
            self.assertEqual("jira_workspace_mismatch", captured.exception.code)

    def test_missing_credentials_are_named_but_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            with mock.patch.dict(os.environ, {}, clear=True):
                context = load_jira_context(
                    resolve_workspace(str(workspace_root), PROJECT_EXECUTION), install
                )
            with self.assertRaises(RuntimeErrorResult) as captured:
                context.require_credentials()
            self.assertEqual("jira_credentials_missing", captured.exception.code)
            self.assertIn("TEST_JIRA_TOKEN", captured.exception.message)
