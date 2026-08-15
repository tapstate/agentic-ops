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
            "manifest_status": "awaiting_developer_resolution_and_confirmation",
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
            "configuration_model": {
                "workspace_once": [
                    "agent_id 与 developer 工作空间",
                    "Project Profile 与工作空间 Jira 账户授权",
                    "业务源码仓库与执行身份",
                ],
                "project_profile_defaults": [
                    "Jira HTTPS 站点、Project Key、状态映射和字段映射",
                    "默认仓库、项目流程和固定策略",
                ],
                "jira_task_facts": [
                    "Issue ID、经办人、状态、标题、描述和已配置业务字段",
                ],
                "runtime_generated": [
                    "agentic_run_id、内容摘要、时间、协议摘要和证据路径",
                ],
                "ai_proposed_for_review": [
                    "实施计划、包含/排除范围、任务分支和验证命令",
                ],
            },
            "required_user_actions": [
                "启动 $test-task-to-pr-e2e 并确认本次真实测试允许的外部副作用范围",
                "运行时通过隐藏输入向隔离 developer 工作空间提供 Jira 授权",
                "测试结束后审查真实 PR、结果包和完整摩擦复盘",
            ],
            "protocol_note": (
                "manifest 是机器审计合同，不是用户配置表；REQUIRED 字段由 developer 工作面按"
                "工作空间、Project Profile、Jira 卡片、Runtime 探测和已审查计划解析"
            ),
            "forbidden_implicit_sources": [
                "~/.agentic-ops",
                ".env 或其它相邻凭据文件",
                "业务项目凭据或进程环境凭据",
                "其它业务工作空间",
                "Git identity 或全局 Git 配置",
                "历史任务状态或聊天隐含信息",
            ],
            "next_action": (
                "由 maintainer 工作面的 $test-task-to-pr-e2e 创建隔离业务工作空间、"
                "启动 developer Agent 并根据 ao-work 结构化 next_action 推进到 PR 审查"
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
