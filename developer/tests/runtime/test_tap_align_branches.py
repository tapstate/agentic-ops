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
                        "--remote-only",
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
        self.assertTrue(planned.call_args.kwargs["remote_only"])

    def test_remote_only_plugin_read_never_falls_back_to_stale_local_branch(self) -> None:
        with mock.patch.object(
            tap_align_branches,
            "_git_try",
            side_effect=["", "tapdata.api.verison=1.0.0"],
        ) as git_try:
            content = tap_align_branches._read_plugin_content(
                "develop",
                Path("/tmp/tapdata"),
                "origin",
                remote_only=True,
            )

        self.assertEqual("", content)
        self.assertEqual(1, git_try.call_count)

    def test_remote_only_branch_probe_ignores_local_branch(self) -> None:
        with mock.patch.object(
            tap_align_branches,
            "_git_quiet",
            side_effect=[False],
        ) as git_quiet:
            exists = tap_align_branches.branch_exists(
                "tapdata",
                "develop",
                Path("/tmp/tapdata"),
                "origin",
                remote_only=True,
            )

        self.assertFalse(exists)
        self.assertEqual(1, git_quiet.call_count)
        self.assertIn("refs/remotes/origin/develop", git_quiet.call_args.args)

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

    def test_develop_connectors_do_not_follow_common_lib_branch(self) -> None:
        with (
            mock.patch.object(tap_align_branches, "branch_exists", return_value=True),
            mock.patch.object(tap_align_branches, "current_branch", return_value="main"),
            mock.patch.object(tap_align_branches, "dirty_state", return_value="clean"),
            mock.patch.object(
                tap_align_branches,
                "plugin_release_for",
                return_value="release-v1.2.6",
            ),
            mock.patch.object(
                tap_align_branches,
                "first_release_ge",
                return_value="release-v1.2.6",
            ) as first_release,
        ):
            rows = tap_align_branches.plan_rows(
                "develop",
                "",
                "",
                Path("/tmp/tapdata"),
                "origin",
                repositories=[
                    "tapdata-common-lib",
                    "tapdata-connectors",
                    "tapdata-connectors-enterprise",
                ],
                remote_only=True,
            )

        targets = {row["repo"]: row["target"] for row in rows}
        self.assertEqual("release-v1.2.6", targets["tapdata-common-lib"])
        self.assertEqual("develop", targets["tapdata-connectors"])
        self.assertEqual("develop", targets["tapdata-connectors-enterprise"])
        self.assertEqual(1, first_release.call_count)

    def test_release_connectors_resolve_independently(self) -> None:
        def first_release(repo, *_args, **_kwargs):
            return {
                "tapdata-connectors": "release-v2.0.8",
                "tapdata-connectors-enterprise": "release-v2.0.9",
            }[repo]

        with (
            mock.patch.object(
                tap_align_branches, "branch_exists", return_value=True
            ),
            mock.patch.object(tap_align_branches, "current_branch", return_value="main"),
            mock.patch.object(tap_align_branches, "dirty_state", return_value="clean"),
            mock.patch.object(
                tap_align_branches,
                "plugin_release_for",
                return_value="release-v1.2.6",
            ),
            mock.patch.object(
                tap_align_branches,
                "first_release_ge",
                side_effect=first_release,
            ),
        ):
            rows = tap_align_branches.plan_rows(
                "release-v4.19.0",
                "",
                "",
                Path("/tmp/tapdata"),
                "origin",
                repositories=[
                    "tapdata-connectors",
                    "tapdata-connectors-enterprise",
                ],
                remote_only=True,
            )

        targets = {row["repo"]: row["target"] for row in rows}
        self.assertEqual("release-v2.0.8", targets["tapdata-connectors"])
        self.assertEqual("release-v2.0.9", targets["tapdata-connectors-enterprise"])

    def test_release_connector_blocks_when_plugin_version_is_missing(self) -> None:
        with (
            mock.patch.object(tap_align_branches, "branch_exists", return_value=True),
            mock.patch.object(tap_align_branches, "current_branch", return_value="main"),
            mock.patch.object(tap_align_branches, "dirty_state", return_value="clean"),
            mock.patch.object(tap_align_branches, "plugin_release_for", return_value=""),
        ):
            rows = tap_align_branches.plan_rows(
                "release-v4.22.0",
                "",
                "",
                Path("/tmp/tapdata"),
                "origin",
                repositories=["tapdata-connectors"],
                remote_only=True,
            )

        self.assertEqual("UNRESOLVED", rows[0]["target"])
        self.assertEqual("blocked", rows[0]["action"])


if __name__ == "__main__":
    unittest.main()
