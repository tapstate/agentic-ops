"""Jira 原始状态到 AgenticOps 标准阶段的唯一解析入口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class StatusResolution:
    stage: str
    source: str
    status_id: str
    status_name: str

    def to_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "source": self.source,
            "status_id": self.status_id,
            "status_name": self.status_name,
        }


def resolve_status(
    status_id: str,
    status_name: str,
    *,
    status_id_mapping: Mapping[str, str] | None,
    status_mapping: Mapping[str, str],
) -> StatusResolution | None:
    """先精确匹配 Jira status ID，再兼容显式声明的显示名别名。

    不做大小写、翻译或模糊匹配：名称兼容只接受 Profile 中已明确登记的
    别名，避免把工作流语义的变化静默当成同一阶段。
    """
    normalized_id = str(status_id or "").strip()
    normalized_name = str(status_name or "").strip()
    ids = status_id_mapping or {}
    if normalized_id and normalized_id in ids:
        return StatusResolution(
            stage=str(ids[normalized_id]),
            source="status_id",
            status_id=normalized_id,
            status_name=normalized_name,
        )
    if normalized_name and normalized_name in status_mapping:
        return StatusResolution(
            stage=str(status_mapping[normalized_name]),
            source="status_name_alias",
            status_id=normalized_id,
            status_name=normalized_name,
        )
    return None


def resolve_issue_status(profile: Any, issue: Any) -> StatusResolution | None:
    return resolve_status(
        str(getattr(issue, "status_id", "") or ""),
        str(getattr(issue, "status", "") or ""),
        status_id_mapping=getattr(profile, "status_id_mapping", {}),
        status_mapping=getattr(profile, "status_mapping", {}),
    )
