from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ao_maint.cli import build_parser
from ao_maint.integration.cli import execute_integration
from ao_maint.integration.service import IntegrationService
from ao_maint.output import RuntimeErrorResult
from ao_maint.workspace import Workspace


class IntegrationPrepareInputTest(unittest.TestCase):
    def test_cli_prefills_only_explicit_test_identity_without_host_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "TAP-12289.manifest.json"
            host_home = root / "unrelated-host-home"
            host_home.mkdir()
            (host_home / ".env").write_text(
                "JIRA_API_TOKEN=must-not-leak\n", encoding="utf-8"
            )
            parser = build_parser()
            arguments = parser.parse_args(
                [
                    "integration",
                    "prepare-task-to-pr",
                    "TAP-12289",
                    "--output",
                    str(output),
                    "--agent-id",
                    "harsen-mini-test-bot",
                    "--confirmed-by",
                    "harsen",
                ]
            )
            workspace = Workspace(root=root, workplane="maintainer")
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "HOME": str(host_home),
                        "USER": "wrong-local-user",
                        "HOSTNAME": "wrong-local-host",
                        "JIRA_API_TOKEN": "must-not-leak-token",
                    },
                    clear=False,
                ),
                mock.patch(
                    "subprocess.run",
                    side_effect=AssertionError("prepare-task-to-pr 不得运行子进程"),
                ),
            ):
                result = execute_integration(arguments, workspace)

            payload = json.loads(output.read_text(encoding="utf-8"))
            serialized = output.read_text(encoding="utf-8")
            self.assertEqual("TAP-12289", payload["issue"]["key"])
            self.assertEqual("harsen-mini-test-bot", payload["agent"]["agent_id"])
            self.assertEqual("harsen", payload["authorization"]["confirmed_by"])
            self.assertEqual("REQUIRED", payload["agent"]["project_profile"])
            self.assertEqual("REQUIRED", payload["execution_identity"]["git_author_name"])
            self.assertFalse(result["host_state_read"])
            self.assertFalse(result["business_workspace_read"])
            self.assertFalse(result["credentials_read"])
            self.assertNotIn("required_inputs", result)
            self.assertEqual(
                [
                    "启动 $test-task-to-pr-e2e 并确认本次真实测试允许的外部副作用范围",
                    "运行时通过隐藏输入向隔离 developer 工作空间提供 Jira 授权",
                    "测试结束后审查真实 PR、结果包和完整摩擦复盘",
                ],
                result["required_user_actions"],
            )
            self.assertNotIn("wrong-local", serialized)
            self.assertNotIn("must-not-leak", serialized)
            self.assertNotIn(str(host_home), serialized)

    def test_optional_identity_defaults_remain_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "manifest.json"
            IntegrationService(root).prepare_task_to_pr(
                "TAP-12289", output=str(output)
            )
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("REQUIRED", payload["agent"]["agent_id"])
            self.assertEqual("REQUIRED", payload["authorization"]["confirmed_by"])

    def test_optional_identity_rejects_invalid_explicit_values(self) -> None:
        invalid = (
            ({"agent_id": "harsen.mini"}, "integration_agent_id_invalid"),
            ({"confirmed_by": "   "}, "integration_confirmed_by_invalid"),
        )
        for values, expected_code in invalid:
            with self.subTest(values=values), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with self.assertRaises(RuntimeErrorResult) as captured:
                    IntegrationService(root).prepare_task_to_pr(
                        "TAP-12289",
                        output=str(root / "manifest.json"),
                        **values,
                    )
                self.assertEqual(expected_code, captured.exception.code)


if __name__ == "__main__":
    unittest.main()
