from __future__ import annotations

import json
import hashlib
import os
import re
import select
import shlex
import shutil
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
    install_entry_sha256,
    resolve_source_pool_root,
    validate_workspace_jira_binding,
    validate_workspace_project_binding,
)
from ao_work.installation import (
    build_execution_identity,
    install_user_dir,
    load_install_credentials,
    load_install_identity,
    mask_email,
    validate_agent_id,
)
from ao_work.jira.client import JiraClient, UrllibJiraTransport
from ao_work.git_security import github_repository_url_matches
from ao_work.managed_io import read_managed_text
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult, write_diagnostic
from ao_work.task_state.io import atomic_write_json, atomic_write_text, read_json
from ao_work.task_state.locking import TaskLock
from ao_work.workspace import (
    Workspace,
    _source_ancestor,
    task_worktree_path,
    validate_business_source_root,
    validate_source_pool_root,
)
from ao_work.workspace_security import (
    protect_workspace_state_from_git,
    read_workspace_root_file,
    validate_managed_path,
    validate_workspace_managed_path,
    validate_workspace_root_file,
    validate_workspace_state_root,
)

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MANAGED_START = "<!-- agentic-ops:workspace:start -->"
MANAGED_END = "<!-- agentic-ops:workspace:end -->"
MANAGED_CODE_START = "<!-- agentic-ops:workspace-code:start -->"
MANAGED_CODE_END = "<!-- agentic-ops:workspace-code:end -->"
WORKSPACE_SKILLS_ROOT = Path(".agents") / "skills"
WORKSPACE_ENTRY = Path(".agentic-ops") / "bin" / "ao-work"


@dataclass(frozen=True)
class WorkspaceCandidate:
    root: Path
    install_root: Path
    agent_id: str
    profile: ProjectProfile
    connection: JiraConnection
    source_root: Path
    source_pool_root: Path | None
    repository: str
    email: str | None
    token: str | None
    credential_source: str
    execution_identity: dict[str, str] | None = None
    source_root_derived: bool = False
    pool_mode: bool = False
    install_identity_ref: str = ""
    install_entry_sha256: str = ""

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
            "source_pool_root": str(self.source_pool_root) if self.source_pool_root else None,
            "repository_count": len(self.profile.repository_candidates()),
            "task_worktree_layout": (
                "<pool_root>/<JIRA-KEY>/<from_branch>/<repo>"
                if self.source_pool_root is not None
                else None
            ),
            "execution_identity": self.execution_identity,
            "workspace_entry": str(WORKSPACE_ENTRY),
        }


