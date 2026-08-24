from __future__ import annotations

import errno
import ipaddress
import os
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ao_maint.jira.client import JiraClient
from ao_maint.output import RuntimeErrorResult

_PROXY_NAMES = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")
_SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5"}


@dataclass(frozen=True)
class _ProxyEndpoint:
    source: str
    scheme: str
    host: str
    port: int
    has_userinfo: bool


class NetworkDiagnoser:
    def __init__(
        self,
        *,
        jira_probe: Callable[[], object],
        github_probe: Callable[[], object] | None = None,
        connector: Callable[[tuple[str, int], float], object] = socket.create_connection,
        environment: Mapping[str, str] | None = None,
        targets: Mapping[str, str] | None = None,
    ) -> None:
        self._jira_probe = jira_probe
        self._github_probe = github_probe or _probe_github
        self._connector = connector
        self._environment = environment if environment is not None else os.environ
        self._targets = dict(targets or {"jira": "https://jira.invalid", "github": "https://api.github.com"})

    @classmethod
    def for_jira_client(cls, client: JiraClient) -> "NetworkDiagnoser":
        return cls(jira_probe=client.current_user_details, targets={"jira": client.connection.base_url, "github": "https://api.github.com"})

    def diagnose(self) -> dict[str, Any]:
        proxy, endpoint = _proxy_config(self._environment)
        targets = {name: _proxy_effective(proxy, self._environment, url) for name, url in self._targets.items()}
        proxy["targets"] = targets
        loopback = self._probe_loopback(proxy, endpoint, targets)
        shared_route_blocked = loopback["status"] == "failed" and any(targets.values())
        jira = {"status": "not_run", "reason": "shared_route_blocked"} if shared_route_blocked and targets.get("jira") else _run_probe(self._jira_probe, "jira")
        github = {"status": "not_run", "reason": "shared_route_blocked"} if shared_route_blocked and targets.get("github") else _run_probe(self._github_probe, "github")
        diagnosis = _diagnosis(self._environment, proxy, loopback, jira, github)
        return {
            "checks": {
                "proxy": proxy,
                "loopback": loopback,
                "jira": jira,
                "github": github,
            },
            "diagnosis": diagnosis,
            "agentic_next_action": _next_action(diagnosis["code"]),
        }

    def _probe_loopback(
        self, proxy: Mapping[str, Any], endpoint: _ProxyEndpoint | None, targets: Mapping[str, bool]
    ) -> dict[str, Any]:
        if endpoint is None or proxy["host_class"] != "loopback" or not any(targets.values()):
            return {"status": "skipped", "reason": "proxy_not_loopback"}
        try:
            self._connector((endpoint.host, endpoint.port), 3.0)
        except OSError as error:
            return {
                "status": "failed",
                "reason": _socket_reason(error),
                "errno": error.errno,
            }
        return {"status": "passed", "reason": "connected"}


def _proxy_config(environment: Mapping[str, str]) -> tuple[dict[str, Any], _ProxyEndpoint | None]:
    source = next((name for name in _PROXY_NAMES if environment.get(name)), "")
    raw = environment.get(source, "")
    if not source:
        return (
            {
                "configured": False,
                "configuration_invalid": False,
                "source": None,
                "host_class": None,
                "port": None,
                "scheme": None,
                "has_userinfo": False,
                "no_proxy_configured": bool(environment.get("NO_PROXY") or environment.get("no_proxy")),
            },
            None,
        )
    parsed = urllib.parse.urlsplit(raw)
    host = (parsed.hostname or "").casefold()
    try:
        port = parsed.port
    except ValueError:
        port = None
    scheme = parsed.scheme.casefold()
    configured = bool(host and port and scheme in _SUPPORTED_PROXY_SCHEMES)
    public = {
        "configured": configured,
        "configuration_invalid": not configured,
        "source": source,
        "scheme": scheme or None,
        "host_class": _host_class(host) if host else None,
        "port": port if configured else None,
        "has_userinfo": bool(parsed.username or parsed.password),
        "no_proxy_configured": bool(environment.get("NO_PROXY") or environment.get("no_proxy")),
    }
    if not configured:
        return public, None
    return public, _ProxyEndpoint(source=source, scheme=scheme, host=host, port=port, has_userinfo=public["has_userinfo"])


def _host_class(host: str) -> str:
    if host == "localhost":
        return "loopback"
    try:
        return "loopback" if ipaddress.ip_address(host).is_loopback else "remote"
    except ValueError:
        return "remote"


