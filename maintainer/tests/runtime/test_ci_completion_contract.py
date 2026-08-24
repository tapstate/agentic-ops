from __future__ import annotations

import copy
import unittest

from ao_maint.integration.task_to_pr import _validate_ci_completion
from ao_maint.output import RuntimeErrorResult


HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
BLOB_SHA = "c" * 40
TIMESTAMP = "2026-08-24T00:00:00+00:00"


class CiCompletionContractTest(unittest.TestCase):
    @staticmethod
    def not_required() -> dict[str, object]:
        return {
            "provider": "github-actions",
            "head_sha": HEAD_SHA,
            "attempt_id": "d" * 24,
            "ci_status": "not_required",
            "ci_requirement": {
                "status": "not_required",
                "source": "github_pr",
                "base_sha": BASE_SHA,
                "reason": "base_has_no_github_actions_workflows",
                "workflow_files": [],
            },
            "started_at": TIMESTAMP,
            "execution_started_at": None,
            "finished_at": TIMESTAMP,
            "start_deadline_at": None,
            "completion_deadline_at": None,
            "required_checks": [],
            "workflow_runs": [],
            "artifact": None,
            "report": None,
            "remediations": [],
            "remediation_attempts_used": 0,
            "remediation_attempts_remaining": 2,
        }

    @classmethod
    def passed(cls) -> dict[str, object]:
        value = cls.not_required()
        value.update(
            {
                "ci_status": "passed",
                "ci_requirement": {
                    "status": "required",
                    "source": "github_pr",
                    "base_sha": BASE_SHA,
                    "reason": "configured_workflows_trigger_for_pr_head",
                    "workflow_files": [
                        {
                            "path": ".github/workflows/integration.yml",
                            "blob_sha": BLOB_SHA,
                            "name": "Integration Tests",
                            "triggers": ["pull_request"],
                            "head_trigger": True,
                        }
                    ],
                },
                "execution_started_at": TIMESTAMP,
                "start_deadline_at": TIMESTAMP,
                "completion_deadline_at": TIMESTAMP,
                "required_checks": [
                    {
                        "name": "integration-test",
                        "status": "COMPLETED",
                        "conclusion": "SUCCESS",
                    }
                ],
            }
        )
        return value

    def test_accepts_github_bound_passed_and_not_required_terminal_evidence(self) -> None:
        self.assertEqual("passed", _validate_ci_completion(self.passed())["ci_status"])
        self.assertEqual(
            "not_required",
            _validate_ci_completion(self.not_required())["ci_status"],
        )

    def test_rejects_not_required_with_ci_execution_facts(self) -> None:
        value = self.not_required()
        value["workflow_runs"] = [{"database_id": 1}]
        with self.assertRaises(RuntimeErrorResult) as captured:
            _validate_ci_completion(value)
        self.assertEqual("integration_result_evidence_invalid", captured.exception.code)

    def test_rejects_status_and_requirement_mismatch(self) -> None:
        value = copy.deepcopy(self.passed())
        value["ci_requirement"]["status"] = "not_required"  # type: ignore[index]
        with self.assertRaises(RuntimeErrorResult) as captured:
            _validate_ci_completion(value)
        self.assertEqual("integration_result_evidence_invalid", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
