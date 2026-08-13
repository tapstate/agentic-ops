from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agentic_ops.config import (
    JiraConnection,
    ProjectProfile,
    load_jira_connection,
    load_project_profile,
)
from agentic_ops.config.env import resolve_secret_pair_with_source, update_env_file
from agentic_ops.jira.client import JiraClient, UrllibJiraTransport
from agentic_ops.output import EXIT_BLOCKED, RuntimeErrorResult
from agentic_ops.task_state.io import atomic_write_json, atomic_write_text, read_json
from agentic_ops.task_state.locking import TaskLock

AGENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MANAGED_START = "<!-- agentic-ops:workspace:start -->"
MANAGED_END = "<!-- agentic-ops:workspace:end -->"


@dataclass(frozen=True)
class WorkspaceCandidate:
    root: Path
    install_root: Path
    agent_id: str
    profile: ProjectProfile
    connection: JiraConnection
    source_root: Path
    repository: str
    email: str | None
    token: str | None
    credential_source: str
    persist_credentials: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "workspace_root": str(self.root),
            "agent_id": self.agent_id,
            "project_profile": self.profile.profile_id,
            "jira_base_url": self.connection.base_url,
            "jira_project": self.profile.project_key,
            "jira_account": mask_email(self.email),
            "credential_source": self.credential_source,
            "repository": self.repository,
            "source_root": str(self.source_root),
        }


def normalize_agent_id(hostname: str | None = None) -> str:
    raw = (hostname if hostname is not None else socket.gethostname()).strip().lower()
    normalized = re.sub(r"[^0-9a-z_-]+", "-", raw).strip("-_")
    if not normalized:
        raise _blocked(
            "agent_id_default_invalid",
            "无法从当前主机名生成有效 agent_id",
            "请在初始化时明确输入只包含 [0-9A-Za-z_-] 的 agent_id",
        )
    return normalized


def validate_agent_id(agent_id: str) -> str:
    value = agent_id.strip()
    if not value or not AGENT_ID_PATTERN.fullmatch(value):
        raise _blocked(
            "agent_id_invalid",
            "agent_id 只能包含字符 [0-9A-Za-z_-]",
            "请修正 agent_id 后重新确认初始化摘要",
        )
    return value


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}{'*' * max(len(local) - len(visible), 1)}@{domain}"


