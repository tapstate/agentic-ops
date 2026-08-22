from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_work.config import load_jira_context, load_jira_connection, load_project_profile
from ao_work.config.loader import _install_identity_ref, install_entry_sha256
from ao_work.installation import load_install_identity, save_install_credentials, save_install_identity
from ao_work.output import RuntimeErrorResult
from ao_work.workspace import resolve_developer_workspace


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
repositories:
  default: tapdata/tapdata
"""


class ConfigTest(unittest.TestCase):
    def prepare(self, root: Path) -> tuple[Path, Path]:
        install = root / "install"
        entry = install / "bin" / "ao-work"
        entry.parent.mkdir(parents=True)
        entry.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        entry.chmod(0o700)
        workspace = root / "workspace"
        connection = install / "developer" / "standards" / "connections" / "tap-cloud.yaml"
        profile = install / "developer" / "standards" / "projects" / "demo" / "profile.yaml"
        connection.parent.mkdir(parents=True)
        profile.parent.mkdir(parents=True)
        connection.write_text(CONNECTION, encoding="utf-8")
        profile.write_text(PROFILE, encoding="utf-8")
        agent = workspace / ".agentic-ops" / "agent.json"
        agent.parent.mkdir(parents=True)
        source = root / "source"
        overlay = workspace / ".agentic-ops" / "profiles" / "demo.local.yaml"
        overlay.parent.mkdir()
        overlay.write_text(
            "workspace:\n"
            f"  source_root: {source}\n"
            "  repository: tapdata/tapdata\n",
            encoding="utf-8",
        )
        identity = {
            "agent_id": "developer-test",
            "jira_email": "owner@example.test",
            "execution_identity": {
                "git_author_name": "Developer Test",
                "git_author_email": "owner@example.test",
                "git_committer_name": "Developer Test",
                "git_committer_email": "owner@example.test",
                "github_actor_login": "developer-test",
            },
        }
        save_install_identity(install, identity)
        agent.write_text(
            json.dumps(
                {
                    "schema_version": 5,
                    "workplane": "developer",
                    "project_profile": "demo",
                    "connection_id": "tap-cloud",
                    "jira_base_url": "https://base.example.test",
                    "jira_site": "base.example.test",
                    "install_identity_ref": _install_identity_ref(
                        install, load_install_identity(install)
                    ),
                    "workspace_entry": ".agentic-ops/bin/ao-work",
                    "install_entry_sha256": install_entry_sha256(install),
                    "jira_project": "TAP",
                    "source_root": str(source),
                    "repository": "tapdata/tapdata",
                }
            ),
            encoding="utf-8",
        )
        return install, workspace

    def test_loads_layered_connection_and_isolates_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            save_install_credentials(install, "owner@example.test", "user-token")
            workspace_env = workspace_root / ".agentic-ops" / ".env"
            workspace_env.write_text(
                "TEST_JIRA_EMAIL=workspace@example.test\nTEST_JIRA_TOKEN=workspace-token\n",
                encoding="utf-8",
            )
            override = workspace_root / ".agentic-ops" / "connections" / "tap-cloud.local.yaml"
            override.parent.mkdir()
            override.write_text("base_url: https://workspace.example.test\n", encoding="utf-8")
            agent_path = workspace_root / ".agentic-ops" / "agent.json"
            agent_payload = json.loads(agent_path.read_text())
            agent_payload.update(
                jira_base_url="https://workspace.example.test",
                jira_site="workspace.example.test",
            )
            agent_path.write_text(json.dumps(agent_payload), encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                workspace = resolve_developer_workspace(str(workspace_root))
                context = load_jira_context(workspace, install)
            self.assertEqual("https://workspace.example.test", context.connection.base_url)
            self.assertEqual("owner@example.test", context.email)
            self.assertEqual("user-token", context.token)
            self.assertEqual(
                {"email_configured": True, "token_configured": True},
                context.credential_status(),
            )
            self.assertEqual({"customfield_10001"}, context.profile.active_custom_field_ids())

    def test_managed_agent_overlays_and_env_reject_hardlinks(self) -> None:
        for leaf in ("agent", "profile", "connection"):
            with self.subTest(leaf=leaf), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                install, workspace_root = self.prepare(root)
                workspace = resolve_developer_workspace(str(workspace_root))
                if leaf == "agent":
                    managed = workspace_root / ".agentic-ops/agent.json"
                elif leaf == "profile":
                    managed = workspace_root / ".agentic-ops/profiles/demo.local.yaml"
                elif leaf == "connection":
                    managed = workspace_root / ".agentic-ops/connections/tap-cloud.local.yaml"
                    managed.parent.mkdir()
                    managed.write_text("timeout_seconds: 20\n", encoding="utf-8")
                original = managed.read_bytes()
                external = root / f"external-{leaf}"
                external.write_bytes(original)
                managed.unlink()
                os.link(external, managed)

                with self.assertRaises(RuntimeErrorResult) as captured:
                    if leaf == "agent":
                        resolve_developer_workspace(str(workspace_root))
                    else:
                        load_jira_context(workspace, install)
                self.assertEqual("managed_file_unsafe", captured.exception.code)
                self.assertEqual(original, external.read_bytes())

    def test_profile_selects_connection_without_duplicate_workspace_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            agent_path = workspace_root / ".agentic-ops" / "agent.json"
            agent_path.write_text(
                json.dumps(
                    {
                        "schema_version": 5,
                        "workplane": "developer",
                        "project_profile": "demo",
                        "jira_base_url": "https://base.example.test",
                        "jira_site": "base.example.test",
                        "install_identity_ref": _install_identity_ref(
                            install, load_install_identity(install)
                        ),
                        "workspace_entry": ".agentic-ops/bin/ao-work",
                        "install_entry_sha256": install_entry_sha256(install),
                        "connection_id": "tap-cloud",
                        "jira_project": "TAP",
                        "source_root": str(Path(temporary) / "source"),
                        "repository": "tapdata/tapdata",
                    }
                ),
                encoding="utf-8",
            )
            save_install_credentials(install, "owner@example.test", "token-value")
            context = load_jira_context(
                resolve_developer_workspace(str(workspace_root)), install
            )
            self.assertEqual("tap-cloud", context.connection.connection_id)

    def test_process_environment_is_not_implicitly_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            workspace_env = workspace_root / ".agentic-ops" / ".env"
            workspace_env.write_text(
                "TEST_JIRA_EMAIL=workspace@example.test\nTEST_JIRA_TOKEN=workspace-token\n",
                encoding="utf-8",
            )
            save_install_credentials(install, "owner@example.test", "install-token")
            with mock.patch.dict(
                os.environ,
                {"TEST_JIRA_EMAIL": "process@example.test", "TEST_JIRA_TOKEN": "process-token"},
                clear=True,
            ):
                context = load_jira_context(
                    resolve_developer_workspace(str(workspace_root)), install
                )
            self.assertEqual("owner@example.test", context.email)
            self.assertEqual("install-token", context.token)

    def test_blocks_connection_profile_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            agent_path = workspace_root / ".agentic-ops" / "agent.json"
            agent_path.write_text(
                json.dumps(
                    {
                        "workplane": "developer",
                        "project_profile": "demo",
                        "connection_id": "another-cloud",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_jira_context(
                    resolve_developer_workspace(str(workspace_root)), install
                )
            self.assertEqual("jira_workspace_mismatch", captured.exception.code)

    def test_missing_credentials_are_named_but_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            with mock.patch.dict(os.environ, {}, clear=True):
                context = load_jira_context(
                    resolve_developer_workspace(str(workspace_root)), install
                )
            with self.assertRaises(RuntimeErrorResult) as captured:
                context.require_credentials()
            self.assertEqual("jira_credentials_missing", captured.exception.code)
            self.assertIn("TEST_JIRA_TOKEN", captured.exception.message)

    def test_profile_and_connection_ids_cannot_escape_configuration_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            for loader, arguments in (
                (load_project_profile, (install, "../../outside")),
                (load_jira_connection, (install, "../outside")),
            ):
                with self.subTest(loader=loader.__name__):
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        loader(*arguments)
                    self.assertEqual("configuration_id_invalid", captured.exception.code)

    def test_context_rejects_unvalidated_profile_id_before_path_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            agent_path = workspace_root / ".agentic-ops" / "agent.json"
            agent_path.write_text(
                json.dumps(
                    {
                        "workplane": "developer",
                        "project_profile": "../../outside",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeErrorResult) as captured:
                load_jira_context(
                    resolve_developer_workspace(str(workspace_root)), install
                )
            self.assertEqual("configuration_id_invalid", captured.exception.code)

    def test_tapdata_custom_fields_remain_pending_until_topic_acceptance(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        profile = load_project_profile(repository_root, "tapdata")
        for logical_name in ("issue_analysis", "fix_details", "verification_method"):
            with self.subTest(logical_name=logical_name):
                mapping = profile.fields[logical_name]
                self.assertEqual("pending_validation", mapping.state)
                self.assertFalse(mapping.writable)
                self.assertNotIn(mapping.jira_field, profile.active_custom_field_ids())

    def test_connection_requires_https_site_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install, _ = self.prepare(root)
            path = install / "developer/standards/connections/tap-cloud.yaml"
            for value in (
                "http://jira.example.test",
                "https://user@jira.example.test",
                "https://jira.example.test/rest/api",
                "https://jira.example.test?next=evil",
                "https://jira.example.test#fragment",
            ):
                with self.subTest(value=value):
                    path.write_text(CONNECTION.replace("https://base.example.test", value), encoding="utf-8")
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        load_jira_connection(install, "tap-cloud")
                    self.assertEqual("configuration_invalid", captured.exception.code)

    def test_connection_rejects_ambiguous_credentials_and_invalid_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install, _ = self.prepare(root)
            path = install / "developer/standards/connections/tap-cloud.yaml"
            invalid_fragments = (
                "auth:\n  email_env: SAME\n  token_env: SAME\n",
                "auth:\n  email_env: bad=name\n  token_env: TEST_JIRA_TOKEN\n",
                "auth:\n  email_env: TEST_JIRA_EMAIL\n  token_env: lower_token\n",
                "auth:\n  email_env: TEST_JIRA_EMAIL\n  token_env: TEST_JIRA_TOKEN\ntimeout_seconds: .nan\n",
                "auth:\n  email_env: TEST_JIRA_EMAIL\n  token_env: TEST_JIRA_TOKEN\ntimeout_seconds: .inf\n",
                "auth:\n  email_env: TEST_JIRA_EMAIL\n  token_env: TEST_JIRA_TOKEN\ntimeout_seconds: 0\n",
                "auth:\n  email_env: TEST_JIRA_EMAIL\n  token_env: TEST_JIRA_TOKEN\ntimeout_seconds: 301\n",
            )
            prefix = (
                "schema_version: 1\n"
                "connection_id: tap-cloud\n"
                "base_url: https://base.example.test\n"
            )
            for fragment in invalid_fragments:
                with self.subTest(fragment=fragment):
                    path.write_text(prefix + fragment, encoding="utf-8")
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        load_jira_connection(install, "tap-cloud")
                    self.assertEqual("configuration_invalid", captured.exception.code)

    def test_connection_overlay_drift_blocks_before_credentials_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            override = workspace_root / ".agentic-ops/connections/tap-cloud.local.yaml"
            override.parent.mkdir()
            override.write_text("base_url: https://evil.example.test\n", encoding="utf-8")
            workspace = resolve_developer_workspace(str(workspace_root))
            with mock.patch(
                "ao_work.installation.load_install_credentials"
            ) as credential_read:
                with self.assertRaises(RuntimeErrorResult) as captured:
                    load_jira_context(workspace, install)
            self.assertEqual("jira_workspace_identity_drift", captured.exception.code)
            credential_read.assert_not_called()

    def test_effective_profile_identity_drift_blocks_before_credentials_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            overlay = workspace_root / ".agentic-ops/profiles/demo.local.yaml"
            overlay.write_text(
                "jira:\n"
                "  project_key: OTHER\n"
                "workspace:\n"
                f"  source_root: {Path(temporary) / 'other-source'}\n"
                "  repository: attacker/repository\n",
                encoding="utf-8",
            )
            workspace = resolve_developer_workspace(str(workspace_root))
            with mock.patch(
                "ao_work.installation.load_install_credentials"
            ) as credential_read:
                with self.assertRaises(RuntimeErrorResult) as captured:
                    load_jira_context(workspace, install)
            self.assertEqual("workspace_project_identity_drift", captured.exception.code)
            credential_read.assert_not_called()

    def test_profile_overlay_symlink_is_rejected_before_credentials_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install, workspace_root = self.prepare(root)
            overlay = workspace_root / ".agentic-ops/profiles/demo.local.yaml"
            external = root / "external-profile.yaml"
            external.write_text(overlay.read_text(encoding="utf-8"), encoding="utf-8")
            overlay.unlink()
            overlay.symlink_to(external)
            workspace = resolve_developer_workspace(str(workspace_root))
            with mock.patch(
                "ao_work.installation.load_install_credentials"
            ) as credential_read:
                with self.assertRaises(RuntimeErrorResult) as captured:
                    load_jira_context(workspace, install)
            self.assertEqual("workspace_managed_path_unsafe", captured.exception.code)
            credential_read.assert_not_called()

    def test_legacy_workspace_requires_reinitialization_before_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            install, workspace_root = self.prepare(Path(temporary))
            agent_path = workspace_root / ".agentic-ops/agent.json"
            payload = json.loads(agent_path.read_text())
            payload["schema_version"] = 2
            agent_path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch(
                "ao_work.installation.load_install_credentials"
            ) as credential_read:
                with self.assertRaises(RuntimeErrorResult) as captured:
                    load_jira_context(
                        resolve_developer_workspace(str(workspace_root)), install
                    )
            self.assertEqual(
                "workspace_jira_identity_upgrade_required", captured.exception.code
            )
            credential_read.assert_not_called()
