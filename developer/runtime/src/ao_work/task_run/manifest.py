from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ao_work.config import load_project_profile
from ao_work.installation import load_install_identity
from ao_work.output import EXIT_BLOCKED, RuntimeErrorResult
from ao_work.task_run.protocol import digest, manifest_digest, validate_manifest
from ao_work.task_run.service import TaskRunProtocol
from ao_work.task_state import TaskStore
from ao_work.task_state.io import atomic_write_json, atomic_write_text, read_json, read_text
from ao_work.workspace import Workspace
from ao_work.workspace_security import validate_managed_path, validate_workspace_managed_path


class TaskRunManifestService:
    """Generate one task-to-PR manifest from current developer task facts."""

    def __init__(
        self,
        workspace: Workspace,
        install_root: Path,
        store: TaskStore,
        *,
        lock_timeout: float,
    ) -> None:
        self.workspace = workspace
        self.install_root = install_root
        self.store = store
        self.protocol = TaskRunProtocol(
            workspace,
            install_root=install_root,
            lock_timeout=lock_timeout,
        )

    def prepare(self, issue_key: str) -> dict[str, Any]:
        bundle = self._build_bundle(issue_key)
        draft_path = self._draft_path(issue_key, bundle["agentic_run_id"])
        existing = read_json(draft_path) if draft_path.is_file() else None
        created = existing is None or existing.get("draft_digest") != bundle["draft_digest"]
        if created:
            atomic_write_json(
                draft_path,
                {
                    "schema_version": 1,
                    "issue_key": issue_key,
                    "agentic_run_id": bundle["agentic_run_id"],
                    "draft_digest": bundle["draft_digest"],
                    "stable": bundle["stable"],
                    "confirmation_package": bundle["confirmation_package"],
                    "prepared_at": self._now(),
                },
            )
        self.store.record_gate_transition(
            issue_key,
            bundle["agentic_run_id"],
            stage="task_run_manifest_review",
            next_action="review_task_run_authorization",
            operation="task_run_prepare",
            status="completed",
            evidence={
                "draft_digest": bundle["draft_digest"],
                "solution_digest": bundle["solution_digest"],
                "head_sha": bundle["head_sha"],
            },
        )
        return {
            "issue_key": issue_key,
            "agentic_run_id": bundle["agentic_run_id"],
            "draft_created": created,
            "confirmation_required": True,
            "confirmation_package": bundle["confirmation_package"],
            "side_effects": [],
            "agentic_next_action": {
                "executor": "human",
                "action": "review_task_run_authorization",
                "required_inputs": [
                    "solution",
                    "repository",
                    "scope",
                    "verification",
                    "permitted_external_actions",
                    "prohibited_actions",
                    "residual_risks",
                ],
                "allowed_operations": ["task_run_authorize"],
                "requires_authorization": True,
                "stop_workflow": True,
                "reason": "请一次审查完整设计与连续执行授权；无需确认内部摘要或文件路径",
            },
        }

    def authorize(
        self,
        issue_key: str,
        *,
        confirmed_by: str,
        confirm: bool,
    ) -> dict[str, Any]:
        if not confirm:
            raise _blocked(
                "task_run_authorization_confirmation_required",
                "尚未收到当前完整执行包的明确确认",
                "请先展示 task-run prepare 返回的完整 confirmation_package；确认后使用 --confirm",
            )
        confirmer = confirmed_by.strip()
        if not confirmer or len(confirmer) > 2048:
            raise _blocked(
                "task_run_confirmer_invalid",
                "confirmed_by 必须是当前会话声明的非空确认人",
                "请使用当前设计审查中声明的确认人名称",
            )
        bundle = self._build_bundle(issue_key)
        draft_path = self._draft_path(issue_key, bundle["agentic_run_id"])
        if not draft_path.is_file():
            raise _blocked(
                "task_run_manifest_draft_missing",
                "当前任务没有待确认的受管 manifest draft",
                "请先执行 task-run prepare 并展示完整确认包",
            )
        draft = read_json(draft_path)
        if draft.get("draft_digest") != bundle["draft_digest"]:
            raise _blocked(
                "task_run_manifest_draft_stale",
                "Jira、方案、身份、Profile、工作树、分支、HEAD、验证或权限已经变化",
                "请重新执行 task-run prepare，并只对新的完整确认包请求一次确认",
            )

        approved_path = self.workspace.root / bundle["approved_plan_file"]
        manifest_path = self.workspace.root / bundle["manifest_file"]
        self._validate_output_path(approved_path)
        self._validate_output_path(manifest_path)

        existing_manifest = read_json(manifest_path) if manifest_path.is_file() else None
        if existing_manifest is not None:
            validated = validate_manifest(existing_manifest)
            if (
                validated["authorization"]["confirmed_by"] != confirmer
                or self._manifest_stable(validated) != bundle["manifest_base"]
            ):
                raise _blocked(
                    "task_run_manifest_output_conflict",
                    "当前运行的最终 manifest 已存在且内容不同",
                    "请停止覆盖；核对既有授权或使用新的 agentic_run_id",
                )
            self.protocol.prevalidate_manifest(
                validated, require_initial_head=False
            )
            return self._authorized_result(bundle, validated, created=False)

        if approved_path.is_file():
            if read_text(approved_path).rstrip("\n") != bundle["approved_plan"].rstrip("\n"):
                raise _blocked(
                    "task_run_approved_plan_output_conflict",
                    "批准计划路径已存在不同内容",
                    "请停止覆盖并核对当前任务运行",
                )
        else:
            atomic_write_text(approved_path, bundle["approved_plan"])

        manifest = copy.deepcopy(bundle["manifest_base"])
        # File SHA is raw UTF-8 SHA-256, not canonical JSON digest.
        approved_sha = hashlib.sha256(
            (bundle["approved_plan"].rstrip("\n") + "\n").encode("utf-8")
        ).hexdigest()
        manifest["task_binding"]["approved_plan_sha256"] = approved_sha
        reference = (
            f"user-confirmation:{issue_key}:{bundle['agentic_run_id']}:{approved_sha}"
        )
        manifest["authorization"] = {
            "reference": reference,
            "confirmed_by": confirmer,
            "confirmed_at": self._now(),
            "confirmed_manifest_sha256": "",
        }
        manifest["authorization"]["confirmed_manifest_sha256"] = manifest_digest(manifest)
        validated = validate_manifest(manifest)
        self.protocol.prevalidate_manifest(validated, require_initial_head=True)

        self.store.append_decision(
            issue_key,
            bundle["agentic_run_id"],
            "task_run_authorization",
            str(bundle["confirmation_package"]["review_summary"]),
            reference,
        )
        atomic_write_json(manifest_path, validated)
        self.store.record_gate_transition(
            issue_key,
            bundle["agentic_run_id"],
            stage="task_run_authorized",
            next_action="task_run_open",
            operation="task_run_authorize",
            status="completed",
            evidence={
                "manifest_sha256": manifest_digest(validated),
                "solution_digest": bundle["solution_digest"],
                "head_sha": bundle["head_sha"],
            },
        )
        return self._authorized_result(bundle, validated, created=True)

    def _build_bundle(self, issue_key: str) -> dict[str, Any]:
        state = self.store.inspect(issue_key)
        task = state["task"]
        run_id = str(task["agentic_run_id"])
        run_root = self.workspace.root / ".agentic-ops" / "tasks" / issue_key / "runs" / run_id
        source = read_json(run_root / "gates" / "source-context.json")
        solution = read_json(run_root / "gates" / "solution.json")
        source_stable = dict(source)
        source_digest = str(source_stable.pop("context_digest", ""))
        source_stable.pop("observed_at", None)
        if (
            source.get("issue_key") != issue_key
            or source.get("agentic_run_id") != run_id
            or digest(source_stable) != source_digest
        ):
            raise _blocked(
                "task_run_source_context_digest_mismatch",
                "任务来源快照身份或摘要无效",
                "请通过当前 Runtime 重新建立来源上下文，不得手工修改门禁状态",
            )
        solution_stable = dict(solution)
        solution_digest = str(solution_stable.pop("solution_digest", ""))
        solution_stable.pop("classified_at", None)
        if (
            solution.get("issue_key") != issue_key
            or solution.get("agentic_run_id") != run_id
            or digest(solution_stable) != solution_digest
        ):
            raise _blocked(
                "task_run_solution_digest_mismatch",
                "L1 方案身份或摘要无效",
                "请通过 task solution classify 重新生成方案，不得手工修改门禁状态",
            )
        if solution.get("solution_level") != "L1":
            raise _blocked(
                "task_run_solution_not_l1",
                "当前方案不是可直接授权的 L1 设计",
                "请先完成风险决策或修订方案，再生成执行包",
            )
        execution = solution.get("execution_plan")
        if not isinstance(execution, dict):
            raise _blocked(
                "task_run_execution_plan_missing",
                "当前 L1 方案缺少结构化验证与变更仓库计划",
                "请由 AI 在 solution 输入补齐 execution_plan 并重新分级；无需用户手写 manifest",
            )
        scope = state.get("repository_scope")
        rows = scope.get("confirmed_repository_branch_map") if isinstance(scope, dict) else None
        prepared = [
            dict(row)
            for row in rows or []
            if isinstance(row, dict) and row.get("worktree_status") == "prepared"
        ]
        if len(prepared) != 1:
            code = (
                "task_run_repository_worktree_missing"
                if not prepared
                else "task_run_multi_repository_unsupported"
            )
            raise _blocked(
                code,
                "单仓 task-to-PR 协议要求恰好一个已准备的实际变更工作树",
                "请准备唯一变更仓库；多仓交付需专题扩展 shared 协议，不能选择默认仓库降级",
            )
        row = prepared[0]
        repository_slug = str(execution.get("change_repository") or "")
        if repository_slug != row.get("repository"):
            raise _blocked(
                "task_run_repository_binding_mismatch",
                "L1 执行计划仓库与唯一 prepared 工作树不一致",
                "请修正方案并重新分级，不得覆盖仓库确认事实",
            )
        root = Path(str(row.get("worktree_path") or "")).expanduser().resolve()
        branch = self._git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        head = self._git(root, "rev-parse", "HEAD")
        status = self._git(root, "status", "--porcelain=v1", "--untracked-files=all")
        if branch != row.get("task_branch"):
            raise _blocked(
                "task_run_worktree_branch_changed",
                "任务工作树当前分支与确认任务分支不一致",
                "请恢复确认分支或重新进入设计审查",
            )
        if head != row.get("worktree_baseline_sha") or status:
            raise _blocked(
                "task_run_worktree_start_changed",
                "生成执行授权前任务工作树 HEAD 已变化或不干净",
                "请在代码修改前重新准备执行包；已有代码事实必须先进入风险决策",
            )
        if solution.get("head_sha") != head:
            raise _blocked(
                "task_run_solution_head_changed",
                "L1 方案绑定的源码 HEAD 与当前任务工作树不一致",
                "请重新执行 intake/solution 并审查新方案",
            )

        defaults = source.get("workspace_defaults")
        issue = source.get("issue")
        profile_snapshot = source.get("project_profile")
        if not all(isinstance(value, dict) for value in (defaults, issue, profile_snapshot)):
            raise _blocked(
                "task_run_source_context_invalid",
                "任务来源快照不完整",
                "请重新建立当前任务来源上下文",
            )
        assert isinstance(defaults, dict)
        assert isinstance(issue, dict)
        assert isinstance(profile_snapshot, dict)
        if defaults.get("repository") != repository_slug or Path(
            str(defaults.get("source_root") or "")
        ).expanduser().resolve() != root:
            raise _blocked(
                "task_run_source_context_changed",
                "任务来源快照尚未绑定确认后的实际工作树",
                "请通过受管 worktree prepare 重建来源上下文后重新执行 intake/solution",
            )

        install_identity = load_install_identity(self.install_root)
        profile = load_project_profile(
            self.install_root,
            str(defaults.get("project_profile") or ""),
            workspace_root=self.workspace.root,
        )
        target_branch = str(row.get("from_branch") or "")
        protected = self._protected_branches(root, target_branch)
        status_category = str(issue.get("status_category") or "").strip()
        if not status_category:
            # 兼容 AO-95 前已经建立的 implementation 来源快照；新快照总是保存 Jira category。
            status_category = "indeterminate"
        if status_category.casefold() == "done":
            raise _blocked(
                "task_run_jira_status_forbidden",
                "当前任务来源快照已经属于 Jira Done 分类",
                "请停止生成执行包并核对任务状态",
            )
        permissions = [
            "jira_read",
            "jira_comment",
            "jira_worklog",
            "git_commit",
            "git_remote_read",
            "git_push_task_branch",
            "github_pr_create_or_update",
            "github_pr_read",
        ]
        endpoint: dict[str, Any] = {
            "provider": "github",
            "repository_slug": repository_slug,
            "target_branch": target_branch,
            "ci_policy": "require_passed",
        }
        if profile.process_id == "development_change_v2":
            if profile.ci is None:
                raise _blocked(
                    "task_run_ci_profile_missing",
                    "development_change_v2 缺少 CI Profile",
                    "请修复 Project Profile 后重新生成执行包",
                )
            endpoint["ci_policy"] = "detect_from_github_pr"
            endpoint["ci"] = profile.ci.manifest_payload()
            permissions.extend(["github_ci_read", "github_artifact_read"])

        relative_root = f"inputs/agentic-ops/{issue_key}/{run_id}"
        approved_file = f"{relative_root}/approved-plan.md"
        manifest_file = f"{relative_root}/task-to-pr.manifest.json"
        manifest_base: dict[str, Any] = {
            "schema_version": 1,
            "protocol": "task_to_pr_review",
            "process_id": profile.process_id,
            "workspace": {"root": str(self.workspace.root.resolve())},
            "issue": {
                "key": issue_key,
                "id": str(issue.get("id") or task.get("jira_issue_id") or ""),
                "project_key": str(issue.get("project_key") or task.get("project_key") or ""),
            },
            "jira": {
                "base_url": str(defaults.get("jira_base_url") or ""),
                "account_id": str(defaults.get("jira_account_id") or ""),
                "assignee_account_id": str(issue.get("assignee_account_id") or ""),
                "status_mapping": dict(profile.status_mapping),
                "allowed_status_categories": [status_category],
            },
            "agent": {
                "agent_id": str(install_identity.get("agent_id") or ""),
                "project_profile": profile.profile_id,
                "agentic_run_id": run_id,
            },
            "task_binding": {
                "issue_content_sha256": str(issue.get("issue_content_sha256") or ""),
                "approved_plan_file": approved_file,
                "approved_plan_sha256": "0" * 64,
            },
            "execution_identity": copy.deepcopy(install_identity.get("execution_identity")),
            "repository": {
                "root": str(root),
                "slug": repository_slug,
                "remote_name": "origin",
                "base_branch": target_branch,
                "task_branch": branch,
                "target_branch": target_branch,
                "protected_branches": protected,
            },
            "scope": copy.deepcopy(solution.get("scope")),
            "verification": copy.deepcopy(execution.get("verification")),
            "pr_endpoint": endpoint,
            "permitted_external_actions": permissions,
            "authorization": {
                "reference": "PENDING",
                "confirmed_by": "PENDING",
                "confirmed_at": "PENDING",
                "confirmed_manifest_sha256": "PENDING",
            },
        }
        plan = self._approved_plan(
            issue_key=issue_key,
            run_id=run_id,
            solution=solution,
            execution=execution,
            repository=manifest_base["repository"],
            permissions=permissions,
            head_sha=head,
        )
        package = {
            "solution": solution.get("proposed_solution"),
            "review_summary": execution.get("review_summary"),
            "repository": {**manifest_base["repository"], "head_sha": head},
            "scope": manifest_base["scope"],
            "verification": manifest_base["verification"],
            "verification_normalization_changes": execution.get(
                "normalization_changes", []
            ),
            "permitted_external_actions": permissions,
            "prohibited_actions": [
                "merge_pr",
                "jira_done",
                "release",
                "create_tag",
                "push_protected_branch",
                "force_push",
                "rewrite_history",
            ],
            "residual_risks": solution.get("residual_risks", []),
            "authorization_scope": "实现、验证、提交、任务分支推送、必要 Jira 回写和 PR 创建；统一停在代码审查",
        }
        stable = {
            "source_context_digest": source.get("context_digest"),
            "solution_digest": solution.get("solution_digest"),
            "repository_scope": row,
            "head_sha": head,
            "manifest_base": manifest_base,
            "approved_plan": plan,
            "confirmation_package": package,
        }
        return {
            "issue_key": issue_key,
            "agentic_run_id": run_id,
            "solution_digest": str(solution.get("solution_digest") or ""),
            "head_sha": head,
            "approved_plan_file": approved_file,
            "manifest_file": manifest_file,
            "approved_plan": plan,
            "manifest_base": manifest_base,
            "confirmation_package": package,
            "stable": stable,
            "draft_digest": digest(stable),
        }

    def _authorized_result(
        self,
        bundle: Mapping[str, Any],
        manifest: Mapping[str, Any],
        *,
        created: bool,
    ) -> dict[str, Any]:
        return {
            "issue_key": bundle["issue_key"],
            "agentic_run_id": bundle["agentic_run_id"],
            "authorization_status": "active",
            "manifest_created": created,
            "manifest_path": bundle["manifest_file"],
            "approved_plan_path": bundle["approved_plan_file"],
            "manifest_sha256": manifest_digest(manifest),
            "authorization_reference": manifest["authorization"]["reference"],
            "agentic_next_action": "task_run_open",
        }

    @staticmethod
    def _manifest_stable(manifest: Mapping[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(dict(manifest))
        value["task_binding"]["approved_plan_sha256"] = "0" * 64
        value["authorization"] = {
            "reference": "PENDING",
            "confirmed_by": "PENDING",
            "confirmed_at": "PENDING",
            "confirmed_manifest_sha256": "PENDING",
        }
        return value

    def _draft_path(self, issue_key: str, run_id: str) -> Path:
        path = (
            self.workspace.root
            / ".agentic-ops"
            / "tasks"
            / issue_key
            / "runs"
            / run_id
            / "task-run-manifest-draft.json"
        )
        return validate_workspace_managed_path(self.workspace.root, path)

    def _validate_output_path(self, path: Path) -> None:
        validate_managed_path(self.workspace.root, path)
        relative = path.relative_to(self.workspace.root)
        if len(relative.parts) < 5 or relative.parts[:2] != ("inputs", "agentic-ops"):
            raise _blocked(
                "task_run_manifest_output_path_invalid",
                "执行包输出路径不在 Runtime 固定 inputs/agentic-ops 命名空间",
                "请停止执行并核对 Runtime 版本",
            )

    @staticmethod
    def _approved_plan(
        *,
        issue_key: str,
        run_id: str,
        solution: Mapping[str, Any],
        execution: Mapping[str, Any],
        repository: Mapping[str, Any],
        permissions: list[str],
        head_sha: str,
    ) -> str:
        commands = "\n".join(
            f"- `{json.dumps(item['command'], ensure_ascii=False)}`，目录 `{item['working_directory']}`，超时 {item['timeout_seconds']} 秒"
            for item in execution["verification"]
        )
        included = "\n".join(f"- `{item}`" for item in solution["scope"]["included"])
        excluded = "\n".join(f"- `{item}`" for item in solution["scope"]["excluded"]) or "- 无"
        actions = "\n".join(f"- `{item}`" for item in permissions)
        risks = "\n".join(f"- {item}" for item in solution.get("residual_risks", [])) or "- 无"
        return "\n".join(
            [
                "# 已确认任务执行计划",
                "",
                f"- Jira: `{issue_key}`",
                f"- 运行 ID: `{run_id}`",
                f"- 仓库: `{repository['slug']}`",
                f"- 工作树: `{repository['root']}`",
                f"- 基线/目标分支: `{repository['target_branch']}`",
                f"- 任务分支: `{repository['task_branch']}`",
                f"- 确认时 HEAD: `{head_sha}`",
                "",
                "## 方案",
                "",
                str(solution["proposed_solution"]),
                "",
                "## 包含范围",
                "",
                included,
                "",
                "## 排除范围",
                "",
                excluded,
                "",
                "## 验证",
                "",
                commands,
                "",
                "## 允许的外部动作",
                "",
                actions,
                "",
                "## 禁止动作",
                "",
                "- 合并 PR、Jira Done、发布、Tag、保护分支推送、强推和历史改写",
                "",
                "## 残留风险",
                "",
                risks,
                "",
            ]
        )

    @staticmethod
    def _protected_branches(root: Path, target: str) -> list[str]:
        result = [target]
        for branch in ("main", "master", "develop"):
            if branch in result:
                continue
            local = subprocess.run(
                ["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
                check=False,
            )
            remote = subprocess.run(
                ["git", "-C", str(root), "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                check=False,
            )
            if local.returncode == 0 or remote.returncode == 0:
                result.append(branch)
        return result

    @staticmethod
    def _git(root: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0:
            raise _blocked(
                "task_run_manifest_git_read_failed",
                f"无法回读任务工作树 Git 事实：{' '.join(arguments)}",
                "请修复任务工作树后重新生成执行包",
            )
        return completed.stdout.strip()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


def _blocked(code: str, message: str, action: str) -> RuntimeErrorResult:
    return RuntimeErrorResult(
        code=code,
        message=message,
        status="blocked",
        exit_code=EXIT_BLOCKED,
        retry_safe=True,
        required_human_action=action,
    )