def _proxy_effective(proxy: Mapping[str, Any], environment: Mapping[str, str], target: str) -> bool:
    if not proxy["configured"]:
        return False
    host = (urllib.parse.urlsplit(target).hostname or "").casefold()
    no_proxy = environment.get("NO_PROXY") or environment.get("no_proxy") or ""
    return not any(_no_proxy_matches(host, item.strip()) for item in no_proxy.split(",") if item.strip())


def _no_proxy_matches(host: str, item: str) -> bool:
    candidate = item.casefold().split(":", 1)[0]
    return candidate == "*" or host == candidate or (candidate.startswith(".") and host.endswith(candidate))


def _run_probe(probe: Callable[[], object], system: str) -> dict[str, Any]:
    try:
        probe()
    except RuntimeErrorResult as error:
        return {"status": "failed", "reason": error.code}
    except urllib.error.HTTPError as error:
        return {"status": "failed", "reason": "http_error", "http_status": error.code}
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
        return {"status": "failed", "reason": _network_reason(error)}
    except Exception as error:  # probe implementations must not leak exception content
        return {"status": "failed", "reason": f"{system}_probe_failed", "exception_type": type(error).__name__}
    return {"status": "passed", "reason": "reachable"}


def _probe_github() -> None:
    subprocess.run(["gh", "api", "user", "--silent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5.0)


def _socket_reason(error: OSError) -> str:
    if error.errno in {errno.EPERM, errno.EACCES}:
        return "operation_not_permitted"
    if error.errno == errno.ECONNREFUSED:
        return "connection_refused"
    if error.errno == errno.ETIMEDOUT:
        return "timeout"
    return "socket_failed"


def _network_reason(error: BaseException) -> str:
    if isinstance(error, TimeoutError | socket.timeout):
        return "timeout"
    if isinstance(error, OSError):
        return _socket_reason(error)
    reason = getattr(error, "reason", None)
    if isinstance(reason, socket.gaierror):
        return "dns_failed"
    if isinstance(reason, OSError):
        return _socket_reason(reason)
    return "connection_failed"


def _diagnosis(
    environment: Mapping[str, str],
    proxy: dict[str, Any],
    loopback: dict[str, Any],
    jira: dict[str, Any],
    github: dict[str, Any],
) -> dict[str, str]:
    sandboxed = bool(environment.get("CODEX_SANDBOX") and environment.get("CODEX_SANDBOX_NETWORK_DISABLED"))
    effective_proxy = any(proxy.get("targets", {}).values())
    if proxy["configuration_invalid"]:
        return {"code": "network_proxy_configuration_invalid", "confidence": "high", "root_cause": "proxy_configuration", "evidence": "代理环境变量必须提供受支持协议、主机和显式端口"}
    if sandboxed and effective_proxy and proxy["host_class"] == "loopback" and loopback["reason"] == "operation_not_permitted":
        return {"code": "network_sandbox_loopback_blocked", "confidence": "high", "root_cause": "sandbox_loopback_policy", "evidence": "Codex 网络禁用标记与有效本机回环代理权限拒绝同时出现"}
    if proxy["configured"] and loopback["status"] == "failed":
        return {"code": "network_proxy_unreachable", "confidence": "high", "root_cause": "proxy_connectivity", "evidence": "本机代理 TCP 连通性检查失败"}
    if jira["status"] == "passed" and github["status"] == "passed":
        return {"code": "network_diagnosis_passed", "confidence": "high", "root_cause": "none", "evidence": "Jira 与 GitHub 只读连通性检查通过"}
    return {"code": "network_probe_failed", "confidence": "low", "root_cause": "undetermined", "evidence": "未满足沙箱或代理故障的严格判定条件"}


def _next_action(code: str) -> dict[str, Any]:
    if code == "network_proxy_configuration_invalid":
        return {"executor": "human", "action": "configure_proxy_environment", "requires_authorization": False, "reason": "请在代理环境变量中显式设置受支持协议、主机和端口后重试"}
    if code == "network_sandbox_loopback_blocked":
        return {"executor": "human", "action": "rerun_outside_sandbox", "requires_authorization": True, "reason": "请在获准的非沙箱执行环境使用相同代理变量重试原 Runtime 命令"}
    if code == "network_proxy_unreachable":
        return {"executor": "human", "action": "check_proxy", "requires_authorization": False, "reason": "请检查本机代理服务、地址和端口后重试"}
    return {"executor": "ai", "action": "continue_original_workflow", "requires_authorization": False, "reason": "根据各检查的独立失败分类处理原工作流"}
