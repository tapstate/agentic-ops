#!/usr/bin/env python3
"""TapData 项目分支对齐脚本的无网络合同测试。"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "projects" / "tapdata" / "scripts" / "align_branches.py"
SPEC = importlib.util.spec_from_file_location("tapdata_align_branches", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
align = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = align
SPEC.loader.exec_module(align)


class TapDataBranchAlignmentTest(unittest.TestCase):
    def test_remote_branch_listing_preserves_slashes_for_tap_marker_matching(self):
        completed = __import__("subprocess").CompletedProcess(
            ["git"], 0,
            stdout="a1 refs/heads/harsen/TAP-123/develop\nb2 refs/heads/release-v3.8.0\n",
            stderr="",
        )
        with mock.patch.object(align, "command", return_value=completed):
            branches = align.remote_branches("test-origin")

        self.assertEqual(["harsen/TAP-123/develop", "release-v3.8.0"], branches)

    def test_task_branch_prefers_exact_same_name_over_jira_marker_guessing(self):
        repositories = {
            "tapdata/tapdata": {"origin": "product-origin"},
            "tapdata/tapdata-enterprise": {"origin": "enterprise-origin"},
        }
        rules = {
            "product_repository": "tapdata/tapdata",
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": [],
            "independent_repositories": [],
            "same_name_repositories": ["tapdata/tapdata-enterprise"],
            "plugin_release_repositories": [],
        }
        with mock.patch.object(align, "remote_branch_status", return_value=("exists", "sha")):
            target, resolution, reason = align.derived_target(
                "tapdata/tapdata-enterprise",
                "feature/TAP-123-fix",
                repositories,
                rules,
                None,
                {},
                {},
            )

        self.assertEqual("feature/TAP-123-fix", target)
        self.assertEqual("same_name", resolution)
        self.assertIn("完全同名", reason)

    def test_task_branch_does_not_use_ambiguous_tap_marker_fallback(self):
        repository = "tapdata/tapdata-enterprise"
        repositories = {
            "tapdata/tapdata": {"origin": "product-origin"},
            repository: {"origin": "enterprise-origin"},
        }
        rules = {
            "product_repository": "tapdata/tapdata",
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": [],
            "independent_repositories": [],
            "same_name_repositories": [repository],
            "plugin_release_repositories": [],
        }
        with mock.patch.object(align, "remote_branch_status", return_value=("missing", "")):
            target, resolution, reason = align.derived_target(
                repository,
                "feature/TAP-123-fix",
                repositories,
                rules,
                None,
                {},
                {},
            )

        self.assertIsNone(target)
        self.assertEqual("unresolved", resolution)
        self.assertIn("完全同名", reason)

        explicit, explicit_resolution, _ = align.derived_target(
            repository,
            "feature/TAP-123-fix",
            repositories,
            rules,
            None,
            {repository: "reviewed/TAP-123/fix"},
            {},
        )
        self.assertEqual("reviewed/TAP-123/fix", explicit)
        self.assertEqual("explicit", explicit_resolution)

    def test_current_matrix_covers_catalog_and_reports_remote_status(self):
        config, repositories = align.load_configuration(ROOT)
        with mock.patch.object(align, "remote_branch_status", return_value=("exists", "sha-current")):
            rows = align.build_plan("current", config, repositories)

        self.assertEqual(set(repositories), {row["repository"] for row in rows})
        self.assertTrue(all(row["resolution"] == "exact_profile" for row in rows))
        self.assertTrue(all(row["remote_status"] == "exists" for row in rows))
        targets = {row["repository"]: row["target_branch"] for row in rows}
        self.assertEqual("main", targets["tapdata/tapdata-license"])
        self.assertEqual("release-v5.5.0", targets["tapdata/hazelcast"])

    def test_release_derives_each_plugin_repository_independently(self):
        repositories = {
            "tapdata/tapdata": {"origin": "tapdata-origin", "domains": ["product"]},
            "tapdata/tapdata-common-lib": {"origin": "common-origin", "domains": ["product"]},
            "tapdata/tapdata-connectors": {"origin": "connectors-origin", "domains": ["product"]},
            "tapdata/tapdata-connectors-enterprise": {"origin": "enterprise-connectors-origin", "domains": ["product"]},
        }
        rules = {
            "product_repository": "tapdata/tapdata",
            "linked_repositories": list(repositories),
            "same_name_repositories": [],
            "plugin_release_repositories": [
                "tapdata/tapdata-common-lib",
                "tapdata/tapdata-connectors",
                "tapdata/tapdata-connectors-enterprise",
            ],
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": [],
            "independent_repositories": [],
        }
        config = {"versions": {}, "derivation": rules}

        def release_for(origin, _minimum):
            return {
                "common-origin": "release-v1.2.6",
                "connectors-origin": "release-v2.0.8",
                "enterprise-connectors-origin": "release-v2.0.9",
            }[origin]

        with (
            mock.patch.object(align, "remote_branch_status", return_value=("exists", "sha")),
            mock.patch.object(align, "plugin_release", return_value=("release-v1.2.6", "PluginKit 1.2.6")),
            mock.patch.object(align, "first_release_ge", side_effect=release_for),
        ):
            rows = align.build_plan("release-v4.19.0", config, repositories, "/pool")

        targets = {row["repository"]: row["target_branch"] for row in rows}
        self.assertEqual("release-v1.2.6", targets["tapdata/tapdata-common-lib"])
        self.assertEqual("release-v2.0.8", targets["tapdata/tapdata-connectors"])
        self.assertEqual("release-v2.0.9", targets["tapdata/tapdata-connectors-enterprise"])

    def test_missing_source_pool_fails_closed_only_for_plugin_derived_repository(self):
        repositories = {
            "tapdata/tapdata": {"origin": "tapdata-origin", "domains": ["product"]},
            "tapdata/tapdata-connectors": {"origin": "connectors-origin", "domains": ["product"]},
        }
        rules = {
            "product_repository": "tapdata/tapdata",
            "linked_repositories": list(repositories),
            "same_name_repositories": [],
            "plugin_release_repositories": ["tapdata/tapdata-connectors"],
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": [],
            "independent_repositories": [],
        }
        config = {"versions": {}, "derivation": rules}
        with mock.patch.object(align, "remote_branch_status", return_value=("exists", "sha")):
            rows = align.build_plan("release-v4.19.0", config, repositories)

        connector = next(row for row in rows if row["repository"] == "tapdata/tapdata-connectors")
        self.assertIsNone(connector["target_branch"])
        self.assertEqual("unresolved", connector["resolution"])
        self.assertEqual("unresolved", connector["remote_status"])

    def test_environment_branch_falls_back_after_exact_same_name_lookup(self):
        rules = {
            "product_repository": "tapdata/tapdata",
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": ["tapdata/tapdata-application"],
            "independent_repositories": ["tapdata/t-layer3-test", "tapdata/docs"],
            "same_name_repositories": [],
            "plugin_release_repositories": ["tapdata/tapdata-common-lib"],
            "environment_fallback_branches": {
                "tapdata/tapdata-application": "main",
                "tapdata/t-layer3-test": "develop",
            },
        }
        refs = {
            "tapdata/tapdata": {"fix-xxx": "a"},
            "tapdata/tapdata-common-lib": {"fix-xxx": "b"},
            "tapdata/tapdata-application": {"main": "c"},
            "tapdata/t-layer3-test": {"develop": "d"},
            "tapdata/docs": {"main": "e"},
        }
        same = align.environment_target(
            "tapdata/tapdata-common-lib", "fix-xxx", rules, refs, Path("/tmp/product"), {}
        )
        application = align.environment_target(
            "tapdata/tapdata-application", "fix-xxx", rules, refs, Path("/tmp/product"), {}
        )
        tests = align.environment_target(
            "tapdata/t-layer3-test", "fix-xxx", rules, refs, Path("/tmp/product"), {}
        )
        unchanged = align.environment_target(
            "tapdata/docs", "fix-xxx", rules, refs, Path("/tmp/product"), {}
        )
        self.assertEqual(("fix-xxx", "same_name"), same[:2])
        self.assertEqual(("main", "fallback"), application[:2])
        self.assertEqual(("develop", "fallback"), tests[:2])
        self.assertEqual((None, "unchanged"), unchanged[:2])


if __name__ == "__main__":
    unittest.main()
