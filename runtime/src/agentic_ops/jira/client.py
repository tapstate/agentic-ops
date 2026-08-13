from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from agentic_ops.config.model import JiraConnection, ProjectProfile
from agentic_ops.jira.adf import markdown_to_adf
from agentic_ops.jira.model import (
    JiraComment,
    JiraIssue,
    JiraWorklog,
    object_name,
    plain_text,
    user_identifier,
)
from agentic_ops.output import EXIT_BLOCKED, RuntimeErrorResult


@dataclass(frozen=True)
class TransportResponse:
    status: int
    payload: Any


class JiraTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> TransportResponse: ...


class JiraTransportError(Exception):
    def __init__(self, message: str, *, response_received: bool = False) -> None:
        super().__init__(message)
        self.response_received = response_received


class UrllibJiraTransport:
    def __init__(self, connection: JiraConnection, email: str, token: str) -> None:
        self.base_url = connection.base_url.rstrip("/")
        self.timeout = connection.timeout_seconds
        encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {encoded}",
            "User-Agent": "AgenticOps-Python-Runtime/0.1",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> TransportResponse:
        encoded_query = urllib.parse.urlencode(query or {})
        url = f"{self.base_url}{path}"
        if encoded_query:
            url = f"{url}?{encoded_query}"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                payload = json.loads(raw) if raw else None
                return TransportResponse(status=response.status, payload=payload)
        except urllib.error.HTTPError as error:
            raw = error.read()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = None
            return TransportResponse(status=error.code, payload=payload)
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as error:
            raise JiraTransportError(type(error).__name__, response_received=False) from error


class JiraClient:
    def __init__(self, profile: ProjectProfile, transport: JiraTransport) -> None:
        self.profile = profile
        self.transport = transport

    def current_user(self) -> str:
        payload = self._request("GET", "/rest/api/3/myself")
        return user_identifier(payload)

    def field_metadata(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/rest/api/3/field")
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def project_access(self, project_key: str) -> dict[str, str]:
        payload = self._request(
            "GET",
            f"/rest/api/3/project/{urllib.parse.quote(project_key, safe='')}",
        )
        if not isinstance(payload, dict):
            raise RuntimeErrorResult(
                code="jira_project_invalid",
                message="Jira Project 返回了无法识别的响应",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请核对 Jira Project Key 和账户权限",
            )
        actual_key = str(payload.get("key", "")).strip()
        if actual_key != project_key:
            raise RuntimeErrorResult(
                code="jira_workspace_mismatch",
                message=f"Jira 返回的 Project Key {actual_key or '<empty>'} 与 {project_key} 不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请核对 Project Profile 与 Jira 站点绑定",
            )
        return {"key": actual_key, "name": str(payload.get("name", "")).strip()}

    def get_issue(self, issue_key: str) -> JiraIssue:
        fields = ",".join(self.profile.requested_jira_fields())
        payload = self._request(
            "GET",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}",
            query={"fields": fields},
        )
        raw_fields = payload.get("fields", {}) if isinstance(payload, dict) else {}
        project = raw_fields.get("project", {})
        project_key = project.get("key", "") if isinstance(project, dict) else issue_key.partition("-")[0]
        return JiraIssue(
            issue_id=str(payload.get("id", "")),
            key=str(payload.get("key", issue_key)),
            project_key=str(project_key),
            summary=str(raw_fields.get("summary", "")),
            status=object_name(raw_fields.get("status")),
            issue_type=object_name(raw_fields.get("issuetype")),
            assignee=user_identifier(raw_fields.get("assignee")),
            description=raw_fields.get("description") if isinstance(raw_fields.get("description"), dict) else None,
            fields=raw_fields,
        )

    def comments(self, issue_key: str) -> list[JiraComment]:
        payload = self._request(
            "GET",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/comment",
            query={"maxResults": "100", "orderBy": "created"},
        )
        items = payload.get("comments", []) if isinstance(payload, dict) else []
        return [
            JiraComment(
                comment_id=str(item.get("id", "")),
                body=plain_text(item.get("body")),
                author=user_identifier(item.get("author")),
                created=str(item.get("created", "")),
            )
            for item in items
            if isinstance(item, dict)
        ]

    def add_comment(self, issue_key: str, markdown: str) -> str:
        payload = self._request(
            "POST",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/comment",
            body={"body": markdown_to_adf(markdown)},
        )
        return str(payload.get("id", "")) if isinstance(payload, dict) else ""

    def update_description(self, issue_key: str, description: dict[str, Any]) -> None:
        self._request(
            "PUT",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}",
            body={"fields": {"description": description}},
        )

    def worklogs(self, issue_key: str) -> list[JiraWorklog]:
        payload = self._request(
            "GET",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/worklog",
            query={"maxResults": "100"},
        )
        items = payload.get("worklogs", []) if isinstance(payload, dict) else []
        return [
            JiraWorklog(
                worklog_id=str(item.get("id", "")),
                body=plain_text(item.get("comment")),
                time_spent_seconds=int(item.get("timeSpentSeconds", 0)),
                started=str(item.get("started", "")),
            )
            for item in items
            if isinstance(item, dict)
        ]

    def add_worklog(
        self,
        issue_key: str,
        *,
        time_spent_seconds: int,
        started: str,
        markdown: str,
    ) -> str:
        payload = self._request(
            "POST",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/worklog",
            body={
                "timeSpentSeconds": time_spent_seconds,
                "started": started,
                "comment": markdown_to_adf(markdown),
            },
        )
        return str(payload.get("id", "")) if isinstance(payload, dict) else ""

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = self.transport.request(method, path, query=query, body=body)
        except JiraTransportError as error:
            if method != "GET":
                raise
            raise RuntimeErrorResult(
                code="jira_connection_failed",
                message="无法连接 Jira 或请求超时",
                retry_safe=True,
                required_human_action="请检查网络、Connection base_url 和 Jira 服务状态后重试",
            ) from error
        if 200 <= response.status < 300:
            return response.payload
        if response.status == 404:
            raise RuntimeErrorResult(
                code="jira_issue_not_found",
                message="Jira 任务或资源不存在",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请核对 Jira 站点、项目和 Issue Key",
            )
        if response.status in {401, 403}:
            raise RuntimeErrorResult(
                code="jira_authorization_failed",
                message=f"Jira 拒绝访问（HTTP {response.status}）",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请检查凭证、token scope 和 Jira 项目权限",
            )
        raise RuntimeErrorResult(
            code="jira_request_failed",
            message=f"Jira 请求失败（HTTP {response.status}）",
            retry_safe=method == "GET",
            required_human_action="请检查 Jira 服务状态；写入请求不要直接重试，先回读",
        )
