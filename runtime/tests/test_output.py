from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

from agentic_ops.output import RuntimeErrorResult, failure, success, write_json


class OutputTest(unittest.TestCase):
    def test_success_has_stable_envelope(self) -> None:
        result = success("workspace_inspect", value="ok")
        self.assertEqual(True, result["ok"])
        self.assertEqual("completed", result["status"])
        self.assertEqual(True, result["retry_safe"])
        self.assertEqual("ok", result["value"])

    def test_failure_has_chinese_human_action(self) -> None:
        result = failure(
            "task_init",
            RuntimeErrorResult(
                code="test_blocked",
                message="测试阻断",
                status="blocked",
                exit_code=2,
                required_human_action="请检查测试输入",
            ),
        )
        self.assertEqual("test_blocked", result["code"])
        self.assertEqual("请检查测试输入", result["required_human_action"])

    def test_failure_can_include_structured_gate_details(self) -> None:
        result = failure(
            "story_impact",
            RuntimeErrorResult(
                code="maintenance_story_impacted",
                message="故事受影响",
                status="blocked",
                exit_code=2,
                details={"impact_id": "abc", "impacted_story_ids": ["PM-007"]},
            ),
        )
        self.assertEqual("abc", result["impact_id"])
        self.assertEqual(["PM-007"], result["impacted_story_ids"])

    def test_writer_outputs_exactly_one_json_object(self) -> None:
        stream = io.StringIO()
        with redirect_stdout(stream):
            write_json(success("test"))
        lines = stream.getvalue().splitlines()
        self.assertEqual(1, len(lines))
        self.assertEqual("test", json.loads(lines[0])["operation"])
