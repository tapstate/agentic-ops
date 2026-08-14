from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from typing import Any

from ao_maint.integration.model import ISSUE_KEY_PATTERN, load_manifest
from ao_maint.integration.offline_fake import OfflineFakeRunner
from ao_maint.integration.task_to_pr import (
    AGENT_ID_PATTERN,
    acceptance_summary,
    load_json_object,
    task_to_pr_manifest_template,
)
from ao_maint.io import atomic_write_json
from ao_maint.output import EXIT_BLOCKED, RuntimeErrorResult


class IntegrationService:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root.resolve()

    def prepare_task_to_pr(
        self,
        issue_key: str,
        *,
        output: str | None = None,
        agent_id: str | None = None,
        confirmed_by: str | None = None,
    ) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        self._validate_optional_identity(agent_id, confirmed_by)
        output_path = (
            Path(output).absolute()
            if output
            else self.source_root
            / "maintainer"
            / ".local"
            / "integration"
            / f"{issue_key}.task-to-pr.manifest.json"
        )
        self._require_new_output(output_path)
        payload = task_to_pr_manifest_template(issue_key)
        if agent_id is not None:
            payload["agent"]["agent_id"] = agent_id
        if confirmed_by is not None:
            payload["authorization"]["confirmed_by"] = confirmed_by
        atomic_write_json(output_path, payload)
        return {
            "issue_key": issue_key,
            "manifest_path": str(output_path),
            "manifest_status": "awaiting_explicit_input_and_confirmation",
            "protocol": "task_to_pr_review",
            "schema_path": str(
                self.source_root
                / "shared"
                / "integration"
                / "task-to-pr-manifest.schema.json"
            ),
            "host_state_read": False,
            "business_workspace_read": False,
            "credentials_read": False,
            "confirmation_instruction": (
                "填写所有 REQUIRED 后，将 authorization.confirmed_manifest_sha256 暂置为空字符串，"
                "以 UTF-8、ensure_ascii=false、sort_keys=true、separators=(',',':') 生成 canonical JSON，"
                "计算 SHA-256 并由确认人把摘要写回；任何字段变化都必须重新确认"
            ),
            "required_inputs": [
                "独立 developer 工作空间绝对路径",
                "Jira key、不可变 issue ID、Jira HTTPS 站点与 Project key",
                "当前 Jira accountId 与真实经办 assignee accountId",
                "Project Profile 状态映射、允许的状态分类与可选 agentic_id Custom Field",
                "agent_id、Project Profile、唯一 agentic_run_id 与明确 execution_identity",
                "canonical Jira issue 内容 SHA-256、inputs/ 下批准计划文件及其原始 UTF-8 SHA-256",
                "业务仓库绝对路径、仓库 slug、remote 名称、基线/任务/目标/保护分支，且 base_branch 必须等于 target_branch",
                "任务包含范围与明确排除范围",
                "固定 argv、工作目录和超时的验证清单",
                "GitHub PR provider、仓库 slug、目标分支与 CI 策略",
                "允许的 Jira、Git、GitHub 外部动作",
                "确认人、确认时间、授权引用与 canonical manifest SHA-256",
            ],
            "forbidden_implicit_sources": [
                "~/.agentic-ops",
                ".env 或其它相邻凭据文件",
                "业务项目凭据或进程环境凭据",
                "其它业务工作空间",
                "Git identity 或全局 Git 配置",
                "历史任务状态或聊天隐含信息",
            ],
            "next_action": (
                "人工填写、审阅并确认 manifest 后，把该文件交给 developer 工作面的 "
                "$run-task-to-pr-test；maintainer 不执行真实业务任务"
            ),
        }

    def _validate_optional_identity(
        self, agent_id: str | None, confirmed_by: str | None
    ) -> None:
        if agent_id is not None and (
            len(agent_id) > 128 or not AGENT_ID_PATTERN.fullmatch(agent_id)
        ):
            raise RuntimeErrorResult(
                code="integration_agent_id_invalid",
                message="agent_id 只能包含 [0-9A-Za-z_-]，且最长 128 字符",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请显式提供合法 agent_id，或省略参数并人工填写清单",
            )
        if confirmed_by is not None and (
            not confirmed_by.strip() or len(confirmed_by) > 2048
        ):
            raise RuntimeErrorResult(
                code="integration_confirmed_by_invalid",
                message="confirmed_by 必须是 1 到 2048 字符的非空显式值",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请显式提供确认人，或省略参数并人工填写清单",
            )

    def accept_task_to_pr(
        self, issue_key: str, manifest_path: str, result_path: str
    ) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        manifest = load_json_object(manifest_path, "manifest")
        result = load_json_object(result_path, "result")
        return acceptance_summary(issue_key, manifest, result)

    def prepare_offline(
        self, issue_key: str, *, output: str | None = None
    ) -> dict[str, Any]:
        self._validate_issue_key(issue_key)
        output_path = (
            Path(output).absolute()
            if output
            else self.source_root
            / "maintainer"
            / ".local"
            / "integration"
            / f"{issue_key}.offline-manifest.json"
        )
        self._require_new_output(output_path)
        payload = _offline_manifest_template(issue_key)
        atomic_write_json(output_path, payload)
        return {
            "issue_key": issue_key,
            "manifest_path": str(output_path),
            "manifest_status": "awaiting_explicit_input_and_confirmation",
            "integration_kind": "offline_contract_regression",
            "host_state_read": False,
            "confirmation_instruction": (
                "填写除 confirmed_manifest_sha256 外的全部字段后，将该字段暂置为空字符串，"
                "按键排序和紧凑 JSON 计算 SHA-256，再把摘要写回该字段并由确认人核对"
            ),
            "required_inputs": [
                "AgenticOps 源码仓库绝对路径与 ref",
                "离线 fixture 代码库绝对路径、仓库 slug 与 ref",
                "agent_id 与 Project Profile",
                "Fake Jira Project、允许读取和允许写入",
                "固定验证 recipe 与参数",
                "cleanup.strategy=always",
                "显式离线能力",
                "维护者确认人、时间和授权引用",
            ],
            "forbidden_implicit_sources": [
                "~/.agentic-ops",
                "其它业务工作空间",
                "Git identity 或全局 Git 配置",
                "进程环境凭据",
                "历史任务状态",
            ],
            "next_action": (
                f"人工填写并确认清单后运行 ao-maint integration run-offline {issue_key} "
                f"--manifest {output_path}"
            ),
        }

    def run_offline(self, issue_key: str, manifest_path: str) -> dict[str, Any]:
        manifest = load_manifest(manifest_path, issue_key)
        if manifest.agentic_ops.repository != self.source_root:
            raise RuntimeErrorResult(
                code="integration_source_mismatch",
                message="清单中的 AgenticOps 源码根与当前 ao-maint 源头不一致",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请把 agentic_ops.repository 固定为当前 AgenticOps 源头工作区并重新确认清单",
                details={
                    "expected_source_root": str(self.source_root),
                    "configured_source_root": str(manifest.agentic_ops.repository),
                },
            )
        digest = hashlib.sha256(manifest.path.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="ao-integration-") as temporary:
            result = OfflineFakeRunner(manifest, Path(temporary)).run()
        return {
            **result,
            "manifest_sha256": digest,
            "cleanup_status": "completed",
        }

    def _validate_issue_key(self, issue_key: str) -> None:
        if not ISSUE_KEY_PATTERN.fullmatch(issue_key):
            raise RuntimeErrorResult(
                code="integration_issue_invalid",
                message="测试 Jira 编号格式无效",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请提供形如 TAP-12289 的测试 Jira 编号",
            )

    def _require_new_output(self, output_path: Path) -> None:
        if output_path.exists():
            raise RuntimeErrorResult(
                code="integration_manifest_exists",
                message=f"集成测试输入清单已存在：{output_path}",
                status="blocked",
                exit_code=EXIT_BLOCKED,
                required_human_action="请人工核对已有清单，或使用 --output 指定新的文件",
            )


def _offline_manifest_template(issue_key: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "issue_key": issue_key,
        "adapter": "REQUIRED",
        "agentic_ops": {"repository": "REQUIRED", "ref": "REQUIRED"},
        "task_repository": {
            "repository": "REQUIRED",
            "slug": "REQUIRED",
            "ref": "REQUIRED",
        },
        "agent": {"agent_id": "REQUIRED", "project_profile": "REQUIRED"},
        "jira": {
            "project_key": issue_key.partition("-")[0],
            "allowed_reads": ["REQUIRED"],
            "allowed_writes": ["REQUIRED"],
        },
        "verification": {
            "commands": [
                {"recipe": "REQUIRED", "args": ["REQUIRED"]},
            ]
        },
        "cleanup": {"strategy": "REQUIRED"},
        "credential_channels": ["REQUIRED"],
        "allowed_external_capabilities": ["REQUIRED"],
        "confirmation": {
            "confirmed_by": "REQUIRED",
            "confirmed_at": "REQUIRED",
            "authorization_reference": "REQUIRED",
            "confirmed_manifest_sha256": "REQUIRED",
        },
    }
