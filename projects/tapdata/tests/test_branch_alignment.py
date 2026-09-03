#!/usr/bin/env python3
"""TapData Source Pool 分支解析的无网络合同测试。"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
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
    def test_workspace_binding_is_default_before_execution_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            binding = workspace / ".agenticops" / "workspace.json"
            binding.parent.mkdir(parents=True)
            binding.write_text(json.dumps({"repository_pool": {"root": "/pool/tapdata"}}), encoding="utf-8")
            root, source = align.resolve_source_pool(None, workspace / "nested")

        self.assertEqual(Path("/pool/tapdata"), root)
        self.assertIn("workspace.json", source)

    def test_execution_directory_is_only_fallback_not_user_home(self):
        root, source = align.resolve_source_pool(None, "/work/current")
        self.assertEqual(Path("/work/current"), root)
        self.assertEqual("cwd", source)

    def test_short_command_maps_first_argument_to_tapdata_version(self):
        self.assertEqual(
            ["show", "--version", "release-v4.21.0", "--json"],
            align.normalize_argv(["release-v4.21.0", "--json"]),
        )

    def test_main_repository_is_validated_before_other_pool_repositories(self):
        repositories = {"tapdata/docs": {}, "tapdata/tapdata": {}}
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(align.AlignmentError, "缺少 TapData 主仓.*tapdata/tapdata"):
                align.refresh_branch_cache(temporary, repositories, "tapdata/tapdata")

    def test_plugin_release_uses_loaded_local_refs(self):
        rules = {
            "product_repository": "tapdata/tapdata",
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": [],
            "independent_repositories": [],
            "same_name_repositories": [],
            "plugin_release_repositories": ["tapdata/tapdata-connectors"],
            "display_fallback_branches": {},
        }
        refs = {
            "tapdata/tapdata": {"release-v4.21.0": "product-sha"},
            "tapdata/tapdata-connectors": {"release-v1.2.5": "old", "release-v1.2.6": "new"},
        }
        with mock.patch.object(align, "plugin_release", return_value=("release-v1.2.6", "PluginKit 1.2.6")):
            target, resolution, _ = align.derived_target(
                "tapdata/tapdata-connectors", "release-v4.21.0", rules, refs, Path("/pool/tapdata/tapdata"), {}
            )

        self.assertEqual("release-v1.2.6", target)
        self.assertEqual("plugin_release", resolution)

    def test_display_fallbacks_and_unchanged_repositories(self):
        rules = {
            "product_repository": "tapdata/tapdata",
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": ["tapdata/tapdata-application"],
            "independent_repositories": ["tapdata/t-layer3-test", "tapdata/docs"],
            "same_name_repositories": [],
            "plugin_release_repositories": [],
            "display_fallback_branches": {"tapdata/tapdata-application": "main", "tapdata/t-layer3-test": "develop"},
        }
        refs = {"tapdata/tapdata-application": {"main": "a"}, "tapdata/t-layer3-test": {"develop": "b"}, "tapdata/docs": {"main": "c"}}
        application = align.derived_target("tapdata/tapdata-application", "fix-xxx", rules, refs, Path("/pool/tapdata/tapdata"), {})
        tests = align.derived_target("tapdata/t-layer3-test", "fix-xxx", rules, refs, Path("/pool/tapdata/tapdata"), {})
        unchanged = align.derived_target("tapdata/docs", "fix-xxx", rules, refs, Path("/pool/tapdata/tapdata"), {})

        self.assertEqual(("main", "fixed"), application[:2])
        self.assertEqual(("develop", "fallback"), tests[:2])
        self.assertEqual((None, "unchanged"), unchanged[:2])

    def test_current_matrix_is_verified_against_local_refs(self):
        config, repositories = align.load_configuration(ROOT)
        paths = {repository: Path("/pool") for repository in repositories}
        refs = {repository: {config["versions"]["current"]["branches"][repository]: "sha-%s" % index} for index, repository in enumerate(repositories)}
        with mock.patch.object(align, "local_remote_refs", side_effect=lambda path: refs[next(name for name, item in paths.items() if item == path)]):
            # 使用不同 Path 对象避免上面按值相等的映射歧义。
            paths = {repository: Path("/pool/%s" % index) for index, repository in enumerate(repositories)}
            rows = align.build_plan("current", config, repositories, paths)

        self.assertEqual(set(repositories), {row["repository"] for row in rows})
        self.assertTrue(all(row["target_status"] == "exists" for row in rows))


if __name__ == "__main__":
    unittest.main()
