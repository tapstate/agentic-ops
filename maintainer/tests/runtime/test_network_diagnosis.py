from __future__ import annotations

import errno
import unittest

from ao_maint.diagnose.network import NetworkDiagnoser
from ao_maint.output import RuntimeErrorResult


class NetworkDiagnosisTest(unittest.TestCase):
    def test_classifies_codex_sandbox_loopback_permission_denied(self) -> None:
        def denied(_: tuple[str, int], __: float) -> object:
            raise PermissionError(errno.EPERM, "Operation not permitted")

        result = NetworkDiagnoser(
            jira_probe=lambda: object(),
            github_probe=lambda: object(),
            connector=denied,
            environment={
                "HTTPS_PROXY": "http://127.0.0.1:7890",
                "CODEX_SANDBOX": "seatbelt",
                "CODEX_SANDBOX_NETWORK_DISABLED": "1",
            },
        ).diagnose()

        self.assertEqual("network_sandbox_loopback_blocked", result["diagnosis"]["code"])
        self.assertEqual("high", result["diagnosis"]["confidence"])
        self.assertEqual("rerun_outside_sandbox", result["agentic_next_action"]["action"])
        self.assertNotIn("http://127.0.0.1:7890", str(result))

    def test_no_proxy_bypass_does_not_claim_loopback_sandbox_block(self) -> None:
        def denied(_: tuple[str, int], __: float) -> object:
            raise PermissionError(errno.EPERM, "Operation not permitted")

        result = NetworkDiagnoser(
            jira_probe=lambda: object(), github_probe=lambda: object(), connector=denied,
            environment={"HTTPS_PROXY": "http://127.0.0.1:7890", "NO_PROXY": "jira.example", "CODEX_SANDBOX": "seatbelt", "CODEX_SANDBOX_NETWORK_DISABLED": "1"},
            targets={"jira": "https://jira.example", "github": "https://github.example"},
        ).diagnose()

        self.assertFalse(result["checks"]["proxy"]["targets"]["jira"])
        self.assertEqual("network_sandbox_loopback_blocked", result["diagnosis"]["code"])

    def test_keeps_unsandboxed_proxy_failure_distinct(self) -> None:
        def refused(_: tuple[str, int], __: float) -> object:
            raise ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused")

        result = NetworkDiagnoser(
            jira_probe=lambda: object(),
            github_probe=lambda: object(),
            connector=refused,
            environment={"HTTP_PROXY": "http://localhost:7890"},
        ).diagnose()

        self.assertEqual("network_proxy_unreachable", result["diagnosis"]["code"])
        self.assertEqual("connection_refused", result["checks"]["loopback"]["reason"])

    def test_preserves_jira_authorization_failure(self) -> None:
        result = NetworkDiagnoser(
            jira_probe=lambda: (_ for _ in ()).throw(RuntimeErrorResult(code="jira_authorization_failed", message="", status="blocked")),
            github_probe=lambda: object(),
            environment={},
        ).diagnose()

        self.assertEqual("jira_authorization_failed", result["checks"]["jira"]["reason"])
        self.assertEqual("network_probe_failed", result["diagnosis"]["code"])

    def test_proxy_credentials_are_not_exposed(self) -> None:
        result = NetworkDiagnoser(
            jira_probe=lambda: object(),
            github_probe=lambda: object(),
            environment={"HTTPS_PROXY": "http://secret:token@proxy.example:7890"},
        ).diagnose()

        self.assertEqual("proxy.example", result["checks"]["proxy"]["host"])
        self.assertNotIn("secret", str(result))
        self.assertNotIn("token", str(result))


if __name__ == "__main__":
    unittest.main()
