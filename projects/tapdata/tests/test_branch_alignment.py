#!/usr/bin/env python3
"""TapData 模块根目录分支解析的无网络合同测试。"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
            binding.write_text(json.dumps({"repository_pool": {"root": "/pool"}}), encoding="utf-8")
            root, source = align.resolve_tapdata_root(None, workspace / "nested")

        self.assertEqual(Path("/pool/tapdata"), root)
        self.assertIn("workspace.json", source)

    def test_execution_directory_is_only_fallback_not_user_home(self):
        root, source = align.resolve_tapdata_root(None, "/work/current")
        self.assertEqual(Path("/work/current"), root)
        self.assertEqual("cwd", source)

    def test_short_command_maps_first_argument_to_tapdata_version(self):
        self.assertEqual(
            ["show", "--version", "release-v4.21.0", "--json"],
            align.normalize_argv(["release-v4.21.0", "--json"]),
        )

    def test_explicit_tapdata_root_contains_module_repositories_directly(self):
        self.assertEqual(Path("/tapdata-root/tapdata"), align.module_repository("/tapdata-root", "tapdata/tapdata"))
        self.assertEqual(Path("/tapdata-root/tapdata-web"), align.module_repository("/tapdata-root", "tapdata/tapdata-web"))

    def test_main_repository_is_validated_before_other_module_repositories(self):
        repositories = {"tapdata/docs": {}, "tapdata/tapdata": {}}
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(align.AlignmentError, "TapData 模块根目录缺少主仓.*tapdata/tapdata"):
                align.refresh_branch_cache(temporary, repositories, "tapdata/tapdata", "never")

    def test_refresh_policy_distinguishes_always_auto_and_never(self):
        fresh = {"last_refresh_epoch": 995}
        stale = {"last_refresh_epoch": 600}
        missing = {"last_refresh_epoch": None}

        self.assertTrue(align.refresh_required("always", fresh, 1_000))
        self.assertFalse(align.refresh_required("never", missing, 1_000))
        self.assertFalse(align.refresh_required("auto", fresh, 1_000))
        self.assertTrue(align.refresh_required("auto", stale, 1_000))
        self.assertTrue(align.refresh_required("auto", missing, 1_000))

    def test_json_includes_refresh_freshness_and_timing_while_progress_uses_stderr(self):
        config = {"derivation": {"product_repository": "tapdata/tapdata"}}
        rows = [{"repository": "tapdata/tapdata", "refs": {"freshness": "cached_local_refs", "last_refresh_at": None}}]
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(align, "load_configuration", return_value=(config, {"tapdata/tapdata": {}})), \
             mock.patch.object(align, "resolve_tapdata_root", return_value=(Path("/tapdata-root"), "explicit")), \
             mock.patch.object(align, "refresh_branch_cache", return_value=({}, {}, 0.25)), \
             mock.patch.object(align, "build_plan", return_value=rows), \
             redirect_stdout(output), redirect_stderr(errors):
            code = align.main(["show", "--version", "main", "--refresh", "never", "--json"])

        document = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("never", document["refresh"]["mode"])
        self.assertEqual(0.25, document["timing_seconds"]["fetch"])
        self.assertEqual("cached_local_refs", document["rows"][0]["refs"]["freshness"])
        self.assertIn("开始解析", errors.getvalue())
        self.assertIn("解析完成", errors.getvalue())

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

    def test_fixed_fallback_and_unchanged_repositories(self):
        rules = {
            "product_repository": "tapdata/tapdata",
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": ["tapdata/tapdata-application"],
            "fixed_branches": {"tapdata/hazelcast": "release-v5.5.0"},
            "independent_repositories": ["tapdata/t-layer3-test", "tapdata/docs"],
            "same_name_repositories": [],
            "plugin_release_repositories": [],
            "display_fallback_branches": {"tapdata/tapdata-application": "main", "tapdata/t-layer3-test": "develop"},
        }
        refs = {"tapdata/tapdata-application": {"main": "a"}, "tapdata/hazelcast": {"release-v5.5.0": "b"}, "tapdata/t-layer3-test": {"develop": "c"}, "tapdata/docs": {"main": "d"}}
        application = align.derived_target("tapdata/tapdata-application", "fix-xxx", rules, refs, Path("/pool/tapdata/tapdata"), {})
        hazelcast = align.derived_target("tapdata/hazelcast", "fix-xxx", rules, refs, Path("/pool/tapdata/tapdata"), {})
        tests = align.derived_target("tapdata/t-layer3-test", "fix-xxx", rules, refs, Path("/pool/tapdata/tapdata"), {})
        unchanged = align.derived_target("tapdata/docs", "fix-xxx", rules, refs, Path("/pool/tapdata/tapdata"), {})

        self.assertEqual(("main", "fixed"), application[:2])
        self.assertEqual(("release-v5.5.0", "fixed"), hazelcast[:2])
        self.assertEqual(("develop", "fallback"), tests[:2])
        self.assertEqual((None, "unchanged"), unchanged[:2])

    def test_current_matrix_is_verified_against_local_refs(self):
        config, repositories = align.load_configuration(ROOT)
        paths = {repository: Path("/pool") for repository in repositories}
        refs = {repository: {config["versions"]["current"]["branches"][repository]: "sha-%s" % index} for index, repository in enumerate(repositories)}
        with mock.patch.object(align, "local_remote_refs", side_effect=lambda path: refs[next(name for name, item in paths.items() if item == path)]):
            # 使用不同 Path 对象避免上面按值相等的映射歧义。
            paths = {repository: Path("/pool/%s" % index) for index, repository in enumerate(repositories)}
            ref_states = {
                repository: {
                    "freshness": "cached_local_refs",
                    "last_refresh_at": "2026-09-03T20:00:00+0800",
                    "last_refresh_evidence": "FETCH_HEAD_mtime",
                    "fetch_duration_seconds": 0.0,
                }
                for repository in repositories
            }
            rows = align.build_plan("current", config, repositories, paths, ref_states)

        self.assertEqual(set(repositories), {row["repository"] for row in rows})
        self.assertTrue(all(row["target_status"] == "exists" for row in rows))
        self.assertTrue(all(row["refs"]["freshness"] == "cached_local_refs" for row in rows))


if __name__ == "__main__":
    unittest.main()
