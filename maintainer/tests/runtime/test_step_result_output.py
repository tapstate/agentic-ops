from __future__ import annotations

import unittest

from ao_maint.output import RuntimeErrorResult, Step, failure, success


class StepResultOutputTest(unittest.TestCase):
    def test_success_has_v2_envelope_and_manual_decision(self) -> None:
        result = success("maintainer_check", repository="tapstate/agentic-ops")

        self.assertEqual("step-result/v2", result["schema_version"])
        self.assertTrue(result["ok"])
        self.assertEqual("succeeded", result["result"]["status"])
        self.assertEqual("decision", result["next_step"]["kind"])
        self.assertTrue(Step.from_mapping(result["next_step"]).requires_human_input)

    def test_failure_has_v2_envelope_and_blocked_step(self) -> None:
        result = failure(
            "maintainer_check",
            RuntimeErrorResult(
                code="blocked",
                message="需要人工确认",
                status="blocked",
                exit_code=2,
                required_human_action="请确认当前维护动作",
            ),
        )

        self.assertFalse(result["ok"])
        self.assertEqual("step-result/v2", result["schema_version"])
        self.assertEqual("resolve_blocked_operation", result["next_step"]["action"])

    def test_legacy_action_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "agentic_next_action"):
            success("maintainer_check", agentic_next_action="legacy")
