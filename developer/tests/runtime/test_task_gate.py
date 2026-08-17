from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ao_work.output import RuntimeErrorResult
from ao_work.task_gate import TaskGateService
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
                    "schema_version": 3,
                    "workplane": "developer",
                    "agent_id": "harsen-mini-test-bot",
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
                "description": "根据任务卡片和源码补齐准入信息。",
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

    def test_intake_assess_and_confirm_bind_full_current_summary(self) -> None:
        path = self._write_intake("intake.json")
        assessed = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(path.relative_to(self.workspace_root)),
        )
        self.assertTrue(assessed["ready_for_confirmation"])
        self.assertEqual("confirm_task_intake", assessed["agentic_next_action"]["action"])
        self.assertEqual(
            {"owner", "target_repo", "target_branch"},
            {item["field"] for item in assessed["intake"]["auto_filled_values"]},
        )
        self.assertTrue(assessed["intake"]["known_facts"])
        digest = assessed["intake_digest"]
        reference = f"user-confirmation:{ISSUE_KEY}:{RUN_ID}:{digest}"
        confirmed = self.service.confirm_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            intake_digest=digest,
            confirmed_by="harsen",
            authorization_reference=reference,
        )
        self.assertEqual(digest, confirmed["intake_confirmation"]["intake_digest"])
        self.assertEqual(
            "session_user_confirmation_attestation",
            confirmed["intake_confirmation"]["evidence_basis"],
        )
        self.assertFalse(
            confirmed["intake_confirmation"]["independent_identity_readback"]
        )
        self.assertEqual(
            "prepare_and_classify_solution",
            confirmed["agentic_next_action"]["action"],
        )

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
        self.assertFalse(first["ready_for_confirmation"])
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

    def test_solution_levels_are_computed_and_only_l2_can_be_confirmed(self) -> None:
        intake_digest = self._confirmed_intake()
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
                results[level] = result
        self.assertEqual(
            "perform_formal_task_takeover",
            results["L1"]["agentic_next_action"]["action"],
        )
        self.assertEqual(
            "confirm_l2_solution",
            results["L2"]["agentic_next_action"]["action"],
        )
        self.assertTrue(results["L3"]["agentic_next_action"]["stop_workflow"])
        self.assertTrue(results["L4"]["agentic_next_action"]["stop_workflow"])

        l2_path = self._write_solution(
            "solution-L2-final.json", intake_digest, "external_side_effect"
        )
        l2 = self.service.classify_solution(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(l2_path.relative_to(self.workspace_root)),
        )
        digest = str(l2["solution_digest"])
        confirmed = self.service.confirm_solution(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            solution_digest=digest,
            confirmed_by="harsen",
            authorization_reference=(
                f"user-confirmation:{ISSUE_KEY}:{RUN_ID}:{digest}"
            ),
        )
        self.assertEqual(
            "perform_formal_task_takeover",
            confirmed["agentic_next_action"]["action"],
        )

    def test_new_source_head_invalidates_confirmed_intake(self) -> None:
        intake_digest = self._confirmed_intake()
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

    def _confirmed_intake(self) -> str:
        path = self._write_intake("confirmed-intake.json")
        assessed = self.service.assess_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            input_file=str(path.relative_to(self.workspace_root)),
        )
        digest = str(assessed["intake_digest"])
        self.service.confirm_intake(
            issue_key=ISSUE_KEY,
            agentic_run_id=RUN_ID,
            intake_digest=digest,
            confirmed_by="harsen",
            authorization_reference=(
                f"user-confirmation:{ISSUE_KEY}:{RUN_ID}:{digest}"
            ),
        )
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
                "confirmed_intake_digest": intake_digest,
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
        completed = subprocess.run(
            ["git", "-C", str(self.source_root), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip()


if __name__ == "__main__":
    unittest.main()
