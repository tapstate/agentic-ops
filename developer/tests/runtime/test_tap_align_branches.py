from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "standards"
    / "projects"
    / "tapdata"
    / "scripts"
    / "tap_align_branches.py"
)
SPEC = importlib.util.spec_from_file_location("tap_align_branches", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
tap_align_branches = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tap_align_branches)


class TapAlignBranchesMachinePlanTest(unittest.TestCase):
    def test_json_plan_only_contains_requested_domain_repositories(self) -> None:
        rows = [
            {
                "repo": "tapdata",
                "current": "develop",
                "target": "release-v3.8.0",
                "action": "switch",
                "reason": "test",
                "dirty": "clean",
            },
            {
                "repo": "tapdata-common-lib",
                "current": "main",
                "target": "release-v1.2.6",
                "action": "switch",
                "reason": "pluginKit inferred",
                "dirty": "clean",
            },
        ]
        with tempfile.TemporaryDirectory() as temporary:
            stdout = io.StringIO()
            with (
                mock.patch.object(tap_align_branches, "plan_rows", return_value=rows) as planned,
                redirect_stdout(stdout),
            ):
                code = tap_align_branches.main(
                    [
                        "--root",
                        temporary,
                        "plan",
                        "release-v3.8.0",
                        "--no-fetch",
                        "--repositories",
                        "tapdata,tapdata-common-lib",
                        "--json",
                    ]
                )

        self.assertEqual(0, code)
        self.assertEqual(rows, json.loads(stdout.getvalue()))
        self.assertEqual(
            ["tapdata", "tapdata-common-lib"],
            planned.call_args.kwargs["repositories"],
        )

    def test_unknown_requested_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(tap_align_branches, "branch_exists", return_value=True):
                with self.assertRaises(tap_align_branches.AlignError):
                    tap_align_branches.plan_rows(
                        "develop",
                        "",
                        "",
                        Path(temporary),
                        "origin",
                        repositories=["not-supported"],
                    )


if __name__ == "__main__":
    unittest.main()
