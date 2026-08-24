from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

FIELD_STATES: Final = frozenset(
    {"active", "read_only", "pending_validation", "unsupported", "deprecated"}
)


@dataclass(frozen=True)
class JiraConnection:
    connection_id: str
    base_url: str
    email_env: str
    token_env: str
    timeout_seconds: float = 20.0


@dataclass(frozen=True)
class FieldMapping:
    logical_name: str
    source: str
    jira_field: str | None = None
    section: str | None = None
    state: str = "active"
    writable: bool = False
    required: bool = False


@dataclass(frozen=True)
class RepositoryBranchRule:
    from_branch: str
    repo: str
    branch: str


@dataclass(frozen=True)
class BranchDerivation:
    derive_from: str = "default"
    default_branch: str = "main"
    default_rule: str = "same_name"
    dev_branches: tuple[tuple[str, str], ...] = ()
    baseline_branches: tuple[tuple[str, str], ...] = ()
    overrides: tuple[RepositoryBranchRule, ...] = ()


@dataclass(frozen=True)
class AnalysisMount:
    mode: str = "all"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorktreeDomain:
    domain_id: str
    baseline_repository: str
    repositories: tuple[str, ...]
    problem_version_repository: str = ""


@dataclass(frozen=True)
class CiProfile:
    provider: str
    start_timeout_seconds: int
    completion_timeout_seconds: int
    poll_interval_seconds: int
    max_remediation_attempts: int
    required_checks: tuple[str, ...]
    workflows: tuple[str, ...]
    artifact_name_patterns: tuple[str, ...]
    report_parser: str
    max_archive_bytes: int = 52_428_800
    max_extracted_bytes: int = 209_715_200
    max_file_bytes: int = 20_971_520
    max_files: int = 2_000
    max_depth: int = 20
    finish_agent_run_on_pass: bool = True
    transition_jira_done: bool = False

    def manifest_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "start_timeout_seconds": self.start_timeout_seconds,
            "completion_timeout_seconds": self.completion_timeout_seconds,
            "poll_interval_seconds": self.poll_interval_seconds,
            "max_remediation_attempts": self.max_remediation_attempts,
            "required_checks": list(self.required_checks),
            "workflows": list(self.workflows),
            "artifact_name_patterns": list(self.artifact_name_patterns),
            "report_parser": self.report_parser,
            "limits": {
                "max_archive_bytes": self.max_archive_bytes,
                "max_extracted_bytes": self.max_extracted_bytes,
                "max_file_bytes": self.max_file_bytes,
                "max_files": self.max_files,
                "max_depth": self.max_depth,
            },
            "completion": {
                "finish_agent_run_on_pass": self.finish_agent_run_on_pass,
                "transition_jira_done": self.transition_jira_done,
            },
        }


@dataclass(frozen=True)
class ProjectProfile:
    profile_id: str
    connection_id: str
    project_key: str
    task_query: str
    issue_types: tuple[str, ...] = ()
    fields: dict[str, FieldMapping] = field(default_factory=dict)
    status_mapping: dict[str, str] = field(default_factory=dict)
    transition_mapping: dict[str, dict[str, Any]] = field(default_factory=dict)
    default_repository: str | None = None
    workspace_source_root: str | None = None
    workspace_repository: str | None = None
    repository_list: tuple[str, ...] = ()
    analysis_mount: AnalysisMount = field(default_factory=AnalysisMount)
    branch_derivation: BranchDerivation = field(default_factory=BranchDerivation)
    worktree_domains: tuple[WorktreeDomain, ...] = ()
    process_id: str = "development_change_v1"
    ci: CiProfile | None = None

    def requested_jira_fields(self) -> list[str]:
        requested = {
            "summary",
            "description",
            "status",
            "issuetype",
            "assignee",
            "comment",
            "labels",
            "components",
            "project",
        }
        for mapping in self.fields.values():
            if mapping.jira_field and mapping.state in {"active", "read_only"}:
                requested.add(mapping.jira_field)
        return sorted(requested)

    def active_custom_field_ids(self) -> set[str]:
        return {
            mapping.jira_field
            for mapping in self.fields.values()
            if mapping.jira_field
            and mapping.jira_field.startswith("customfield_")
            and mapping.state in {"active", "read_only"}
        }

    def repository_candidates(self) -> tuple[str, ...]:
        """全部允许的业务仓库：list 优先，缺省回退 [default]。"""
        return self.repository_list or (
            (self.default_repository,) if self.default_repository else ()
        )

    def mounts_for_analysis(self) -> tuple[str, ...]:
        """按 analysis_mount 策略计算任务分析工作树集。

        - mode=include：仅 include 声明的仓库。
        - mode=exclude / mode=all：全量 candidates 减去 exclude。
          （mode=all + exclude 用于排除超大仓库，如 t-layer3-test 9.3G 按需挂载。）
        """
        candidates = self.repository_candidates()
        excluded = set(self.analysis_mount.exclude)
        if self.analysis_mount.mode == "include":
            include = set(self.analysis_mount.include)
            return tuple(repo for repo in candidates if repo in include)
        return tuple(repo for repo in candidates if repo not in excluded)

    def domain_for(self, repository: str) -> WorktreeDomain | None:
        matched = next((domain for domain in self.worktree_domains if repository in domain.repositories), None)
        if matched is not None or self.worktree_domains:
            return matched
        # TapData 必须显式配置领域，overlay 清空领域时也不能触发全量回退。
        if self.profile_id == "tapdata":
            return None
        # 兼容尚未声明领域的其它 Profile。
        primary = self.branch_derivation.derive_from
        if primary == "default":
            primary = self.default_repository or repository
        return WorktreeDomain(
            "default",
            primary,
            self.mounts_for_analysis(),
            primary,
        )

    def derive_branch(self, repo: str, from_branch: str, *, primary_repository: str | None = None) -> str | None:
        """分支推导：主仓库/overrides/dev_branches/same_name；返回目标分支，None 表示无法推导。

        优先级（确定性）：
        1. 主仓库（derive_from / default）→ from_branch。
        2. overrides（from_branch + repo 精确匹配）→ 规则声明的 branch。
        3. dev_branches：当 from_branch 等于主仓库声明的开发分支时，
           repo 命中 dev_branches → 声明的开发分支（如 tapdata-common-lib 用 main、
           feishu_robot 用 master、hazelcast 用 release-v5.5.0）。
        4. default_rule=same_name → from_branch。
        """
        derivation = self.branch_derivation
        primary = primary_repository or (
            derivation.derive_from
            if derivation.derive_from != "default"
            else self.default_repository
        )
        if repo == primary:
            return from_branch
        for rule in derivation.overrides:
            if rule.from_branch == from_branch and rule.repo == repo:
                return rule.branch
        dev_branches = dict(derivation.dev_branches)
        if primary in dev_branches and dev_branches[primary] == from_branch:
            declared = dev_branches.get(repo)
            if declared is not None:
                return declared
        if derivation.default_rule == "same_name":
            return from_branch
        return None

    def baseline_branch(self, repo: str) -> str | None:
        """返回目标仓库的显式任务基线；配置了映射时不得猜测默认分支。"""
        declared = dict(self.branch_derivation.baseline_branches)
        if declared:
            return declared.get(repo)
        return self.branch_derivation.default_branch


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value