class WorkspaceInitializer:
    def __init__(self, root: Path, install_root: Path, *, git_timeout: float = 60.0) -> None:
        self.root = root.expanduser().resolve()
        self.install_root = install_root.expanduser().resolve()
        self.git_timeout = git_timeout
        validate_workspace_state_root(self.root)

    def prepare(
        self,
        profile_id: str,
        agent_id: str | None = None,
        *,
        source_root: str | None = None,
        source_pool_root: str | None = None,
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
        install_identity = load_install_identity(self.install_root)
        install_credentials = load_install_credentials(self.install_root)
        if install_credentials is None:
            raise _blocked(
                "jira_credentials_missing",
                "developer 安装尚未配置完整 Jira 凭据",
                "请先运行 ao-work auth 完成安装级授权",
            )
        effective_agent_id = validate_agent_id(str(install_identity["agent_id"]))
        if agent_id is not None and validate_agent_id(agent_id) != effective_agent_id:
            raise _blocked(
                "install_identity_drift",
                "工作空间候选 agent_id 与当前安装身份不一致",
                "请停止传入工作空间身份，并通过 ao-work auth 核对当前安装",
            )
        execution_identity = dict(install_identity["execution_identity"])
        email, token = (install_credentials[0].strip(), install_credentials[1].strip())
        if email != str(install_identity["jira_email"]).strip():
            raise _blocked(
                "install_identity_drift",
                "安装身份中的 Jira email 与安装凭据不一致",
                "请运行 ao-work auth 重新配置同一 Jira 账户",
            )
        credential_source = "installation"
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
        execution_identity = build_execution_identity(
            str(execution_identity.get("git_author_name", "")),
            str(execution_identity.get("git_author_email", "")),
            str(execution_identity.get("github_actor_login", "")),
        )
        source_root_derived = not source_root
        # 池根必配：显式参数 > 研发员级配置 > 阻断（无兼容回退）。
        # init 场景 allow_missing=True：池根不存在时由 apply 自动创建（D-048 3.2）。
        if source_pool_root is not None:
            pool_root = validate_source_pool_root(
                Path(source_pool_root), allow_missing=True
            )
        else:
            configured_pool_root = resolve_source_pool_root(self.install_root)
            if configured_pool_root is None:
                raise _blocked(
                    "source_pool_root_invalid",
                    "中央克隆池根（source_pool_root）未配置",
                    "请先在 ~/.agentic-ops/user/config.yaml 配置 source_pool_root，"
                    "或使用 --source-pool-root 显式指定（仅本次）",
                )
            pool_root = validate_source_pool_root(
                configured_pool_root, allow_missing=True
            )
        if source_root:
            resolved_source = validate_business_source_root(
                self.root, Path(source_root).expanduser().resolve()
            )
            # 显式 source_root 等于池根时仍视为池模式（preflight 重放场景）。
            pool_mode = resolved_source == pool_root
        else:
            # 池模式：source_root 语义改为池根（任务工作树在接管时创建）。
            resolved_source = validate_business_source_root(self.root, pool_root)
            pool_mode = True
        # 安装目录身份指纹（阶段二）：agent.json v4 引用，防错装。
        identity_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "agent_id": install_identity["agent_id"],
                    "jira_email": install_identity["jira_email"],
                    "execution_identity": install_identity["execution_identity"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        install_identity_ref = f"install:{identity_fingerprint}"
        entry_sha256 = install_entry_sha256(self.install_root)
        return WorkspaceCandidate(
            root=self.root,
            install_root=self.install_root,
            agent_id=effective_agent_id,
            profile=profile,
            connection=connection,
            source_root=resolved_source,
            source_pool_root=pool_root,
            repository=repository,
            email=email,
            token=token,
            credential_source=credential_source,
            execution_identity=execution_identity,
            source_root_derived=source_root_derived,
            pool_mode=pool_mode,
            install_identity_ref=install_identity_ref,
            install_entry_sha256=entry_sha256,
        )

    def preflight(
        self,
        candidate: WorkspaceCandidate,
        *,
        confirm_existing_config: bool = False,
        check_remote: bool = True,
    ) -> dict[str, Any]:
        validate_workspace_state_root(self.root)
        write_diagnostic("初始化预检 1/3：工作空间边界、安装资产与既有配置检查")
        checks: list[dict[str, str]] = []
        self._check_workspace_boundary(checks)
        self._check_workspace_writable(checks)
        self._check_install_ai_assets(checks)
        validate_agent_id(candidate.agent_id)
        checks.append({"check": "agent_id_format", "status": "passed"})
        checks.append({"check": "project_profile", "status": "passed"})
        self._check_existing_config(candidate, checks, confirm_existing_config)
        existing_agent = candidate.root / ".agentic-ops" / "agent.json"
        if existing_agent.is_file() and read_json(existing_agent).get("schema_version") == 5:
            self._check_workspace_ai_assets(candidate, checks)
        self._check_agent_id_collision(candidate, checks)
        self._check_authorization(candidate, checks)
        write_diagnostic("初始化预检 2/3：Jira 授权与项目访问验证")
        jira_identity, jira_project = self._check_jira(candidate, checks)
        self._check_existing_jira_account(
            candidate,
            jira_identity,
            confirmed=confirm_existing_config,
        )
        write_diagnostic("初始化预检 3/3：源码仓库只读访问与冲突校验")
        source_status, skipped_repositories = self._check_source(
            candidate, checks, check_remote=check_remote
        )
        self._check_source_root_conflict(candidate, checks)
        return {
            "status": "passed",
            "checks": checks,
            "jira_identity": jira_identity,
            "jira_project_name": jira_project,
            "source_checkout_status": source_status,
            "skipped_repositories": skipped_repositories,
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
                write_diagnostic("初始化步骤 1/5：校验并保护工作空间状态")
                credential_protection = self._protect_workspace_state_from_git()
                if candidate.pool_mode:
                    write_diagnostic(
                        f"初始化步骤 2/5：准备中央克隆池成员（{len(candidate.profile.repository_candidates())} 个仓库）→ {candidate.source_pool_root}"
                    )
                    source_status, skipped_members = self._prepare_pool_members(
                        candidate, preflight
                    )
                    self._persist_pool_root(candidate)
                    write_diagnostic(f"池成员准备完成（{source_status}）")
                else:
                    write_diagnostic(
                        f"初始化步骤 2/5：下载业务源码仓库 {candidate.repository} → {candidate.source_root}"
                    )
                    source_status = self._ensure_source_checkout(candidate)
                    skipped_members = []
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
                atomic_write_text(
                    validate_workspace_root_file(
                        candidate.root,
                        candidate.root / "CLAUDE.md",
                        label="CLAUDE.md",
                    ),
                    self._managed_guide_content(candidate),
                )
                self._write_workspace_entry(candidate)
                write_diagnostic("初始化步骤 4/5：安装研发 Skill")
                self._install_workspace_skills(candidate)
                write_diagnostic("初始化步骤 5/5：写入研发员身份与工作空间索引")
                index_path = self._index_path()
                previous_index = index_path.read_bytes() if index_path.is_file() else None
                try:
                    self._write_workspace_index(candidate)
                    agent_path = state_root / "agent.json"
                    # agent.json schema v5：身份/凭证只在安装目录，工作空间只保留绑定与本地入口摘要。
                    atomic_write_json(
                        agent_path,
                        {
                            "schema_version": 5,
                            "workplane": "developer",
                            "project_profile": candidate.profile.profile_id,
                            "jira_project": candidate.profile.project_key,
                            "connection_id": candidate.connection.connection_id,
                            "jira_base_url": candidate.connection.base_url,
                            "jira_site": jira_site_identity(candidate.connection.base_url),
                            "source_root": str(candidate.source_root),
                            "repository": candidate.repository,
                            "install_identity_ref": candidate.install_identity_ref,
                            "workspace_entry": str(WORKSPACE_ENTRY),
                            "install_entry_sha256": candidate.install_entry_sha256,
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

        if skipped_members:
            names = "、".join(entry["repository"] for entry in skipped_members)
            write_diagnostic(
                f"初始化完成，但有 {len(skipped_members)} 个源码仓库因无权限被跳过："
                f"{names}；请补权限后重新运行 ao-work workspace init 补齐"
            )
        return {
            **candidate.summary(),
            "jira_identity": preflight["jira_identity"],
            "jira_project_name": preflight["jira_project_name"],
            "source_checkout_status": source_status,
            "skipped_repositories": skipped_members,
            "agent_config": str(state_root / "agent.json"),
            "profile_overlay": str(overlay_path),
            "agent_instructions": str(candidate.root / "AGENTS.md"),
            "workspace_entry": str(candidate.root / WORKSPACE_ENTRY),
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
        guide_path = validate_workspace_root_file(
            candidate.root,
            candidate.root / "CLAUDE.md",
            label="CLAUDE.md",
        )
        guide_content = read_workspace_root_file(
            candidate.root,
            guide_path,
            label="CLAUDE.md",
        )
        if guide_content.count(MANAGED_START) != 1 or guide_content.count(
            MANAGED_END
        ) != 1:
            raise _blocked(
                "workspace_ai_entry_drift",
                "业务工作空间 CLAUDE.md 中的 developer 引导入口缺失或管理块不完整",
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
        self._check_workspace_entry(candidate)
        checks.append({"check": "workspace_ai_assets", "status": "passed"})

    def _workspace_entry_path(self, candidate: WorkspaceCandidate) -> Path:
        return validate_workspace_managed_path(
            candidate.root,
            candidate.root / WORKSPACE_ENTRY,
        )

    def _workspace_entry_content(self, candidate: WorkspaceCandidate) -> str:
        install_entry = candidate.install_root / "bin" / "ao-work"
        return (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n\n"
            "SCRIPT_DIR=\"$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd -P)\"\n"
            f"INSTALL_ENTRY={shlex.quote(str(install_entry))}\n\n"
            "if [ ! -x \"$INSTALL_ENTRY\" ]; then\n"
            "  printf 'AgenticOps：绑定的 developer 安装入口不可用；请使用该安装重新运行 "
            "ao-work workspace init --confirm-existing-config 恢复工作空间。\\n' >&2\n"
            "  exit 1\n"
            "fi\n\n"
            "exec \"$INSTALL_ENTRY\" \"$@\"\n"
        )

    def _write_workspace_entry(self, candidate: WorkspaceCandidate) -> None:
        path = self._workspace_entry_path(candidate)
        atomic_write_text(path, self._workspace_entry_content(candidate))
        os.chmod(path, 0o700)

    def _check_workspace_entry(self, candidate: WorkspaceCandidate) -> None:
        path = self._workspace_entry_path(candidate)
        try:
            content = read_managed_text(path, label="工作空间本地 ao-work 入口")
        except OSError as error:
            raise _blocked(
                "workspace_local_entry_missing",
                "业务工作空间缺少可执行的本地 ao-work 入口",
                "请使用绑定安装的 ao-work workspace init --confirm-existing-config 重新生成入口",
            ) from error
        if content != self._workspace_entry_content(candidate) or not os.access(path, os.X_OK):
            raise _blocked(
                "workspace_local_entry_drift",
                "业务工作空间本地 ao-work 入口已漂移或不可执行",
                "请使用绑定安装的 ao-work workspace init --confirm-existing-config 恢复入口",
            )

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
        expected = {
            "schema_version": 5,
            "workplane": "developer",
            "project_profile": candidate.profile.profile_id,
            "jira_project": candidate.profile.project_key,
            "source_root": str(candidate.source_root),
            "repository": candidate.repository,
            "connection_id": candidate.connection.connection_id,
            "jira_base_url": candidate.connection.base_url,
            "jira_site": jira_site_identity(candidate.connection.base_url),
            "install_identity_ref": candidate.install_identity_ref,
            "workspace_entry": str(WORKSPACE_ENTRY),
            "install_entry_sha256": candidate.install_entry_sha256,
        }
        differences = [
            {
                "field": key,
                "existing": str(existing.get(key, "")),
                "candidate": str(value),
            }
            for key, value in expected.items()
            if existing.get(key) != value
        ]
        if differences and not confirmed:
            raise _blocked(
                "existing_config_confirmation_required",
                "工作空间已有不同的 AgenticOps 配置",
                "请核对初始化摘要并明确确认覆盖已有配置",
                details={"differences": differences},
            )
        checks.append(
            {
                "check": "existing_config",
                "status": "same" if not differences else "confirmed",
            }
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
                required_human_action="请先运行 ao-work auth 完成安装级授权",
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
    ) -> tuple[str, list[dict[str, Any]]]:
        if shutil.which("git") is None:
            raise _blocked(
                "git_missing",
                "未找到 Git 命令",
                "请安装 Git 后重试",
            )
        checks.append({"check": "git_available", "status": "passed"})
        if candidate.pool_mode:
            # preflight 只读校验：池根不存在时允许（apply 阶段创建），但不在此创建。
            pool_root = validate_source_pool_root(
                candidate.source_pool_root or candidate.source_root,
                allow_missing=True,
            )
            repositories = candidate.profile.repository_candidates()
            if not repositories:
                raise _blocked(
                    "source_pool_members_empty",
                    "Project Profile 没有配置 repositories.default 或 repositories.list",
                    "请先配置 profile 的默认仓库或仓库清单",
                )
            skipped: list[dict[str, Any]] = []
            if check_remote:
                self._reject_git_url_rewrites(None)
                total = len(repositories)
                for index, repository in enumerate(repositories, start=1):
                    write_diagnostic(
                        f"初始化预检：检查源码仓库 {repository}（{index}/{total}）"
                    )
                    url = _repository_url(repository)
                    result = self._run_git(["ls-remote", url, "HEAD"])
                    if result.returncode == 0:
                        continue
                    reason = _classify_remote_permission_error(result.stderr)
                    if reason is None:
                        # 网络类错误（超时/DNS/连接失败等）保持阻断，不跳过。
                        raise _blocked(
                            "source_repository_access_failed",
                            f"无法只读访问源码仓库 {repository}",
                            "请检查 GitHub 登录、SSH key、网络和仓库权限后重试",
                            details={
                                "repository": repository,
                                "stderr_tail": _stderr_tail(result.stderr),
                            },
                        )
                    # 权限类错误：跳过该仓库并提示用户，其余仓库继续检查。
                    write_diagnostic(
                        f"初始化预检：跳过无权限源码仓库 {repository}（{reason}）"
                    )
                    skipped.append(
                        {
                            "repository": repository,
                            "url": url,
                            "reason": reason,
                            "stderr_tail": _stderr_tail(result.stderr),
                        }
                    )
            checks.append({"check": "source_pool_root", "status": "passed"})
            checks.append({"check": "source_pool_members_remote", "status": "passed"})
            return "pool_ready", skipped
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
            return "reused", []

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
        return "ready_to_clone", []

    def _check_source_root_conflict(
        self, candidate: WorkspaceCandidate, checks: list[dict[str, str]]
    ) -> None:
        if candidate.pool_mode:
            # 池模式：池根由多个工作空间共享是设计意图，任务工作树按任务隔离；
            # 旧式「共享写树不受支持」只适用于非池模式单仓库源码目录。
            checks.append({"check": "source_root_conflict", "status": "pool_shared"})
            return
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
            "本目录存放业务项目源代码，由 ao-work workspace init 管理。身份与凭证保存在当前\n"
            f"developer 安装的 user/ 目录；项目和任务状态保存在 {candidate.root}/.agentic-ops/。\n"
            "本文件只是配套说明，权威映射以受管配置为准。请勿手改管理块；重新初始化工作空间时会重新生成。\n"
            f"{MANAGED_CODE_END}\n"
        )
        atomic_write_text(container / "README.md", content)

    def _persist_pool_root(self, candidate: WorkspaceCandidate) -> None:
        """把显式 --source-pool-root 持久化为研发员级配置（install user/config.yaml）。

        只补 source_pool_root 键，保留 config.yaml 已有内容；写入失败不阻断初始化
        （池根在本次运行内已生效，缺失时后续 preflight/takeover 会提示重新配置）。
        """
        if candidate.source_pool_root is None:
            return
        user_dir = install_user_dir(candidate.install_root)
        config_path = user_dir / "config.yaml"
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
            existing: dict[str, Any] = {}
            if config_path.is_file():
                try:
                    content = read_managed_text(config_path, label="研发员级配置 config.yaml") or ""
                    parsed = yaml.safe_load(content)
                    if isinstance(parsed, dict):
                        existing = parsed
                except Exception as error:
                    # 已有配置不可解析：留痕并覆盖为新配置（保留降级说明）。
                    write_diagnostic(
                        f"研发员级配置解析失败，将重建（{type(error).__name__}）"
                    )
                    existing = {}
            existing["source_pool_root"] = str(candidate.source_pool_root)
            atomic_write_text(
                config_path,
                yaml.safe_dump(existing, allow_unicode=True, sort_keys=False),
            )
        except Exception as error:
            # 持久化失败不阻断本次池模式初始化，但必须留痕供后续诊断。
            write_diagnostic(
                f"source_pool_root 持久化到研发员级配置失败（{type(error).__name__}）："
                "本次运行内已生效，后续 preflight/takeover 若提示未配置请人工核对"
            )

    def _prepare_pool_members(
        self,
        candidate: WorkspaceCandidate,
        preflight: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """准备中央克隆池成员全集：认领已有池成员或流式克隆缺失成员。

        - 池成员 = <pool_root>/<owner>/<repo> 普通完整克隆（保留主 checkout）。
        - 已存在 → 认领（adopt）：校验 remotes 精确匹配、拒绝 URL 改写、
          拒绝指向 AgenticOps 源头仓库；浅克隆自动流式 unshallow。
        - 缺失 → 流式 clone；中断续传：已完成成员保留，下次补齐。
        - 无权限仓库（预检已跳过或克隆时权限类失败）→ 跳过并提示用户，
          其余仓库继续准备；返回 (状态摘要, 跳过清单)。
        - 池成员级并发锁：<pool_root>/.locks/<owner>/<repo>.lock。
        """
        assert candidate.pool_mode
        pool_root = validate_source_pool_root(
            candidate.source_pool_root or candidate.source_root,
            allow_missing=True,
        )
        pool_root.mkdir(parents=True, exist_ok=True)
        repositories = candidate.profile.repository_candidates()
        if not repositories:
            raise _blocked(
                "source_pool_members_empty",
                "Project Profile 没有配置 repositories.default 或 repositories.list",
                "请先配置 profile 的默认仓库或仓库清单",
            )
        preflight_skipped = {
            entry["repository"]
            for entry in (preflight or {}).get("skipped_repositories", [])
            if isinstance(entry, dict) and entry.get("repository")
        }
        prepared: list[str] = []
        skipped_members: list[dict[str, Any]] = []
        adopted = 0
        cloned = 0
        for repository in repositories:
            if repository in preflight_skipped:
                # 预检已确认无权限，直接跳过，不重复尝试克隆。
                write_diagnostic(
                    f"池成员准备：跳过无权限源码仓库 {repository}（预检已跳过）"
                )
                skipped_members.append(
                    {
                        "repository": repository,
                        "url": _repository_url(repository),
                        "reason": "无访问权限（预检阶段已跳过）",
                    }
                )
                continue
            member_dir = pool_root / repository
            lock_path = pool_root / ".locks" / f"{repository.replace('/', '__')}.lock"
            with TaskLock(lock_path, timeout=10):
                if member_dir.is_dir() and any(member_dir.iterdir()):
                    self._validate_checked_out_source(candidate, source=member_dir, repository=repository)
                    if self._is_shallow_clone(member_dir):
                        self._unshallow_pool_member(member_dir, repository)
                        adopted += 1
                    else:
                        adopted += 1
                    prepared.append(repository)
                    continue
                self._reject_git_url_rewrites(None)
                result = self._run_git_streaming(
                    ["clone", "--progress", _repository_url(repository), str(member_dir)]
                )
                if result.returncode != 0:
                    reason = _classify_remote_permission_error(result.stderr)
                    if reason is not None:
                        # 权限类错误：跳过该仓库并提示用户，其余仓库继续准备。
                        write_diagnostic(
                            f"池成员准备：跳过无权限源码仓库 {repository}（{reason}）"
                        )
                        skipped_members.append(
                            {
                                "repository": repository,
                                "url": _repository_url(repository),
                                "reason": reason,
                                "stderr_tail": _stderr_tail(result.stderr),
                            }
                        )
                        continue
                    raise _blocked(
                        "source_checkout_failed",
                        f"池成员克隆失败：{repository}",
                        "请检查 GitHub 权限和网络后重试；未写入初始化完成标记，已完成成员保留",
                        details={"stderr_tail": _stderr_tail(result.stderr)},
                    )
                self._validate_checked_out_source(candidate, source=member_dir, repository=repository)
                cloned += 1
                prepared.append(repository)
        self._write_source_pool_readme(candidate, pool_root, tuple(prepared))
        return f"adopted={adopted},cloned={cloned},total={len(prepared)}", skipped_members

    def _is_shallow_clone(self, source: Path) -> bool:
        result = self._run_git(["-C", str(source), "rev-parse", "--is-shallow-repository"])
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _unshallow_pool_member(self, source: Path, repository: str) -> None:
        self._reject_git_url_rewrites(source)
        result = self._run_git_streaming(["-C", str(source), "fetch", "--unshallow", "origin"])
        if result.returncode != 0:
            raise _blocked(
                "source_pool_unshallow_failed",
                f"池成员浅克隆自动转完整克隆失败：{repository}",
                "请检查网络与远端可用性后重试；浅克隆成员不会被当作完整池成员",
                details={"stderr_tail": _stderr_tail(result.stderr)},
            )

    def _write_source_pool_readme(
        self,
        candidate: WorkspaceCandidate,
        pool_root: Path,
        repositories: tuple[str, ...],
    ) -> None:
        pool_root.mkdir(parents=True, exist_ok=True)
        content = (
            "# 中央克隆池（AI 研发员源码池）\n\n"
            f"{MANAGED_CODE_START}\n"
            f"- 归属安装：{candidate.install_root}\n"
            f"- agent_id：{candidate.agent_id}\n"
            f"- project_profile：{candidate.profile.profile_id}\n"
            f"- 池成员数：{len(repositories)}\n"
            f"- 生成时间：{datetime.now(timezone.utc).isoformat()}\n\n"
            "本目录存放业务项目源代码池，由 ao-work workspace init 管理。池成员按 "
            "<owner>/<repo> 组织；任务执行时在任务根 <JIRA-KEY>/<from_branch>/ 下用 "
            "git worktree 挂出任务级子工作树。身份与凭证保存在当前 developer 安装的 user/ "
            "目录，项目和任务状态保存在业务项目工作空间 .agentic-ops/；本文件只是配套说明，"
            "权威映射以受管配置为准。请勿手改管理块。\n"
            f"{MANAGED_CODE_END}\n"
        )
        atomic_write_text(pool_root / "README.md", content)

    def _validate_checked_out_source(
        self,
        candidate: WorkspaceCandidate,
        *,
        source: Path | None = None,
        repository: str | None = None,
    ) -> None:
        target = source if source is not None else candidate.source_root
        expected_repository = repository if repository is not None else candidate.repository
        validated = validate_business_source_root(candidate.root, target)
        if not validated.is_dir() or not (validated / ".git").exists():
            raise _blocked(
                "source_checkout_invalid",
                "源码下载结果不是可识别的 Git 仓库",
                "请检查源码目录；初始化已回滚，不要继续使用该下载结果",
            )
        self._validate_repository_remotes(validated, expected_repository)

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
        return protect_workspace_state_from_git(
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
            "下面的 developer AI 规则已在初始化时从受信安装复制到本工作空间，AI 必须直接加载并执行；命令入口固定为 `./.agentic-ops/bin/ao-work`，不得调用裸 `ao-work`、搜索 PATH 或自行选择、切换工作面。\n"
            "Codex 可发现的 developer Skill 已作为受管副本安装到当前工作空间 `.agents/skills/`；需要流程能力时只从该目录选择 Skill。标准资产由 `ao-work` 从受信安装根解析，不能把 `developer/...` 当作业务仓库相对路径，也不得搜索或恢复 maintainer 资产。\n"
            "`workspace preflight` 只用于诊断或修复工作空间，不是接管任务的前置步骤；接管时由 Runtime 重新校验工作空间、安装身份和 Jira 事实。\n\n"
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

    def _managed_guide_content(self, candidate: WorkspaceCandidate) -> str:
        """生成 Claude Code 的轻量引导文件（CLAUDE.md）。

        - 只声明 developer 工作面与权威入口，规则正文在 AGENTS.md（避免两份重复维护）。
        - Codex 兼容说明：AGENTS.md 是 Codex / Claude Code 共同识别的便携入口；
          Claude Code 不直接读 AGENTS.md，本文件用 @AGENTS.md 导入同一份规则。
        - Skill 发现：Codex 直接扫描 .agents/skills/；Claude Code 通过
          .claude/skills/ 下的 symlink 桥接（init 时创建）发现同一套 Skill。
        """
        return (
            f"{MANAGED_START}\n"
            "# AgenticOps 业务项目工作空间（Claude Code 引导）\n\n"
            "本文件（CLAUDE.md）只做入口引导，规则正文不在此维护。权威规则是仓库根的 "
            "`AGENTS.md`（developer 工作面，由 ao-work workspace init 生成并受管）：\n\n"
            "@AGENTS.md\n\n"
            "1. 请先按 `AGENTS.md` 中的 developer AI 规则执行，本文件不替代它。\n"
            "2. 工作空间固定使用 `developer` 工作面，命令入口为 `./.agentic-ops/bin/ao-work`；"
            "不得加载或调用 `maintainer` 工作面的规则、Skill、授权、配置或入口。\n"
            "3. `workspace preflight` 只用于诊断或修复工作空间，不是接管任务的前置步骤；接管时由 Runtime 重新校验工作空间、安装身份和 Jira 事实。\n\n"
            "## 与 Codex 的兼容说明\n\n"
            "- Codex 直接识别 `AGENTS.md` 并扫描 `.agents/skills/` 自动发现 Skill；"
            "Claude Code 通过 `.claude/skills/` 下的 symlink（由 init 创建）"
            "指向 `.agents/skills/` 发现同一套 Skill。\n"
            "- 若本文件与 `AGENTS.md` 内容冲突，以 `AGENTS.md` 为准；"
            "两者都不得把 `developer/...` 当作业务仓库相对路径，也不得搜索或恢复 maintainer 资产。\n"
            f"- 本文件由 `ao-work workspace init` 维护（管理块标记之间），"
            "请勿手改管理块；重新初始化工作空间时会重新生成。\n"
            f"{MANAGED_END}"
        )

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
        self._link_claude_skills(candidate, skill_root, expected)

    def _link_claude_skills(
        self,
        candidate: WorkspaceCandidate,
        skill_root: Path,
        expected: dict[str, Path],
    ) -> None:
        """为 Claude Code 建 `.claude/skills/` symlink 桥接到 `.agents/skills/`。

        Codex 直接扫描 `.agents/skills/`；Claude Code 只读 `.claude/skills/`，
        这里用 symlink 让两个工具共享同一套 Skill 目录（Claude Code 官方认可
        symlink 形式的项目 Skill）。每个 `<name>` 桥接为 `.claude/skills/<name>`
        → `../.agents/skills/<name>`（相对路径）。
        """
        claude_root = validate_managed_path(
            candidate.root,
            candidate.root / ".claude" / "skills",
            code="workspace_ai_asset_unsafe",
        )
        if claude_root.is_symlink() or (claude_root.exists() and not claude_root.is_dir()):
            raise _blocked(
                "workspace_ai_asset_unsafe",
                "业务工作空间 .claude/skills 必须是工作空间内的普通目录",
                "请移除异常路径并重新运行 ao-work workspace init",
            )
        claude_root.mkdir(parents=True, exist_ok=True)
        for existing in claude_root.iterdir():
            if existing.name not in expected and not existing.is_symlink():
                raise _blocked(
                    "workspace_ai_asset_contaminated",
                    f"业务工作空间存在非准入 Claude Skill：{existing.name}",
                    "请先人工核对并移除非准入 Skill，再重新初始化",
                )
        for name in expected:
            link = validate_managed_path(
                candidate.root,
                claude_root / name,
                code="workspace_ai_asset_unsafe",
            )
            target = os.path.relpath(skill_root / name, claude_root)
            if link.is_symlink():
                if os.readlink(link) != target:
                    raise _blocked(
                        "workspace_ai_asset_drift",
                        f"业务工作空间 Claude Skill symlink 目标不一致：{name}",
                        "请由指导员重新运行 ao-work workspace init 修复桥接",
                    )
                continue
            if link.exists():
                raise _blocked(
                    "workspace_ai_asset_unsafe",
                    f"业务工作空间 Claude Skill 路径类型不安全：{name}",
                    "请移除异常路径并重新运行 ao-work workspace init",
                )
            link.symlink_to(target, target_is_directory=True)

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


_PERMISSION_DENIED_MARKERS: tuple[tuple[str, str], ...] = (
    ("permission denied", "SSH 权限被拒绝"),
    ("permission to ", "仓库访问被拒绝"),
    ("repository not found", "仓库不存在或无访问权限"),
    ("access denied", "访问被拒绝"),
    ("authentication failed", "认证失败"),
    ("could not read username", "HTTPS 未提供凭证"),
    ("not authorized", "未授权"),
    ("403", "HTTP 403（无权限）"),
    ("404", "HTTP 404（仓库不存在或无权限）"),
)


def _classify_remote_permission_error(stderr: str) -> str | None:
    """判断 git 远端访问失败的 stderr 是否属于权限/认证类错误。

    权限类错误（GitHub 私有仓库无权限、仓库不存在、SSH key 被拒、HTTPS 认证失败等）
    在初始化时跳过该仓库并提示用户；返回匹配到的中文原因摘要，未匹配返回 None。
    网络类错误（超时、DNS 解析失败、连接拒绝、传输中断等）不匹配任何标记，
    由调用方保持阻断，避免把临时网络故障误判为无权限而静默跳过。
    """
    if not stderr:
        return None
    lowered = stderr.lower()
    for marker, reason in _PERMISSION_DENIED_MARKERS:
        if marker in lowered:
            return reason
    return None


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
