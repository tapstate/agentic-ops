from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ao_work.output import RuntimeErrorResult
from ao_work.workflow_query import execute_workflow_query


class WorkflowQueryTest(unittest.TestCase):
    def test_returns_read_only_process_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process_root = root / "developer/standards/contracts/processes"
            process_root.mkdir(parents=True)
            (process_root / "development-change-v2.yaml").write_text(
                "process_id: development_change_v2\n"
                "stages:\n"
                "  - id: implementation\n"
                "    review_gate: null\n"
                "  - id: design_review\n"
                "    review_gate: development_engineer_design_review\n",
                encoding="utf-8",
            )

            result = execute_workflow_query(
                root,
                process_id="development_change_v2",
                current_step_id="implementation",
            )

        self.assertFalse(result["executable"])
        self.assertEqual("workflow-query/v1", result["schema_version"])
        self.assertEqual("action", result["steps"][0]["kind"])
        self.assertEqual("decision", result["steps"][1]["kind"])

    def test_rejects_unknown_process_without_path_interpretation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeErrorResult) as captured:
                execute_workflow_query(
                    Path(temporary),
                    process_id="../../outside",
                    current_step_id="implementation",
                )

        self.assertEqual("workflow_query_process_invalid", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
