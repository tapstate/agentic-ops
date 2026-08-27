from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ao_work.output import RuntimeErrorResult, failure
from ao_work.task_gate import TaskGateService, _digest
from ao_work.task_state import TaskIdentity, TaskStore
from ao_work.workspace import Workspace


ISSUE_KEY = "TAP-12289"
RUN_ID = "run-TAP-12289-test"


class TaskGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.workspace_root = root / "workspace"
        self.source_root = root / "source"
        self.workspace_root.mkdir()
        self.source_root.mkdir()
        self._git("init", "-b", "main")
        self._git("config", "user.name", "Harsen Test Bot")
        self._git("config", "user.email", "harsen@example.test")
        (self.source_root / "README.md").write_text(
            "# 任务证据\n\n默认目标分支为 develop。\n", encoding="utf-8"
        )
        self._git("add", "README.md")
        self._git("commit", "-m", "初始化测试仓库")

        state_root = self.workspace_root / ".agentic-ops"
        state_root.mkdir()
        config_path = state_root / "agent.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": 4,
                    "workplane": "developer",
                    "install_identity_ref": "install:" + "a" * 64,
                    "source_root": str(self.source_root),
                }
            ),
            encoding="utf-8",
        )
        self.workspace = Workspace(
            root=self.workspace_root,
            workplane="developer",
            config_path=config_path,
        )
        self.store = TaskStore(self.workspace_root)
        self.store.initialize(
            TaskIdentity(
                connection_id="tapdata-cloud",
                jira_issue_id="12289",
                issue_key=ISSUE_KEY,
                project_key="TAP",
                agentic_run_id=RUN_ID,
            )
        )
        self.service = TaskGateService(self.workspace, self.store)
        self.context = self.service.record_source_context(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            issue={
                "id": "12289",
                "key": ISSUE_KEY,
                "project_key": "TAP",
                "summary": "减少任务配置阻塞",
                "status": "正在进行",
                "mapped_status": "implementation",
                "issue_type": "任务",
                "assignee_account_id": "jira-account-1",
                "task_facts": {
                    "description": {
                        "status": "available",
                        "facts": [],
                        "sections": {
                            "__overview__": "根据任务卡片和源码补齐准入信息。",
                            "仓库分支": "tapdata/tapdata@develop",
                        },
                    },
                    "comments": {"status": "available", "comment_count": 0, "facts": []},
                    "repository_branch_hints": [],
                },
                "issue_content_sha256": "a" * 64,
            },
            workspace_defaults={
                "agent_id": "harsen-mini-test-bot",
                "project_profile": "tapdata",
                "connection_id": "tapdata-cloud",
                "jira_base_url": "https://tapdata.atlassian.net",
                "jira_account_id": "jira-account-1",
                "repository": "tapdata/tapdata",
                "source_root": str(self.source_root),
                "execution_identity": {
                    "git_author_name": "Harsen Test Bot",
                    "git_author_email": "harsen@example.test",
                    "git_committer_name": "Harsen Test Bot",
                    "git_committer_email": "harsen@example.test",
                    "github_actor_login": "harsen-mini-test-bot",
                },
            },
            project_profile={
                "profile_id": "tapdata",
                "connection_id": "tapdata-cloud",
                "project_key": "TAP",
                "issue_types": ["任务"],
                "default_repository": "tapdata/tapdata",
                "status_mapping": {"正在进行": "implementation"},
                "fields": {},
                "resolved_fields": {
                    "owner": {
                        "source": "jira_field",
                        "required": True,
                        "value": "jira-account-1",
                    },
                    "target_repo": {
                        "source": "workspace_repo_mapping",
                        "required": True,
                        "value": "tapdata/tapdata",
                    },
                },
            },
        )
        (self.workspace_root / "inputs").mkdir()

    def test_source_context_digest_binds_trusted_reference_catalog(self) -> None:
        source_path = Path(self.context["source_context_path"])
        source = json.loads(source_path.read_text(encoding="utf-8"))
        source_stable = dict(source)
        source_stable.pop("context_digest")
        source_stable.pop("observed_at")

        self.assertTrue(source["trusted_reference_catalog"])
        self.assertEqual(source["context_digest"], _digest(source_stable))

    def test_intake_assess_continues_without_separate_confirmation(self) -> None:
        path = self._write_intake("intake.json")
        assessed = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(path.relative_to(self.workspace_root)),
        )
        self.assertTrue(assessed["ready_for_solution"])
        self.assertEqual(
            "prepare_and_classify_solution",
            assessed["agentic_next_action"]["action"],
        )
        self.assertFalse(assessed["agentic_next_action"]["requires_authorization"])
        self.assertEqual(
            {"owner", "target_repo", "target_branch"},
            {item["field"] for item in assessed["intake"]["auto_filled_values"]},
        )
        self.assertTrue(assessed["intake"]["known_facts"])
        self.assertTrue(assessed["intake_digest"])

    def test_intake_contract_error_is_ai_retryable_without_consuming_retry(self) -> None:
        invalid = self._intake_payload()
        invalid["auto_filled_values"] = {}
        path = self._write_json("invalid-auto-filled.json", invalid)

        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.assess_intake(
                issue_key=ISSUE_KEY,
                agentic_run_id=RUN_ID,
                input_file=str(path.relative_to(self.workspace_root)),
            )

        error = captured.exception
        self.assertEqual("intake_auto_fill_invalid", error.code)
        self.assertTrue(error.retry_safe)
        self.assertEqual(
            "rebuild_contract_input_and_retry_once",
            error.details["input_recovery"]["action"],
        )
        rendered = failure("task_intake_assess", error)
        self.assertEqual("ai", rendered["agentic_next_action"]["executor"])
        self.assertFalse(
            rendered["agentic_next_action"]["requires_authorization"]
        )

        assessed = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(self._write_intake("recovered.json").relative_to(self.workspace_root)),
        )
        self.assertEqual(0, assessed["intake"]["retry_count"])

    def test_intake_accepts_runtime_catalog_evidence_id(self) -> None:
        catalog = self.context["trusted_reference_catalog"]
        evidence = next(
            item
            for item in catalog
            if item["value"] == "tapdata/tapdata@develop"
        )
        payload = self._intake_payload()
        payload["auto_filled_values"] = [
            {
                "field": "repository_branch",
                "evidence_id": evidence["evidence_id"],
                "rationale": "Jira 脱敏仓库分支章节明确给出该值",
            }
        ]
        path = self._write_json("catalog-evidence.json", payload)

        assessed = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(path.relative_to(self.workspace_root)),
        )

        selected = next(
            item
            for item in assessed["intake"]["auto_filled_values"]
            if item["field"] == "repository_branch"
        )
        self.assertEqual("jira_issue", selected["source"])
        self.assertEqual(
            "issue.task_facts.description.sections.仓库分支",
            selected["reference"],
        )
        self.assertEqual("tapdata/tapdata@develop", selected["value"])

    def test_unknown_evidence_id_returns_current_catalog_without_retry(self) -> None:
        payload = self._intake_payload()
        payload["auto_filled_values"] = [
            {
                "field": "repository_branch",
                "evidence_id": "evidence-not-in-current-snapshot",
                "rationale": "测试无效证据 ID 的修复指引",
            }
        ]
        path = self._write_json("unknown-evidence.json", payload)

        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.assess_intake(
                issue_key=ISSUE_KEY,
                agentic_run_id=RUN_ID,
                input_file=str(path.relative_to(self.workspace_root)),
            )

        error = captured.exception
        self.assertEqual("intake_evidence_id_invalid", error.code)
        self.assertTrue(error.retry_safe)
        self.assertTrue(error.details["available_evidence"])
        self.assertEqual(
            self.context["context_digest"], error.details["source_context_digest"]
        )
        intake_path = self.service._gate_path(ISSUE_KEY, RUN_ID, "intake.json")
        self.assertFalse(intake_path.exists())

    def test_invalid_source_context_remains_human_blocker(self) -> None:
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service._profile_required_values({"project_profile": None}, {})

        self.assertEqual("task_source_context_invalid", captured.exception.code)
        self.assertFalse(captured.exception.retry_safe)

    def test_intake_rejects_forged_trusted_value_and_changed_source_evidence(self) -> None:
        forged = self._intake_payload()
        forged["auto_filled_values"] = [
            {
                "field": "summary_copy",
                "value": "伪造标题",
                "source": "jira_issue",
                "reference": "issue.summary",
                "rationale": "尝试使用 Jira 标题补全",
            }
        ]
        path = self._write_json("forged.json", forged)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.assess_intake(
                issue_key=ISSUE_KEY,
                agentic_run_id=RUN_ID,
                input_file=str(path.relative_to(self.workspace_root)),
            )
        self.assertEqual("intake_verified_value_mismatch", captured.exception.code)

        changed = self._intake_payload()
        changed["auto_filled_values"][0]["evidence_sha256"] = "b" * 64
        path = self._write_json("changed.json", changed)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.assess_intake(
                issue_key=ISSUE_KEY,
                agentic_run_id=RUN_ID,
                input_file=str(path.relative_to(self.workspace_root)),
            )
        self.assertEqual("intake_source_evidence_changed", captured.exception.code)

    def test_incomplete_intake_allows_only_one_changed_input_retry(self) -> None:
        payload = self._intake_payload()
        payload["unresolved_information"] = [
            {
                "field": "acceptance_criteria",
                "required": True,
                "reason": "任务卡片尚未给出明确验收标准",
            }
        ]
        first_path = self._write_json("missing-1.json", payload)
        first = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(first_path.relative_to(self.workspace_root)),
        )
        self.assertFalse(first["ready_for_solution"])
        self.assertTrue(first["agentic_next_action"]["retry_gate"]["allowed"])
        self.assertEqual(0, first["intake"]["retry_count"])

        repeated = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(first_path.relative_to(self.workspace_root)),
        )
        self.assertEqual(0, repeated["intake"]["retry_count"])

        payload["unresolved_information"][0]["reason"] = "补充读取后仍缺少可验证的验收标准"
        second_path = self._write_json("missing-2.json", payload)
        second = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(second_path.relative_to(self.workspace_root)),
        )
        self.assertEqual(1, second["intake"]["retry_count"])
        self.assertTrue(second["agentic_next_action"]["stop_workflow"])

        payload["unresolved_information"][0]["reason"] = "再次尝试仍无法补齐验收标准"
        third_path = self._write_json("missing-3.json", payload)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.assess_intake(
                issue_key=ISSUE_KEY,
                agentic_run_id=RUN_ID,
                input_file=str(third_path.relative_to(self.workspace_root)),
            )
        self.assertEqual("task_intake_retry_exhausted", captured.exception.code)

    def test_solution_levels_route_only_to_design_or_risk_gates(self) -> None:
        intake_digest = self._assessed_intake()
        cases = {
            "L1": None,
            "L2": "external_side_effect",
            "L3": "public_contract_change",
            "L4": "capability_gap",
        }
        results: dict[str, dict[str, object]] = {}
        for level, flag in cases.items():
            with self.subTest(level=level):
                path = self._write_solution(f"solution-{level}.json", intake_digest, flag)
                result = self.service.classify_solution(
                    issue_key=ISSUE_KEY,
                    agentic_run_id=RUN_ID,
                    input_file=str(path.relative_to(self.workspace_root)),
                )
                self.assertEqual(level, result["solution_level"])
                journal_path = (
                    self.workspace_root
                    / ".agentic-ops"
                    / "tasks"
                    / ISSUE_KEY
                    / "journal.ndjson"
                )
                last_event = json.loads(
                    journal_path.read_text(encoding="utf-8").splitlines()[-1]
                )
                self.assertEqual(
                    "blocked" if level == "L4" else "completed",
                    last_event["status"],
                )
                results[level] = result
        self.assertEqual(
            "review_task_design",
            results["L1"]["agentic_next_action"]["action"],
        )
        self.assertEqual(
            "decide_solution_risk",
            results["L2"]["agentic_next_action"]["action"],
        )
        self.assertEqual(
            "revise_design_and_reassess",
            results["L3"]["agentic_next_action"]["action"],
        )
        self.assertFalse(results["L3"]["agentic_next_action"]["stop_workflow"])
        self.assertTrue(results["L4"]["agentic_next_action"]["stop_workflow"])

    def test_new_source_head_invalidates_current_intake(self) -> None:
        intake_digest = self._assessed_intake()
        (self.source_root / "README.md").write_text(
            "# 任务证据\n\n目标分支发生变化。\n", encoding="utf-8"
        )
        self._git("add", "README.md")
        self._git("commit", "-m", "变更事实基线")
        path = self._write_solution("stale.json", intake_digest, None)
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service.classify_solution(
                issue_key=ISSUE_KEY,
                agentic_run_id=RUN_ID,
                input_file=str(path.relative_to(self.workspace_root)),
            )
        self.assertEqual("task_intake_source_changed", captured.exception.code)

    def test_execution_plan_normalizes_maven_without_new_user_gate(self) -> None:
        intake_digest = self._assessed_intake()
        path = self._write_solution("solution-execution.json", intake_digest, None)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["execution_plan"] = {
            "change_repository": "tapdata/tapdata",
            "verification": [
                {
                    "id": "mysql-unit",
                    "command": [
                        "mvn",
                        "-pl",
                        "connectors/mysql-connector",
                        "-am",
                        "-Dtest=MysqlConnectorTest",
                        "test",
                    ],
                    "working_directory": ".",
                    "timeout_seconds": 600,
                }
            ],
            "review_summary": "仅修改已确认源码和测试，并执行 MySQL 单元测试。",
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        result = self.service.classify_solution(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(path.relative_to(self.workspace_root)),
        )

        execution = result["solution"]["execution_plan"]
        command = execution["verification"][0]["command"]
        self.assertIn("--batch-mode", command)
        self.assertIn("--offline", command)
        self.assertEqual(1, len(execution["normalization_changes"]))
        self.assertEqual(
            "prepare_task_run_manifest",
            result["agentic_next_action"]["action"],
        )
        self.assertFalse(result["agentic_next_action"]["requires_authorization"])

    def test_multi_repository_source_binds_evidence_and_solution_head(self) -> None:
        connector_root = self.workspace_root.parent / "connector-source"
        connector_root.mkdir()
        self._git_at(connector_root, "init", "-b", "develop")
        self._git_at(connector_root, "config", "user.name", "Harsen Test Bot")
        self._git_at(
            connector_root, "config", "user.email", "harsen@example.test"
        )
        connector_evidence = "# Connector\n\nMySQL 修复位于连接器仓库。\n"
        (connector_root / "CONNECTOR.md").write_text(
            connector_evidence, encoding="utf-8"
        )
        self._git_at(connector_root, "add", "CONNECTOR.md")
        self._git_at(connector_root, "commit", "-m", "初始化连接器仓库")

        source_path = Path(str(self.context["source_context_path"]))
        source = json.loads(source_path.read_text(encoding="utf-8"))
        workspace_defaults = dict(source["workspace_defaults"])
        workspace_defaults["repository_scope_revision"] = 7
        workspace_defaults["source_roots"] = [
            {
                "repository": "tapdata/tapdata",
                "source_root": str(self.source_root),
                "head_sha": self._git("rev-parse", "HEAD"),
                "task_branch": "main",
            },
            {
                "repository": "tapdata/tapdata-connectors",
                "source_root": str(connector_root),
                "head_sha": self._git_at(connector_root, "rev-parse", "HEAD"),
                "task_branch": "develop",
            },
        ]
        self.service.record_source_context(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            issue=source["issue"],
            workspace_defaults=workspace_defaults,
            project_profile=source["project_profile"],
        )

        payload = self._intake_payload()
        payload["auto_filled_values"][0] = {
            "field": "change_repository",
            "value": "tapdata/tapdata-connectors",
            "source": "business_source_code",
            "reference": "tapdata/tapdata-connectors::CONNECTOR.md",
            "evidence_sha256": hashlib.sha256(
                connector_evidence.encode("utf-8")
            ).hexdigest(),
            "rationale": "连接器源码证据明确指出 MySQL 修复所在仓库",
        }
        assessed = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(
                self._write_json("multi-source-intake.json", payload).relative_to(
                    self.workspace_root
                )
            ),
        )
        revision = assessed["intake"]["source_revision"]
        self.assertEqual(7, revision["repository_scope_revision"])
        self.assertEqual(
            ["tapdata/tapdata", "tapdata/tapdata-connectors"],
            [item["repository"] for item in revision["repositories"]],
        )

        solution_path = self._write_solution(
            "multi-source-solution.json", str(assessed["intake_digest"]), None
        )
        solution = json.loads(solution_path.read_text(encoding="utf-8"))
        solution["scope"] = {
            "included": [
                "tapdata/tapdata-connectors::connectors/mysql-connector/**",
                "tapdata/tapdata::build/version.properties",
            ],
            "excluded": ["tapdata/tapdata::dist/**"],
        }
        solution["execution_plan"] = {
            "change_repositories": [
                "tapdata/tapdata-connectors",
                "tapdata/tapdata",
            ],
            "verification": [
                {
                    "id": "connector-unit",
                    "repository": "tapdata/tapdata-connectors",
                    "command": ["mvn", "-Dtest=MysqlConnectorTest", "test"],
                    "working_directory": ".",
                    "timeout_seconds": 600,
                },
                {
                    "id": "product-build",
                    "repository": "tapdata/tapdata",
                    "command": ["mvn", "test"],
                    "working_directory": ".",
                    "timeout_seconds": 900,
                }
            ],
            "review_summary": "分别修改连接器和产品版本文件，并逐仓验证后推进到代码审查。",
        }
        solution_path.write_text(
            json.dumps(solution, ensure_ascii=False), encoding="utf-8"
        )
        classified = self.service.classify_solution(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(solution_path.relative_to(self.workspace_root)),
        )
        self.assertEqual(
            self._git_at(connector_root, "rev-parse", "HEAD"),
            classified["solution"]["head_sha"],
        )
        self.assertEqual(
            {
                "tapdata/tapdata-connectors": self._git_at(
                    connector_root, "rev-parse", "HEAD"
                ),
                "tapdata/tapdata": self._git("rev-parse", "HEAD"),
            },
            classified["solution"]["repository_heads"],
        )

        current_source = json.loads(source_path.read_text(encoding="utf-8"))
        with self.assertRaises(RuntimeErrorResult) as captured:
            self.service._source_evidence_target(current_source, "CONNECTOR.md")
        self.assertEqual(
            "intake_evidence_reference_invalid", captured.exception.code
        )

    def _assessed_intake(self) -> str:
        path = self._write_intake("confirmed-intake.json")
        assessed = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(path.relative_to(self.workspace_root)),
        )
        digest = str(assessed["intake_digest"])
        return digest

    def _write_intake(self, name: str) -> Path:
        return self._write_json(name, self._intake_payload())

    def _intake_payload(self) -> dict[str, object]:
        content = (self.source_root / "README.md").read_text(encoding="utf-8")
        return {
            "schema_version": 1,
            "auto_filled_values": [
                {
                    "field": "target_branch",
                    "value": "develop",
                    "source": "business_source_code",
                    "reference": "README.md",
                    "evidence_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "rationale": "业务源码文档明确给出默认目标分支",
                }
            ],
            "unresolved_information": [],
            "assumptions": [
                {
                    "statement": "本次只处理任务卡片明确范围",
                    "impact": "范围变化时必须重新执行准入分析",
                }
            ],
            "impacts": [
                {
                    "area": "研发流程",
                    "description": "增加确定性准入与方案门禁",
                    "risk": "low",
                }
            ],
        }

    def _write_solution(
        self, name: str, intake_digest: str, enabled_flag: str | None
    ) -> Path:
        flags = {
            key: key == enabled_flag
            for key in (
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
        }
        evidence = (
            {enabled_flag: ["任务方案证据明确命中该风险标志"]}
            if enabled_flag
            else {}
        )
        return self._write_json(
            name,
            {
                "schema_version": 1,
                "intake_digest": intake_digest,
                "proposed_solution": "在 Runtime 中增加确定性准入与方案分级门禁。",
                "scope": {
                    "included": ["src/task-gate.py"],
                    "excluded": ["src/task-transfer.py"],
                },
                "risk_flags": flags,
                "classification_evidence": evidence,
                "residual_risks": ["正式任务接管仍是后续能力缺口"],
            },
        )

    def _write_json(self, name: str, payload: object) -> Path:
        path = self.workspace_root / "inputs" / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )
        return path

    def _git(self, *arguments: str) -> str:
        return self._git_at(self.source_root, *arguments)

    def _git_at(self, root: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
