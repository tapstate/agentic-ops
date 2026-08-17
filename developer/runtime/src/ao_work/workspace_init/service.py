from __future__ import annotations

import json
import hashlib
import os
import re
import select
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ao_work.config import (
    JiraConnection,
    ProjectProfile,
    load_jira_connection,
    load_project_profile,
    jira_site_identity,
    validate_workspace_jira_binding,
    validate_workspace_project_binding,
)
from ao_work.config.env import resolve_secret_pair_with_source, update_env_file
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.git_security import github_repository_url_matches
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult, write_diagnostic
from ao_work.task_state.io import atomic_write_json, atomic_write_text, read_json
from ao_work.task_state.locking import TaskLock
from ao_work.workspace import Workspace, _source_ancestor, validate_business_source_root
from ao_work.workspace_security import (
    protect_workspace_env_from_git,
    read_workspace_root_file,
    validate_managed_path,
    validate_workspace_managed_path,
    validate_workspace_root_file,
    validate_workspace_state_root,
)

AGENT_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
MANAGED_START = "<!-- agentic-ops:workspace:start -->"
MANAGED_END = "<!-- agentic-ops:workspace:end -->"
MANAGED_CODE_START = "<!-- agentic-ops:workspace-code:start -->"
MANAGED_CODE_END = "<!-- agentic-ops:workspace-code:end -->"
WORKSPACE_SKILLS_ROOT = Path(".agents") / "skills"


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
    execution_identity: dict[str, str] | None = None
    persist_credentials: bool = False
    source_root_derived: bool = False

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
            "execution_identity": self.execution_identity,
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


def build_execution_identity(
    git_name: str,
    git_email: str,
    github_actor_login: str,
) -> dict[str, str]:
    name = git_name.strip()
    email = git_email.strip()
    login = github_actor_login.strip()
    if not name or "\x00" in name or len(name) > 256:
        raise _blocked(
            "execution_identity_invalid",
            "Git author/committer name 无效",
            "请在工作空间初始化时确认当前研发员的 Git 姓名",
        )
    if not EMAIL_PATTERN.fullmatch(email):
        raise _blocked(
            "execution_identity_invalid",
            "Git author/committer email 格式无效",
            "请在工作空间初始化时确认当前研发员的 Git email",
        )
    if not GITHUB_LOGIN_PATTERN.fullmatch(login):
        raise _blocked(
            "execution_identity_invalid",
            "GitHub actor login 格式无效",
            "请在工作空间初始化时确认当前研发员的 GitHub login",
        )
    return {
        "git_author_name": name,
        "git_author_email": email,
        "git_committer_name": name,
        "git_committer_email": email,
        "github_actor_login": login,
    }


