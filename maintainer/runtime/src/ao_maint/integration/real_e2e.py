from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

import yaml

from ao_maint.integration.model import ISSUE_KEY_PATTERN
from ao_maint.integration.task_to_pr import AGENT_ID_PATTERN, PROFILE_PATTERN
from ao_maint.io import atomic_write_json
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult

REQUIRED_CAPABILITIES = (
    "workspace_init",
    "workspace_preflight",
    "jira_authorization",
    "task_start",
    "task_intake_assess",
    "solution_gate",
    "takeover_task",
    "task_run_audit",
    "jira_comment",
    "jira_worklog",
    "git_commit",
    "git_push_task_branch",
    "github_pr_create",
)

CONFIG_SCHEMA_VERSION = 1
CONFIG_FILE_NAME = "task-to-pr-e2e.config.json"


class RealTaskToPrE2EPreflight:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root.resolve()

    def run(
        self,
        issue_key: str,
    ) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        config_path = self.config_path()
        config = self._load_config(config_path)
        capabilities = self._load_capabilities()
        required = []
        gaps = []
        for capability_id in REQUIRED_CAPABILITIES:
            capability = capabilities.get(capability_id)
            if capability is None:
                entry = {
                    "id": capability_id,
                    "status": "missing",
                    "next_action": "请先把该原子步骤登记到 developer 能力目录并实现固定合同",
                }
                gaps.append(entry)
            else:
                entry = {
                    "id": capability_id,
                    "status": capability["status"],
                    "next_action": capability["next_action"],
                }
                if capability["status"] != "implemented":
                    gaps.append(entry)
            required.append(entry)

        ready = not gaps
        return {
            "issue_key": issue_key,
            "ready": ready,
            "test_identity": {
                "agent_id": config["agent_id"],
                "expected_confirmer": config["expected_confirmer"],
                "project_profile": config["project_profile"],
                "source": "task_to_pr_e2e_config",
                "config_path": str(config_path),
                "config_sha256": hashlib.sha256(
                    config_path.read_bytes()
                ).hexdigest(),
            },
            "required_capabilities": required,
            "blocking_capabilities": gaps,
            "external_access_performed": False,
            "business_workspace_created": False,
            "credentials_read": False,
            "real_side_effects": [
                "创建隔离业务工作空间并克隆业务源码",
                "真实读取并受控写入指定 Jira",
                "修改、提交并推送业务任务分支",
                "创建真实 GitHub PR 并停在审查",
                "写入中文 Jira Comment 与真实 Worklog",
            ],
            "forbidden_side_effects": [
                "merge",
                "jira_done",
                "release",
                "tag",
                "protected_branch_push",
                "force_push",
                "history_rewrite",
            ],
            "next_action": (
                {
                    "executor": "human",
                    "action": "implement_required_ao_work_capabilities",
                    "required_inputs": ["blocking_capabilities"],
                    "allowed_operations": [],
                    "requires_authorization": False,
                    "stop_workflow": True,
                    "ownership_effect": "none",
                }
                if gaps
                else {
                    "executor": "human",
                    "action": "confirm_real_task_to_pr_side_effects",
                    "required_inputs": [
                        "issue_key",
                        "test_identity",
                        "real_side_effects",
                        "forbidden_side_effects",
                    ],
                    "allowed_operations": ["test-task-to-pr-e2e"],
                    "requires_authorization": True,
                    "stop_workflow": False,
                    "ownership_effect": "none",
                }
            ),
        }

    def prepare_config(
        self,
        *,
        agent_id: str,
        project_profile: str,
        expected_confirmer: str,
    ) -> dict[str, Any]:
        self._validate_config_values(
            agent_id,
            project_profile,
            expected_confirmer,
        )
        path = self.config_path()
        if path.exists() or path.is_symlink():
            raise RuntimeErrorResult(
                code="e2e_configuration_exists",
                message="真实全链路配置已存在",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请先审查现有配置；需要变更时使用独立的配置修改流程",
                details={"config_path": str(path)},
            )
        payload = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "agent_id": agent_id,
            "project_profile": project_profile,
            "expected_confirmer": expected_confirmer,
        }
        atomic_write_json(path, payload)
        return {
            "config_path": str(path),
            "configuration_status": "created",
            "test_identity": payload,
            "credentials_written": False,
            "next_action": {
                "executor": "human",
                "action": "review_e2e_configuration",
                "required_inputs": ["test_identity", "config_path"],
                "allowed_operations": ["preflight-task-to-pr-e2e"],
                "requires_authorization": True,
                "stop_workflow": False,
                "ownership_effect": "none",
            },
        }

    def config_path(self) -> Path:
        return (
            self.source_root
            / "maintainer"
            / ".local"
            / "integration"
            / CONFIG_FILE_NAME
        )

    def _load_config(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise RuntimeErrorResult(
                code="e2e_configuration_missing",
                message="真实全链路配置尚未初始化",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                retry_safe=True,
                required_human_action=(
                    "请先运行 prepare-task-to-pr-e2e-config，一次性确认测试身份与 Project Profile"
                ),
                details={"config_path": str(path)},
            )
        try:
            info = path.lstat()
        except OSError as error:
            raise self._invalid_config(f"无法读取配置元数据：{error}") from error
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
            raise self._invalid_config("配置必须是单链接普通文件")
        try:
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(
                raw,
                object_pairs_hook=self._reject_duplicate_keys,
                parse_constant=self._reject_non_finite,
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise self._invalid_config(f"配置不是有效 UTF-8 JSON：{error}") from error
        if not isinstance(payload, dict):
            raise self._invalid_config("配置根必须是 JSON object")
        allowed = {
            "schema_version",
            "agent_id",
            "project_profile",
            "expected_confirmer",
        }
        if set(payload) != allowed:
            raise self._invalid_config("配置字段必须与版本 1 合同完全一致")
        if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
            raise self._invalid_config("配置 schema_version 不受支持")
        self._validate_config_values(
            payload.get("agent_id"),
            payload.get("project_profile"),
            payload.get("expected_confirmer"),
        )
        return payload

    def _load_capabilities(self) -> dict[str, dict[str, str]]:
        path = (
            self.source_root
            / "developer"
            / "standards"
            / "capabilities"
            / "operations.yaml"
        )
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise RuntimeErrorResult(
                code="e2e_capability_catalog_unavailable",
                message="无法读取 developer 原子能力目录",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请先修复 developer 能力目录，再运行真实全链路预检",
                details={"reason": str(error)},
            ) from error
        if not isinstance(payload, dict) or payload.get("workplane") != "developer":
            raise self._invalid_catalog("能力目录不是 developer 工作面")
        entries = payload.get("capabilities")
        if not isinstance(entries, list):
            raise self._invalid_catalog("能力目录缺少 capabilities 列表")
        result: dict[str, dict[str, str]] = {}
        for raw in entries:
            if not isinstance(raw, dict):
                raise self._invalid_catalog("能力条目必须是映射")
            capability_id = raw.get("id")
            status = raw.get("status")
            next_action = raw.get("next_action")
            if not all(isinstance(value, str) and value.strip() for value in (capability_id, status, next_action)):
                raise self._invalid_catalog("能力条目缺少 id、status 或 next_action")
            if capability_id in result:
                raise self._invalid_catalog(f"能力编号重复：{capability_id}")
            result[capability_id] = {
                "status": status,
                "next_action": next_action,
            }
        return result

    def _validate_issue_key(self, issue_key: str) -> None:
        if not ISSUE_KEY_PATTERN.fullmatch(issue_key):
            raise RuntimeErrorResult(
                code="integration_issue_invalid",
                message="测试 Jira 编号格式无效",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请提供形如 TAP-12289 的测试 Jira 编号",
            )
    def _validate_config_values(
        self,
        agent_id: object,
        project_profile: object,
        expected_confirmer: object,
    ) -> None:
        if not isinstance(agent_id, str) or (
            len(agent_id) > 128 or not AGENT_ID_PATTERN.fullmatch(agent_id)
        ):
            raise RuntimeErrorResult(
                code="integration_agent_id_invalid",
                message="agent_id 只能包含 [0-9A-Za-z_-]，且最长 128 字符",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请在全链路配置中提供合法测试 agent_id",
            )
        if not isinstance(project_profile, str) or not PROFILE_PATTERN.fullmatch(
            project_profile
        ):
            raise RuntimeErrorResult(
                code="integration_project_profile_invalid",
                message="project_profile 格式无效",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请在全链路配置中选择已安装的 Project Profile",
            )
        if not isinstance(expected_confirmer, str) or (
            not expected_confirmer.strip() or len(expected_confirmer) > 2048
        ):
            raise RuntimeErrorResult(
                code="integration_confirmed_by_invalid",
                message="expected_confirmer 必须是非空显式值",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请在全链路配置中提供预期确认人；每次运行仍需现场确认",
            )

    def _reject_duplicate_keys(self, pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise self._invalid_config(f"配置包含重复字段：{key}")
            result[key] = value
        return result

    def _reject_non_finite(self, value: str) -> Any:
        raise self._invalid_config(f"配置不允许非有限数值：{value}")

    def _invalid_config(self, message: str) -> RuntimeErrorResult:
        return RuntimeErrorResult(
            code="e2e_configuration_invalid",
            message=message,
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请重新生成并人工审查真实全链路配置",
        )

    def _invalid_catalog(self, message: str) -> RuntimeErrorResult:
        return RuntimeErrorResult(
            code="e2e_capability_catalog_invalid",
            message=message,
            status="blocked",
            exit_code=EXIT_BLOCKED,
            required_human_action="请先修复 developer 能力目录，再运行真实全链路预检",
        )
