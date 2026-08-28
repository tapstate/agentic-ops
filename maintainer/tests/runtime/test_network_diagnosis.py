from __future__ import annotations

import errno
import unittest

from ao_maint.diagnose.network import NetworkDiagnoser
from ao_maint.output import RuntimeErrorResult


class NetworkDiagnosisTest(unittest.TestCase):
    @staticmethod
    def _proxy_url(host: str, port: int, *, userinfo: str = "") -> str:
        authority = f"{userinfo}@{host}" if userinfo else host
        return f"http://{authority}:{port}"

    def test_classifies_codex_sandbox_loopback_permission_denied(self) -> None:
        def denied(_: tuple[str, int], __: float) -> object:
            raise PermissionError(errno.EPERM, "Operation not permitted")

        result = NetworkDiagnoser(
            jira_probe=lambda: object(),
            github_probe=lambda: object(),
            connector=denied,
            environment={
                "HTTPS_PROXY": self._proxy_url("127.0.0.2", 18443),
                "CODEX_SANDBOX": "seatbelt",
                "CODEX_SANDBOX_NETWORK_DISABLED": "1",
            },
        ).diagnose()

        self.assertEqual("network_sandbox_loopback_blocked", result["diagnosis"]["code"])
        self.assertEqual("high", result["diagnosis"]["confidence"])
        self.assertEqual("rerun_outside_sandbox", result["next_step"]["action"])
        self.assertNotIn("127.0.0.2", str(result))
        self.assertEqual(18443, result["checks"]["proxy"]["port"])

    def test_no_proxy_bypass_does_not_claim_loopback_sandbox_block(self) -> None:
        def denied(_: tuple[str, int], __: float) -> object:
            raise PermissionError(errno.EPERM, "Operation not permitted")

        result = NetworkDiagnoser(
            jira_probe=lambda: object(), github_probe=lambda: object(), connector=denied,
            environment={"HTTPS_PROXY": self._proxy_url("127.0.0.3", 18444), "NO_PROXY": "jira.example", "CODEX_SANDBOX": "seatbelt", "CODEX_SANDBOX_NETWORK_DISABLED": "1"},
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
            environment={"HTTP_PROXY": self._proxy_url("localhost", 18445)},
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
            environment={"HTTPS_PROXY": self._proxy_url("proxy.example", 18447, userinfo="secret:token")},
        ).diagnose()

        self.assertNotIn("host", result["checks"]["proxy"])
        self.assertNotIn("proxy.example", str(result))
        self.assertNotIn("secret", str(result))
        self.assertNotIn("token", str(result))

    def test_uses_the_explicit_environment_endpoint_without_a_default_port(self) -> None:
        captured: list[tuple[str, int]] = []

        def denied(endpoint: tuple[str, int], _: float) -> object:
            captured.append(endpoint)
            raise PermissionError(errno.EPERM, "Operation not permitted")

        result = NetworkDiagnoser(
            jira_probe=lambda: object(),
            github_probe=lambda: object(),
            connector=denied,
            environment={"HTTPS_PROXY": self._proxy_url("127.0.0.4", 18446), "CODEX_SANDBOX": "seatbelt", "CODEX_SANDBOX_NETWORK_DISABLED": "1"},
        ).diagnose()

        self.assertEqual([("127.0.0.4", 18446)], captured)
        self.assertEqual("network_sandbox_loopback_blocked", result["diagnosis"]["code"])

    def test_requires_an_explicit_proxy_port(self) -> None:
        connector_called = False

        def connector(_: tuple[str, int], __: float) -> object:
            nonlocal connector_called
            connector_called = True
            return object()

        result = NetworkDiagnoser(
            jira_probe=lambda: object(), github_probe=lambda: object(), connector=connector,
            environment={"HTTPS_PROXY": "http://localhost"},
        ).diagnose()

        self.assertFalse(connector_called)
        self.assertEqual("network_proxy_configuration_invalid", result["diagnosis"]["code"])

    def test_supports_lowercase_proxy_environment_variables(self) -> None:
        def denied(_: tuple[str, int], __: float) -> object:
            raise PermissionError(errno.EPERM, "Operation not permitted")

        result = NetworkDiagnoser(
            jira_probe=lambda: object(),
            github_probe=lambda: object(),
            connector=denied,
            environment={"https_proxy": self._proxy_url("127.0.0.5", 18448), "CODEX_SANDBOX": "seatbelt", "CODEX_SANDBOX_NETWORK_DISABLED": "1"},
        ).diagnose()

        self.assertEqual("https_proxy", result["checks"]["proxy"]["source"])
        self.assertEqual("network_sandbox_loopback_blocked", result["diagnosis"]["code"])


if __name__ == "__main__":
    unittest.main()
