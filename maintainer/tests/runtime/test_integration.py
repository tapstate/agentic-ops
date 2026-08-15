from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_maint.integration.model import confirmation_digest
from ao_maint.integration.service import IntegrationService
from ao_maint.output import RuntimeErrorResult


class IntegrationServiceTest(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[3]

    def test_prepare_only_writes_explicit_checklist_without_host_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "TAP-12289.json"
            host_install = root / "host-home" / ".agentic-ops"
            host_install.mkdir(parents=True)
            (host_install / "secret.txt").write_text("must-not-leak", encoding="utf-8")

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(root / "host-home"),
                        "TAPDATA_JIRA_API_TOKEN": "must-not-leak-token",
                    },
                    clear=False,
                ),
                mock.patch("subprocess.run", side_effect=AssertionError("prepare 不得运行子进程")),
            ):
                result = IntegrationService(root).prepare_offline(
                    "TAP-12289", output=str(output)
                )

            content = output.read_text(encoding="utf-8")
            self.assertFalse(result["host_state_read"])
            self.assertNotIn("must-not-leak", content)
            self.assertNotIn(str(host_install), content)
            self.assertEqual("REQUIRED", json.loads(content)["agentic_ops"]["repository"])

    def test_run_blocks_incomplete_manifest_before_any_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            IntegrationService(root).prepare_offline("TAP-12289", output=str(manifest))
            with mock.patch(
                "subprocess.run", side_effect=AssertionError("无效清单不得运行子进程")
            ):
                with self.assertRaises(RuntimeErrorResult) as captured:
                    IntegrationService(root).run_offline("TAP-12289", str(manifest))
            self.assertEqual("integration_manifest_invalid", captured.exception.code)

    def test_offline_manifest_rejects_duplicate_keys_and_non_finite_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            manifest = self._offline_manifest(repository)
            manifest["confirmation"]["confirmed_manifest_sha256"] = confirmation_digest(
                manifest
            )
            raw = json.dumps(manifest)
            payloads = {
                "duplicate": raw.replace(
                    '"schema_version": 1,',
                    '"schema_version": 1, "schema_version": 1,',
                    1,
                ),
                "non-finite": raw.replace('"schema_version": 1', '"schema_version": NaN', 1),
            }
            for name, content in payloads.items():
                with self.subTest(name=name):
                    path = root / f"{name}.json"
                    path.write_text(content, encoding="utf-8")
                    with mock.patch(
                        "subprocess.run",
                        side_effect=AssertionError("非法 JSON 不得运行子进程"),
                    ):
                        with self.assertRaises(RuntimeErrorResult) as captured:
                            IntegrationService(self.ROOT).run_offline(
                                "TAP-12289", str(path)
                            )
                    self.assertEqual(
                        "integration_manifest_invalid", captured.exception.code
                    )

    def test_offline_fake_runs_deployment_to_fixture_evidence_readback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            task_repository = root / "task-repository"
            task_repository.mkdir()
            (task_repository / "fixture.txt").write_text("ok\n", encoding="utf-8")
            verification = task_repository / "verify-fixture.sh"
            verification.write_text(
                "#!/bin/sh\nset -eu\ntest \"$(cat fixture.txt)\" = ok\n",
                encoding="utf-8",
            )
            verification.chmod(0o700)
            self._git(task_repository, "init", "--initial-branch", "main")
            self._git(task_repository, "add", "fixture.txt", "verify-fixture.sh")
            self._git(
                task_repository,
                "-c",
                "user.name=Integration Test",
                "-c",
                "user.email=integration@example.invalid",
                "commit",
                "-m",
                "fixture",
            )
            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    self._offline_manifest(task_repository),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            payload["confirmation"]["confirmed_manifest_sha256"] = confirmation_digest(payload)
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            try:
                result = IntegrationService(self.ROOT).run_offline(
                    "TAP-12289", str(manifest)
                )
            except RuntimeErrorResult as error:
                self.fail(f"{error.code}: {error.message}; details={error.details}")

            self.assertEqual("offline_fixture_completed", result["task_completion"])
            self.assertFalse(result["production_jira_completed"])
            self.assertEqual("completed", result["cleanup_status"])
            ordered_steps = [item["step"] for item in result["steps"]]
            steps = set(ordered_steps)
            self.assertIn("developer_bootstrap_install", steps)
            self.assertIn("ao_work_workspace_init_--project", steps)
            self.assertIn("ao_work_jira_comment_plan", steps)
            self.assertIn("fixture_jira_plan_contract", steps)
            self.assertIn("ao_work_jira_comment_apply", steps)
            self.assertIn("ao_work_jira_comment_readback", steps)
            self.assertIn("fixture_evidence_readback", steps)
            self.assertTrue(any(step.startswith("verification_") for step in steps))
            self.assertLess(
                ordered_steps.index("ao_work_jira_comment_plan"),
                ordered_steps.index("fixture_jira_plan_contract"),
            )
            self.assertLess(
                ordered_steps.index("fixture_jira_plan_contract"),
                ordered_steps.index("ao_work_jira_comment_apply"),
            )
            self.assertLess(
                ordered_steps.index("ao_work_jira_comment_apply"),
                ordered_steps.index("ao_work_jira_comment_readback"),
            )
            plan_contract = next(
                item
                for item in result["steps"]
                if item["step"] == "fixture_jira_plan_contract"
            )
            self.assertEqual(
                (
                    f".agentic-ops/tasks/TAP-12289/runs/{result['agentic_run_id']}/"
                    "jira-plans/completion-comment.json"
                ),
                plan_contract["plan_file"],
            )
            self.assertTrue(plan_contract["plan_id_present"])
            readback = next(
                item
                for item in result["steps"]
                if item["step"] == "fixture_evidence_readback"
            )
            self.assertEqual("正在进行", readback["issue_status"])
            self.assertFalse(readback["transition_attempted"])

    def test_offline_contract_rejects_unknown_adapter_without_reading_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            manifest = self._offline_manifest(repository)
            manifest["adapter"] = "unsupported_network_adapter"
            manifest["agentic_ops"]["ref"] = "main"
            manifest["jira"]["allowed_writes"] = ["comment", "transition"]
            manifest["credential_channels"] = ["jira_api_token_stdin"]
            manifest["allowed_external_capabilities"] = [
                "local_filesystem",
                "subprocess",
                "network_git",
                "network_jira",
            ]
            path = root / "manifest.json"
            manifest["confirmation"]["confirmed_manifest_sha256"] = confirmation_digest(manifest)
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch("subprocess.run") as run:
                with self.assertRaises(RuntimeErrorResult) as captured:
                    IntegrationService(self.ROOT).run_offline("TAP-12289", str(path))
            self.assertEqual("integration_manifest_invalid", captured.exception.code)
            run.assert_not_called()

    def test_integration_runtime_does_not_import_developer_package(self) -> None:
        integration_root = (
            self.ROOT / "maintainer" / "runtime" / "src" / "ao_maint" / "integration"
        )
        for path in integration_root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""]
                else:
                    continue
                self.assertFalse(
                    any(name == "ao_work" or name.startswith("ao_work.") for name in imported),
                    path,
                )

    def test_offline_fixture_evidence_does_not_claim_real_takeover(self) -> None:
        source = (
            self.ROOT
            / "maintainer"
            / "runtime"
            / "src"
            / "ao_maint"
            / "integration"
            / "offline_fake.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("已接管任务", source)
        self.assertNotIn("任务接管、固定验证", source)
        self.assertIn("不代表正式接管或真实 Jira 完成", source)

    def test_offline_distribution_includes_shared_task_to_pr_protocols(self) -> None:
        source = (
            self.ROOT
            / "maintainer"
            / "runtime"
            / "src"
            / "ao_maint"
            / "integration"
            / "offline_fake.py"
        ).read_text(encoding="utf-8")
        self.assertIn('shared = source / "shared"', source)
        self.assertIn('distribution / "shared"', source)
        for name in (
            "task-to-pr-manifest.schema.json",
            "task-to-pr-event.schema.json",
            "task-to-pr-result.schema.json",
        ):
            self.assertIn(name, source)

    def test_python_unittest_recipe_cannot_escape_business_checkout(self) -> None:
        from ao_maint.integration.model import load_manifest
        from ao_maint.integration.offline_fake import OfflineFakeRunner

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            manifest = self._offline_manifest(repository)
            manifest["verification"]["commands"] = [
                {"recipe": "python_unittest", "args": ["discover", "-s", "/tmp"]}
            ]
            manifest["confirmation"]["confirmed_manifest_sha256"] = confirmation_digest(manifest)
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = load_manifest(str(path), "TAP-12289")
            runner = OfflineFakeRunner(loaded, root / "sandbox")
            runner.source_checkout.mkdir(parents=True)

            with self.assertRaises(RuntimeErrorResult) as captured:
                runner._verification_command(
                    "python_unittest", ("discover", "-s", "/tmp")
                )
            self.assertEqual(
                "integration_verification_invalid",
                captured.exception.code,
            )

    def _offline_manifest(self, task_repository: Path) -> dict[str, object]:
        return {
            "schema_version": 1,
            "issue_key": "TAP-12289",
            "adapter": "offline_fake",
            "agentic_ops": {"repository": str(self.ROOT), "ref": "WORKTREE"},
            "task_repository": {
                "repository": str(task_repository),
                "slug": "integration/task-fixture",
                "ref": "main",
            },
            "agent": {"agent_id": "integration_agent", "project_profile": "tapdata"},
            "jira": {
                "project_key": "TAP",
                "allowed_reads": ["identity", "project", "issue", "comment"],
                "allowed_writes": ["comment"],
            },
            "verification": {
                "commands": [
                    {"recipe": "executable", "args": ["verify-fixture.sh"]}
                ]
            },
            "cleanup": {"strategy": "always"},
            "credential_channels": [],
            "allowed_external_capabilities": [
                "local_filesystem",
                "local_git",
                "loopback_http",
                "subprocess",
            ],
            "confirmation": {
                "confirmed_by": "integration-test",
                "confirmed_at": "2026-08-13T00:00:00Z",
                "authorization_reference": "test:TAP-12289",
                "confirmed_manifest_sha256": "",
            },
        }

    def test_manifest_change_after_confirmation_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            manifest = self._offline_manifest(repository)
            manifest["confirmation"]["confirmed_manifest_sha256"] = confirmation_digest(manifest)
            manifest["agent"]["agent_id"] = "changed_after_confirmation"
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(RuntimeErrorResult) as captured:
                IntegrationService(self.ROOT).run_offline("TAP-12289", str(path))
            self.assertEqual(
                "integration_manifest_confirmation_mismatch", captured.exception.code
            )

    def test_manifest_source_and_profile_cannot_escape_current_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            for profile, source, expected_code in (
                ("../outside", self.ROOT, "integration_manifest_invalid"),
                ("tapdata", root / "other-agentic-ops", "integration_source_mismatch"),
            ):
                with self.subTest(profile=profile, source=source):
                    manifest = self._offline_manifest(repository)
                    manifest["agent"]["project_profile"] = profile
                    manifest["agentic_ops"]["repository"] = str(source)
                    manifest["confirmation"]["confirmed_manifest_sha256"] = confirmation_digest(manifest)
                    path = root / f"{profile.replace('/', '_')}.json"
                    path.write_text(json.dumps(manifest), encoding="utf-8")
                    with self.assertRaises(RuntimeErrorResult) as captured:
                        IntegrationService(self.ROOT).run_offline(
                            "TAP-12289", str(path)
                        )
                    self.assertEqual(expected_code, captured.exception.code)

    def _git(self, repository: Path, *arguments: str) -> None:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(repository), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == "__main__":
    unittest.main()
