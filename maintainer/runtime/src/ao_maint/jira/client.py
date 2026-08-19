from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from ao_maint.jira.adf import markdown_to_adf
from ao_maint.jira.model import (
    JiraComment,
    JiraIssue,
    JiraWorklog,
    object_name,
    plain_text,
    standalone_paragraph_lines,
    user_identifier,
)
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult


@dataclass(frozen=True)
class JiraConnection:
    connection_id: str
    base_url: str
    email_env: str
    token_env: str
    timeout_seconds: float = 20.0


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


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


class UrllibJiraTransport:
    def __init__(self, connection: JiraConnection, email: str, token: str) -> None:
        self.base_url = connection.base_url.rstrip("/")
        self.timeout = connection.timeout_seconds
        encoded = base64.b64encode(f"{email}:{token}".encode()).decode()
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {encoded}",
            "User-Agent": "AgenticOps-Maintainer-Runtime/0.1",
        }
        self.opener = urllib.request.build_opener(_RejectRedirects())

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
            with self.opener.open(request, timeout=self.timeout) as response:
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
    def __init__(self, connection: JiraConnection, transport: JiraTransport) -> None:
        self.connection = connection
        self.transport = transport

    def current_user(self) -> str:
        return self.current_user_details()["account_id"]

    def current_user_details(self) -> dict[str, str]:
        payload = self._request("GET", "/rest/api/3/myself")
        if not isinstance(payload, dict):
            raise RuntimeErrorResult(
                code="jira_identity_missing",
                message="Jira 当前账户返回了无法识别的响应",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请重新验证当前维护工作区 Jira 授权",
            )
        account_id = str(payload.get("accountId", "")).strip()
        if not account_id:
            raise RuntimeErrorResult(
                code="jira_identity_missing",
                message="Jira 当前账户响应缺少 accountId",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请重新验证当前维护工作区 Jira 授权",
            )
        return {
            "account_id": account_id,
            "display_name": str(payload.get("displayName", "")).strip(),
        }

    def field_metadata(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/rest/api/3/field")
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def get_issue(self, issue_key: str) -> JiraIssue:
        fields = ",".join(
            sorted(
                {
                    "summary",
                    "description",
                    "status",
                    "issuetype",
                    "assignee",
                    "project",
                }
            )
        )
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

    def create_issue(self, fields: dict[str, Any]) -> dict[str, str]:
        """创建 Jira 任务，返回 {id, key}；失败抛异常（调用方负责回读验证）。"""
        payload = self._request(
            "POST",
            "/rest/api/3/issue",
            body={"fields": fields},
        )
        if not isinstance(payload, dict):
            raise RuntimeErrorResult(
                code="jira_create_response_invalid",
                message="Jira 创建任务返回了无法识别的响应",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请回读 Jira 确认任务是否已创建，不要重复写入",
            )
        issue_id = str(payload.get("id", "")).strip()
        key = str(payload.get("key", "")).strip()
        if not issue_id or not key:
            raise RuntimeErrorResult(
                code="jira_create_response_invalid",
                message="Jira 创建任务响应缺少 id 或 key",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请回读 Jira 确认任务是否已创建，不要重复写入",
            )
        return {"id": issue_id, "key": key}

    def update_description(self, issue_key: str, description: dict[str, Any]) -> None:
        """整体替换 Jira 任务 Description（ADF）；调用方负责回读验证。"""
        self._request(
            "PUT",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}",
            body={"fields": {"description": description}},
        )

    def search_issues(
        self, jql: str, fields: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """JQL 搜索任务，返回原始 issue 对象列表（含 key/fields）。"""
        payload = self._request(
            "GET",
            "/rest/api/3/search/jql",
            query={
                "jql": jql,
                "fields": ",".join(fields or ["summary", "description"]),
                "maxResults": "50",
            },
        )
        raw = payload.get("issues", []) if isinstance(payload, dict) else []
        return [item for item in raw if isinstance(item, dict)]

    def create_meta(
        self, project_key: str, issuetype_name: str
    ) -> dict[str, Any]:
        """查询项目+事务类型的 createmeta，返回该类型的必填字段与 schema。

        返回形如 {"issuetype_id": ..., "issuetype_name": ..., "required": {field_id: name}, "fields": {field_id: schema}}。
        """
        payload = self._request(
            "GET",
            "/rest/api/3/issue/createmeta",
            query={
                "projectKeys": project_key,
                "expand": "projects.issuetypes.fields",
            },
        )
        projects = payload.get("projects", []) if isinstance(payload, dict) else []
        for project in projects:
            if not isinstance(project, dict):
                continue
            if str(project.get("key", "")) != project_key:
                continue
            for issuetype in project.get("issuetypes", []):
                if not isinstance(issuetype, dict):
                    continue
                if str(issuetype.get("name", "")) != issuetype_name:
                    continue
                fields_raw = issuetype.get("fields", {})
                if not isinstance(fields_raw, dict):
                    fields_raw = {}
                required: dict[str, str] = {}
                schemas: dict[str, dict[str, Any]] = {}
                for field_id, spec in fields_raw.items():
                    if not isinstance(spec, dict):
                        continue
                    schemas[field_id] = spec
                    if spec.get("required"):
                        required[field_id] = str(spec.get("name", field_id))
                return {
                    "issuetype_id": str(issuetype.get("id", "")),
                    "issuetype_name": str(issuetype.get("name", "")),
                    "required": required,
                    "fields": schemas,
                }
        raise RuntimeErrorResult(
            code="jira_create_meta_missing",
            message=(
                f"Jira 项目 {project_key} 下未找到事务类型「{issuetype_name}」，"
                "或 createmeta 查询结果不含该项目"
            ),
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action=(
                f"请核对项目 Key 与事务类型名称，可用 ao-maint jira create plan 前先确认"
            ),
        )

    def comments(self, issue_key: str) -> list[JiraComment]:
        items = self._paginated(
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/comment",
            item_field="comments",
            extra_query={"orderBy": "created"},
        )
        return [
            JiraComment(
                comment_id=str(item.get("id", "")),
                body=plain_text(item.get("body")),
                author=user_identifier(item.get("author")),
                created=str(item.get("created", "")),
                standalone_lines=standalone_paragraph_lines(item.get("body")),
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

    def worklogs(self, issue_key: str) -> list[JiraWorklog]:
        items = self._paginated(
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/worklog",
            item_field="worklogs",
        )
        return [
            JiraWorklog(
                worklog_id=str(item.get("id", "")),
                body=plain_text(item.get("comment")),
                time_spent_seconds=int(item.get("timeSpentSeconds", 0)),
                started=str(item.get("started", "")),
                standalone_lines=standalone_paragraph_lines(item.get("comment")),
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

    def available_transitions(self, issue_key: str) -> list[dict[str, str]]:
        payload = self._request(
            "GET",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/transitions",
        )
        raw = payload.get("transitions", []) if isinstance(payload, dict) else []
        result: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            transition_id = str(item.get("id", "")).strip()
            name = str(item.get("name", "")).strip()
            to_status = object_name(item.get("to"))
            if transition_id and name:
                result.append({"id": transition_id, "name": name, "to": to_status})
        return result

    def execute_transition(
        self, issue_key: str, transition_id: str, markdown: str | None = None
    ) -> None:
        body: dict[str, Any] = {"transition": {"id": transition_id}}
        if markdown:
            body["update"] = {"comment": [{"add": {"body": markdown_to_adf(markdown)}}]}
        self._request(
            "POST",
            f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}/transitions",
            body=body,
        )

    def _paginated(
        self,
        path: str,
        *,
        item_field: str,
        extra_query: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        start_at = 0
        page_size = 100
        collected: list[dict[str, Any]] = []
        for _ in range(1000):
            query = {
                "startAt": str(start_at),
                "maxResults": str(page_size),
                **(extra_query or {}),
            }
            payload = self._request("GET", path, query=query)
            if not isinstance(payload, dict):
                raise self._pagination_invalid(item_field)
            raw_items = payload.get(item_field, [])
            if not isinstance(raw_items, list):
                raise self._pagination_invalid(item_field)
            items = [item for item in raw_items if isinstance(item, dict)]
            collected.extend(items)
            total_raw = payload.get("total")
            total = total_raw if isinstance(total_raw, int) and total_raw >= 0 else None
            if total is not None and len(collected) >= total:
                return collected
            if not raw_items:
                return collected
            next_start = start_at + len(raw_items)
            if next_start <= start_at:
                raise self._pagination_invalid(item_field)
            if total is None and len(raw_items) < page_size:
                return collected
            start_at = next_start
        raise self._pagination_invalid(item_field)

    @staticmethod
    def _pagination_invalid(item_field: str) -> RuntimeErrorResult:
        return RuntimeErrorResult(
            code="jira_pagination_invalid",
            message=f"Jira {item_field} 分页响应无进展或格式无效",
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请停止写入并核对 Jira 分页响应，避免遗漏既有幂等记录",
        )

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