class WorkspaceInitializer:
    def __init__(self, root: Path, install_root: Path, *, git_timeout: float = 20.0) -> None:
        self.root = root.expanduser().resolve()
        self.install_root = install_root.expanduser().resolve()
        self.git_timeout = git_timeout
        validate_workspace_state_root(self.root)

    def prepare(
        self,
        profile_id: str,
        agent_id: str,
        *,
        source_root: str | None = None,
        credentials: tuple[str, str] | None = None,
        execution_identity: dict[str, str] | None = None,
        persist_credentials: bool = False,
        allow_rebind: bool = False,
    ) -> WorkspaceCandidate:
        validate_workspace_state_root(self.root)
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
                "请先在 developer/standards/projects/<profile>/profile.yaml 配置 repositories.default",
            )
        if credentials is None:
            agent_path = self.root / ".agentic-ops" / "agent.json"
            if agent_path.is_file():
                existing = read_json(agent_path)
                if existing.get("schema_version") == 3:
                    try:
                        validate_workspace_jira_binding(
                            Workspace(self.root, "developer", agent_path),
                            connection,
                        )
                        validate_workspace_project_binding(
                            Workspace(self.root, "developer", agent_path),
                            profile,
                        )
                    except RuntimeErrorResult as error:
                        if error.code not in {
                            "jira_workspace_identity_drift",
                            "workspace_project_identity_drift",
                        } or not allow_rebind:
                            raise
                        email, token, credential_source = None, None, "rebind_required"
                    else:
                        email, token, credential_source = resolve_secret_pair_with_source(
                            connection.email_env,
                            connection.token_env,
                            self.root / ".agentic-ops" / ".env",
                        )
                else:
                    email, token, credential_source = None, None, "upgrade_required"
            else:
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
        if execution_identity is None:
            agent_path = self.root / ".agentic-ops" / "agent.json"
            if agent_path.is_file():
                existing_agent = read_json(agent_path)
                existing_identity = existing_agent.get("execution_identity")
                if isinstance(existing_identity, dict):
                    rebuilt_identity = build_execution_identity(
                        str(existing_identity.get("git_author_name", "")),
                        str(existing_identity.get("git_author_email", "")),
                        str(existing_identity.get("github_actor_login", "")),
                    )
                    if existing_identity != rebuilt_identity:
                        raise _blocked(
                            "execution_identity_invalid",
                            "工作空间中已保存的执行身份字段不完整或不一致",
                            "请重新运行交互初始化并确认 Git/GitHub 执行身份",
                        )
                    execution_identity = rebuilt_identity
        source_root_derived = not source_root
        resolved_source = (
            Path(source_root).expanduser().resolve()
            if source_root
            else self.root.parent / f"{self.root.name}-code" / _repository_short_name(repository)
        )
        resolved_source = validate_business_source_root(self.root, resolved_source)
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
            execution_identity=execution_identity,
            persist_credentials=persist_credentials,
            source_root_derived=source_root_derived,
        )

    def preflight(
        self,
        candidate: WorkspaceCandidate,
        *,
        confirm_existing_config: bool = False,
        check_remote: bool = True,
    ) -> dict[str, Any]:
        validate_workspace_state_root(self.root)
        checks: list[dict[str, str]] = []
        self._check_workspace_boundary(checks)
        self._check_workspace_writable(checks)
        self._check_install_ai_assets(checks)
        validate_agent_id(candidate.agent_id)
        checks.append({"check": "agent_id_format", "status": "passed"})
        checks.append({"check": "project_profile", "status": "passed"})
        self._check_existing_config(candidate, checks, confirm_existing_config)
        if (candidate.root / ".agentic-ops" / "agent.json").is_file():
            self._check_workspace_ai_assets(candidate, checks)
        self._check_agent_id_collision(candidate, checks)
        self._check_authorization(candidate, checks)
        jira_identity, jira_project = self._check_jira(candidate, checks)
        self._check_existing_jira_account(
            candidate,
            jira_identity,
            confirmed=confirm_existing_config,
        )
        source_status = self._check_source(candidate, checks, check_remote=check_remote)
        self._check_source_root_conflict(candidate, checks)
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
        state_root = validate_workspace_state_root(candidate.root)
        for path in (
            state_root / "agent.json",
            state_root / "profiles",
            state_root / "connections",
            state_root / "runs",
            state_root / "audit",
            state_root / "feedback",
            state_root / "handoff",
            state_root / "locks",
        ):
            validate_workspace_managed_path(candidate.root, path)
        state_root_existed = state_root.exists()
        lock_path = state_root / ".workspace-init.lock"
        try:
            with TaskLock(lock_path, timeout=5):
                write_diagnostic("初始化步骤 1/5：校验并保护工作空间状态（防止凭证被 Git 跟踪）")
                credential_protection = self._protect_workspace_state_from_git()
                write_diagnostic(
                    f"初始化步骤 2/5：下载业务源码仓库 {candidate.repository} → {candidate.source_root}"
                )
                source_status = self._ensure_source_checkout(candidate)
                write_diagnostic(f"源码仓库下载完成（{source_status}）")
                if candidate.source_root_derived:
                    self._write_source_container_readme(candidate)
                write_diagnostic("初始化步骤 3/5：写入工作空间配置与 AGENTS.md")
                for directory in (
                    state_root / "tasks",
                    state_root / "runs",
                    state_root / "audit",
                    state_root / "feedback",
                    state_root / "handoff",
                    state_root / "profiles",
                ):
                    directory.mkdir(parents=True, exist_ok=True)

                overlay_path = (
                    state_root / "profiles" / f"{candidate.profile.profile_id}.local.yaml"
                )
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
                    validate_workspace_root_file(
                        candidate.root,
                        candidate.root / "AGENTS.md",
                        label="AGENTS.md",
                    ),
                    self._managed_agents_content(candidate),
                )
                write_diagnostic("初始化步骤 4/5：安装研发 Skill 并写入授权凭证")
                self._install_workspace_skills(candidate)
                if candidate.persist_credentials and candidate.email and candidate.token:
                    update_env_file(
                        state_root / ".env",
                        {
                            candidate.connection.email_env: candidate.email,
                            candidate.connection.token_env: candidate.token,
                        },
                    )
                write_diagnostic("初始化步骤 5/5：写入研发员身份与工作空间索引")
                index_path = self._index_path()
                previous_index = index_path.read_bytes() if index_path.is_file() else None
                try:
                    self._write_workspace_index(candidate)
                    agent_path = state_root / "agent.json"
                    atomic_write_json(
                        agent_path,
                        {
                            "schema_version": 3,
                            "workplane": "developer",
                            "agent_id": candidate.agent_id,
                            "project_profile": candidate.profile.profile_id,
                            "jira_project": candidate.profile.project_key,
                            "connection_id": candidate.connection.connection_id,
                            "jira_base_url": candidate.connection.base_url,
                            "jira_site": jira_site_identity(candidate.connection.base_url),
                            "jira_account_id": preflight["jira_identity"],
                            "source_root": str(candidate.source_root),
                            "repository": candidate.repository,
                            **(
                                {"execution_identity": candidate.execution_identity}
                                if candidate.execution_identity is not None
                                else {}
                            ),
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
        except BaseException:
            if not state_root_existed and state_root.is_dir():
                shutil.rmtree(state_root, ignore_errors=True)
            raise

        return {
            **candidate.summary(),
            "jira_identity": preflight["jira_identity"],
            "jira_project_name": preflight["jira_project_name"],
            "source_checkout_status": source_status,
            "agent_config": str(state_root / "agent.json"),
            "profile_overlay": str(overlay_path),
            "agent_instructions": str(candidate.root / "AGENTS.md"),
            "skill_root": str(candidate.root / WORKSPACE_SKILLS_ROOT),
            "credential_protection": credential_protection,
            "preflight_status": "passed",
            "preflight_checks": preflight["checks"],
            "agentic_next_action": "inspect_explicit_jira_task",
        }

    def _check_workspace_boundary(self, checks: list[dict[str, str]]) -> None:
        validate_workspace_state_root(self.root)
        validate_workspace_root_file(
            self.root,
            self.root / "AGENTS.md",
            label="AGENTS.md",
        )
        if self.root == self.install_root or self.install_root in self.root.parents:
            raise _blocked(
                "workspace_boundary_invalid",
                "业务项目工作空间不能位于 AgenticOps 安装目录中",
                "请在独立目录创建业务项目工作空间",
            )
        source_ancestor = _source_ancestor(self.root)
        if source_ancestor is not None:
            raise _blocked(
                "workplane_mismatch",
                f"AgenticOps 源头仓库或其子目录不能初始化为业务项目工作空间：{source_ancestor}",
                "请切换到不位于 AgenticOps 源头目录树中的独立业务项目工作空间",
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

    def _check_install_ai_assets(self, checks: list[dict[str, str]]) -> None:
        self._developer_ai_rules()
        self._developer_skill_assets()
        checks.append({"check": "developer_ai_assets", "status": "passed"})

    def _check_workspace_ai_assets(
        self, candidate: WorkspaceCandidate, checks: list[dict[str, str]]
    ) -> None:
        agents_path = validate_workspace_root_file(
            candidate.root,
            candidate.root / "AGENTS.md",
            label="AGENTS.md",
        )
        agents_content = read_workspace_root_file(
            candidate.root,
            agents_path,
            label="AGENTS.md",
        )
        if (
            agents_content.count(MANAGED_START) != 1
            or agents_content.count(MANAGED_END) != 1
            or agents_content.rstrip("\n")
            != self._managed_agents_content(candidate).rstrip("\n")
        ):
            raise _blocked(
                "workspace_ai_entry_drift",
                "业务工作空间 AGENTS.md 中的 developer Rule 入口缺失或已漂移",
                "请由指导员重新运行 ao-work workspace init 同步当前 developer AI 入口",
            )
        skill_assets = self._developer_skill_assets()
        skill_root = validate_managed_path(
            candidate.root,
            candidate.root / WORKSPACE_SKILLS_ROOT,
            code="workspace_ai_asset_unsafe",
        )
        for name, source in skill_assets.items():
            target = validate_managed_path(
                candidate.root,
                skill_root / name / "SKILL.md",
                code="workspace_ai_asset_unsafe",
            )
            if target.is_symlink() or not target.is_file():
                raise _blocked(
                    "workspace_ai_asset_missing",
                    f"业务工作空间缺少可发现的 developer Skill：{name}",
                    "请由指导员重新运行 ao-work workspace init 修复 AI 入口",
                )
            unexpected = [path for path in target.parent.iterdir() if path.name != "SKILL.md"]
            if unexpected:
                raise _blocked(
                    "workspace_ai_asset_contaminated",
                    f"业务工作空间 Skill 含非准入文件：{name}/{unexpected[0].name}",
                    "请停止使用该工作空间，并由指导员核对后重新初始化",
                )
            try:
                target_content = target.read_bytes()
                source_content = source.read_bytes()
            except OSError as error:
                raise _blocked(
                    "workspace_ai_asset_invalid",
                    f"无法读取业务工作空间 developer Skill：{name}",
                    "请由指导员重新运行 ao-work workspace init 修复 AI 入口",
                ) from error
            if hashlib.sha256(target_content).digest() != hashlib.sha256(
                source_content
            ).digest():
                raise _blocked(
                    "workspace_ai_asset_drift",
                    f"业务工作空间 developer Skill 已偏离当前受信安装：{name}",
                    "请由指导员重新运行 ao-work workspace init 同步当前 developer Skill",
                )
        actual_entries = list(skill_root.iterdir()) if skill_root.is_dir() and not skill_root.is_symlink() else []
        actual_names = {path.name for path in actual_entries}
        if (
            actual_names != set(skill_assets)
            or any(path.is_symlink() or not path.is_dir() for path in actual_entries)
        ):
            raise _blocked(
                "workspace_ai_asset_contaminated",
                "业务工作空间 AI 入口包含非准入 Skill 或 maintainer 资产",
                "请停止使用该工作空间，并由指导员核对后重新初始化",
            )
        checks.append({"check": "workspace_ai_assets", "status": "passed"})

    def _check_existing_config(
        self,
        candidate: WorkspaceCandidate,
        checks: list[dict[str, str]],
        confirmed: bool,
    ) -> None:
        path = candidate.root / ".agentic-ops" / "agent.json"
        validate_workspace_managed_path(candidate.root, path)
        if not path.is_file():
            checks.append({"check": "existing_config", "status": "not_present"})
            return
        existing = read_json(path)
        same = all(
            existing.get(key) == value
            for key, value in {
                "workplane": "developer",
                "agent_id": candidate.agent_id,
                "project_profile": candidate.profile.profile_id,
                "jira_project": candidate.profile.project_key,
                "source_root": str(candidate.source_root),
                "repository": candidate.repository,
                "connection_id": candidate.connection.connection_id,
                "jira_base_url": candidate.connection.base_url,
                "jira_site": jira_site_identity(candidate.connection.base_url),
            }.items()
        )
        if candidate.execution_identity is not None:
            same = same and existing.get("execution_identity") == candidate.execution_identity
        if not same and not confirmed:
            raise _blocked(
                "existing_config_confirmation_required",
                "工作空间已有不同的 AgenticOps 配置",
                "请核对初始化摘要并明确确认覆盖已有配置",
            )
        checks.append(
            {"check": "existing_config", "status": "same" if same else "confirmed"}
        )

    def _check_existing_jira_account(
        self,
        candidate: WorkspaceCandidate,
        account_id: str,
        *,
        confirmed: bool,
    ) -> None:
        path = candidate.root / ".agentic-ops" / "agent.json"
        validate_workspace_managed_path(candidate.root, path)
        if not path.is_file():
            return
        existing = read_json(path)
        previous = existing.get("jira_account_id")
        if (
            existing.get("schema_version") == 3
            and previous != account_id
            and not confirmed
        ):
            raise _blocked(
                "existing_jira_account_confirmation_required",
                "工作空间已验证 Jira accountId 与当前授权账户不同",
                "请核对账户切换并显式确认重新初始化；不得静默替换研发员身份",
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
        source = validate_business_source_root(candidate.root, candidate.source_root)
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
            remote = self._validate_repository_remotes(source, candidate.repository)
            if check_remote:
                self._reject_git_url_rewrites(source)
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
            self._reject_git_url_rewrites(None)
            self._require_remote_access(_repository_url(candidate.repository))
        checks.append({"check": "source_repository", "status": "passed"})
        return "ready_to_clone"

    def _check_source_root_conflict(
        self, candidate: WorkspaceCandidate, checks: list[dict[str, str]]
    ) -> None:
        candidate_source = candidate.source_root.resolve()
        for entry in self._workspace_entries():
            other_root = Path(str(entry.get("workspace_root", ""))).expanduser()
            if other_root == candidate.root:
                continue
            if not (other_root / ".agentic-ops" / "agent.json").is_file():
                continue
            raw_source = entry.get("source_root")
            if not isinstance(raw_source, str) or not raw_source:
                continue
            other_source = Path(raw_source).expanduser().resolve()
            if (
                other_source == candidate_source
                or other_source in candidate_source.parents
                or candidate_source in other_source.parents
            ):
                raise _blocked(
                    "source_root_conflict",
                    f"业务源码目录已被另一业务项目工作空间使用：{candidate_source}",
                    "请为每个工作空间使用独立源码目录；共享写树不受支持",
                )
        checks.append({"check": "source_root_conflict", "status": "passed"})

    def _require_remote_access(self, remote: str) -> None:
        result = self._run_git(["ls-remote", remote, "HEAD"])
        if result.returncode != 0:
            raise _blocked(
                "source_repository_access_failed",
                "无法只读访问 Project Profile 配置的源码仓库",
                "请检查 GitHub 登录、SSH key、网络和仓库权限后重试",
            )

    def _ensure_source_checkout(self, candidate: WorkspaceCandidate) -> str:
        source = validate_business_source_root(candidate.root, candidate.source_root)
        if source.is_dir() and any(source.iterdir()):
            self._validate_checked_out_source(candidate)
            return "reused"
        restore_empty_directory = source.is_dir()
        source.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            source.rmdir()
        try:
            self._reject_git_url_rewrites(None)
            result = self._run_git_streaming(
                ["clone", "--progress", _repository_url(candidate.repository), str(source)]
            )
            if result.returncode != 0:
                raise _blocked(
                    "source_checkout_failed",
                    "源码仓库下载失败",
                    "请检查 GitHub 权限和网络后重试；未写入初始化完成标记",
                    details={"stderr_tail": _stderr_tail(result.stderr)},
                )
            self._validate_checked_out_source(candidate)
        except BaseException:
            self._rollback_source_checkout(source, restore_empty_directory)
            raise
        return "cloned"

    def _write_source_container_readme(self, candidate: WorkspaceCandidate) -> None:
        container = candidate.source_root.parent
        container.mkdir(parents=True, exist_ok=True)
        content = (
            "# 业务源码目录\n\n"
            f"{MANAGED_CODE_START}\n"
            f"- 归属工作空间：{candidate.root}\n"
            f"- agent_id：{candidate.agent_id}\n"
            f"- project_profile：{candidate.profile.profile_id}\n"
            f"- repository：{candidate.repository}\n"
            f"- 生成时间：{datetime.now(timezone.utc).isoformat()}\n\n"
            "本目录存放业务项目源代码，由 ao-work workspace init 管理。身份、凭证和任务状态保存在\n"
            f"工作空间 {candidate.root}/.agentic-ops/ 内；本文件只是配套说明，权威映射以 .agentic-ops\n"
            "受管配置为准。请勿手改管理块；重新初始化工作空间时会重新生成。\n"
            f"{MANAGED_CODE_END}\n"
        )
        atomic_write_text(container / "README.md", content)

    def _validate_checked_out_source(self, candidate: WorkspaceCandidate) -> None:
        source = validate_business_source_root(candidate.root, candidate.source_root)
        if not source.is_dir() or not (source / ".git").exists():
            raise _blocked(
                "source_checkout_invalid",
                "源码下载结果不是可识别的 Git 仓库",
                "请检查源码目录；初始化已回滚，不要继续使用该下载结果",
            )
        self._validate_repository_remotes(source, candidate.repository)

    def _validate_repository_remotes(
        self, source: Path, repository: str
    ) -> subprocess.CompletedProcess[str]:
        raw_fetch = self._run_git(
            ["-C", str(source), "config", "--get-all", "remote.origin.url"]
        )
        effective_fetch = self._run_git(
            ["-C", str(source), "remote", "get-url", "--all", "origin"]
        )
        effective_push = self._run_git(
            ["-C", str(source), "remote", "get-url", "--push", "--all", "origin"]
        )
        raw_push = self._run_git(
            ["-C", str(source), "config", "--get-all", "remote.origin.pushurl"]
        )
        raw_push_urls = raw_push.stdout.splitlines() if raw_push.returncode == 0 else []
        invalid = (
            raw_fetch.returncode != 0
            or len(raw_fetch.stdout.splitlines()) != 1
            or effective_fetch.returncode != 0
            or len(effective_fetch.stdout.splitlines()) != 1
            or effective_push.returncode != 0
            or len(effective_push.stdout.splitlines()) != 1
            or raw_push.returncode not in {0, 1}
            or len(raw_push_urls) > 1
        )
        urls = [
            *raw_fetch.stdout.splitlines(),
            *effective_fetch.stdout.splitlines(),
            *effective_push.stdout.splitlines(),
            *raw_push_urls,
        ]
        if invalid or any(
            not github_repository_url_matches(url, repository) for url in urls
        ):
            raise _blocked(
                "source_repository_mismatch",
                "源码仓库 raw/effective fetch/push URL 数量或仓库身份不一致",
                "请核对 origin、pushurl 和 repositories.default；不接受 URL 改写后的等价地址",
            )
        return subprocess.CompletedProcess(
            effective_fetch.args,
            effective_fetch.returncode,
            effective_fetch.stdout.splitlines()[0] + "\n",
            effective_fetch.stderr,
        )

    def _reject_git_url_rewrites(self, source: Path | None) -> None:
        prefix = ["-C", str(source)] if source is not None else []
        result = self._run_git(
            [*prefix, "config", "--show-origin", "--get-regexp", r"^url\..*\.(insteadOf|pushInsteadOf)$"]
        )
        if result.returncode == 0 and result.stdout.strip():
            raise _blocked(
                "git_url_rewrite_forbidden",
                "检测到 Git url.*.insteadOf/pushInsteadOf 改写，禁止访问业务仓库",
                "请移除 URL 改写后重试；Runtime 必须核对真实 github.com owner/repository",
            )
        if result.returncode not in {0, 1}:
            raise _blocked(
                "git_url_rewrite_check_failed",
                "无法确认 Git URL 改写配置",
                "请修复 Git 配置读取后重试；在确认前不得 clone 或 ls-remote",
            )

    @staticmethod
    def _rollback_source_checkout(source: Path, restore_empty_directory: bool) -> None:
        if source.is_symlink():
            source.unlink(missing_ok=True)
        elif source.is_dir():
            shutil.rmtree(source, ignore_errors=True)
        elif source.exists():
            source.unlink(missing_ok=True)
        if restore_empty_directory:
            source.mkdir(parents=True, exist_ok=True)

    def _protect_workspace_state_from_git(self) -> str:
        return protect_workspace_env_from_git(
            self.root,
            run_git=lambda arguments: self._run_git(arguments),
        )

    def _run_git(
        self, arguments: list[str], *, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        effective_timeout = timeout or self.git_timeout
        started = time.monotonic()
        try:
            return subprocess.run(
                ["git", *arguments],
                capture_output=True,
                text=True,
                check=False,
                timeout=effective_timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            command = " ".join(["git", *arguments])
            elapsed = time.monotonic() - started
            raise _blocked(
                "git_check_failed",
                f"Git 前置检查失败（{command}，已等待 {elapsed:.1f}s）：{type(error).__name__}",
                "请检查 Git 安装、网络和仓库权限后重试",
                details={
                    "git_command": command,
                    "elapsed_seconds": round(elapsed, 1),
                    "git_timeout_seconds": effective_timeout,
                },
            ) from error

    def _run_git_streaming(
        self,
        arguments: list[str],
        *,
        stall_warn_interval: float = 30.0,
    ) -> subprocess.CompletedProcess[str]:
        """流式运行 git 命令（仅用于源码克隆），不设超时。

        大仓库 + 慢网络下克隆可以无限期进行，git 的 --progress 输出通过
        stderr 实时转发给用户自行判断快慢；仅当 stderr 持续无任何输出超过
        stall_warn_interval 时输出停滞提示（不终止进程）。调用方可以用
        Ctrl+C 中断，克隆残留由初始化回滚清理，不会污染工作空间。
        """
        command = " ".join(["git", *arguments])
        process = subprocess.Popen(
            ["git", *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        last_output = time.monotonic()
        last_warned = last_output
        active_streams: list[Any] = [process.stdout, process.stderr]
        try:
            while True:
                now = time.monotonic()
                if (
                    now - last_output >= stall_warn_interval
                    and now - last_warned >= stall_warn_interval
                ):
                    last_warned = now
                    write_diagnostic(
                        f"源码克隆已 {now - last_output:.0f}s 无新进度输出，可能网络停滞；"
                        "请检查代理与 SSH 隧道（可 Ctrl+C 中断，初始化会自动回滚，不残留污染）"
                    )
                ready, _, _ = select.select(
                    active_streams, [], [], min(1.0, max(0.05, stall_warn_interval / 2))
                )
                for stream in ready:
                    try:
                        chunk = os.read(stream.fileno(), 65536)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        if stream in active_streams:
                            active_streams.remove(stream)
                        continue
                    last_output = time.monotonic()
                    if stream is process.stderr:
                        _forward_stderr(chunk)
                        stderr_chunks.append(chunk)
                    else:
                        stdout_chunks.append(chunk)
                if process.poll() is not None:
                    for stream in (process.stdout, process.stderr):
                        if stream is None:
                            continue
                        while True:
                            try:
                                chunk = os.read(stream.fileno(), 65536)
                            except OSError:
                                chunk = b""
                            if not chunk:
                                break
                            if stream is process.stderr:
                                _forward_stderr(chunk)
                                stderr_chunks.append(chunk)
                            else:
                                stdout_chunks.append(chunk)
                    break
        except BaseException:
            try:
                process.kill()
            except OSError:
                pass
            process.wait()
            self._close_streams(process)
            raise
        self._close_streams(process)
        return subprocess.CompletedProcess(
            ["git", *arguments],
            process.returncode,
            b"".join(stdout_chunks).decode("utf-8", errors="replace"),
            b"".join(stderr_chunks).decode("utf-8", errors="replace"),
        )

    @staticmethod
    def _close_streams(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def _managed_agents_content(self, candidate: WorkspaceCandidate) -> str:
        path = validate_workspace_root_file(
            candidate.root,
            candidate.root / "AGENTS.md",
            label="AGENTS.md",
        )
        existing = read_workspace_root_file(
            candidate.root,
            path,
            label="AGENTS.md",
        )
        developer_rules = self._developer_ai_rules()
        block = (
            f"{MANAGED_START}\n"
            "# AgenticOps 业务项目工作空间\n\n"
            "本工作空间固定使用 `developer` 工作面。不得加载或调用 `maintainer` 工作面的规则、Skill、授权、配置或入口。自然语言交互和 Jira 人可见内容使用中文。\n\n"
            f"- agent_id：`{candidate.agent_id}`\n"
            f"- Project Profile：`{candidate.profile.profile_id}`\n"
            f"- Jira Project：`{candidate.profile.project_key}`\n"
            f"- 源码目录：`{candidate.source_root}`\n\n"
            "下面的 developer AI 规则已在初始化时从受信安装复制到本工作空间，AI 必须直接加载并执行；命令入口固定为 `ao-work`，不得自行选择或切换工作面。\n"
            "Codex 可发现的 developer Skill 已作为受管副本安装到当前工作空间 `.agents/skills/`；需要流程能力时只从该目录选择 Skill。标准资产由 `ao-work` 从受信安装根解析，不能把 `developer/...` 当作业务仓库相对路径，也不得搜索或恢复 maintainer 资产。\n"
            "执行任务前先调用 `ao-work workspace preflight`；任何阻断结果都不得绕过。\n\n"
            "## developer AI 规则（受管副本）\n\n"
            f"{developer_rules}\n"
            f"{MANAGED_END}"
        )
        if MANAGED_START in existing and MANAGED_END in existing:
            before, rest = existing.split(MANAGED_START, 1)
            _, after = rest.split(MANAGED_END, 1)
            prefix = f"{before.rstrip()}\n\n" if before.strip() else ""
            return f"{prefix}{block}{after}"
        if existing.strip():
            return f"{existing.rstrip()}\n\n{block}"
        return block

    def _developer_ai_rules(self) -> str:
        assets = (
            self.install_root / "developer" / "AGENTS.md",
            self.install_root / "developer" / "rules" / "ai-execution.md",
        )
        sections: list[str] = []
        for asset in assets:
            try:
                content = asset.read_text(encoding="utf-8").strip()
            except OSError as error:
                raise _blocked(
                    "developer_rule_asset_missing",
                    f"无法读取 developer AI 规则资产：{asset}",
                    "请重新安装 AgenticOps developer 分发后重试",
                ) from error
            if not content:
                raise _blocked(
                    "developer_rule_asset_missing",
                    f"developer AI 规则资产为空：{asset}",
                    "请重新安装 AgenticOps developer 分发后重试",
                )
            sections.append(content)
        return "\n\n".join(sections)

    def _developer_skill_assets(self) -> dict[str, Path]:
        root = self.install_root / "developer" / "skills"
        if root.is_symlink() or not root.is_dir():
            raise _blocked(
                "developer_skill_assets_missing",
                "developer 安装缺少安全的 Skill 目录",
                "请重新安装 AgenticOps developer 分发后重试",
            )
        assets: dict[str, Path] = {}
        for skill_dir in sorted(root.iterdir(), key=lambda path: path.name):
            if skill_dir.is_symlink() or not skill_dir.is_dir():
                raise _blocked(
                    "developer_skill_assets_invalid",
                    f"developer Skill 路径类型不安全：{skill_dir}",
                    "请重新安装 AgenticOps developer 分发后重试",
                )
            skill_file = skill_dir / "SKILL.md"
            if skill_file.is_symlink() or not skill_file.is_file():
                raise _blocked(
                    "developer_skill_assets_invalid",
                    f"developer Skill 缺少普通 SKILL.md：{skill_dir.name}",
                    "请重新安装 AgenticOps developer 分发后重试",
                )
            content = skill_file.read_text(encoding="utf-8")
            if "workplane: developer" not in content or "maintainer" in skill_dir.name:
                raise _blocked(
                    "developer_skill_assets_invalid",
                    f"developer Skill 工作面声明无效：{skill_dir.name}",
                    "请由项目维护者修复 Skill 归属后重新发布",
                )
            assets[skill_dir.name] = skill_file
        if not assets:
            raise _blocked(
                "developer_skill_assets_missing",
                "developer 安装没有可发现的 Skill",
                "请重新安装 AgenticOps developer 分发后重试",
            )
        return assets

    def _install_workspace_skills(self, candidate: WorkspaceCandidate) -> None:
        skill_root = validate_managed_path(
            candidate.root,
            candidate.root / WORKSPACE_SKILLS_ROOT,
            code="workspace_ai_asset_unsafe",
        )
        if skill_root.is_symlink() or (skill_root.exists() and not skill_root.is_dir()):
            raise _blocked(
                "workspace_ai_asset_unsafe",
                "业务工作空间 .agents/skills 必须是工作空间内的普通目录",
                "请移除异常路径并重新运行 ao-work workspace init",
            )
        skill_root.mkdir(parents=True, exist_ok=True)
        expected = self._developer_skill_assets()
        for existing in skill_root.iterdir():
            if existing.name not in expected:
                raise _blocked(
                    "workspace_ai_asset_contaminated",
                    f"业务工作空间存在非准入 Skill：{existing.name}",
                    "请先人工核对并移除非准入 Skill，再重新初始化",
                )
        for name, source in expected.items():
            target_dir = validate_managed_path(
                candidate.root,
                skill_root / name,
                code="workspace_ai_asset_unsafe",
            )
            if target_dir.is_symlink() or (
                target_dir.exists() and not target_dir.is_dir()
            ):
                raise _blocked(
                    "workspace_ai_asset_unsafe",
                    f"业务工作空间 Skill 目录类型不安全：{name}",
                    "请移除异常路径并重新运行 ao-work workspace init",
                )
            target_dir.mkdir(parents=True, exist_ok=True)
            unexpected = [path for path in target_dir.iterdir() if path.name != "SKILL.md"]
            if unexpected:
                raise _blocked(
                    "workspace_ai_asset_contaminated",
                    f"业务工作空间 Skill 含非准入文件：{name}/{unexpected[0].name}",
                    "请先人工核对并移除非准入文件，再重新初始化",
                )
            atomic_write_text(
                target_dir / "SKILL.md",
                source.read_text(encoding="utf-8"),
            )

    def _index_path(self) -> Path:
        return validate_managed_path(
            self.install_root / "user",
            self.install_root / "user" / "workspace-index.json",
            code="workspace_index_path_unsafe",
        )

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
                "source_root": str(candidate.source_root),
            }
        )
        atomic_write_json(
            self._index_path(),
            {"schema_version": 1, "workspaces": entries},
        )


def _repository_url(repository: str) -> str:
    return f"git@github.com:{repository}.git"


def _repository_short_name(repository: str) -> str:
    name = repository.rsplit("/", 1)[-1].strip()
    if (
        not name
        or name in {".", ".."}
        or any(character in name for character in ("/", "\\", "\x00"))
    ):
        raise _blocked(
            "source_root_repository_name_invalid",
            f"无法从仓库映射推导源码目录名：{repository}",
            "请检查 Project Profile 的 repositories.default 配置",
        )
    return name


def _forward_stderr(chunk: bytes) -> None:
    """把 git 的原始 stderr 字节实时转发给人读终端。

    真实终端下走 sys.stderr.buffer 保持字节原样（git --progress 用 \\r
    行内刷新，不能按 \\n 切行）；测试环境 stderr 被替换为文本流时降级解码。
    """
    stream = sys.stderr
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(chunk)
        binary.flush()
    else:
        stream.write(chunk.decode("utf-8", errors="replace"))
        stream.flush()


def _stderr_tail(stderr: str, limit: int = 4096) -> str:
    """截取 git stderr 尾部，供失败 JSON 的诊断信息使用。"""
    if len(stderr) <= limit:
        return stderr
    return stderr[-limit:]


def _blocked(
    code: str, message: str, action: str, details: dict[str, Any] | None = None
) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
        details=details or {},
    )
