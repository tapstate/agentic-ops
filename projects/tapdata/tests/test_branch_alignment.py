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
    @staticmethod
    def observation(repository, refs=None, selection="catalog_optional", local_status="available", verification="verified", fetch_status="refreshed"):
        return {
            "repository": repository,
            "selection": selection,
            "local": {"status": local_status, "path": "/pool/%s" % repository.rsplit("/", 1)[-1], "error": None},
            "refs": {
                "freshness": "refreshed_during_run" if verification == "verified" else "cached_local_refs",
                "fetch_status": fetch_status,
                "verification": verification,
                "last_refresh_at": "2026-09-03T20:00:00+0800",
                "last_refresh_evidence": "FETCH_HEAD_mtime",
                "fetch_duration_seconds": 0.0,
                "error_kind": None,
                "error": None,
                "prompt": None,
            },
            "_refs": refs or {},
            "_path": Path("/pool/%s" % repository.rsplit("/", 1)[-1]) if local_status == "available" else None,
        }

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

    def test_missing_main_repository_stops_before_any_remote_refresh(self):
        repositories = {"tapdata/tapdata": {}, "tapdata/tapdata-enterprise": {}}
        scope = align.resolve_scope(repositories, "tapdata/tapdata", [])
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(align, "fetch_origin") as fetch:
            observations, elapsed = align.inspect_repositories(temporary, repositories, scope, "always")

        self.assertEqual("missing", observations["tapdata/tapdata"]["local"]["status"])
        self.assertEqual(0.0, elapsed)
        fetch.assert_not_called()

    def test_scope_makes_only_main_and_explicit_repositories_strict(self):
        repositories = {"tapdata/tapdata": {}, "tapdata/tapdata-enterprise": {}, "tapdata/tapdata-web": {}}
        scope = align.resolve_scope(repositories, "tapdata/tapdata", ["tapdata/tapdata-enterprise", "tapdata/tapdata-enterprise"])

        self.assertEqual(["tapdata/tapdata-enterprise"], scope["requested_repositories"])
        self.assertEqual(["tapdata/tapdata", "tapdata/tapdata-enterprise"], scope["required_repositories"])
        with self.assertRaisesRegex(align.AlignmentError, "未登记的目标仓库"):
            align.resolve_scope(repositories, "tapdata/tapdata", ["tapdata/unknown"])

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
        rows = [{
            "repository": "tapdata/tapdata", "selection": "required", "local": {"status": "available"},
            "target_status": "cached_exists", "refs": {"freshness": "cached_local_refs", "last_refresh_at": None, "fetch_status": "not_requested"},
        }]
        output, errors = io.StringIO(), io.StringIO()
        observations = {"tapdata/tapdata": self.observation("tapdata/tapdata", {"main": "sha"}, selection="required")}
        with mock.patch.object(align, "load_configuration", return_value=(config, {"tapdata/tapdata": {}})), \
             mock.patch.object(align, "resolve_tapdata_root", return_value=(Path("/tapdata-root"), "explicit")), \
             mock.patch.object(align, "inspect_repositories", return_value=(observations, 0.25)), \
             mock.patch.object(align, "build_plan", return_value=rows), \
             mock.patch.object(align, "report_outcome", return_value=("partial", [], {"cached_exists": 1})), \
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

    def test_unverified_refs_do_not_trigger_negative_fallback(self):
        rules = {
            "product_repository": "tapdata/tapdata",
            "license_repository": "tapdata/tapdata-license",
            "keep_current_repositories": [],
            "independent_repositories": [],
            "same_name_repositories": ["tapdata/tapdata-enterprise"],
            "plugin_release_repositories": [],
            "display_fallback_branches": {},
        }
        target, resolution, reason = align.derived_target(
            "tapdata/tapdata-enterprise", "fix-xxx", rules,
            {"tapdata/tapdata-enterprise": {}}, Path("/pool/tapdata"), {},
            {"tapdata/tapdata-enterprise": "cached_unverified"},
        )

        self.assertIsNone(target)
        self.assertEqual("unresolved", resolution)
        self.assertIn("未核验", reason)

    def test_fetch_error_category_and_prompt_distinguish_access_from_network(self):
        self.assertEqual("ssh_auth_failed", align.classify_fetch_error("Permission denied (publickey)."))
        self.assertEqual(
            "ssh_auth_failed",
            align.classify_fetch_error("git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository."),
        )
        self.assertEqual("repository_access_denied", align.classify_fetch_error("remote: Repository not found."))
        self.assertEqual("network_unreachable", align.classify_fetch_error("fatal: Could not resolve host: github.com"))
        self.assertEqual("fetch_timeout", align.classify_fetch_error("Git fetch 超时（30s）"))
        self.assertIn("读取权限", align.fetch_prompt("tapdata/tapdata-enterprise", "repository_access_denied"))

    def test_current_matrix_is_verified_against_local_refs(self):
        config, repositories = align.load_configuration(ROOT)
        refs = {repository: {config["versions"]["current"]["branches"][repository]: "sha-%s" % index} for index, repository in enumerate(repositories)}
        observations = {
            repository: self.observation(
                repository, refs[repository],
                selection="required" if repository == "tapdata/tapdata" else "catalog_optional",
            )
            for repository in repositories
        }
        rows = align.build_plan("current", config, repositories, observations)

        self.assertEqual(set(repositories), {row["repository"] for row in rows})
        self.assertTrue(all(row["target_status"] == "verified_exists" for row in rows))
        self.assertTrue(all(row["refs"]["verification"] == "verified" for row in rows))

    def test_optional_missing_repository_is_reported_without_blocking_main(self):
        rows = [
            dict(self.observation("tapdata/tapdata", {"develop": "main-sha"}, selection="required"), target_status="verified_exists"),
            dict(self.observation("tapdata/tapdata-enterprise", local_status="missing"), target_status="not_covered"),
        ]
        scope = {"required_repositories": ["tapdata/tapdata"]}
        outcome, blockers, summary = align.report_outcome(rows, scope, "auto")

        self.assertEqual("partial", outcome)
        self.assertEqual([], blockers)
        self.assertEqual(1, summary["not_covered"])

    def test_explicit_missing_repository_blocks_the_report(self):
        rows = [
            dict(self.observation("tapdata/tapdata", {"develop": "main-sha"}, selection="required"), target_status="verified_exists"),
            dict(self.observation("tapdata/tapdata-enterprise", selection="required", local_status="missing"), target_status="unavailable"),
        ]
        scope = {"required_repositories": ["tapdata/tapdata", "tapdata/tapdata-enterprise"]}
        outcome, blockers, _ = align.report_outcome(rows, scope, "auto")

        self.assertEqual("blocked", outcome)
        self.assertEqual("tapdata/tapdata-enterprise", blockers[0]["repository"])

    def test_required_access_failure_exposes_actionable_blocker(self):
        main = dict(self.observation("tapdata/tapdata", {"develop": "main-sha"}, selection="required"), target_status="cached_exists")
        main["refs"].update({
            "fetch_status": "failed",
            "error_kind": "repository_access_denied",
            "prompt": "tapdata/tapdata：当前身份可能没有该仓库的读取权限；请确认仓库访问授权与 origin 地址。",
        })
        outcome, blockers, _ = align.report_outcome([main], {"required_repositories": ["tapdata/tapdata"]}, "always")

        self.assertEqual("blocked", outcome)
        self.assertEqual("repository_access_denied", blockers[0]["error_kind"])
        self.assertIn("读取权限", blockers[0]["message"])


if __name__ == "__main__":
    unittest.main()
