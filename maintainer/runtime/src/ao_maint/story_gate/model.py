from __future__ import annotations

from dataclasses import dataclass


STORY_CATEGORIES = frozenset({"maintainer", "developer"})
ACCEPTANCE_CHECKS = frozenset(
    {"python_runtime", "resource_contracts", "release_workflow", "story_registry"}
)


@dataclass(frozen=True)
class StoryContract:
    story_id: str
    category: str
    title: str
    document: str
    protected_paths: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    evidence_requirements: tuple[str, ...]


@dataclass(frozen=True)
class StoryRegistry:
    schema_version: int
    path: str
    stories: tuple[StoryContract, ...]
    digest: str


@dataclass(frozen=True)
class ChangeSet:
    source: str
    paths: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True)
class StoryImpact:
    impact_id: str
    change_source: str
    changed_paths: tuple[str, ...]
    impacted_story_ids: tuple[str, ...]
    impacted_categories: tuple[str, ...]
    revision_story_ids: tuple[str, ...]
    unmapped_paths: tuple[str, ...]
    acceptance_checks: tuple[str, ...]
    current_branch: str
    review_channel: str
    confirmation_stage: str
    target_branch: str
    commit_sha: str
    pr_number: int | None
    pr_url: str
    pr_head_sha: str
    pr_review_approved: bool

    @property
    def requires_revision_confirmation(self) -> bool:
        return bool(self.revision_story_ids)

    @property
    def has_impact(self) -> bool:
        return bool(self.impacted_story_ids or self.unmapped_paths)

    def as_dict(self) -> dict[str, object]:
        return {
            "impact_id": self.impact_id,
            "change_source": self.change_source,
            "changed_paths": list(self.changed_paths),
            "impacted_story_ids": list(self.impacted_story_ids),
            "impacted_categories": list(self.impacted_categories),
            "revision_story_ids": list(self.revision_story_ids),
            "unmapped_paths": list(self.unmapped_paths),
            "acceptance_checks": list(self.acceptance_checks),
            "requires_revision_confirmation": self.requires_revision_confirmation,
            "current_branch": self.current_branch,
            "review_channel": self.review_channel,
            "confirmation_stage": self.confirmation_stage,
            "target_branch": self.target_branch,
            "commit_sha": self.commit_sha,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "pr_head_sha": self.pr_head_sha,
            "pr_review_approved": self.pr_review_approved,
        }