class WorkspaceInitializer:
    def __init__(self, root: Path, install_root: Path, *, git_timeout: float = 20.0) -> None:
        self.root = root.expanduser().resolve()
        self.install_root = install_root.expanduser().resolve()
        self.git_timeout = git_timeout

    def prepare(
        self,
        profile_id: str,
        agent_id: str,
        *,
        source_root: str | None = None,
        credentials: tuple[str, str] | None = None,
        persist_credentials: bool = False,
    ) -> WorkspaceCandidate:
        if not self.root.is_dir():
            raise _blocked(
                "workspace_not_found",
                f"业务项目工作空间不存在：{self.root}",
                "请先创建工作空间目录，并使用 --workspace-root 指向该目录",
            )
        profile = load_project_profile(
            self.install_root,
            profile_id.strip(),
            workspace_root=self.root,
        )
        connection = load_jira_connection(
            self.install_root,
            profile.connection_id,
            workspace_root=self.root,
        )
        repository = profile.default_repository
        if not repository:
            raise _blocked(
                "workspace_repository_mapping_missing",
                f"Project Profile {profile.profile_id} 没有默认仓库映射",
                "请先在 standards/projects/<profile>/profile.yaml 配置 repositories.default",
            )
        if credentials is None:
            email, token, credential_source = resolve_secret_pair_with_source(
                connection.email_env,
                connection.token_env,
                self.root / ".agentic-ops" / ".env",
            )
        else:
            email, token = (credentials[0].strip(), credentials[1].strip())
            credential_source = "interactive_input" if persist_credentials else "standard_input"
        if email and not EMAIL_PATTERN.fullmatch(email):
            raise _blocked(
                "authorization_email_invalid",
                "Jira email 格式无效",
                "请修正 Jira email 后重新确认初始化摘要",
            )
        if token and len(token) < 8:
            raise _blocked(
                "authorization_token_invalid",
                "Jira token 长度明显不合理",
                "请重新输入当前 Jira 账户的 API token",
            )
        resolved_source = (
            Path(source_root).expanduser().resolve()
            if source_root
            else self.root / "repos" / profile.profile_id
        )
        return WorkspaceCandidate(
            root=self.root,
            install_root=self.install_root,
            agent_id=validate_agent_id(agent_id),
            profile=profile,
            connection=connection,
            source_root=resolved_source,
            repository=repository,
            email=email,
            token=token,
            credential_source=credential_source,
            persist_credentials=persist_credentials,
        )

    def preflight(
        self,
        candidate: WorkspaceCandidate,
        *,
        confirm_existing_config: bool = False,
        check_remote: bool = True,
    ) -> dict[str, Any]:
        checks: list[dict[str, str]] = []
        self._check_workspace_boundary(checks)
        self._check_workspace_writable(checks)
        validate_agent_id(candidate.agent_id)
        checks.append({"check": "agent_id_format", "status": "passed"})
        checks.append({"check": "project_profile", "status": "passed"})
        self._check_existing_config(candidate, checks, confirm_existing_config)
        self._check_agent_id_collision(candidate, checks)
        self._check_authorization(candidate, checks)
        jira_identity, jira_project = self._check_jira(candidate, checks)
        source_status = self._check_source(candidate, checks, check_remote=check_remote)
        return {
            "status": "passed",
            "checks": checks,
            "jira_identity": jira_identity,
            "jira_project_name": jira_project,
            "source_checkout_status": source_status,
        }

    def apply(
        self,
        candidate: WorkspaceCandidate,
        preflight: dict[str, Any],
    ) -> dict[str, Any]:
        state_root = candidate.root / ".agentic-ops"
        lock_path = state_root / ".workspace-init.lock"
        with TaskLock(lock_path, timeout=5):
            source_status = self._ensure_source_checkout(candidate)
            for directory in (
                state_root / "tasks",
                state_root / "runs",
                state_root / "audit",
                state_root / "feedback",
                state_root / "handoff",
                state_root / "profiles",
            ):
                directory.mkdir(parents=True, exist_ok=True)

            overlay_path = state_root / "profiles" / f"{candidate.profile.profile_id}.local.yaml"
            overlay = {
                "schema_version": 1,
                "workspace": {
                    "source_root": str(candidate.source_root),
                    "repository": candidate.repository,
                },
            }
            atomic_write_text(
                overlay_path,
                yaml.safe_dump(overlay, allow_unicode=True, sort_keys=False),
            )
            atomic_write_text(
                candidate.root / "AGENTS.md",
                self._managed_agents_content(candidate),
            )
            if candidate.persist_credentials and candidate.email and candidate.token:
                update_env_file(
                    state_root / ".env",
                    {
                        candidate.connection.email_env: candidate.email,
                        candidate.connection.token_env: candidate.token,
                    },
                )

            index_path = self._index_path()
            previous_index = index_path.read_bytes() if index_path.is_file() else None
            try:
                self._write_workspace_index(candidate)
                agent_path = state_root / "agent.json"
                atomic_write_json(
                    agent_path,
                    {
                        "schema_version": 1,
                        "mode": "project_execution",
                        "agent_id": candidate.agent_id,
                        "project_profile": candidate.profile.profile_id,
                        "jira_project": candidate.profile.project_key,
                        "source_root": str(candidate.source_root),
                        "repository": candidate.repository,
                        "initialized_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except BaseException:
                if previous_index is None:
                    index_path.unlink(missing_ok=True)
                else:
                    index_path.parent.mkdir(parents=True, exist_ok=True)
                    index_path.write_bytes(previous_index)
                raise

        return {
            **candidate.summary(),
            "jira_identity": preflight["jira_identity"],
            "jira_project_name": preflight["jira_project_name"],
            "source_checkout_status": source_status,
            "agent_config": str(state_root / "agent.json"),
            "profile_overlay": str(overlay_path),
            "agent_instructions": str(candidate.root / "AGENTS.md"),
            "preflight_status": "passed",
            "preflight_checks": preflight["checks"],
            "agentic_next_action": "list_assigned_jira_tasks",
        }

    def _check_workspace_boundary(self, checks: list[dict[str, str]]) -> None:
        if self.root == self.install_root or self.install_root in self.root.parents:
            raise _blocked(
                "workspace_boundary_invalid",
                "业务项目工作空间不能位于 AgenticOps 安装目录中",
                "请在独立目录创建业务项目工作空间",
            )
        if (self.root / "docs" / "strategy" / "project-goals.md").is_file():
            raise _blocked(
                "workspace_mode_mismatch",
                "AgenticOps 源头仓库不能初始化为业务项目工作空间",
                "请切换到独立业务项目工作空间",
            )
        checks.append({"check": "workspace_boundary", "status": "passed"})

    def _check_workspace_writable(self, checks: list[dict[str, str]]) -> None:
        if not os.access(self.root, os.W_OK | os.X_OK):
            raise _blocked(
                "workspace_not_writable",
                f"业务项目工作空间不可写：{self.root}",
                "请修复目录权限后重试",
            )
        checks.append({"check": "workspace_writable", "status": "passed"})

    def _check_existing_config(
        self,
        candidate: WorkspaceCandidate,
        checks: list[dict[str, str]],
        confirmed: bool,
    ) -> None:
        path = candidate.root / ".agentic-ops" / "agent.json"
        if not path.is_file():
            checks.append({"check": "existing_config", "status": "not_present"})
            return
        existing = read_json(path)
        same = all(
            existing.get(key) == value
            for key, value in {
                "mode": "project_execution",
                "agent_id": candidate.agent_id,
                "project_profile": candidate.profile.profile_id,
                "jira_project": candidate.profile.project_key,
                "source_root": str(candidate.source_root),
                "repository": candidate.repository,
            }.items()
        )
        if not same and not confirmed:
            raise _blocked(
                "existing_config_confirmation_required",
                "工作空间已有不同的 AgenticOps 配置",
                "请核对初始化摘要并明确确认覆盖已有配置",
            )
        checks.append(
            {"check": "existing_config", "status": "same" if same else "confirmed"}
        )

    def _check_agent_id_collision(
        self, candidate: WorkspaceCandidate, checks: list[dict[str, str]]
    ) -> None:
        for entry in self._workspace_entries():
            other_root = Path(str(entry.get("workspace_root", ""))).expanduser()
            if other_root == candidate.root:
                continue
            if not (other_root / ".agentic-ops" / "agent.json").is_file():
                continue
            if entry.get("agent_id") == candidate.agent_id:
                raise RuntimeErrorResult(
                    code="agent_id_conflict",
                    message=f"agent_id {candidate.agent_id} 已被另一业务项目工作空间使用",
                    status="blocked",
                    exit_code=EXIT_BLOCKED,
                    retry_safe=True,
                    required_human_action="请为当前研发员输入不同的 agent_id 后重新确认",
                    details={"conflicting_workspace": str(other_root)},
                )
        checks.append({"check": "agent_id_collision", "status": "passed"})

    def _check_authorization(
        self, candidate: WorkspaceCandidate, checks: list[dict[str, str]]
    ) -> None:
        missing = []
        if not candidate.email:
            missing.append(candidate.connection.email_env)
        if not candidate.token:
            missing.append(candidate.connection.token_env)
        if missing:
            raise RuntimeErrorResult(
                code="jira_credentials_missing",
                message=f"Jira 授权尚未配置完整：{', '.join(missing)}",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action="请在交互初始化中完成 Jira 授权，或为非交互模式提供完整凭证对",
            )
        checks.append({"check": "jira_credentials", "status": "passed"})

    def _check_jira(
        self, candidate: WorkspaceCandidate, checks: list[dict[str, str]]
    ) -> tuple[str, str]:
        assert candidate.email is not None and candidate.token is not None
        client = JiraClient(
            candidate.profile,
            UrllibJiraTransport(candidate.connection, candidate.email, candidate.token),
        )
        identity = client.current_user()
        if not identity:
            raise _blocked(
                "jira_identity_missing",
                "Jira 授权验证未返回当前账户身份",
                "请检查 Jira 账户和 API token 后重试",
            )
        checks.append({"check": "jira_identity", "status": "passed"})
        project = client.project_access(candidate.profile.project_key)
        checks.append({"check": "jira_project_access", "status": "passed"})
        return identity, project.get("name", "")

    def _check_source(
        self,
        candidate: WorkspaceCandidate,
        checks: list[dict[str, str]],
        *,
        check_remote: bool,
    ) -> str:
        if shutil.which("git") is None:
            raise _blocked(
                "git_missing",
                "未找到 Git 命令",
                "请安装 Git 后重试",
            )
        checks.append({"check": "git_available", "status": "passed"})
        source = candidate.source_root
        if source.exists() and not source.is_dir():
            raise _blocked(
                "source_root_invalid",
                f"源码路径不是目录：{source}",
                "请修改 source_root 后重新确认",
            )
        if source.is_dir() and any(source.iterdir()):
            if not (source / ".git").exists():
                raise _blocked(
                    "source_root_not_repository",
                    f"非空源码目录不是 Git 仓库：{source}",
                    "请使用空目录或指向目标 Git 仓库",
                )
            remote = self._run_git(["-C", str(source), "remote", "get-url", "origin"])
            if remote.returncode != 0 or not _repository_matches(
                remote.stdout.strip(), candidate.repository
            ):
                raise _blocked(
                    "source_repository_mismatch",
                    "本地源码仓库与 Project Profile 默认仓库不一致",
                    "请核对 source_root 和 repositories.default",
                )
            if check_remote:
                self._require_remote_access(remote.stdout.strip())
            checks.append({"check": "source_repository", "status": "passed"})
            return "reused"

        parent = source if source.is_dir() else source.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
            raise _blocked(
                "source_root_not_writable",
                f"无法创建源码目录：{source}",
                "请修复父目录权限或指定其它 source_root",
            )
        if check_remote:
            self._require_remote_access(_repository_url(candidate.repository))
        checks.append({"check": "source_repository", "status": "passed"})
        return "ready_to_clone"

    def _require_remote_access(self, remote: str) -> None:
        result = self._run_git(["ls-remote", remote, "HEAD"])
        if result.returncode != 0:
            raise _blocked(
                "source_repository_access_failed",
                "无法只读访问 Project Profile 配置的源码仓库",
                "请检查 GitHub 登录、SSH key、网络和仓库权限后重试",
            )

    def _ensure_source_checkout(self, candidate: WorkspaceCandidate) -> str:
        source = candidate.source_root
        if source.is_dir() and any(source.iterdir()):
            return "reused"
        source.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            source.rmdir()
        result = self._run_git(
            ["clone", _repository_url(candidate.repository), str(source)],
            timeout=max(self.git_timeout, 120.0),
        )
        if result.returncode != 0:
            raise _blocked(
                "source_checkout_failed",
                "源码仓库下载失败",
                "请检查 GitHub 权限和网络后重试；未写入初始化完成标记",
            )
        return "cloned"

    def _run_git(
        self, arguments: list[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout or self.git_timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise _blocked(
                "git_check_failed",
                f"Git 前置检查失败：{type(error).__name__}",
                "请检查 Git 安装、网络和仓库权限后重试",
            ) from error

    def _managed_agents_content(self, candidate: WorkspaceCandidate) -> str:
        path = candidate.root / "AGENTS.md"
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        block = (
            f"{MANAGED_START}\n"
            "# AgenticOps 业务项目工作空间\n\n"
            "本工作空间使用 `project_execution` 模式。自然语言交互和 Jira 人可见内容使用中文。\n\n"
            f"- agent_id：`{candidate.agent_id}`\n"
            f"- Project Profile：`{candidate.profile.profile_id}`\n"
            f"- Jira Project：`{candidate.profile.project_key}`\n"
            f"- 源码目录：`{candidate.source_root}`\n\n"
            "执行任务前先调用 `agentic-cli workspace preflight`；任何阻断结果都不得绕过。\n"
            f"{MANAGED_END}"
        )
        if MANAGED_START in existing and MANAGED_END in existing:
            before, rest = existing.split(MANAGED_START, 1)
            _, after = rest.split(MANAGED_END, 1)
            return f"{before.rstrip()}\n\n{block}{after}"
        if existing.strip():
            return f"{existing.rstrip()}\n\n{block}"
        return block

    def _index_path(self) -> Path:
        return self.install_root / "user" / "workspace-index.json"

    def _workspace_entries(self) -> list[dict[str, Any]]:
        path = self._index_path()
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise _blocked(
                "workspace_index_invalid",
                "本机工作空间索引无法读取",
                "请修复或移除损坏的 user/workspace-index.json 后重试",
            ) from error
        entries = payload.get("workspaces", []) if isinstance(payload, dict) else []
        return [entry for entry in entries if isinstance(entry, dict)]

    def _write_workspace_index(self, candidate: WorkspaceCandidate) -> None:
        entries = [
            entry
            for entry in self._workspace_entries()
            if Path(str(entry.get("workspace_root", ""))).expanduser() != candidate.root
            and (
                Path(str(entry.get("workspace_root", "")))
                / ".agentic-ops"
                / "agent.json"
            ).is_file()
        ]
        entries.append(
            {
                "workspace_root": str(candidate.root),
                "agent_id": candidate.agent_id,
                "project_profile": candidate.profile.profile_id,
            }
        )
        atomic_write_json(
            self._index_path(),
            {"schema_version": 1, "workspaces": entries},
        )


def _repository_url(repository: str) -> str:
    return f"git@github.com:{repository}.git"


def _repository_matches(remote: str, repository: str) -> bool:
    normalized = remote.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.endswith(f"github.com/{repository}") or normalized.endswith(
        f"github.com:{repository}"
    )


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )
