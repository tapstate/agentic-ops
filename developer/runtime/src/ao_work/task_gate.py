from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_state import TaskStore
from ao_work.task_state.io import atomic_write_json, read_json
from ao_work.workspace import Workspace, validate_business_source_root
from ao_work.workspace_security import (
    read_workspace_outbound_file,
    validate_workspace_managed_path,
)


SCHEMA_VERSION = 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CHINESE_PATTERN = re.compile(r"[\u3400-\u9fff]")
INPUT_CONTRACT_RECOVERY = {
    "executor": "ai",
    "action": "rebuild_contract_input_and_retry_once",
    "user_input_required": False,
    "rule": "由 AI 按当前任务准入或方案门禁合同重建输入；不要向用户索要内部 JSON 字段。",
}
ALLOWED_EVIDENCE_SOURCES = frozenset(
    {"jira_issue", "project_profile", "business_source_code", "runtime_readback"}
)
RISK_FLAGS = (
    "user_choice_required",
    "external_side_effect",
    "nontrivial_risk",
    "architecture_change",
    "public_contract_change",
    "security_boundary_change",
    "data_migration",
    "confirmed_design_change",
    "fact_conflict",
    "permission_gap",
    "capability_gap",
)
L4_FLAGS = frozenset({"fact_conflict", "permission_gap", "capability_gap"})
L3_FLAGS = frozenset(
    {
        "architecture_change",
        "public_contract_change",
        "security_boundary_change",
        "data_migration",
        "confirmed_design_change",
    }
)
L2_FLAGS = frozenset(
    {"user_choice_required", "external_side_effect", "nontrivial_risk"}
)


def record_task_start_context(
    workspace: Workspace,
    store: TaskStore,
    *,
    issue_key: str,
    agentic_run_id: str,
    issue: Mapping[str, Any],
    workspace_defaults: Mapping[str, Any],
    project_profile: Mapping[str, Any],
) -> dict[str, Any]:
    service = TaskGateService(workspace, store)
    return service.record_source_context(
        issue_key=issue_key,
        agentic_run_id=agentic_run_id,
        issue=issue,
        workspace_defaults=workspace_defaults,
        project_profile=project_profile,
    )


def execute_task_gate(
    args: Any,
    workspace: Workspace,
    store: TaskStore,
) -> dict[str, Any]:
    service = TaskGateService(workspace, store)
    if args.command == "intake" and args.action == "assess":
        return service.assess_intake(
            issue_key=args.issue_key,
            agentic_run_id=args.agentic_run_id,
            input_file=args.input_file,
        )
    if args.command == "solution" and args.action == "classify":
        return service.classify_solution(
            issue_key=args.issue_key,
            agentic_run_id=args.agentic_run_id,
            input_file=args.input_file,
        )
    raise _blocked(
        "task_gate_operation_invalid",
        "任务准入或方案门禁操作无效",
        "请按 ao-work task intake|solution 的命令帮助重试",
    )


