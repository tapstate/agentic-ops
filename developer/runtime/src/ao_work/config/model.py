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
    overrides: tuple[RepositoryBranchRule, ...] = ()


@dataclass(frozen=True)
class AnalysisMount:
    mode: str = "all"
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectProfile:
    profile_id: str
    connection_id: str
    project_key: str
    task_query: str
    issue_types: tuple[str, ...] = ()
    fields: dict[str, FieldMapping] = field(default_factory=dict)
    status_mapping: dict[str, str] = field(default_factory=dict)
    transition_mapping: dict[str, dict[str, str]] = field(default_factory=dict)
    default_repository: str | None = None
    workspace_source_root: str | None = None
    workspace_repository: str | None = None
    repository_list: tuple[str, ...] = ()
    analysis_mount: AnalysisMount = field(default_factory=AnalysisMount)
    branch_derivation: BranchDerivation = field(default_factory=BranchDerivation)

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
        """按 analysis_mount 策略计算任务分析工作树集。"""
        candidates = self.repository_candidates()
        if self.analysis_mount.mode == "include":
            include = tuple(self.analysis_mount.include)
            return tuple(repo for repo in candidates if repo in include)
        if self.analysis_mount.mode == "exclude":
            excluded = set(self.analysis_mount.exclude)
            return tuple(repo for repo in candidates if repo not in excluded)
        return candidates

    def derive_branch(self, repo: str, from_branch: str) -> str | None:
        """分支推导：主仓库/同名默认/overrides；返回目标分支，None 表示无法推导。

        调用方负责校验 from_branch 非空与仓库合法性；这里只做确定性映射。
        """
        derivation = self.branch_derivation
        if repo == (derivation.derive_from if derivation.derive_from != "default" else self.default_repository):
            return from_branch
        for rule in derivation.overrides:
            if rule.from_branch == from_branch and rule.repo == repo:
                return rule.branch
        if derivation.default_rule == "same_name":
            return from_branch
        return None


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value