class TaskGateService:
    def __init__(self, workspace: Workspace, store: TaskStore) -> None:
        self.workspace = workspace
        self.store = store
        self.root = workspace.root.expanduser().resolve()

    def record_source_context(
        self,
        *,
        issue_key: str,
        agentic_run_id: str,
        issue: Mapping[str, Any],
        workspace_defaults: Mapping[str, Any],
        project_profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._require_task(issue_key, agentic_run_id)
        stable = {
            "schema_version": SCHEMA_VERSION,
            "issue_key": issue_key,
            "agentic_run_id": agentic_run_id,
            "issue": _json_value(issue, "issue"),
            "workspace_defaults": _json_value(
                workspace_defaults, "workspace_defaults"
            ),
            "project_profile": _json_value(project_profile, "project_profile"),
            "runtime_readback": {
                "agentic_run_id": agentic_run_id,
                "issue_content_sha256": issue.get("issue_content_sha256"),
                "workspace_defaults": _json_value(
                    workspace_defaults, "workspace_defaults"
                ),
            },
        }
        context_digest = _digest(stable)
        payload = {
            **stable,
            "context_digest": context_digest,
            "observed_at": _timestamp(),
        }
        path = self._gate_path(issue_key, agentic_run_id, "source-context.json")
        self._write(path, payload)
        return {
            "context_digest": context_digest,
            "source_context_path": str(path),
        }

    def assess_intake(
        self,
        *,
        issue_key: str,
        agentic_run_id: str,
        input_file: str,
    ) -> dict[str, Any]:
        self._require_task(issue_key, agentic_run_id)
        source = self._source_context(issue_key, agentic_run_id)
        source_revision = self._source_revision(source)
        source_revision_digest = _digest(source_revision)
        raw = _load_json_input(self.root, input_file, "任务准入输入")
        normalized = self._normalize_intake_input(raw, source)

        supplied_fields = {
            str(item["field"]): item for item in normalized["auto_filled_values"]
        }
        generated, missing_required = self._profile_required_values(
            source, supplied_fields
        )
        auto_filled = [*generated, *normalized["auto_filled_values"]]
        unresolved = list(normalized["unresolved_information"])
        unresolved_fields = {str(item["field"]) for item in unresolved}
        for item in missing_required:
            if item["field"] not in unresolved_fields:
                unresolved.append(item)

        required_missing = sorted(
            str(item["field"])
            for item in unresolved
            if item["required"] is True
        )
        stable = {
            "schema_version": SCHEMA_VERSION,
            "issue_key": issue_key,
            "agentic_run_id": agentic_run_id,
            "source_context_digest": source["context_digest"],
            "source_revision": source_revision,
            "known_facts": self._known_facts(source),
            "auto_filled_values": sorted(
                auto_filled, key=lambda item: (str(item["field"]), str(item["source"]))
            ),
            "unresolved_information": sorted(
                unresolved, key=lambda item: str(item["field"])
            ),
            "assumptions": normalized["assumptions"],
            "impacts": normalized["impacts"],
        }
        intake_digest = _digest(stable)
        ready = not required_missing
        path = self._gate_path(issue_key, agentic_run_id, "intake.json")
        previous = self._optional_read(path)
        retry_count = self._intake_retry_count(
            previous,
            intake_digest=intake_digest,
            source_context_digest=str(source["context_digest"]),
            source_revision_digest=source_revision_digest,
            ready=ready,
        )
        payload = {
            **stable,
            "intake_digest": intake_digest,
            "ready_for_solution": ready,
            "required_missing_fields": required_missing,
            "retry_count": retry_count,
            "assessed_at": _timestamp(),
        }
        self._write(path, payload)

        if ready:
            next_action = _next_action(
                executor="ai",
                action="prepare_and_classify_solution",
                required_inputs=["intake_digest", "solution_input_file"],
                allowed_operations=["task_solution_classify"],
                requires_authorization=False,
                reason="准入事实完整，继续形成方案并分流到设计审查或风险决策",
            )
            progress_action = "prepare_and_classify_solution"
        elif retry_count == 0:
            next_action = _next_action(
                executor="ai",
                action="resolve_missing_task_information_and_retry_once",
                required_inputs=["required_missing_fields", "unresolved_information"],
                allowed_operations=["task_intake_assess"],
                requires_authorization=False,
                reason="请先回读已知事实，用新增证据修正输入后只重试一次",
                retry=True,
                retry_key=_digest(
                    {
                        "operation": "task_intake_assess",
                        "issue_key": issue_key,
                        "agentic_run_id": agentic_run_id,
                        "source_context_digest": source["context_digest"],
                        "source_revision_digest": source_revision_digest,
                    }
                ),
            )
            progress_action = "resolve_missing_task_information"
        else:
            next_action = _next_action(
                executor="human",
                action="resolve_intake_retry_exhausted",
                required_inputs=["required_missing_fields", "unresolved_information"],
                allowed_operations=[],
                requires_authorization=True,
                stop_workflow=True,
                reason="同一事实基线的准入补全重试已耗尽，请人工补充 Jira 或项目事实",
            )
            progress_action = "resolve_intake_retry_exhausted"

        self.store.record_gate_transition(
            issue_key,
            agentic_run_id,
            stage="task_intake",
            next_action=progress_action,
            operation="task_intake_assess",
            status="completed" if ready else "blocked",
            evidence={
                "intake_digest": intake_digest,
                "source_context_digest": str(source["context_digest"]),
                "head_sha": self._revision_head(source_revision),
                "source_revision_digest": source_revision_digest,
                "retry_count": retry_count,
            },
        )
        return {
            "intake": payload,
            "intake_digest": intake_digest,
            "intake_path": str(path),
            "ready_for_solution": ready,
            "required_missing_fields": required_missing,
            "next_step": next_action,
        }

    def classify_solution(
        self,
        *,
        issue_key: str,
        agentic_run_id: str,
        input_file: str,
    ) -> dict[str, Any]:
        intake = self._current_intake(issue_key, agentic_run_id)
        self._verify_current_source(intake, issue_key, agentic_run_id)
        raw = _load_json_input(self.root, input_file, "方案分级输入")
        normalized = self._normalize_solution_input(raw)
        if normalized["intake_digest"] != intake["intake_digest"]:
            raise _blocked(
                "solution_intake_digest_mismatch",
                "方案未绑定当前准入事实摘要",
                "请使用当前 intake_digest 重新形成方案",
            )
        level = self._solution_level(normalized["risk_flags"])
        execution = normalized.get("execution_plan")
        repositories = (
            self._execution_repositories(execution)
            if isinstance(execution, dict)
            else []
        )
        repository_heads = {
            repository: self._revision_head(
                intake["source_revision"], repository=repository
            )
            for repository in repositories
        }
        solution_head_sha = (
            repository_heads[repositories[0]]
            if repositories
            else self._revision_head(intake["source_revision"])
        )
        stable = {
            "schema_version": SCHEMA_VERSION,
            "issue_key": issue_key,
            "agentic_run_id": agentic_run_id,
            "intake_digest": intake["intake_digest"],
            "source_context_digest": intake["source_context_digest"],
            "head_sha": solution_head_sha,
            **normalized,
            "solution_level": level,
        }
        if repository_heads:
            stable["repository_heads"] = repository_heads
        solution_digest = _digest(stable)
        payload = {
            **stable,
            "solution_digest": solution_digest,
            "classified_at": _timestamp(),
        }
        path = self._gate_path(issue_key, agentic_run_id, "solution.json")
        self._write(path, payload)
        next_action = self._solution_next_action(
            level,
            solution_digest,
            has_execution_plan="execution_plan" in normalized,
        )
        self.store.record_gate_transition(
            issue_key,
            agentic_run_id,
            stage="solution_classification",
            next_action=str(next_action["action"]),
            operation="task_solution_classify",
            status="completed" if level in {"L1", "L2", "L3"} else "blocked",
            evidence={
                "intake_digest": str(intake["intake_digest"]),
                "solution_digest": solution_digest,
                "solution_level": level,
                "head_sha": solution_head_sha,
            },
        )
        return {
            "solution": payload,
            "solution_digest": solution_digest,
            "solution_level": level,
            "solution_path": str(path),
            "next_step": next_action,
        }

    def _normalize_intake_input(
        self, payload: dict[str, Any], source: Mapping[str, Any]
    ) -> dict[str, Any]:
        expected = {
            "schema_version",
            "auto_filled_values",
            "unresolved_information",
            "assumptions",
            "impacts",
        }
        _require_exact_keys(payload, expected, "任务准入输入")
        if payload["schema_version"] != SCHEMA_VERSION:
            raise _invalid("intake_schema_version", "schema_version 必须为 1")
        auto_filled = self._normalize_auto_filled(
            payload["auto_filled_values"], source
        )
        unresolved = _normalize_records(
            payload["unresolved_information"],
            required_keys={"field", "required", "reason"},
            label="unresolved_information",
            boolean_keys={"required"},
            plain_text_keys={"field"},
        )
        assumptions = _normalize_records(
            payload["assumptions"],
            required_keys={"statement", "impact"},
            label="assumptions",
        )
        impacts = _normalize_records(
            payload["impacts"],
            required_keys={"area", "description", "risk"},
            label="impacts",
            plain_text_keys={"risk"},
            enums={"risk": {"low", "medium", "high"}},
        )
        _unique_field(auto_filled, "field", "auto_filled_values")
        _unique_field(unresolved, "field", "unresolved_information")
        overlap = sorted(
            {str(item["field"]) for item in auto_filled}
            & {str(item["field"]) for item in unresolved}
        )
        if overlap:
            raise _invalid(
                "intake_field_conflict",
                f"同一字段不能同时已补全和未解决：{', '.join(overlap)}",
            )
        return {
            "auto_filled_values": auto_filled,
            "unresolved_information": unresolved,
            "assumptions": assumptions,
            "impacts": impacts,
        }

    def _normalize_auto_filled(
        self, value: Any, source: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        if not isinstance(value, list) or len(value) > 128:
            raise _invalid(
                "intake_auto_fill_invalid",
                "auto_filled_values 必须是最多 128 项的数组",
            )
        normalized: list[dict[str, Any]] = []
        for raw in value:
            if not isinstance(raw, dict):
                raise _invalid(
                    "intake_auto_fill_invalid", "每个自动补全项必须是对象"
                )
            expected = {"field", "value", "source", "reference", "rationale"}
            optional = {"evidence_sha256"}
            if not set(raw) <= expected | optional or not expected <= set(raw):
                raise _invalid(
                    "intake_auto_fill_invalid", "自动补全项字段不完整或包含未知字段"
                )
            field = _text(raw["field"], "auto_fill.field")
            source_name = _text(raw["source"], "auto_fill.source")
            if source_name not in ALLOWED_EVIDENCE_SOURCES:
                raise _invalid(
                    "intake_evidence_source_invalid",
                    f"不支持的自动补全来源：{source_name}",
                )
            reference = _text(raw["reference"], "auto_fill.reference")
            rationale = _chinese_text(raw["rationale"], "auto_fill.rationale")
            item_value = _json_value(raw["value"], "auto_fill.value")
            evidence_sha256: str
            if source_name == "business_source_code":
                evidence_sha256 = _text(
                    raw.get("evidence_sha256"), "auto_fill.evidence_sha256"
                )
                self._require_digest("evidence_sha256", evidence_sha256)
                source_root, source_reference = self._source_evidence_target(
                    source, reference
                )
                content = read_workspace_outbound_file(
                    source_root, source_reference, label="业务源码证据文件"
                )
                actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if actual != evidence_sha256:
                    raise _blocked(
                        "intake_source_evidence_changed",
                        f"业务源码证据摘要不一致：{reference}",
                        "请重新读取源码证据并更新准入输入，不能沿用旧摘要",
                    )
            else:
                if "evidence_sha256" in raw:
                    raise _invalid(
                        "intake_auto_fill_invalid",
                        "Jira、Profile 和 Runtime 来源的摘要由 Runtime 计算，不能手工提供",
                    )
                verified = _resolve_reference(
                    source, source_name, reference
                )
                if verified != item_value:
                    raise _blocked(
                        "intake_verified_value_mismatch",
                        f"自动补全值与受信来源不一致：{field}",
                        "请使用 Runtime 回读的准确值，或改用业务源码证据并接受人工语义审查",
                    )
                evidence_sha256 = _digest({"value": verified})
            normalized.append(
                {
                    "field": field,
                    "value": item_value,
                    "source": source_name,
                    "reference": reference,
                    "evidence_sha256": evidence_sha256,
                    "rationale": rationale,
                    "semantic_validation": (
                        "human_review_required"
                        if source_name == "business_source_code"
                        else "runtime_exact_match"
                    ),
                }
            )
        return normalized

    def _normalize_solution_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        expected = {
            "schema_version",
            "intake_digest",
            "proposed_solution",
            "scope",
            "risk_flags",
            "classification_evidence",
            "residual_risks",
        }
        optional = {"execution_plan"}
        if not expected <= set(payload) or not set(payload) <= expected | optional:
            raise _invalid(
                "solution_input_invalid",
                "方案分级输入字段不完整或包含未知字段",
            )
        if payload["schema_version"] != SCHEMA_VERSION:
            raise _invalid("solution_schema_version", "schema_version 必须为 1")
        intake_digest = _text(payload["intake_digest"], "intake_digest")
        self._require_digest("intake_digest", intake_digest)
        proposed_solution = _chinese_text(
            payload["proposed_solution"], "proposed_solution", maximum=32768
        )
        scope = payload["scope"]
        if not isinstance(scope, dict):
            raise _invalid("solution_scope_invalid", "scope 必须是对象")
        _require_exact_keys(scope, {"included", "excluded"}, "scope")
        included = _string_list(
            scope["included"],
            "scope.included",
            nonempty=True,
            require_chinese=False,
        )
        excluded = _string_list(
            scope["excluded"],
            "scope.excluded",
            require_chinese=False,
        )
        overlap = sorted(set(included) & set(excluded))
        if overlap:
            raise _invalid(
                "solution_scope_conflict",
                f"方案范围与非范围重叠：{', '.join(overlap)}",
            )
        flags = payload["risk_flags"]
        if not isinstance(flags, dict) or set(flags) != set(RISK_FLAGS):
            raise _invalid(
                "solution_risk_flags_invalid",
                "risk_flags 必须显式且仅包含全部固定风险标志",
            )
        if not all(isinstance(flags[name], bool) for name in RISK_FLAGS):
            raise _invalid(
                "solution_risk_flags_invalid", "每个风险标志必须是 boolean"
            )
        evidence = payload["classification_evidence"]
        if not isinstance(evidence, dict) or not set(evidence) <= set(RISK_FLAGS):
            raise _invalid(
                "solution_classification_evidence_invalid",
                "classification_evidence 包含未知风险标志",
            )
        normalized_evidence: dict[str, list[str]] = {}
        for name, value in evidence.items():
            normalized_evidence[str(name)] = _string_list(
                value, f"classification_evidence.{name}", nonempty=True
            )
        missing = sorted(
            name for name in RISK_FLAGS if flags[name] and name not in normalized_evidence
        )
        if missing:
            raise _invalid(
                "solution_classification_evidence_missing",
                f"启用的风险标志缺少证据：{', '.join(missing)}",
            )
        residual_risks = _string_list(
            payload["residual_risks"], "residual_risks"
        )
        normalized = {
            "intake_digest": intake_digest,
            "proposed_solution": proposed_solution,
            "scope": {"included": included, "excluded": excluded},
            "risk_flags": {name: bool(flags[name]) for name in RISK_FLAGS},
            "classification_evidence": normalized_evidence,
            "residual_risks": residual_risks,
        }
        if "execution_plan" in payload:
            execution = self._normalize_execution_plan(
                payload["execution_plan"]
            )
            self._validate_execution_scope(normalized["scope"], execution)
            normalized["execution_plan"] = execution
        return normalized

    def _normalize_execution_plan(self, value: Any) -> dict[str, Any]:
        # 延迟导入，避免 task_run 包入口与 task_start/task_gate 的模块初始化环。
        from ao_work.task_run.protocol import normalize_verification_command

        if not isinstance(value, dict):
            raise _invalid("solution_execution_plan_invalid", "execution_plan 必须是对象")
        legacy_keys = {"change_repository", "verification", "review_summary"}
        multi_keys = {"change_repositories", "verification", "review_summary"}
        if set(value) == legacy_keys:
            multi = False
            repositories = [
                _text(value["change_repository"], "execution_plan.change_repository")
            ]
        elif set(value) == multi_keys:
            multi = True
            repositories = _string_list(
                value["change_repositories"],
                "execution_plan.change_repositories",
                nonempty=True,
                require_chinese=False,
            )
            if len(repositories) > 32 or len(repositories) != len(set(repositories)):
                raise _invalid(
                    "solution_execution_plan_invalid",
                    "change_repositories 必须是最多 32 个且不重复的仓库数组",
                )
        else:
            raise _invalid(
                "solution_execution_plan_invalid",
                "execution_plan 必须使用单仓 change_repository 或多仓 change_repositories 合同",
            )
        for repository in repositories:
            if repository.count("/") != 1 or len(repository) > 256:
                raise _invalid(
                    "solution_execution_plan_invalid",
                    "变更仓库必须是 owner/repository",
                )
        review_summary = _chinese_text(
            value["review_summary"],
            "execution_plan.review_summary",
            maximum=16384,
        )
        raw_verification = value["verification"]
        if not isinstance(raw_verification, list) or not raw_verification:
            raise _invalid(
                "solution_execution_plan_invalid",
                "execution_plan.verification 必须是非空数组",
            )
        if len(raw_verification) > 32:
            raise _invalid(
                "solution_execution_plan_invalid",
                "execution_plan.verification 最多包含 32 项",
            )
        seen: set[str] = set()
        verification: list[dict[str, Any]] = []
        normalization_changes: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_verification):
            label = f"execution_plan.verification[{index}]"
            if not isinstance(raw, dict):
                raise _invalid("solution_execution_plan_invalid", f"{label} 必须是对象")
            expected = {"id", "command", "working_directory", "timeout_seconds"}
            if multi:
                expected.add("repository")
            _require_exact_keys(raw, expected, label)
            verification_repository = (
                _text(raw["repository"], f"{label}.repository")
                if multi
                else repositories[0]
            )
            if verification_repository not in repositories:
                raise _invalid(
                    "solution_execution_plan_invalid",
                    f"{label}.repository 不在 change_repositories 中",
                )
            verification_id = _text(raw["id"], f"{label}.id")
            if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}", verification_id):
                raise _invalid("solution_execution_plan_invalid", f"{label}.id 无效")
            if verification_id in seen:
                raise _invalid("solution_execution_plan_invalid", "验证 id 不能重复")
            seen.add(verification_id)
            working_directory = _text(
                raw["working_directory"], f"{label}.working_directory"
            )
            timeout = raw["timeout_seconds"]
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 3600:
                raise _invalid(
                    "solution_execution_plan_invalid",
                    f"{label}.timeout_seconds 必须是 1..3600 秒整数",
                )
            normalized = normalize_verification_command(
                raw["command"],
                working_directory,
                label=label,
            )
            verification_item = {
                "id": verification_id,
                "command": normalized["command"],
                "working_directory": working_directory,
                "timeout_seconds": timeout,
            }
            if multi:
                verification_item["repository"] = verification_repository
            verification.append(verification_item)
            if normalized["changes"]:
                change = {
                    "verification_id": verification_id,
                    "original_command": normalized["original_command"],
                    "normalized_command": normalized["command"],
                    "changes": normalized["changes"],
                }
                if multi:
                    change["repository"] = verification_repository
                normalization_changes.append(change)
        missing_verification = sorted(
            set(repositories)
            - {
                str(item.get("repository") or repositories[0])
                for item in verification
            }
        )
        if missing_verification:
            raise _invalid(
                "solution_execution_plan_invalid",
                "每个变更仓库必须至少声明一项验证："
                + ", ".join(missing_verification),
            )
        result = {
            "verification": verification,
            "review_summary": review_summary,
            "normalization_changes": normalization_changes,
        }
        result[
            "change_repositories" if multi else "change_repository"
        ] = repositories if multi else repositories[0]
        return result

    def _validate_execution_scope(
        self, scope: Mapping[str, Any], execution: Mapping[str, Any]
    ) -> None:
        repositories = self._execution_repositories(execution)
        if "change_repositories" not in execution:
            return
        selected = set(repositories)
        included_by_repository: set[str] = set()
        for field in ("included", "excluded"):
            for reference in scope[field]:
                repository, separator, relative = str(reference).partition("::")
                if not separator or repository not in selected or not relative:
                    raise _invalid(
                        "solution_scope_invalid",
                        "多仓方案 scope 必须使用 owner/repository::relative/path，且仓库属于 change_repositories",
                    )
                if field == "included":
                    included_by_repository.add(repository)
        missing = sorted(selected - included_by_repository)
        if missing:
            raise _invalid(
                "solution_scope_invalid",
                "每个变更仓库必须至少包含一条 scope.included：" + ", ".join(missing),
            )

    @staticmethod
    def _execution_repositories(execution: Mapping[str, Any]) -> list[str]:
        raw = execution.get("change_repositories")
        if isinstance(raw, list):
            return [str(item) for item in raw]
        repository = str(execution.get("change_repository") or "")
        return [repository] if repository else []

    def _profile_required_values(
        self,
        source: Mapping[str, Any],
        supplied_fields: Mapping[str, Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        profile = source.get("project_profile")
        if not isinstance(profile, dict):
            raise _invalid("task_source_context_invalid", "Project Profile 快照无效")
        resolved = profile.get("resolved_fields")
        if not isinstance(resolved, dict):
            raise _invalid("task_source_context_invalid", "Profile 字段解析快照无效")
        generated: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for field, raw in sorted(resolved.items()):
            if not isinstance(raw, dict) or raw.get("required") is not True:
                continue
            if field in supplied_fields:
                continue
            value = raw.get("value")
            if _has_value(value):
                reference = f"project_profile.resolved_fields.{field}.value"
                generated.append(
                    {
                        "field": field,
                        "value": _json_value(value, f"profile.{field}"),
                        "source": "project_profile",
                        "reference": reference,
                        "evidence_sha256": _digest({"value": value}),
                        "rationale": "Project Profile 已声明该必填字段及其确定性来源",
                        "semantic_validation": "runtime_exact_match",
                    }
                )
            else:
                missing.append(
                    {
                        "field": field,
                        "required": True,
                        "reason": "Project Profile 声明为必填，但 Jira、工作空间或源码证据尚未提供值",
                    }
                )
        return generated, missing

    def _known_facts(self, source: Mapping[str, Any]) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for source_name in ("jira_issue", "project_profile", "runtime_readback"):
            key = {
                "jira_issue": "issue",
                "project_profile": "project_profile",
                "runtime_readback": "runtime_readback",
            }[source_name]
            value = source.get(key)
            for reference, item in _flatten(value, key):
                facts.append(
                    {
                        "source": source_name,
                        "reference": reference,
                        "value": item,
                        "evidence_sha256": _digest({"value": item}),
                    }
                )
        return sorted(facts, key=lambda item: (str(item["source"]), str(item["reference"])))

    def _solution_level(self, flags: Mapping[str, bool]) -> str:
        enabled = {name for name, value in flags.items() if value}
        if enabled & L4_FLAGS:
            return "L4"
        if enabled & L3_FLAGS:
            return "L3"
        if enabled & L2_FLAGS:
            return "L2"
        return "L1"

    def _solution_next_action(
        self,
        level: str,
        solution_digest: str,
        *,
        has_execution_plan: bool,
    ) -> dict[str, Any]:
        if level == "L1":
            if has_execution_plan:
                return _next_action(
                    executor="ai",
                    action="prepare_task_run_manifest",
                    required_inputs=[
                        "solution_digest",
                        "proposed_solution",
                        "scope",
                        "execution_plan",
                        "residual_risks",
                    ],
                    allowed_operations=["task_run_prepare"],
                    requires_authorization=False,
                    reason=(
                        "事实、方案与执行计划已完整，由 Runtime 生成一次性设计和连续执行授权确认包"
                    ),
                )
            return _next_action(
                executor="human",
                action="review_task_design",
                required_inputs=["solution_digest", "proposed_solution", "scope", "residual_risks"],
                allowed_operations=[],
                requires_authorization=True,
                stop_workflow=True,
                reason="旧版方案缺少执行计划，保留设计审查但不能生成 task-run 执行包",
            )
        if level == "L2":
            return _next_action(
                executor="human",
                action="decide_solution_risk",
                required_inputs=["solution_digest", "full_solution", "classification_evidence"],
                allowed_operations=[],
                requires_authorization=True,
                stop_workflow=True,
                reason="方案包含用户选择、外部副作用或非平凡风险，必须逐项进行风险决策",
            )
        if level == "L3":
            return _next_action(
                executor="ai",
                action="revise_design_and_reassess",
                required_inputs=["proposed_solution", "classification_evidence"],
                allowed_operations=["task_intake_assess"],
                requires_authorization=False,
                reason="方案触及架构、公共合同或安全边界，先修订设计并重新评估，再进入设计审查",
            )
        return _next_action(
            executor="human",
            action="resolve_solution_blocker",
            required_inputs=["classification_evidence"],
            allowed_operations=[],
            requires_authorization=True,
            stop_workflow=True,
            reason="L4 存在事实冲突、权限或能力缺口，当前不能继续",
        )

    def _source_context(self, issue_key: str, agentic_run_id: str) -> dict[str, Any]:
        return self._required_read(
            self._gate_path(issue_key, agentic_run_id, "source-context.json"),
            "task_source_context_missing",
            "任务缺少 task start 的受信来源快照",
            "请重新执行 ao-work task start <ISSUE-KEY>",
        )

    def _current_intake(self, issue_key: str, agentic_run_id: str) -> dict[str, Any]:
        return self._required_read(
            self._gate_path(issue_key, agentic_run_id, "intake.json"),
            "task_intake_not_assessed",
            "任务尚未完成准入分析",
            "请先执行 task intake assess",
        )

    def _verify_current_source(
        self,
        intake: Mapping[str, Any],
        issue_key: str,
        agentic_run_id: str,
    ) -> None:
        source = self._source_context(issue_key, agentic_run_id)
        if source.get("context_digest") != intake.get("source_context_digest"):
            raise _blocked(
                "task_intake_source_changed",
                "Jira、Project Profile 或工作空间事实已变化",
                "请重新执行 task intake assess 并确认新摘要",
            )
        revision = self._source_revision(source)
        expected = intake.get("source_revision")
        if not isinstance(expected, dict) or revision != expected:
            raise _blocked(
                "task_intake_source_changed",
                "业务源码 HEAD 或工作树状态已变化",
                "请停止沿用旧确认，重新执行 task intake assess",
            )
        for item in intake.get("auto_filled_values", []):
            if not isinstance(item, dict) or item.get("source") != "business_source_code":
                continue
            content = read_workspace_outbound_file(
                *self._source_evidence_target(
                    source, str(item.get("reference", ""))
                ),
                label="业务源码证据文件",
            )
            actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if actual != item.get("evidence_sha256"):
                raise _blocked(
                    "task_intake_source_changed",
                    f"业务源码证据已变化：{item.get('reference')}",
                    "请重新分析准入信息并确认新摘要",
                )

    def _source_revision(self, source: Mapping[str, Any]) -> dict[str, Any]:
        roots = self._source_roots(source)
        if roots:
            defaults = source.get("workspace_defaults")
            assert isinstance(defaults, dict)
            scope_revision = defaults.get("repository_scope_revision")
            if (
                isinstance(scope_revision, bool)
                or not isinstance(scope_revision, int)
                or scope_revision < 1
            ):
                raise _invalid(
                    "task_source_context_invalid",
                    "多仓来源快照缺少有效 repository_scope_revision",
                )
            repositories: list[dict[str, str]] = []
            for repository, root, expected_head, expected_branch in roots:
                revision = self._git_source_revision(root)
                current_branch = _run_git(root, "symbolic-ref", "--short", "HEAD")
                if (
                    revision["head_sha"] != expected_head
                    or current_branch != expected_branch
                ):
                    raise _blocked(
                        "task_source_context_changed",
                        f"领域工作树已偏离建树来源快照：{repository}",
                        "请核对领域工作树并重新执行 worktrees prepare，不能沿用旧来源上下文",
                    )
                repositories.append({"repository": repository, **revision})
            return {
                "repository_scope_revision": scope_revision,
                "repositories": repositories,
            }
        return self._git_source_revision(self._source_root(source))

    def _git_source_revision(self, root: Path) -> dict[str, str]:
        top = _run_git(root, "rev-parse", "--show-toplevel")
        try:
            top_root = Path(top).expanduser().resolve()
        except OSError as error:
            raise _blocked(
                "task_source_repository_invalid",
                "无法确认业务源码 Git 根目录",
                "请修复业务源码仓库后重新执行准入分析",
            ) from error
        if top_root != root:
            raise _blocked(
                "task_source_repository_invalid",
                "业务源码目录不是精确 Git 根目录",
                "请重新初始化工作空间并绑定准确的 source_root",
            )
        head_sha = _run_git(root, "rev-parse", "--verify", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
            raise _blocked(
                "task_source_head_invalid",
                "业务源码 HEAD 不是有效提交",
                "请先建立有效 Git 基线，再执行准入分析",
            )
        status = _run_git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if status:
            raise _blocked(
                "task_source_not_clean",
                "准入分析前业务源码工作树必须干净",
                "请人工检查并提交、暂存到安全位置或移除既有修改后重试",
            )
        return {"repository_root": str(root), "head_sha": head_sha, "worktree": "clean"}

    def _source_roots(
        self, source: Mapping[str, Any]
    ) -> list[tuple[str, Path, str, str]]:
        defaults = source.get("workspace_defaults")
        if not isinstance(defaults, dict):
            raise _invalid("task_source_context_invalid", "工作空间默认快照无效")
        raw = defaults.get("source_roots")
        if raw is None or raw == [] or raw == ():
            return []
        if not isinstance(raw, list):
            raise _invalid("task_source_context_invalid", "领域源码根快照必须是数组")
        roots: list[tuple[str, Path, str, str]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                raise _invalid("task_source_context_invalid", "领域源码根条目无效")
            repository = str(item.get("repository") or "").strip()
            raw_root = str(item.get("source_root") or "").strip()
            expected_head = str(item.get("head_sha") or "").strip()
            expected_branch = str(item.get("task_branch") or "").strip()
            if (
                not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                or repository in seen
                or not raw_root
                or not re.fullmatch(r"[0-9a-f]{40}", expected_head)
                or not expected_branch
            ):
                raise _invalid("task_source_context_invalid", "领域源码根身份无效或重复")
            roots.append(
                (
                    repository,
                    validate_business_source_root(self.root, Path(raw_root)),
                    expected_head,
                    expected_branch,
                )
            )
            seen.add(repository)
        return roots

    def _source_evidence_target(
        self, source: Mapping[str, Any], reference: str
    ) -> tuple[Path, str]:
        roots = self._source_roots(source)
        if not roots:
            return self._source_root(source), reference
        repository, separator, relative = reference.partition("::")
        if not separator or not repository or not relative:
            raise _invalid(
                "intake_evidence_reference_invalid",
                "多仓源码证据必须使用 owner/repository::relative/path 引用",
            )
        root_by_repository = {
            repository: root for repository, root, _, _ in roots
        }
        root = root_by_repository.get(repository)
        if root is None:
            raise _invalid(
                "intake_evidence_reference_invalid",
                f"源码证据仓库不在确认领域：{repository}",
            )
        return root, relative

    def _source_root(self, source: Mapping[str, Any]) -> Path:
        defaults = source.get("workspace_defaults")
        if not isinstance(defaults, dict):
            raise _invalid("task_source_context_invalid", "工作空间默认快照无效")
        raw = defaults.get("source_root")
        if not isinstance(raw, str) or not raw.strip():
            raise _blocked(
                "task_source_root_missing",
                "任务来源快照缺少业务源码目录",
                "请确认工作空间初始化完成后重新执行 task start",
            )
        return validate_business_source_root(self.root, Path(raw))

    def _intake_retry_count(
        self,
        previous: Mapping[str, Any] | None,
        *,
        intake_digest: str,
        source_context_digest: str,
        source_revision_digest: str,
        ready: bool,
    ) -> int:
        if ready or not previous:
            return 0
        if previous.get("intake_digest") == intake_digest:
            return int(previous.get("retry_count", 0))
        same_cycle = (
            previous.get("source_context_digest") == source_context_digest
            and _digest(previous.get("source_revision")) == source_revision_digest
        )
        if not same_cycle or previous.get("ready_for_solution") is True:
            return 0
        count = int(previous.get("retry_count", 0)) + 1
        if count > 1:
            raise _blocked(
                "task_intake_retry_exhausted",
                "同一事实基线的准入补全重试已耗尽",
                "请人工补充 Jira 或项目事实；事实基线变化后再启动新的准入分析",
            )
        return count

    def _revision_head(
        self, revision: Mapping[str, Any], *, repository: str = ""
    ) -> str:
        repositories = revision.get("repositories")
        if isinstance(repositories, list):
            if not repository:
                first = next(
                    (item for item in repositories if isinstance(item, dict)), None
                )
                return str(first.get("head_sha") or "") if first else ""
            candidates = [
                item
                for item in repositories
                if isinstance(item, dict)
                and item.get("repository") == repository
            ]
            if len(candidates) != 1:
                raise _blocked(
                    "solution_change_repository_outside_source_context",
                    f"方案变更仓库不在确认领域来源中：{repository}",
                    "请从确认领域工作树中选择变更仓库并重新分级",
                )
            return str(candidates[0].get("head_sha") or "")
        return str(revision.get("head_sha") or "")

    def _require_task(self, issue_key: str, agentic_run_id: str) -> dict[str, Any]:
        state = self.store.inspect(issue_key)
        task = state["task"]
        if task.get("agentic_run_id") != agentic_run_id:
            raise _blocked(
                "task_identity_mismatch",
                "任务门禁运行编号与当前任务绑定不一致",
                "请使用 task start 返回的当前 agentic_run_id",
            )
        return task

    def _gate_path(self, issue_key: str, agentic_run_id: str, name: str) -> Path:
        task = self._require_task(issue_key, agentic_run_id)
        task_dir = Path(str(self.store.inspect(issue_key)["task_dir"]))
        if task.get("agentic_run_id") != agentic_run_id:
            raise _blocked(
                "task_identity_mismatch",
                "任务门禁路径与当前运行不一致",
                "请使用当前任务运行编号",
            )
        path = task_dir / "runs" / agentic_run_id / "gates" / name
        validate_workspace_managed_path(self.root, path)
        return path

    def _write(self, path: Path, payload: Mapping[str, Any]) -> None:
        validate_workspace_managed_path(self.root, path)
        parent = path.parent
        validate_workspace_managed_path(self.root, parent)
        parent.mkdir(parents=True, exist_ok=True)
        validate_workspace_managed_path(self.root, parent)
        atomic_write_json(path, payload)

    @staticmethod
    def _optional_read(path: Path) -> dict[str, Any] | None:
        if not path.exists() and not path.is_symlink():
            return None
        return read_json(path)

    def _required_read(
        self, path: Path, code: str, message: str, action: str
    ) -> dict[str, Any]:
        if not path.exists() and not path.is_symlink():
            raise _blocked(code, message, action)
        return read_json(path)

    @staticmethod
    def _require_digest(field: str, value: str) -> None:
        if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
            raise _invalid(field, f"{field} 必须是小写 SHA-256")

def _next_action(
    *,
    executor: str,
    action: str,
    required_inputs: list[str],
    allowed_operations: list[str],
    requires_authorization: bool,
    reason: str,
    stop_workflow: bool = False,
    retry: bool = False,
    retry_key: str | None = None,
) -> dict[str, Any]:
    retry_gate: dict[str, Any] = {
        "allowed": retry,
        "max_additional_attempts": 1 if retry else 0,
        "same_input_allowed": False,
        "requires_state_readback": retry,
        "requires_recorded_retry_event": retry,
        "on_exhausted": "escalate_to_human" if retry else "not_applicable",
    }
    if retry_key is not None:
        retry_gate["retry_key"] = retry_key
    return {
        "executor": executor,
        "action": action,
        "required_inputs": required_inputs,
        "allowed_operations": allowed_operations,
        "requires_authorization": requires_authorization,
        "stop_workflow": stop_workflow,
        "ownership_effect": "none",
        "reason": reason,
        "retry_gate": retry_gate,
    }


def _load_json_input(workspace_root: Path, value: str, label: str) -> dict[str, Any]:
    content = read_workspace_outbound_file(workspace_root, value, label=label)

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = item
        return result

    try:
        payload = json.loads(
            content,
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite number: {value}")
            ),
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise _invalid("task_gate_input_invalid", f"{label}不是严格 JSON：{error}") from error
    if not isinstance(payload, dict):
        raise _invalid("task_gate_input_invalid", f"{label}顶层必须是对象")
    return payload


def _resolve_reference(source: Mapping[str, Any], source_name: str, reference: str) -> Any:
    prefix = {
        "jira_issue": "issue",
        "project_profile": "project_profile",
        "runtime_readback": "runtime_readback",
    }[source_name]
    parts = reference.split(".")
    if not parts or parts[0] != prefix or any(not part for part in parts):
        raise _invalid(
            "intake_evidence_reference_invalid",
            f"{source_name} 引用必须从 {prefix}. 开始",
        )
    current: Any = source
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise _invalid(
                "intake_evidence_reference_invalid",
                f"受信来源中不存在引用：{reference}",
            )
        current = current[part]
    return _json_value(current, reference)


def _flatten(value: Any, prefix: str) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        result: list[tuple[str, Any]] = []
        for key in sorted(value):
            result.extend(_flatten(value[key], f"{prefix}.{key}"))
        return result
    if isinstance(value, list):
        return [(prefix, _json_value(value, prefix))]
    return [(prefix, _json_value(value, prefix))]


def _normalize_records(
    value: Any,
    *,
    required_keys: set[str],
    label: str,
    boolean_keys: set[str] = frozenset(),
    plain_text_keys: set[str] = frozenset(),
    enums: Mapping[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 128:
        raise _invalid(f"{label}_invalid", f"{label} 必须是最多 128 项的数组")
    normalized: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != required_keys:
            raise _invalid(
                f"{label}_invalid", f"{label} 每项必须精确包含固定字段"
            )
        item: dict[str, Any] = {}
        for key in sorted(required_keys):
            if key in boolean_keys:
                if not isinstance(raw[key], bool):
                    raise _invalid(f"{label}_invalid", f"{label}.{key} 必须是 boolean")
                item[key] = raw[key]
            elif key in plain_text_keys:
                item[key] = _text(raw[key], f"{label}.{key}")
            else:
                item[key] = _chinese_text(raw[key], f"{label}.{key}")
            if enums and key in enums and item[key] not in enums[key]:
                raise _invalid(
                    f"{label}_invalid", f"{label}.{key} 不在允许枚举中"
                )
        normalized.append(item)
    return normalized


def _string_list(
    value: Any,
    label: str,
    *,
    nonempty: bool = False,
    require_chinese: bool = True,
) -> list[str]:
    if not isinstance(value, list) or len(value) > 128:
        raise _invalid(f"{label}_invalid", f"{label} 必须是最多 128 项的数组")
    normalizer = _chinese_text if require_chinese else _text
    normalized = [normalizer(item, label) for item in value]
    if nonempty and not normalized:
        raise _invalid(f"{label}_invalid", f"{label} 不能为空")
    if len(normalized) != len(set(normalized)):
        raise _invalid(f"{label}_invalid", f"{label} 不能包含重复项")
    return normalized


def _unique_field(records: list[dict[str, Any]], key: str, label: str) -> None:
    values = [str(item[key]) for item in records]
    if len(values) != len(set(values)):
        raise _invalid(f"{label}_duplicate", f"{label} 不能包含重复 {key}")


def _require_exact_keys(payload: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(payload) != expected:
        missing = sorted(expected - set(payload))
        unknown = sorted(set(payload) - expected)
        raise _invalid(
            "task_gate_input_fields_invalid",
            f"{label}字段不匹配；缺少={missing}，未知={unknown}",
        )


def _json_value(value: Any, label: str) -> Any:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise _invalid("task_gate_value_invalid", f"{label} 不是安全 JSON 值") from error
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise _invalid("task_gate_value_invalid", f"{label} 超过 256 KiB")
    return json.loads(encoded)


def _text(value: Any, label: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise _invalid("task_gate_text_invalid", f"{label} 必须是非空安全文本")
    return value.strip()


def _chinese_text(value: Any, label: str, *, maximum: int = 4096) -> str:
    text = _text(value, label, maximum=maximum)
    if not CHINESE_PATTERN.search(text):
        raise _invalid("task_gate_chinese_required", f"{label} 必须包含中文说明")
    return text


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
    )
    if completed.returncode != 0:
        raise _blocked(
            "task_source_git_read_failed",
            f"无法读取业务源码 Git 状态：git {' '.join(arguments)}",
            "请修复业务源码仓库后重新执行准入分析",
        )
    return completed.stdout.strip()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _invalid(code: str, message: str) -> RuntimeErrorResult:
    input_contract_error = code != "task_source_context_invalid"
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=input_contract_error,
        required_human_action=(
            "请由 AI 按任务准入或方案门禁合同重建输入并重试一次；"
            "不要向用户索要内部 JSON 字段"
            if input_contract_error
            else "任务来源快照无效；请停止并由人工修复 Runtime、工作空间或 Profile 状态"
        ),
        details={"input_recovery": INPUT_CONTRACT_RECOVERY}
        if input_contract_error
        else {},
    )


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=False,
        required_human_action=action,
    )
